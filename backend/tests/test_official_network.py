import json
import os
from pathlib import Path
import time

import pytest
import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import Settings
import scripts.ingest_njdot_roads as road_ingestion
from scripts.arcgis_client import (
    ArcGISClient,
    ArcGISResponseError,
    validate_layer_metadata,
)
from scripts.ingest_municipalities import (
    MunicipalityIngester,
    MunicipalityReport,
    normalize_municipality,
)
from scripts.ingest_njdot_roads import (
    IncompatibleRoadRefreshError,
    NJDOTRoadIngester,
    RoadReport,
    evaluate_coverage,
)
from scripts.ingest_osm_roads import main as deprecated_osm_main


class FakeResponse:
    def __init__(self, payload, *, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(f"HTTP {self.status_code}", response=response)

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, dict(params), timeout))
        return FakeResponse(self.responses.pop(0))


def test_arcgis_client_pages_in_object_id_order_with_bounded_timeout():
    session = FakeSession(
        [
            {
                "objectIdField": "OBJECTID",
                "maxRecordCount": 2,
                "advancedQueryCapabilities": {"supportsPagination": True},
            },
            {
                "features": [{"attributes": {"OBJECTID": 1}}, {"attributes": {"OBJECTID": 2}}],
                "exceededTransferLimit": True,
            },
            {
                "features": [{"attributes": {"OBJECTID": 3}}],
                "exceededTransferLimit": False,
            },
        ]
    )
    client = ArcGISClient(
        "https://example.test/FeatureServer/0",
        session=session,
        timeout=(3, 17),
    )

    features = list(
        client.iter_features(
            out_fields=("OBJECTID", "sri"),
            output_format="json",
            return_m=True,
        )
    )

    assert [feature["attributes"]["OBJECTID"] for feature in features] == [1, 2, 3]
    query_calls = session.calls[1:]
    assert [call[1]["resultOffset"] for call in query_calls] == [0, 2]
    assert all(call[1]["orderByFields"] == "OBJECTID ASC" for call in query_calls)
    assert all(call[1]["returnM"] == "true" for call in query_calls)
    assert all(call[2] == (3, 17) for call in session.calls)


def test_arcgis_client_rejects_service_error_in_http_200_response():
    client = ArcGISClient(
        "https://example.test/FeatureServer/0",
        session=FakeSession([{"error": {"code": 400, "message": "bad query"}}]),
    )

    with pytest.raises(ArcGISResponseError, match="bad query"):
        client.metadata()


def test_arcgis_client_offline_cache_replays_without_network(tmp_path):
    online = ArcGISClient(
        "https://example.test/FeatureServer/0",
        session=FakeSession([{"objectIdField": "OBJECTID", "maxRecordCount": 2000}]),
        cache_dir=tmp_path,
    )
    assert online.metadata()["objectIdField"] == "OBJECTID"

    offline = ArcGISClient(
        "https://example.test/FeatureServer/0",
        session=FakeSession([]),
        cache_dir=tmp_path,
        offline=True,
    )
    assert offline.metadata()["maxRecordCount"] == 2000


def test_arcgis_client_retries_transient_http_failure():
    session = FakeSession(
        [
            {"ignored": True},
            {"objectIdField": "OBJECTID", "maxRecordCount": 2000},
        ]
    )
    session.responses[0] = FakeResponse({"ignored": True}, status_code=503)

    original_get = session.get

    def get_with_prebuilt_response(url, *, params, timeout):
        session.calls.append((url, dict(params), timeout))
        response = session.responses.pop(0)
        return response if isinstance(response, FakeResponse) else FakeResponse(response)

    session.get = get_with_prebuilt_response
    client = ArcGISClient(
        "https://example.test/FeatureServer/0",
        session=session,
        max_retries=1,
        retry_backoff_seconds=0,
    )

    assert client.metadata()["objectIdField"] == "OBJECTID"
    assert len(session.calls) == 2
    session.get = original_get


def test_arcgis_client_does_not_cache_service_error(tmp_path):
    failing = ArcGISClient(
        "https://example.test/FeatureServer/0",
        session=FakeSession([{"error": {"code": 500, "message": "temporary failure"}}]),
        cache_dir=tmp_path,
        max_retries=0,
    )
    with pytest.raises(ArcGISResponseError, match="temporary failure"):
        failing.metadata()

    recovery_session = FakeSession(
        [{"objectIdField": "OBJECTID", "maxRecordCount": 2000}]
    )
    recovered = ArcGISClient(
        "https://example.test/FeatureServer/0",
        session=recovery_session,
        cache_dir=tmp_path,
        max_retries=0,
    )
    assert recovered.metadata()["objectIdField"] == "OBJECTID"
    assert len(recovery_session.calls) == 1


def test_arcgis_client_does_not_cache_malformed_metadata(tmp_path):
    session = FakeSession(
        [
            {},
            {"objectIdField": "OBJECTID", "maxRecordCount": 2000},
        ]
    )
    client = ArcGISClient(
        "https://example.test/FeatureServer/0",
        session=session,
        cache_dir=tmp_path,
        max_retries=0,
    )

    with pytest.raises(ArcGISResponseError, match="lacks objectIdField"):
        client.metadata()
    assert client.metadata()["maxRecordCount"] == 2000
    assert len(session.calls) == 2


def test_arcgis_client_does_not_cache_invalid_metadata_values(tmp_path):
    session = FakeSession(
        [
            {"objectIdField": "OBJECTID", "maxRecordCount": None},
            {"objectIdField": "OBJECTID", "maxRecordCount": 2000},
        ]
    )
    client = ArcGISClient(
        "https://example.test/FeatureServer/0",
        session=session,
        cache_dir=tmp_path,
        max_retries=0,
    )

    with pytest.raises(ArcGISResponseError, match="maxRecordCount"):
        client.metadata()
    assert client.metadata()["maxRecordCount"] == 2000
    assert len(session.calls) == 2


def test_arcgis_client_does_not_cache_wrong_loader_schema(tmp_path):
    good = {
        "objectIdField": "OBJECTID",
        "maxRecordCount": 2000,
        "geometryType": "esriGeometryPolyline",
        "hasM": True,
        "fields": [{"name": "OBJECTID"}, {"name": "sri"}],
    }
    session = FakeSession([{**good, "geometryType": "esriGeometryPoint"}, good])
    client = ArcGISClient(
        "https://example.test/FeatureServer/0",
        session=session,
        cache_dir=tmp_path,
        max_retries=0,
    )

    with pytest.raises(ArcGISResponseError, match="esriGeometryPolyline"):
        client.metadata(
            geometry_type="esriGeometryPolyline",
            required_fields=("OBJECTID", "sri"),
            require_m=True,
        )
    assert client.metadata(
        geometry_type="esriGeometryPolyline",
        required_fields=("OBJECTID", "sri"),
        require_m=True,
    )["hasM"] is True
    assert len(session.calls) == 2


def test_arcgis_client_refetches_non_utf8_cache_online(tmp_path):
    session = FakeSession(
        [{"objectIdField": "OBJECTID", "maxRecordCount": 2000}]
    )
    client = ArcGISClient(
        "https://example.test/FeatureServer/0",
        session=session,
        cache_dir=tmp_path,
        max_retries=0,
    )
    cache_path = client._cache_path(client.layer_url, {"f": "json"})
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(b"\xff\xfe\x00")

    assert client.metadata()["objectIdField"] == "OBJECTID"
    assert len(session.calls) == 1


def test_arcgis_client_rejects_null_capabilities_then_recovers(tmp_path):
    session = FakeSession(
        [
            {
                "objectIdField": "OBJECTID",
                "maxRecordCount": 2,
                "advancedQueryCapabilities": None,
            },
            {
                "objectIdField": "OBJECTID",
                "maxRecordCount": 2,
                "advancedQueryCapabilities": {"supportsPagination": True},
            },
            {
                "features": [{"attributes": {"OBJECTID": 1}}],
                "exceededTransferLimit": False,
            },
        ]
    )
    client = ArcGISClient(
        "https://example.test/FeatureServer/0",
        session=session,
        cache_dir=tmp_path,
        max_retries=0,
    )

    with pytest.raises(ArcGISResponseError, match="pagination capabilities"):
        list(client.iter_features(out_fields=("OBJECTID",)))
    assert [feature["attributes"]["OBJECTID"] for feature in client.iter_features(
        out_fields=("OBJECTID",)
    )] == [1]


def test_layer_schema_validation_rejects_missing_measure_contract():
    metadata = {
        "geometryType": "esriGeometryPolyline",
        "hasM": False,
        "fields": [{"name": "OBJECTID"}, {"name": "sri"}],
    }

    with pytest.raises(ArcGISResponseError, match="M-valued"):
        validate_layer_metadata(
            metadata,
            geometry_type="esriGeometryPolyline",
            required_fields=("OBJECTID", "sri"),
            require_m=True,
        )


def test_boundary_polygon_becomes_valid_multipolygon():
    feature = {
        "type": "Feature",
        "properties": {
            "MUN_CODE": "1101",
            "MUN_LABEL": "Test Township",
            "COUNTY": "MERCER",
            "MUN_TYPE": "Township",
            "NAME": "Test Township",
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[-74.8, 40.2], [-74.7, 40.2], [-74.7, 40.3], [-74.8, 40.3], [-74.8, 40.2]]
            ],
        },
    }

    record = normalize_municipality(feature)

    assert record.muni_code == "1101"
    assert record.name == "Test Township"
    assert record.county == "Mercer"
    assert record.geometry["type"] == "MultiPolygon"
    assert len(record.geometry["coordinates"]) == 1


def test_boundary_load_repairs_geometry_and_preserves_identity_on_rerun():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    engine = create_engine(Settings(database_url=database_url, _env_file=None).database_url)
    connection = engine.connect()
    transaction = connection.begin()
    database = Session(bind=connection)
    try:
        record = normalize_municipality(
            {
                "properties": {
                    "MUN_CODE": "9999",
                    "MUN_LABEL": "Repair Test",
                    "COUNTY": "MERCER",
                    "MUN_TYPE": "Township",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [-74.8, 40.1], [-74.6, 40.3], [-74.8, 40.3],
                        [-74.6, 40.1], [-74.8, 40.1],
                    ]],
                },
            }
        )
        first_report = MunicipalityReport(accepted=1)
        MunicipalityIngester.load_to_db([record], database, first_report)
        first = connection.execute(
            text(
                """
                SELECT muni_id, ST_IsValid(geom), GeometryType(geom)
                FROM municipalities WHERE muni_code = '9999'
                """
            )
        ).one()
        second_report = MunicipalityReport(accepted=1)
        MunicipalityIngester.load_to_db([record], database, second_report)
        second_id = connection.execute(
            text("SELECT muni_id FROM municipalities WHERE muni_code = '9999'")
        ).scalar_one()

        assert first.st_isvalid and first.geometrytype == "MULTIPOLYGON"
        assert second_id == first.muni_id
        assert first_report.inserted == 1
        assert second_report.updated == 1
    finally:
        database.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_road_parser_preserves_measures_and_uses_path_measure_bounds():
    feature = {
        "attributes": {
            "OBJECTID": 17,
            "sri": " 00000009__x212800 ",
            "mp_start": 0.0,
            "mp_end": 9.0,
            "f_system_code": 3,
        },
        "geometry": {
            "hasM": True,
            "paths": [
                [
                    [-74.2981272003279, 40.4555376067631, 0.00632949999487664],
                    [-74.2984220837621, 40.4551469577767, 0.0309999994934742],
                ]
            ],
        },
    }

    parsed = NJDOTRoadIngester.parse_feature(feature)

    assert len(parsed.analysis_paths) == 1
    assert len(parsed.route_paths) == 1
    route = parsed.route_paths[0]
    assert route.source_id == "NJDOT:17:0"
    assert route.sri == "00000009__X212800"
    assert route.mp_start == pytest.approx(0.00632949999487664)
    assert route.mp_end == pytest.approx(0.0309999994934742)
    assert "0.00632949999487664" in route.ewkt
    assert "0.0309999994934742" in route.ewkt
    assert parsed.analysis_paths[0].road_class == "arterial"


def test_road_parser_keeps_xy_analysis_path_when_reference_measures_are_invalid():
    feature = {
        "attributes": {
            "OBJECTID": 18,
            "sri": "00000010__",
            "mp_start": 2.0,
            "mp_end": 3.0,
            "f_system_code": 6,
        },
        "geometry": {
            "paths": [[[-74.7, 40.2], [-74.699, 40.2]]],
        },
    }

    parsed = NJDOTRoadIngester.parse_feature(feature)

    assert len(parsed.analysis_paths) == 1
    assert parsed.analysis_paths[0].road_class == "collector"
    assert parsed.route_paths == []
    assert parsed.reference_rejections == ["NJDOT:18:0: missing or invalid M values"]


def test_individual_bad_road_is_reported_without_discarding_the_snapshot():
    class OneBadFeatureClient:
        def metadata(self, **kwargs):
            del kwargs
            return {
                "geometryType": "esriGeometryPolyline",
                "hasM": True,
                "fields": [{"name": field} for field in (
                    "OBJECTID", "sri", "mp_start", "mp_end", "f_system_code"
                )],
            }

        def count(self):
            return 1

        def iter_features(self, **kwargs):
            del kwargs
            yield {
                "attributes": {"OBJECTID": 99, "sri": "BAD_PATH"},
                "geometry": {"paths": []},
            }

    class NoDatabaseRoadIngester(NJDOTRoadIngester):
        @staticmethod
        def begin_snapshot(db):
            del db

        @staticmethod
        def validate_complete_snapshot(db):
            del db

    report = NoDatabaseRoadIngester(OneBadFeatureClient()).ingest(object())

    assert report.fetched == 1
    assert report.rejected_features == 1
    assert report.analysis_coverage_complete is False
    assert "no paths" in report.rejection_details[0]


def test_partial_road_coverage_is_strict_by_default():
    report = RoadReport(
        source_count=10,
        fetched=10,
        analysis_segments_upserted=20,
        reference_path_rejections=1,
    )

    assert evaluate_coverage(report, allow_partial=False) != 0
    assert report.analysis_coverage_complete is True
    assert report.reference_coverage_complete is False
    assert report.partial_coverage_accepted is False


def test_partial_road_coverage_requires_nonempty_analysis_network():
    report = RoadReport(
        source_count=10,
        fetched=10,
        analysis_segments_upserted=0,
        rejected_features=10,
    )

    assert evaluate_coverage(report, allow_partial=True) != 0
    assert report.analysis_coverage_complete is False
    assert report.reference_coverage_complete is False
    assert report.partial_coverage_accepted is False


@pytest.mark.parametrize("allow_partial", [False, True])
def test_empty_road_snapshot_is_never_complete_or_accepted(allow_partial):
    report = RoadReport(source_count=0, fetched=0, analysis_segments_upserted=0)

    assert evaluate_coverage(report, allow_partial=allow_partial) != 0
    assert report.analysis_coverage_complete is False
    assert report.reference_coverage_complete is False
    assert report.partial_coverage_accepted is False


def test_partial_road_coverage_opt_in_sets_explicit_report_flag():
    report = RoadReport(
        source_count=10,
        fetched=10,
        analysis_segments_upserted=20,
        rejected_features=1,
        reference_path_rejections=2,
    )

    assert evaluate_coverage(report, allow_partial=True) == 0
    assert report.analysis_coverage_complete is False
    assert report.reference_coverage_complete is False
    assert report.partial_coverage_accepted is True


def test_road_json_report_includes_explicit_coverage_flags(tmp_path):
    report = RoadReport(
        source_count=10,
        fetched=10,
        analysis_segments_upserted=20,
        rejected_features=1,
    )
    assert evaluate_coverage(report, allow_partial=True) == 0
    report_path = tmp_path / "roads.json"

    road_ingestion._write_report(report_path, report)

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["analysis_coverage_complete"] is False
    assert payload["reference_coverage_complete"] is False
    assert payload["partial_coverage_accepted"] is True


def test_deprecated_osm_entrypoint_fails_with_official_replacement(caplog):
    assert deprecated_osm_main([]) == 2
    assert "ingest_njdot_roads.py" in caplog.text


def test_network_batch_load_is_idempotent_and_retains_linestring_m():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    engine = create_engine(Settings(database_url=database_url, _env_file=None).database_url)
    connection = engine.connect()
    transaction = connection.begin()
    database = Session(bind=connection)
    ingester = NJDOTRoadIngester(segment_length_miles=0.1)

    try:
        connection.execute(
            text(
                """
                INSERT INTO municipalities (muni_id, name, county, muni_code, geom)
                VALUES (
                    -90201, 'Network Test', 'Mercer', 'network-test',
                    ST_GeomFromText(
                        'MULTIPOLYGON(((-74.8 40.1,-74.6 40.1,-74.6 40.3,-74.8 40.3,-74.8 40.1)))',
                        4326
                    )
                )
                """
            )
        )
        parsed = NJDOTRoadIngester.parse_feature(
            {
                "attributes": {
                    "OBJECTID": 19,
                    "sri": "00000011__",
                    "mp_start": 1.0,
                    "mp_end": 1.2,
                    "f_system_code": 7,
                },
                "geometry": {
                    "hasM": True,
                    "paths": [[[-74.75, 40.2, 1.0], [-74.747, 40.2, 1.2]]],
                },
            }
        )

        first = ingester.load_batch(database, parsed.analysis_paths, parsed.route_paths)
        database.flush()
        first_ids = connection.execute(
            text("SELECT segment_id FROM road_segments WHERE source_id LIKE 'NJDOT:19:0:%' ORDER BY source_id")
        ).scalars().all()
        second = ingester.load_batch(database, parsed.analysis_paths, parsed.route_paths)
        database.flush()
        second_ids = connection.execute(
            text("SELECT segment_id FROM road_segments WHERE source_id LIKE 'NJDOT:19:0:%' ORDER BY source_id")
        ).scalars().all()

        route = connection.execute(
            text(
                """
                SELECT ST_NDims(geom), ST_M(ST_PointN(geom, 1)), mp_start, mp_end
                FROM road_routes WHERE source_id = 'NJDOT:19:0'
                """
            )
        ).one()
        segment_rows = connection.execute(
            text(
                """
                SELECT source_id, length_miles, ST_IsValid(geom), GeometryType(geom)
                FROM road_segments WHERE source_id LIKE 'NJDOT:19:0:%'
                """
            )
        ).all()

        assert first.routes_upserted == 1
        assert second.routes_upserted == 1
        assert first_ids == second_ids
        assert route == (3, 1.0, 1.0, 1.2)
        assert len(segment_rows) == 2
        assert all(row.length_miles > 0 for row in segment_rows)
        assert all(row.length_miles <= 0.1005 for row in segment_rows)

        second_path = NJDOTRoadIngester.parse_feature(
            {
                "attributes": {
                    "OBJECTID": 20,
                    "sri": "00000012__",
                    "f_system_code": 5,
                },
                "geometry": {
                    "hasM": True,
                    "paths": [[[-74.74, 40.2, 2.0], [-74.739, 40.2, 2.1]]],
                },
            }
        )
        ingester.begin_snapshot(database)
        ingester.load_batch(
            database,
            parsed.analysis_paths + second_path.analysis_paths,
            parsed.route_paths + second_path.route_paths,
        )
        ingester.validate_complete_snapshot(database)
        ingester.begin_snapshot(database)
        ingester.load_batch(database, parsed.analysis_paths, parsed.route_paths)
        with pytest.raises(IncompatibleRoadRefreshError, match="fresh database"):
            ingester.validate_complete_snapshot(database)

        with pytest.raises(IncompatibleRoadRefreshError, match="fresh database"):
            NJDOTRoadIngester(segment_length_miles=0.05).load_batch(
                database, parsed.analysis_paths, parsed.route_paths
            )
        assert all(row.st_isvalid and row.geometrytype == "LINESTRING" for row in segment_rows)
    finally:
        database.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_snapshot_guard_scales_linearly_for_thousands_of_paths():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

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
                    -90202, 'Guard Test', 'Mercer', 'guard-test',
                    ST_GeomFromText(
                        'MULTIPOLYGON(((-75 40,-74 40,-74 41,-75 41,-75 40)))', 4326
                    )
                )
                """
            )
        )
        NJDOTRoadIngester.begin_snapshot(database)
        connection.execute(
            text(
                """
                INSERT INTO seen_njdot_paths(source_id)
                SELECT 'NJDOT' || chr(58) || value || chr(58) || '0'
                FROM generate_series(1, 2000) AS value
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO road_segments
                    (source_id, sri, road_name, road_type, road_class,
                     length_miles, muni_id, geom)
                SELECT 'NJDOT' || chr(58) || path_id || chr(58) || '0' ||
                           chr(58) || 'Mguard-test' || chr(58) || 'P0' ||
                           chr(58) || 'S' || piece_id,
                       'SRI', 'SRI', 'functional_class_7', 'local',
                       0.05, -90202,
                       ST_GeomFromText('LINESTRING(-74.8 40.2,-74.799 40.2)', 4326)
                FROM generate_series(1, 2000) AS path_id
                CROSS JOIN generate_series(0, 1) AS piece_id
                """
            )
        )

        started = time.perf_counter()
        NJDOTRoadIngester.validate_complete_snapshot(database)
        elapsed = time.perf_counter() - started

        assert elapsed < 0.5
    finally:
        database.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
