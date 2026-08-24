from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.routes.invoices import ignore_invoice, list_invoices
from app.integrations.paperless import PaperlessDocument
from app.models import (
    AuditEvent,
    InvoiceDisposition,
    InvoiceStatus,
    SourceDocumentStatus,
    ValidationResult,
    ValidationSeverity,
)
from app.schemas import CurrentUser, InvoiceDispositionSet
from app.services.disposition import ensure_actionable, restore_disposition, set_disposition
from app.services.paperless_sync import mark_source_missing, sync_document_snapshot
from app.services.validation import run_validations
from app.services.workflow import WorkflowError, create_invoice, transition


def manager() -> CurrentUser:
    return CurrentUser(subject="manager", username="manager", roles=["QUEUE_MANAGER"])


def test_duplicate_disposition_and_restore_are_audited_without_changing_workflow(db) -> None:
    original = create_invoice(db, 501)
    duplicate = create_invoice(db, 502)
    old_status = duplicate.status

    set_disposition(
        db,
        duplicate,
        InvoiceDisposition.IGNORED_DUPLICATE,
        "manager",
        "same supplier, number and total",
        comment="Confirmed against the original PDF",
        duplicate_of=original,
    )
    db.flush()

    assert duplicate.disposition == InvoiceDisposition.IGNORED_DUPLICATE
    assert duplicate.duplicate_of_invoice_id == original.id
    assert duplicate.status == old_status
    event = db.scalar(
        select(AuditEvent).where(
            AuditEvent.invoice_id == duplicate.id,
            AuditEvent.event_type == "DUPLICATE_MARKED",
        )
    )
    assert event.metadata_json["duplicate_of_invoice_id"] == original.id
    with pytest.raises(WorkflowError, match="Ignored invoice"):
        ensure_actionable(duplicate, "be exported")

    restore_disposition(db, duplicate, "manager", "Mistaken match")
    db.flush()
    assert duplicate.disposition == InvoiceDisposition.ACTIVE
    assert duplicate.duplicate_of_invoice_id is None
    assert db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.invoice_id == duplicate.id,
            AuditEvent.event_type == "INVOICE_RESTORED",
        )
    ) == 1


def test_ignore_is_limited_to_early_workflow_but_missing_duplicate_is_recoverable(db) -> None:
    original = create_invoice(db, 511)
    invoice = create_invoice(db, 512)
    transition(db, invoice, InvoiceStatus.VALIDATION, "system")
    transition(db, invoice, InvoiceStatus.QUEUE_REVIEW, "system")
    transition(db, invoice, InvoiceStatus.READY_FOR_APPROVAL, "system")

    try:
        set_disposition(
            db,
            invoice,
            InvoiceDisposition.IGNORED_OTHER,
            "manager",
            "late ignore",
        )
    except WorkflowError as exc:
        assert "cannot be ignored" in str(exc)
    else:
        raise AssertionError("late active invoice was ignored")

    invoice.source_status = SourceDocumentStatus.MISSING
    set_disposition(
        db,
        invoice,
        InvoiceDisposition.IGNORED_DUPLICATE,
        "manager",
        "orphan duplicate",
        duplicate_of=original,
    )
    assert invoice.disposition == InvoiceDisposition.IGNORED_DUPLICATE


def test_source_missing_and_restored_events_are_idempotent_and_validation_blocks(db) -> None:
    invoice = create_invoice(db, 521)
    assert mark_source_missing(db, invoice)
    assert not mark_source_missing(db, invoice)
    run_validations(db, invoice)
    db.flush()

    assert invoice.source_status == SourceDocumentStatus.MISSING
    missing_validation = db.scalar(
        select(ValidationResult).where(
            ValidationResult.revision_id == invoice.current_revision.id,
            ValidationResult.code == "SOURCE_DOCUMENT_MISSING",
        )
    )
    assert missing_validation.severity == ValidationSeverity.BLOCKING_ERROR
    assert db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.invoice_id == invoice.id,
            AuditEvent.event_type == "SOURCE_DOCUMENT_MISSING",
        )
    ) == 1
    try:
        ensure_actionable(invoice, "be exported")
    except WorkflowError as exc:
        assert "missing Paperless source" in str(exc)
    else:
        raise AssertionError("missing source was actionable")

    document = PaperlessDocument(
        id=521,
        title="Restored",
        content="",
        created_at=None,
        tags=(),
        tag_names=(),
        correspondent=None,
        correspondent_name=None,
        original_filename="restored.pdf",
    )
    sync_document_snapshot(db, invoice, document)
    sync_document_snapshot(db, invoice, document)
    db.flush()
    assert invoice.source_status == SourceDocumentStatus.AVAILABLE
    assert invoice.source_missing_at is None
    assert db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.invoice_id == invoice.id,
            AuditEvent.event_type == "SOURCE_DOCUMENT_RESTORED",
        )
    ) == 1


def test_queue_views_separate_active_ignored_and_missing(db) -> None:
    active = create_invoice(db, 531)
    ignored = create_invoice(db, 532)
    missing = create_invoice(db, 533)
    set_disposition(
        db,
        ignored,
        InvoiceDisposition.IGNORED_OTHER,
        "manager",
        "not an invoice",
    )
    mark_source_missing(db, missing)
    db.flush()

    def ids(view: str) -> set[str]:
        return {
            row.id
            for row in list_invoices(
                status_filter=None,
                supplier=None,
                approver=None,
                cost_center=None,
                view=view,
                db=db,
                user=manager(),
            )
        }

    assert ids("active") == {active.id}
    assert ids("ignored") == {ignored.id}
    assert ids("missing") == {missing.id}
    assert ids("all") == {active.id, ignored.id, missing.id}


def test_approver_cannot_change_disposition(db) -> None:
    invoice = create_invoice(db, 541)
    payload = InvoiceDispositionSet(
        disposition="IGNORED_OTHER",
        reason="not allowed",
    )
    with pytest.raises(HTTPException) as error:
        ignore_invoice(
            invoice.id,
            payload,
            db,
            CurrentUser(
                subject="approver",
                username="approver",
                roles=["APPROVER"],
                csrf_token="csrf",
            ),
        )
    assert error.value.status_code == 403
