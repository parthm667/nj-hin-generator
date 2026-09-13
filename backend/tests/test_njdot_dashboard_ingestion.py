"""Dashboard import must preserve identity and refuse ambiguous source evidence."""
import csv
import json
import sqlite3
import os
import subprocess
import sys
from pathlib import Path
from argparse import Namespace
from contextlib import closing, contextmanager

import pytest
from sqlalchemy import create_engine, text

from scripts import ingest_njdot_dashboard as dashboard


def source_row(**changes):
    row = {
        'id_cr': '42', 'casenumber': 'AB-001', 'County': 'Mercer',
        'Municipality': 'Princeton, Mercer', 'dateofcrash': '2022-04-05',
        'Date & Time of Crash': '2022-04-05T09:15:00.000', 'Year': '2022',
        'Highest Injury Severity Rating': 'Suspected Serious Injury (A)',
        'Geopoint': '{"lon":-74.65,"lat":40.35}',
        'Geopoint (Calculated)': '{"lon":-74.66,"lat":40.36}',
        'latitude': '40.35', 'longitude': '-74.65',
        'NJDOT Summary': '["Pedestrian Involved", "Total Crashes"]',
        'fatalitycount': '0', 'crashfatalitycount': '0', 'injurycount': '1',
        'pedestrianfatalitycount': '0', 'pedestrianinjurycount': '1',
        'fatalcrashind': 'N', 'agencyori': 'NJ0110900',
        'njdot_dln': '11142022-AB-001A', 'streetname': 'NASSAU ST',
        'streetsri': '00000027__', 'Weather Condition': 'Clear',
    }
    row.update(changes)
    return row


def municipalities():
    return dashboard.municipality_lookup([
        {'muni_id': 99, 'muni_code': '1114', 'name': 'Princeton', 'county': 'Mercer'},
    ])


def prepare(**changes):
    return dashboard.prepare_record(source_row(**changes), municipalities())


def test_historical_identity_uses_official_municipality_not_police_ori():
    record = prepare()
    assert record['external_id'] == 'NJDOT:2022:MERCER:20221114AB-001'
    assert record['muni_id'] == 99
    assert record['crash_time'] == '09:15'
    assert record['severity'] == 'serious_injury'


@pytest.mark.parametrize(('rating', 'expected'), [
    ('Fatal Injury (K)', 'fatal'),
    ('Suspected Serious Injury (A)', 'serious_injury'),
    ('Suspected Minor Injury (B)', 'minor_injury'),
    ('Possible Injury (C)', 'possible_injury'),
    ('No Apparent Injury (O)', 'property_damage'),
])
def test_rating_keeps_distinct_kabco_categories(rating, expected):
    record = prepare(**{'Highest Injury Severity Rating': rating, 'injurycount': '0',
                        'pedestrianinjurycount': '0'})
    assert record['severity'] == expected


def test_no_apparent_injury_with_injured_people_is_not_property_damage():
    record = prepare(**{'Highest Injury Severity Rating': 'No Apparent Injury (O)'})
    assert record['severity'] == 'injury_unknown'
    assert 'rating_o_with_injury_evidence' in record['source_metadata']['conflicts']
    assert record['source_metadata']['raw']['Highest Injury Severity Rating'] == 'No Apparent Injury (O)'


def test_null_people_counts_are_not_filled_from_crash_fatality_count():
    record = prepare(fatalitycount='', crashfatalitycount='1', injurycount='',
                     pedestrianinjurycount='', pedestrianfatalitycount='')
    assert record['total_killed'] is None
    assert record['total_injured'] is None
    assert record['source_metadata']['raw']['crashfatalitycount'] == '1'


def test_unqualified_fatality_count_does_not_assert_fatal_crash():
    record = prepare(**{'Highest Injury Severity Rating': 'No Apparent Injury (O)',
                        'fatalitycount': '1', 'injurycount': '0', 'pedestrianinjurycount': '0'})
    assert record['severity'] == 'injury_unknown'
    assert record['total_killed'] == 1
    assert 'rating_o_with_unqualified_fatality_count' in record['source_metadata']['conflicts']


def test_plain_single_summary_label_is_supported():
    assert prepare(**{'NJDOT Summary': 'Total Crashes'})['ped_involved'] is True


def test_bicycle_summary_preserves_positive_evidence():
    assert prepare(**{'NJDOT Summary': '["Bicyclist Involved"]'})['bike_involved'] is True


def test_reconciliation_does_not_insert_unmatched_historical_crash():
    record = prepare()
    outcome, values = dashboard.reconcile_record(record, {'existing': None, 'reported_valid': True,
                                                        'calculated_valid': True})
    assert outcome == 'historical_identity_unmatched'
    assert values is None


def test_reconciliation_rejects_date_conflict():
    record = prepare()
    outcome, _ = dashboard.reconcile_record(record, {'existing': {
        'crash_id': 8, 'crash_date': '2022-04-06', 'muni_id': 99, 'dashboard_id': None,
    }})
    assert outcome == 'existing_date_or_municipality_conflict'


def test_reconciliation_preserves_identity_and_bicycle_evidence_and_clears_changed_snap():
    record = prepare()
    outcome, values = dashboard.reconcile_record(record, {
        'reported_valid': True, 'calculated_valid': True, 'existing_valid': True,
        'existing': {'crash_id': 8, 'external_id': record['external_id'], 'crash_date': '2022-04-05',
                     'muni_id': 99, 'dashboard_id': None, 'bike_involved': True,
                     'longitude': -74.7, 'latitude': 40.3, 'segment_id': 4, 'snap_distance': 3},
    })
    assert outcome == 'updated'
    assert values['crash_id'] == 8
    assert values['external_id'] == 'NJDOT:2022:MERCER:20221114AB-001'
    assert values['bike_involved'] is True
    assert values['segment_id'] is None
    assert values['snap_distance'] is None
    assert values['geocode_quality'] == 'dashboard_current'


def test_reconciliation_falls_back_to_calculated_then_existing_valid_geometry():
    record = prepare()
    record['year'] = 2023
    record['crash_date'] = '2023-04-05'
    outcome, values = dashboard.reconcile_record(record, {'existing': None, 'reported_valid': False,
                                                        'calculated_valid': True})
    assert outcome == 'inserted'
    assert values['longitude'] == -74.66
    assert values['geocode_quality'] == 'dashboard_calculated'
    existing = dict(values, crash_id=9, segment_id=3, snap_distance=2, severity='property_damage')
    outcome, preserved = dashboard.reconcile_record(record, {'existing': existing,
        'reported_valid': False, 'calculated_valid': False, 'existing_valid': True})
    assert preserved['segment_id'] == 3
    assert preserved['longitude'] == -74.66


def test_identical_reimport_has_no_values_to_write_even_with_new_retrieval_timestamp():
    record = prepare()
    record['year'] = 2023
    record['crash_date'] = '2023-04-05'
    _, inserted = dashboard.reconcile_record(record, {'existing': None, 'reported_valid': True})
    existing = dict(inserted, crash_id=19)
    record['source_metadata']['provenance'] = {'retrieved_at': 'different time'}
    outcome, values = dashboard.reconcile_record(record, {'existing': existing, 'reported_valid': True})
    assert outcome == 'unchanged'
    assert values is None


def test_uncertain_bicycle_flag_preserves_known_false():
    record = prepare()
    _, values = dashboard.reconcile_record(record, {'reported_valid': True, 'existing': {
        'crash_id': 8, 'external_id': record['external_id'], 'crash_date': '2022-04-05',
        'muni_id': 99, 'bike_involved': False}})
    assert values['bike_involved'] is False


def test_new_historical_insert_requires_explicit_option():
    record = prepare()
    outcome, values = dashboard.reconcile_record(record, {'existing': None, 'reported_valid': True},
                                                allow_new_historical=True)
    assert outcome == 'inserted'
    assert values['external_id'] == 'NJDOT:2022:MERCER:20221114AB-001'


def test_unknown_source_overlap_is_quarantined_even_when_historical_insert_enabled():
    outcome, values = dashboard.reconcile_record(prepare(), {'existing': None,
        'reported_valid': True, 'unknown_source_overlap': True}, allow_new_historical=True)
    assert outcome == 'unknown_source_overlap'
    assert values is None


def test_metadata_whitelist_excludes_personal_and_narrative_fields():
    record = prepare(drivername='PRIVATE', narrative='PRIVATE', thirdpartydln='PRIVATE')
    assert 'PRIVATE' not in json.dumps(record)
    assert record['source_metadata']['raw']['njdot_dln'] == '11142022-AB-001A'
    assert record['bike_involved'] is None


def test_reported_and_calculated_locations_are_kept_distinct():
    record = prepare()
    assert record['reported_point'] == [-74.65, 40.35]
    assert record['calculated_point'] == [-74.66, 40.36]
    record = prepare(Geopoint='{"lon":100,"lat":40}', latitude='', longitude='')
    assert record['reported_point'] is None
    assert record['calculated_point'] == [-74.66, 40.36]


@pytest.mark.parametrize('changes', [
    {'Year': '2025'}, {'dateofcrash': '2026-01-01', 'Year': '2026'},
    {'Municipality': 'Princeton, Middlesex'}, {'injurycount': '-1'},
    {'County': 'Unknown'}, {'id_cr': ''},
])
def test_invalid_or_out_of_scope_records_are_rejected(changes):
    with pytest.raises(ValueError):
        prepare(**changes)


def write_csv(path, rows):
    headers = list(dict.fromkeys([*dashboard.REQUIRED_COLUMNS, *source_row()]))
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def test_whole_file_validation_quarantines_every_conflicting_duplicate(tmp_path):
    path = tmp_path / 'crashes.csv'
    write_csv(path, [source_row(), source_row(injurycount='2'), source_row(id_cr='43', casenumber='other')])
    stage = tmp_path / 'stage.sqlite'
    report = dashboard.stage_csv(path, stage, municipalities())
    assert report['input_rows'] == 3
    assert report['eligible_rows'] == 1
    assert report['quarantined_rows'] == 2
    assert len(report['sha256']) == 64
    with closing(sqlite3.connect(stage)) as db, db:
        assert db.execute('SELECT dashboard_id FROM records WHERE issue IS NULL').fetchall() == [('43',)]


def test_distinct_dashboard_ids_targeting_same_historical_crash_are_quarantined(tmp_path):
    path = tmp_path / 'crashes.csv'
    write_csv(path, [source_row(), source_row(id_cr='43')])
    report = dashboard.stage_csv(path, tmp_path / 'stage.sqlite', municipalities())
    assert report['eligible_rows'] == 0
    assert report['quarantined_rows'] == 2


def test_conflicting_duplicate_group_does_not_double_count_exact_duplicates(tmp_path):
    path = tmp_path / 'crashes.csv'
    write_csv(path, [source_row(), source_row(), source_row(injurycount='2')])
    report = dashboard.stage_csv(path, tmp_path / 'stage.sqlite', municipalities())
    assert report['quarantined_rows'] == 3
    assert report['duplicates'] == 0


def test_cache_rebuilds_after_parser_change_and_preserves_previous_audit(tmp_path):
    path = tmp_path / 'crashes.csv'
    stage_path = tmp_path / 'stage.sqlite'
    write_csv(path, [source_row()])
    args = Namespace(source_url='https://example.test/official-export', retrieved_at='2026-09-13')
    first = dashboard._validated_stage(path, stage_path, municipalities(), args)
    first['parser_version'] = -1
    with closing(sqlite3.connect(stage_path)) as stage, stage:
        stage.execute('UPDATE manifest SET payload=?', (json.dumps(first),))
    rebuilt = dashboard._validated_stage(path, stage_path, municipalities(), args)
    assert rebuilt['eligible_rows'] == 1
    assert len(list(tmp_path.glob('stage.sqlite.*.previous'))) == 1
    assert not list(tmp_path.glob('*.building'))


def test_incomplete_cache_is_recovered_and_never_used_for_mutation(tmp_path):
    path = tmp_path / 'crashes.csv'
    stage_path = tmp_path / 'stage.sqlite'
    write_csv(path, [source_row()])
    with closing(sqlite3.connect(stage_path)) as stage, stage:
        stage.execute('CREATE TABLE partial (id INTEGER)')
    report = dashboard._validated_stage(path, stage_path, municipalities(), Namespace(source_url=None, retrieved_at=None))
    assert report['eligible_rows'] == 1
    with closing(sqlite3.connect(stage_path)) as stage, stage:
        assert stage.execute('SELECT count(*) FROM records').fetchone()[0] == 1


def test_reconciliation_refuses_out_of_scope_staged_date_before_database_access(tmp_path):
    path = tmp_path / 'crashes.csv'
    stage_path = tmp_path / 'stage.sqlite'
    write_csv(path, [source_row()])
    dashboard.stage_csv(path, stage_path, municipalities())
    with closing(sqlite3.connect(stage_path)) as stage, stage:
        payload = json.loads(stage.execute('SELECT payload FROM records').fetchone()[0])
        payload['crash_date'] = '2026-01-01'
        stage.execute('UPDATE records SET payload=?', (json.dumps(payload),))
    with pytest.raises(ValueError, match='out_of_scope_staged_record'):
        dashboard.process_stage(None, stage_path, apply=True)


def test_late_malformed_csv_aborts_full_validation(tmp_path):
    path = tmp_path / 'crashes.csv'
    write_csv(path, [source_row()])
    with path.open('a') as stream:
        stream.write('too,few,columns\n')
    with pytest.raises(ValueError, match='columns'):
        dashboard.stage_csv(path, tmp_path / 'stage.sqlite', municipalities())


@pytest.fixture
def dashboard_db():
    url = os.getenv('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL is not configured')
    engine = create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text('''INSERT INTO municipalities (muni_id,name,county,muni_code,geom)
            VALUES (-94234,'Dashboard Test','Mercer','9942',ST_GeomFromText(
            'MULTIPOLYGON(((-75 40,-74 40,-74 41,-75 41,-75 40)))',4326))'''))
        try:
            yield connection
        finally:
            transaction.rollback()
    engine.dispose()


def database_stage(tmp_path, rows):
    path = tmp_path / 'crashes.csv'
    write_csv(path, [dict(row, Municipality='Dashboard Test, Mercer') for row in rows])
    lookup = dashboard.municipality_lookup([{'muni_id': -94234, 'muni_code': '9942',
                                            'name': 'Dashboard Test', 'county': 'Mercer'}])
    stage = tmp_path / 'stage.sqlite'
    dashboard.stage_csv(path, stage, lookup)
    return stage


def test_postgis_dry_run_then_atomic_apply_and_idempotent_reimport(dashboard_db, tmp_path):
    conn = dashboard_db
    stage = database_stage(tmp_path, [source_row(id_cr='99420001', Year='2023',
        dateofcrash='2023-04-05', **{'Date & Time of Crash': '2023-04-05T09:15:00'})])
    report = dashboard.process_stage(conn, stage, apply=False, batch_size=2)
    assert report['outcomes'] == {'inserted': 1}
    assert conn.scalar(text("SELECT count(*) FROM crashes WHERE dashboard_id='99420001'")) == 0
    dashboard.process_stage(conn, stage, apply=True, batch_size=2)
    row = conn.execute(text("SELECT crash_id,severity,geocode_quality FROM crashes WHERE dashboard_id='99420001'")).mappings().one()
    assert row['severity'] == 'serious_injury'
    assert row['geocode_quality'] == 'dashboard_current'
    version = conn.scalar(text("SELECT revision FROM dataset_revisions WHERE dataset='crashes'"))
    report = dashboard.process_stage(conn, stage, apply=True, batch_size=2)
    assert report['outcomes'] == {'unchanged': 1}
    assert conn.scalar(text("SELECT revision FROM dataset_revisions WHERE dataset='crashes'")) == version
    assert conn.scalar(text("SELECT crash_id FROM crashes WHERE dashboard_id='99420001'")) == row['crash_id']


def test_postgis_historical_enrichment_and_legacy_loader_source_guard(dashboard_db, tmp_path):
    from scripts.ingest_njdot_crashes import FIELD_NAMES, NJDOTCrashIngester, ArchiveReport, prepare_record as legacy_prepare
    from test_njdot_crash_ingestion import make_row
    from sqlalchemy.orm import Session
    conn = dashboard_db
    external_id = 'NJDOT:2022:MERCER:20229942AB-001'
    conn.execute(text('''INSERT INTO crashes(crash_id,external_id,crash_date,severity,bike_involved,muni_id,geom)
        VALUES (-94235,:id,'2022-04-05','injury_unknown',true,-94234,ST_GeomFromText('POINT(-74.7 40.3)',4326))'''), {'id': external_id})
    stage = database_stage(tmp_path, [source_row(id_cr='99420002')])
    report = dashboard.process_stage(conn, stage, apply=True)
    assert report['outcomes'] == {'updated': 1}
    row = conn.execute(text('SELECT crash_id,external_id,bike_involved,severity FROM crashes WHERE crash_id=-94235')).mappings().one()
    assert dict(row) == {'crash_id': -94235, 'external_id': external_id, 'bike_involved': True, 'severity': 'serious_injury'}
    legacy = legacy_prepare(dict(zip(FIELD_NAMES, make_row(id='20229942AB-001'))),
                            expected_county='Mercer', expected_year=2022)
    session = Session(bind=conn)
    try:
        NJDOTCrashIngester()._enrich_batch(session, [legacy], ArchiveReport('Mercer', 2022, 'test'))
        assert conn.scalar(text('SELECT severity FROM crashes WHERE crash_id=-94235')) == 'serious_injury'
        NJDOTCrashIngester()._load_batch(session, [(legacy, -94234)], ArchiveReport('Mercer', 2022, 'test'))
        assert conn.scalar(text('SELECT severity FROM crashes WHERE crash_id=-94235')) == 'serious_injury'
    finally:
        session.close()


def test_postgis_invalid_municipal_location_is_quarantined(dashboard_db, tmp_path):
    stage = database_stage(tmp_path, [source_row(id_cr='99420003', Year='2023',
        dateofcrash='2023-04-05', Geopoint='{"lon":-74.6,"lat":39.5}',
        **{'Date & Time of Crash': '2023-04-05T09:15:00', 'Geopoint (Calculated)': ''})])
    assert dashboard.process_stage(dashboard_db, stage, apply=True)['outcomes'] == {'location_unresolved': 1}


def test_postgis_later_batch_failure_rolls_back_prior_batch_and_revision(dashboard_db, tmp_path):
    conn = dashboard_db
    stage = database_stage(tmp_path, [source_row(id_cr=id_, casenumber=id_, Year='2023',
        dateofcrash='2023-04-05', **{'Date & Time of Crash': '2023-04-05T09:15:00'})
        for id_ in ('99420011', '99420012')])
    conn.execute(text('''CREATE FUNCTION pg_temp.reject_dashboard_test_row() RETURNS trigger LANGUAGE plpgsql
        AS $$ BEGIN IF NEW.dashboard_id='99420012' THEN RAISE EXCEPTION 'injected late failure';
        END IF; RETURN NEW; END $$;
        CREATE TRIGGER dashboard_test_failure BEFORE INSERT ON crashes FOR EACH ROW
        EXECUTE FUNCTION pg_temp.reject_dashboard_test_row()'''))
    version = conn.scalar(text("SELECT revision FROM dataset_revisions WHERE dataset='crashes'"))
    with pytest.raises(Exception, match='injected late failure'):
        with conn.begin_nested():
            dashboard.process_stage(conn, stage, apply=True, batch_size=1)
    assert conn.scalar(text("SELECT count(*) FROM crashes WHERE dashboard_id IN ('99420011','99420012')")) == 0
    assert conn.scalar(text("SELECT revision FROM dataset_revisions WHERE dataset='crashes'")) == version


class BorrowedEngine:
    """Keep end-to-end importer transactions inside the disposable test rollback."""
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def connect(self):
        yield self.connection

    @contextmanager
    def begin(self):
        with self.connection.begin_nested():
            yield self.connection


def import_args(tmp_path, **overrides):
    values = dict(input_csv=tmp_path / 'crashes.csv', report=tmp_path / 'report.json',
                  stage=tmp_path / 'stage.sqlite', source_url='https://example.test/export',
                  retrieved_at='2026-09-13T00:00:00Z', expected_count=1, batch_size=1,
                  apply=True, allow_new_historical=True)
    values.update(overrides)
    return Namespace(**values)


def test_postgis_run_import_validates_locks_commits_and_preserves_out_of_scope_years(dashboard_db, tmp_path):
    conn = dashboard_db
    conn.execute(text('''INSERT INTO crashes(crash_id,external_id,crash_date,severity,muni_id,geom)
        VALUES (-94236,'dashboard-test-untouched-2018','2018-01-01','property_damage',-94234,
        ST_GeomFromText('POINT(-74.7 40.3)',4326))'''))
    args = import_args(tmp_path)
    write_csv(args.input_csv, [source_row(id_cr='99420021', Municipality='Dashboard Test, Mercer')])
    report = dashboard.run_import(args, database_engine=BorrowedEngine(conn))
    assert report['status'] == 'applied'
    assert report['database_after']['total'] == report['database_before']['total'] + 1
    assert report['database_after']['years']['2018'] == report['database_before']['years']['2018']
    assert report['reconciliation_years']['2022']['inserted'] == 1
    assert conn.scalar(text("SELECT external_id FROM crashes WHERE dashboard_id='99420021'")) == 'NJDOT:2022:MERCER:20229942AB-001'
    assert json.loads(args.report.read_text())['status'] == 'applied'
    version = conn.scalar(text("SELECT revision FROM dataset_revisions WHERE dataset='crashes'"))
    rerun = dashboard.run_import(args, database_engine=BorrowedEngine(conn))
    assert rerun['outcomes'] == {'unchanged': 1}
    assert conn.scalar(text("SELECT revision FROM dataset_revisions WHERE dataset='crashes'")) == version


def test_postgis_expected_source_count_failure_never_mutates_crashes(dashboard_db, tmp_path):
    args = import_args(tmp_path, expected_count=2)
    write_csv(args.input_csv, [source_row(id_cr='99420022', Municipality='Dashboard Test, Mercer')])
    version = dashboard_db.scalar(text("SELECT revision FROM dataset_revisions WHERE dataset='crashes'"))
    with pytest.raises(ValueError, match='expected_count_mismatch'):
        dashboard.run_import(args, database_engine=BorrowedEngine(dashboard_db))
    assert dashboard_db.scalar(text("SELECT count(*) FROM crashes WHERE dashboard_id='99420022'")) == 0
    assert dashboard_db.scalar(text("SELECT revision FROM dataset_revisions WHERE dataset='crashes'")) == version
    report = json.loads(args.report.read_text())
    assert report['status'] == 'failed'
    assert report['database_committed'] is False


def test_cli_database_failure_omits_parameters_and_driver_secrets_from_log_and_report(tmp_path):
    report_path = tmp_path / 'report.json'
    script = '''
from sqlalchemy.exc import StatementError
from scripts import ingest_njdot_dashboard as dashboard
class DriverError(Exception):
    pgcode = '23505'
class FailingEngine:
    def connect(self):
        raise StatementError('DB_SECRET_SENTINEL', 'SQL_TEXT_SENTINEL',
                             {'records': 'RAW_ROW_SENTINEL'}, DriverError('DRIVER_SECRET_SENTINEL'))
run_import = dashboard.run_import
dashboard.run_import = lambda args: run_import(args, database_engine=FailingEngine())
raise SystemExit(dashboard.main())
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run([sys.executable, '-c', script, '--input-csv', str(tmp_path / 'input.csv'),
                             '--report', str(report_path)], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 1
    report = json.loads(report_path.read_text())
    combined = result.stdout + result.stderr + json.dumps(report)
    for sentinel in ('DB_SECRET_SENTINEL', 'SQL_TEXT_SENTINEL', 'RAW_ROW_SENTINEL', 'DRIVER_SECRET_SENTINEL'):
        assert sentinel not in combined
    assert 'Traceback' not in combined
    assert report['error'] == 'Database error: StatementError (SQLSTATE 23505)'
    assert report['error'] in result.stderr
    assert report['status'] == 'failed'
    assert report['database_committed'] is False


def test_failure_messages_preserve_known_validation_codes_but_not_arbitrary_exception_text():
    assert dashboard.failure_message(ValueError('no_eligible_records_after_validation')) == 'no_eligible_records_after_validation'
    assert dashboard.failure_message(ValueError('expected_count_mismatch: expected 4, observed 3')) == 'expected_count_mismatch: expected 4, observed 3'
    assert 'SECRET_ROW' not in dashboard.failure_message(RuntimeError('unexpected record SECRET_ROW'))
