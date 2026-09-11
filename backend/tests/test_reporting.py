from types import SimpleNamespace

from sqlalchemy import text

from app.models.tables import Analysis
from app.services.hin_service import HINService
from app.services.pdf_service import PDFReportGenerator
from test_metric_analysis import metric_db, insert_municipality


def test_csv_text_cannot_be_interpreted_as_a_formula():
    from app.routers.export import csv_cell
    assert csv_cell('=HYPERLINK("bad")').startswith("'")
    assert csv_cell('  +1+1').startswith("'")
    assert csv_cell(-74.5) == -74.5
    assert csv_cell(None) == ''


def test_summary_counts_people_not_crashes_and_preserves_unknown(metric_db):
    db, conn, _ = metric_db
    insert_municipality(conn, -995001)
    conn.execute(text("""INSERT INTO crashes
      (crash_id,muni_id,crash_date,severity,total_killed,total_injured,geom)
      VALUES (-995002,-995001,'2020-01-01','fatal',3,4,ST_SetSRID(ST_Point(-74.5,40.5),4326))"""))
    analysis = Analysis(muni_id=-995001,start_year=2020,end_year=2020,status='running')
    db.add(analysis)
    db.flush()
    HINService(db).update_analysis_summary(analysis)
    assert analysis.total_fatalities == 3
    assert analysis.total_injuries == 4
    conn.execute(text('UPDATE crashes SET total_injured=NULL WHERE crash_id=-995002'))
    HINService(db).update_analysis_summary(analysis)
    assert analysis.total_injuries is None


def test_methodology_reports_actual_snap_distance_and_exploratory_limit():
    report = PDFReportGenerator(None)
    elements = report._build_methodology(SimpleNamespace(snap_distance_meters=125, significance_threshold=0.05))
    content = ' '.join(getattr(item,'text','') for item in elements)
    assert '125' in content
    assert 'exploratory' in content.lower()
    assert '4x' not in content
    assert 'OpenStreetMap' not in content


def test_pdf_vru_table_uses_distinct_source_counts(metric_db):
    db, conn, _ = metric_db
    insert_municipality(conn, -995011)
    conn.execute(text("""INSERT INTO crashes
      (crash_id,muni_id,crash_date,severity,total_killed,total_injured,pedestrians_killed,pedestrians_injured,bike_involved,geom)
      VALUES (-995012,-995011,'2020-01-01','fatal',2,7,1,3,NULL,ST_SetSRID(ST_Point(-74.5,40.5),4326))"""))
    analysis = SimpleNamespace(muni_id=-995011,start_year=2020,end_year=2020)
    stats = PDFReportGenerator(db)._get_detailed_crash_statistics(analysis)
    assert stats['ped_killed'] == 1
    assert stats['ped_injured'] == 3
    assert stats['bike_killed'] == 'Unknown'
    assert stats['bike_injured'] == 'Unknown'
