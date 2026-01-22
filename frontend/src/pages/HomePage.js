import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation } from '@tanstack/react-query';
import { municipalitiesApi, analysisApi } from '../services/api';

function HomePage() {
  const navigate = useNavigate();
  const [selectedMuni, setSelectedMuni] = useState('');
  const [startYear, setStartYear] = useState(2017);
  const [endYear, setEndYear] = useState(2021);

  const { data: municipalities, isLoading } = useQuery({
    queryKey: ['municipalities'],
    queryFn: () => municipalitiesApi.list().then(res => res.data),
  });

  const createAnalysisMutation = useMutation({
    mutationFn: (data) => analysisApi.create(data).then(res => res.data),
    onSuccess: (data) => {
      navigate(`/analysis/${data.analysis_id}`);
    },
  });

  const handleSubmit = (e) => {
    e.preventDefault();

    if (!selectedMuni) {
      alert('Please select a municipality');
      return;
    }

    createAnalysisMutation.mutate({
      muni_id: parseInt(selectedMuni),
      config: {
        start_year: startYear,
        end_year: endYear,
        snap_distance_meters: 50,
        segment_length_miles: 0.1,
        significance_threshold: 0.05,
      },
    });
  };

  return (
    <div className="App">
      <div className="header">
        <div className="container">
          <h1>NJ High Injury Network Generator</h1>
          <p>Automated crash analysis and safety planning for New Jersey municipalities</p>
        </div>
      </div>

      <div className="container">
        <div className="card">
          <h2>Create New Analysis</h2>
          <p style={{ color: '#666', marginBottom: '20px' }}>
            Select your municipality and analysis period to generate a High Injury Network analysis.
          </p>

          {isLoading ? (
            <div className="loading">Loading municipalities...</div>
          ) : (
            <form onSubmit={handleSubmit}>
              <div style={{ marginBottom: '20px' }}>
                <label style={{ display: 'block', marginBottom: '8px', fontWeight: '500' }}>
                  Municipality
                </label>
                <select
                  className="select"
                  value={selectedMuni}
                  onChange={(e) => setSelectedMuni(e.target.value)}
                  required
                >
                  <option value="">Select a municipality...</option>
                  {municipalities?.map((muni) => (
                    <option key={muni.muni_id} value={muni.muni_id}>
                      {muni.name} - {muni.county} County
                    </option>
                  ))}
                </select>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px', marginBottom: '20px' }}>
                <div>
                  <label style={{ display: 'block', marginBottom: '8px', fontWeight: '500' }}>
                    Start Year
                  </label>
                  <input
                    type="number"
                    className="select"
                    min="2000"
                    max="2024"
                    value={startYear}
                    onChange={(e) => setStartYear(parseInt(e.target.value))}
                    required
                  />
                </div>

                <div>
                  <label style={{ display: 'block', marginBottom: '8px', fontWeight: '500' }}>
                    End Year
                  </label>
                  <input
                    type="number"
                    className="select"
                    min="2000"
                    max="2024"
                    value={endYear}
                    onChange={(e) => setEndYear(parseInt(e.target.value))}
                    required
                  />
                </div>
              </div>

              <button
                type="submit"
                className="button"
                disabled={createAnalysisMutation.isPending}
              >
                {createAnalysisMutation.isPending ? 'Creating Analysis...' : 'Run Analysis'}
              </button>

              {createAnalysisMutation.isError && (
                <div className="error" style={{ marginTop: '20px' }}>
                  Error creating analysis: {createAnalysisMutation.error.message}
                </div>
              )}
            </form>
          )}
        </div>

        <div className="card">
          <h2>About This Tool</h2>
          <p>
            The NJ High Injury Network Generator automatically identifies road segments with
            statistically significant crash rates, helping municipalities:
          </p>
          <ul style={{ color: '#666', lineHeight: '1.8' }}>
            <li>Apply for federal Safe Streets and Roads for All (SS4A) grants</li>
            <li>Prioritize safety improvements based on crash data</li>
            <li>Identify vulnerable road user safety issues</li>
            <li>Focus resources on high-impact corridors</li>
          </ul>
        </div>
      </div>
    </div>
  );
}

export default HomePage;
