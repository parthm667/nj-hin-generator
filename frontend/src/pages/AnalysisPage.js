import React, { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { analysisApi, exportApi } from '../services/api';
import { MapContainer, TileLayer, CircleMarker, Polyline, Popup } from 'react-leaflet';
import { 
  Loader2, AlertCircle, CheckCircle, Clock,
  X, Info, FileText
} from 'lucide-react';
import 'leaflet/dist/leaflet.css';
import AnalysisMapView, { isMapCoordinate } from '../components/AnalysisMapView';

const toLeafletPoint = ([longitude, latitude]) => [latitude, longitude];

const getLinePositions = (geometry) => {
  if (!geometry?.coordinates) {
    return null;
  }

  if (geometry.type === 'LineString') {
    return geometry.coordinates.map(toLeafletPoint);
  }

  if (geometry.type === 'MultiLineString') {
    return geometry.coordinates.map((line) => line.map(toLeafletPoint));
  }

  return null;
};

const getHINColor = (crashRate) => {
  if (crashRate > 10) return '#dc2626';
  if (crashRate > 5) return '#ea580c';
  return '#f59e0b';
};

const formatSeverity = (severity) => (
  severity === 'injury_unknown'
    ? 'UNKNOWN INJURY DETAIL'
    : typeof severity === 'string'
    ? severity.replaceAll('_', ' ').toUpperCase()
    : 'UNKNOWN'
);

const formatCount = (count) => (
  count === null || count === undefined ? 'Unknown' : count
);

const formatQualityCount = (count) => (
  typeof count === 'number' && Number.isFinite(count)
    ? count.toLocaleString('en-US')
    : null
);

const formatQualityLabel = (label) => (
  label.replaceAll('_', ' ').replace(/^./, (character) => character.toUpperCase())
);

const formatInputVersion = (inputVersion) => (
  typeof inputVersion === 'string' ? inputVersion : JSON.stringify(inputVersion)
);

const DATA_ISSUES = {
  no_data: {
    title: 'No usable crash data',
    fallbackMessage: 'No usable crash data was available for this analysis period.',
  },
  missing_years: {
    title: 'Missing year data',
    fallbackMessage: 'Crash data is missing for one or more years in this analysis period.',
  },
  stale: {
    title: 'Results out of date',
    fallbackMessage: 'The loaded crash data changed after this analysis was run.',
  },
};

const getDataIssue = (analysis) => {
  if (analysis?.status !== 'completed') return null;
  if (analysis.data_status === 'ready') return null;
  if (!analysis.data_status && Number(analysis.total_crashes) > 0) return null;

  return DATA_ISSUES[analysis.data_status] || DATA_ISSUES.no_data;
};

function AnalysisPage() {
  const { analysisId } = useParams();
  const [sidePanelOpen, setSidePanelOpen] = useState(true);
  const [resetKey, setResetKey] = useState(0);
  const [isDownloading, setIsDownloading] = useState(false);
  const [downloadingCSV, setDownloadingCSV] = useState(null);
  const closePanelButtonRef = useRef(null);
  const openPanelButtonRef = useRef(null);
  const panelWasToggled = useRef(false);

  useEffect(() => {
    if (!panelWasToggled.current) return;
    const focusTarget = sidePanelOpen
      ? closePanelButtonRef.current
      : openPanelButtonRef.current;
    focusTarget?.focus();
  }, [sidePanelOpen]);

  const closeSidePanel = () => {
    panelWasToggled.current = true;
    setSidePanelOpen(false);
  };

  const openSidePanel = () => {
    panelWasToggled.current = true;
    setSidePanelOpen(true);
  };

  // Fetch analysis details
  const { data: analysis, isLoading, error, refetch } = useQuery({
    queryKey: ['analysis', analysisId],
    queryFn: () => analysisApi.get(analysisId).then(res => res.data),
    refetchInterval: (query) => {
      // Refetch every 3 seconds if status is pending or running
      const status = query.state.data?.status;
      if (status === 'pending' || status === 'running') {
        return 3000;
      }
      return false;
    },
  });

  const dataIssue = getDataIssue(analysis);
  const hasUsableResults = analysis?.status === 'completed' && !dataIssue;

  // Fetch crash data when analysis is complete
  const {
    data: crashData,
    isLoading: crashDataLoading,
    isError: crashDataError,
    refetch: refetchCrashData,
  } = useQuery({
    queryKey: ['crashes', analysisId],
    queryFn: () => analysisApi.getCrashes(analysisId).then(res => res.data),
    enabled: hasUsableResults,
  });

  // Fetch HIN data when analysis is complete
  const {
    data: hinData,
    isLoading: hinDataLoading,
    isError: hinDataError,
    refetch: refetchHINData,
  } = useQuery({
    queryKey: ['hin', analysisId],
    queryFn: () => analysisApi.getHIN(analysisId).then(res => res.data),
    enabled: hasUsableResults,
  });

  // Get map center from first crash or default to NJ
  const getMapCenter = () => {
    const firstCrash = crashData?.features?.find(
      (feature) => feature.geometry?.type === 'Point' && feature.geometry.coordinates?.length >= 2
    );
    if (firstCrash) {
      const coords = firstCrash.geometry.coordinates;
      return [coords[1], coords[0]]; // Leaflet uses [lat, lon]
    }
    return [40.0583, -74.4057]; // Center of NJ
  };

  const getSeverityColor = (severity) => {
    const colors = {
      fatal: '#dc2626',
      serious_injury: '#ea580c',
      minor_injury: '#f59e0b',
      injury_unknown: '#7c3aed',
      property_damage: '#3b82f6',
    };
    return colors[severity] || '#6b7280';
  };

  const getStatusDisplay = (status) => {
    if (dataIssue) {
      return {
        icon: AlertCircle,
        color: 'text-amber-800',
        bg: 'bg-amber-50',
        text: dataIssue.title,
      };
    }

    const displays = {
      pending: { icon: Clock, color: 'text-gray-600', bg: 'bg-gray-50', text: 'Pending' },
      running: { icon: Loader2, color: 'text-primary', bg: 'bg-blue-50', text: 'Running' },
      completed: { icon: CheckCircle, color: 'text-success', bg: 'bg-green-50', text: 'Completed' },
      failed: { icon: AlertCircle, color: 'text-error', bg: 'bg-red-50', text: 'Failed' },
    };
    return displays[status] || displays.pending;
  };

  if (isLoading) {
    return (
      <div className="h-full min-h-0 min-w-0 flex-1 flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-primary animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-full min-h-0 min-w-0 flex-1 overflow-y-auto p-4 flex flex-col">
        <div className="max-w-md w-full my-auto mx-auto bg-white rounded-xl shadow-sm border border-gray-200 p-8 text-center">
          <AlertCircle className="w-12 h-12 text-red-600 mx-auto mb-4" />
          <h2 className="text-xl font-semibold text-gray-900 mb-2">Error Loading Analysis</h2>
          <p className="text-gray-600 mb-6">Unable to load analysis data. Please try again.</p>
          <button
            onClick={() => refetch()}
            className="px-6 py-2 bg-primary hover:bg-primary-hover text-white rounded-lg transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }


  const handleDownloadPDF = async () => {
    setIsDownloading(true);
    try {
      const response = await exportApi.downloadPDF(analysisId);
      
      // Create blob and download
      const blob = new Blob([response.data], { type: 'application/pdf' });
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `HIN_Analysis_${analysis.municipality_name?.replace(/\s+/g, '_')}_${analysis.start_year}-${analysis.end_year}.pdf`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (error) {
      console.error('Error downloading PDF:', error);
      alert('Error downloading PDF. Please try again.');
    } finally {
      setIsDownloading(false);
    }
  };

  const handleDownloadCSV = async (dataType) => {
    setDownloadingCSV(dataType);
    try {
      const response = await exportApi.downloadCSV(analysisId, dataType);
      const blob = new Blob([response.data], { type: 'text/csv' });
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      const suffix = dataType === 'crashes' ? 'crashes' : 'hin_segments';
      link.href = url;
      link.download = `HIN_Analysis_${analysis.municipality_name?.replace(/\s+/g, '_')}_${analysis.start_year}-${analysis.end_year}_${suffix}.csv`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (error) {
      console.error('Error downloading CSV:', error);
      alert('Error downloading CSV. Please try again.');
    } finally {
      setDownloadingCSV(null);
    }
  };

  const statusDisplay = getStatusDisplay(analysis.status);
  const StatusIcon = statusDisplay.icon;
  const dataQuality = analysis.data_quality || {};
  const geocodeCounts = dataQuality.geocode_counts || {};
  const reportedLocationCount = formatQualityCount(geocodeCounts.reported);
  const estimatedLocationCount = formatQualityCount(geocodeCounts.route_milepost);
  const otherLocationCounts = Object.entries(geocodeCounts).filter(
    ([method, count]) => (
      method !== 'reported'
      && method !== 'route_milepost'
      && formatQualityCount(count) !== null
    )
  );
  const sviYears = Array.isArray(dataQuality.svi_years)
    ? dataQuality.svi_years.filter(Number.isInteger)
    : [];
  const mapDataLoading = hasUsableResults && (crashDataLoading || hinDataLoading);
  const mapDataError = hasUsableResults && (crashDataError || hinDataError);
  const mapDataReady = hasUsableResults && crashData && hinData;

  return (
    <div className="relative h-full min-h-0 min-w-0 flex-1 overflow-hidden">
      {/* Map Container */}
      <div className={`absolute inset-0 z-0 ${sidePanelOpen ? 'md:right-96' : ''}`}>
        {mapDataReady ? (
          <MapContainer
            center={getMapCenter()}
            zoom={13}
            style={{ height: '100%', width: '100%' }}
            zoomControl={true}
          >
            <AnalysisMapView crashData={crashData} hinData={hinData} sidePanelOpen={sidePanelOpen} resetKey={resetKey} />
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />

            {/* Crash Points */}
            {crashData.features?.map((feature, idx) => {
              const coordinates = feature.geometry?.coordinates;
              if (feature.geometry?.type !== 'Point' || !isMapCoordinate(coordinates)) return null;

              const properties = feature.properties || {};
              return (
                <CircleMarker
                  key={feature.id || `crash-${idx}`}
                  center={toLeafletPoint(coordinates)}
                  radius={4}
                  fillColor={getSeverityColor(properties.severity)}
                  color="#fff"
                  weight={1}
                  fillOpacity={0.7}
                >
                  <Popup>
                    <div className="text-sm">
                      <p className="font-medium mb-1">
                        {formatSeverity(properties.severity)}
                      </p>
                      <p className="text-gray-600">
                        {properties.date}
                      </p>
                      {properties.road_name && (
                        <p className="text-gray-600 mt-1">
                          {properties.road_name}
                        </p>
                      )}
                    </div>
                  </Popup>
                </CircleMarker>
              );
            })}

            {/* High Injury Network */}
            {hinData.features?.map((feature, idx) => {
              const positions = getLinePositions(feature.geometry);
              if (!positions?.length) return null;

              const properties = feature.properties || {};
              return (
                <Polyline
                  key={feature.id || `hin-${idx}`}
                  positions={positions}
                  color={getHINColor(properties.crash_rate)}
                  weight={4}
                  opacity={0.8}
                >
                  <Popup>
                    <div className="text-sm">
                      <p className="font-medium mb-1">HIN Segment</p>
                      {properties.road_name && (
                        <p className="text-gray-600">Road: {properties.road_name}</p>
                      )}
                      <p className="text-gray-600">Crashes: {properties.crash_count ?? 0}</p>
                      {typeof properties.crash_rate === 'number' && (
                        <p className="text-gray-600">
                          Crash rate: {properties.crash_rate.toFixed(2)} per mile/year
                        </p>
                      )}
                      {properties.corridor_name && (
                        <p className="text-gray-600">Corridor: {properties.corridor_name}</p>
                      )}
                      {properties.in_vulnerable_tract && (
                        <p className="text-gray-600">In Vulnerable Area</p>
                      )}
                    </div>
                  </Popup>
                </Polyline>
              );
            })}
          </MapContainer>
        ) : mapDataError ? (
          <div className="h-full w-full bg-gray-100 flex items-center justify-center p-4">
            <div className="max-w-md text-center">
              <AlertCircle className="w-12 h-12 text-red-600 mx-auto mb-4" />
              <p className="font-medium text-gray-900">Unable to load map data</p>
              <p className="text-sm text-gray-600 mt-2 mb-4">
                Crash or HIN results could not be retrieved.
              </p>
              <button
                onClick={() => {
                  refetchCrashData();
                  refetchHINData();
                }}
                className="px-4 py-2 bg-primary hover:bg-primary-hover text-white rounded-lg transition-colors"
              >
                Retry map data
              </button>
            </div>
          </div>
        ) : dataIssue ? (
          <div className="h-full w-full bg-amber-50 flex items-center justify-center p-4">
            <div className="max-w-md text-center">
              <AlertCircle className="w-12 h-12 text-amber-700 mx-auto mb-4" />
              <p className="font-medium text-gray-900">Analysis results unavailable</p>
              <p className="text-sm text-gray-600 mt-2">
                Run a new analysis after confirming the loaded crash data.
              </p>
            </div>
          </div>
        ) : analysis.status === 'failed' ? (
          <div className="h-full w-full bg-gray-100 flex items-center justify-center p-4">
            <div className="max-w-md text-center">
              <AlertCircle className="w-12 h-12 text-red-600 mx-auto mb-4" />
              <p className="font-medium text-gray-900">Analysis failed</p>
              <p className="text-sm text-gray-600 mt-2">
                {analysis.error_message || 'The analysis could not be completed.'}
              </p>
            </div>
          </div>
        ) : (
          <div className="h-full w-full bg-gray-100 flex items-center justify-center">
            <div className="text-center">
              <Loader2 className="w-12 h-12 text-primary animate-spin mx-auto mb-4" />
              <p className="text-gray-600">
                {mapDataLoading ? 'Loading map data...' : 'Analysis in progress...'}
              </p>
            </div>
          </div>
        )}
        {mapDataReady && (
          <div className="absolute top-3 bottom-6 left-14 z-[1000] max-w-[calc(100%-4rem)] flex flex-col md:flex-row items-start gap-2 pointer-events-none">
            <button onClick={() => setResetKey(key => key + 1)} className="shrink-0 min-h-[44px] rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-medium shadow-md hover:bg-gray-50 pointer-events-auto">Reset view</button>
          </div>
        )}
      </div>

      {/* Side Panel */}
      <div
        aria-hidden={!sidePanelOpen}
        inert={sidePanelOpen ? undefined : ''}
        className={`absolute top-0 right-0 z-10 h-full w-full sm:w-96 bg-white shadow-2xl transform transition-transform duration-300 ease-in-out ${
          sidePanelOpen ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        <div className="h-full min-h-0 flex flex-col">
          {/* Panel Header */}
          <div className="shrink-0 flex items-center justify-between p-6 border-b border-gray-200">
            <h2 className="text-lg font-semibold text-gray-900">Analysis Details</h2>
            <button
              ref={closePanelButtonRef}
              onClick={closeSidePanel}
              aria-label="Close analysis details"
              className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
            >
              <X className="w-5 h-5 text-gray-600" />
            </button>
          </div>

          {/* Panel Content */}
          <div
            role="region"
            aria-label="Analysis details content"
            tabIndex="0"
            className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-6 space-y-6 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
          >
            {/* Status Card */}
            <div className={`${statusDisplay.bg} rounded-lg p-4`}>
              <div className="flex items-center space-x-3">
                <StatusIcon className={`w-5 h-5 ${statusDisplay.color} ${analysis.status === 'running' ? 'animate-spin' : ''}`} />
                <div>
                  <p className="text-sm font-medium text-gray-900">Status</p>
                  <p className={`text-sm ${statusDisplay.color}`}>{statusDisplay.text}</p>
                </div>
              </div>
            </div>

            {/* Municipality Info */}
            <div>
              <h3 className="text-sm font-medium text-gray-900 mb-3">Municipality</h3>
              <div className="bg-gray-50 rounded-lg p-4">
                <p className="text-sm font-medium text-gray-900">{analysis.municipality_name || 'Loading...'}</p>
                <p className="text-xs text-gray-600 mt-1">
                  Analysis Period: {analysis.start_year} - {analysis.end_year}
                </p>
              </div>
            </div>

            {/* Statistics */}
            {hasUsableResults && (
              <>
                <div>
                  <h3 className="text-sm font-medium text-gray-900 mb-3">Crash Statistics</h3>
                  <div className="space-y-3">
                    <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                      <span className="text-sm text-gray-600">Total Crashes</span>
                      <span className="text-sm font-semibold text-gray-900">{analysis.total_crashes || 0}</span>
                    </div>
                    <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                      <span className="text-sm text-gray-600">Fatalities</span>
                      <span className="text-sm font-semibold text-red-600">{formatCount(analysis.total_fatalities)}</span>
                    </div>
                    <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                      <span className="text-sm text-gray-600">Injuries</span>
                      <span className="text-sm font-semibold text-orange-600">{formatCount(analysis.total_injuries)}</span>
                    </div>
                  </div>
                </div>

                <div>
                  <h3 className="text-sm font-medium text-gray-900 mb-3">High Injury Network</h3>
                  <div className="space-y-3">
                    <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                      <span className="text-sm text-gray-600">HIN Miles</span>
                      <span className="text-sm font-semibold text-gray-900">{(analysis.hin_miles || 0).toFixed(2)}</span>
                    </div>
                    <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                      <span className="text-sm text-gray-600">HIN Segments</span>
                      <span className="text-sm font-semibold text-gray-900">{analysis.hin_segment_count || 0}</span>
                    </div>
                  </div>
                </div>

                <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
                  <p className="text-sm font-medium text-amber-950">Exploratory screening only</p>
                  <p className="text-sm text-amber-900 mt-1">
                    These results support preliminary planning and should be reviewed with local knowledge and source data before decisions are made.
                  </p>
                </div>

                <div>
                  <h3 className="text-sm font-medium text-gray-900 mb-3">About the data</h3>
                  <div className="bg-gray-50 rounded-lg p-4 space-y-4 text-sm text-gray-700">
                    <div>
                      <h4 className="font-medium text-gray-900">Crash locations</h4>
                      <div className="mt-1 space-y-1">
                        {reportedLocationCount !== null && (
                          <p>
                            {reportedLocationCount} {geocodeCounts.reported === 1 ? 'crash uses' : 'crashes use'} coordinates reported in the source records.
                          </p>
                        )}
                        {estimatedLocationCount !== null && (
                          <p>
                            {estimatedLocationCount} {geocodeCounts.route_milepost === 1 ? 'crash location is an estimate' : 'crash locations are estimates'} based on road names and mile markers.
                          </p>
                        )}
                        {reportedLocationCount === null && estimatedLocationCount === null && (
                          <p>Reported-coordinate and estimated-location counts are not available for this analysis.</p>
                        )}
                        {reportedLocationCount === null && estimatedLocationCount !== null && (
                          <p>The reported-coordinate count is not available.</p>
                        )}
                        {reportedLocationCount !== null && estimatedLocationCount === null && (
                          <p>The estimated-location count is not available.</p>
                        )}
                      </div>
                    </div>

                    <div>
                      <h4 className="font-medium text-gray-900">People killed or injured</h4>
                      {dataQuality.casualty_counts_complete === true ? (
                        <p className="mt-1">
                          Fatality and injury counts are recorded for every crash included in this analysis. This does not mean every crash was captured in the loaded data.
                        </p>
                      ) : (
                        <p className="mt-1">
                          Some fatality and injury counts may be missing from the included crash records.
                        </p>
                      )}
                    </div>

                    <div>
                      <h4 className="font-medium text-gray-900">Injury severity</h4>
                      <p className="mt-1">
                        {dataQuality.injury_detail_available === true
                          ? 'The included records distinguish serious from minor injuries.'
                          : dataQuality.injury_detail_available === false
                            ? 'The source does not consistently distinguish serious from minor injuries, so this detail is limited.'
                            : "Serious and minor injury detail isn't available for this analysis."}
                      </p>
                    </div>

                    <div>
                      <h4 className="font-medium text-gray-900">Bicycle involvement</h4>
                      <p className="mt-1">
                        {dataQuality.bicycle_data_available === true
                          ? 'Bicycle involvement is available for the included records.'
                          : dataQuality.bicycle_data_available === false
                            ? 'Bicycle involvement is not reliably available in the source records.'
                            : "Bicycle-involvement information isn't available for this analysis."}
                      </p>
                    </div>

                    <div>
                      <h4 className="font-medium text-gray-900">Community conditions</h4>
                      {dataQuality.svi_available === true ? (
                        <p className="mt-1">
                          Uses neighborhood data{sviYears.length ? ` from ${sviYears.join(', ')}` : ''} to help understand where support may be needed. The Social Vulnerability Index (SVI) does not measure individual people.
                        </p>
                      ) : (
                        <p className="mt-1">Community-condition information isn't available for this analysis.</p>
                      )}
                    </div>

                    <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-amber-950">
                      Some crashes may be missing. Crashes we couldn't locate are not shown, and estimated locations may differ from where a crash occurred.
                    </div>

                    <details className="border-t border-gray-200 pt-3">
                      <summary className="cursor-pointer min-h-[44px] py-2 font-medium text-gray-900 rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2">
                        Technical details
                      </summary>
                      <div className="mt-3 space-y-3 min-w-0 text-xs text-gray-600">
                        <p>Version identifiers are used internally to track the data and analysis method that produced this result.</p>
                        {dataQuality.coverage_note && <p>{dataQuality.coverage_note}</p>}
                        {(dataQuality.method_version || analysis.input_version) && (
                          <dl className="space-y-2">
                            {dataQuality.method_version && (
                              <div className="flex items-start justify-between gap-4 min-w-0">
                                <dt className="shrink-0">Method version</dt>
                                <dd className="font-medium text-gray-900 text-right min-w-0">{dataQuality.method_version}</dd>
                              </div>
                            )}
                            {analysis.input_version && (
                              <div className="flex items-start justify-between gap-4 min-w-0">
                                <dt className="shrink-0">Input version</dt>
                                <dd
                                  className="font-medium text-gray-900 text-right min-w-0"
                                  style={{ overflowWrap: 'anywhere' }}
                                >
                                  {formatInputVersion(analysis.input_version)}
                                </dd>
                              </div>
                            )}
                          </dl>
                        )}
                        {otherLocationCounts.length > 0 && (
                          <div>
                            <p className="font-medium text-gray-900">Other location methods</p>
                            <div className="mt-1 space-y-1">
                              {otherLocationCounts.map(([method, count]) => (
                                <p key={method}>{formatQualityLabel(method)}: {formatQualityCount(count)}</p>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    </details>
                  </div>
                </div>

                {/* Export Buttons */}
                <div className="space-y-3">
                  <button
                    onClick={handleDownloadPDF}
                    disabled={isDownloading}
                    className="w-full flex items-center justify-center space-x-2 bg-primary hover:bg-primary-hover disabled:bg-gray-400 disabled:cursor-not-allowed text-white py-3 px-4 rounded-lg transition-colors"
                  >
                    {isDownloading ? (
                      <Loader2 className="w-5 h-5 animate-spin" />
                    ) : (
                      <FileText className="w-5 h-5" />
                    )}
                    <span className="font-medium">
                      {isDownloading ? 'Generating PDF...' : 'Download PDF Report'}
                    </span>
                  </button>
                  <button
                    onClick={() => handleDownloadCSV('crashes')}
                    disabled={Boolean(downloadingCSV)}
                    className="w-full flex items-center justify-center space-x-2 border border-primary text-primary hover:bg-blue-50 disabled:border-gray-300 disabled:text-gray-400 disabled:cursor-not-allowed py-3 px-4 rounded-lg transition-colors"
                  >
                    {downloadingCSV === 'crashes' && <Loader2 className="w-5 h-5 animate-spin" />}
                    <span className="font-medium">Download crashes CSV</span>
                  </button>
                  <button
                    onClick={() => handleDownloadCSV('hin_segments')}
                    disabled={Boolean(downloadingCSV)}
                    className="w-full flex items-center justify-center space-x-2 border border-primary text-primary hover:bg-blue-50 disabled:border-gray-300 disabled:text-gray-400 disabled:cursor-not-allowed py-3 px-4 rounded-lg transition-colors"
                  >
                    {downloadingCSV === 'hin_segments' && <Loader2 className="w-5 h-5 animate-spin" />}
                    <span className="font-medium">Download HIN CSV</span>
                  </button>
                </div>
              </>
            )}

            {dataIssue && (
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
                <div className="flex items-start space-x-3">
                  <AlertCircle className="w-5 h-5 text-amber-700 flex-shrink-0 mt-0.5" />
                  <div>
                    <p className="text-sm font-medium text-amber-950">Analysis results unavailable</p>
                    <p className="text-sm text-amber-900 mt-1">
                      {analysis.data_message || dataIssue.fallbackMessage}
                    </p>
                    <Link
                      to="/"
                      className="inline-block mt-3 text-sm font-medium text-amber-950 underline"
                    >
                      Run a new analysis
                    </Link>
                  </div>
                </div>
              </div>
            )}

            {/* Error Message */}
            {analysis.status === 'failed' && (
              <div className="bg-red-50 border border-red-200 rounded-lg p-4">
                <div className="flex items-start space-x-3">
                  <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
                  <div>
                    <p className="text-sm font-medium text-red-900">Analysis Failed</p>
                    <p className="text-sm text-red-700 mt-1">
                      {analysis.error_message || 'The analysis could not be completed.'}
                    </p>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Toggle Button (when panel is closed) */}
      {!sidePanelOpen && (
        <button
          ref={openPanelButtonRef}
          onClick={openSidePanel}
          aria-label="Open analysis details"
          className="absolute top-4 right-4 z-20 p-3 bg-white shadow-lg rounded-lg hover:bg-gray-50 transition-colors"
        >
          <Info className="w-5 h-5 text-gray-600" />
        </button>
      )}
    </div>
  );
}

export default AnalysisPage;
