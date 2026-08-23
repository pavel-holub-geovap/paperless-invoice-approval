from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.paperless import PaperlessDocument
from app.models import AuditEvent, InvoiceStatus, PaperlessSyncStatus
from app.services.paperless_sync import sync_document_snapshot
from app.services.workflow import create_invoice


def document(*, title: str = "Synthetic invoice") -> PaperlessDocument:
    return PaperlessDocument(
        id=42,
        title=title,
        content="OCR text from an image-only invoice",
        created_at=datetime(2026, 8, 23, 15, 42, tzinfo=UTC),
        tags=(1, 2),
        tag_names=("Přijatá faktura", "Test"),
        correspondent=7,
        correspondent_name="Synthetic Supplier",
        original_filename="synthetic-invoice.pdf",
    )


def test_paperless_snapshot_is_persisted_without_pdf(db: Session) -> None:
    invoice = create_invoice(db, 42)

    changed = sync_document_snapshot(db, invoice, document())

    assert changed
    assert invoice.status == InvoiceStatus.QUEUE_REVIEW
    assert invoice.sync_status == PaperlessSyncStatus.SYNCED
    assert invoice.paperless_title == "Synthetic invoice"
    assert invoice.paperless_correspondent_name == "Synthetic Supplier"
    assert invoice.paperless_tags == ["Přijatá faktura", "Test"]
    assert invoice.paperless_ocr_text.startswith("OCR text")
    assert not hasattr(invoice, "paperless_pdf")
    events = list(
        db.scalars(
            select(AuditEvent).where(AuditEvent.invoice_id == invoice.id).order_by(AuditEvent.created_at)
        )
    )
    assert [event.event_type for event in events] == [
        "DOCUMENT_DISCOVERED",
        "REVISION_CREATED",
        "PAPERLESS_DOCUMENT_SYNCED",
        "WORKFLOW_TRANSITION",
        "WORKFLOW_TRANSITION",
        "AI_EXTRACTION_QUEUED",
    ]


def test_unchanged_snapshot_does_not_duplicate_data_change_audit(db: Session) -> None:
    invoice = create_invoice(db, 42)
    sync_document_snapshot(db, invoice, document())
    before = len(
        list(
            db.scalars(
                select(AuditEvent).where(AuditEvent.event_type == "PAPERLESS_DOCUMENT_SYNCED")
            )
        )
    )

    changed = sync_document_snapshot(db, invoice, document())

    assert not changed
    after = len(
        list(
            db.scalars(
                select(AuditEvent).where(AuditEvent.event_type == "PAPERLESS_DOCUMENT_SYNCED")
            )
        )
    )
    assert before == after


def test_changed_paperless_metadata_is_append_only_audited(db: Session) -> None:
    invoice = create_invoice(db, 42)
    sync_document_snapshot(db, invoice, document())

    assert sync_document_snapshot(db, invoice, document(title="Updated title"))

    event = db.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "PAPERLESS_DOCUMENT_CHANGED")
        .order_by(AuditEvent.created_at.desc())
    )
    assert event is not None
    assert event.old_value["title"] == "Synthetic invoice"
    assert event.new_value["title"] == "Updated title"
