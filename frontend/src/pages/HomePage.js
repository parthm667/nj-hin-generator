import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation } from '@tanstack/react-query';
import { municipalitiesApi, analysisApi } from '../services/api';
import { MapPin, Calendar, TrendingUp, AlertCircle, Loader2 } from 'lucide-react';

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
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
      {/* Hero Section */}
      <div className="text-center mb-16">
        <h1 className="text-4xl font-bold text-gray-900 mb-4">
          High Injury Network Analysis
        </h1>
        <p className="text-lg text-gray-600 max-w-2xl mx-auto">
          Identify statistically significant crash corridors to support Safe Streets and Roads for All grant applications
        </p>
      </div>

      {/* Main Form Card */}
      <div className="max-w-2xl mx-auto">
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
          <div className="p-8">
            <h2 className="text-xl font-semibold text-gray-900 mb-6">
              Create Analysis
            </h2>

            {isLoading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="w-8 h-8 text-primary animate-spin" />
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-6">
                {/* Municipality Selection */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Municipality
                  </label>
                  <div className="relative">
                    <MapPin className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
                    <select
                      value={selectedMuni}
                      onChange={(e) => setSelectedMuni(e.target.value)}
                      className="w-full pl-10 pr-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary focus:border-transparent outline-none transition-all"
                      required
                    >
                      <option value="">Select a municipality...</option>
                      {municipalities?.map((muni) => (
                        <option key={muni.muni_id} value={muni.muni_id}>
                          {muni.name} — {muni.county} County
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                {/* Year Selection */}
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Start Year
                    </label>
                    <div className="relative">
                      <Calendar className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
                      <input
                        type="number"
                        min="2000"
                        max="2024"
                        value={startYear}
                        onChange={(e) => setStartYear(parseInt(e.target.value))}
                        className="w-full pl-10 pr-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary focus:border-transparent outline-none transition-all"
                        required
                      />
                    </div>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      End Year
                    </label>
                    <div className="relative">
                      <Calendar className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
                      <input
                        type="number"
                        min="2000"
                        max="2024"
                        value={endYear}
                        onChange={(e) => setEndYear(parseInt(e.target.value))}
                        className="w-full pl-10 pr-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary focus:border-transparent outline-none transition-all"
                        required
                      />
                    </div>
                  </div>
                </div>

                {/* Submit Button */}
                <button
                  type="submit"
                  disabled={createAnalysisMutation.isPending || !selectedMuni}
                  className="w-full flex items-center justify-center space-x-2 bg-primary hover:bg-primary-hover text-white font-medium py-3 px-6 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {createAnalysisMutation.isPending ? (
                    <>
                      <Loader2 className="w-5 h-5 animate-spin" />
                      <span>Creating Analysis...</span>
                    </>
                  ) : (
                    <>
                      <TrendingUp className="w-5 h-5" />
                      <span>Run Analysis</span>
                    </>
                  )}
                </button>

                {createAnalysisMutation.isError && (
                  <div className="flex items-start space-x-3 p-4 bg-red-50 border border-red-200 rounded-lg">
                    <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
                    <div className="text-sm text-red-800">
                      <p className="font-medium">Error creating analysis</p>
                      <p className="mt-1 text-red-700">{createAnalysisMutation.error.message}</p>
                    </div>
                  </div>
                )}
              </form>
            )}
          </div>
        </div>

        {/* Info Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-12">
          <div className="text-center">
            <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mx-auto mb-4">
              <MapPin className="w-6 h-6 text-primary" />
            </div>
            <h3 className="text-sm font-medium text-gray-900 mb-2">
              Statistical Analysis
            </h3>
            <p className="text-sm text-gray-600">
              Poisson-based significance testing identifies true crash hotspots
            </p>
          </div>

          <div className="text-center">
            <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mx-auto mb-4">
              <TrendingUp className="w-6 h-6 text-primary" />
            </div>
            <h3 className="text-sm font-medium text-gray-900 mb-2">
              Grant Ready
            </h3>
            <p className="text-sm text-gray-600">
              Results formatted for SS4A Action Plan submissions
            </p>
          </div>

          <div className="text-center">
            <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mx-auto mb-4">
              <Calendar className="w-6 h-6 text-primary" />
            </div>
            <h3 className="text-sm font-medium text-gray-900 mb-2">
              Multi-Year Data
            </h3>
            <p className="text-sm text-gray-600">
              Analyze up to 5 years of crash history for robust results
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

export default HomePage;
