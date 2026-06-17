import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000/api';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const municipalitiesApi = {
  list: (county = null) => {
    const params = county ? { county } : {};
    return api.get('/municipalities', { params });
  },
  get: (muniId) => {
    return api.get(`/municipalities/${muniId}`);
  },
  getSummary: (muniId) => {
    return api.get(`/municipalities/${muniId}/summary`);
  },
};

export const analysisApi = {
  create: (data) => {
    return api.post('/analysis', data);
  },
  list: (muniId = null, status = null) => {
    const params = {};
    if (muniId) params.muni_id = muniId;
    if (status) params.status = status;
    return api.get('/analysis', { params });
  },
  get: (analysisId) => {
    return api.get(`/analysis/${analysisId}`);
  },
  getSummary: (analysisId) => {
    return api.get(`/analysis/${analysisId}/summary`);
  },
  getCrashes: (analysisId, filters = {}) => {
    return api.get(`/analysis/${analysisId}/crashes`, { params: filters });
  },
  getHIN: (analysisId, hinType = 'general') => {
    return api.get(`/analysis/${analysisId}/hin`, { params: { hin_type: hinType } });
  },
  delete: (analysisId) => {
    return api.delete(`/analysis/${analysisId}`);
  },
};

export const exportApi = {
  downloadPDF: (analysisId) => {
    return api.get(`/analysis/${analysisId}/export/pdf`, { responseType: 'blob' });
  },
  downloadGeoJSON: (analysisId, layer = 'crashes') => {
    // Download crashes or HIN as GeoJSON
    const endpoint = layer === 'crashes'
      ? `/analysis/${analysisId}/crashes`
      : `/analysis/${analysisId}/hin`;
    return api.get(endpoint, { responseType: 'blob' });
  },
};

export default api;
