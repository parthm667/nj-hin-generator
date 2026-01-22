import React from 'react';
import { MapContainer, TileLayer, CircleMarker, Polyline, Popup } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

const MapView = ({ crashes, hinSegments }) => {
  const defaultCenter = [40.0583, -74.4057];
  const defaultZoom = 10;

  const getSeverityColor = (severity) => {
    switch (severity) {
      case 'fatal':
        return '#dc2626';
      case 'serious_injury':
        return '#ea580c';
      case 'minor_injury':
        return '#f59e0b';
      default:
        return '#3b82f6';
    }
  };

  const getHINColor = (crashRate) => {
    if (crashRate > 10) return '#dc2626';
    if (crashRate > 5) return '#ea580c';
    return '#f59e0b';
  };

  return (
    <MapContainer
      center={defaultCenter}
      zoom={defaultZoom}
      style={{ width: '100%', height: '100%' }}
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />

      {crashes?.features?.map((crash, index) => (
        <CircleMarker
          key={`crash-${index}`}
          center={[crash.geometry.coordinates[1], crash.geometry.coordinates[0]]}
          radius={5}
          fillColor={getSeverityColor(crash.properties.severity)}
          color="#fff"
          weight={1}
          opacity={0.8}
          fillOpacity={0.6}
        >
          <Popup>
            <div>
              <strong>Crash Details</strong>
              <br />
              Date: {crash.properties.date}
              <br />
              Severity: {crash.properties.severity}
              <br />
              {crash.properties.ped_involved && 'Pedestrian Involved '}
              {crash.properties.bike_involved && 'Bicycle Involved'}
              <br />
              {crash.properties.road_name && `Road: ${crash.properties.road_name}`}
            </div>
          </Popup>
        </CircleMarker>
      ))}

      {hinSegments?.features?.map((segment, index) => {
        // Note: In production, properly parse LineString coordinates from GeoJSON
        // This is a placeholder - actual implementation needs proper coordinate parsing
        const coords = segment.geometry.coordinates || [];
        if (coords.length < 2) return null;

        return (
          <Polyline
            key={`hin-${index}`}
            positions={coords.map(coord => [coord[1], coord[0]])}
            color={getHINColor(segment.properties.crash_rate)}
            weight={4}
            opacity={0.8}
          >
            <Popup>
              <div>
                <strong>HIN Segment</strong>
                <br />
                {segment.properties.road_name && `Road: ${segment.properties.road_name}`}
                <br />
                Crashes: {segment.properties.crash_count}
                <br />
                Crash Rate: {segment.properties.crash_rate?.toFixed(2)} per mile/year
                <br />
                {segment.properties.corridor_name && `Corridor: ${segment.properties.corridor_name}`}
                <br />
                {segment.properties.in_vulnerable_tract && 'In Vulnerable Area'}
              </div>
            </Popup>
          </Polyline>
        );
      })}
    </MapContainer>
  );
};

export default MapView;
