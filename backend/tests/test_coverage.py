import os
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import app
from app.models.database import get_db
from app.services.hin_service import HINService
import app.routers.analysis as analysis_router


@pytest.fixture
def coverage_api(monkeypatch):
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    normalized_url = Settings(database_url=database_url, _env_file=None).database_url
    engine = create_engine(normalized_url)
    connection = engine.connect()
    transaction = connection.begin()
    database = Session(bind=connection)

    app.dependency_overrides[get_db] = lambda: database

    try:
        with TestClient(app) as client:
            yield client, database, connection
    finally:
        app.dependency_overrides.clear()
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
                :muni_id, :name, 'Mercer', :muni_code,
                ST_GeomFromText(
                    'MULTIPOLYGON(((-75 40, -74 40, -74 41, -75 41, -75 40)))',
                    4326
                )
            )
            """
        ),
        {
            "muni_id": muni_id,
            "name": f"Coverage {abs(muni_id)}",
            "muni_code": f"coverage-{abs(muni_id)}",
        },
    )


def insert_crash(connection, crash_id, muni_id, year):
    connection.execute(
        text(
            """
            INSERT INTO crashes
                (crash_id, crash_date, severity, ped_involved, bike_involved,
                 muni_id, geom)
            VALUES
                (:crash_id, :crash_date, 'property_damage', false, false,
                 :muni_id, ST_GeomFromText('POINT(-74.5 40.3)', 4326))
            """
        ),
        {
            "crash_id": crash_id,
            "crash_date": date(year, 6, 1),
            "muni_id": muni_id,
        },
    )


def insert_analysis(
    connection,
    analysis_id,
    muni_id,
    start_year,
    end_year,
    *,
    status="completed",
    total_crashes=0,
):
    connection.execute(
        text(
            """
            INSERT INTO analyses
                (analysis_id, muni_id, created_at, start_year, end_year,
                 years_included, status, total_crashes,
                 snap_distance_meters, significance_threshold)
            VALUES
                (:analysis_id, :muni_id, CURRENT_TIMESTAMP,
                 :start_year, :end_year, :years_included,
                 :status, :total_crashes, 50, 0.05)
            """
        ),
        {
            "analysis_id": analysis_id,
            "muni_id": muni_id,
            "start_year": start_year,
            "end_year": end_year,
            "years_included": list(range(start_year, end_year + 1)),
            "status": status,
            "total_crashes": total_crashes,
        },
    )


def analysis_request(muni_id, start_year, end_year):
    return {
        "muni_id": muni_id,
        "config": {
            "start_year": start_year,
            "end_year": end_year,
            "snap_distance_meters": 50,
            "segment_length_miles": 0.1,
            "significance_threshold": 0.05,
        },
    }


def test_coverage_endpoint_reports_sorted_positive_loaded_record_counts(coverage_api):
    client, _, connection = coverage_api
    insert_municipality(connection, -94001)
    insert_crash(connection, -94011, -94001, 2022)
    insert_crash(connection, -94012, -94001, 2020)
    insert_crash(connection, -94013, -94001, 2022)

    response = client.get("/api/municipalities/-94001/coverage")

    assert response.status_code == 200
    assert response.json() == {
        "muni_id": -94001,
        "available_years": [2020, 2022],
        "crash_counts_by_year": {"2020": 1, "2022": 2},
        "total_crashes": 3,
    }


def test_coverage_endpoint_handles_no_loaded_years_and_unknown_municipality(coverage_api):
    client, _, connection = coverage_api
    insert_municipality(connection, -94101)

    empty = client.get("/api/municipalities/-94101/coverage")
    missing = client.get("/api/municipalities/-94102/coverage")

    assert empty.status_code == 200
    assert empty.json() == {
        "muni_id": -94101,
        "available_years": [],
        "crash_counts_by_year": {},
        "total_crashes": 0,
    }
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Municipality not found"}


def test_analysis_post_blocks_entire_unloaded_historical_range_before_persist(
    coverage_api,
):
    client, _, connection = coverage_api
    insert_municipality(connection, -94201)
    insert_crash(connection, -94211, -94201, 2022)

    response = client.post(
        "/api/analysis/",
        json=analysis_request(-94201, 2017, 2021),
    )
    persisted = connection.execute(
        text("SELECT COUNT(*) FROM analyses WHERE muni_id = -94201")
    ).scalar_one()

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "missing_crash_data",
        "message": (
            "Crash records are not loaded for every selected year. "
            "Load the missing years before starting this analysis."
        ),
        "missing_years": [2017, 2018, 2019, 2020, 2021],
        "available_years": [2022],
    }
    assert persisted == 0


def test_analysis_post_identifies_a_hole_inside_the_selected_range(coverage_api):
    client, _, connection = coverage_api
    insert_municipality(connection, -94301)
    insert_crash(connection, -94311, -94301, 2018)
    insert_crash(connection, -94312, -94301, 2020)

    response = client.post(
        "/api/analysis/",
        json=analysis_request(-94301, 2018, 2020),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["missing_years"] == [2019]
    assert response.json()["detail"]["available_years"] == [2018, 2020]


def test_analysis_post_accepts_a_multi_year_range_with_loaded_records(coverage_api):
    client, _, connection = coverage_api
    insert_municipality(connection, -94401)
    insert_crash(connection, -94411, -94401, 2019)
    insert_crash(connection, -94412, -94401, 2020)

    response = client.post(
        "/api/analysis/",
        json=analysis_request(-94401, 2019, 2020),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert connection.execute(
        text("SELECT COUNT(*) FROM analyses WHERE muni_id = -94401")
    ).scalar_one() == 1


def test_analysis_detail_distinguishes_no_data_missing_years_stale_and_ready(
    coverage_api,
):
    client, _, connection = coverage_api

    insert_municipality(connection, -94501)
    insert_analysis(connection, -94511, -94501, 2017, 2018, total_crashes=0)

    insert_municipality(connection, -94502)
    insert_crash(connection, -94521, -94502, 2018)
    insert_crash(connection, -94522, -94502, 2020)
    insert_analysis(connection, -94512, -94502, 2018, 2020, total_crashes=2)

    insert_municipality(connection, -94503)
    insert_crash(connection, -94531, -94503, 2022)
    insert_analysis(connection, -94513, -94503, 2022, 2022, total_crashes=0)

    insert_municipality(connection, -94504)
    insert_crash(connection, -94541, -94504, 2021)
    insert_crash(connection, -94542, -94504, 2022)
    insert_analysis(connection, -94514, -94504, 2021, 2022, total_crashes=2)
    from app.models.tables import Analysis
    from app.services.version_service import current_input_version
    database = coverage_api[1]
    current = database.get(Analysis, -94514)
    current.input_version = current_input_version(database)
    database.flush()

    no_data = client.get("/api/analysis/-94511").json()
    missing = client.get("/api/analysis/-94512").json()
    stale = client.get("/api/analysis/-94513").json()
    ready = client.get("/api/analysis/-94514").json()

    assert no_data["data_status"] == "no_data"
    assert no_data["available_years"] == []
    assert no_data["missing_years"] == [2017, 2018]
    assert "No crash records" in no_data["data_message"]

    assert missing["data_status"] == "missing_years"
    assert missing["available_years"] == [2018, 2020]
    assert missing["missing_years"] == [2019]
    assert "not loaded" in missing["data_message"]

    assert stale["status"] == "completed"
    assert stale["total_crashes"] == 0
    assert stale["data_status"] == "stale"
    assert stale["available_years"] == [2022]
    assert stale["missing_years"] == []
    assert "changed" in stale["data_message"]

    assert ready["data_status"] == "ready"
    assert ready["available_years"] == [2021, 2022]
    assert ready["missing_years"] == []
    assert ready["data_message"] is None


def test_pending_analysis_reports_ready_without_reclassifying_runtime_status(coverage_api):
    client, _, connection = coverage_api
    insert_municipality(connection, -94601)
    insert_analysis(
        connection,
        -94611,
        -94601,
        2019,
        2020,
        status="running",
        total_crashes=None,
    )

    response = client.get("/api/analysis/-94611")

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert response.json()["data_status"] == "ready"


@pytest.mark.parametrize(
    "path",
    [
        "/api/analysis/-94711/summary",
        "/api/analysis/-94711/crashes",
        "/api/analysis/-94711/hin",
        "/api/analysis/-94711/export/pdf",
        "/api/export/-94711/geojson",
    ],
)
def test_completed_stale_analysis_results_are_blocked_with_shared_409_guard(
    coverage_api,
    monkeypatch,
    path,
):
    client, _, connection = coverage_api
    insert_municipality(connection, -94701)
    insert_crash(connection, -94721, -94701, 2022)
    insert_crash(connection, -94722, -94701, 2022)
    insert_analysis(connection, -94711, -94701, 2022, 2022, total_crashes=1)
    monkeypatch.setattr(
        analysis_router.PDFReportGenerator,
        "generate_report",
        lambda *args: b"%PDF-test",
    )

    response = client.get(path)

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "analysis_data_stale",
        "message": (
            "The source data or analysis methodology has changed, or this result predates version tracking. "
            "Rerun the analysis to refresh its results."
        ),
        "data_status": "stale",
        "missing_years": [],
        "available_years": [2022],
    }


def test_hin_service_refuses_to_complete_an_analysis_with_unloaded_years(coverage_api):
    _, database, connection = coverage_api
    insert_municipality(connection, -94801)
    insert_analysis(
        connection,
        -94811,
        -94801,
        2017,
        2021,
        status="pending",
        total_crashes=None,
    )

    with pytest.raises(ValueError, match="not loaded"):
        HINService(database).run_analysis(
            -94811,
            -94801,
            2017,
            2021,
            snap_distance_meters=50,
        )

    status = connection.execute(
        text("SELECT status FROM analyses WHERE analysis_id = -94811")
    ).scalar_one()
    assert status != "completed"
