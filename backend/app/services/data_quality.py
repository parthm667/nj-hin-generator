"""Describe loaded-record limitations without claiming statewide completeness."""
from sqlalchemy import text
from app.services.crash_service import CrashService
from app.services.version_service import METHOD_VERSION


def analysis_data_quality(db, analysis):
    stats = CrashService(db).get_municipality_crash_summary(
        analysis.muni_id, analysis.start_year, analysis.end_year)
    geocodes = dict(db.execute(text('''SELECT COALESCE(geocode_quality,'unknown'),COUNT(*)
        FROM crashes WHERE muni_id=:muni AND crash_date >= make_date(:start,1,1)
        AND crash_date < make_date(:end+1,1,1) GROUP BY geocode_quality'''),
        {'muni': analysis.muni_id, 'start': analysis.start_year, 'end': analysis.end_year}).all())
    svi = db.execute(text('''SELECT COUNT(*) FILTER (WHERE tract.svi_percentile IS NOT NULL),
        ARRAY_AGG(DISTINCT tract.source_year) FILTER (WHERE tract.source_year IS NOT NULL)
        FROM census_tracts tract JOIN municipalities muni ON ST_Intersects(tract.geom,muni.geom)
        WHERE muni.muni_id=:muni'''), {'muni': analysis.muni_id}).one()
    return {
        'method_version': METHOD_VERSION,
        'injury_detail_available': stats['injury_unknown_crashes'] == 0,
        'bicycle_data_available': stats['bike_involvement_unknown_crashes'] == 0,
        'casualty_counts_complete': stats['casualty_counts_complete'],
        'svi_available': svi[0] > 0,
        'svi_years': sorted(svi[1] or []),
        'geocode_counts': geocodes,
        'coverage_note': 'Loaded years are not a completeness guarantee. Records without usable locations are excluded. Route-milepost positions are estimates using the loaded road network. Unknown values are not zero.',
    }
