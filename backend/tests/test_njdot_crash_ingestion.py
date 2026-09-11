import csv
import io
import json
import os
import subprocess
import sys
import time
import zipfile
from argparse import Namespace
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import Settings
import scripts.ingest_njdot_crashes as crash_ingestion
from scripts.ingest_all_real_data import build_stage_commands, run_pipeline
from scripts.ingest_njdot_crashes import (
    ArchiveReport,
    ArchiveValidationError,
    FIELD_NAMES,
    NJDOTCrashIngester,
    build_external_id,
    locate_route_point,
    normalize_municipality_name,
    parse_njdot_accidents,
    prepare_record,
)


def make_row(**overrides):
    values = {name: "" for name in FIELD_NAMES}
    values.update(
        {
            "id": "2022110101-00001",
            "county_name": "MERCER",
            "municipality_name": "WEST WINDSOR TWP",
            "crash_date": "03/14/2022",
            "crash_time": "0815",
            "total_killed": "0",
            "total_injured": "1",
            "pedestrians_killed": "0",
            "pedestrians_injured": "1",
            "severity": "I",
            "crash_location": "US 1",
            "sri_std_rte_identifier": "00000001__",
            "milepost": "8.25",
            "latitude": "40.2901",
            "longitude": "74.6402",
            "light_condition": "DAYLIGHT",
        }
    )
    values.update(overrides)
    return [values[name] for name in FIELD_NAMES]


def write_archive(path: Path, rows, *, member=None):
    member = member or f"{path.stem}.txt"
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerows(rows)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, buffer.getvalue().encode("latin-1"))


def test_archive_parser_streams_valid_rows_and_reports_wrong_column_count(tmp_path):
    archive = tmp_path / "Mercer2022Accidents.zip"
    write_archive(archive, [make_row(), make_row()[:-1]])
    rejections = []

    records = list(parse_njdot_accidents(archive, on_reject=rejections.append))

    assert len(records) == 1
    assert records[0]["id"] == "2022110101-00001"
    assert rejections == ["schema_columns"]


def test_archive_parser_rejects_an_unexpected_zip_shape(tmp_path):
    archive = tmp_path / "Mercer2022Accidents.zip"
    write_archive(archive, [make_row()], member="readme.txt")

    with pytest.raises(ArchiveValidationError, match="Accidents.txt"):
        list(parse_njdot_accidents(archive))


def test_archive_parser_recovers_unambiguous_historical_multiline_damage_field(tmp_path):
    archive = tmp_path / "Mercer2017Accidents.zip"
    broken = make_row(id="2017-multiline")[:49]
    broken[48] = "Guardrail owner"
    write_archive(
        archive,
        [broken, ["PO Box 5042"], ["Woodbridge NJ", "7470"]],
    )
    rejections = []

    records = list(parse_njdot_accidents(archive, on_reject=rejections.append))

    assert len(records) == 1
    assert records[0]["id"] == "2017-multiline"
    assert records[0]["other_property_damage"] == "Guardrail owner PO Box 5042 Woodbridge NJ"
    assert records[0]["reporting_badge_no"] == "7470"
    assert rejections == []


def test_archive_parser_rejects_ambiguous_continuation_without_consuming_next_record(tmp_path):
    archive = tmp_path / "Mercer2017Accidents.zip"
    broken = make_row(id="ambiguous")[:49]
    following = make_row(id="valid-following")
    write_archive(archive, [broken, ["damage", "badge is not numeric"], following])
    rejections = []

    records = list(parse_njdot_accidents(archive, on_reject=rejections.append))

    assert [record["id"] for record in records] == ["valid-following"]
    assert rejections == ["schema_columns"]


def test_archive_parser_recovers_historical_damage_field_with_blank_continuation(tmp_path):
    archive = tmp_path / "Mercer2018Accidents.zip"
    broken = make_row(id="2018-blank-continuation")[:49]
    broken[48] = "DOT Pole E412"
    write_archive(archive, [broken, [], ["PO Box 600", "8257"]])
    rejections = []

    records = list(parse_njdot_accidents(archive, on_reject=rejections.append))

    assert len(records) == 1
    assert records[0]["other_property_damage"] == "DOT Pole E412 PO Box 600"
    assert records[0]["reporting_badge_no"] == "8257"
    assert rejections == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (" West  Windsor TWP. ", "WEST WINDSOR TOWNSHIP"),
        ("Princeton Boro", "PRINCETON BOROUGH"),
        ("Jersey City", "JERSEY CITY"),
        ("Boonton Town", "BOONTON TOWN"),
        ("Mount Ephriam Boro", "MOUNT EPHRAIM BOROUGH"),
        ("Orange City", "CITY OF ORANGE TOWNSHIP"),
        ("Fairfield Boro", "FAIRFIELD TOWNSHIP"),
        ("South Orange Village Twp", "SOUTH ORANGE VILLAGE"),
        ("Milford Twp", "MILFORD BOROUGH"),
        ("Parsippany Troy Hills", "PARSIPPANY TROY HILLS TOWNSHIP"),
        ("Passaic Twp", "LONG HILL TOWNSHIP"),
        ("Pt Pleasant Beach Boro", "POINT PLEASANT BEACH BOROUGH"),
        ("Lower Alloways Crk Twp", "LOWER ALLOWAYS CREEK TOWNSHIP"),
        ("Sandvston Twp", "SANDYSTON TOWNSHIP"),
    ],
)
def test_municipality_normalization_preserves_government_type(source, expected):
    assert normalize_municipality_name(source) == expected


def test_stable_external_id_includes_county_year_and_stays_within_schema_limit():
    source_id = "A" * 80

    first = build_external_id("Cape May", 2022, source_id)

    assert first == build_external_id("Cape May", 2022, source_id)
    assert first != build_external_id("Cape May", 2021, source_id)
    assert first != build_external_id("Mercer", 2022, source_id)
    assert len(first) <= 50


def test_record_validation_negates_positive_nj_longitude_without_fabricating_fields():
    raw = dict(zip(FIELD_NAMES, make_row()))

    prepared = prepare_record(raw, expected_county="Mercer", expected_year=2022)

    assert prepared.crash_date == date(2022, 3, 14)
    assert prepared.severity == "injury_unknown"
    assert prepared.reported_point == pytest.approx((-74.6402, 40.2901))
    assert prepared.ped_involved is True
    assert prepared.bike_involved is None
    assert prepared.total_killed == 0
    assert prepared.total_injured == 1
    assert prepared.pedestrians_killed == 0
    assert prepared.pedestrians_injured == 1
    assert prepared.sri == "00000001__"
    assert prepared.milepost == pytest.approx(8.25)


def test_record_validation_preserves_unknown_casualty_counts():
    raw = dict(
        zip(
            FIELD_NAMES,
            make_row(
                total_killed="",
                total_injured=" ",
                pedestrians_killed="",
                pedestrians_injured="",
            ),
        )
    )

    prepared = prepare_record(raw, expected_county="Mercer", expected_year=2022)

    assert prepared.total_killed is None
    assert prepared.total_injured is None
    assert prepared.pedestrians_killed is None
    assert prepared.pedestrians_injured is None
    assert prepared.ped_involved is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("total_killed", "-1"),
        ("total_injured", "1.5"),
        ("pedestrians_killed", "unknown"),
        ("pedestrians_injured", "-2"),
    ],
)
def test_record_validation_rejects_invalid_casualty_counts(field, value):
    raw = dict(zip(FIELD_NAMES, make_row(**{field: value})))

    with pytest.raises(ValueError, match=field):
        prepare_record(raw, expected_county="Mercer", expected_year=2022)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"severity": "S"}, "severity"),
        ({"crash_date": "2022-03-14"}, "date"),
        ({"crash_date": "03/14/2021"}, "year"),
        ({"county_name": "MIDDLESEX"}, "county"),
        ({"id": ""}, "source_id"),
    ],
)
def test_record_validation_rejects_bad_source_values(changes, reason):
    raw = dict(zip(FIELD_NAMES, make_row(**changes)))

    with pytest.raises(ValueError, match=reason):
        prepare_record(raw, expected_county="Mercer", expected_year=2022)


def test_missing_coordinates_keep_route_reference_for_fallback():
    raw = dict(zip(FIELD_NAMES, make_row(latitude="", longitude="")))

    prepared = prepare_record(raw, expected_county="Mercer", expected_year=2022)

    assert prepared.reported_point is None
    assert prepared.sri == "00000001__"
    assert prepared.milepost == pytest.approx(8.25)


class FakeRouteResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class FakeRouteSession:
    def __init__(self, rows):
        self.rows = rows
        self.statement = None
        self.parameters = None

    def execute(self, statement, parameters):
        self.statement = str(statement)
        self.parameters = parameters
        return FakeRouteResult(self.rows)


def test_route_lookup_collapses_floating_noise_at_a_shared_endpoint():
    database = FakeRouteSession(
        [
            {"source_id": "a", "longitude": -74.6, "latitude": 40.2},
            {"source_id": "b", "longitude": -74.60000000002, "latitude": 40.20000000002},
        ]
    )

    result = locate_route_point(database, muni_id=17, sri="0001", milepost=2.0)

    assert result.point == (-74.6, 40.2)
    assert result.reason is None
    assert "ST_LocateAlong" in database.statement
    assert "ST_Dump" in database.statement
    assert "ST_Force2D" in database.statement
    assert "LEAST" in database.statement and "GREATEST" in database.statement
    assert database.parameters == {"muni_id": 17, "sri": "0001", "milepost": 2.0}


def test_route_lookup_rejects_meaningfully_distinct_points_as_ambiguous():
    database = FakeRouteSession(
        [
            {"source_id": "a", "longitude": -74.6, "latitude": 40.2},
            {"source_id": "b", "longitude": -74.61, "latitude": 40.21},
        ]
    )

    result = locate_route_point(database, muni_id=17, sri="0001", milepost=2.0)

    assert result.point is None
    assert result.reason == "route_ambiguous"


def test_route_lookup_reports_measure_gaps():
    result = locate_route_point(FakeRouteSession([]), muni_id=17, sri="0001", milepost=2.0)

    assert result.point is None
    assert result.reason == "route_measure_unresolved"


class FakeDownloadResponse:
    def __init__(self, payload: bytes, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        assert chunk_size <= 1024 * 1024
        yield self.payload


class FakeDownloadSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, timeout, stream):
        self.calls.append((url, timeout, stream))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def archive_bytes():
    output = io.BytesIO()
    row_buffer = io.StringIO(newline="")
    csv.writer(row_buffer).writerow(make_row())
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("Mercer2022Accidents.txt", row_buffer.getvalue().encode("latin-1"))
    return output.getvalue()


def test_downloader_retries_with_bounded_timeout_then_caches_valid_archive(tmp_path):
    session = FakeDownloadSession([RuntimeError("temporary"), FakeDownloadResponse(archive_bytes())])
    ingester = NJDOTCrashIngester(cache_dir=tmp_path, session=session, retries=2, timeout=(3, 19))

    path = ingester.fetch_archive("Mercer", 2022)

    assert path == tmp_path / "Mercer2022Accidents.zip"
    assert path.exists()
    assert len(session.calls) == 2
    assert all(call[1:] == ((3, 19), True) for call in session.calls)


def test_downloader_accepts_verified_historical_cape_may_member_with_space(tmp_path):
    output = io.BytesIO()
    row_buffer = io.StringIO(newline="")
    csv.writer(row_buffer).writerow(make_row(county_name="CAPE MAY"))
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "Cape May2017Accidents.txt",
            row_buffer.getvalue().encode("latin-1"),
        )
    session = FakeDownloadSession([FakeDownloadResponse(output.getvalue())])
    ingester = NJDOTCrashIngester(cache_dir=tmp_path, session=session, retries=1)

    path = ingester.fetch_archive("Cape May", 2017)

    assert path.name == "CapeMay2017Accidents.zip"
    assert list(parse_njdot_accidents(path))[0]["county_name"] == "CAPE MAY"


def pipeline_args(tmp_path, **overrides):
    values = {
        "start_year": 2022,
        "end_year": 2022,
        "county": ["Mercer"],
        "cache_dir": tmp_path / "cache",
        "report": tmp_path / "manifest.json",
        "offline": False,
        "refresh_cache": False,
        "segment_length": 0.1,
        "batch_size": 500,
        "allow_partial_roads": False,
        "skip_municipalities": False,
        "skip_roads": False,
        "skip_crashes": False,
    }
    values.update(overrides)
    return Namespace(**values)


def test_orchestrator_builds_boundaries_roads_crashes_in_dependency_order(tmp_path):
    commands = build_stage_commands(pipeline_args(tmp_path))

    assert [command.stage for command in commands] == ["boundaries", "roads", "crashes"]
    assert [Path(command.argv[1]).name for command in commands] == [
        "ingest_municipalities.py",
        "ingest_njdot_roads.py",
        "ingest_njdot_crashes.py",
    ]
    crash_command = commands[-1].argv
    assert crash_command[0] == os.fspath(Path(os.sys.executable))
    assert crash_command.count("--county") == 1
    assert crash_command[crash_command.index("--county") + 1] == "Mercer"


def test_orchestrator_forwards_explicit_partial_road_acceptance(tmp_path):
    strict_road_command = build_stage_commands(pipeline_args(tmp_path))[1].argv
    accepted_road_command = build_stage_commands(
        pipeline_args(tmp_path, allow_partial_roads=True)
    )[1].argv

    assert "--allow-partial" not in strict_road_command
    assert "--allow-partial" in accepted_road_command


def test_orchestrator_stops_on_failure_and_writes_durable_manifest(tmp_path):
    calls = []

    def runner(argv, check=False):
        calls.append(Path(argv[1]).name)
        return Namespace(returncode=7 if len(calls) == 2 else 0)

    report = tmp_path / "manifest.json"
    status = run_pipeline(pipeline_args(tmp_path, report=report), runner=runner)

    assert status == 1
    assert calls == ["ingest_municipalities.py", "ingest_njdot_roads.py"]
    manifest = json.loads(report.read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert [stage["status"] for stage in manifest["stages"]] == ["succeeded", "failed"]


def test_retired_socrata_entrypoint_routes_to_official_njdot_cli():
    script = Path(__file__).parents[1] / "scripts" / "ingest_nj_crash_data.py"

    result = subprocess.run(
        [sys.executable, os.fspath(script), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "official NJDOT Accidents archives" in result.stdout


def test_crash_manifest_is_running_until_every_requested_archive_succeeds():
    reports = [
        ArchiveReport("Mercer", 2022, "https://example.test/mercer", status="succeeded"),
        ArchiveReport("Middlesex", 2022, "https://example.test/middlesex"),
    ]

    progress = crash_ingestion._manifest(reports, started_at="start", parameters={})

    assert progress["status"] == "running"
    assert progress["totals"]["status_counts"] == {"pending": 1, "succeeded": 1}

    reports[1].status = "succeeded"
    complete = crash_ingestion._manifest(reports, started_at="start", parameters={})
    assert complete["status"] == "succeeded"
    assert complete["totals"]["status_counts"] == {"succeeded": 2}


def crash_run_args(tmp_path):
    return Namespace(
        start_year=2022,
        end_year=2022,
        county=["Mercer", "Middlesex"],
        cache_dir=tmp_path,
        report=tmp_path / "crash-manifest.json",
        batch_size=10,
        offline=True,
        refresh_cache=False,
        archives=[],
        municipality=None,
        muni_id=None,
    )


def test_crash_manifest_predeclares_all_archives_and_stays_running_on_interrupt(
    tmp_path, monkeypatch
):
    paths = {}
    for county in ("Mercer", "Middlesex"):
        path = tmp_path / f"{county}2022Accidents.zip"
        write_archive(path, [make_row(county_name=county.upper())])
        paths[county] = path

    class FakeIngester:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def source_url(county, year):
            return f"https://example.test/{county}/{year}"

        def fetch_archive(self, county, _year):
            return paths[county]

        def ingest_archive(self, _database, _path, *, county, **_kwargs):
            if county == "Middlesex":
                raise KeyboardInterrupt("simulated operator interruption")

    class FakeDatabase:
        def rollback(self):
            pass

        def close(self):
            pass

    snapshots = []

    def capture_manifest(_path, payload):
        snapshots.append(json.loads(json.dumps(payload)))

    monkeypatch.setattr(crash_ingestion, "NJDOTCrashIngester", FakeIngester)
    monkeypatch.setattr(crash_ingestion, "_atomic_json", capture_manifest)

    with pytest.raises(KeyboardInterrupt, match="simulated operator interruption"):
        crash_ingestion.run_ingestion(crash_run_args(tmp_path), database_factory=FakeDatabase)

    assert snapshots[0]["status"] == "running"
    assert [item["status"] for item in snapshots[0]["archives"]] == ["pending", "pending"]
    assert snapshots[0]["totals"]["status_counts"] == {"pending": 2}

    interrupted = snapshots[-1]
    assert interrupted["status"] == "running"
    assert [item["status"] for item in interrupted["archives"]] == [
        "succeeded",
        "running",
    ]
    assert interrupted["totals"]["status_counts"] == {"running": 1, "succeeded": 1}


def test_atomic_manifest_write_retries_a_transient_windows_permission_error(
    tmp_path, monkeypatch
):
    target = tmp_path / "report.json"
    target.write_text('{"status":"old"}\n', encoding="utf-8")
    real_replace = os.replace
    attempts = 0
    delays = []

    def transient_replace(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError(5, "target is temporarily open")
        return real_replace(source, destination)

    monkeypatch.setattr(crash_ingestion.os, "replace", transient_replace)
    monkeypatch.setattr(time, "sleep", delays.append)

    crash_ingestion._atomic_json(target, {"status": "new"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "new"}
    assert attempts == 2
    assert delays == [0.05]


def test_atomic_manifest_write_preserves_old_target_after_permanent_permission_error(
    tmp_path, monkeypatch
):
    target = tmp_path / "report.json"
    old_bytes = b'{"status":"old"}\n'
    target.write_bytes(old_bytes)
    attempts = 0
    delays = []

    def denied_replace(_source, _destination):
        nonlocal attempts
        attempts += 1
        raise PermissionError(5, "target remains open")

    monkeypatch.setattr(crash_ingestion.os, "replace", denied_replace)
    monkeypatch.setattr(time, "sleep", delays.append)

    with pytest.raises(PermissionError, match="target remains open"):
        crash_ingestion._atomic_json(target, {"status": "new"})

    assert attempts == 5
    assert delays == [0.05, 0.1, 0.2, 0.4]
    assert target.read_bytes() == old_bytes


def test_postgis_route_lookup_uses_calibrated_vertex_measure_not_endpoint_fraction():
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
                CREATE TEMP TABLE municipalities (
                    muni_id integer PRIMARY KEY,
                    geom geometry(MultiPolygon, 4326) NOT NULL
                ) ON COMMIT DROP;
                CREATE TEMP TABLE road_routes (
                    source_id varchar(100) PRIMARY KEY,
                    sri varchar(20),
                    mp_start double precision,
                    mp_end double precision,
                    geom geometry(LineStringM, 4326) NOT NULL
                ) ON COMMIT DROP;
                INSERT INTO municipalities VALUES (
                    17,
                    ST_GeomFromText(
                        'MULTIPOLYGON(((-75 40,-74 40,-74 41,-75 41,-75 40)))', 4326
                    )
                );
                INSERT INTO road_routes VALUES (
                    'route-a', '0001', 0, 10,
                    ST_GeomFromEWKT('SRID=4326;LINESTRINGM(-74.9 40.2 0,-74.8 40.2 9,-74.1 40.2 10)')
                );
                """
            )
        )

        located = locate_route_point(database, muni_id=17, sri="0001", milepost=9.0)

        assert located.reason is None
        assert located.point == pytest.approx((-74.8, 40.2))
    finally:
        database.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_postgis_batch_ingestion_prefers_contained_reported_points_and_is_idempotent(tmp_path):
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    archive = tmp_path / "Mercer2022Accidents.zip"
    rows = [
        make_row(id="phase2-reported", latitude="40.30", longitude="74.70"),
        make_row(id="phase2-route", latitude="", longitude="", milepost="9"),
        make_row(id="phase2-outside", latitude="40.30", longitude="73.95", milepost="9"),
        make_row(id="phase2-unresolved", latitude="", longitude="", sri_std_rte_identifier=""),
        make_row(id="phase2-reported", latitude="40.30", longitude="74.70"),
    ]
    write_archive(archive, rows)

    engine = create_engine(Settings(database_url=database_url, _env_file=None).database_url)
    connection = engine.connect()
    transaction = connection.begin()
    database = Session(bind=connection)
    try:
        connection.execute(text("""
            INSERT INTO municipalities (muni_id, name, county, muni_code, geom)
            VALUES (
                -92301, 'West Windsor Township', 'Mercer', 'phase2-crash-test',
                ST_GeomFromText(
                    'MULTIPOLYGON(((-75 40,-74 40,-74 41,-75 41,-75 40)))', 4326
                )
            );
            INSERT INTO road_routes (source_id, sri, mp_start, mp_end, geom)
            VALUES (
                'phase2-crash-route', '00000001__', 0, 10,
                ST_GeomFromEWKT(
                    'SRID=4326;LINESTRINGM(-74.9 40.2 0,-74.8 40.2 9,-74.1 40.2 10)'
                )
            );
        """))
        ingester = NJDOTCrashIngester(cache_dir=tmp_path, batch_size=20)
        first = ArchiveReport("Mercer", 2022, ingester.source_url("Mercer", 2022))

        ingester.ingest_archive(database, archive, county="Mercer", year=2022, report=first)

        loaded = connection.execute(text("""
            SELECT external_id, geocode_quality, ST_X(geom) AS longitude
            FROM crashes
            WHERE external_id LIKE 'NJDOT:2022:MERCER:phase2-%'
            ORDER BY external_id
        """)).mappings().all()
        assert first.records == 5
        assert first.loaded == 3
        assert first.duplicates == 1
        assert first.rejections == {"route_reference_missing": 1}
        assert first.location_methods == {"reported": 2, "route_milepost": 2}
        assert [(row["geocode_quality"], row["longitude"]) for row in loaded] == [
            ("route_milepost", pytest.approx(-74.8)),
            ("reported", pytest.approx(-74.7)),
            ("route_milepost", pytest.approx(-74.8)),
        ]

        second = ArchiveReport("Mercer", 2022, ingester.source_url("Mercer", 2022))
        ingester.ingest_archive(database, archive, county="Mercer", year=2022, report=second)
        assert second.loaded == 0
        assert second.duplicates == 4
    finally:
        database.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_enrichment_updates_official_semantics_without_regeocoding_existing_crash(
    tmp_path,
):
    database_url = os.getenv("DATA_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("DATA_TEST_DATABASE_URL is not configured")

    archive = tmp_path / "Mercer2022Accidents.zip"
    write_archive(
        archive,
        [
            make_row(
                id="enrichment-existing",
                total_killed="1",
                total_injured="2",
                pedestrians_killed="0",
                pedestrians_injured="1",
            ),
            make_row(id="enrichment-not-loaded"),
        ],
    )
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
                    -93821, 'Enrichment Test', 'Mercer', 'enrichment-test',
                    ST_GeomFromText(
                        'MULTIPOLYGON(((-75 40,-74 40,-74 41,-75 41,-75 40)))',
                        4326
                    )
                );
                INSERT INTO crashes (
                    crash_id, external_id, crash_date, severity, ped_involved,
                    bike_involved, muni_id, geom, geocode_quality
                ) VALUES (
                    -93822, 'NJDOT:2022:MERCER:enrichment-existing',
                    '2022-03-14', 'minor_injury', true, false, -93821,
                    ST_GeomFromText('POINT(-74.55 40.55)', 4326), 'reported'
                );
                """
            )
        )
        ingester = NJDOTCrashIngester(cache_dir=tmp_path, batch_size=20)
        report = ArchiveReport("Mercer", 2022, ingester.source_url("Mercer", 2022))

        ingester.enrich_archive(
            database,
            archive,
            county="Mercer",
            year=2022,
            report=report,
        )

        row = connection.execute(
            text(
                """
                SELECT severity, bike_involved, total_killed, total_injured,
                       pedestrians_killed, pedestrians_injured,
                       geocode_quality, ST_AsText(geom) AS geometry
                FROM crashes
                WHERE external_id = 'NJDOT:2022:MERCER:enrichment-existing'
                """
            )
        ).mappings().one()
        assert dict(row) == {
            "severity": "injury_unknown",
            "bike_involved": None,
            "total_killed": 1,
            "total_injured": 2,
            "pedestrians_killed": 0,
            "pedestrians_injured": 1,
            "geocode_quality": "reported",
            "geometry": "POINT(-74.55 40.55)",
        }
        assert report.records == 2
        assert report.updated == 1
        assert report.rejections == {"existing_crash_missing": 1}
    finally:
        database.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
