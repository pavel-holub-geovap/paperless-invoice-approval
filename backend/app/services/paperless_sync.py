from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.integrations.paperless import PaperlessDocument
from app.models import (
    AIExtraction,
    Invoice,
    InvoiceDisposition,
    InvoiceStatus,
    PaperlessSyncStatus,
    SourceDocumentStatus,
    utcnow,
)
from app.services.audit import record_event
from app.services.workflow import transition


def _snapshot(invoice: Invoice) -> dict[str, Any]:
    return {
        "title": invoice.paperless_title,
        "created_at": (
            invoice.paperless_created_at.isoformat() if invoice.paperless_created_at else None
        ),
        "correspondent_id": invoice.paperless_correspondent_id,
        "correspondent": invoice.paperless_correspondent_name,
        "tag_ids": list(invoice.paperless_tag_ids),
        "tags": list(invoice.paperless_tags),
        "original_filename": invoice.paperless_original_filename,
        "ocr_characters": len(invoice.paperless_ocr_text),
        "ocr_sha256": hashlib.sha256(invoice.paperless_ocr_text.encode()).hexdigest(),
    }


def sync_document_snapshot(
    db: Session,
    invoice: Invoice,
    document: PaperlessDocument,
    actor: str = "system",
) -> bool:
    source_restored = invoice.source_status == SourceDocumentStatus.MISSING
    if source_restored:
        invoice.source_status = SourceDocumentStatus.AVAILABLE
        invoice.source_missing_at = None
        record_event(
            db,
            "SOURCE_DOCUMENT_RESTORED",
            actor=actor,
            invoice=invoice,
            old_state=SourceDocumentStatus.MISSING.value,
            new_state=SourceDocumentStatus.AVAILABLE.value,
            metadata={"paperless_document_id": document.id},
        )
    old_snapshot = _snapshot(invoice)
    invoice.paperless_title = document.title
    invoice.paperless_created_at = document.created_at
    invoice.paperless_correspondent_id = document.correspondent
    invoice.paperless_correspondent_name = document.correspondent_name
    invoice.paperless_tag_ids = list(document.tags)
    invoice.paperless_tags = list(document.tag_names)
    invoice.paperless_ocr_text = document.content
    invoice.paperless_original_filename = document.original_filename
    invoice.sync_status = PaperlessSyncStatus.SYNCED
    invoice.sync_error = None
    invoice.last_synced_at = utcnow()
    new_snapshot = _snapshot(invoice)
    changed = old_snapshot != new_snapshot

    if changed:
        event_type = (
            "PAPERLESS_DOCUMENT_SYNCED"
            if old_snapshot["ocr_characters"] == 0
            else "PAPERLESS_DOCUMENT_CHANGED"
        )
        record_event(
            db,
            event_type,
            actor=actor,
            invoice=invoice,
            old_value=old_snapshot,
            new_value=new_snapshot,
            metadata={"paperless_document_id": document.id},
        )

    if invoice.status == InvoiceStatus.NEW and document.content.strip():
        transition(db, invoice, InvoiceStatus.VALIDATION, actor)
        transition(db, invoice, InvoiceStatus.QUEUE_REVIEW, actor)
    settings = get_settings()
    if source_restored and invoice.disposition != InvoiceDisposition.ACTIVE:
        from app.services.jobs import enqueue_job

        tag_setting = (
            "paperless_tag_duplicate"
            if invoice.disposition == InvoiceDisposition.IGNORED_DUPLICATE
            else "paperless_tag_ignored"
        )
        enqueue_job(
            db,
            "SYNC_PAPERLESS_STATUS",
            f"paperless-source-restored:{invoice.id}:{invoice.disposition.value}",
            invoice_id=invoice.id,
            payload={"tag_setting": tag_setting, "disposition": invoice.disposition.value},
        )
    has_extraction = db.scalar(
        select(AIExtraction.id).where(AIExtraction.invoice_id == invoice.id).limit(1)
    )
    if (
        settings.ai_extraction_enabled
        and invoice.disposition == InvoiceDisposition.ACTIVE
        and not has_extraction
        and document.content.strip()
    ):
        from app.services.extraction import queue_ai_extraction

        queue_ai_extraction(db, invoice, settings, actor)
    return changed


def mark_source_missing(db: Session, invoice: Invoice, actor: str = "system") -> bool:
    if invoice.source_status == SourceDocumentStatus.MISSING:
        return False
    invoice.source_status = SourceDocumentStatus.MISSING
    invoice.source_missing_at = utcnow()
    invoice.sync_status = PaperlessSyncStatus.ERROR
    invoice.sync_error = "Paperless source document returned HTTP 404"
    record_event(
        db,
        "SOURCE_DOCUMENT_MISSING",
        actor=actor,
        invoice=invoice,
        old_state=SourceDocumentStatus.AVAILABLE.value,
        new_state=SourceDocumentStatus.MISSING.value,
        metadata={"paperless_document_id": invoice.paperless_document_id},
    )
    return True


def mark_sync_error(db: Session, invoice: Invoice, error: Exception) -> None:
    invoice.sync_status = PaperlessSyncStatus.ERROR
    invoice.sync_error = str(error)[:4000]
    record_event(
        db,
        "PAPERLESS_SYNC_FAILED",
        invoice=invoice,
        comment=invoice.sync_error,
        metadata={"paperless_document_id": invoice.paperless_document_id},
    )
