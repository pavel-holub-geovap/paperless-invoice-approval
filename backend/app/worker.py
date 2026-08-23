from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.integrations.ollama import OllamaClient, OllamaError
from app.integrations.paperless import PaperlessClient, PaperlessError
from app.models import (
    AIExtraction,
    AIExtractionStatus,
    Invoice,
    ProcessingJob,
    SystemHeartbeat,
    utcnow,
)
from app.services.extraction import (
    AI_JOB_TYPE,
    complete_ai_extraction,
    mark_ai_extraction_failed,
    queue_ai_extraction,
    start_ai_extraction,
)
from app.services.jobs import complete_job, fail_job, lease_next_job
from app.services.paperless_sync import mark_sync_error, sync_document_snapshot
from app.services.workflow import create_invoice

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("worker")


async def discover_documents(paperless: PaperlessClient) -> None:
    settings = get_settings()
    async for document in paperless.iter_documents_with_tag(settings.paperless_inbox_tag):
        try:
            with SessionLocal.begin() as db:
                invoice = create_invoice(db, document.id)
                sync_document_snapshot(db, invoice, document)
        except Exception as exc:
            logger.exception("paperless_document_sync_failed document_id=%s", document.id)
            with SessionLocal.begin() as db:
                invoice = db.scalar(
                    select(Invoice).where(Invoice.paperless_document_id == document.id)
                )
                if invoice is not None:
                    mark_sync_error(db, invoice, exc)


def queue_pending_ai() -> None:
    settings = get_settings()
    if not settings.ai_extraction_enabled:
        return
    with SessionLocal.begin() as db:
        invoices = db.scalars(
            select(Invoice).where(
                Invoice.ai_status == AIExtractionStatus.AI_PENDING,
                Invoice.paperless_ocr_text != "",
            )
        ).all()
        for invoice in invoices:
            existing = db.scalar(
                select(AIExtraction.id).where(AIExtraction.invoice_id == invoice.id).limit(1)
            )
            if existing is None:
                queue_ai_extraction(db, invoice, settings)


async def _process_ai_job(
    job_id: str,
    invoice_id: str,
    extraction_id: str,
    paperless: PaperlessClient,
    ollama: OllamaClient,
) -> None:
    with SessionLocal.begin() as db:
        extraction = db.get(AIExtraction, extraction_id)
        if extraction is None or extraction.invoice_id != invoice_id:
            raise ValueError("AI extraction no longer exists")
        start_ai_extraction(db, extraction)
        paperless_id = extraction.invoice.paperless_document_id

    document = await paperless.get_document(paperless_id)
    with SessionLocal.begin() as db:
        extraction = db.get(AIExtraction, extraction_id)
        if extraction is None:
            raise ValueError("AI extraction no longer exists")
        sync_document_snapshot(db, extraction.invoice, document)

    result = await ollama.extract_invoice(document.content)
    with SessionLocal.begin() as db:
        extraction = db.get(AIExtraction, extraction_id)
        job = db.get(ProcessingJob, job_id)
        if extraction is None or job is None:
            raise ValueError("AI extraction or job no longer exists")
        complete_ai_extraction(db, extraction, result)
        complete_job(job)


async def process_one(paperless: PaperlessClient, ollama: OllamaClient | None) -> bool:
    with SessionLocal.begin() as db:
        job = lease_next_job(db)
        if not job:
            return False
        job_id = job.id
        job_type = job.job_type
        invoice_id = job.invoice_id
        job_payload = dict(job.payload)

    try:
        if not invoice_id:
            raise ValueError("Invoice job has no invoice_id")
        if job_type == "SYNC_PAPERLESS_STATUS":
            with SessionLocal() as db:
                sync_job = db.get(ProcessingJob, job_id)
                invoice = db.get(Invoice, invoice_id)
                if sync_job is None or invoice is None:
                    raise ValueError("Invoice or status job no longer exists")
                tag_name = getattr(get_settings(), str(sync_job.payload["tag_setting"]))
                paperless_id = invoice.paperless_document_id
            await paperless.set_managed_status_tag(paperless_id, tag_name)
            document = await paperless.get_document(paperless_id)
            with SessionLocal.begin() as db:
                sync_job = db.get(ProcessingJob, job_id)
                invoice = db.get(Invoice, invoice_id)
                if sync_job is None or invoice is None:
                    raise ValueError("Invoice or status job no longer exists")
                sync_document_snapshot(db, invoice, document)
                complete_job(sync_job)
            return True
        if job_type == AI_JOB_TYPE:
            if ollama is None:
                raise OllamaError("AI extraction is disabled")
            await _process_ai_job(
                job_id,
                invoice_id,
                str(job_payload["ai_extraction_id"]),
                paperless,
                ollama,
            )
            return True
        raise ValueError(f"Unsupported job type: {job_type}")
    except Exception as exc:
        logger.exception("job_failed job_id=%s invoice_id=%s", job_id, invoice_id)
        with SessionLocal.begin() as db:
            persisted = db.get(ProcessingJob, job_id)
            if persisted:
                fail_job(persisted, exc)
                extraction_id = job_payload.get("ai_extraction_id")
                extraction = db.get(AIExtraction, extraction_id) if extraction_id else None
                if extraction is not None:
                    code = (
                        "PAPERLESS_ERROR"
                        if isinstance(exc, PaperlessError)
                        else exc.code
                        if isinstance(exc, OllamaError)
                        else "EXTRACTION_FAILED"
                    )
                    mark_ai_extraction_failed(
                        db,
                        extraction,
                        code=code,
                        message=str(exc),
                        final=persisted.status.value == "FAILED",
                    )
        return True


async def run() -> None:
    settings = get_settings()
    paperless = PaperlessClient(settings)
    ollama = OllamaClient(settings) if settings.ai_extraction_enabled else None
    next_discovery = 0.0
    queue_pending_ai()
    try:
        while True:
            with SessionLocal.begin() as db:
                heartbeat = db.get(SystemHeartbeat, "worker")
                if heartbeat is None:
                    db.add(
                        SystemHeartbeat(
                            name="worker",
                            details={"status": "running", "mode": "paperless-ai"},
                        )
                    )
                else:
                    heartbeat.updated_at = utcnow()
                    heartbeat.details = {"status": "running", "mode": "paperless-ai"}
            if time.monotonic() >= next_discovery:
                try:
                    await discover_documents(paperless)
                except Exception:
                    logger.exception("paperless_discovery_failed")
                next_discovery = time.monotonic() + settings.paperless_sync_seconds
            processed = await process_one(paperless, ollama)
            if not processed:
                await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        await paperless.close()
        if ollama is not None:
            await ollama.close()


if __name__ == "__main__":
    asyncio.run(run())
