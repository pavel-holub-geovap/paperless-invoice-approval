from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.integrations.ollama import OllamaClient
from app.integrations.paperless import PaperlessClient
from app.models import Invoice, InvoiceStatus, ProcessingJob, SystemHeartbeat, utcnow
from app.services.audit import record_event
from app.services.extraction import apply_extraction
from app.services.jobs import complete_job, enqueue_job, fail_job, lease_next_job
from app.services.workflow import create_invoice, transition

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("worker")


async def discover_documents(paperless: PaperlessClient) -> None:
    settings = get_settings()
    async for document in paperless.iter_documents_with_tag(settings.paperless_inbox_tag):
        with SessionLocal.begin() as db:
            invoice = create_invoice(db, document.id)
            enqueue_job(
                db,
                "EXTRACT_INVOICE",
                f"extract:{invoice.id}:r{invoice.current_revision_number}",
                invoice_id=invoice.id,
                payload={"paperless_document_id": document.id},
            )


async def process_one(paperless: PaperlessClient, ollama: OllamaClient) -> bool:
    with SessionLocal.begin() as db:
        job = lease_next_job(db)
        if not job:
            return False
        job_id = job.id
        job_type = job.job_type
        invoice_id = job.invoice_id

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
            with SessionLocal.begin() as db:
                complete_job(db.get(ProcessingJob, job_id))
            return True
        if job_type != "EXTRACT_INVOICE":
            raise ValueError(f"Unsupported job type {job_type}")
        with SessionLocal.begin() as db:
            invoice = db.scalar(select(Invoice).where(Invoice.id == invoice_id).with_for_update())
            if invoice is None:
                raise ValueError("Invoice no longer exists")
            if invoice.status == InvoiceStatus.NEW:
                transition(db, invoice, InvoiceStatus.AI_PROCESSING, "system")
                record_event(db, "AI_EXTRACTION_STARTED", invoice=invoice, metadata={"job_id": job_id})
            paperless_id = invoice.paperless_document_id
        document = await paperless.get_document(paperless_id)
        payload = await ollama.extract_invoice(document.content)
        with SessionLocal.begin() as db:
            invoice = db.get(Invoice, invoice_id)
            if invoice is None:
                raise ValueError("Invoice no longer exists")
            apply_extraction(db, invoice, payload)
            complete_job(db.get(ProcessingJob, job_id))
        return True
    except Exception as exc:
        logger.exception("job_failed job_id=%s invoice_id=%s", job_id, invoice_id)
        with SessionLocal.begin() as db:
            persisted = db.get(ProcessingJob, job_id)
            if persisted:
                fail_job(persisted, exc)
        return True


async def run() -> None:
    settings = get_settings()
    paperless = PaperlessClient(settings)
    ollama = OllamaClient(settings)
    try:
        while True:
            with SessionLocal.begin() as db:
                heartbeat = db.get(SystemHeartbeat, "worker")
                if heartbeat is None:
                    db.add(SystemHeartbeat(name="worker", details={"status": "running"}))
                else:
                    heartbeat.updated_at = utcnow()
                    heartbeat.details = {"status": "running"}
            try:
                await discover_documents(paperless)
            except Exception:
                logger.exception("paperless_discovery_failed")
            processed = await process_one(paperless, ollama)
            if not processed:
                await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        await paperless.close()
        await ollama.close()


if __name__ == "__main__":
    asyncio.run(run())
