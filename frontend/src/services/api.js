import axios from 'axios';

export const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000/api';
export const API_DOCS_URL = `${API_BASE_URL.replace(/\/+$/, '').replace(/\/api$/, '')}/docs`;

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const municipalitiesApi = {
  list: (county = null) => {
    const params = county ? { county } : {};
    return api.get('/municipalities/', { params });
  },
  get: (muniId) => {
    return api.get(`/municipalities/${muniId}`);
  },
  getSummary: (muniId) => {
    return api.get(`/municipalities/${muniId}/summary`);
  },
  getCoverage: (muniId) => {
    return api.get(`/municipalities/${muniId}/coverage`);
  },
};

export const analysisApi = {
  create: (data) => {
    return api.post('/analysis/', data);
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
};

export const exportApi = {
  downloadPDF: (analysisId) => {
    return api.get(`/analysis/${analysisId}/export/pdf`, { responseType: 'blob' });
  },
  downloadCSV: (analysisId, dataType) => {
    return api.get(`/export/${analysisId}/csv`, {
      params: { data_type: dataType },
      responseType: 'blob',
    });
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
