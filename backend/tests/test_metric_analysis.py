import math
import os
from contextlib import contextmanager
from datetime import date

import pytest
from psycopg2.errors import DivisionByZero
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.tables import Analysis
from app.services.crash_service import CrashService
from app.services.hin_service import HINService


@pytest.fixture
def metric_db():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    normalized_url = Settings(database_url=database_url, _env_file=None).database_url
    engine = create_engine(normalized_url)
    connection = engine.connect()
    transaction = connection.begin()
    database = Session(bind=connection)

    try:
        yield database, connection, engine
    finally:
        database.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


@contextmanager
def count_statements(connection):
    statements = []

    def record_statement(*args):
        statements.append(args[2])

    event.listen(connection, "before_cursor_execute", record_statement)
    try:
        yield statements
    finally:
        event.remove(connection, "before_cursor_execute", record_statement)


def insert_municipality(connection, muni_id, name="Metric Test"):
    connection.execute(
        text(
            """
            INSERT INTO municipalities (muni_id, name, county, muni_code, geom)
            VALUES (
                :muni_id, :name, 'Mercer', :muni_code,
                ST_GeomFromText(
                    'MULTIPOLYGON(((-75 40, -74 40, -74 41, -75 41, -75 40)))',
                    4326
                )
            )
            """
        ),
        {"muni_id": muni_id, "name": name, "muni_code": f"metric-{abs(muni_id)}"},
    )


def insert_road(
    connection,
    segment_id,
    muni_id,
    wkt,
    *,
    road_name="Test Road",
    road_class="local",
    length_miles=1.0,
):
    connection.execute(
        text(
            """
            INSERT INTO road_segments
                (segment_id, road_name, road_class, length_miles, muni_id, geom)
            VALUES
                (:segment_id, :road_name, :road_class, :length_miles, :muni_id,
                 ST_GeomFromText(:wkt, 4326))
            """
        ),
        {
            "segment_id": segment_id,
            "road_name": road_name,
            "road_class": road_class,
            "length_miles": length_miles,
            "muni_id": muni_id,
            "wkt": wkt,
        },
    )


def insert_crash(
    connection,
    crash_id,
    muni_id,
    wkt,
    *,
    crash_date=date(2024, 6, 1),
    severity="property_damage",
    segment_id=None,
    snap_distance=None,
    ped=False,
    bike=False,
):
    connection.execute(
        text(
            """
            INSERT INTO crashes
                (crash_id, crash_date, severity, ped_involved, bike_involved,
                 muni_id, segment_id, snap_distance, geom)
            VALUES
                (:crash_id, :crash_date, :severity, :ped, :bike,
                 :muni_id, :segment_id, :snap_distance,
                 ST_GeomFromText(:wkt, 4326))
            """
        ),
        {
            "crash_id": crash_id,
            "crash_date": crash_date,
            "severity": severity,
            "ped": ped,
            "bike": bike,
            "muni_id": muni_id,
            "segment_id": segment_id,
            "snap_distance": snap_distance,
            "wkt": wkt,
        },
    )


def insert_analysis(connection, analysis_id, muni_id, *, start_year=2024, end_year=2024):
    connection.execute(
        text(
            """
            INSERT INTO analyses
                (analysis_id, muni_id, created_at, start_year, end_year, status)
            VALUES
                (:analysis_id, :muni_id, CURRENT_TIMESTAMP,
                 :start_year, :end_year, 'pending')
            """
        ),
        {
            "analysis_id": analysis_id,
            "muni_id": muni_id,
            "start_year": start_year,
            "end_year": end_year,
        },
    )


def test_batch_snap_uses_metric_nearest_and_preserves_raw_crash_geometry(metric_db):
    database, connection, _ = metric_db
    insert_municipality(connection, -92001)
    # At this latitude, 0.0009 degrees longitude is closer in meters than
    # 0.0007 degrees latitude, even though geometry-distance ordering disagrees.
    insert_road(
        connection,
        -92011,
        -92001,
        "LINESTRING(-74.4991 40.2999, -74.4991 40.3001)",
        road_name="Metric Nearest",
    )
    insert_road(
        connection,
        -92012,
        -92001,
        "LINESTRING(-74.5001 40.3007, -74.4999 40.3007)",
        road_name="Degree Nearest",
    )
    insert_crash(connection, -92021, -92001, "POINT(-74.5 40.3)")

    degree_nearest = connection.execute(
        text(
            """
            SELECT segment_id
            FROM road_segments
            WHERE muni_id = -92001
            ORDER BY geom <-> ST_GeomFromText('POINT(-74.5 40.3)', 4326), segment_id
            LIMIT 1
            """
        )
    ).scalar_one()
    before = connection.execute(
        text("SELECT ST_AsEWKT(geom) FROM crashes WHERE crash_id = -92021")
    ).scalar_one()

    with count_statements(connection) as statements:
        snapped = CrashService(database).snap_crashes_to_segments(-92001, 100, True)

    assigned = connection.execute(
        text(
            """
            SELECT segment_id, snap_distance, ST_AsEWKT(geom)
            FROM crashes WHERE crash_id = -92021
            """
        )
    ).one()
    assert degree_nearest == -92012
    assert len(statements) == 1
    assert snapped == 1
    assert assigned.segment_id == -92011
    assert 70 < assigned.snap_distance < 85
    assert assigned.st_asewkt == before


def test_batch_snap_breaks_equal_metric_distance_ties_by_segment_id(metric_db):
    database, connection, _ = metric_db
    insert_municipality(connection, -92051)
    insert_road(
        connection,
        -92062,
        -92051,
        "LINESTRING(-74.5 40.3, -74.49 40.3)",
    )
    insert_road(
        connection,
        -92061,
        -92051,
        "LINESTRING(-74.5 40.3, -74.49 40.3)",
    )
    insert_crash(connection, -92071, -92051, "POINT(-74.5 40.3)")

    CrashService(database).snap_crashes_to_segments(-92051, 0, True)

    assigned = connection.execute(
        text("SELECT segment_id FROM crashes WHERE crash_id = -92071")
    ).scalar_one()
    assert assigned == -92062


def test_forced_snap_clears_assignment_outside_reduced_threshold(metric_db):
    database, connection, _ = metric_db
    insert_municipality(connection, -92101)
    insert_road(
        connection,
        -92111,
        -92101,
        "LINESTRING(-74.49965 40.2999, -74.49965 40.3001)",
    )
    insert_crash(
        connection,
        -92121,
        -92101,
        "POINT(-74.5 40.3)",
        segment_id=-92111,
        snap_distance=30,
    )

    snapped = CrashService(database).snap_crashes_to_segments(-92101, 10, True)

    assignment = connection.execute(
        text(
            "SELECT segment_id, snap_distance FROM crashes WHERE crash_id = -92121"
        )
    ).one()
    assert snapped == 0
    assert assignment.segment_id is None
    assert assignment.snap_distance is None


@pytest.mark.parametrize("radius", [-1, math.nan, math.inf, -math.inf])
def test_snap_rejects_nonnegative_nonfinite_radius_before_query(metric_db, radius):
    database, connection, _ = metric_db
    service = CrashService(database)

    with count_statements(connection) as statements:
        with pytest.raises(ValueError, match="finite nonnegative"):
            service.snap_crashes_to_segments(-92201, radius, True)

    assert statements == []


def test_batch_snap_never_crosses_municipality_boundaries(metric_db):
    database, connection, _ = metric_db
    insert_municipality(connection, -92301, "First")
    insert_municipality(connection, -92302, "Second")
    insert_road(
        connection,
        -92311,
        -92301,
        "LINESTRING(-74.4995 40.2999, -74.4995 40.3001)",
    )
    insert_road(
        connection,
        -92312,
        -92302,
        "LINESTRING(-74.50001 40.2999, -74.50001 40.3001)",
    )
    insert_crash(connection, -92321, -92301, "POINT(-74.5 40.3)")
    insert_crash(connection, -92322, -92302, "POINT(-74.50001 40.3)")

    CrashService(database).snap_crashes_to_segments(-92301, 100, True)

    assignments = dict(
        connection.execute(
            text(
                "SELECT crash_id, segment_id FROM crashes "
                "WHERE crash_id IN (-92321, -92322) ORDER BY crash_id"
            )
        ).all()
    )
    assert assignments[-92321] == -92311
    assert assignments[-92322] is None


def test_segment_stats_include_zero_crash_roads_and_apply_date_bounds_in_one_query(
    metric_db,
):
    database, connection, _ = metric_db
    insert_municipality(connection, -92401)
    insert_road(connection, -92411, -92401, "LINESTRING(-74.6 40.3, -74.59 40.3)")
    insert_road(connection, -92412, -92401, "LINESTRING(-74.6 40.4, -74.59 40.4)")
    insert_crash(
        connection,
        -92421,
        -92401,
        "POINT(-74.6 40.3)",
        segment_id=-92411,
        crash_date=date(2022, 1, 1),
        severity="fatal",
        ped=True,
    )
    insert_crash(
        connection,
        -92422,
        -92401,
        "POINT(-74.6 40.3)",
        segment_id=-92411,
        crash_date=date(2023, 12, 31),
        severity="minor_injury",
        bike=True,
    )
    insert_crash(
        connection,
        -92423,
        -92401,
        "POINT(-74.6 40.3)",
        segment_id=-92411,
        crash_date=date(2024, 1, 1),
        severity="fatal",
    )

    with count_statements(connection) as statements:
        stats = HINService(database).calculate_all_segment_stats(-92401, 2022, 2023)

    by_segment = {row["segment_id"]: row for row in stats}
    assert len(statements) == 1
    assert by_segment[-92411] == {
        "segment_id": -92411,
        "length_miles": 1.0,
        "road_class": "local",
        "total_crashes": 2,
        "fatal_crashes": 1,
        "serious_injury_crashes": 0,
        "minor_injury_crashes": 1,
        "property_damage_crashes": 0,
        "ped_crashes": 1,
        "bike_crashes": 1,
        "severity_score": 13,
    }
    assert by_segment[-92412]["total_crashes"] == 0
    assert by_segment[-92412]["severity_score"] == 0


def test_equity_overlay_uses_percentile_is_null_safe_and_selects_tract_deterministically(
    metric_db,
):
    database, connection, _ = metric_db
    insert_municipality(connection, -92501)
    insert_analysis(connection, -92531, -92501)
    road_specs = [
        (-92511, "LINESTRING(-74.90 40.10, -74.80 40.10)"),
        (-92512, "LINESTRING(-74.90 40.30, -74.80 40.30)"),
        (-92513, "LINESTRING(-74.90 40.50, -74.80 40.50)"),
        (-92514, "LINESTRING(-74.90 40.70, -74.80 40.70)"),
    ]
    for segment_id, wkt in road_specs:
        insert_road(connection, segment_id, -92501, wkt)
        connection.execute(
            text(
                """
                INSERT INTO hin_segments
                    (segment_id, analysis_id, severity_score, crash_rate,
                     is_significant, avg_svi_score, in_vulnerable_tract)
                VALUES (:segment_id, -92531, 1, 1, true, 12, true)
                """
            ),
            {"segment_id": segment_id},
        )

    tracts = [
        ("metric-a", 99, 50, "MULTIPOLYGON(((-74.95 40.05, -74.75 40.05, -74.75 40.15, -74.95 40.15, -74.95 40.05)))"),
        ("metric-b", 1, 99, "MULTIPOLYGON(((-74.95 40.05, -74.75 40.05, -74.75 40.15, -74.95 40.15, -74.95 40.05)))"),
        ("metric-c", None, 90, "MULTIPOLYGON(((-74.95 40.25, -74.75 40.25, -74.75 40.35, -74.95 40.35, -74.95 40.25)))"),
        ("metric-d", 88, None, "MULTIPOLYGON(((-74.95 40.45, -74.75 40.45, -74.75 40.55, -74.95 40.55, -74.95 40.45)))"),
    ]
    for tract_id, score, percentile, wkt in tracts:
        connection.execute(
            text(
                """
                INSERT INTO census_tracts
                    (tract_id, muni_id, svi_score, svi_percentile, geom)
                VALUES
                    (:tract_id, -92501, :score, :percentile,
                     ST_GeomFromText(:wkt, 4326))
                """
            ),
            {
                "tract_id": tract_id,
                "score": score,
                "percentile": percentile,
                "wkt": wkt,
            },
        )

    with count_statements(connection) as statements:
        HINService(database).add_equity_overlay(-92531)

    rows = connection.execute(
        text(
            """
            SELECT segment_id, avg_svi_score, in_vulnerable_tract
            FROM hin_segments
            WHERE analysis_id = -92531
            ORDER BY segment_id
            """
        )
    ).all()
    assert len(statements) <= 2
    assert rows == [
        (-92514, None, None),
        (-92513, 88, None),
        (-92512, None, True),
        (-92511, 99, False),
    ]


def test_corridor_grouping_and_analysis_summary_have_bounded_queries(metric_db):
    database, connection, _ = metric_db
    insert_municipality(connection, -92601)
    insert_analysis(connection, -92631, -92601)
    for segment_id, name, miles, wkt in [
        (-92611, " main STREET ", 1.25, "LINESTRING(-74.9 40.1, -74.85 40.1)"),
        (-92612, "MAIN street", 0.75, "LINESTRING(-74.85 40.1, -74.8 40.1)"),
        (-92613, None, 2.0, "LINESTRING(-74.9 40.2, -74.8 40.2)"),
    ]:
        insert_road(
            connection,
            segment_id,
            -92601,
            wkt,
            road_name=name,
            length_miles=miles,
        )
        connection.execute(
            text(
                """
                INSERT INTO hin_segments
                    (segment_id, analysis_id, severity_score, crash_rate,
                     is_significant)
                VALUES (:segment_id, -92631, 5, 5, true)
                """
            ),
            {"segment_id": segment_id},
        )
    insert_crash(
        connection,
        -92621,
        -92601,
        "POINT(-74.8 40.1)",
        crash_date=date(2024, 1, 1),
        severity="serious_injury",
    )

    service = HINService(database)
    with count_statements(connection) as corridor_statements:
        service.group_into_corridors(-92631)
    analysis = database.query(Analysis).filter(Analysis.analysis_id == -92631).one()
    with count_statements(connection) as summary_statements:
        service.update_analysis_summary(analysis)

    grouped = connection.execute(
        text(
            """
            SELECT segment_id, corridor_name, corridor_id
            FROM hin_segments WHERE analysis_id = -92631 ORDER BY segment_id
            """
        )
    ).all()
    assert len(corridor_statements) == 1
    assert len(summary_statements) <= 2
    assert grouped[0].corridor_name == "Unnamed"
    assert grouped[1].corridor_name == grouped[2].corridor_name == "Main Street"
    assert grouped[1].corridor_id == grouped[2].corridor_id
    assert analysis.total_crashes == 1
    assert analysis.total_injuries is None  # No person-count data in this fixture.
    assert analysis.hin_segment_count == 3
    assert analysis.hin_miles == pytest.approx(4.0)


def test_run_analysis_forces_threshold_refresh(metric_db):
    database, connection, _ = metric_db
    insert_municipality(connection, -92701)
    insert_road(
        connection,
        -92711,
        -92701,
        "LINESTRING(-74.49965 40.2999, -74.49965 40.3001)",
    )
    insert_crash(
        connection,
        -92721,
        -92701,
        "POINT(-74.5 40.3)",
        segment_id=-92711,
        snap_distance=30,
    )
    insert_analysis(connection, -92731, -92701)

    analysis = HINService(database).run_analysis(
        -92731, -92701, 2024, 2024, snap_distance_meters=10
    )

    assignment = connection.execute(
        text("SELECT segment_id FROM crashes WHERE crash_id = -92721")
    ).scalar_one()
    assert assignment is None
    assert analysis.status == "completed"


def test_session_advisory_lock_survives_commit_and_releases_on_error(metric_db):
    database, _, engine = metric_db
    service = HINService(database)

    with pytest.raises(RuntimeError, match="intentional"):
        with service._municipality_analysis_lock(-92801):
            database.commit()
            with engine.connect() as contender:
                acquired = contender.execute(
                    text(
                        "SELECT pg_try_advisory_lock(:namespace, :muni_id)"
                    ),
                    {
                        "namespace": service.ANALYSIS_LOCK_NAMESPACE,
                        "muni_id": -92801,
                    },
                ).scalar_one()
            assert acquired is False
            raise RuntimeError("intentional")

    with engine.connect() as contender:
        acquired = contender.execute(
            text("SELECT pg_try_advisory_lock(:namespace, :muni_id)"),
            {
                "namespace": service.ANALYSIS_LOCK_NAMESPACE,
                "muni_id": -92801,
            },
        ).scalar_one()
        assert acquired is True
        contender.execute(
            text("SELECT pg_advisory_unlock(:namespace, :muni_id)"),
            {
                "namespace": service.ANALYSIS_LOCK_NAMESPACE,
                "muni_id": -92801,
            },
        )


def test_failed_advisory_unlock_discards_locked_physical_connection(metric_db):
    database, control_connection, engine = metric_db
    service = HINService(database)
    observed = {}

    def fail_unlock(connection, cursor, statement, *args):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("select pg_advisory_lock("):
            observed["lock_backend_pid"] = (
                connection.connection.driver_connection.get_backend_pid()
            )
        elif normalized.startswith("select pg_advisory_unlock("):
            cursor.execute("SELECT 1 / 0")

    event.listen(engine, "before_cursor_execute", fail_unlock)
    try:
        with pytest.raises(DivisionByZero, match="division by zero"):
            with service._municipality_analysis_lock(-92851):
                pass
    finally:
        event.remove(engine, "before_cursor_execute", fail_unlock)

    lock_backend_pid = observed["lock_backend_pid"]
    remaining_locks = control_connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM pg_locks
            WHERE locktype = 'advisory' AND pid = :backend_pid
            """
        ),
        {"backend_pid": lock_backend_pid},
    ).scalar_one()

    with engine.connect() as recycled:
        recycled_pid = recycled.execute(text("SELECT pg_backend_pid() ")).scalar_one()
        with engine.connect() as contender:
            acquired = contender.execute(
                text("SELECT pg_try_advisory_lock(:namespace, :muni_id)"),
                {
                    "namespace": service.ANALYSIS_LOCK_NAMESPACE,
                    "muni_id": -92851,
                },
            ).scalar_one()
            if acquired:
                contender.execute(
                    text("SELECT pg_advisory_unlock(:namespace, :muni_id)"),
                    {
                        "namespace": service.ANALYSIS_LOCK_NAMESPACE,
                        "muni_id": -92851,
                    },
                )

    assert remaining_locks == 0
    assert recycled_pid != lock_backend_pid
    assert acquired is True


def test_failed_advisory_lock_acquisition_preserves_primary_error(metric_db):
    database, _, engine = metric_db
    service = HINService(database)

    def fail_acquisition(connection, cursor, statement, *args):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("select pg_advisory_lock("):
            cursor.execute("SELECT 1 / 0")

    event.listen(engine, "before_cursor_execute", fail_acquisition)
    try:
        with pytest.raises(DivisionByZero, match="division by zero"):
            with service._municipality_analysis_lock(-92861):
                pytest.fail("the lock body must not run")
    finally:
        event.remove(engine, "before_cursor_execute", fail_acquisition)
