import {
  forgetRecentAnalysis,
  getRecentAnalyses,
  saveRecentAnalysis,
} from './local_history';

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  jest.restoreAllMocks();
  window.localStorage.clear();
});

const entry = (analysisId) => ({
  analysis_id: analysisId,
  municipality_name: `Municipality ${analysisId}`,
  start_year: 2017,
  end_year: 2021,
  created_at: `2026-09-10T12:${String(analysisId).padStart(2, '0')}:00.000Z`,
});

test('keeps the newest 20 valid analyses and moves a repeated id to the front', () => {
  for (let analysisId = 1; analysisId <= 21; analysisId += 1) {
    saveRecentAnalysis(entry(analysisId));
  }

  expect(getRecentAnalyses()).toHaveLength(20);
  expect(getRecentAnalyses()[0].analysis_id).toBe(21);
  expect(getRecentAnalyses()[19].analysis_id).toBe(2);

  saveRecentAnalysis({ ...entry(10), municipality_name: 'Updated Municipality' });
  expect(getRecentAnalyses()).toHaveLength(20);
  expect(getRecentAnalyses()[0]).toEqual(expect.objectContaining({
    analysis_id: 10,
    municipality_name: 'Updated Municipality',
  }));
});

test('forgets an analysis locally and removes the empty storage record', () => {
  saveRecentAnalysis(entry(1));

  expect(forgetRecentAnalysis(1)).toEqual([]);
  expect(window.localStorage.getItem('nj-hin-recent-analyses')).toBeNull();
});

test('continues safely when browser storage is unavailable', () => {
  jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('storage disabled');
  });
  jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('storage disabled');
  });

  expect(getRecentAnalyses()).toEqual([]);
  expect(() => saveRecentAnalysis(entry(1))).not.toThrow();
  expect(saveRecentAnalysis(entry(1))).toEqual([entry(1)]);
});
