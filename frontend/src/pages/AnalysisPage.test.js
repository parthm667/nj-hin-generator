import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import AnalysisPage from './AnalysisPage';
import { analysisApi, exportApi } from '../services/api';

jest.mock('../services/api', () => ({
  analysisApi: {
    get: jest.fn(),
    getCrashes: jest.fn(),
    getHIN: jest.fn(),
  },
  exportApi: {
    downloadPDF: jest.fn(),
    downloadCSV: jest.fn(),
  },
}));

jest.mock('react-leaflet', () => ({
  useMap: () => mockMap,
  MapContainer: ({ children }) => <div data-testid="map">{children}</div>,
  TileLayer: () => null,
  CircleMarker: ({ center, fillColor, children }) => (
    <div
      data-testid="crash-marker"
      data-center={JSON.stringify(center)}
      data-fill-color={fillColor}
    >
      {children}
    </div>
  ),
  Polyline: ({ positions, color, weight, opacity, children }) => (
    <div
      data-testid="hin-line"
      data-positions={JSON.stringify(positions)}
      data-color={color}
      data-weight={weight}
      data-opacity={opacity}
    >
      {children}
    </div>
  ),
  Popup: ({ children }) => <div>{children}</div>,
}));

const mockMap = {
  fitBounds: jest.fn(),
  invalidateSize: jest.fn(),
  getContainer: () => document.createElement('div'),
};

const completedAnalysis = {
  analysis_id: 42,
  municipality_name: 'Example Township',
  start_year: 2019,
  end_year: 2023,
  status: 'completed',
  total_crashes: 1,
  total_fatalities: 0,
  total_injuries: 1,
  hin_miles: 1.25,
  hin_segment_count: 2,
  data_status: 'ready',
  data_message: null,
  available_years: [2019, 2020, 2021, 2022, 2023],
  missing_years: [],
  input_version: {
    crashes: 123,
    roads: 2,
    boundaries: 3,
    svi: 4,
    method: 'input-v1',
  },
  data_quality: {
    injury_detail_available: false,
    bicycle_data_available: false,
    casualty_counts_complete: false,
    svi_available: true,
    geocode_counts: { reported: 1402, route_milepost: 4353 },
    coverage_note: 'Coverage reflects loaded records only.',
    method_version: 'exploratory-v1',
    svi_years: [2020],
  },
};

const emptyFeatureCollection = {
  type: 'FeatureCollection',
  features: [],
};

let queryClient;

test('filters crash severity independently of the HIN layer and restores hidden layers', async () => {
  analysisApi.get.mockResolvedValue({ data: completedAnalysis });
  analysisApi.getCrashes.mockResolvedValue({ data: { features: [
    { geometry: { type: 'Point', coordinates: [-74.5, 40.1] }, properties: { severity: 'fatal' } },
    { geometry: { type: 'Point', coordinates: [-74.4, 40.2] }, properties: { severity: 'minor_injury' } },
  ] } });
  analysisApi.getHIN.mockResolvedValue({ data: { features: [
    { geometry: { type: 'LineString', coordinates: [[-74.5, 40.1], [-74.3, 40.3]] } },
  ] } });
  renderAnalysisPage();
  expect(await screen.findAllByTestId('crash-marker')).toHaveLength(2);
  fireEvent.change(screen.getByLabelText('Crash severity'), { target: { value: 'fatal' } });
  expect(screen.getAllByTestId('crash-marker')).toHaveLength(1);
  expect(screen.getByTestId('hin-line')).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('Show crashes'));
  expect(screen.queryByTestId('crash-marker')).not.toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('Show HIN'));
  expect(screen.queryByTestId('hin-line')).not.toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('Show crashes'));
  expect(screen.getAllByTestId('crash-marker')).toHaveLength(1);
  fireEvent.change(screen.getByLabelText('Crash severity'), { target: { value: 'all' } });
  expect(screen.getAllByTestId('crash-marker')).toHaveLength(2);
});

test('fits every result including multiline segments and resets the full extent after filtering', async () => {
  analysisApi.get.mockResolvedValue({ data: completedAnalysis });
  analysisApi.getCrashes.mockResolvedValue({ data: { features: [
    { geometry: { type: 'Point', coordinates: [-74.5, 40.1] }, properties: { severity: 'fatal' } },
  ] } });
  analysisApi.getHIN.mockResolvedValue({ data: { features: [
    { geometry: { type: 'MultiLineString', coordinates: [[[-74.3, 40.3], [-74.0, 40.6]]] } },
  ] } });
  renderAnalysisPage();
  await screen.findByTestId('map');
  expect(mockMap.fitBounds).toHaveBeenLastCalledWith(
    [[40.1, -74.5], [40.3, -74.3], [40.6, -74]],
    expect.objectContaining({ paddingBottomRight: [24, 24], maxZoom: 16 })
  );
  mockMap.fitBounds.mockClear();
  fireEvent.click(screen.getByLabelText('Show HIN'));
  expect(mockMap.fitBounds).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Reset view' }));
  expect(mockMap.fitBounds).toHaveBeenCalledTimes(1);
});

function renderAnalysisPage() {
  queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: Infinity,
      },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={['/analysis/42']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/analysis/:analysisId" element={<AnalysisPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

afterEach(() => {
  queryClient?.clear();
  jest.clearAllMocks();
});

test('keeps polling a pending analysis until it becomes completed', async () => {
  analysisApi.get
    .mockResolvedValueOnce({ data: { ...completedAnalysis, status: 'pending' } })
    .mockResolvedValue({ data: completedAnalysis });
  analysisApi.getCrashes.mockResolvedValue({ data: emptyFeatureCollection });
  analysisApi.getHIN.mockResolvedValue({ data: emptyFeatureCollection });

  renderAnalysisPage();

  expect(await screen.findByText('Pending')).toBeInTheDocument();
  expect(await screen.findByText('Completed', {}, { timeout: 4500 })).toBeInTheDocument();
  expect(analysisApi.get).toHaveBeenCalledTimes(2);
});

test('keeps the panel scroll region keyboard accessible and removes closed controls from the focus order', async () => {
  analysisApi.get.mockResolvedValue({ data: completedAnalysis });
  analysisApi.getCrashes.mockResolvedValue({ data: emptyFeatureCollection });
  analysisApi.getHIN.mockResolvedValue({ data: emptyFeatureCollection });

  renderAnalysisPage();

  const scrollRegion = await screen.findByRole('region', { name: 'Analysis details content' });
  expect(scrollRegion).toHaveAttribute('tabindex', '0');

  const closeButton = screen.getByRole('button', { name: 'Close analysis details' });
  closeButton.focus();
  fireEvent.click(closeButton);

  const openButton = await screen.findByRole('button', { name: 'Open analysis details' });
  await waitFor(() => expect(openButton).toHaveFocus());
  expect(screen.queryByRole('region', { name: 'Analysis details content' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Close analysis details' })).not.toBeInTheDocument();
  const hiddenPanel = screen.getByText('Analysis Details').closest('[aria-hidden="true"]');
  expect(hiddenPanel).toHaveAttribute('inert', '');

  fireEvent.click(openButton);

  const reopenedCloseButton = await screen.findByRole('button', { name: 'Close analysis details' });
  await waitFor(() => expect(reopenedCloseButton).toHaveFocus());
  expect(screen.getByRole('region', { name: 'Analysis details content' })).toBeInTheDocument();
});

test('renders LineString and MultiLineString HIN geometry in Leaflet coordinate order', async () => {
  analysisApi.get.mockResolvedValue({ data: completedAnalysis });
  analysisApi.getCrashes.mockResolvedValue({
    data: {
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [-74.5, 40.1] },
          properties: { severity: 'injury_unknown', date: '2023-01-15', road_name: 'Main Street' },
        },
      ],
    },
  });
  analysisApi.getHIN.mockResolvedValue({
    data: {
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          geometry: {
            type: 'LineString',
            coordinates: [[-74.5, 40.1], [-74.4, 40.2]],
          },
          properties: { road_name: 'Main Street', crash_count: 7, crash_rate: 12 },
        },
        {
          type: 'Feature',
          geometry: {
            type: 'MultiLineString',
            coordinates: [
              [[-74.3, 40.3], [-74.2, 40.4]],
              [[-74.1, 40.5], [-74.0, 40.6]],
            ],
          },
          properties: { road_name: 'Broad Avenue', crash_count: 4, crash_rate: 6 },
        },
      ],
    },
  });

  renderAnalysisPage();

  const lines = await screen.findAllByTestId('hin-line');
  expect(lines).toHaveLength(2);
  expect(JSON.parse(lines[0].getAttribute('data-positions'))).toEqual([
    [40.1, -74.5],
    [40.2, -74.4],
  ]);
  expect(JSON.parse(lines[1].getAttribute('data-positions'))).toEqual([
    [[40.3, -74.3], [40.4, -74.2]],
    [[40.5, -74.1], [40.6, -74.0]],
  ]);
  expect(lines[0]).toHaveAttribute('data-color', '#dc2626');
  expect(lines[0]).toHaveAttribute('data-weight', '4');
  expect(lines[0]).toHaveAttribute('data-opacity', '0.8');
  expect(screen.getByText('UNKNOWN INJURY DETAIL')).toBeInTheDocument();
  expect(screen.getByTestId('crash-marker')).toHaveAttribute('data-fill-color', '#7c3aed');
  expect(screen.getByText('Crash rate: 12.00 per mile/year')).toBeInTheDocument();
  expect(screen.queryByText(/Severity-weighted rate/)).not.toBeInTheDocument();
});

test('explains location estimates and supplied data limitations in plain language', async () => {
  analysisApi.get.mockResolvedValue({
    data: { ...completedAnalysis, total_fatalities: null, total_injuries: null },
  });
  analysisApi.getCrashes.mockResolvedValue({ data: emptyFeatureCollection });
  analysisApi.getHIN.mockResolvedValue({ data: emptyFeatureCollection });

  renderAnalysisPage();

  const fatalitiesRow = (await screen.findByText('Fatalities')).parentElement;
  const injuriesRow = screen.getByText('Injuries').parentElement;
  expect(within(fatalitiesRow).getByText('Unknown')).toBeInTheDocument();
  expect(within(injuriesRow).getByText('Unknown')).toBeInTheDocument();
  expect(screen.getByText('Exploratory screening only')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'About the data' })).toBeInTheDocument();
  expect(screen.getByText(/1,402 crashes use coordinates reported/i)).toBeInTheDocument();
  expect(screen.getByText(/4,353 crash locations are estimates.*road names and mile markers/i)).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'People killed or injured' })).toBeInTheDocument();
  expect(screen.getByText(/Some fatality and injury counts may be missing/i)).toBeInTheDocument();
  expect(screen.getByText(/does not consistently distinguish serious from minor injuries/i)).toBeInTheDocument();
  expect(screen.getByText(/Bicycle involvement is not reliably available/i)).toBeInTheDocument();
  expect(screen.getByText(/Uses neighborhood data from 2020/i)).toBeInTheDocument();
  expect(screen.getByText(/does not measure individual people/i)).toBeInTheDocument();
  expect(screen.getByText(/Some crashes may be missing/i)).toBeInTheDocument();
  expect(screen.getByText(/crashes we couldn't locate are not shown/i)).toBeInTheDocument();

  const technicalDetails = screen.getByText('Technical details').closest('details');
  expect(technicalDetails).not.toHaveAttribute('open');
  expect(technicalDetails.firstElementChild).toHaveTextContent('Technical details');
  expect(technicalDetails.firstElementChild).toHaveClass('min-h-[44px]');
  expect(within(technicalDetails).getByText('Coverage reflects loaded records only.')).toBeInTheDocument();
  expect(within(technicalDetails).getByText('exploratory-v1')).toBeInTheDocument();
  const inputVersion = within(technicalDetails).getByText('{"crashes":123,"roads":2,"boundaries":3,"svi":4,"method":"input-v1"}');
  expect(inputVersion).toHaveStyle({ overflowWrap: 'anywhere' });
  expect(within(technicalDetails).getByText(/used internally to track/i)).toBeInTheDocument();
});

test('uses reassuring data-quality wording only when availability is explicitly true', async () => {
  analysisApi.get.mockResolvedValue({
    data: {
      ...completedAnalysis,
      data_quality: {
        ...completedAnalysis.data_quality,
        injury_detail_available: true,
        bicycle_data_available: true,
        casualty_counts_complete: true,
      },
    },
  });
  analysisApi.getCrashes.mockResolvedValue({ data: emptyFeatureCollection });
  analysisApi.getHIN.mockResolvedValue({ data: emptyFeatureCollection });

  renderAnalysisPage();

  expect(await screen.findByText(/counts are recorded for every crash included/i)).toBeInTheDocument();
  expect(screen.getByText(/does not mean every crash was captured/i)).toBeInTheDocument();
  expect(screen.getByText(/included records distinguish serious from minor injuries/i)).toBeInTheDocument();
  expect(screen.getByText(/Bicycle involvement is available for the included records/i)).toBeInTheDocument();
  expect(screen.queryByText(/totals are incomplete/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/not reliably available/i)).not.toBeInTheDocument();
});

test('treats missing quality flags as unavailable and retains other location methods in technical details', async () => {
  analysisApi.get.mockResolvedValue({
    data: {
      ...completedAnalysis,
      data_quality: {
        geocode_counts: { manual_review: 12 },
        coverage_note: 'Only usable loaded records are summarized.',
      },
    },
  });
  analysisApi.getCrashes.mockResolvedValue({ data: emptyFeatureCollection });
  analysisApi.getHIN.mockResolvedValue({ data: emptyFeatureCollection });

  renderAnalysisPage();

  expect(await screen.findByText(/Reported-coordinate and estimated-location counts are not available/i)).toBeInTheDocument();
  expect(screen.getByText(/Some fatality and injury counts may be missing/i)).toBeInTheDocument();
  expect(screen.getByText(/Serious and minor injury detail isn't available/i)).toBeInTheDocument();
  expect(screen.getByText(/Bicycle-involvement information isn't available/i)).toBeInTheDocument();
  expect(screen.getByText(/Community-condition information isn't available/i)).toBeInTheDocument();

  const technicalDetails = screen.getByText('Technical details').closest('details');
  expect(within(technicalDetails).getByText('Manual review: 12')).toBeInTheDocument();
  expect(within(technicalDetails).getByText('Only usable loaded records are summarized.')).toBeInTheDocument();
});

test('uses singular wording for one reported crash location', async () => {
  analysisApi.get.mockResolvedValue({
    data: {
      ...completedAnalysis,
      data_quality: {
        ...completedAnalysis.data_quality,
        geocode_counts: { reported: 1, route_milepost: 1 },
      },
    },
  });
  analysisApi.getCrashes.mockResolvedValue({ data: emptyFeatureCollection });
  analysisApi.getHIN.mockResolvedValue({ data: emptyFeatureCollection });

  renderAnalysisPage();

  expect(await screen.findByText(/1 crash uses coordinates reported/i)).toBeInTheDocument();
  expect(screen.getByText(/1 crash location is an estimate.*road names and mile markers/i)).toBeInTheDocument();
  expect(screen.queryByText(/1 crashes use coordinates reported/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/1 crash locations are estimates/i)).not.toBeInTheDocument();
});

test('downloads crash and HIN CSV exports without changing the page', async () => {
  analysisApi.get.mockResolvedValue({ data: completedAnalysis });
  analysisApi.getCrashes.mockResolvedValue({ data: emptyFeatureCollection });
  analysisApi.getHIN.mockResolvedValue({ data: emptyFeatureCollection });
  exportApi.downloadCSV.mockResolvedValue({ data: new Blob(['csv']) });
  Object.defineProperty(window.URL, 'createObjectURL', {
    configurable: true,
    value: jest.fn(() => 'blob:csv'),
  });
  Object.defineProperty(window.URL, 'revokeObjectURL', {
    configurable: true,
    value: jest.fn(),
  });
  const clickSpy = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

  renderAnalysisPage();

  fireEvent.click(await screen.findByRole('button', { name: 'Download crashes CSV' }));
  await waitFor(() => expect(exportApi.downloadCSV).toHaveBeenCalledWith('42', 'crashes'));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Download HIN CSV' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Download HIN CSV' }));
  await waitFor(() => expect(exportApi.downloadCSV).toHaveBeenCalledWith('42', 'hin_segments'));
  expect(screen.getByText('Analysis Details')).toBeInTheDocument();

  clickSpy.mockRestore();
});

test('does not present a legacy completed analysis with zero crashes as valid results', async () => {
  const { data_status, data_message, available_years, missing_years, ...legacyAnalysis } = completedAnalysis;
  analysisApi.get.mockResolvedValue({
    data: { ...legacyAnalysis, total_crashes: 0, hin_miles: 0, hin_segment_count: 0 },
  });

  renderAnalysisPage();

  expect(await screen.findByText('No usable crash data')).toBeInTheDocument();
  expect(screen.queryByText('Completed')).not.toBeInTheDocument();
  expect(screen.queryByText('Crash Statistics')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Download PDF Report' })).not.toBeInTheDocument();
  expect(analysisApi.getCrashes).not.toHaveBeenCalled();
  expect(analysisApi.getHIN).not.toHaveBeenCalled();
});

test('shows an out-of-date completed analysis as an amber rerun state', async () => {
  analysisApi.get.mockResolvedValue({
    data: {
      ...completedAnalysis,
      data_status: 'stale',
      data_message: 'Crash data changed after this analysis was run.',
    },
  });

  renderAnalysisPage();

  expect(await screen.findByText('Results out of date')).toBeInTheDocument();
  expect(screen.getByText('Crash data changed after this analysis was run.')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Run a new analysis' })).toHaveAttribute('href', '/');
  expect(screen.queryByText('Completed')).not.toBeInTheDocument();
  expect(screen.queryByText('Crash Statistics')).not.toBeInTheDocument();
  expect(analysisApi.getCrashes).not.toHaveBeenCalled();
  expect(analysisApi.getHIN).not.toHaveBeenCalled();
});
