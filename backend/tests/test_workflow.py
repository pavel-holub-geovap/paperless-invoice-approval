from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    Allocation,
    ApprovalAction,
    ApprovalAssignment,
    ApprovalDecision,
    ApprovedPdfStatus,
    CostCenter,
    DocumentType,
    ExtractionSource,
    InvoiceStatus,
    IsdocStatus,
    ProcessingJob,
    UserIdentity,
)
from app.services.approved_pdf import prepare_approved_pdf_artifact
from app.services.validation import run_validations
from app.services.workflow import (
    WorkflowError,
    confirm_original,
    create_invoice,
    decide,
    fork_revision,
    reopen,
    submit_for_approval,
    transition,
    update_invoice_data,
)


def prepared_invoice(db: Session, approvers: tuple[str, ...] = ("approver-1",)):
    invoice = create_invoice(db, 101, "system")
    invoice.document_type = DocumentType.RECEIVED_INVOICE
    invoice.isdoc_status = IsdocStatus.NOT_PRESENT
    invoice.extraction_source = ExtractionSource.OCR_AI
    update_invoice_data(
        db,
        invoice,
        {
            "supplier_name": "Test s.r.o.",
            "ico": "27082440",
            "dic": "CZ27082440",
            "invoice_number": "2026001",
            "variable_symbol": "2026001",
            "issue_date": "2026-08-01",
            "taxable_supply_date": "2026-08-01",
            "due_date": "2026-08-15",
            "currency": "CZK",
            "total_amount": "121.00",
            "vat_breakdown": [{"base": "100.00", "rate": "21", "vat": "21.00"}],
            "description": "Testovací licence",
        },
        "manager",
    )
    transition(db, invoice, InvoiceStatus.VALIDATION, "system")
    centre = CostCenter(code="IT", name="IT", pohoda_code="IT")
    db.add(centre)
    db.flush()
    allocation = Allocation(
        invoice_id=invoice.id,
        revision_id=invoice.current_revision.id,
        cost_center_id=centre.id,
        amount=Decimal("121.00"),
    )
    db.add(allocation)
    db.flush()
    assignments = []
    for subject in approvers:
        db.add(UserIdentity(subject=subject, username=subject, roles=["APPROVER"], active=True))
        assignment = ApprovalAssignment(
            invoice_id=invoice.id,
            revision_id=invoice.current_revision.id,
            allocation_id=allocation.id,
            approver_subject=subject,
        )
        db.add(assignment)
        assignments.append(assignment)
    db.flush()
    run_validations(db, invoice)
    transition(db, invoice, InvoiceStatus.QUEUE_REVIEW, "system")
    confirm_original(db, invoice, "manager")
    return invoice, assignments


def test_single_approver_happy_path(db: Session) -> None:
    invoice, assignments = prepared_invoice(db)
    submit_for_approval(db, invoice, "manager")
    assert invoice.status == InvoiceStatus.AWAITING_APPROVAL
    decide(db, assignments[0], ApprovalAction.APPROVE, "approver-1", None)
    assert invoice.status == InvoiceStatus.APPROVED
    job = db.scalar(
        select(ProcessingJob).where(
            ProcessingJob.invoice_id == invoice.id,
            ProcessingJob.job_type == "CREATE_APPROVED_PDF",
        )
    )
    assert job is not None
    assert job.payload["revision_id"] == invoice.current_revision.id
    first = prepare_approved_pdf_artifact(db, invoice, Settings())
    second = prepare_approved_pdf_artifact(db, invoice, Settings())
    assert first.id == second.id


def test_parallel_approval_waits_for_every_required_assignment(db: Session) -> None:
    invoice, assignments = prepared_invoice(db, ("approver-1", "approver-2"))
    submit_for_approval(db, invoice, "manager")
    decide(db, assignments[0], ApprovalAction.APPROVE, "approver-1", None)
    assert invoice.status == InvoiceStatus.AWAITING_APPROVAL
    decide(db, assignments[1], ApprovalAction.APPROVE, "approver-2", None)
    assert invoice.status == InvoiceStatus.APPROVED


def test_return_requires_comment_and_returns_whole_invoice(db: Session) -> None:
    invoice, assignments = prepared_invoice(db)
    submit_for_approval(db, invoice, "manager")
    with pytest.raises(WorkflowError, match="comment"):
        decide(db, assignments[0], ApprovalAction.RETURN, "approver-1", "")
    decide(db, assignments[0], ApprovalAction.RETURN, "approver-1", "Doplňte DUZP")
    assert invoice.status == InvoiceStatus.RETURNED


def test_returned_invoice_is_resubmitted_as_new_revision(db: Session) -> None:
    invoice, assignments = prepared_invoice(db)
    submit_for_approval(db, invoice, "manager")
    decide(db, assignments[0], ApprovalAction.RETURN, "approver-1", "Doplňte DUZP")
    old_revision = invoice.current_revision_number
    submit_for_approval(db, invoice, "manager")
    assert invoice.current_revision_number == old_revision + 1
    assert invoice.status == InvoiceStatus.AWAITING_APPROVAL
    new_assignment = db.scalar(
        select(ApprovalAssignment).where(
            ApprovalAssignment.invoice_id == invoice.id,
            ApprovalAssignment.revision_id == invoice.current_revision.id,
            ApprovalAssignment.active.is_(True),
        )
    )
    decide(db, new_assignment, ApprovalAction.APPROVE, "approver-1", None)
    assert invoice.status == InvoiceStatus.APPROVED


def test_reject_blocks_invoice_globally(db: Session) -> None:
    invoice, assignments = prepared_invoice(db, ("approver-1", "approver-2"))
    submit_for_approval(db, invoice, "manager")
    decide(db, assignments[0], ApprovalAction.REJECT, "approver-1", "Nesouhlasí plnění")
    assert invoice.status == InvoiceStatus.REJECTED


def test_reopen_rejected_invoice_invalidates_rejection_and_requires_review(db: Session) -> None:
    invoice, assignments = prepared_invoice(db)
    submit_for_approval(db, invoice, "manager")
    decision = decide(
        db, assignments[0], ApprovalAction.REJECT, "approver-1", "Nesouhlasí plnění"
    )
    reopen(db, invoice, "manager", "Po opravě")
    assert invoice.status == InvoiceStatus.NEEDS_REVIEW
    assert invoice.current_revision_number == 3
    assert not decision.valid
    assert invoice.original_reviewed_at is None


def test_significant_change_invalidates_historical_decision(db: Session) -> None:
    invoice, assignments = prepared_invoice(db)
    submit_for_approval(db, invoice, "manager")
    decide(db, assignments[0], ApprovalAction.APPROVE, "approver-1", None)
    artifact = prepare_approved_pdf_artifact(db, invoice, Settings())
    old_revision = invoice.current_revision_number
    update_invoice_data(db, invoice, {"total_amount": "122.00"}, "manager")
    assert invoice.current_revision_number == old_revision + 1
    assert invoice.status == InvoiceStatus.NEEDS_REVIEW
    decision = db.scalar(select(ApprovalDecision).where(ApprovalDecision.assignment_id == assignments[0].id))
    assert decision is not None and not decision.valid and decision.invalidated_at is not None
    assert artifact.status == ApprovedPdfStatus.HISTORICAL


def test_three_cost_centres_are_approved_in_parallel(db: Session) -> None:
    invoice = create_invoice(db, 303, "system")
    invoice.document_type = DocumentType.RECEIVED_INVOICE
    invoice.isdoc_status = IsdocStatus.NOT_PRESENT
    invoice.extraction_source = ExtractionSource.OCR_AI
    update_invoice_data(
        db,
        invoice,
        {
            "supplier_name": "Více středisek s.r.o.",
            "ico": "27082440",
            "invoice_number": "MULTI-1",
            "issue_date": "2026-08-01",
            "due_date": "2026-08-15",
            "currency": "CZK",
            "total_amount": "121000.00",
            "vat_breakdown": [],
        },
        "manager",
    )
    transition(db, invoice, InvoiceStatus.VALIDATION, "system")
    amounts = (Decimal("60500"), Decimal("36300"), Decimal("24200"))
    assignments = []
    for index, (code, amount) in enumerate(zip(("IT", "GIS", "PROVOZ"), amounts, strict=True), start=1):
        centre = CostCenter(code=code, name=code, pohoda_code=code)
        db.add(centre)
        db.flush()
        allocation = Allocation(
            invoice_id=invoice.id,
            revision_id=invoice.current_revision.id,
            cost_center_id=centre.id,
            amount=amount,
        )
        db.add(allocation)
        db.flush()
        assignment = ApprovalAssignment(
            invoice_id=invoice.id,
            revision_id=invoice.current_revision.id,
            allocation_id=allocation.id,
            approver_subject=f"approver-{index}",
        )
        db.add(
            UserIdentity(
                subject=f"approver-{index}",
                username=f"approver-{index}",
                roles=["APPROVER"],
                active=True,
            )
        )
        db.add(assignment)
        assignments.append(assignment)
    db.flush()
    run_validations(db, invoice)
    transition(db, invoice, InvoiceStatus.QUEUE_REVIEW, "system")
    confirm_original(db, invoice, "manager")
    submit_for_approval(db, invoice, "manager")
    for assignment in assignments[:-1]:
        decide(db, assignment, ApprovalAction.APPROVE, assignment.approver_subject, None)
        assert invoice.status == InvoiceStatus.AWAITING_APPROVAL
    decide(db, assignments[-1], ApprovalAction.APPROVE, "approver-3", None)
    assert invoice.status == InvoiceStatus.APPROVED

    old_decisions = list(
        db.scalars(
            select(ApprovalDecision).where(
                ApprovalDecision.revision_id != "",
                ApprovalDecision.valid.is_(True),
            )
        ).all()
    )
    fork_revision(db, invoice, "manager", "Změna allocation amount")
    assert invoice.status == InvoiceStatus.NEEDS_REVIEW
    assert all(not decision.valid for decision in old_decisions)
