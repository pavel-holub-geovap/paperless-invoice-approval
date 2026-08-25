from __future__ import annotations

from app.models import JobStatus, ProcessingJob
from app.services.jobs import fail_job


def test_non_retryable_schema_job_is_terminal_before_max_attempts() -> None:
    job = ProcessingJob(
        job_type="AI_EXTRACT_INVOICE",
        idempotency_key="schema-job",
        attempts=1,
        max_attempts=3,
        status=JobStatus.RUNNING,
    )

    fail_job(job, ValueError("schema failed"), retryable=False)

    assert job.status == JobStatus.FAILED
    assert job.attempts == 1
    assert job.last_error == "schema failed"
