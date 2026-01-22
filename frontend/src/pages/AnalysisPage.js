import React, { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { analysisApi, exportApi } from '../services/api';
import { MapContainer, TileLayer, CircleMarker, Polyline, Popup } from 'react-leaflet';
import { 
  Loader2, AlertCircle, CheckCircle, Clock,
  X, Info, Download, Users, MapPin, TrendingUp, FileText
} from 'lucide-react';
import 'leaflet/dist/leaflet.css';

function AnalysisPage() {
  const { analysisId } = useParams();
  const [sidePanelOpen, setSidePanelOpen] = useState(true);
  const [isDownloading, setIsDownloading] = useState(false);

  // Fetch analysis details
  const { data: analysis, isLoading, error, refetch } = useQuery({
    queryKey: ['analysis', analysisId],
    queryFn: () => analysisApi.get(analysisId).then(res => res.data),
    refetchInterval: (data) => {
      // Refetch every 3 seconds if status is pending or running
      if (data?.status === 'pending' || data?.status === 'running') {
        return 3000;
      }
      return false;
    },
  });

  // Fetch crash data when analysis is complete
  const { data: crashData } = useQuery({
    queryKey: ['crashes', analysisId],
    queryFn: () => analysisApi.getCrashes(analysisId).then(res => res.data),
    enabled: analysis?.status === 'completed',
  });

  // Fetch HIN data when analysis is complete
  const { data: hinData } = useQuery({
    queryKey: ['hin', analysisId],
    queryFn: () => analysisApi.getHIN(analysisId).then(res => res.data),
    enabled: analysis?.status === 'completed',
  });

  // Get map center from first crash or default to NJ
  const getMapCenter = () => {
    if (crashData?.features && crashData.features.length > 0) {
      const coords = crashData.features[0].geometry.coordinates;
      return [coords[1], coords[0]]; // Leaflet uses [lat, lon]
    }
    return [40.0583, -74.4057]; // Center of NJ
  };

  const getSeverityColor = (severity) => {
    const colors = {
      fatal: '#dc2626',
      serious_injury: '#ea580c',
      minor_injury: '#f59e0b',
      property_damage: '#3b82f6',
    };
    return colors[severity] || '#6b7280';
  };

  const getStatusDisplay = (status) => {
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
      <div className="h-screen flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-primary animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-screen flex items-center justify-center p-4">
        <div className="max-w-md w-full bg-white rounded-xl shadow-sm border border-gray-200 p-8 text-center">
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

  const statusDisplay = getStatusDisplay(analysis.status);
  const StatusIcon = statusDisplay.icon;

  return (
    <div className="h-[calc(100vh-4rem)] relative">
      {/* Map Container */}
      <div className="absolute inset-0">
        {analysis.status === 'completed' && crashData ? (
          <MapContainer
            center={getMapCenter()}
            zoom={13}
            style={{ height: '100%', width: '100%' }}
            zoomControl={false}
          >
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />

            {/* Crash Points */}
            {crashData.features?.map((feature, idx) => (
              <CircleMarker
                key={idx}
                center={[
                  feature.geometry.coordinates[1],
                  feature.geometry.coordinates[0]
                ]}
                radius={4}
                fillColor={getSeverityColor(feature.properties.severity)}
                color="#fff"
                weight={1}
                fillOpacity={0.7}
              >
                <Popup>
                  <div className="text-sm">
                    <p className="font-medium mb-1">
                      {feature.properties.severity.replace('_', ' ').toUpperCase()}
                    </p>
                    <p className="text-gray-600">
                      {feature.properties.date}
                    </p>
                    {feature.properties.road_name && (
                      <p className="text-gray-600 mt-1">
                        {feature.properties.road_name}
                      </p>
                    )}
                  </div>
                </Popup>
              </CircleMarker>
            ))}
          </MapContainer>
        ) : (
          <div className="h-full w-full bg-gray-100 flex items-center justify-center">
            <div className="text-center">
              <Loader2 className="w-12 h-12 text-primary animate-spin mx-auto mb-4" />
              <p className="text-gray-600">Loading map...</p>
            </div>
          </div>
        )}
      </div>

      {/* Side Panel */}
      <div
        className={`absolute top-0 right-0 h-full w-full sm:w-96 bg-white shadow-2xl transform transition-transform duration-300 ease-in-out ${
          sidePanelOpen ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        <div className="h-full flex flex-col">
          {/* Panel Header */}
          <div className="flex items-center justify-between p-6 border-b border-gray-200">
            <h2 className="text-lg font-semibold text-gray-900">Analysis Details</h2>
            <button
              onClick={() => setSidePanelOpen(false)}
              className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
            >
              <X className="w-5 h-5 text-gray-600" />
            </button>
          </div>

          {/* Panel Content */}
          <div className="flex-1 overflow-y-auto p-6 space-y-6">
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
            {analysis.status === 'completed' && (
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
                      <span className="text-sm font-semibold text-red-600">{analysis.total_fatalities || 0}</span>
                    </div>
                    <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                      <span className="text-sm text-gray-600">Injuries</span>
                      <span className="text-sm font-semibold text-orange-600">{analysis.total_injuries || 0}</span>
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

                {/* Export Button */}
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
                </div>
              </>
            )}

            {/* Error Message */}
            {analysis.status === 'failed' && analysis.error_message && (
              <div className="bg-red-50 border border-red-200 rounded-lg p-4">
                <div className="flex items-start space-x-3">
                  <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
                  <div>
                    <p className="text-sm font-medium text-red-900">Analysis Failed</p>
                    <p className="text-sm text-red-700 mt-1">{analysis.error_message}</p>
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
          onClick={() => setSidePanelOpen(true)}
          className="absolute top-4 right-4 p-3 bg-white shadow-lg rounded-lg hover:bg-gray-50 transition-colors"
        >
          <Info className="w-5 h-5 text-gray-600" />
        </button>
      )}
    </div>
  );
}

export default AnalysisPage;
