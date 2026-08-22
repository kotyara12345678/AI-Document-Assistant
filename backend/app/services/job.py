"""Persistent background job queue backed by PostgreSQL.

Provides the core lifecycle for long-running tasks (large translations,
PDF edits, multi-stage document generation). Jobs are stored in PostgreSQL
and survive backend/worker restarts. ``FOR UPDATE SKIP LOCKED`` ensures
multiple workers never execute the same job twice.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.job import Job

logger = logging.getLogger("app.jobs")

# Job statuses
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

_VALID_STATUSES = {STATUS_QUEUED, STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED}


def create_job(
    db: Session,
    *,
    user_id: int,
    chat_id: int | None = None,
    job_type: str = "agent",
    payload: dict | None = None,
) -> Job:
    """Create a new job in ``queued`` status.

    Enforces per-user queue limits before creation.
    """
    _enforce_queue_limits(db, user_id)
    job = Job(
        user_id=user_id,
        chat_id=chat_id,
        type=job_type,
        status=STATUS_QUEUED,
        payload=payload or {},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    logger.info("Job %s created (user=%s, type=%s)", job.id, user_id, job_type)
    return job


def _enforce_queue_limits(db: Session, user_id: int) -> None:
    """Raise if the user already has too many queued or running jobs."""
    queued = (
        db.query(func.count(Job.id))
        .filter(Job.user_id == user_id, Job.status == STATUS_QUEUED)
        .scalar()
    )
    if queued >= settings.MAX_QUEUED_JOBS_PER_USER:
        raise _JobLimitError(
            f"Превышен лимит очереди задач ({settings.MAX_QUEUED_JOBS_PER_USER}). "
            "Подождите завершения текущих задач."
        )
    running = (
        db.query(func.count(Job.id))
        .filter(Job.user_id == user_id, Job.status == STATUS_RUNNING)
        .scalar()
    )
    if running >= settings.MAX_RUNNING_JOBS_PER_USER:
        raise _JobLimitError(
            f"Превышен лимит выполняемых задач ({settings.MAX_RUNNING_JOBS_PER_USER}). "
            "Подождите завершения текущих задач."
        )


class _JobLimitError(Exception):
    """Raised when a user exceeds their job limits."""


def claim_next_job(db: Session) -> Job | None:
    """Atomically claim the next queued job using ``FOR UPDATE SKIP LOCKED``.

    Returns None when no queued jobs are available.
    """
    result = db.execute(
        text(
            "SELECT id FROM jobs "
            "WHERE status = :queued "
            "ORDER BY created_at ASC "
            "LIMIT 1 "
            "FOR UPDATE SKIP LOCKED"
        ),
        {"queued": STATUS_QUEUED},
    )
    row = result.fetchone()
    if row is None:
        return None

    job = db.get(Job, row[0])
    if job is None:
        return None

    now = datetime.now(timezone.utc)
    job.status = STATUS_RUNNING
    job.started_at = now
    job.updated_at = now
    db.commit()
    db.refresh(job)
    logger.info("Job %s claimed by worker", job.id)
    return job


def complete_job(db: Session, job: Job, result: dict) -> None:
    """Mark a job as completed with its result payload."""
    now = datetime.now(timezone.utc)
    job.status = STATUS_COMPLETED
    job.result = result
    job.finished_at = now
    job.updated_at = now
    db.commit()
    logger.info("Job %s completed", job.id)


def fail_job(db: Session, job: Job, error: str) -> None:
    """Mark a job as failed with an error message."""
    now = datetime.now(timezone.utc)
    job.status = STATUS_FAILED
    job.error = error
    job.finished_at = now
    job.updated_at = now
    db.commit()
    logger.warning("Job %s failed: %s", job.id, error)


def cancel_job(db: Session, job_id: int, user_id: int) -> Job:
    """Request cancellation of a queued or running job.

    Queued jobs are cancelled immediately. Running jobs have
    ``cancel_requested`` set so the worker can check cooperatively.
    """
    job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
    if job is None:
        raise _JobNotFoundError(job_id)
    if job.status not in (STATUS_QUEUED, STATUS_RUNNING):
        raise _JobAlreadyFinishedError(job_id, job.status)

    if job.status == STATUS_QUEUED:
        now = datetime.now(timezone.utc)
        job.status = STATUS_CANCELLED
        job.finished_at = now
        job.updated_at = now
    else:
        job.cancel_requested = True
        job.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    logger.info("Job %s cancel requested (status=%s)", job.id, job.status)
    return job


def recover_stale_jobs(db: Session) -> int:
    """Reset jobs stuck in ``running`` longer than JOB_STALE_TIMEOUT_SECONDS.

    Called on worker startup. Returns the number of recovered jobs.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - settings.JOB_STALE_TIMEOUT_SECONDS
    cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)

    stale = (
        db.query(Job)
        .filter(
            Job.status == STATUS_RUNNING,
            Job.started_at < cutoff_dt,
        )
        .all()
    )
    count = 0
    for job in stale:
        logger.warning(
            "Recovering stale job %s (started %s, >%ds ago)",
            job.id,
            job.started_at,
            settings.JOB_STALE_TIMEOUT_SECONDS,
        )
        job.status = STATUS_QUEUED
        job.started_at = None
        job.updated_at = datetime.now(timezone.utc)
        count += 1
    if count:
        db.commit()
    return count


def get_user_jobs(
    db: Session,
    user_id: int,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Job]:
    """List jobs belonging to a user, newest first."""
    query = db.query(Job).filter(Job.user_id == user_id)
    if status:
        query = query.filter(Job.status == status)
    return query.order_by(Job.created_at.desc()).offset(offset).limit(limit).all()


def get_job(db: Session, job_id: int, user_id: int) -> Job | None:
    """Get a single job by id, enforcing ownership."""
    return db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()


def check_cancel_requested(db: Session, job_id: int) -> bool:
    """Check if cancellation was requested for a running job."""
    job = db.get(Job, job_id)
    if job is None:
        return True
    return job.cancel_requested


class _JobNotFoundError(Exception):
    def __init__(self, job_id: int):
        super().__init__(f"Job {job_id} not found")
        self.job_id = job_id


class _JobAlreadyFinishedError(Exception):
    def __init__(self, job_id: int, status: str):
        super().__init__(f"Job {job_id} is already {status}")
        self.job_id = job_id
        self.status = status
