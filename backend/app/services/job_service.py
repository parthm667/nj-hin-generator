"""Atomic admission to the bounded anonymous analysis queue."""

from sqlalchemy import func, text

from app.models.jobs import AnalysisJob


class QueueFullError(Exception):
    """The shared pending queue is full."""


class ClientQuotaError(Exception):
    """This anonymous client already has its allowed active jobs."""


class JobService:
    # Different namespace from worker ownership and municipality locks.
    ENQUEUE_LOCK_NAMESPACE = 1212763715

    def __init__(self, db):
        self.db = db

    def enqueue(self, analysis, client_key, *, max_pending=20, max_client_active=2):
        """Add analysis + job in the caller's transaction; never commit here."""
        if not client_key or len(client_key) > 64:
            raise ValueError("Invalid client key")
        if max_pending < 1 or max_client_active < 1:
            raise ValueError("Queue limits must be positive")
        with self.db.no_autoflush:
            self.db.execute(
                text("SELECT pg_advisory_xact_lock(:namespace, 1)"),
                {"namespace": self.ENQUEUE_LOCK_NAMESPACE},
            )
            # Include the running slot: recovery can return it to pending without
            # exceeding the bound even if requests filled the queue meanwhile.
            outstanding = self.db.query(func.count(AnalysisJob.analysis_id)).filter(
                AnalysisJob.status.in_(("pending", "running"))
            ).scalar()
            if outstanding >= max_pending:
                raise QueueFullError("The analysis queue is full. Please try again later.")
            active = self.db.query(func.count(AnalysisJob.analysis_id)).filter(
                AnalysisJob.client_key == client_key,
                AnalysisJob.status.in_(("pending", "running")),
            ).scalar()
            if active >= max_client_active:
                raise ClientQuotaError("Please wait for your existing analyses to finish.")
        self.db.add(analysis)
        self.db.flush()
        job = AnalysisJob(analysis_id=analysis.analysis_id, client_key=client_key)
        self.db.add(job)
        self.db.flush()
        return job
