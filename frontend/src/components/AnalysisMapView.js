import { useCallback, useEffect, useMemo } from 'react';
import { useMap } from 'react-leaflet';

export const isMapCoordinate = (point) => Array.isArray(point)
  && point.length >= 2 && Number.isFinite(point[0]) && Number.isFinite(point[1])
  && Math.abs(point[0]) <= 180 && Math.abs(point[1]) <= 90;

// Frame all results, even when a layer or severity is hidden.
export default function AnalysisMapView({ crashData, hinData, sidePanelOpen, resetKey }) {
  const map = useMap();
  const points = useMemo(() => {
    const coordinates = [];
    for (const feature of [...(crashData.features || []), ...(hinData.features || [])]) {
      const geometry = feature.geometry;
      if (geometry?.type === 'Point') coordinates.push(geometry.coordinates);
      if (geometry?.type === 'LineString') coordinates.push(...(geometry.coordinates || []));
      if (geometry?.type === 'MultiLineString') coordinates.push(...(geometry.coordinates || []).flat());
    }
    return coordinates.filter(isMapCoordinate).map(([longitude, latitude]) => [latitude, longitude]);
  }, [crashData, hinData]);

  const fitResults = useCallback(() => {
    map.invalidateSize({ pan: false });
    if (!points.length) return;
    map.fitBounds(points, {
      paddingTopLeft: [24, 64],
      paddingBottomRight: [24, 24],
      maxZoom: 16,
      animate: false,
    });
  }, [map, points]);

  useEffect(() => {
    fitResults();
    // Mobile sheet expansion changes the map container without a window resize.
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(fitResults);
    observer?.observe(map.getContainer());
    window.addEventListener('resize', fitResults);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', fitResults);
    };
  }, [fitResults, map, resetKey, sidePanelOpen]);

  return null;
}
