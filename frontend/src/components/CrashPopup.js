import React, { useEffect, useId, useRef, useState } from 'react';
import { Popup, useMap } from 'react-leaflet';

const textValue = (value) => typeof value === 'string' ? value.trim() : '';
const knownCount = (value) => Number.isInteger(value) && value >= 0;
const countLabel = (value) => knownCount(value) ? value.toLocaleString('en-US') : 'Not recorded';

export function formatCrashDate(value) {
  const match = typeof value === 'string' && /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return 'Not recorded';
  const [year, month, day] = match.slice(1).map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) {
    return 'Not recorded';
  }
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
}

export function formatCrashTime(value) {
  const match = /^(\d{2}):?(\d{2})$/.exec(textValue(value));
  if (!match) return 'Not recorded';
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (hour > 23 || minute > 59) return 'Not recorded';
  return `${hour % 12 || 12}:${match[2]} ${hour < 12 ? 'AM' : 'PM'}`;
}

// NJDOT NJTR-1 Crash Report Manual (2017), box 98, codes 01–07:
// https://www.nj.gov/transportation/refdata/accident/pdf/NJTR-1CrashReportManual12517.pdf
const LIGHTING = {
  '01': 'Daylight', '02': 'Dawn', '03': 'Dusk', '04': 'Dark — lights off',
  '05': 'Dark — no streetlights', '06': 'Dark — continuous streetlighting',
  '07': 'Dark — spot streetlighting',
  daylight: 'Daylight', dark: 'Dark', dawn: 'Dawn', dusk: 'Dusk',
};

export function formatLighting(value) {
  const code = textValue(value).toLowerCase();
  return code ? Object.prototype.hasOwnProperty.call(LIGHTING, code) ? LIGHTING[code] : 'Unavailable' : 'Not recorded';
}

export function crashSummary(properties) {
  const road = textValue(properties.road_name);
  const location = road ? `Crash on ${road}` : 'Recorded crash';
  const killed = properties.total_killed;
  const injured = properties.total_injured;
  if (!knownCount(killed) && !knownCount(injured)) return `${location}: casualty counts not recorded.`;
  const casualties = (count, outcome) => `${countLabel(count)} ${count === 1 ? 'person' : 'people'} ${outcome}`;
  return `${location}: ${knownCount(killed) ? casualties(killed, 'killed') : 'fatality count not recorded'} and ${
    knownCount(injured) ? casualties(injured, 'injured') : 'injury count not recorded'}.`;
}

const popupBounds = ({ x, y }) => {
  const topPadding = x < 768 ? 120 : 76;
  return {
    width: Math.max(140, Math.min(300, x - 64)),
    maxHeight: Math.max(80, Math.min(520, y - topPadding - 48)),
    topPadding,
  };
};

// React Leaflet mounts these children when the popup opens, so unopened crash
// points do not format details or subscribe to map resizes.
function CrashDetails({ properties, severityLabel, popupRef }) {
  const map = useMap();
  const [bounds, setBounds] = useState(() => popupBounds(map.getSize()));
  const [sourceOpen, setSourceOpen] = useState(false);
  const sourceId = useId();
  useEffect(() => {
    const resize = ({ newSize }) => setBounds(popupBounds(newSize));
    map.on('resize', resize);
    return () => map.off('resize', resize);
  }, [map]);
  useEffect(() => {
    const popup = popupRef.current;
    if (popup) {
      // Leave the floating Layers/Reset controls visible above the incident.
      popup.options.autoPanPaddingTopLeft = [12, bounds.topPadding];
      popup.update();
    }
  }, [bounds, sourceOpen, popupRef]);

  const date = formatCrashDate(properties.date);
  const time = formatCrashTime(properties.time);
  const sourceRecord = textValue(properties.external_id);
  const njdotSource = /^NJDOT:\d{4}:[A-Z_]+:[^\s:]+$/.test(sourceRecord);
  const estimated = properties.geocode_quality === 'route_milepost';
  const reported = properties.geocode_quality === 'reported';
  const personRows = [
    ['All people killed', properties.total_killed],
    ['All people injured', properties.total_injured],
    ['Pedestrians killed', properties.pedestrians_killed],
    ['Pedestrians injured', properties.pedestrians_injured],
  ];
  const sourceRows = [
    ['Source', njdotSource ? 'NJDOT crash archive' : 'Not recorded'],
    ['Source record ID', sourceRecord || 'Not recorded'],
    ['Local crash ID', properties.crash_id ?? 'Not recorded'],
    ['Route identifier', textValue(properties.route_number) || 'Not recorded'],
    ['Location method', estimated ? 'Route and milepost estimate' : reported ? 'Reported coordinates' : 'Unavailable'],
  ];

  return (
      <section
        aria-label="Crash incident details"
        tabIndex={0}
        style={{ width: bounds.width, maxHeight: bounds.maxHeight }}
        className="overflow-y-auto overscroll-contain p-4 text-sm text-gray-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
      >
        <h3 className="m-0 pr-5 text-xs font-semibold uppercase tracking-wide text-gray-600">{severityLabel}</h3>
        <p className="!my-2 font-medium">
          <span>{date === 'Not recorded' ? 'Date not recorded' : date}</span>
          {' · '}
          <span>{time === 'Not recorded' ? 'Time not recorded' : time}</span>
        </p>
        <p className="!my-3 leading-relaxed" style={{ overflowWrap: 'anywhere' }}>{crashSummary(properties)}</p>
        {properties.severity === 'possible_injury' && (
          <p className="mt-2 text-xs text-gray-600">
            Possible injury is the recorded crash category (KABCO C). It does not confirm a minor or serious injury.
          </p>
        )}
        {properties.severity === 'injury_unknown' && (
          <p className="!my-2 text-xs text-gray-600">Injury severity was not specified in this record.</p>
        )}
        <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1.5 border-t border-gray-200 pt-3">
          {personRows.map(([label, count]) => (
            <React.Fragment key={label}>
              <dt className="text-gray-600">{label}</dt>
              <dd className="text-right font-medium tabular-nums">{countLabel(count)}</dd>
            </React.Fragment>
          ))}
        </dl>
        <p className="!my-2 text-xs leading-relaxed text-gray-500">
          Pedestrian counts are included in the all-person totals above; do not add them again.
        </p>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-2 border-t border-gray-200 pt-3">
          <dt className="text-gray-600">Bicycle involvement</dt>
          <dd className="text-right">{properties.bike_involved === true ? 'Yes' : properties.bike_involved === false ? 'No' : 'Unknown'}</dd>
          <dt className="text-gray-600">Lighting</dt>
          <dd className="text-right">{formatLighting(properties.light_condition)}</dd>
        </dl>
        {estimated && <p className="!mb-0 !mt-3 rounded bg-gray-100 px-2 py-1 text-xs text-gray-600">Estimated map position</p>}
        <button
          type="button"
          aria-expanded={sourceOpen}
          aria-controls={sourceId}
          onClick={() => setSourceOpen(!sourceOpen)}
          className="mt-2 flex min-h-[44px] w-full items-center justify-between border-t border-gray-200 text-left font-medium text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
        >
          Source and location <span aria-hidden="true">{sourceOpen ? '−' : '+'}</span>
        </button>
        <div id={sourceId}>
          {sourceOpen && <>
            <dl className="space-y-2 text-xs" style={{ overflowWrap: 'anywhere' }}>
              {sourceRows.map(([label, value]) => (
                <div key={label}>
                  <dt className="font-medium text-gray-600">{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
            {estimated && <p className="!mb-0 !mt-3 text-xs leading-relaxed text-gray-600">
              Position estimated from a route and milepost; it may not identify the exact crash site.
            </p>}
          </>}
        </div>
      </section>
  );
}

export default function CrashPopup({ properties, severityLabel }) {
  const popupRef = useRef(null);
  return (
    <Popup ref={popupRef} minWidth={140} maxWidth={320} autoPanPadding={[12, 12]} autoPanPaddingTopLeft={[12, 120]}>
      <CrashDetails properties={properties} severityLabel={severityLabel} popupRef={popupRef} />
    </Popup>
  );
}
