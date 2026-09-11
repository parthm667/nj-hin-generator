import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import HomePage from './HomePage';
import { analysisApi, municipalitiesApi } from '../services/api';

jest.mock('../services/api', () => ({
  municipalitiesApi: {
    list: jest.fn(),
    getCoverage: jest.fn(),
  },
  analysisApi: {
    create: jest.fn(),
  },
}));

const municipalities = [
  { muni_id: 1, name: 'Alpha Township', county: 'Mercer' },
  { muni_id: 2, name: 'Beta Borough', county: 'Union' },
];

let queryClient;

function renderHome() {
  queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={['/']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/analysis/:analysisId" element={<div>Analysis opened</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function coverage(availableYears, crashCountsByYear = {}) {
  return {
    muni_id: 1,
    available_years: availableYears,
    crash_counts_by_year: crashCountsByYear,
    total_crashes: Object.values(crashCountsByYear).reduce((sum, count) => sum + count, 0),
  };
}

beforeEach(() => {
  window.localStorage.clear();
  municipalitiesApi.list.mockResolvedValue({ data: municipalities });
});

afterEach(() => {
  queryClient?.clear();
  window.localStorage.clear();
  jest.clearAllMocks();
});

test('disables analysis when the municipality has no usable crash data', async () => {
  municipalitiesApi.getCoverage.mockResolvedValue({ data: coverage([]) });
  renderHome();

  fireEvent.change(await screen.findByLabelText('Municipality'), { target: { value: '1' } });

  expect(await screen.findByText('No usable crash data is loaded for this municipality.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Run Analysis' })).toBeDisabled();
});

test('blocks a selected range that crosses an unloaded year', async () => {
  municipalitiesApi.getCoverage.mockResolvedValue({
    data: coverage([2019, 2021], { 2019: 15, 2021: 20 }),
  });
  renderHome();

  fireEvent.change(await screen.findByLabelText('Municipality'), { target: { value: '1' } });
  await waitFor(() => expect(screen.getByLabelText('Start Year')).toHaveValue('2021'));
  fireEvent.change(screen.getByLabelText('Start Year'), { target: { value: '2019' } });

  expect(await screen.findByRole('alert')).toHaveTextContent('Missing loaded crash data for 2020');
  expect(screen.getByRole('button', { name: 'Run Analysis' })).toBeDisabled();
});

test('defaults a single available year to both range endpoints', async () => {
  municipalitiesApi.getCoverage.mockResolvedValue({
    data: coverage([2022], { 2022: 47 }),
  });
  renderHome();

  fireEvent.change(await screen.findByLabelText('Municipality'), { target: { value: '1' } });

  await waitFor(() => {
    expect(screen.getByLabelText('Start Year')).toHaveValue('2022');
    expect(screen.getByLabelText('End Year')).toHaveValue('2022');
  });
  expect(screen.getByText(/47 loaded crash records/)).toBeInTheDocument();
  expect(screen.getByText(/do not guarantee complete coverage/i)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Run Analysis' })).toBeEnabled();
});

test('keeps an empty year sentinel when a range endpoint is cleared', async () => {
  municipalitiesApi.getCoverage.mockResolvedValue({
    data: coverage([2022], { 2022: 47 }),
  });
  renderHome();

  fireEvent.change(await screen.findByLabelText('Municipality'), { target: { value: '1' } });
  await waitFor(() => expect(screen.getByLabelText('Start Year')).toHaveValue('2022'));
  fireEvent.change(screen.getByLabelText('Start Year'), { target: { value: '' } });

  expect(screen.getByLabelText('Start Year')).toHaveValue('');
  expect(screen.getByRole('alert')).toHaveTextContent('Choose a start and end year.');
  expect(screen.getByLabelText('Start Year')).toHaveAttribute('aria-describedby', 'year-range-error');
  expect(screen.getByRole('button', { name: 'Run Analysis' })).toBeDisabled();
});

test('preserves the preferred 2017 through 2021 default when all five years are loaded', async () => {
  municipalitiesApi.getCoverage.mockResolvedValue({
    data: coverage(
      [2017, 2018, 2019, 2020, 2021],
      { 2017: 10, 2018: 11, 2019: 12, 2020: 13, 2021: 14 }
    ),
  });
  renderHome();

  fireEvent.change(await screen.findByLabelText('Municipality'), { target: { value: '1' } });

  await waitFor(() => {
    expect(screen.getByLabelText('Start Year')).toHaveValue('2017');
    expect(screen.getByLabelText('End Year')).toHaveValue('2021');
  });
});

test('resets coverage when municipalities change and posts the new valid range', async () => {
  municipalitiesApi.getCoverage.mockImplementation((muniId) => Promise.resolve({
    data: String(muniId) === '1'
      ? coverage([2017, 2018, 2019, 2020, 2021], { 2017: 1, 2018: 1, 2019: 1, 2020: 1, 2021: 1 })
      : { ...coverage([2022], { 2022: 47 }), muni_id: 2 },
  }));
  analysisApi.create.mockResolvedValue({ data: { analysis_id: 99 } });
  renderHome();

  const municipalitySelect = await screen.findByLabelText('Municipality');
  fireEvent.change(municipalitySelect, { target: { value: '1' } });
  await waitFor(() => expect(screen.getByLabelText('End Year')).toHaveValue('2021'));
  fireEvent.change(screen.getByLabelText('End Year'), { target: { value: '2020' } });
  fireEvent.change(municipalitySelect, { target: { value: '2' } });

  await waitFor(() => {
    expect(screen.getByLabelText('Start Year')).toHaveValue('2022');
    expect(screen.getByLabelText('End Year')).toHaveValue('2022');
  });
  fireEvent.click(screen.getByRole('button', { name: 'Run Analysis' }));

  await waitFor(() => expect(analysisApi.create).toHaveBeenCalledWith({
    muni_id: 2,
    config: {
      start_year: 2022,
      end_year: 2022,
      snap_distance_meters: 50,
      segment_length_miles: 0.1,
      significance_threshold: 0.05,
    },
  }));
  expect(JSON.parse(window.localStorage.getItem('nj-hin-recent-analyses'))[0]).toEqual(
    expect.objectContaining({
      analysis_id: 99,
      municipality_name: 'Beta Borough',
      start_year: 2022,
      end_year: 2022,
    })
  );
});

test('shows the FastAPI detail message when analysis creation is rejected', async () => {
  municipalitiesApi.getCoverage.mockResolvedValue({
    data: coverage([2022], { 2022: 47 }),
  });
  analysisApi.create.mockRejectedValue({
    response: {
      status: 422,
      data: {
        detail: {
          code: 'missing_crash_data',
          message: 'Crash data is missing for 2021.',
          missing_years: [2021],
          available_years: [2022],
        },
      },
    },
  });
  renderHome();

  fireEvent.change(await screen.findByLabelText('Municipality'), { target: { value: '1' } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Run Analysis' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Run Analysis' }));

  expect(await screen.findByText('Crash data is missing for 2021.')).toBeInTheDocument();
});

test('shows and forgets recent analyses only in this browser', async () => {
  window.localStorage.setItem('nj-hin-recent-analyses', JSON.stringify([
    {
      analysis_id: 73,
      municipality_name: 'Alpha Township',
      start_year: 2017,
      end_year: 2021,
      created_at: '2026-09-10T12:00:00.000Z',
    },
  ]));
  renderHome();

  const resultLink = await screen.findByRole('link', { name: /Alpha Township/ });
  expect(resultLink).toHaveAttribute('href', '/analysis/73');
  expect(screen.getByText(/saved only in this browser/i)).toBeInTheDocument();
  expect(screen.getByText(/anyone with a result URL can open it/i)).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Forget Alpha Township analysis' }));

  expect(screen.queryByRole('link', { name: /Alpha Township/ })).not.toBeInTheDocument();
  expect(window.localStorage.getItem('nj-hin-recent-analyses')).toBeNull();
});

test('describes the analysis as exploratory rather than grant ready or definitive', async () => {
  renderHome();

  await screen.findByLabelText('Municipality');
  expect(screen.getByText(/Explore loaded crash records to screen corridors/i)).toBeInTheDocument();
  expect(screen.getByText(/exploratory screening/i)).toBeInTheDocument();
  expect(screen.queryByText('Grant Ready')).not.toBeInTheDocument();
  expect(screen.queryByText(/true crash hotspots/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/statistically significant|grant applications/i)).not.toBeInTheDocument();
});
