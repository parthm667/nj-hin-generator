import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { analysisApi, exportApi } from '../services/api';
import MapView from '../components/MapView';

function AnalysisPage() {
  const { analysisId } = useParams();
  const navigate = useNavigate();
  const [showCrashes, setShowCrashes] = useState(true);
  const [showHIN, setShowHIN] = useState(true);
  const [hinType, setHINType] = useState('general');

  const { data: analysis, isLoading: analysisLoading, error: analysisError } = useQuery({
    queryKey: ['analysis', analysisId],
    queryFn: () => analysisApi.get(analysisId).then(res => res.data),
    refetchInterval: (data) => {
      if (data?.status === 'running' || data?.status === 'pending') {
        return 5000;
      }
      return false;
    },
  });

  const { data: summary } = useQuery({
    queryKey: ['analysis-summary', analysisId],
    queryFn: () => analysisApi.getSummary(analysisId).then(res => res.data),
    enabled: analysis?.status === 'completed',
  });

  const { data: crashData } = useQuery({
    queryKey: ['crashes', analysisId],
    queryFn: () => analysisApi.getCrashes(analysisId).then(res => res.data),
    enabled: analysis?.status === 'completed' && showCrashes,
  });

  const { data: hinData } = useQuery({
    queryKey: ['hin', analysisId, hinType],
    queryFn: () => analysisApi.getHIN(analysisId, hinType).then(res => res.data),
    enabled: analysis?.status === 'completed' && showHIN,
  });

  const handleDownloadGeoJSON = async () => {
    try {
      const response = await exportApi.downloadGeoJSON(analysisId, 'hin');
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `analysis_${analysisId}_hin.geojson`);
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (error) {
      console.error('Download failed:', error);
      alert('Download failed. Please try again.');
    }
  };

  if (analysisLoading) {
    return (
      <div className="App">
        <div className="header">
          <div className="container">
            <h1>Analysis Loading...</h1>
          </div>
        </div>
        <div className="container">
          <div className="loading">Loading analysis details...</div>
        </div>
      </div>
    );
  }

  if (analysisError) {
    return (
      <div className="App">
        <div className="header">
          <div className="container">
            <h1>Error</h1>
          </div>
        </div>
        <div className="container">
          <div className="error">
            Error loading analysis: {analysisError.message}
          </div>
          <button className="button" onClick={() => navigate('/')}>
            Back to Home
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="App">
      <div className="header">
        <div className="container">
          <h1>High Injury Network Analysis</h1>
          <p>{analysis.municipality_name || `Municipality ID: ${analysis.muni_id}`}</p>
          <p>Years: {analysis.start_year} - {analysis.end_year}</p>
        </div>
      </div>

      <div className="container">
        {analysis.status === 'running' || analysis.status === 'pending' ? (
          <div className="card">
            <h2>Analysis in Progress</h2>
            <p>Status: {analysis.status}</p>
            <p>This page will automatically update when the analysis is complete.</p>
            <div className="loading">Processing crash data and identifying high-risk segments...</div>
          </div>
        ) : analysis.status === 'failed' ? (
          <div className="error">
            <h3>Analysis Failed</h3>
            <p>{analysis.error_message || 'Unknown error occurred'}</p>
            <button className="button" onClick={() => navigate('/')} style={{ marginTop: '10px' }}>
              Start New Analysis
            </button>
          </div>
        ) : (
          <>
            <div className="stats-grid">
              <div className="stat-card">
                <div className="stat-value">{summary?.total_crashes || 0}</div>
                <div className="stat-label">Total Crashes</div>
              </div>
              <div className="stat-card">
                <div className="stat-value">{summary?.fatal_crashes || 0}</div>
                <div className="stat-label">Fatal Crashes</div>
              </div>
              <div className="stat-card">
                <div className="stat-value">{summary?.hin_miles?.toFixed(1) || 0}</div>
                <div className="stat-label">HIN Miles</div>
              </div>
              <div className="stat-card">
                <div className="stat-value">{summary?.hin_corridors || 0}</div>
                <div className="stat-label">HIN Corridors</div>
              </div>
            </div>

            <div className="card">
              <h2>Map Controls</h2>
              <div style={{ display: 'flex', gap: '20px', marginBottom: '15px', flexWrap: 'wrap' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <input
                    type="checkbox"
                    checked={showCrashes}
                    onChange={(e) => setShowCrashes(e.target.checked)}
                  />
                  Show Crash Points
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <input
                    type="checkbox"
                    checked={showHIN}
                    onChange={(e) => setShowHIN(e.target.checked)}
                  />
                  Show HIN Segments
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  HIN Type:
                  <select
                    className="select"
                    style={{ width: 'auto', marginLeft: '8px' }}
                    value={hinType}
                    onChange={(e) => setHINType(e.target.value)}
                  >
                    <option value="general">General</option>
                    <option value="pedestrian">Pedestrian</option>
                    <option value="bicycle">Bicycle</option>
                  </select>
                </label>
              </div>
              <button className="button" onClick={handleDownloadGeoJSON} style={{ marginRight: '10px' }}>
                Download HIN GeoJSON
              </button>
              <button className="button" onClick={() => navigate('/')}>
                New Analysis
              </button>
            </div>

            <div className="card">
              <div className="map-container">
                <MapView
                  crashes={showCrashes ? crashData : null}
                  hinSegments={showHIN ? hinData : null}
                />
              </div>
            </div>

            {summary && (
              <div className="card">
                <h2>Analysis Summary</h2>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <tbody>
                    <tr style={{ borderBottom: '1px solid #e5e7eb' }}>
                      <td style={{ padding: '10px' }}>Total Crashes</td>
                      <td style={{ padding: '10px', fontWeight: 'bold' }}>{summary.total_crashes}</td>
                    </tr>
                    <tr style={{ borderBottom: '1px solid #e5e7eb' }}>
                      <td style={{ padding: '10px' }}>Fatal Crashes</td>
                      <td style={{ padding: '10px', fontWeight: 'bold', color: '#dc2626' }}>{summary.fatal_crashes}</td>
                    </tr>
                    <tr style={{ borderBottom: '1px solid #e5e7eb' }}>
                      <td style={{ padding: '10px' }}>Serious Injury Crashes</td>
                      <td style={{ padding: '10px', fontWeight: 'bold', color: '#ea580c' }}>{summary.serious_injury_crashes}</td>
                    </tr>
                    <tr style={{ borderBottom: '1px solid #e5e7eb' }}>
                      <td style={{ padding: '10px' }}>Pedestrian Crashes</td>
                      <td style={{ padding: '10px', fontWeight: 'bold' }}>{summary.ped_crashes}</td>
                    </tr>
                    <tr style={{ borderBottom: '1px solid #e5e7eb' }}>
                      <td style={{ padding: '10px' }}>Bicycle Crashes</td>
                      <td style={{ padding: '10px', fontWeight: 'bold' }}>{summary.bike_crashes}</td>
                    </tr>
                    <tr style={{ borderBottom: '1px solid #e5e7eb' }}>
                      <td style={{ padding: '10px' }}>High Injury Network Miles</td>
                      <td style={{ padding: '10px', fontWeight: 'bold' }}>{summary.hin_miles.toFixed(2)}</td>
                    </tr>
                    <tr style={{ borderBottom: '1px solid #e5e7eb' }}>
                      <td style={{ padding: '10px' }}>Number of HIN Corridors</td>
                      <td style={{ padding: '10px', fontWeight: 'bold' }}>{summary.hin_corridors}</td>
                    </tr>
                    <tr>
                      <td style={{ padding: '10px' }}>HIN in Vulnerable Areas</td>
                      <td style={{ padding: '10px', fontWeight: 'bold' }}>{summary.vulnerable_tract_percentage.toFixed(1)}%</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default AnalysisPage;
