from app.config import Settings
from app.models.schemas import SeverityLevel
from app.services.pdf_service import PDFReportGenerator
from app.services.report_latex import render_report_tex
from test_report_latex import fixture
from test_metric_analysis import (
    metric_db, insert_analysis, insert_crash, insert_municipality, insert_road,
)


def test_possible_injury_is_a_distinct_category_with_descriptive_weight():
    assert SeverityLevel('possible_injury').value == 'possible_injury'
    weights = Settings(_env_file=None).severity_weights
    assert weights['possible_injury'] == weights['minor_injury'] == 3
    assert weights['possible_injury'] < weights['serious_injury']


def test_pdf_preserves_possible_injury_count_and_label(monkeypatch):
    from app.services.crash_service import CrashService
    summary = dict(total_crashes=4, fatal_crashes=0, serious_injury_crashes=0,
                   minor_injury_crashes=1, possible_injury_crashes=2,
                   injury_unknown_crashes=0, property_damage_crashes=1,
                   total_killed=0, total_injured=None,
                   pedestrians_killed=None, pedestrians_injured=None)
    monkeypatch.setattr(CrashService, 'get_municipality_crash_summary', lambda *args: summary)
    values = list(fixture())
    values[0].muni_id = 1
    values[2] = PDFReportGenerator(None)._get_detailed_crash_statistics(values[0])
    assert values[2]['possible_injury'] == 2
    assert values[2]['possible_injury_pct'] == 50
    assert values[2]['minor_injury'] == 1
    source = render_report_tex(*values)
    assert r'Possible injury & 2 & 50.0\%' in source
    assert 'KABCO C' in source


def test_possible_injury_remains_distinct_through_sql_hin_and_report(metric_db, monkeypatch):
    import asyncio
    import csv
    import io
    from sqlalchemy import text
    from app.models.tables import Analysis
    from app.services.crash_service import CrashService
    from app.services.hin_service import HINService
    from app.services.data_quality import analysis_data_quality
    from app.services.report_evidence import collect_report_evidence
    from app.services.coverage_service import CoverageService
    from app.routers.export import export_csv

    db, connection, _ = metric_db
    insert_municipality(connection, -97301)
    insert_road(connection, -97311, -97301, 'LINESTRING(-74.5 40.5,-74.4 40.5)')
    insert_analysis(connection, -97331, -97301)
    for index, severity in enumerate(('fatal', 'serious_injury', 'minor_injury', 'possible_injury', 'possible_injury', 'property_damage')):
        insert_crash(connection, -97321-index, -97301, 'POINT(-74.5 40.5)',
                     segment_id=-97311, severity=severity)
    service = CrashService(db)
    summary = service.get_municipality_crash_summary(-97301, 2024, 2024)
    assert summary['total_crashes'] == 6
    assert summary['possible_injury_crashes'] == 2
    assert summary['minor_injury_crashes'] == summary['property_damage_crashes'] == 1
    assert summary['fatal_crashes'] == summary['serious_injury_crashes'] == 1
    assert summary['injury_unknown_crashes'] == 0
    assert summary['total_injured'] is None  # Never derive people from severity.
    segment = service.calculate_segment_crash_stats(-97311, 2024, 2024)
    assert segment['possible_injury_crashes'] == 2
    assert segment['severity_score'] == 25
    hin = HINService(db)
    stats = hin.calculate_all_segment_stats(-97301, 2024, 2024)
    assert stats[0]['possible_injury_crashes'] == 2
    assert stats[0]['severity_score'] == 25
    hin.identify_significant_segments(-97331, stats, 2024, 2024, .05)
    row = connection.execute(text('SELECT crash_count_possible_injury, crash_count_minor_injury FROM hin_segments WHERE analysis_id=-97331')).one()
    assert tuple(row) == (2, 1)
    # Mark this stored result selected to exercise export and corridor aggregation.
    connection.execute(text('UPDATE hin_segments SET is_significant=true WHERE analysis_id=-97331'))
    assert hin.get_hin_geojson(-97331)['features'][0]['properties']['crash_count_possible_injury'] == 2
    analysis = db.get(Analysis, -97331)
    quality = analysis_data_quality(db, analysis)
    assert quality['injury_detail_available'] is True
    assert quality['casualty_counts_complete'] is False
    evidence = collect_report_evidence(db, analysis)
    assert evidence['annual'][0]['possible_injury_crashes'] == 2
    assert evidence['network']['selected_possible_injury_crashes'] == 2
    assert evidence['corridors'][0]['possible_injury_crashes'] == 2
    analysis.status = 'completed'
    # Freshness is covered separately; isolate these CSV serialization assertions.
    monkeypatch.setattr(CoverageService, 'require_current_results', lambda *args: None)

    async def csv_rows(kind):
        response = export_csv(-97331, data_type=kind, db=db)
        content = ''.join([chunk async for chunk in response.body_iterator])
        return list(csv.DictReader(io.StringIO(content)))

    crash_rows = asyncio.run(csv_rows('crashes'))
    assert sum(row['severity'] == 'possible_injury' for row in crash_rows) == 2
    assert sum(row['severity'] == 'minor_injury' for row in crash_rows) == 1
    hin_rows = asyncio.run(csv_rows('hin_segments'))
    assert hin_rows[0]['crash_count_possible_injury'] == '2'
