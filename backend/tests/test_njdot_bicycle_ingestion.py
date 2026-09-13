import importlib
import importlib.util
import json
import os
import zipfile

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from test_njdot_crash_ingestion import write_archive, FakeDownloadResponse, FakeDownloadSession


def module():
    return importlib.import_module("scripts.ingest_njdot_bicycles")


def person(source="202211010001", flag="Y", number="31"):
    row = [""] * 35
    row[0], row[1], row[33] = source, number, flag
    return row


def evidence(tmp_path, rows=None, county="Mercer", year=2022):
    path = tmp_path / f"{county}{year}Pedestrians.zip"
    write_archive(path, rows if rows is not None else [person()])
    return module().read_archive(path, county=county, year=year)


def test_bicycle_import_entrypoint_exists():
    assert importlib.util.find_spec("scripts.ingest_njdot_bicycles") is not None


def test_parser_counts_only_explicit_positives_and_deduplicates_crashes(tmp_path):
    result = evidence(tmp_path, [person(), person(number="32"), person(flag=""),
                                 person("202211010002", " "), person("202211010003", "N")])
    assert result.external_ids == {"NJDOT:2022:MERCER:202211010001"}
    assert result.report["source_rows"] == 5
    assert result.report["source_person_rows"] == 2
    assert result.report["source_crashes"] == 1


@pytest.mark.parametrize("row,reason", [
    (person()[:-1], "35"), (person("202111010001"), "year"),
    (person("202212010001"), "county"), (person("202211AA0001"), "municipality"),
    (person("20221101"), "case"), (person(flag="?"), "flag"),
])
def test_archive_rejects_invalid_rows_including_unflagged_records(tmp_path, row, reason):
    with pytest.raises(ValueError, match=reason):
        evidence(tmp_path, [person(), row])


def test_archive_rejects_wrong_member_and_corrupt_zip(tmp_path):
    path = tmp_path / "Mercer2022Pedestrians.zip"
    write_archive(path, [person()], member="Middlesex2022Pedestrians.txt")
    with pytest.raises(ValueError, match="member"):
        module().read_archive(path, county="Mercer", year=2022)


def test_official_bundle_allows_companion_zips_but_not_extra_text(tmp_path):
    evidence(tmp_path)
    path = tmp_path / "Mercer2022Pedestrians.zip"
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("Mercer2022Occupants.zip", b"companion bytes are not extracted")
    assert module().read_archive(path, county="Mercer", year=2022).report["source_crashes"] == 1
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("Other2022Pedestrians.txt", "unexpected")
    with pytest.raises(ValueError, match="member"):
        module().read_archive(path, county="Mercer", year=2022)
    path.write_bytes(b"not a ZIP")
    with pytest.raises(ValueError, match="ZIP"):
        module().read_archive(path, county="Mercer", year=2022)


def test_downloader_retries_validates_and_reuses_offline_cache(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    evidence(source)
    payload = (source / "Mercer2022Pedestrians.zip").read_bytes()
    session = FakeDownloadSession([FakeDownloadResponse(b"bad"), FakeDownloadResponse(payload)])
    fetcher = module().BicycleIngester(cache_dir=tmp_path / "cache", session=session, retries=2)
    path = fetcher.fetch_archive("Mercer", 2022)
    assert path.read_bytes() == payload
    assert len(session.calls) == 2
    offline = module().BicycleIngester(cache_dir=path.parent, offline=True)
    assert offline.fetch_archive("Mercer", 2022) == path
    with pytest.raises(FileNotFoundError, match="offline"):
        offline.fetch_archive("Mercer", 2021)


def test_invalid_later_archive_prevents_any_database_connection(tmp_path):
    evidence(tmp_path)
    evidence(tmp_path, [person("202212010001")], county="Middlesex")
    # Rewrite the second archive after preparing it so validation fails on the CLI path.
    write_archive(tmp_path / "Middlesex2022Pedestrians.zip", [person()[:-1]])
    args = module().build_parser().parse_args([
        "--start-year", "2022", "--end-year", "2022", "--county", "Mercer",
        "--county", "Middlesex", "--offline", "--cache-dir", str(tmp_path),
        "--report", str(tmp_path / "report.json")])
    def no_database():
        pytest.fail("database opened before all archives were validated")
    assert module().run_ingestion(args, database_factory=no_database) == 1
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["status"] == "failed"
    assert report["totals"]["updated"] == 0


def test_dry_run_validates_without_opening_database(tmp_path):
    evidence(tmp_path)
    args = module().build_parser().parse_args([
        "--start-year", "2022", "--end-year", "2022", "--county", "Mercer",
        "--offline", "--dry-run", "--cache-dir", str(tmp_path), "--report", str(tmp_path / "report.json")])
    def no_database():
        pytest.fail("dry run opened the database")
    assert module().run_ingestion(args, database_factory=no_database) == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["status"] == "validated"
    assert report["totals"]["source_crashes"] == 1
    assert report["totals"]["updated"] == 0


@pytest.fixture
def sqlite_db():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE municipalities (muni_id integer PRIMARY KEY, county text)"))
        connection.execute(text("CREATE TABLE crashes (external_id text PRIMARY KEY, bike_involved boolean, crash_date date, muni_id integer, geom text, severity text)"))
        connection.execute(text("INSERT INTO municipalities VALUES (1,'Mercer'),(2,'Middlesex')"))
        for index, bike in enumerate([None, False, True, None, None, None], 1):
            connection.execute(text("INSERT INTO crashes VALUES (:id,:bike,:date,:muni,'unchanged','fatal')"),
                               {"id":f"NJDOT:2022:MERCER:20221101000{index}", "bike":bike,
                                "date":"2021-01-01" if index == 5 else "2022-01-01", "muni":2 if index == 6 else 1})
    with Session(engine) as database:
        yield database
    engine.dispose()


def test_enrichment_changes_only_confirmed_matches_and_is_idempotent(tmp_path, sqlite_db):
    rows = [person(f"20221101000{i}") for i in [1,2,3,5,6,7]] + [person("202211010004", "")]
    result = evidence(tmp_path, rows)
    module().apply_evidence(sqlite_db, [result])
    assert {key:result.report[key] for key in ["matched","updated","already_known","unmatched","conflicting_false"]} == {
        "matched":3,"updated":2,"already_known":1,"unmatched":3,"conflicting_false":1}
    after = sqlite_db.execute(text("SELECT bike_involved,geom,severity FROM crashes ORDER BY external_id")).all()
    assert [row[0] for row in after] == [1,1,1,None,None,None]
    assert all(row[1:] == ("unchanged","fatal") for row in after)
    result = evidence(tmp_path, rows)
    module().apply_evidence(sqlite_db, [result])
    assert result.report["updated"] == 0
    assert result.report["already_known"] == 3


def test_verification_rejects_database_not_retaining_confirmation(tmp_path, sqlite_db):
    sqlite_db.execute(text("CREATE TRIGGER undo_bicycle AFTER UPDATE ON crashes BEGIN UPDATE crashes SET bike_involved=0 WHERE external_id=NEW.external_id; END"))
    with pytest.raises(RuntimeError, match="verification"):
        module().apply_evidence(sqlite_db, [evidence(tmp_path)])


def test_postgis_bicycle_update_preserves_geometry_and_revision_on_rerun(tmp_path):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    engine = create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        database = Session(bind=connection)
        try:
            connection.execute(text("INSERT INTO municipalities (muni_id,name,county,muni_code,geom) VALUES (-94901,'Bicycle Test','Mercer','bicycle-test',ST_GeomFromText('MULTIPOLYGON(((-75 40,-74 40,-74 41,-75 41,-75 40)))',4326))"))
            connection.execute(text("INSERT INTO crashes (external_id,crash_date,severity,muni_id,geom) VALUES ('NJDOT:2022:MERCER:202211010001','2022-01-01','fatal',-94901,ST_GeomFromText('POINT(-74.5 40.5)',4326))"))
            revision = lambda: connection.scalar(text("SELECT revision FROM dataset_revisions WHERE dataset='crashes'"))
            before = revision()
            first = evidence(tmp_path)
            module().apply_evidence(database, [first])
            assert first.report["updated"] == 1
            assert revision() > before
            after = revision()
            module().apply_evidence(database, [evidence(tmp_path)])
            assert revision() == after
            assert connection.scalar(text("SELECT ST_AsText(geom) FROM crashes WHERE external_id='NJDOT:2022:MERCER:202211010001'")) == "POINT(-74.5 40.5)"
            with pytest.raises(RuntimeError):
                with database.begin_nested():
                    database.execute(text("UPDATE crashes SET bike_involved=NULL WHERE external_id='NJDOT:2022:MERCER:202211010001'"))
                    module().apply_evidence(database, [evidence(tmp_path)])
                    raise RuntimeError("rollback")
            assert revision() == after
        finally:
            database.close()
            transaction.rollback()
    engine.dispose()


@pytest.mark.parametrize("reject_second", [False, True])
def test_postgis_pipeline_commits_or_rolls_back_all_archives(tmp_path, reject_second):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    evidence(tmp_path)
    evidence(tmp_path, [person("202212010001")], county="Middlesex")
    args = module().build_parser().parse_args([
        "--start-year", "2022", "--end-year", "2022", "--county", "Mercer",
        "--county", "Middlesex", "--offline", "--cache-dir", str(tmp_path),
        "--report", str(tmp_path / "report.json")])
    engine = create_engine(url)
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            for muni_id, county, source in [(-94911,"Mercer","202211010001"), (-94912,"Middlesex","202212010001")]:
                connection.execute(text("INSERT INTO municipalities (muni_id,name,county,muni_code,geom) VALUES (:id,:county,:county,:code,ST_GeomFromText('MULTIPOLYGON(((-75 40,-74 40,-74 41,-75 41,-75 40)))',4326))"),
                                   dict(id=muni_id,county=county,code=f"bicycle-{muni_id}"))
                connection.execute(text("INSERT INTO crashes (external_id,crash_date,severity,muni_id,geom) VALUES (:id,'2022-01-01','fatal',:muni,ST_GeomFromText('POINT(-74.5 40.5)',4326))"),
                                   dict(id=f"NJDOT:2022:{county.upper()}:{source}",muni=muni_id))
            if reject_second:
                connection.execute(text("""
                    CREATE FUNCTION pg_temp.reject_bicycle_test() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN
                        IF NEW.external_id='NJDOT:2022:MIDDLESEX:202212010001' THEN
                            RAISE EXCEPTION 'forced second-archive failure';
                        END IF;
                        RETURN NEW;
                    END $$;
                    CREATE TRIGGER reject_bicycle_test BEFORE UPDATE ON crashes
                    FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_bicycle_test();
                """))
            revision = lambda: connection.scalar(text("SELECT revision FROM dataset_revisions WHERE dataset='crashes'"))
            before = revision()
            factory = lambda: Session(bind=connection, join_transaction_mode="create_savepoint")
            status = module().run_ingestion(args, database_factory=factory)
            report = json.loads((tmp_path / "report.json").read_text())
            assert status == (1 if reject_second else 0)
            flags = connection.execute(text("SELECT bike_involved FROM crashes WHERE muni_id IN (-94911,-94912)")).scalars().all()
            if reject_second:
                assert flags == [None, None]
                assert report["status"] == "failed"
                assert report["totals"]["updated"] == 0
                assert revision() == before
            else:
                assert flags == [True, True]
                assert report["totals"]["updated"] == 2
                assert report["verification"]["non_bicycle_fields_unchanged"] is True
                assert report["verification"]["crash_count_before"] == report["verification"]["crash_count_after"]
                after = revision()
                assert after > before
                assert module().run_ingestion(args, database_factory=factory) == 0
                assert revision() == after
                assert json.loads((tmp_path / "report.json").read_text())["totals"]["updated"] == 0
        finally:
            outer.rollback()
    engine.dispose()
