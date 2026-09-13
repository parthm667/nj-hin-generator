"""Bounded public incident fields from validated dashboard metadata."""
from datetime import date

from sqlalchemy import text

DASHBOARD_NOTE = 'Dashboard records are provisional and subject to revision. Locations come from NJDOT; calculated positions are estimates.'
LOCATION_LABELS = {
    'dashboard_current': 'NJDOT processed current coordinates',
    'dashboard_calculated': 'NJDOT calculated coordinates (estimate)',
    'historical_validated': 'Previously validated historical position',
}

FIELDS = {
    'weather': 'Weather Condition',
    'crash_type': 'Crash Type',
    'first_harmful_event': 'First Harmful Event',
    'source_street_name': 'streetname',
    'intersection_name': 'intersectstreetname',
    'at_intersection': 'At Intersection',
    'speed_limit': 'speedlimit',
    'vehicle_count': 'vehiclecount',
    'surface_condition': 'Surface Condition',
    'document_locator': 'njdot_dln',
    'source_severity_rating': 'Highest Injury Severity Rating',
}
SEVERITY_CONFLICTS = frozenset((
    'rating_with_fatal_evidence', 'rating_o_with_injury_evidence',
    'rating_o_with_unqualified_fatality_count', 'fatal_rating_with_zero_fatalities',
    'injury_rating_with_zero_injured',
))
DASHBOARD_CSV_FIELDS = (
    'source_name', 'dashboard_id', *FIELDS, 'location_method',
    'source_url', 'source_retrieved_at', 'severity_conflict', 'source_conflict',
)


def _text(value, limit=200):
    if not isinstance(value, str):
        return None
    return value.strip()[:limit] or None


def dashboard_details(crash):
    metadata = getattr(crash, 'source_metadata', None)
    if not isinstance(metadata, dict) or metadata.get('source') != 'njdot_dashboard' or metadata.get('schema_version') != 1:
        return {}
    raw = metadata.get('raw')
    raw = raw if isinstance(raw, dict) else {}
    provenance = metadata.get('provenance')
    provenance = provenance if isinstance(provenance, dict) else {}
    conflicts = metadata.get('conflicts')
    conflicts = conflicts if isinstance(conflicts, list) else []
    # Unknown columns, free text narratives and the full file hash stay out.
    return {
        'source_name': 'NJDOT dashboard',
        'dashboard_id': _text(getattr(crash, 'dashboard_id', None), 64),
        **{field: _text(raw.get(header)) for field, header in FIELDS.items()},
        'location_method': _text(metadata.get('location_method'), 40),
        'source_url': _text(provenance.get('source_url'), 500),
        'source_retrieved_at': _text(provenance.get('retrieved_at'), 40),
        'severity_conflict': any(isinstance(item, str) and item in SEVERITY_CONFLICTS for item in conflicts),
        'source_conflict': bool(conflicts),
    }


def dashboard_source_coverage(db, analysis):
    """Count dashboard provenance only within the analysis municipality/period."""
    return dict(db.execute(text('''
        SELECT COUNT(*)::integer AS total_crashes,
            COUNT(*) FILTER (WHERE dashboard)::integer AS dashboard_crashes,
            COUNT(*) FILTER (WHERE dashboard AND
                source_metadata->'conflicts' ?| CAST(:severity_conflicts AS text[]))::integer
                AS dashboard_severity_conflict_crashes,
            COUNT(*) FILTER (WHERE dashboard AND
                CASE WHEN jsonb_typeof(source_metadata->'conflicts') = 'array'
                    THEN source_metadata->'conflicts' <> '[]'::jsonb
                    ELSE false END)::integer AS dashboard_conflict_crashes
        FROM (
            SELECT source_metadata,
                source_metadata->>'source' = 'njdot_dashboard'
                AND source_metadata->>'schema_version' = '1' AS dashboard
            FROM crashes WHERE muni_id=:muni_id
                AND crash_date >= :start_date AND crash_date < :end_date
        ) AS selected_crashes
    '''), {'muni_id': analysis.muni_id,
           'severity_conflicts': sorted(SEVERITY_CONFLICTS),
           'start_date': date(analysis.start_year, 1, 1),
           'end_date': date(analysis.end_year + 1, 1, 1)}).mappings().one())
