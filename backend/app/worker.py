"""One durable PostgreSQL worker, independent of the API process.

Run ``python -m app.worker`` (or ``--once`` for one job). A session advisory
lock lives on the SAME connection as all HIN writes. No lease/heartbeat permits
takeover: PostgreSQL must first release the previous worker's ownership lock.
"""

import argparse
import logging
import multiprocessing
import math
import signal
import time
from contextlib import contextmanager

from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.jobs import AnalysisJob
from app.models.tables import Analysis
from app.services.hin_service import HINService


logger = logging.getLogger(__name__)
WORKER_LOCK_NAMESPACE = 1212763714
WORKER_LOCK_KEY = 1
FAILURE_MESSAGE = "Analysis could not be completed. Please try again later."
TRANSIENT_SQLSTATES = {"40001", "40P01", "55P03", "57014"}


class WorkerConnectionLost(RuntimeError):
    """Never resume a worker's writes on a replacement PostgreSQL session."""


class _WorkerHINService(HINService):
    @contextmanager
    def _municipality_analysis_lock(self, muni_id):
        """Use the owned connection so lock waits obey worker DB timeouts too."""
        parameters = {"ns": self.ANALYSIS_LOCK_NAMESPACE, "muni": muni_id}
        self.db.execute(text("SELECT pg_advisory_lock(:ns,:muni)"), parameters)
        try:
            yield
        finally:
            try:
                self.db.rollback()
                unlocked = self.db.scalar(text("SELECT pg_advisory_unlock(:ns,:muni)"), parameters)
                if not unlocked:
                    raise WorkerConnectionLost("Municipality lock ownership was lost")
                self.db.commit()
            except BaseException:
                # An uncertain session lock must never be reused by this worker.
                self.db.get_bind().invalidate()
                raise


def _recover(db, max_attempts):
    for job in db.scalars(select(AnalysisJob).where(AnalysisJob.status.in_(("pending", "running")))):
        analysis = db.get(Analysis, job.analysis_id)
        if analysis.status == "completed":
            job.status = "completed"
            job.finished_at = func.now()
        elif job.attempts >= max_attempts:
            job.status = analysis.status = "failed"
            analysis.error_message = FAILURE_MESSAGE
            job.finished_at = func.now()
        elif job.status == "running":
            job.status = analysis.status = "pending"
            analysis.error_message = None
        job.updated_at = func.now()
    # Windows are at most a day. Expired HMACs need no permanent retention.
    db.execute(text("DELETE FROM request_limits WHERE window_start < clock_timestamp() - interval '2 days'"))
    db.commit()


def _process_next(db, max_attempts, ensure_connection):
    job = db.scalars(select(AnalysisJob).where(
        AnalysisJob.status == "pending"
    ).order_by(AnalysisJob.created_at, AnalysisJob.analysis_id).with_for_update(skip_locked=True).limit(1)).first()
    if job is None:
        db.rollback()
        return False
    analysis = db.get(Analysis, job.analysis_id)
    job.status = analysis.status = "running"
    job.attempts += 1
    job.started_at = job.updated_at = func.now()
    db.commit()
    analysis_id = analysis.analysis_id
    try:
        _WorkerHINService(db).run_analysis(
            analysis_id=analysis_id, muni_id=analysis.muni_id,
            start_year=analysis.start_year, end_year=analysis.end_year,
            snap_distance_meters=analysis.snap_distance_meters,
            significance_threshold=analysis.significance_threshold,
        )
        ensure_connection()
        job.status = "completed"
        job.finished_at = job.updated_at = func.now()
        db.commit()
    except Exception as exc:
        db.rollback()
        ensure_connection()  # Losing ownership is fatal, never a local retry.
        logger.exception("Analysis job %s failed", analysis_id)
        db.expire_all()
        analysis = db.get(Analysis, analysis_id)
        job = db.get(AnalysisJob, analysis_id)
        # A final analysis commit may have succeeded before later bookkeeping failed.
        if analysis.status == "completed":
            job.status = "completed"
        else:
            sqlstate = getattr(getattr(exc, "orig", None), "pgcode", None)
            retry = isinstance(exc, DBAPIError) and sqlstate in TRANSIENT_SQLSTATES and job.attempts < max_attempts
            job.status = analysis.status = "pending" if retry else "failed"
            analysis.error_message = None if retry else FAILURE_MESSAGE
        job.updated_at = func.now()
        if job.status in ("failed", "completed"):
            job.finished_at = func.now()
        db.commit()
    return True


def run_worker(engine, *, once=False, poll_seconds=2, max_attempts=3,
               statement_timeout_ms=300000, lock_timeout_ms=5000):
    """Run continuously, or once; only once-mode exits when ownership is busy."""
    if not 1 <= max_attempts <= 3 or min(statement_timeout_ms, lock_timeout_ms, poll_seconds) <= 0:
        raise ValueError("Worker limits must be positive, with at most three attempts")
    connection = engine.connect()
    physical_connection = connection.connection.dbapi_connection
    db = Session(bind=connection, expire_on_commit=False)
    acquired = False
    processed = 0

    def ensure_connection(*args):
        if connection.closed or connection.invalidated or connection.connection.dbapi_connection is not physical_connection:
            raise WorkerConnectionLost("Worker database connection was lost; restart required")

    # HIN's existing exception handler rolls back and attempts another write.
    # Fence every statement/commit before it can run on a reconnected session.
    event.listen(connection, "before_cursor_execute", ensure_connection)
    event.listen(db, "before_commit", ensure_connection)
    try:
        for name, value in (("statement_timeout", statement_timeout_ms), ("lock_timeout", lock_timeout_ms)):
            db.execute(text("SELECT set_config(:name,:value,false)"), {"name": name, "value": str(value)})
        db.commit()
        while not acquired:
            acquired = db.scalar(text("SELECT pg_try_advisory_lock(:ns,:key)"),
                                 {"ns": WORKER_LOCK_NAMESPACE, "key": WORKER_LOCK_KEY})
            db.commit()
            if acquired:
                break
            if once:
                return 0
            # A rolling replacement waits for the old process to relinquish
            # ownership. No recovery or work runs while that lock is held.
            time.sleep(poll_seconds)
        _recover(db, max_attempts)
        next_cleanup = time.monotonic() + 3600
        while True:
            if _process_next(db, max_attempts, ensure_connection):
                processed += 1
            if time.monotonic() >= next_cleanup:
                db.execute(text("DELETE FROM request_limits WHERE window_start < clock_timestamp() - interval '2 days'"))
                db.commit()
                next_cleanup = time.monotonic() + 3600
            if once:
                return processed
            time.sleep(poll_seconds)
    finally:
        # Always discard this physical connection, including ambiguous acquire or
        # disconnect errors. SQLAlchemy pool close alone would retain session locks
        # and session timeout settings; physical disconnect releases both.
        try:
            db.close()
        finally:
            try:
                connection.invalidate()
            finally:
                connection.close()


def _run_once_from_settings():
    """Spawn entry point: build/use database resources only in the child."""
    from app.config import settings
    from app.models.database import engine
    try:
        run_worker(engine, once=True,
                   max_attempts=settings.worker_max_attempts,
                   statement_timeout_ms=settings.worker_statement_timeout_ms,
                   lock_timeout_ms=settings.worker_lock_timeout_ms)
    finally:
        engine.dispose()


def _run_bounded_child(target, timeout_seconds):
    """Return nonzero after child failure or a hard whole-process deadline."""
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Whole-job timeout must be positive and finite")
    child = multiprocessing.get_context("spawn").Process(target=target, daemon=True)
    child.start()
    try:
        child.join(timeout_seconds)
        if child.is_alive():
            logger.error("Worker exceeded its whole-job deadline; stopping child")
            return 1
        if child.exitcode != 0:
            logger.error("Worker child failed with exit code %s", child.exitcode)
            return 1
        return 0
    finally:
        # Also reap on supervisor interruption. No timed-out child may continue
        # work while the hosting service starts a replacement supervisor.
        if child.is_alive():
            child.terminate()
            child.join(5)
        if child.is_alive():
            child.kill()
            child.join(5)
        if child.is_alive():
            raise RuntimeError("Worker child could not be stopped")
        child.close()


def main():
    from app.config import settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    # A new child per job/poll costs startup time, acceptable for the single-
    # worker pilot. It bounds Python computation as well as database statements.
    def stop_supervisor(signum, frame):
        raise SystemExit(128 + signum)
    previous_handler = signal.signal(signal.SIGTERM, stop_supervisor)
    try:
        while True:
            result = _run_bounded_child(_run_once_from_settings, settings.worker_job_timeout_seconds)
            if result or args.once:
                return result
            time.sleep(2)
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


if __name__ == "__main__":
    raise SystemExit(main())
