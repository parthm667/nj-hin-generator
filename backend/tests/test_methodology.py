import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.services.hin_service import HINService


@pytest.fixture
def method_db():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    normalized_url = Settings(database_url=database_url, _env_file=None).database_url
    engine = create_engine(normalized_url)
    connection = engine.connect()
    transaction = connection.begin()
    database = Session(bind=connection)

    try:
        yield database, connection
    finally:
        database.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def insert_municipality(connection, muni_id):
    connection.execute(
        text(
            """
            INSERT INTO municipalities (muni_id, name, county, muni_code, geom)
            VALUES (
                :muni_id, :name, 'Mercer', :code,
                ST_GeomFromText(
                    'MULTIPOLYGON(((-75 39, -73 39, -73 41, -75 41, -75 39)))',
                    4326
                )
            )
            """
        ),
        {
            "muni_id": muni_id,
            "name": f"Method {abs(muni_id)}",
            "code": f"method-{abs(muni_id)}",
        },
    )


def insert_analysis(connection, analysis_id, muni_id):
    connection.execute(
        text(
            """
            INSERT INTO analyses
                (analysis_id, muni_id, created_at, start_year, end_year, status)
            VALUES (:analysis_id, :muni_id, CURRENT_TIMESTAMP, 2024, 2024, 'pending')
            """
        ),
        {"analysis_id": analysis_id, "muni_id": muni_id},
    )


def insert_road(
    connection,
    segment_id,
    muni_id,
    wkt,
    *,
    length_miles=1.0,
    road_class="local",
    sri=None,
    road_name="Test Road",
):
    connection.execute(
        text(
            """
            INSERT INTO road_segments
                (segment_id, sri, road_name, road_class, length_miles, muni_id, geom)
            VALUES
                (:segment_id, :sri, :road_name, :road_class, :length_miles,
                 :muni_id, ST_GeomFromText(:wkt, 4326))
            """
        ),
        {
            "segment_id": segment_id,
            "sri": sri,
            "road_name": road_name,
            "road_class": road_class,
            "length_miles": length_miles,
            "muni_id": muni_id,
            "wkt": wkt,
        },
    )


def segment_stats(
    segment_id,
    length_miles,
    total_crashes,
    severity_score,
    *,
    road_class="local",
):
    return {
        "segment_id": segment_id,
        "length_miles": length_miles,
        "road_class": road_class,
        "total_crashes": total_crashes,
        "fatal_crashes": 0,
        "serious_injury_crashes": 0,
        "minor_injury_crashes": 0,
        "property_damage_crashes": total_crashes,
        "ped_crashes": 0,
        "bike_crashes": 0,
        "severity_score": severity_score,
    }


def test_poisson_uses_actual_counts_and_leave_one_out_class_exposure(method_db):
    database, connection = method_db
    insert_municipality(connection, -96001)
    insert_analysis(connection, -96031, -96001)
    for segment_id, length in [(-96011, 0.1), (-96012, 0.1), (-96013, 10.0)]:
        insert_road(
            connection,
            segment_id,
            -96001,
            f"LINESTRING(-74.9 {40 + abs(segment_id) / 1000000}, "
            f"-74.8 {40 + abs(segment_id) / 1000000})",
            length_miles=length,
        )

    HINService(database).identify_significant_segments(
        -96031,
        [
            segment_stats(-96011, 0.1, 1, 10),
            segment_stats(-96012, 0.1, 3, 3),
            segment_stats(-96013, 10.0, 1, 1),
        ],
        2024,
        2024,
        0.05,
    )

    rows = connection.execute(
        text(
            """
            SELECT segment_id, crash_count_total, severity_score, crash_rate,
                   expected_crashes, p_value, is_significant
            FROM hin_segments WHERE analysis_id = -96031 ORDER BY segment_id
            """
        )
    ).all()

    by_segment = {row.segment_id: row for row in rows}
    one_fatal = by_segment[-96011]
    three_crashes = by_segment[-96012]
    assert one_fatal.crash_count_total == 1
    assert one_fatal.severity_score == 10
    assert one_fatal.crash_rate == pytest.approx(10.0)
    assert one_fatal.expected_crashes == pytest.approx((4 / 10.1) * 0.1)
    assert one_fatal.is_significant is False
    assert three_crashes.crash_rate == pytest.approx(30.0)
    assert three_crashes.expected_crashes == pytest.approx((2 / 10.1) * 0.1)
    assert three_crashes.p_value < 0.001
    assert three_crashes.is_significant is True


def test_baseline_falls_back_to_leave_one_out_municipality_without_invention(
    method_db,
):
    database, connection = method_db
    insert_municipality(connection, -96101)
    insert_analysis(connection, -96131, -96101)
    insert_road(
        connection,
        -96111,
        -96101,
        "LINESTRING(-74.9 40.1, -74.899 40.1)",
        length_miles=0.1,
        road_class="arterial",
    )
    insert_road(
        connection,
        -96112,
        -96101,
        "LINESTRING(-74.9 40.2, -74.8 40.2)",
        length_miles=10.0,
        road_class="local",
    )

    service = HINService(database)
    service.identify_significant_segments(
        -96131,
        [
            segment_stats(-96111, 0.1, 3, 3, road_class="arterial"),
            segment_stats(-96112, 10.0, 1, 1, road_class="local"),
        ],
        2024,
        2024,
        0.05,
    )

    target = connection.execute(
        text(
            """
            SELECT expected_crashes, p_value, is_significant
            FROM hin_segments WHERE analysis_id = -96131 AND segment_id = -96111
            """
        )
    ).one()
    assert target.expected_crashes == pytest.approx(0.01)
    assert target.p_value < 0.001
    assert target.is_significant is True

    insert_municipality(connection, -96102)
    insert_analysis(connection, -96132, -96102)
    insert_road(
        connection,
        -96121,
        -96102,
        "LINESTRING(-74.9 40.3, -74.899 40.3)",
        length_miles=0.1,
        road_class="arterial",
    )
    service.identify_significant_segments(
        -96132,
        [segment_stats(-96121, 0.1, 3, 3, road_class="arterial")],
        2024,
        2024,
        0.05,
    )
    no_reference = connection.execute(
        text(
            """
            SELECT expected_crashes, p_value, is_significant
            FROM hin_segments WHERE analysis_id = -96132
            """
        )
    ).one()
    assert no_reference.expected_crashes == 0
    assert no_reference.p_value == 1
    assert no_reference.is_significant is False


def test_short_fragments_are_ineligible_and_do_not_distort_baselines(method_db):
    database, connection = method_db
    insert_municipality(connection, -96201)
    insert_analysis(connection, -96231, -96201)
    for segment_id, length in [(-96211, 0.001), (-96212, 0.1), (-96213, 10.0)]:
        insert_road(
            connection,
            segment_id,
            -96201,
            f"LINESTRING(-74.9 {40 + abs(segment_id) / 1000000}, "
            f"-74.8 {40 + abs(segment_id) / 1000000})",
            length_miles=length,
        )

    HINService(database).identify_significant_segments(
        -96231,
        [
            segment_stats(-96211, 0.001, 100, 100),
            segment_stats(-96212, 0.1, 3, 3),
            segment_stats(-96213, 10.0, 1, 1),
        ],
        2024,
        2024,
        0.05,
    )

    rows = connection.execute(
        text(
            """
            SELECT segment_id, expected_crashes, p_value, is_significant
            FROM hin_segments
            WHERE analysis_id = -96231 AND segment_id IN (-96211, -96212)
            ORDER BY segment_id
            """
        )
    ).all()
    by_segment = {row.segment_id: row for row in rows}
    short = by_segment[-96211]
    ordinary = by_segment[-96212]
    assert short.expected_crashes == 0
    assert short.p_value == 1
    assert short.is_significant is False
    assert ordinary.expected_crashes == pytest.approx(0.01)
    assert ordinary.is_significant is True


def test_unknown_injury_is_weighted_descriptively_but_not_counted_as_minor(method_db):
    database, connection = method_db
    insert_municipality(connection, -96301)
    insert_road(
        connection,
        -96311,
        -96301,
        "LINESTRING(-74.9 40.1, -74.8 40.1)",
    )
    connection.execute(
        text(
            """
            INSERT INTO crashes
                (crash_id, crash_date, severity, ped_involved, bike_involved,
                 muni_id, segment_id, geom)
            VALUES
                (-96321, DATE '2024-01-01', 'injury_unknown', false, false,
                 -96301, -96311, ST_GeomFromText('POINT(-74.85 40.1)', 4326)),
                (-96322, DATE '2024-02-01', 'minor_injury', false, false,
                 -96301, -96311, ST_GeomFromText('POINT(-74.85 40.1)', 4326))
            """
        )
    )

    stats = HINService(database).calculate_all_segment_stats(-96301, 2024, 2024)

    assert stats[0]["total_crashes"] == 2
    assert stats[0]["minor_injury_crashes"] == 1
    assert stats[0]["severity_score"] == 6


def test_corridors_require_same_road_identity_and_metric_endpoint_connectivity(
    method_db,
):
    database, connection = method_db
    insert_municipality(connection, -96401)
    insert_analysis(connection, -96431, -96401)
    roads = [
        (-96411, "A", "Route A", "LINESTRING(-74.500 40, -74.499 40)"),
        # About 0.5 m from -96411's endpoint: inside the one-meter tolerance.
        (-96412, "A", "Route A", "LINESTRING(-74.498994 40, -74.498 40)"),
        (-96413, "A", "Route A", "LINESTRING(-74.400 40, -74.399 40)"),
        # Touches -96412 but has a different stable route identity.
        (-96414, "B", "Route B", "LINESTRING(-74.498 40, -74.497 40)"),
        # Same fallback name and no SRI: these two form another component.
        (-96415, None, " local ROAD ", "LINESTRING(-74.300 40, -74.299 40)"),
        (-96416, None, "LOCAL road", "LINESTRING(-74.299 40, -74.298 40)"),
    ]
    for segment_id, sri, name, wkt in roads:
        insert_road(
            connection,
            segment_id,
            -96401,
            wkt,
            sri=sri,
            road_name=name,
        )
        connection.execute(
            text(
                """
                INSERT INTO hin_segments
                    (segment_id, analysis_id, severity_score, crash_rate,
                     is_significant)
                VALUES (:segment_id, -96431, 3, 3, true)
                """
            ),
            {"segment_id": segment_id},
        )

    HINService(database).group_into_corridors(-96431)

    grouped = dict(
        connection.execute(
            text(
                """
                SELECT segment_id, corridor_id
                FROM hin_segments WHERE analysis_id = -96431
                """
            )
        ).all()
    )
    assert grouped[-96411] == grouped[-96412]
    assert grouped[-96413] != grouped[-96411]
    assert grouped[-96414] != grouped[-96412]
    assert grouped[-96415] == grouped[-96416]
    assert len(set(grouped.values())) == 4

    first_assignment = grouped
    HINService(database).group_into_corridors(-96431)
    rerun_assignment = dict(
        connection.execute(
            text(
                """
                SELECT segment_id, corridor_id
                FROM hin_segments WHERE analysis_id = -96431
                """
            )
        ).all()
    )
    assert rerun_assignment == first_assignment
