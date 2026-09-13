import React from 'react';
import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom';
import CrashPopup, { formatCrashDate, formatCrashTime, formatLighting, crashSummary } from './CrashPopup';

const mockMap = { getSize: () => ({ x: 360, y: 300 }), on: jest.fn(), off: jest.fn() };
let mockPopupOpen = true;
jest.mock('react-leaflet', () => {
  const React = require('react');
  return {
    useMap: () => mockMap,
    Popup: React.forwardRef(({ children }, ref) => {
      React.useImperativeHandle(ref, () => ({ options: {}, update: jest.fn() }));
      return <div>{mockPopupOpen ? children : null}</div>;
    }),
  };
});

afterEach(() => { mockPopupOpen = true; });

test('shows recorded dashboard circumstances and honest conflict and location labels', () => {
  render(<CrashPopup severityLabel="UNKNOWN INJURY DETAIL" properties={{
    severity: 'injury_unknown', source_name: 'NJDOT dashboard', dashboard_id: '1001',
    weather: 'Rain', crash_type: 'Rear End', first_harmful_event: 'Other Motor Vehicle',
    intersection_name: 'MAIN ST', at_intersection: 'Y', speed_limit: '25', vehicle_count: '0',
    surface_condition: 'Wet', document_locator: 'D123', source_street_name: 'SECOND ST',
    source_retrieved_at: '2026-09-13T00:00:00Z', source_url: 'https://example.org/public.csv',
    location_method: 'dashboard_calculated', severity_conflict: true,
  }} />);
  for (const text of ['Rain', 'Rear End', 'Other Motor Vehicle', 'MAIN ST', 'Wet']) {
    expect(screen.getByText(text)).toBeInTheDocument();
  }
  expect(screen.getByText(/severity rating and casualty evidence disagree/i)).toBeInTheDocument();
  expect(screen.queryByText('Injury severity was not specified in this record.')).not.toBeInTheDocument();
  expect(screen.getByText('Vehicles').nextElementSibling).toHaveTextContent('0');
  expect(screen.getByText('Speed limit').nextElementSibling).toHaveTextContent('25 mph');
  userEvent.click(screen.getByRole('button', { name: 'Source and location' }));
  expect(screen.getByText('NJDOT dashboard')).toBeInTheDocument();
  expect(screen.getByText('Dashboard record ID').nextElementSibling).toHaveTextContent('1001');
  expect(screen.getByText('Document locator').nextElementSibling).toHaveTextContent('D123');
  expect(screen.getByText('Location method').nextElementSibling).toHaveTextContent('NJDOT calculated position');
  expect(screen.getByText(/subject to revision/i)).toBeInTheDocument();
  expect(screen.queryByText('Reported coordinates')).not.toBeInTheDocument();
});

test.each(['0', '', '-5', '999', 'unknown'])('does not display placeholder speed %p as a speed limit', (speed) => {
  render(<CrashPopup severityLabel="FATAL" properties={{ source_name: 'NJDOT dashboard', speed_limit: speed }} />);
  expect(screen.getByText('Speed limit').nextElementSibling).toHaveTextContent('Not recorded');
});

test('does not read or format incident details until its popup content opens', () => {
  mockPopupOpen = false;
  const properties = { get date() { throw new Error('Closed popup formatted a crash date'); } };
  render(<CrashPopup severityLabel="FATAL" properties={properties} />);
  expect(screen.queryByRole('region', { name: 'Crash incident details' })).not.toBeInTheDocument();
});

test.each([
  ['0000', '12:00 AM'], ['00:00', '12:00 AM'], ['2359', '11:59 PM'],
  ['12:00', '12:00 PM'], [' 0930 ', '9:30 AM'], ['23:60', 'Not recorded'],
  ['2400', 'Not recorded'], ['9999', 'Not recorded'], ['930', 'Not recorded'],
  [null, 'Not recorded'], [undefined, 'Not recorded'], [0, 'Not recorded'],
])('formats recorded time %p as %p', (value, expected) => {
  expect(formatCrashTime(value)).toBe(expected);
});

test.each([
  ['2017-10-10', 'Oct 10, 2017'], ['2020-02-29', 'Feb 29, 2020'],
  ['2019-02-29', 'Not recorded'], ['2023-13-01', 'Not recorded'],
  ['2023-01-01T00:00:00Z', 'Not recorded'], [null, 'Not recorded'],
])('formats calendar date %p without a timezone shift', (value, expected) => {
  expect(formatCrashDate(value)).toBe(expected);
});

test.each([
  ['01', 'Daylight'], ['02', 'Dawn'], ['03', 'Dusk'],
  ['04', 'Dark — lights off'], ['05', 'Dark — no streetlights'],
  ['06', 'Dark — continuous streetlighting'], ['07', 'Dark — spot streetlighting'],
  [' daylight ', 'Daylight'], ['DARK', 'Dark'], ['dawn', 'Dawn'], ['dusk', 'Dusk'],
  ['00', 'Unavailable'], ['99', 'Unavailable'], ['sunny', 'Unavailable'],
  ['constructor', 'Unavailable'], ['__proto__', 'Unavailable'], [null, 'Not recorded'],
])('decodes lighting %p without inventing labels', (value, expected) => {
  expect(formatLighting(value)).toBe(expected);
});

test('summary uses recorded counts and never substitutes fatal severity for missing people', () => {
  expect(crashSummary({ road_name: 'NJ 27', total_killed: 1, total_injured: 0 }))
    .toBe('Crash on NJ 27: 1 person killed and 0 people injured.');
  expect(crashSummary({ severity: 'fatal', total_killed: null, total_injured: 2 }))
    .toBe('Recorded crash: fatality count not recorded and 2 people injured.');
  expect(crashSummary({ severity: 'property_damage', total_killed: null, total_injured: null }))
    .toBe('Recorded crash: casualty counts not recorded.');
  expect(crashSummary({ total_killed: -1, total_injured: 0 }))
    .toBe('Recorded crash: fatality count not recorded and 0 people injured.');
});

test('shows all-person counts separately from pedestrian subsets and opens source details by keyboard', () => {
  render(<CrashPopup severityLabel="FATAL" properties={{
    crash_id: 92856, external_id: 'NJDOT:2017:MERCER:92856', date: '2017-10-10', time: '0000',
    road_name: 'NJ 27', route_number: '11060001__', total_killed: 1, total_injured: 0,
    pedestrians_killed: 1, pedestrians_injured: 0, ped_involved: false,
    bike_involved: null, light_condition: '06', geocode_quality: 'route_milepost',
  }} />);
  const card = screen.getByRole('region', { name: 'Crash incident details' });
  expect(within(card).getByText('Oct 10, 2017')).toBeInTheDocument();
  expect(within(card).getByText('12:00 AM')).toBeInTheDocument();
  for (const label of ['All people killed', 'All people injured', 'Pedestrians killed', 'Pedestrians injured']) {
    expect(within(card).getByText(label)).toBeInTheDocument();
  }
  expect(within(card).getByText(/included in the all-person totals/i)).toBeInTheDocument();
  expect(within(card).getByText('Bicycle involvement').nextElementSibling).toHaveTextContent('Unknown');
  expect(within(card).queryByText(/no pedestrian/i)).not.toBeInTheDocument();
  expect(within(card).getByText('Estimated map position')).toBeInTheDocument();
  const toggle = within(card).getByRole('button', { name: 'Source and location' });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(within(card).queryByText('NJDOT:2017:MERCER:92856')).not.toBeInTheDocument();
  toggle.focus();
  userEvent.keyboard('{enter}');
  expect(toggle).toHaveAttribute('aria-expanded', 'true');
  expect(within(card).getByText('NJDOT crash archive')).toBeInTheDocument();
  expect(within(card).getByText('Route identifier').nextElementSibling).toHaveTextContent('11060001__');
  expect(within(card).getByText(/may not identify the exact crash site/i)).toBeInTheDocument();
  userEvent.keyboard(' ');
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(within(card).queryByText('NJDOT:2017:MERCER:92856')).not.toBeInTheDocument();
});

test.each([[true, 'Yes'], [false, 'No'], [null, 'Unknown'], ['false', 'Unknown']])(
  'bicycle involvement %p is displayed as %p only from explicit booleans', (bike, expected) => {
    render(<CrashPopup severityLabel="UNKNOWN INJURY DETAIL" properties={{ severity: 'injury_unknown', bike_involved: bike }} />);
    expect(screen.getByText('Bicycle involvement').nextElementSibling).toHaveTextContent(expected);
    expect(screen.getByText('Injury severity was not specified in this record.')).toBeInTheDocument();
  },
);

test('unrecognized provenance and location codes do not become NJDOT attribution or reported coordinates', () => {
  render(<CrashPopup severityLabel="FATAL" properties={{ external_id: 'legacy-123', geocode_quality: 'high' }} />);
  userEvent.click(screen.getByRole('button', { name: 'Source and location' }));
  expect(screen.queryByText('NJDOT crash archive')).not.toBeInTheDocument();
  expect(screen.getByText('Source').nextElementSibling).toHaveTextContent('Not recorded');
  expect(screen.getByText('Location method').nextElementSibling).toHaveTextContent('Unavailable');
});

test('scrolling incident card shrinks when the mobile map height changes', () => {
  render(<CrashPopup severityLabel="FATAL" properties={{}} />);
  const card = screen.getByRole('region', { name: 'Crash incident details' });
  expect(card).toHaveStyle({ maxHeight: '132px' });
  const handler = mockMap.on.mock.calls.find(([event]) => event === 'resize')[1];
  act(() => handler({ newSize: { x: 300, y: 230 } }));
  expect(card).toHaveStyle({ maxHeight: '80px' });
  act(() => handler({ newSize: { x: 1000, y: 800 } }));
  expect(card).toHaveStyle({ maxHeight: '520px' });
  expect(card).toHaveAttribute('tabindex', '0');
});
