"""Anonymous queue and shared request accounting; no user identity tables."""

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, func

from app.models.database import Base


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    analysis_id = Column(Integer, ForeignKey("analyses.analysis_id", ondelete="CASCADE"), primary_key=True)
    client_key = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="pending", server_default="pending")
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('pending','running','completed','failed')", name="ck_job_status"),
        CheckConstraint("attempts >= 0", name="ck_job_attempts"),
        Index("idx_jobs_queue", "status", "created_at"),
        Index("idx_jobs_client", "client_key", "status"),
    )


class RequestLimit(Base):
    __tablename__ = "request_limits"

    scope = Column(String(40), primary_key=True)
    client_key = Column(String(64), primary_key=True)
    window_start = Column(DateTime(timezone=True), nullable=False)
    count = Column(Integer, nullable=False)

