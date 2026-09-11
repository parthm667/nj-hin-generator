import os
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.services.crash_service import CrashService
from app.services.hin_service import HINService


def test_geojson_services_project_seeded_postgis_geometry():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    normalized_url = Settings(
        database_url=database_url,
        _env_file=None,
    ).database_url
    engine = create_engine(normalized_url)

    connection = engine.connect()
    transaction = connection.begin()
    database = Session(bind=connection)

    try:
        connection.execute(
            text(
                """
                INSERT INTO municipalities
                    (muni_id, name, county, muni_code, geom)
                VALUES
                    (:muni_id, 'GeoJSON Test', 'Mercer', :muni_code,
                     ST_GeomFromText(
                       'MULTIPOLYGON(((-75 40, -74 40, -74 41, -75 41, -75 40)))',
                       4326
                     ))
                """
            ),
            {"muni_id": -90101, "muni_code": "geojson-test"},
        )
        connection.execute(
            text(
                """
                INSERT INTO road_segments
                    (segment_id, road_name, road_class, length_miles, muni_id, geom)
                VALUES
                    (:segment_id, 'Brunswick Pike', 'arterial', 1.25, :muni_id,
                     ST_GeomFromText(
                       'LINESTRING(-74.7 40.3, -74.69 40.31)',
                       4326
                     ))
                """
            ),
            {"segment_id": -90102, "muni_id": -90101},
        )
        connection.execute(
            text(
                """
                INSERT INTO crashes
                    (crash_id, crash_date, severity, ped_involved, bike_involved,
                     muni_id, segment_id, road_name, geom)
                VALUES
                    (:crash_id, :crash_date, 'serious_injury', true, false,
                     :muni_id, :segment_id, 'Nassau Street',
                     ST_SetSRID(ST_MakePoint(-74.65, 40.35), 4326))
                """
            ),
            {
                "crash_id": -90103,
                "crash_date": date(2024, 5, 17),
                "muni_id": -90101,
                "segment_id": -90102,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO analyses
                    (analysis_id, muni_id, created_at, start_year, end_year, status)
                VALUES
                    (:analysis_id, :muni_id, CURRENT_TIMESTAMP,
                     2020, 2024, 'completed')
                """
            ),
            {"analysis_id": -90104, "muni_id": -90101},
        )
        connection.execute(
            text(
                """
                INSERT INTO hin_segments
                    (hin_id, segment_id, analysis_id, crash_count_total,
                     severity_score, crash_rate, is_significant, corridor_name,
                     in_vulnerable_tract)
                VALUES
                    (:hin_id, :segment_id, :analysis_id, 6,
                     19, 3.5, true, 'Route 1', true)
                """
            ),
            {
                "hin_id": -90105,
                "segment_id": -90102,
                "analysis_id": -90104,
            },
        )

        crashes = CrashService(database).get_crashes_geojson(muni_id=-90101)
        hin = HINService(database).get_hin_geojson(analysis_id=-90104)

        assert crashes["features"][0]["geometry"] == {
            "type": "Point",
            "coordinates": [-74.65, 40.35],
        }
        assert crashes["features"][0]["properties"]["road_name"] == (
            "Nassau Street"
        )
        assert hin["features"][0]["geometry"] == {
            "type": "LineString",
            "coordinates": [[-74.7, 40.3], [-74.69, 40.31]],
        }
        assert hin["features"][0]["properties"]["road_name"] == (
            "Brunswick Pike"
        )
    finally:
        database.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
