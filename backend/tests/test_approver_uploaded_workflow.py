from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ApprovalAction,
    ApprovalAssignment,
    ApprovalDecision,
    AuditEvent,
    CostCenter,
    DocumentType,
    ExtractionSource,
    InvoiceStatus,
    IsdocStatus,
    UploadOrigin,
    UserIdentity,
)
from app.schemas import AllocationInput
from app.services.approval_setup import replace_allocations
from app.services.classification import classify_document
from app.services.section_permissions import has_section_permission, set_section_permission
from app.services.validation import run_validations
from app.services.workflow import (
    WorkflowError,
    confirm_original,
    create_invoice,
    decide,
    submit_for_approval,
    submit_to_queue_review,
    transition,
    update_invoice_data,
)


def approver_invoice(db: Session):
    user = UserIdentity(
        subject="approver-subject",
        username="approver1",
        roles=["APPROVER"],
        active=True,
    )
    center = CostCenter(code="SEC-A", name="Sekce A", pohoda_code="SEC-A")
    db.add_all([user, center])
    db.flush()
    set_section_permission(
        db,
        approver_subject=user.subject,
        cost_center_id=center.id,
        active=True,
        actor="manager-subject",
    )
    invoice = create_invoice(db, 9911, user.subject)
    invoice.uploaded_by_subject = user.subject
    invoice.uploaded_by_username = user.username
    invoice.upload_origin = UploadOrigin.APPROVER
    invoice.document_type = DocumentType.RECEIVED_INVOICE
    invoice.isdoc_status = IsdocStatus.NOT_PRESENT
    invoice.extraction_source = ExtractionSource.OCR_AI
    update_invoice_data(
        db,
        invoice,
        {
            "supplier_name": "Dodavatel s.r.o.",
            "supplier_ico": "28652240",
            "invoice_number": "APP-2026-1",
            "issue_date": "2026-09-01",
            "due_date": "2026-09-15",
            "currency": "CZK",
            "total_amount": "121.00",
            "vat_breakdown": [{"base": "100.00", "rate": "21", "vat": "21.00"}],
        },
        user.subject,
    )
    transition(db, invoice, InvoiceStatus.VALIDATION, user.subject)
    run_validations(db, invoice, user.subject)
    transition(db, invoice, InvoiceStatus.NEEDS_REVIEW, user.subject)
    confirm_original(db, invoice, user.subject)
    return invoice, user, center


def test_permission_grant_and_revoke_are_audited(db: Session) -> None:
    _, user, center = approver_invoice(db)
    assert has_section_permission(db, user.subject, center.id)
    set_section_permission(
        db,
        approver_subject=user.subject,
        cost_center_id=center.id,
        active=False,
        actor="manager-subject",
    )
    assert not has_section_permission(db, user.subject, center.id)
    events = set(db.scalars(select(AuditEvent.event_type)).all())
    assert "APPROVER_SECTION_PERMISSION_GRANTED" in events
    assert "APPROVER_SECTION_PERMISSION_REVOKED" in events


def test_uploader_can_only_allocate_permitted_sections_and_is_auto_assigned(
    db: Session,
) -> None:
    invoice, user, center = approver_invoice(db)
    forbidden = CostCenter(code="SEC-B", name="Sekce B", pohoda_code="SEC-B")
    db.add(forbidden)
    db.flush()
    with pytest.raises(WorkflowError, match="povolené sekce"):
        replace_allocations(
            db,
            invoice,
            [AllocationInput(cost_center_id=forbidden.id, amount=Decimal("121.00"))],
            user.subject,
            self_assign_subject=user.subject,
        )
    replace_allocations(
        db,
        invoice,
        [AllocationInput(cost_center_id=center.id, amount=Decimal("121.00"))],
        user.subject,
        self_assign_subject=user.subject,
    )
    assignment = db.scalar(
        select(ApprovalAssignment).where(
            ApprovalAssignment.revision_id == invoice.current_revision.id,
            ApprovalAssignment.active.is_(True),
        )
    )
    assert assignment is not None
    assert assignment.approver_subject == user.subject


def test_self_approval_waits_for_revision_specific_manager_review(db: Session) -> None:
    invoice, user, center = approver_invoice(db)
    replace_allocations(
        db,
        invoice,
        [AllocationInput(cost_center_id=center.id, amount=Decimal("121.00"))],
        user.subject,
        self_assign_subject=user.subject,
    )
    assignment = db.scalar(
        select(ApprovalAssignment).where(
            ApprovalAssignment.revision_id == invoice.current_revision.id,
            ApprovalAssignment.active.is_(True),
        )
    )
    assert assignment is not None
    decide(db, assignment, ApprovalAction.APPROVE, user.subject, None)
    assert invoice.status == InvoiceStatus.NEEDS_REVIEW
    assert invoice.current_revision.queue_manager_reviewed_at is None

    submit_to_queue_review(db, invoice, user.subject)
    assert invoice.status == InvoiceStatus.QUEUE_REVIEW
    assert invoice.current_revision.submitted_to_queue_by == user.subject

    submit_for_approval(db, invoice, "manager-subject")
    assert invoice.status == InvoiceStatus.APPROVED
    assert invoice.current_revision.queue_manager_reviewed_by == "manager-subject"
    events = set(
        db.scalars(
            select(AuditEvent.event_type).where(AuditEvent.invoice_id == invoice.id)
        ).all()
    )
    assert {
        "UPLOADER_SECTION_SELF_APPROVED",
        "SUBMITTED_TO_QUEUE_MANAGER",
        "QUEUE_MANAGER_REVISION_REVIEWED",
    } <= events


def test_manager_change_after_submission_creates_revision_and_invalidates_approval(
    db: Session,
) -> None:
    invoice, user, center = approver_invoice(db)
    replace_allocations(
        db,
        invoice,
        [AllocationInput(cost_center_id=center.id, amount=Decimal("121.00"))],
        user.subject,
        self_assign_subject=user.subject,
    )
    assignment = db.scalar(select(ApprovalAssignment).where(ApprovalAssignment.active.is_(True)))
    assert assignment is not None
    decision = decide(db, assignment, ApprovalAction.APPROVE, user.subject, None)
    submit_to_queue_review(db, invoice, user.subject)
    old_revision = invoice.current_revision_number

    classify_document(
        db,
        invoice,
        document_type=DocumentType.RECEIVED_ADVANCE_INVOICE,
        processing_mode=invoice.processing_mode,
        actor="manager-subject",
    )
    assert invoice.current_revision_number == old_revision + 1
    assert not decision.valid
    assert invoice.current_revision.submitted_to_queue_at is not None
    assert invoice.current_revision.submitted_to_queue_by == user.subject
    assert invoice.current_revision.queue_manager_reviewed_at is None
    assert invoice.pohoda_import_method.value == "NONE"


def test_revoked_permission_blocks_a_new_decision(db: Session) -> None:
    invoice, user, center = approver_invoice(db)
    replace_allocations(
        db,
        invoice,
        [AllocationInput(cost_center_id=center.id, amount=Decimal("121.00"))],
        user.subject,
        self_assign_subject=user.subject,
    )
    assignment = db.scalar(select(ApprovalAssignment).where(ApprovalAssignment.active.is_(True)))
    assert assignment is not None
    set_section_permission(
        db,
        approver_subject=user.subject,
        cost_center_id=center.id,
        active=False,
        actor="manager-subject",
    )
    with pytest.raises(WorkflowError, match="již nemá oprávnění"):
        decide(db, assignment, ApprovalAction.APPROVE, user.subject, None)
    assert db.scalar(select(ApprovalDecision.id)) is None
