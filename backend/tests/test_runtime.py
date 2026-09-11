import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.main import app, get_db, health_check, lifespan


BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_app_import_does_not_connect_to_database():
    environment = os.environ.copy()
    environment["DATABASE_URL"] = (
        "postgresql://test_user:test_password@127.0.0.1:1/test_database"
    )

    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_lifespan_does_not_create_database_schema(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("application startup attempted to create the schema")

    monkeypatch.setattr("app.models.database.Base.metadata.create_all", fail_if_called)

    async def enter_lifespan():
        async with lifespan(None):
            pass

    asyncio.run(enter_lifespan())


class HealthySession:
    statement = None

    def execute(self, statement):
        self.statement = statement
        return object()


class UnavailableSession:
    def execute(self, statement):
        raise OperationalError("SELECT 1", {}, ConnectionError("secret-db-host"))


def test_health_check_queries_database():
    database = HealthySession()

    result = asyncio.run(health_check(database))

    assert result == {
        "status": "healthy",
        "version": "0.1.0",
        "database": "connected",
    }
    assert str(database.statement) == "SELECT 1"


def test_health_check_returns_generic_503_when_database_is_unavailable():
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(health_check(UnavailableSession()))

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Database unavailable"
    assert "secret-db-host" not in exc_info.value.detail


def test_health_endpoint_reports_database_status_over_http():
    database = HealthySession()
    app.dependency_overrides[get_db] = lambda: database

    try:
        with TestClient(app) as client:
            response = client.get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["database"] == "connected"
    assert str(database.statement) == "SELECT 1"


def test_health_endpoint_hides_database_error_details_over_http():
    app.dependency_overrides[get_db] = lambda: UnavailableSession()

    try:
        with TestClient(app) as client:
            response = client.get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
    assert "secret-db-host" not in response.text


def test_cors_preflight_allows_configured_origin():
    with TestClient(app) as client:
        response = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        "http://localhost:3000"
    )
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_preflight_rejects_unconfigured_origin():
    with TestClient(app) as client:
        response = client.options(
            "/health",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
