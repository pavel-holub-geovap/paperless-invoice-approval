from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import JobStatus, ProcessingJob


def enqueue_job(
    db: Session,
    job_type: str,
    idempotency_key: str,
    *,
    invoice_id: str | None = None,
    payload: dict[str, Any] | None = None,
    max_attempts: int = 3,
) -> ProcessingJob:
    existing = db.scalar(select(ProcessingJob).where(ProcessingJob.idempotency_key == idempotency_key))
    if existing:
        return existing
    job = ProcessingJob(
        job_type=job_type,
        idempotency_key=idempotency_key,
        invoice_id=invoice_id,
        payload=payload or {},
        max_attempts=max_attempts,
    )
    db.add(job)
    db.flush()
    return job


def lease_next_job(db: Session, lease_seconds: int = 300) -> ProcessingJob | None:
    now = datetime.now(UTC)
    stmt = (
        select(ProcessingJob)
        .where(
            ProcessingJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
            ProcessingJob.available_at <= now,
            or_(ProcessingJob.locked_until.is_(None), ProcessingJob.locked_until < now),
            ProcessingJob.attempts < ProcessingJob.max_attempts,
        )
        .order_by(ProcessingJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    job = db.scalar(stmt)
    if job:
        job.status = JobStatus.RUNNING
        job.attempts += 1
        job.locked_until = now + timedelta(seconds=lease_seconds)
        db.flush()
    return job


def complete_job(job: ProcessingJob) -> None:
    job.status = JobStatus.DONE
    job.locked_until = None
    job.last_error = None


def fail_job(job: ProcessingJob, error: Exception) -> None:
    job.last_error = str(error)[:4000]
    job.locked_until = None
    if job.attempts >= job.max_attempts:
        job.status = JobStatus.FAILED
    else:
        job.status = JobStatus.PENDING
        job.available_at = datetime.now(UTC) + timedelta(seconds=min(300, 2**job.attempts))
