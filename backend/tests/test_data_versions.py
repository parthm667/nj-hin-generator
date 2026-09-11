"""Same-count edits and methodology changes must invalidate published results."""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.services.version_service import current_input_version


@pytest.fixture
def version_db():
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL is not configured')
    engine = create_engine(url)
    with engine.connect() as conn:
        transaction = conn.begin()
        db = Session(bind=conn)
        try:
            yield db
        finally:
            db.close()
            transaction.rollback()
    engine.dispose()


def test_same_count_boundary_edit_changes_version(version_db):
    db = version_db
    db.execute(text("""INSERT INTO municipalities (muni_id,name,county,geom)
        VALUES (-990001,'Version test','Mercer',
        ST_GeomFromText('MULTIPOLYGON(((-75 40,-74 40,-74 41,-75 41,-75 40)))',4326))"""))
    before = current_input_version(db)
    db.execute(text("UPDATE municipalities SET name='Changed name' WHERE muni_id=-990001"))
    assert current_input_version(db) != before


def test_snap_assignments_do_not_change_source_version(version_db):
    db = version_db
    before = current_input_version(db)
    db.execute(text('UPDATE crashes SET snap_distance=0 WHERE crash_id=-9999999'))
    assert current_input_version(db) == before


def test_method_change_invalidates_version(version_db, monkeypatch):
    import app.services.version_service as versions
    before = current_input_version(version_db)
    monkeypatch.setattr(versions, 'METHOD_VERSION', 'a-different-screening-method')
    assert current_input_version(version_db) != before


def test_same_count_crash_and_road_and_svi_edits_change_version(version_db):
    db = version_db
    for statement in (
        "UPDATE crashes SET severity=severity WHERE crash_id=-9999999",
        "UPDATE road_segments SET length_miles=length_miles WHERE segment_id=-9999999",
        "UPDATE census_tracts SET svi_percentile=svi_percentile WHERE tract_id='missing'",
    ):
        before = current_input_version(db)
        db.execute(text(statement))
        assert current_input_version(db) != before
