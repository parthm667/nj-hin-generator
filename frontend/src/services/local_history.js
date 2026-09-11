const STORAGE_KEY = 'nj-hin-recent-analyses';
const MAX_RECENT_ANALYSES = 20;

const getStorage = () => {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch (error) {
    return null;
  }
};

const normalizeEntry = (entry) => {
  const analysisId = Number(entry?.analysis_id);
  const startYear = Number(entry?.start_year);
  const endYear = Number(entry?.end_year);

  if (
    !Number.isInteger(analysisId)
    || analysisId < 1
    || typeof entry?.municipality_name !== 'string'
    || !entry.municipality_name.trim()
    || !Number.isInteger(startYear)
    || !Number.isInteger(endYear)
  ) {
    return null;
  }

  return {
    analysis_id: analysisId,
    municipality_name: entry.municipality_name.trim(),
    start_year: startYear,
    end_year: endYear,
    created_at: typeof entry.created_at === 'string' ? entry.created_at : new Date().toISOString(),
  };
};

export const getRecentAnalyses = () => {
  const storage = getStorage();
  if (!storage) return [];

  try {
    const stored = JSON.parse(storage.getItem(STORAGE_KEY) || '[]');
    if (!Array.isArray(stored)) return [];
    return stored.map(normalizeEntry).filter(Boolean).slice(0, MAX_RECENT_ANALYSES);
  } catch (error) {
    return [];
  }
};

export const saveRecentAnalysis = (entry) => {
  const normalized = normalizeEntry(entry);
  const current = getRecentAnalyses();
  if (!normalized) return current;

  const updated = [
    normalized,
    ...current.filter((item) => item.analysis_id !== normalized.analysis_id),
  ].slice(0, MAX_RECENT_ANALYSES);

  try {
    getStorage()?.setItem(STORAGE_KEY, JSON.stringify(updated));
  } catch (error) {
    // Storage can be blocked or full; keep the in-memory result usable.
  }

  return updated;
};

export const forgetRecentAnalysis = (analysisId) => {
  const updated = getRecentAnalyses().filter(
    (item) => item.analysis_id !== Number(analysisId)
  );

  try {
    const storage = getStorage();
    if (updated.length === 0) {
      storage?.removeItem(STORAGE_KEY);
    } else {
      storage?.setItem(STORAGE_KEY, JSON.stringify(updated));
    }
  } catch (error) {
    // Forgetting locally should remain safe when storage is unavailable.
  }

  return updated;
};
