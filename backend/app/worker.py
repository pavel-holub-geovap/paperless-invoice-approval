from __future__ import annotations

import asyncio
import hashlib
import logging
import time

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.integrations.ollama import (
    OllamaClient,
    OllamaError,
    OllamaRequestRejected,
    SchemaValidationFailed,
)
from app.integrations.paperless import PaperlessClient, PaperlessError, PaperlessNotFound
from app.models import (
    AIExtraction,
    AIExtractionStatus,
    ApprovedPdfArtifact,
    ApprovedPdfStatus,
    Invoice,
    InvoiceDisposition,
    IsdocStatus,
    ProcessingJob,
    SourceDocumentStatus,
    SystemHeartbeat,
    utcnow,
)
from app.services.approved_pdf import (
    create_approved_pdf,
    finalize_approved_pdf_bytes,
    mark_approved_pdf_stored,
    prepare_approved_pdf_artifact,
)
from app.services.extraction import (
    AI_JOB_TYPE,
    complete_ai_extraction,
    mark_ai_extraction_failed,
    queue_ai_extraction,
    start_ai_extraction,
)
from app.services.isdoc import apply_isdoc_inspection, inspect_pdf_isdoc
from app.services.jobs import complete_job, fail_job, lease_next_job
from app.services.paperless_sync import mark_source_missing, mark_sync_error, sync_document_snapshot
from app.services.uploads import poll_pending_uploads
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


async def reconcile_source_documents(paperless: PaperlessClient) -> None:
    with SessionLocal() as db:
        rows = db.execute(select(Invoice.id, Invoice.paperless_document_id)).all()
    for invoice_id, document_id in rows:
        try:
            document = await paperless.get_document(document_id)
        except PaperlessNotFound:
            with SessionLocal.begin() as db:
                invoice = db.get(Invoice, invoice_id)
                if invoice is not None:
                    changed = mark_source_missing(db, invoice)
                    if changed:
                        from app.services.validation import run_validations

                        run_validations(db, invoice)
        except Exception as exc:
            logger.warning(
                "paperless_reconciliation_unavailable document_id=%s error=%s",
                document_id,
                type(exc).__name__,
            )
            with SessionLocal.begin() as db:
                invoice = db.get(Invoice, invoice_id)
                if invoice is not None:
                    mark_sync_error(db, invoice, exc)
        else:
            with SessionLocal.begin() as db:
                invoice = db.get(Invoice, invoice_id)
                if invoice is not None:
                    was_missing = invoice.source_status == SourceDocumentStatus.MISSING
                    sync_document_snapshot(db, invoice, document)
                    if was_missing:
                        from app.services.validation import run_validations

                        run_validations(db, invoice)


def queue_pending_ai() -> None:
    settings = get_settings()
    if not settings.ai_extraction_enabled:
        return
    with SessionLocal.begin() as db:
        invoices = db.scalars(
            select(Invoice).where(
                Invoice.ai_status == AIExtractionStatus.AI_PENDING,
                Invoice.paperless_ocr_text != "",
                Invoice.disposition == InvoiceDisposition.ACTIVE,
                Invoice.source_status == SourceDocumentStatus.AVAILABLE,
                Invoice.isdoc_status.in_(
                    [IsdocStatus.NOT_PRESENT, IsdocStatus.INVALID, IsdocStatus.ERROR]
                ),
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
                if invoice.source_status == SourceDocumentStatus.MISSING:
                    complete_job(sync_job)
                    db.commit()
                    return True
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
        if job_type == "INSPECT_ISDOC":
            with SessionLocal() as db:
                invoice = db.get(Invoice, invoice_id)
                if invoice is None:
                    raise ValueError("Invoice for ISDOC inspection no longer exists")
                paperless_document_id = invoice.paperless_document_id
            pdf = await paperless.download_pdf(paperless_document_id)
            inspection = inspect_pdf_isdoc(pdf, get_settings())
            with SessionLocal.begin() as db:
                persisted = db.get(ProcessingJob, job_id)
                invoice = db.get(Invoice, invoice_id)
                if persisted is None or invoice is None:
                    raise ValueError("Invoice or ISDOC job no longer exists")
                actual_hash = hashlib.sha256(pdf).hexdigest()
                if invoice.source_pdf_sha256 and invoice.source_pdf_sha256 != actual_hash:
                    raise ValueError("Paperless original PDF hash changed")
                invoice.source_pdf_sha256 = actual_hash
                apply_isdoc_inspection(db, invoice, inspection)
                if (
                    inspection.status != IsdocStatus.VALID
                    and get_settings().ai_extraction_enabled
                    and invoice.paperless_ocr_text.strip()
                    and db.scalar(
                        select(AIExtraction.id)
                        .where(AIExtraction.invoice_id == invoice.id)
                        .limit(1)
                    )
                    is None
                ):
                    queue_ai_extraction(db, invoice, get_settings())
                complete_job(persisted)
            return True
        if job_type == "CREATE_APPROVED_PDF":
            with SessionLocal.begin() as db:
                persisted = db.get(ProcessingJob, job_id)
                invoice = db.get(Invoice, invoice_id)
                if persisted is None or invoice is None:
                    raise ValueError("Invoice or approved PDF job no longer exists")
                artifact = prepare_approved_pdf_artifact(db, invoice, get_settings())
                artifact_id = artifact.id
                if artifact.status == ApprovedPdfStatus.STORED:
                    complete_job(persisted)
                    return True
                paperless_document_id = invoice.paperless_document_id
                snapshot = dict(artifact.approval_snapshot)
                known_task_id = artifact.paperless_task_id
            original_pdf = await paperless.download_pdf(paperless_document_id)
            approved_pdf = create_approved_pdf(original_pdf, snapshot)
            with SessionLocal.begin() as db:
                artifact = db.get(ApprovedPdfArtifact, artifact_id)
                if artifact is None:
                    raise ValueError("Approved PDF artifact no longer exists")
                if artifact.approved_pdf_sha256:
                    if artifact.approved_pdf_sha256 != hashlib.sha256(approved_pdf).hexdigest():
                        raise ValueError("Approved PDF retry produced different bytes")
                else:
                    finalize_approved_pdf_bytes(db, artifact, original_pdf, approved_pdf)
                known_task_id = artifact.paperless_task_id
            if not known_task_id:
                tag_id = await paperless.resolve_tag_id(
                    get_settings().paperless_tag_approved_copy
                )
                known_task_id = await paperless.post_document(
                    approved_pdf,
                    filename=f"approved-{invoice_id}-r{snapshot['invoice_revision']}.pdf",
                    title=f"Schválená kopie {snapshot.get('invoice_number') or invoice_id} r{snapshot['invoice_revision']}",
                    tag_id=tag_id,
                )
                with SessionLocal.begin() as db:
                    artifact = db.get(ApprovedPdfArtifact, artifact_id)
                    if artifact is None:
                        raise ValueError("Approved PDF artifact no longer exists")
                    artifact.paperless_task_id = known_task_id
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                task = await paperless.get_task(known_task_id)
                if task and task.error:
                    raise ValueError(task.error)
                if task and task.related_document_ids:
                    with SessionLocal.begin() as db:
                        artifact = db.get(ApprovedPdfArtifact, artifact_id)
                        persisted = db.get(ProcessingJob, job_id)
                        if artifact is None or persisted is None:
                            raise ValueError("Approved PDF artifact or job disappeared")
                        mark_approved_pdf_stored(db, artifact, task.related_document_ids[0])
                        complete_job(persisted)
                    return True
                await asyncio.sleep(2)
            raise TimeoutError("Paperless approved-copy processing timed out")
        raise ValueError(f"Unsupported job type: {job_type}")
    except Exception as exc:
        logger.exception("job_failed job_id=%s invoice_id=%s", job_id, invoice_id)
        with SessionLocal.begin() as db:
            persisted = db.get(ProcessingJob, job_id)
            if persisted:
                terminal_ai_failure = isinstance(
                    exc, (SchemaValidationFailed, OllamaRequestRejected)
                )
                fail_job(persisted, exc, retryable=not terminal_ai_failure)
                if job_type == "CREATE_APPROVED_PDF" and persisted.status.value == "FAILED":
                    revision_id = job_payload.get("revision_id")
                    artifact = db.scalar(
                        select(ApprovedPdfArtifact)
                        .where(ApprovedPdfArtifact.revision_id == revision_id)
                        .order_by(ApprovedPdfArtifact.created_at.desc())
                    )
                    if artifact is not None and artifact.status != ApprovedPdfStatus.STORED:
                        artifact.status = ApprovedPdfStatus.FAILED
                        artifact.error_message = str(exc)[:4000]
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
                        raw_response=getattr(exc, "raw_response", None),
                        raw_attempts=getattr(exc, "raw_attempts", None),
                        schema_validation_errors=getattr(exc, "errors", None),
                        duration_ms=getattr(exc, "duration_ms", None),
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
                    await poll_pending_uploads(paperless)
                    await discover_documents(paperless)
                    await reconcile_source_documents(paperless)
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
