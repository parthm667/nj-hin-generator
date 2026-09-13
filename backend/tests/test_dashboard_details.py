from types import SimpleNamespace

from app.services import crash_service
from test_metric_analysis import metric_db, insert_crash, insert_municipality


def test_dashboard_details_expose_only_bounded_public_fields():
    crash = SimpleNamespace(dashboard_id='1001', source_metadata={
        'source': 'njdot_dashboard', 'schema_version': 1,
        'raw': {'Weather Condition': 'Rain', 'Crash Type': 'Rear End',
                'First Harmful Event': 'Other Motor Vehicle', 'At Intersection': 'Y',
                'intersectstreetname': 'MAIN ST', 'speedlimit': '25',
                'vehiclecount': '0', 'Surface Condition': 'Wet', 'njdot_dln': 'D123',
                'Narrative': 'SHOULD NEVER LEAK', 'streetname': 'x' * 2000},
        'conflicts': ['rating_o_with_injury_evidence'],
        'location_method': 'dashboard_calculated',
        'provenance': {'source_url': 'https://example.org/public.csv',
                       'retrieved_at': '2026-09-13T00:00:00Z', 'file_sha256': 'SECRET_HASH'},
    })
    details = crash_service.dashboard_details(crash)
    assert details['weather'] == 'Rain'
    assert details['crash_type'] == 'Rear End'
    assert details['vehicle_count'] == '0'
    assert details['document_locator'] == 'D123'
    assert details['dashboard_id'] == '1001'
    assert details['severity_conflict'] is True
    assert details['source_url'] == 'https://example.org/public.csv'
    assert 'SHOULD NEVER LEAK' not in str(details)
    assert 'SECRET_HASH' not in str(details)
    assert 'raw' not in details
    assert len(details['source_street_name']) <= 200


def test_unknown_or_malformed_metadata_does_not_invent_dashboard_provenance():
    for metadata in (None, [], {'source': 'other'}, {'source': 'njdot_dashboard', 'schema_version': 99}):
        assert crash_service.dashboard_details(SimpleNamespace(source_metadata=metadata)) == {}
    assert crash_service.dashboard_details(SimpleNamespace()) == {}


def test_dashboard_source_coverage_is_limited_to_selected_municipality_and_year(metric_db):
    import json
    from datetime import date
    from sqlalchemy import text
    from app.services.dashboard_details import dashboard_source_coverage
    db, conn, _ = metric_db
    insert_municipality(conn, -97401)
    insert_municipality(conn, -97402)
    for crash_id, muni, year, dashboard in (
        (-97411, -97401, 2024, True), (-97412, -97401, 2024, False),
        (-97413, -97401, 2023, True), (-97414, -97402, 2024, True),
    ):
        insert_crash(conn, crash_id, muni, 'POINT(-74.5 40.5)', crash_date=date(year, 1, 1))
        if dashboard:
            conn.execute(text('UPDATE crashes SET source_metadata=CAST(:value AS jsonb) WHERE crash_id=:id'),
                         {'id': crash_id, 'value': json.dumps({'source': 'njdot_dashboard', 'schema_version': 1,
                                                           'conflicts': ['rating_o_with_injury_evidence']})})
    coverage = dashboard_source_coverage(db, SimpleNamespace(muni_id=-97401, start_year=2024, end_year=2024))
    assert coverage == {'total_crashes': 2, 'dashboard_crashes': 1, 'dashboard_conflict_crashes': 1,
                        'dashboard_severity_conflict_crashes': 1}
    from app.services.data_quality import analysis_data_quality
    analysis = SimpleNamespace(muni_id=-97401, start_year=2024, end_year=2024)
    assert analysis_data_quality(db, analysis)['injury_detail_available'] is False
    conn.execute(text("UPDATE crashes SET source_metadata=jsonb_set(source_metadata, '{conflicts}', '[\"bicycle_flag_disagreement\"]'::jsonb) WHERE crash_id=-97411"))
    assert analysis_data_quality(db, analysis)['injury_detail_available'] is True


def test_pdf_discloses_scoped_dashboard_count_and_revision_limitations():
    from app.services.report_latex import render_report_tex
    from test_report_latex import fixture
    values = list(fixture())
    values[-1]['sources'] = {'total_crashes': 100, 'dashboard_crashes': 12, 'dashboard_conflict_crashes': 2}
    values[-1]['locations'] = [{'method': 'dashboard_calculated', 'count': 12}]
    source = render_report_tex(*values)
    assert '12 of 100 loaded crashes' in source
    assert 'subject to revision' in source
    assert '2 dashboard records' in source
    assert 'NJDOT calculated coordinates (estimate)' in source


def test_dashboard_geojson_and_csv_preserve_details_without_raw_metadata(monkeypatch):
    import asyncio
    import csv
    import io
    from datetime import date
    from app.routers.export import export_csv
    from app.services.coverage_service import CoverageService
    from test_geojson import FakeSession

    crash = SimpleNamespace(**dict.fromkeys(('crash_time', 'light_condition', 'external_id', 'route_number',
                            'ped_involved', 'bike_involved', 'total_killed', 'total_injured',
                            'pedestrians_killed', 'pedestrians_injured', 'road_name', 'geocode_quality')))
    crash.crash_id, crash.crash_date, crash.severity = 1, date(2025, 1, 1), 'possible_injury'
    crash.dashboard_id = '123'
    crash.source_metadata = {'source': 'njdot_dashboard', 'schema_version': 1,
                             'raw': {'Weather Condition': '=1+1', 'Crash Type': 'Rear End',
                                     'njdot_dln': 'D123', 'secret': 'NEVER EXPOSE'},
                             'location_method': 'dashboard_current'}
    collection = crash_service.CrashService(FakeSession([(crash, {'type': 'Point', 'coordinates': [-74.5, 40.5]})])).get_crashes_geojson(1, 2025, 2025)
    props = collection['features'][0]['properties']
    assert props['document_locator'] == 'D123'
    assert props['location_method'] == 'dashboard_current'
    assert 'source_metadata' not in props
    assert 'NEVER EXPOSE' not in str(collection)
    monkeypatch.setattr(crash_service.CrashService, 'get_crashes_geojson', lambda *args: collection)
    monkeypatch.setattr(CoverageService, 'require_current_results', lambda *args: None)
    analysis = SimpleNamespace(status='completed', muni_id=1, start_year=2025, end_year=2025)
    query = SimpleNamespace(first=lambda: analysis)
    query.filter = lambda *args: query
    db = SimpleNamespace(query=lambda *args: query)

    async def content():
        response = export_csv(1, db=db)
        return ''.join([chunk async for chunk in response.body_iterator])

    rows = list(csv.DictReader(io.StringIO(asyncio.run(content()))))
    assert rows[0]['weather'] == "'=1+1"
    assert rows[0]['dashboard_id'] == '123'
    assert rows[0]['crash_type'] == 'Rear End'
    assert rows[0]['severity'] == 'possible_injury'
    assert rows[0]['source_retrieved_at'] == ''
