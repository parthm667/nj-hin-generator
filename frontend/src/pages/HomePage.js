import React, { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { municipalitiesApi, analysisApi } from '../services/api';
import {
  forgetRecentAnalysis,
  getRecentAnalyses,
  saveRecentAnalysis,
} from '../services/local_history';
import { MapPin, Calendar, TrendingUp, AlertCircle, Loader2 } from 'lucide-react';

const PREFERRED_YEARS = [2017, 2018, 2019, 2020, 2021];

const normalizeYears = (years = []) => (
  [...new Set(years.filter(Number.isInteger))].sort((a, b) => a - b)
);

const getDefaultRange = (availableYears) => {
  if (PREFERRED_YEARS.every((year) => availableYears.includes(year))) {
    return { startYear: 2017, endYear: 2021 };
  }

  if (availableYears.length === 0) {
    return { startYear: '', endYear: '' };
  }

  const endYear = availableYears[availableYears.length - 1];
  let startYear = endYear;

  for (let index = availableYears.length - 2; index >= 0; index -= 1) {
    const candidate = availableYears[index];
    if (candidate !== startYear - 1 || endYear - candidate >= 5) break;
    startYear = candidate;
  }

  return { startYear, endYear };
};

const getRangeError = (startYear, endYear, availableYears) => {
  if (startYear === '' || endYear === '') return 'Choose a start and end year.';
  if (startYear > endYear) return 'Start year must be before or equal to end year.';

  const available = new Set(availableYears);
  const missingYears = [];
  for (let year = startYear; year <= endYear; year += 1) {
    if (!available.has(year)) missingYears.push(year);
  }

  if (missingYears.length > 0) {
    return `Missing loaded crash data for ${missingYears.join(', ')}. Choose a continuous range.`;
  }

  return null;
};

const getMutationErrorMessage = (error) => {
  const detail = error?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail?.message) return detail.message;
  return error?.message || 'Unable to create the analysis. Please try again.';
};

function HomePage() {
  const navigate = useNavigate();
  const initializedMunicipality = useRef(null);
  const [selectedMuni, setSelectedMuni] = useState('');
  const [startYear, setStartYear] = useState('');
  const [endYear, setEndYear] = useState('');
  const [recentAnalyses, setRecentAnalyses] = useState(getRecentAnalyses);

  const {
    data: municipalities = [],
    isLoading: municipalitiesLoading,
    isError: municipalitiesError,
  } = useQuery({
    queryKey: ['municipalities'],
    queryFn: () => municipalitiesApi.list().then((response) => response.data),
  });

  const {
    data: coverage,
    isLoading: coverageLoading,
    isFetching: coverageFetching,
    isError: coverageError,
    refetch: refetchCoverage,
  } = useQuery({
    queryKey: ['municipality-coverage', selectedMuni],
    queryFn: () => municipalitiesApi.getCoverage(selectedMuni).then((response) => response.data),
    enabled: Boolean(selectedMuni),
  });

  const createAnalysisMutation = useMutation({
    mutationFn: (data) => analysisApi.create(data).then((response) => response.data),
    onSuccess: (data) => {
      const municipality = municipalities.find(
        (item) => String(item.muni_id) === selectedMuni
      );
      setRecentAnalyses(saveRecentAnalysis({
        analysis_id: data.analysis_id,
        municipality_name: data.municipality_name || municipality?.name || 'Municipality analysis',
        start_year: startYear,
        end_year: endYear,
        created_at: new Date().toISOString(),
      }));
      navigate(`/analysis/${data.analysis_id}`);
    },
  });

  const availableYears = normalizeYears(coverage?.available_years);
  const rangeError = getRangeError(startYear, endYear, availableYears);
  const hasCoverage = availableYears.length > 0;
  const coverageUnavailable = coverageLoading || coverageFetching || coverageError || !hasCoverage;
  const showRangeError = Boolean(
    selectedMuni
    && hasCoverage
    && rangeError
    && initializedMunicipality.current === selectedMuni
  );
  const runDisabled = (
    createAnalysisMutation.isPending
    || municipalitiesLoading
    || municipalitiesError
    || !selectedMuni
    || coverageUnavailable
    || Boolean(rangeError)
  );

  useEffect(() => {
    if (!selectedMuni || !coverage || initializedMunicipality.current === selectedMuni) return;
    const defaults = getDefaultRange(normalizeYears(coverage.available_years));
    setStartYear(defaults.startYear);
    setEndYear(defaults.endYear);
    initializedMunicipality.current = selectedMuni;
  }, [coverage, selectedMuni]);

  const handleMunicipalityChange = (event) => {
    const muniId = event.target.value;
    setSelectedMuni(muniId);
    setStartYear('');
    setEndYear('');
    initializedMunicipality.current = null;
    createAnalysisMutation.reset();
  };

  const handleSubmit = (event) => {
    event.preventDefault();
    if (runDisabled) return;

    createAnalysisMutation.mutate({
      muni_id: Number(selectedMuni),
      config: {
        start_year: startYear,
        end_year: endYear,
        snap_distance_meters: 50,
        segment_length_miles: 0.1,
        significance_threshold: 0.05,
      },
    });
  };

  const coverageSpan = availableYears.length === 1
    ? String(availableYears[0])
    : `${availableYears[0]}–${availableYears[availableYears.length - 1]}`;
  const crashCount = coverage?.total_crashes || 0;

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
      <div className="text-center mb-16">
        <h1 className="text-4xl font-bold text-gray-900 mb-4">
          High Injury Network Analysis
        </h1>
        <p className="text-lg text-gray-600 max-w-2xl mx-auto">
          Explore loaded crash records to screen corridors for further review and preliminary planning
        </p>
      </div>

      <div className="max-w-2xl mx-auto">
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
          <div className="p-8">
            <h2 className="text-xl font-semibold text-gray-900 mb-6">
              Create Analysis
            </h2>

            {municipalitiesLoading ? (
              <div className="flex items-center justify-center py-12" role="status">
                <Loader2 className="w-8 h-8 text-primary animate-spin" aria-hidden="true" />
                <span className="sr-only">Loading municipalities</span>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-6">
                {municipalitiesError && (
                  <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-sm text-red-800" role="alert">
                    Unable to load municipalities. Please refresh the page and try again.
                  </div>
                )}

                {!municipalitiesError && municipalities.length === 0 && (
                  <div className="p-4 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-900" role="alert">
                    No municipalities are available.
                  </div>
                )}

                <div>
                  <label htmlFor="municipality" className="block text-sm font-medium text-gray-700 mb-2">
                    Municipality
                  </label>
                  <div className="relative">
                    <MapPin className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" aria-hidden="true" />
                    <select
                      id="municipality"
                      value={selectedMuni}
                      onChange={handleMunicipalityChange}
                      disabled={municipalitiesError || municipalities.length === 0}
                      className="w-full pl-10 pr-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary focus:border-transparent outline-none transition-all disabled:bg-gray-100 disabled:text-gray-500"
                      required
                    >
                      <option value="">Select a municipality...</option>
                      {municipalities.map((muni) => (
                        <option key={muni.muni_id} value={muni.muni_id}>
                          {muni.name} — {muni.county} County
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                {selectedMuni && coverageLoading && (
                  <div className="flex items-center space-x-2 text-sm text-gray-600" role="status">
                    <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                    <span>Loading crash data coverage...</span>
                  </div>
                )}

                {selectedMuni && coverageError && (
                  <div className="p-4 bg-red-50 border border-red-200 rounded-lg" role="alert">
                    <p className="text-sm text-red-800">Unable to load crash data coverage.</p>
                    <button
                      type="button"
                      onClick={() => refetchCoverage()}
                      className="mt-2 text-sm font-medium text-red-800 underline"
                    >
                      Retry coverage
                    </button>
                  </div>
                )}

                {selectedMuni && coverage && !coverageError && !hasCoverage && (
                  <div className="p-4 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-900" role="alert">
                    No usable crash data is loaded for this municipality.
                  </div>
                )}

                {selectedMuni && coverage && hasCoverage && (
                  <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg text-sm text-blue-900">
                    <p id="coverage-summary" className="font-medium">
                      {crashCount.toLocaleString()} loaded crash records span {coverageSpan} ({availableYears.length} available {availableYears.length === 1 ? 'year' : 'years'}).
                    </p>
                    <p id="coverage-disclaimer" className="mt-1 text-blue-800">
                      Loaded records do not guarantee complete coverage. Confirm completeness before using results.
                    </p>
                  </div>
                )}

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label htmlFor="start-year" className="block text-sm font-medium text-gray-700 mb-2">
                      Start Year
                    </label>
                    <div className="relative">
                      <Calendar className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" aria-hidden="true" />
                      <select
                        id="start-year"
                        value={startYear}
                        onChange={(event) => setStartYear(
                          event.target.value === '' ? '' : Number(event.target.value)
                        )}
                        disabled={coverageUnavailable}
                        aria-invalid={showRangeError}
                        aria-describedby={showRangeError ? 'year-range-error' : 'coverage-summary coverage-disclaimer'}
                        className="w-full pl-10 pr-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary focus:border-transparent outline-none transition-all disabled:bg-gray-100 disabled:text-gray-500"
                        required
                      >
                        <option value="">Select year</option>
                        {availableYears.map((year) => <option key={year} value={year}>{year}</option>)}
                      </select>
                    </div>
                  </div>

                  <div>
                    <label htmlFor="end-year" className="block text-sm font-medium text-gray-700 mb-2">
                      End Year
                    </label>
                    <div className="relative">
                      <Calendar className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" aria-hidden="true" />
                      <select
                        id="end-year"
                        value={endYear}
                        onChange={(event) => setEndYear(
                          event.target.value === '' ? '' : Number(event.target.value)
                        )}
                        disabled={coverageUnavailable}
                        aria-invalid={showRangeError}
                        aria-describedby={showRangeError ? 'year-range-error' : 'coverage-summary coverage-disclaimer'}
                        className="w-full pl-10 pr-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary focus:border-transparent outline-none transition-all disabled:bg-gray-100 disabled:text-gray-500"
                        required
                      >
                        <option value="">Select year</option>
                        {availableYears.map((year) => <option key={year} value={year}>{year}</option>)}
                      </select>
                    </div>
                  </div>
                </div>

                {showRangeError && (
                  <div id="year-range-error" className="p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-900" role="alert">
                    {rangeError}
                  </div>
                )}

                <button
                  type="submit"
                  disabled={runDisabled}
                  className="w-full flex items-center justify-center space-x-2 bg-primary hover:bg-primary-hover text-white font-medium py-3 px-6 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {createAnalysisMutation.isPending ? (
                    <>
                      <Loader2 className="w-5 h-5 animate-spin" aria-hidden="true" />
                      <span>Creating Analysis...</span>
                    </>
                  ) : (
                    <>
                      <TrendingUp className="w-5 h-5" aria-hidden="true" />
                      <span>Run Analysis</span>
                    </>
                  )}
                </button>

                {createAnalysisMutation.isError && (
                  <div className="flex items-start space-x-3 p-4 bg-red-50 border border-red-200 rounded-lg" role="alert">
                    <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" aria-hidden="true" />
                    <div className="text-sm text-red-800">
                      <p className="font-medium">Error creating analysis</p>
                      <p className="mt-1 text-red-700">{getMutationErrorMessage(createAnalysisMutation.error)}</p>
                    </div>
                  </div>
                )}
              </form>
            )}
          </div>
        </div>

        {recentAnalyses.length > 0 && (
          <section className="mt-8 bg-white rounded-xl shadow-sm border border-gray-200 p-6" aria-labelledby="recent-analyses-heading">
            <h2 id="recent-analyses-heading" className="text-lg font-semibold text-gray-900">
              Recent analyses
            </h2>
            <p className="text-sm text-gray-600 mt-1">
              This convenience list is saved only in this browser. Analysis links are not private; anyone with a result URL can open it.
            </p>
            <ul className="mt-4 divide-y divide-gray-200">
              {recentAnalyses.map((item) => (
                <li key={item.analysis_id} className="py-3 flex items-center justify-between gap-4">
                  <Link
                    to={`/analysis/${item.analysis_id}`}
                    className="min-w-0 text-sm text-primary hover:text-primary-hover focus:underline"
                  >
                    <span className="block font-medium truncate">{item.municipality_name}</span>
                    <span className="block text-xs text-gray-600 mt-1">
                      {item.start_year}–{item.end_year} · Analysis #{item.analysis_id}
                    </span>
                  </Link>
                  <button
                    type="button"
                    onClick={() => setRecentAnalyses(forgetRecentAnalysis(item.analysis_id))}
                    aria-label={`Forget ${item.municipality_name} analysis`}
                    className="shrink-0 px-3 py-2 text-sm font-medium text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
                  >
                    Forget
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-12">
          <div className="text-center">
            <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mx-auto mb-4">
              <MapPin className="w-6 h-6 text-primary" aria-hidden="true" />
            </div>
            <h3 className="text-sm font-medium text-gray-900 mb-2">Statistical Analysis</h3>
            <p className="text-sm text-gray-600">Exploratory screening highlights corridors for further review</p>
          </div>

          <div className="text-center">
            <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mx-auto mb-4">
              <TrendingUp className="w-6 h-6 text-primary" aria-hidden="true" />
            </div>
            <h3 className="text-sm font-medium text-gray-900 mb-2">Planning Support</h3>
            <p className="text-sm text-gray-600">Export results for review after confirming source-data completeness</p>
          </div>

          <div className="text-center">
            <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mx-auto mb-4">
              <Calendar className="w-6 h-6 text-primary" aria-hidden="true" />
            </div>
            <h3 className="text-sm font-medium text-gray-900 mb-2">Multi-Year Data</h3>
            <p className="text-sm text-gray-600">Analyze up to 5 years of loaded crash records</p>
          </div>
        </div>
      </div>
    </div>
  );
}

export default HomePage;
