import json
import os
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.services.crash_service import CrashService
from app.services.hin_service import HINService


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *criteria):
        return self

    def join(self, *targets):
        return self

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.projections = None

    def query(self, *projections):
        self.projections = projections
        return FakeQuery(self.rows)


def test_crash_geojson_uses_database_geometry_and_preserves_properties():
    crash = SimpleNamespace(
        crash_id=7,
        crash_date=date(2024, 5, 17),
        severity="serious_injury",
        ped_involved=True,
        bike_involved=None,
        total_killed=0,
        total_injured=2,
        pedestrians_killed=0,
        pedestrians_injured=1,
        road_name="Nassau Street",
        geocode_quality="route_milepost",
    )
    database = FakeSession(
        [(crash, json.dumps({"type": "Point", "coordinates": [-74.65, 40.35]}))]
    )

    result = CrashService(database).get_crashes_geojson(muni_id=11)

    assert result == {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-74.65, 40.35]},
                "properties": {
                    "crash_id": 7,
                    "date": "2024-05-17",
                    "severity": "serious_injury",
                    "ped_involved": True,
                    "bike_involved": None,
                    "total_killed": 0,
                    "total_injured": 2,
                    "pedestrians_killed": 0,
                    "pedestrians_injured": 1,
                    "road_name": "Nassau Street",
                    "geocode_quality": "route_milepost",
                },
            }
        ],
    }
    assert "ST_AsGeoJSON" in str(database.projections[-1])


def test_hin_geojson_uses_database_geometry_and_preserves_properties():
    hin_segment = SimpleNamespace(
        hin_id=9,
        segment_id=12,
        crash_count_total=6,
        crash_rate=3.5,
        severity_score=19,
        corridor_name="Route 1",
        in_vulnerable_tract=True,
    )
    road_segment = SimpleNamespace(road_name="Brunswick Pike")
    geometry = {
        "type": "LineString",
        "coordinates": [[-74.7, 40.3], [-74.69, 40.31]],
    }
    database = FakeSession([(hin_segment, road_segment, json.dumps(geometry))])

    result = HINService(database).get_hin_geojson(analysis_id=5)

    assert result == {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "hin_id": 9,
                    "segment_id": 12,
                    "road_name": "Brunswick Pike",
                    "crash_count": 6,
                    "crash_rate": 3.5,
                    "severity_score": 19,
                    "corridor_name": "Route 1",
                    "in_vulnerable_tract": True,
                },
            }
        ],
    }
    assert "ST_AsGeoJSON" in str(database.projections[-1])


def test_municipality_summary_discloses_unknown_injury_bike_and_casualty_data():
    database_url = os.getenv("DATA_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("DATA_TEST_DATABASE_URL is not configured")

    engine = create_engine(Settings(database_url=database_url, _env_file=None).database_url)
    connection = engine.connect()
    transaction = connection.begin()
    database = Session(bind=connection)
    try:
        connection.execute(
            text(
                """
                INSERT INTO municipalities (muni_id, name, county, muni_code, geom)
                VALUES (
                    -93801, 'Data Correctness Test', 'Mercer', 'data-correct-test',
                    ST_GeomFromText(
                        'MULTIPOLYGON(((-75 40,-74 40,-74 41,-75 41,-75 40)))',
                        4326
                    )
                );
                INSERT INTO crashes (
                    crash_id, external_id, crash_date, severity, ped_involved,
                    bike_involved, total_killed, total_injured,
                    pedestrians_killed, pedestrians_injured, muni_id, geom
                ) VALUES
                    (
                        -93811, 'data-correctness-known', '2020-01-02', 'fatal',
                        true, false, 1, 2, 1, 1, -93801,
                        ST_GeomFromText('POINT(-74.6 40.4)', 4326)
                    ),
                    (
                        -93812, 'data-correctness-unknown', '2020-02-03',
                        'injury_unknown', false, NULL, NULL, NULL, NULL, NULL,
                        -93801, ST_GeomFromText('POINT(-74.7 40.5)', 4326)
                    );
                """
            )
        )

        result = CrashService(database).get_municipality_crash_summary(
            -93801, 2020, 2020
        )

        assert result == {
            "total_crashes": 2,
            "fatal_crashes": 1,
            "serious_injury_crashes": 0,
            "minor_injury_crashes": 0,
            "injury_unknown_crashes": 1,
            "property_damage_crashes": 0,
            "ped_crashes": 1,
            "bike_crashes": 0,
            "bike_involvement_unknown_crashes": 1,
            "total_killed": None,
            "total_injured": None,
            "pedestrians_killed": None,
            "pedestrians_injured": None,
            "casualty_counts_complete": False,
            "crashes_with_segments": 0,
        }
    finally:
        database.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
