from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import (
    Invoice,
    InvoiceDisposition,
    InvoiceStatus,
    SourceDocumentStatus,
)
from app.services.audit import record_event
from app.services.jobs import enqueue_job
from app.services.workflow import PAPERLESS_TAG_SETTING, WorkflowError

EARLY_IGNORE_STATUSES = {
    InvoiceStatus.NEW,
    InvoiceStatus.VALIDATION,
    InvoiceStatus.QUEUE_REVIEW,
    InvoiceStatus.NEEDS_REVIEW,
    InvoiceStatus.RETURNED,
}


def ensure_actionable(invoice: Invoice, action: str) -> None:
    if invoice.disposition != InvoiceDisposition.ACTIVE:
        raise WorkflowError(f"Ignored invoice cannot {action}")
    if invoice.source_status == SourceDocumentStatus.MISSING:
        raise WorkflowError(f"Invoice with a missing Paperless source cannot {action}")


def set_disposition(
    db: Session,
    invoice: Invoice,
    disposition: InvoiceDisposition,
    actor: str,
    reason: str,
    *,
    comment: str | None = None,
    duplicate_of: Invoice | None = None,
) -> None:
    if disposition == InvoiceDisposition.ACTIVE:
        raise WorkflowError("Use restore to return an invoice to ACTIVE")
    if (
        invoice.status not in EARLY_IGNORE_STATUSES
        and not (
            invoice.source_status == SourceDocumentStatus.MISSING
            and disposition == InvoiceDisposition.IGNORED_DUPLICATE
        )
    ):
        raise WorkflowError(f"Invoice cannot be ignored from {invoice.status.value}")
    if disposition == InvoiceDisposition.IGNORED_DUPLICATE:
        if duplicate_of is None:
            raise WorkflowError("Duplicate disposition requires a target invoice")
        if duplicate_of.id == invoice.id:
            raise WorkflowError("Invoice cannot be a duplicate of itself")
    elif duplicate_of is not None:
        raise WorkflowError("Only a duplicate disposition can reference another invoice")

    old = invoice.disposition
    invoice.disposition = disposition
    invoice.disposition_reason = reason.strip()
    invoice.disposition_comment = comment.strip() if comment and comment.strip() else None
    invoice.disposition_actor = actor
    invoice.disposition_changed_at = datetime.now(UTC)
    invoice.duplicate_of_invoice_id = duplicate_of.id if duplicate_of else None
    event_type = (
        "DUPLICATE_MARKED"
        if disposition == InvoiceDisposition.IGNORED_DUPLICATE
        else "INVOICE_IGNORED"
    )
    record_event(
        db,
        event_type,
        actor=actor,
        invoice=invoice,
        old_state=old.value,
        new_state=disposition.value,
        comment=invoice.disposition_comment,
        metadata={
            "reason": invoice.disposition_reason,
            "duplicate_of_invoice_id": invoice.duplicate_of_invoice_id,
            "paperless_document_id": invoice.paperless_document_id,
        },
    )
    if invoice.source_status == SourceDocumentStatus.AVAILABLE:
        tag_setting = (
            "paperless_tag_duplicate"
            if disposition == InvoiceDisposition.IGNORED_DUPLICATE
            else "paperless_tag_ignored"
        )
        enqueue_job(
            db,
            "SYNC_PAPERLESS_STATUS",
            f"paperless-disposition:{invoice.id}:{disposition.value}",
            invoice_id=invoice.id,
            payload={"tag_setting": tag_setting, "disposition": disposition.value},
        )


def restore_disposition(db: Session, invoice: Invoice, actor: str, comment: str | None) -> None:
    if invoice.disposition == InvoiceDisposition.ACTIVE:
        return
    old = invoice.disposition
    old_target = invoice.duplicate_of_invoice_id
    invoice.disposition = InvoiceDisposition.ACTIVE
    invoice.disposition_reason = None
    invoice.disposition_comment = None
    invoice.disposition_actor = actor
    invoice.disposition_changed_at = datetime.now(UTC)
    invoice.duplicate_of_invoice_id = None
    record_event(
        db,
        "INVOICE_RESTORED",
        actor=actor,
        invoice=invoice,
        old_state=old.value,
        new_state=InvoiceDisposition.ACTIVE.value,
        comment=comment,
        metadata={"former_duplicate_of_invoice_id": old_target},
    )
    if invoice.source_status == SourceDocumentStatus.AVAILABLE:
        tag_setting = PAPERLESS_TAG_SETTING.get(invoice.status, "paperless_tag_queue_review")
        enqueue_job(
            db,
            "SYNC_PAPERLESS_STATUS",
            f"paperless-disposition:{invoice.id}:ACTIVE:r{invoice.current_revision_number}",
            invoice_id=invoice.id,
            payload={"tag_setting": tag_setting, "disposition": "ACTIVE"},
        )
