"""Queue behavior against isolated, disposable PostgreSQL schemas."""

import importlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Timer
from types import SimpleNamespace
from functools import partial
import multiprocessing
import signal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.models.database import Base
from app.models.tables import Analysis


def test_durable_job_models_available():
    assert importlib.util.find_spec("app.models.jobs"), "Durable job models are missing"


@pytest.fixture
def jobs_engine():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    assert "test" in make_url(database_url).database.lower(), "Disposable test database required"
    schema = "jobs_test_" + uuid.uuid4().hex
    admin = create_engine(database_url)
    with admin.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(database_url, connect_args={"options": f"-csearch_path={schema},public"})
    try:
        # Import here so missing implementation is an explicit test failure.
        assert importlib.util.find_spec("app.models.jobs"), "Durable job models are missing"
        importlib.import_module("app.models.jobs")
        # A public application schema may already exist. Do not let SQLAlchemy's
        # search-path introspection reuse those tables in an isolated fixture.
        Base.metadata.create_all(engine, checkfirst=False)
        with engine.begin() as connection:
            from scripts.init_schema import install_versions
            connection.execute(text('CREATE TABLE dataset_revisions (dataset varchar(32) PRIMARY KEY, revision bigint NOT NULL DEFAULT 1)'))
            install_versions(connection)
            connection.execute(text("""
                INSERT INTO municipalities (muni_id, name, county, geom)
                VALUES (1, 'Queue test', 'Test', ST_GeomFromText(
                  'MULTIPOLYGON(((-75 40,-74 40,-74 41,-75 41,-75 40)))',4326))
            """))
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def new_analysis():
    return Analysis(muni_id=1, start_year=2022, end_year=2022,
                    years_included=[2022], snap_distance_meters=50,
                    significance_threshold=0.05, status="pending")


def enqueue(engine, client="a", **kwargs):
    from app.services.job_service import JobService
    with Session(engine) as db:
        analysis = new_analysis()
        job = JobService(db).enqueue(analysis, client, **kwargs)
        db.commit()
        return job.analysis_id


def test_enqueue_and_analysis_rollback_together(jobs_engine):
    from app.services.job_service import JobService
    with Session(jobs_engine) as db:
        JobService(db).enqueue(new_analysis(), "a")
        db.rollback()
    with jobs_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM analyses")) == 0
        assert connection.scalar(text("SELECT count(*) FROM analysis_jobs")) == 0


def test_client_quota_counts_running_and_pending(jobs_engine):
    from app.services.job_service import ClientQuotaError
    first = enqueue(jobs_engine)
    enqueue(jobs_engine)
    with jobs_engine.begin() as connection:
        connection.execute(text("UPDATE analysis_jobs SET status='running' WHERE analysis_id=:id"), {"id": first})
    with pytest.raises(ClientQuotaError):
        enqueue(jobs_engine)
    enqueue(jobs_engine, "b")
    with jobs_engine.begin() as connection:
        connection.execute(text("UPDATE analysis_jobs SET status='completed' WHERE analysis_id=:id"), {"id": first})
    enqueue(jobs_engine)


def test_global_queue_capacity_is_shared_and_atomic(jobs_engine):
    from app.services.job_service import QueueFullError
    def attempt(number):
        try:
            enqueue(jobs_engine, str(number), max_pending=2)
            return "accepted"
        except QueueFullError:
            return "full"
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(attempt, range(6)))
    assert results.count("accepted") == 2
    assert results.count("full") == 4
    with jobs_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM analyses")) == 2


def test_global_capacity_reserves_room_for_interrupted_running_job(jobs_engine):
    from app.services.job_service import QueueFullError
    first = enqueue(jobs_engine, "a", max_pending=2)
    with jobs_engine.begin() as connection:
        connection.execute(text("UPDATE analysis_jobs SET status='running' WHERE analysis_id=:id"), {"id": first})
    enqueue(jobs_engine, "b", max_pending=2)
    with pytest.raises(QueueFullError):
        enqueue(jobs_engine, "c", max_pending=2)


def test_client_quota_is_atomic_across_sessions(jobs_engine):
    from app.services.job_service import ClientQuotaError
    def attempt(_):
        try:
            enqueue(jobs_engine, max_pending=20)
            return True
        except ClientQuotaError:
            return False
    with ThreadPoolExecutor(max_workers=6) as executor:
        assert sum(executor.map(attempt, range(6))) == 2


def test_real_worker_completes_hin_and_releases_lock(jobs_engine):
    from app.worker import run_worker, WORKER_LOCK_NAMESPACE, WORKER_LOCK_KEY
    with jobs_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO crashes (crash_date,severity,muni_id,geom)
            VALUES ('2022-06-01','property_damage',1,ST_GeomFromText('POINT(-74.5 40.5)',4326))
        """))
    analysis_id = enqueue(jobs_engine)
    assert run_worker(jobs_engine, once=True) == 1
    with jobs_engine.connect() as connection:
        row = connection.execute(text("SELECT status,total_crashes FROM analyses WHERE analysis_id=:id"), {"id": analysis_id}).one()
        assert tuple(row) == ("completed", 1)
        assert connection.execute(text("SELECT status,attempts FROM analysis_jobs")).one() == ("completed", 1)
        assert connection.scalar(text("SELECT pg_try_advisory_lock(:ns,:key)"), {"ns": WORKER_LOCK_NAMESPACE, "key": WORKER_LOCK_KEY})
        connection.execute(text("SELECT pg_advisory_unlock(:ns,:key)"), {"ns": WORKER_LOCK_NAMESPACE, "key": WORKER_LOCK_KEY})


def test_busy_worker_does_not_recover_or_execute(jobs_engine):
    from app.worker import run_worker, WORKER_LOCK_NAMESPACE, WORKER_LOCK_KEY
    analysis_id = enqueue(jobs_engine)
    with jobs_engine.begin() as connection:
        connection.execute(text("UPDATE analysis_jobs SET status='running', attempts=1"))
    with jobs_engine.connect() as owner:
        owner.execute(text("SELECT pg_advisory_lock(:ns,:key)"), {"ns": WORKER_LOCK_NAMESPACE, "key": WORKER_LOCK_KEY})
        owner.commit()
        try:
            assert run_worker(jobs_engine, once=True) == 0
            with jobs_engine.connect() as observer:
                assert observer.scalar(text("SELECT status FROM analysis_jobs WHERE analysis_id=:id"), {"id": analysis_id}) == "running"
        finally:
            owner.execute(text("SELECT pg_advisory_unlock(:ns,:key)"), {"ns": WORKER_LOCK_NAMESPACE, "key": WORKER_LOCK_KEY})


def test_recovery_never_reexecutes_completed_analysis(jobs_engine):
    from app.worker import run_worker
    enqueue(jobs_engine)
    with jobs_engine.begin() as connection:
        connection.execute(text("UPDATE analyses SET status='completed', total_crashes=42"))
        connection.execute(text("UPDATE analysis_jobs SET status='running', attempts=1"))
    assert run_worker(jobs_engine, once=True) == 0
    with jobs_engine.connect() as connection:
        assert connection.execute(text("SELECT status,total_crashes FROM analyses")).one() == ("completed", 42)
        assert connection.execute(text("SELECT status,attempts FROM analysis_jobs")).one() == ("completed", 1)


def test_interrupted_exhausted_job_fails_without_fourth_attempt(jobs_engine):
    from app.worker import run_worker
    enqueue(jobs_engine)
    with jobs_engine.begin() as connection:
        connection.execute(text("UPDATE analyses SET status='running'"))
        connection.execute(text("UPDATE analysis_jobs SET status='running', attempts=3"))
    assert run_worker(jobs_engine, once=True) == 0
    with jobs_engine.connect() as connection:
        assert connection.execute(text("SELECT status,attempts FROM analysis_jobs")).one() == ("failed", 3)
        assert connection.scalar(text("SELECT status FROM analyses")) == "failed"


def test_permanent_failure_is_generic_and_not_retried(jobs_engine):
    from app.worker import run_worker
    enqueue(jobs_engine)  # Missing coverage is a permanent validation failure.
    assert run_worker(jobs_engine, once=True) == 1
    assert run_worker(jobs_engine, once=True) == 0
    with jobs_engine.connect() as connection:
        assert connection.execute(text("SELECT status,attempts FROM analysis_jobs")).one() == ("failed", 1)
        message = connection.scalar(text("SELECT error_message FROM analyses"))
        assert message == "Analysis could not be completed. Please try again later."


def test_interrupted_job_retries_and_completes_after_owner_disconnect(jobs_engine):
    from app.worker import run_worker
    with jobs_engine.begin() as connection:
        connection.execute(text("""INSERT INTO crashes (crash_date,severity,muni_id,geom)
            VALUES ('2022-06-01','property_damage',1,ST_GeomFromText('POINT(-74.5 40.5)',4326))"""))
    enqueue(jobs_engine)
    with jobs_engine.begin() as connection:
        connection.execute(text("UPDATE analysis_jobs SET status='running', attempts=1"))
        connection.execute(text("UPDATE analyses SET status='running'"))
    assert run_worker(jobs_engine, once=True) == 1
    with jobs_engine.connect() as connection:
        assert connection.execute(text("SELECT status,attempts FROM analysis_jobs")).one() == ("completed", 2)


def test_worker_disconnect_cannot_reconnect_and_write(jobs_engine, monkeypatch):
    from app.services.hin_service import HINService
    from app.worker import run_worker, WorkerConnectionLost
    from sqlalchemy.exc import DBAPIError
    with jobs_engine.begin() as connection:
        connection.execute(text("""INSERT INTO crashes (crash_date,severity,muni_id,geom)
            VALUES ('2022-06-01','property_damage',1,ST_GeomFromText('POINT(-74.5 40.5)',4326))"""))
    enqueue(jobs_engine)
    def lose_connection(service, *args):
        pid = service.db.scalar(text("SELECT pg_backend_pid()"))
        with jobs_engine.connect() as control:
            assert control.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        service.db.execute(text("SELECT 1"))
    monkeypatch.setattr(HINService, "calculate_all_segment_stats", lose_connection)
    with pytest.raises((WorkerConnectionLost, DBAPIError)):
        run_worker(jobs_engine, once=True)
    with jobs_engine.connect() as connection:
        assert connection.execute(text("SELECT status,attempts FROM analysis_jobs")).one() == ("running", 1)
        # HIN's failure handler must NOT mark this failed using a new DB session.
        assert connection.scalar(text("SELECT status FROM analyses")) == "running"
    monkeypatch.undo()
    assert run_worker(jobs_engine, once=True) == 1
    with jobs_engine.connect() as connection:
        assert connection.execute(text("SELECT status,attempts FROM analysis_jobs")).one() == ("completed", 2)


def test_statement_timeout_retries_only_three_times(jobs_engine, monkeypatch):
    from app.services.hin_service import HINService
    from app.worker import run_worker
    with jobs_engine.begin() as connection:
        connection.execute(text("""INSERT INTO crashes (crash_date,severity,muni_id,geom)
            VALUES ('2022-06-01','property_damage',1,ST_GeomFromText('POINT(-74.5 40.5)',4326))"""))
    enqueue(jobs_engine)
    def timeout(service, *args):
        service.db.execute(text("SELECT pg_sleep(0.15)"))
    monkeypatch.setattr(HINService, "calculate_all_segment_stats", timeout)
    for attempt, expected in ((1, "pending"), (2, "pending"), (3, "failed")):
        assert run_worker(jobs_engine, once=True, statement_timeout_ms=50) == 1
        with jobs_engine.connect() as connection:
            assert connection.execute(text("SELECT status,attempts FROM analysis_jobs")).one() == (expected, attempt)
    assert run_worker(jobs_engine, once=True, statement_timeout_ms=50) == 0


def test_global_lock_is_on_hin_connection_across_commits(jobs_engine, monkeypatch):
    from app.services.hin_service import HINService
    from app.worker import run_worker, WORKER_LOCK_NAMESPACE, WORKER_LOCK_KEY
    with jobs_engine.begin() as connection:
        connection.execute(text("""INSERT INTO crashes (crash_date,severity,muni_id,geom)
            VALUES ('2022-06-01','property_damage',1,ST_GeomFromText('POINT(-74.5 40.5)',4326))"""))
    enqueue(jobs_engine)
    original = HINService.calculate_all_segment_stats
    def observe(service, *args):
        for _ in range(2):
            assert service.db.scalar(text("""SELECT count(*) FROM pg_locks
                WHERE locktype='advisory' AND pid=pg_backend_pid()
                  AND classid=:namespace AND objid=:key AND granted"""),
                {"namespace": WORKER_LOCK_NAMESPACE, "key": WORKER_LOCK_KEY}) == 1
            service.db.commit()
            assert run_worker(jobs_engine, once=True) == 0
        return original(service, *args)
    monkeypatch.setattr(HINService, "calculate_all_segment_stats", observe)
    assert run_worker(jobs_engine, once=True) == 1
    with jobs_engine.connect() as connection:
        assert connection.scalar(text("SELECT status FROM analyses")) == "completed"


def test_municipality_lock_wait_is_bounded_by_worker_timeout(jobs_engine):
    from app.services.hin_service import HINService
    from app.worker import run_worker
    with jobs_engine.begin() as connection:
        connection.execute(text("""INSERT INTO crashes (crash_date,severity,muni_id,geom)
            VALUES ('2022-06-01','property_damage',1,ST_GeomFromText('POINT(-74.5 40.5)',4326))"""))
    enqueue(jobs_engine)
    with jobs_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:ns,1)"), {"ns": HINService.ANALYSIS_LOCK_NAMESPACE})
        holder.commit()
        def release():
            holder.execute(text("SELECT pg_advisory_unlock(:ns,1)"), {"ns": HINService.ANALYSIS_LOCK_NAMESPACE})
        timer = Timer(0.6, release)
        timer.start()
        try:
            assert run_worker(jobs_engine, once=True, lock_timeout_ms=50) == 1
        finally:
            timer.join()
    with jobs_engine.connect() as connection:
        # No analysis work should run after the 50ms lock timeout.
        assert connection.execute(text("SELECT status,attempts FROM analysis_jobs")).one() == ("pending", 1)


def test_continuous_worker_waits_then_recovers_after_previous_owner_releases(jobs_engine, monkeypatch):
    import app.worker as worker
    class StopWorker(Exception):
        pass
    with jobs_engine.begin() as connection:
        connection.execute(text("""INSERT INTO crashes (crash_date,severity,muni_id,geom)
            VALUES ('2022-06-01','property_damage',1,ST_GeomFromText('POINT(-74.5 40.5)',4326))"""))
    enqueue(jobs_engine)
    with jobs_engine.begin() as connection:
        connection.execute(text("UPDATE analysis_jobs SET status='running', attempts=1"))
        connection.execute(text("UPDATE analyses SET status='running'"))
    parameters = {"ns": worker.WORKER_LOCK_NAMESPACE, "key": worker.WORKER_LOCK_KEY}
    with jobs_engine.connect() as owner:
        owner.execute(text("SELECT pg_advisory_lock(:ns,:key)"), parameters)
        owner.commit()
        sleeps = []
        def poll(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 1:
                # While the old worker still owns the lock, no recovery may occur.
                with jobs_engine.connect() as observer:
                    assert observer.execute(text("SELECT status,attempts FROM analysis_jobs")).one() == ("running", 1)
                assert owner.scalar(text("SELECT pg_advisory_unlock(:ns,:key)"), parameters)
                owner.commit()
                return
            raise StopWorker()
        monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=lambda: 0, sleep=poll))
        try:
            with pytest.raises(StopWorker):
                worker.run_worker(jobs_engine, poll_seconds=0.01)
        finally:
            owner.execute(text("SELECT pg_advisory_unlock_all()"))
    assert sleeps == [0.01, 0.01]
    with jobs_engine.connect() as observer:
        assert observer.execute(text("SELECT status,attempts FROM analysis_jobs")).one() == ("completed", 2)


def test_continuous_worker_periodically_removes_expired_limits_after_startup(jobs_engine, monkeypatch):
    import app.worker as worker
    class StopWorker(Exception):
        pass
    clock = [0]
    original_process = worker._process_next
    def process_then_age_limits(db, max_attempts, ensure_connection):
        result = original_process(db, max_attempts, ensure_connection)
        # These rows did not exist during startup recovery/cleanup.
        db.execute(text("""INSERT INTO request_limits(scope,client_key,window_start,count)
            VALUES ('read','expired',clock_timestamp()-interval '3 days',1),
                   ('read','current',clock_timestamp(),1)"""))
        db.commit()
        clock[0] = 3601
        return result
    def stop_after_iteration(_):
        raise StopWorker()
    monkeypatch.setattr(worker, "_process_next", process_then_age_limits)
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=stop_after_iteration))
    with pytest.raises(StopWorker):
        worker.run_worker(jobs_engine)
    with jobs_engine.connect() as observer:
        assert observer.execute(text("SELECT client_key FROM request_limits ORDER BY client_key")).scalars().all() == ["current"]


def _hang_claimed_job(database_url, schema, ready):
    """Spawn-safe child fault injection, after the real durable job claim."""
    import time
    import app.worker as worker
    engine = create_engine(database_url, connect_args={"options": f"-csearch_path={schema},public"})
    def hang(service, **kwargs):
        ready.set()
        time.sleep(30)
    worker._WorkerHINService.run_analysis = hang
    worker.run_worker(engine, once=True)


def _successful_watchdog_child():
    return


def test_watchdog_kills_hung_child_and_leaves_job_recoverable(jobs_engine):
    import app.worker as worker
    assert hasattr(worker, "_run_bounded_child"), "A whole-job process deadline is missing"
    with jobs_engine.begin() as connection:
        connection.execute(text("""INSERT INTO crashes (crash_date,severity,muni_id,geom)
            VALUES ('2022-06-01','property_damage',1,ST_GeomFromText('POINT(-74.5 40.5)',4326))"""))
        schema = connection.scalar(text("SELECT current_schema()"))
    enqueue(jobs_engine)
    ready = multiprocessing.get_context("spawn").Event()
    target = partial(_hang_claimed_job, jobs_engine.url.render_as_string(hide_password=False), schema, ready)
    child_pids_before = {child.pid for child in multiprocessing.active_children()}
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(worker._run_bounded_child, target, 5)
        assert ready.wait(4), "Spawned worker never reached the claimed job"
        assert future.result(timeout=8) == 1
    assert {child.pid for child in multiprocessing.active_children()} == child_pids_before
    with jobs_engine.connect() as observer:
        assert observer.execute(text("SELECT status,attempts FROM analysis_jobs")).one() == ("running", 1)
    assert worker.run_worker(jobs_engine, once=True) == 1
    with jobs_engine.connect() as observer:
        assert observer.execute(text("SELECT status,attempts FROM analysis_jobs")).one() == ("completed", 2)


def test_watchdog_returns_success_and_reaps_completed_child():
    import app.worker as worker
    assert hasattr(worker, "_run_bounded_child"), "A whole-job process deadline is missing"
    child_pids_before = {child.pid for child in multiprocessing.active_children()}
    assert worker._run_bounded_child(_successful_watchdog_child, 10) == 0
    assert {child.pid for child in multiprocessing.active_children()} == child_pids_before


def test_cli_once_uses_watchdog_and_propagates_failure(monkeypatch):
    import app.worker as worker
    from app.config import settings
    assert hasattr(worker, "_run_bounded_child"), "A whole-job process deadline is missing"
    observed = []
    def fail_child(target, timeout_seconds):
        observed.append(timeout_seconds)
        return 1
    monkeypatch.setattr(worker, "_run_bounded_child", fail_child)
    monkeypatch.setattr("sys.argv", ["worker", "--once"])
    assert worker.main() == 1
    assert observed == [settings.worker_job_timeout_seconds]


def test_cli_sigterm_unwinds_child_supervision_and_restores_handler(monkeypatch):
    import app.worker as worker
    previous_handler = signal.getsignal(signal.SIGTERM)
    def terminate_supervisor(target, timeout_seconds):
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler), "SIGTERM must unwind the child-reaping finally block"
        handler(signal.SIGTERM, None)
    monkeypatch.setattr(worker, "_run_bounded_child", terminate_supervisor)
    monkeypatch.setattr("sys.argv", ["worker", "--once"])
    with pytest.raises(SystemExit) as stopped:
        worker.main()
    assert stopped.value.code == 128 + signal.SIGTERM
    assert signal.getsignal(signal.SIGTERM) == previous_handler
