#!/usr/bin/env python3
"""Validate and atomically import the public NJDOT dashboard CSV through 2025.

Default execution is a read-only database reconciliation. A disk-backed stage
validates the entire source before --apply can modify any crash. Historical
records enrich exact, date-checked Accidents identities; unmatched 2019–2022
records remain in the audit for reconciliation instead of creating duplicates.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import sys
import tempfile
import uuid
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text
from app.models.database import engine
from scripts.ingest_njdot_crashes import (
    NJ_BOUNDS, _atomic_json, build_external_id, normalize_county_name,
    normalize_municipality_name,
)

logger = logging.getLogger(__name__)
PARSER_VERSION = 2
REQUIRED_COLUMNS = (
    'id_cr', 'casenumber', 'County', 'Municipality', 'dateofcrash', 'Year',
    'Highest Injury Severity Rating', 'NJDOT Summary', 'Geopoint',
    'Geopoint (Calculated)', 'fatalitycount', 'injurycount',
    'pedestrianfatalitycount', 'pedestrianinjurycount', 'crashfatalitycount',
)
# Public incident attributes only. Never copy the whole input row; unrecognized
# source columns, narratives, driver identifiers, and third-party IDs stay out.
PUBLIC_FIELDS = (
    *REQUIRED_COLUMNS, 'Date & Time of Crash', 'Weather Condition',
    'Alcohol Involved', 'At Intersection', 'Crash Type',
    'Damage to Property (Yes/No)', 'Day of Week', 'Direction From Intersection',
    'distancefromintersection', 'njdot_dln', 'fatalcrashind',
    'First Harmful Event', 'Hazmat Involved', 'intersectroutenumber',
    'intersectstreetsri', 'intersectstreetname', 'intersectionspeedlimit',
    'Is Private Property', 'latitude', 'longitude', 'milepost', 'Ramp',
    'Ramp Direction', 'ramproutenumber', 'rampstreetsri',
    'Road Character - Grade', 'Road Horizontal Alignment', 'Road Median',
    'Road Surface Type', 'Road System', 'Roadway Direction', 'routenumber',
    'routesuffix', 'Run Off Road', 'speedlimit', 'streetsri', 'streetname',
    'Surface Condition', 'vehiclecount', 'Traffic Control Zone',
)
RATINGS = {'K': 'fatal', 'A': 'serious_injury', 'B': 'minor_injury',
           'C': 'possible_injury', 'O': 'property_damage'}


def municipality_lookup(rows):
    lookup = defaultdict(list)
    for row in rows:
        county = normalize_county_name(row['county'])
        code = str(row['muni_code'] or '').strip()
        if not code.isdigit() or len(code) > 4:
            continue
        key = (county, normalize_municipality_name(row['name']))
        lookup[key].append({'muni_id': int(row['muni_id']), 'muni_code': code.zfill(4)})
    return dict(lookup)


def _count(row, name):
    value = (row.get(name) or '').strip()
    if not value:
        return None
    if not re.fullmatch(r'\d+', value) or int(value) > 2147483647:
        raise ValueError('invalid_' + name)
    return int(value)


def _point(value):
    if not value:
        return None
    try:
        obj = json.loads(value)
        lon, lat = float(obj['lon']), float(obj['lat'])
        west, south, east, north = NJ_BOUNDS
        if all(map(math.isfinite, (lon, lat))) and west <= lon <= east and south <= lat <= north:
            return [lon, lat]
    except (ValueError, TypeError, KeyError):
        pass
    return None


def prepare_record(row, lookup):
    dashboard_id = (row.get('id_cr') or '').strip()
    if not dashboard_id.isdigit() or len(dashboard_id) > 64:
        raise ValueError('invalid_dashboard_id')
    county = normalize_county_name(row.get('County', ''))
    label = (row.get('Municipality') or '').strip()
    if ',' in label:
        label, label_county = label.rsplit(',', 1)
        if normalize_county_name(label_county) != county:
            raise ValueError('municipality_county_conflict')
    matches = lookup.get((county, normalize_municipality_name(label)), [])
    if len(matches) != 1:
        raise ValueError('municipality_ambiguous' if matches else 'municipality_unmatched')
    municipality = matches[0]
    try:
        crash_date = date.fromisoformat(row.get('dateofcrash', ''))
    except ValueError as exc:
        raise ValueError('invalid_date') from exc
    if str(crash_date.year) != row.get('Year', '').strip():
        raise ValueError('year_date_conflict')
    if not 2019 <= crash_date.year <= 2025:
        raise ValueError('outside_2019_2025')
    case = (row.get('casenumber') or '').strip()
    if not case or len(case) > 200:
        raise ValueError('invalid_case_number')
    timestamp = (row.get('Date & Time of Crash') or '').strip()
    crash_time = None
    if timestamp:
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError as exc:
            raise ValueError('invalid_datetime') from exc
        if parsed.date() != crash_date:
            raise ValueError('datetime_date_conflict')
        crash_time = parsed.strftime('%H:%M')
    counts = {name: _count(row, name) for name in (
        'fatalitycount', 'injurycount', 'pedestrianfatalitycount',
        'pedestrianinjurycount', 'crashfatalitycount',
    )}
    try:
        summary_text = (row.get('NJDOT Summary') or '').strip()
        summary = json.loads(summary_text) if summary_text.startswith('[') else ([summary_text] if summary_text else [])
    except ValueError as exc:
        raise ValueError('invalid_summary') from exc
    if not isinstance(summary, list) or not all(isinstance(x, str) for x in summary):
        raise ValueError('invalid_summary')
    rating = (row.get('Highest Injury Severity Rating') or '').strip()
    match = re.search(r'\(([KABCO])\)$', rating)
    code = match.group(1) if match else rating if rating in RATINGS else None
    # The dashboard distinguishes reported fatalities from crash-related
    # fatalities; the former alone must not assert a fatal crash classification.
    fatal_evidence = (counts['crashfatalitycount'] or 0) > 0 or row.get('fatalcrashind', '').upper() == 'Y'
    injury_evidence = any((counts[x] or 0) > 0 for x in (
        'injurycount', 'pedestrianinjurycount',
    )) or 'Injury Crashes' in summary
    conflicts = []
    if fatal_evidence:
        severity = 'fatal'
        if code != 'K':
            conflicts.append('rating_with_fatal_evidence')
    elif code == 'O' and injury_evidence:
        severity = 'injury_unknown'
        conflicts.append('rating_o_with_injury_evidence')
    elif code == 'O' and any((counts[x] or 0) > 0 for x in ('fatalitycount', 'pedestrianfatalitycount')):
        severity = 'injury_unknown'
        conflicts.append('rating_o_with_unqualified_fatality_count')
    elif code:
        severity = RATINGS[code]
    elif injury_evidence:
        severity = 'injury_unknown'
        conflicts.append('missing_rating_with_injury_evidence')
    else:
        raise ValueError('unrecognized_severity')
    for pedestrian, total in (('pedestrianfatalitycount', 'fatalitycount'),
                               ('pedestrianinjurycount', 'injurycount')):
        if counts[pedestrian] is not None and counts[total] is not None and counts[pedestrian] > counts[total]:
            conflicts.append(pedestrian + '_exceeds_total')
    if code == 'K' and counts['fatalitycount'] == 0:
        conflicts.append('fatal_rating_with_zero_fatalities')
    if code in ('A', 'B', 'C') and counts['injurycount'] == 0:
        conflicts.append('injury_rating_with_zero_injured')
    reported = _point(row.get('Geopoint'))
    if reported is None and not row.get('Geopoint'):
        reported = _point(json.dumps({'lon': row.get('longitude'), 'lat': row.get('latitude')}))
    calculated = _point(row.get('Geopoint (Calculated)'))
    bike_flags = {'Bicyclist Involved', 'Bicycle Involved', 'Pedalcyclist Involved'}
    bike = True if bike_flags.intersection(summary) else None
    return {
        'dashboard_id': dashboard_id,
        'external_id': build_external_id(county, crash_date.year, f"{crash_date.year}{municipality['muni_code']}{case}"),
        'crash_date': crash_date.isoformat(), 'crash_time': crash_time,
        'year': crash_date.year, 'muni_id': municipality['muni_id'],
        'severity': severity, 'ped_involved': (
            'Pedestrian Involved' in summary or row.get('Crash Type') == 'Pedestrian'
            or any((counts[x] or 0) > 0 for x in ('pedestrianinjurycount', 'pedestrianfatalitycount'))
        ), 'bike_involved': bike,
        'total_killed': counts['fatalitycount'], 'total_injured': counts['injurycount'],
        'pedestrians_killed': counts['pedestrianfatalitycount'],
        'pedestrians_injured': counts['pedestrianinjurycount'],
        'road_name': (row.get('streetname') or '').strip()[:200] or None,
        'route_number': (row.get('streetsri') or '').strip()[:20] or None,
        'weather_condition': (row.get('Weather Condition') or '').strip()[:50] or None,
        'reported_point': reported, 'calculated_point': calculated,
        'source_metadata': {'source': 'njdot_dashboard', 'schema_version': 1,
                            'raw': {key: row[key] for key in PUBLIC_FIELDS if key in row},
                            'conflicts': conflicts},
    }


def stage_csv(csv_path, stage_path, lookup, *, source_url=None, retrieved_at=None):
    """Stream source into a new SQLite stage; malformed structure aborts all work."""
    csv_path, stage_path = Path(csv_path), Path(stage_path)
    if stage_path.exists():
        raise ValueError('stage_already_exists')
    stage_path.parent.mkdir(parents=True, exist_ok=True)
    before = csv_path.stat()
    with csv_path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    report = {'status': 'validating', 'parser_version': PARSER_VERSION, 'input_rows': 0, 'eligible_rows': 0,
              'quarantined_rows': 0, 'duplicates': 0, 'sha256': digest,
              'size_bytes': before.st_size, 'source_url': source_url,
              'retrieved_at': retrieved_at, 'years': {}, 'rejections': {}}
    years, reasons = defaultdict(Counter), Counter()
    with closing(sqlite3.connect(stage_path)) as stage, stage:
        stage.execute('PRAGMA cache_size=-32768')
        stage.execute('''CREATE TABLE records (
            dashboard_id TEXT PRIMARY KEY, external_id TEXT NOT NULL,
            year INTEGER NOT NULL, payload TEXT NOT NULL, issue TEXT,
            occurrences INTEGER NOT NULL DEFAULT 1)''')
        stage.execute('CREATE INDEX records_external_id ON records(external_id)')
        stage.execute('CREATE TABLE audit (line INTEGER, dashboard_id TEXT, year TEXT, reason TEXT)')
        with csv_path.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream, strict=True)
            fields = reader.fieldnames or []
            if len(fields) != len(set(fields)) or not set(REQUIRED_COLUMNS).issubset(fields):
                raise ValueError('missing_or_duplicate_columns')
            for line, row in enumerate(reader, 2):
                report['input_rows'] += 1
                if None in row or None in row.values():
                    raise ValueError(f'columns_mismatch_at_line_{line}')
                year = row.get('Year', '')
                years[year]['input'] += 1
                try:
                    record = prepare_record(row, lookup)
                except ValueError as exc:
                    reason = str(exc)
                    reasons[reason] += 1
                    years[year]['quarantined'] += 1
                    stage.execute('INSERT INTO audit VALUES (?,?,?,?)',
                                  (line, row.get('id_cr'), year, reason))
                    continue
                record['source_metadata']['provenance'] = {
                    'source_url': source_url, 'retrieved_at': retrieved_at,
                    'file_sha256': digest,
                }
                payload = json.dumps(record, sort_keys=True, separators=(',', ':'))
                previous = stage.execute('SELECT payload, issue FROM records WHERE dashboard_id=?',
                                         (record['dashboard_id'],)).fetchone()
                if previous:
                    issue = previous[1]
                    if previous[0] != payload:
                        issue = 'conflicting_dashboard_id'
                    else:
                        report['duplicates'] += 1
                    stage.execute('UPDATE records SET occurrences=occurrences+1, issue=? WHERE dashboard_id=?',
                                  (issue, record['dashboard_id']))
                else:
                    stage.execute('INSERT INTO records(dashboard_id,external_id,year,payload) VALUES (?,?,?,?)',
                                  (record['dashboard_id'], record['external_id'], record['year'], payload))
                if report['input_rows'] % 10000 == 0:
                    stage.commit()
                    logger.info('Validated %s CSV records', report['input_rows'])
        stage.execute('''UPDATE records SET issue='ambiguous_legacy_identity'
            WHERE issue IS NULL AND external_id IN (
                SELECT external_id FROM records GROUP BY external_id HAVING count(*) > 1)''')
        report['duplicates'] = 0
        for year, issue, count, occurrences in stage.execute(
            'SELECT year,issue,count(*),sum(occurrences) FROM records GROUP BY year,issue'
        ):
            if issue:
                reasons[issue] += occurrences
                years[str(year)]['quarantined'] += occurrences
            else:
                report['eligible_rows'] += count
                report['duplicates'] += occurrences - count
                years[str(year)]['eligible'] += count
        after = csv_path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('source_changed_during_validation')
        if not report['input_rows']:
            raise ValueError('empty_csv')
        report.update(status='validated', years=dict(years), rejections=dict(reasons),
                      quarantined_rows=sum(reasons.values()))
        stage.execute('CREATE TABLE manifest (payload TEXT NOT NULL)')
        stage.execute('INSERT INTO manifest VALUES (?)', (json.dumps(report),))
    return report


VALUE_COLUMNS = (
    'dashboard_id', 'external_id', 'crash_date', 'crash_time', 'severity',
    'ped_involved', 'bike_involved', 'total_killed', 'total_injured',
    'pedestrians_killed', 'pedestrians_injured', 'muni_id', 'road_name',
    'route_number', 'weather_condition', 'light_condition', 'source_metadata',
    'geocode_quality', 'segment_id', 'snap_distance', 'longitude', 'latitude',
)


def reconcile_record(record, match, *, allow_new_historical=False):
    """Choose a conservative mutation from a set-based identity/spatial lookup."""
    existing = match.get('existing')
    if match.get('identity_conflict'):
        return 'dashboard_and_legacy_identity_conflict', None
    if existing:
        if str(existing['crash_date']) != record['crash_date'] or existing['muni_id'] != record['muni_id']:
            return 'existing_date_or_municipality_conflict', None
        if existing.get('dashboard_id') not in (None, record['dashboard_id']):
            return 'existing_dashboard_identity_conflict', None
    elif record['year'] <= 2022 and not allow_new_historical:
        return 'historical_identity_unmatched', None
    elif match.get('unknown_source_overlap'):
        return 'unknown_source_overlap', None

    metadata = dict(record['source_metadata'], conflicts=list(record['source_metadata']['conflicts']))
    if match.get('reported_valid'):
        point, method = record['reported_point'], 'dashboard_current'
    elif match.get('calculated_valid'):
        point, method = record['calculated_point'], 'dashboard_calculated'
    elif existing and match.get('existing_valid'):
        point = [existing['longitude'], existing['latitude']]
        method = existing.get('geocode_quality') or 'historical_validated'
    else:
        return 'location_unresolved', None
    metadata['location_method'] = method
    values = {name: record.get(name) for name in VALUE_COLUMNS}
    values.update(crash_id=existing['crash_id'] if existing else None,
                  longitude=point[0], latitude=point[1], geocode_quality=method,
                  source_metadata=metadata)
    if existing:
        values['external_id'] = existing['external_id']
        # An absent or uncertain dashboard flag cannot erase positive companion
        # table evidence. Unknown source counts remain NULL as reported.
        prior_bike = existing.get('bike_involved')
        if prior_bike is not None and values['bike_involved'] is not None and prior_bike != values['bike_involved']:
            metadata['conflicts'].append('bicycle_flag_conflicts_with_existing_evidence')
        if prior_bike is True or values['bike_involved'] is None:
            values['bike_involved'] = prior_bike
        if existing.get('ped_involved') is True:
            values['ped_involved'] = True
        if record.get('light_condition') is None:
            values['light_condition'] = existing.get('light_condition')
        geometry_changed = (existing.get('longitude'), existing.get('latitude')) != tuple(point)
        if not geometry_changed:
            values['segment_id'] = existing.get('segment_id')
            values['snap_distance'] = existing.get('snap_distance')
        previous_metadata = existing.get('source_metadata') or {}
        if {k: v for k, v in previous_metadata.items() if k != 'provenance'} == {
            k: v for k, v in metadata.items() if k != 'provenance'
        }:
            values['source_metadata'] = previous_metadata
        comparable = dict(existing, crash_date=str(existing['crash_date']))
        if all(comparable.get(name) == values[name] for name in VALUE_COLUMNS):
            return 'unchanged', None
    return ('updated' if existing else 'inserted'), values


MATCH_SQL = text('''
    WITH input AS (
        SELECT * FROM jsonb_to_recordset(CAST(:records AS jsonb)) AS item(
            dashboard_id text, external_id text, crash_date date, muni_id integer,
            reported_point jsonb, calculated_point jsonb)
    ), points AS (
        SELECT input.*,
            ST_SetSRID(ST_Point((reported_point->>0)::float8, (reported_point->>1)::float8),4326) AS reported,
            ST_SetSRID(ST_Point((calculated_point->>0)::float8, (calculated_point->>1)::float8),4326) AS calculated
        FROM input
    )
    SELECT item.dashboard_id,
        d.crash_id IS NOT NULL AND h.crash_id IS NOT NULL AND d.crash_id <> h.crash_id AS identity_conflict,
        ST_Covers(m.geom,item.reported) AS reported_valid,
        ST_Covers(m.geom,item.calculated) AS calculated_valid,
        ST_Covers(m.geom,e.geom) AND ST_X(e.geom) BETWEEN -75.6 AND -73.9
            AND ST_Y(e.geom) BETWEEN 38.9 AND 41.4 AS existing_valid,
        CASE WHEN e.crash_id IS NULL THEN NULL ELSE (to_jsonb(e)-'geom') ||
            jsonb_build_object('longitude',ST_X(e.geom),'latitude',ST_Y(e.geom)) END AS existing,
        CASE WHEN e.crash_id IS NOT NULL THEN false ELSE EXISTS (
            SELECT 1 FROM crashes AS u
            WHERE u.muni_id=item.muni_id AND u.crash_date=item.crash_date
              AND u.dashboard_id IS NULL
              AND (u.external_id IS NULL OR u.external_id NOT LIKE 'NJDOT:%')
        ) END AS unknown_source_overlap
    FROM points item
    JOIN municipalities m ON m.muni_id=item.muni_id
    LEFT JOIN crashes d ON d.dashboard_id=item.dashboard_id
    LEFT JOIN crashes h ON h.external_id=item.external_id
    LEFT JOIN crashes e ON e.crash_id=COALESCE(d.crash_id,h.crash_id)
''')

_RECORD_TYPES = {
    'crash_id': 'integer', 'crash_date': 'date', 'ped_involved': 'boolean',
    'bike_involved': 'boolean', 'total_killed': 'integer', 'total_injured': 'integer',
    'pedestrians_killed': 'integer', 'pedestrians_injured': 'integer',
    'muni_id': 'integer', 'source_metadata': 'jsonb', 'segment_id': 'integer',
    'snap_distance': 'float8', 'longitude': 'float8', 'latitude': 'float8',
}
_INPUT_COLUMNS = ('crash_id', *VALUE_COLUMNS)
_INPUT_DECLARATION = ','.join(f'{name} {_RECORD_TYPES.get(name, "text")}' for name in _INPUT_COLUMNS)
_INPUT_CTE = f'''WITH input AS (
    SELECT * FROM jsonb_to_recordset(CAST(:records AS jsonb)) AS item({_INPUT_DECLARATION})
)'''
_STORED_COLUMNS = tuple(name for name in VALUE_COLUMNS if name not in ('longitude', 'latitude'))
_GEOMETRY_EXPRESSION = 'ST_SetSRID(ST_Point(input.longitude,input.latitude),4326)'
INSERT_SQL = text(_INPUT_CTE + f'''
    INSERT INTO crashes ({','.join(_STORED_COLUMNS)},geom)
    SELECT {','.join('input.' + name for name in _STORED_COLUMNS)},{_GEOMETRY_EXPRESSION}
    FROM input RETURNING crash_id
''')
UPDATE_SQL = text(_INPUT_CTE + f'''
    UPDATE crashes AS crash SET
        {','.join(name + '=input.' + name for name in _STORED_COLUMNS)},
        geom={_GEOMETRY_EXPRESSION}
    FROM input WHERE crash.crash_id=input.crash_id RETURNING crash.crash_id
''')


def process_stage(connection, stage_path, *, apply=False, batch_size=1000,
                  allow_new_historical=False, report_path=None):
    """Set-based matching and mutations; caller owns the atomic transaction.

    No INSERT/UPDATE is issued for an unchanged batch, including empty batches,
    because the application's statement triggers intentionally bump revisions.
    """
    if not 1 <= batch_size <= 10000:
        raise ValueError('batch_size must be between 1 and 10000')
    run_id = uuid.uuid4().hex
    outcomes, conflicts, locations = Counter(), Counter(), Counter()
    years = defaultdict(Counter)
    with closing(sqlite3.connect(stage_path)) as stage, stage:
        manifest = json.loads(stage.execute('SELECT payload FROM manifest').fetchone()[0])
        stage.execute('''CREATE TABLE IF NOT EXISTS reconciliation (
            run_id TEXT, dashboard_id TEXT, year INTEGER, outcome TEXT)''')
        cursor = stage.execute('SELECT payload FROM records WHERE issue IS NULL ORDER BY dashboard_id')
        report = dict(manifest, status='applying' if apply else 'reconciling',
                      run_id=run_id, apply=apply, allow_new_historical=allow_new_historical,
                      stage_path=str(Path(stage_path).resolve()))
        while True:
            batch = [json.loads(row[0]) for row in cursor.fetchmany(batch_size)]
            if not batch:
                break
            for record in batch:
                if not 2019 <= record['year'] <= 2025 or date.fromisoformat(record['crash_date']).year != record['year']:
                    raise ValueError('out_of_scope_staged_record')
            matches = {row['dashboard_id']: row for row in connection.execute(
                MATCH_SQL, {'records': json.dumps(batch)}
            ).mappings()}
            inserts, updates, audit = [], [], []
            for record in batch:
                match = matches.get(record['dashboard_id'])
                if match is None:
                    raise RuntimeError('municipality_changed_after_validation')
                outcome, values = reconcile_record(record, match, allow_new_historical=allow_new_historical)
                year = str(record['year'])
                outcomes[outcome] += 1
                years[year][outcome] += 1
                if match.get('existing'):
                    years[year]['matched'] += 1
                if outcome == 'historical_identity_unmatched' and (match.get('reported_valid') or match.get('calculated_valid')):
                    years[year]['historical_unmatched_with_valid_location'] += 1
                conflicts.update(record['source_metadata']['conflicts'])
                if values:
                    locations[values['geocode_quality']] += 1
                    (inserts if outcome == 'inserted' else updates).append(values)
                audit.append((run_id, record['dashboard_id'], record['year'], outcome))
            if apply:
                for sql, values in ((UPDATE_SQL, updates), (INSERT_SQL, inserts)):
                    if values:
                        changed = connection.execute(sql, {'records': json.dumps(values)}).scalars().all()
                        if len(changed) != len(values):
                            raise RuntimeError('mutation_count_mismatch')
            stage.executemany('INSERT INTO reconciliation VALUES (?,?,?,?)', audit)
            stage.commit()
            report.update(outcomes=dict(outcomes), reconciliation_years=dict(years),
                          conflicts=dict(conflicts), location_methods=dict(locations))
            if report_path:
                _atomic_json(Path(report_path), report)
            logger.info('Reconciled %s records: %s', sum(outcomes.values()), dict(outcomes))
    # "applied" is set by the CLI only after the outer transaction commits.
    report['status'] = 'ready_to_commit' if apply else 'dry_run_complete'
    return report


def _lookup_digest(lookup):
    serializable = sorted((county, name, values) for (county, name), values in lookup.items())
    return hashlib.sha256(json.dumps(serializable, sort_keys=True).encode()).hexdigest()


def _database_counts(connection):
    years = {str(row.year): row.count for row in connection.execute(text('''
        SELECT EXTRACT(YEAR FROM crash_date)::integer AS year, COUNT(*) AS count
        FROM crashes GROUP BY 1 ORDER BY 1
    '''))}
    return {'total': sum(years.values()), 'years': years}


def _validated_stage(csv_path, stage_path, lookup, args):
    """Publish only complete validation caches; interrupted temporary files are never reused."""
    lookup_hash = _lookup_digest(lookup)
    previous = None
    if stage_path.exists():
        try:
            with closing(sqlite3.connect(stage_path)) as stage, stage:
                previous = json.loads(stage.execute('SELECT payload FROM manifest').fetchone()[0])
        except (sqlite3.Error, TypeError, ValueError):
            # A pre-atomic implementation may have left a partial cache. It is
            # retained under a unique backup name after a replacement validates.
            previous = None
        if previous and previous.get('parser_version') == PARSER_VERSION and previous.get('municipality_lookup_sha256') == lookup_hash:
            with csv_path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if previous['sha256'] == digest:
                return previous
    stage_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=stage_path.parent, prefix=stage_path.name + '.', suffix='.building', delete=False) as temporary:
        building = Path(temporary.name)
    building.unlink()  # stage_csv requires exclusive creation of a new path.
    try:
        report = stage_csv(csv_path, building, lookup, source_url=args.source_url, retrieved_at=args.retrieved_at)
        report['municipality_lookup_sha256'] = lookup_hash
        with closing(sqlite3.connect(building)) as stage, stage:
            stage.execute('UPDATE manifest SET payload=?', (json.dumps(report),))
        if stage_path.exists():
            backup = stage_path.with_name(stage_path.name + '.' + uuid.uuid4().hex[:12] + '.previous')
            os.replace(stage_path, backup)
        os.replace(building, stage_path)
        return report
    finally:
        building.unlink(missing_ok=True)


def run_import(args, *, database_engine=engine):
    """Validate first, then reconcile within one consistent transaction."""
    csv_path, report_path = Path(args.input_csv), Path(args.report)
    stage_path = Path(args.stage) if args.stage else report_path.with_suffix('.sqlite')
    report = {'status': 'validating', 'apply': args.apply, 'input_csv': str(csv_path.resolve())}
    committed = False
    _atomic_json(report_path, report)
    try:
        with database_engine.connect() as connection:
            lookup = municipality_lookup(connection.execute(
                text('SELECT muni_id,name,county,muni_code FROM municipalities ORDER BY muni_id')
            ).mappings())
        lookup_hash = _lookup_digest(lookup)
        report = _validated_stage(csv_path, stage_path, lookup, args)
        selected = sum(values.get('input', 0) for year, values in report['years'].items()
                       if year in {str(y) for y in range(2019, 2026)})
        if args.expected_count is not None and selected != args.expected_count:
            raise ValueError(f'expected_count_mismatch: expected {args.expected_count}, observed {selected}')
        if not report['eligible_rows']:
            raise ValueError('no_eligible_records_after_validation')
        _atomic_json(report_path, report)
        with database_engine.begin() as connection:
            if not args.apply:
                connection.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            connection.execute(text("SET LOCAL statement_timeout='120s'"))
            connection.execute(text("SET LOCAL lock_timeout='5s'"))
            if args.apply:
                # The worker can finish its active job; it is never stopped by
                # this importer. A busy worker causes a bounded timeout/retry.
                connection.execute(text("SET LOCAL lock_timeout='60s'"))
                connection.execute(text('SELECT pg_advisory_xact_lock(1212763714,1)'))
                connection.execute(text("SET LOCAL lock_timeout='5s'"))
                # Exclude concurrent legacy/bicycle writers for the entire atomic
                # import; ordinary API reads remain available.
                connection.execute(text('LOCK TABLE crashes IN SHARE ROW EXCLUSIVE MODE'))
                connection.execute(text('LOCK TABLE municipalities IN SHARE MODE'))
            current_lookup = municipality_lookup(connection.execute(
                text('SELECT muni_id,name,county,muni_code FROM municipalities ORDER BY muni_id')
            ).mappings())
            if _lookup_digest(current_lookup) != lookup_hash:
                raise RuntimeError('municipalities_changed_after_validation')
            before = _database_counts(connection)
            report = process_stage(connection, stage_path, apply=args.apply,
                                   batch_size=args.batch_size,
                                   allow_new_historical=args.allow_new_historical,
                                   report_path=report_path)
            after = _database_counts(connection)
            expected_total = before['total'] + (report.get('outcomes', {}).get('inserted', 0) if args.apply else 0)
            if after['total'] != expected_total:
                raise RuntimeError('database_count_mismatch')
            for year, count in before['years'].items():
                if year not in {str(y) for y in range(2019, 2026)} and after['years'].get(year) != count:
                    raise RuntimeError('out_of_scope_year_changed')
            report.update(database_before=before, database_after=after)
        committed = args.apply
        report['status'] = 'applied' if args.apply else 'dry_run_complete'
        report['completed_at'] = datetime.now(timezone.utc).isoformat()
        _atomic_json(report_path, report)
        return report
    except BaseException as exc:
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding='utf-8'))
        report.update(status='failed', error=str(exc), apply=args.apply, database_committed=committed)
        _atomic_json(report_path, report)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-csv', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--stage', type=Path, help='SQLite validation/audit file; reuses only a matching source and municipality lookup')
    parser.add_argument('--source-url', help='Official acquisition URL recorded as provenance')
    parser.add_argument('--retrieved-at', help='Acquisition timestamp from the download manifest')
    parser.add_argument('--expected-count', type=int, help='Expected number of source rows dated 2019–2025, before row-level quarantine')
    parser.add_argument('--batch-size', type=int, default=1000)
    parser.add_argument('--apply', action='store_true', help='Commit one atomic import after validation; default is dry-run')
    parser.add_argument('--allow-new-historical', action='store_true',
                        help='Also insert unmatched 2019–2022 canonical identities after duplicate and spatial checks')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    run_import(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
