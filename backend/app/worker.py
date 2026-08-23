from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.integrations.paperless import PaperlessClient
from app.models import Invoice, ProcessingJob, SystemHeartbeat, utcnow
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


async def process_one(paperless: PaperlessClient) -> bool:
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
        raise ValueError(f"Unsupported job type in Paperless-only worker: {job_type}")
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
    next_discovery = 0.0
    try:
        while True:
            with SessionLocal.begin() as db:
                heartbeat = db.get(SystemHeartbeat, "worker")
                if heartbeat is None:
                    db.add(
                        SystemHeartbeat(
                            name="worker",
                            details={"status": "running", "mode": "paperless-sync"},
                        )
                    )
                else:
                    heartbeat.updated_at = utcnow()
                    heartbeat.details = {"status": "running", "mode": "paperless-sync"}
            if time.monotonic() >= next_discovery:
                try:
                    await discover_documents(paperless)
                except Exception:
                    logger.exception("paperless_discovery_failed")
                next_discovery = time.monotonic() + settings.paperless_sync_seconds
            processed = await process_one(paperless)
            if not processed:
                await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        await paperless.close()


if __name__ == "__main__":
    asyncio.run(run())
