from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.routes.approvals import make_decision
from app.api.routes.invoices import get_invoice, set_allocations
from app.models import (
    Allocation,
    ApprovalAction,
    ApprovalAssignment,
    ApprovalAssignmentStatus,
    ApprovalDecision,
    ApproverSectionPermission,
    AuditEvent,
    CostCenter,
    DocumentType,
    ExtractionSource,
    InvoiceStatus,
    IsdocStatus,
    UserIdentity,
    ValidationResult,
    ValidationSeverity,
)
from app.schemas import AllocationInput, AllocationSet, ApprovalRequest, CurrentUser
from app.services.approval_setup import replace_allocations, replace_approvers
from app.services.cost_centers import create_cost_center, update_cost_center
from app.services.validation import run_validations, validate_invoice_data
from app.services.workflow import (
    WorkflowError,
    confirm_original,
    create_invoice,
    decide,
    reopen,
    submit_for_approval,
    transition,
    update_invoice_data,
)


def manager() -> CurrentUser:
    return CurrentUser(subject="manager", username="manager", roles=["QUEUE_MANAGER"])


def approver(subject: str) -> CurrentUser:
    return CurrentUser(subject=subject, username=subject, roles=["APPROVER"])


def base_invoice(db, paperless_id: int = 8001):
    invoice = create_invoice(db, paperless_id)
    invoice.document_type = DocumentType.RECEIVED_INVOICE
    invoice.isdoc_status = IsdocStatus.NOT_PRESENT
    invoice.extraction_source = ExtractionSource.OCR_AI
    update_invoice_data(
        db,
        invoice,
        {
            "supplier_name": "Dodavatel Etapa E s.r.o.",
            "supplier_ico": "27082440",
            "supplier_dic": "CZ27082440",
            "invoice_number": f"E-{paperless_id}",
            "variable_symbol": str(paperless_id),
            "issue_date": "2026-08-20",
            "taxable_supply_date": "2026-08-20",
            "due_date": "2026-09-03",
            "currency": "CZK",
            "bank_account": "123456789",
            "bank_code": "0800",
            "vat_lines": [
                {"vat_rate": "21", "taxable_base": "1000.00", "vat_amount": "210.00"}
            ],
            "total_without_vat": "1000.00",
            "total_vat": "210.00",
            "total_amount": "1210.00",
        },
        "manager",
    )
    transition(db, invoice, InvoiceStatus.VALIDATION, "system")
    run_validations(db, invoice)
    transition(db, invoice, InvoiceStatus.QUEUE_REVIEW, "system")
    centres = [
        CostCenter(code=code, name=name, pohoda_code=code)
        for code, name in (("100", "Správa"), ("200", "Vývoj"), ("300", "Obchod"))
    ]
    db.add_all(centres)
    for subject in ("approver-1", "approver-2", "approver-3"):
        db.add(UserIdentity(subject=subject, username=subject, roles=["APPROVER"], active=True))
    db.flush()
    for subject in ("approver-1", "approver-2", "approver-3"):
        for centre in centres:
            db.add(
                ApproverSectionPermission(
                    approver_subject=subject,
                    cost_center_id=centre.id,
                    granted_by="manager",
                )
            )
    db.flush()
    return invoice, centres


def configure_full_approval(db, invoice, centres):
    replace_allocations(
        db,
        invoice,
        [
            AllocationInput(cost_center_id=centres[1].id, amount=Decimal("700.00")),
            AllocationInput(cost_center_id=centres[2].id, amount=Decimal("510.00")),
        ],
        "manager",
    )
    allocations = list(
        db.scalars(
            select(Allocation)
            .where(Allocation.revision_id == invoice.current_revision.id, Allocation.active.is_(True))
            .order_by(Allocation.amount.desc())
        )
    )
    replace_approvers(db, invoice, allocations[0], ["approver-1"], "manager")
    replace_approvers(
        db,
        allocations[1].invoice,
        allocations[1],
        ["approver-2", "approver-3"],
        "manager",
    )
    db.flush()
    assignments = list(
        db.scalars(
            select(ApprovalAssignment)
            .where(
                ApprovalAssignment.revision_id == invoice.current_revision.id,
                ApprovalAssignment.active.is_(True),
            )
            .order_by(ApprovalAssignment.approver_subject)
        )
    )
    return allocations, assignments


def test_parallel_three_approver_flow_and_audit(db) -> None:
    invoice, centres = base_invoice(db)
    allocations, assignments = configure_full_approval(db, invoice, centres)
    confirm_original(db, invoice, "manager")
    submit_for_approval(db, invoice, "manager")
    assert invoice.status == InvoiceStatus.AWAITING_APPROVAL
    assert [row.amount for row in allocations] == [Decimal("700.00"), Decimal("510.00")]

    decide(db, assignments[0], ApprovalAction.APPROVE, "approver-1", None)
    assert invoice.status == InvoiceStatus.AWAITING_APPROVAL
    decide(db, assignments[1], ApprovalAction.APPROVE, "approver-2", None)
    assert invoice.status == InvoiceStatus.AWAITING_APPROVAL
    decide(db, assignments[2], ApprovalAction.APPROVE, "approver-3", None)
    assert invoice.status == InvoiceStatus.APPROVED
    events = set(
        db.scalars(select(AuditEvent.event_type).where(AuditEvent.invoice_id == invoice.id)).all()
    )
    assert {"ORIGINAL_REVIEW_CONFIRMED", "SENT_FOR_APPROVAL", "APPROVED"} <= events


def test_submit_rechecks_original_allocation_approver_and_active_role(db) -> None:
    invoice, centres = base_invoice(db)
    with pytest.raises(WorkflowError, match="Originál"):
        submit_for_approval(db, invoice, "manager")

    confirm_original(db, invoice, "manager")
    replace_allocations(
        db,
        invoice,
        [AllocationInput(cost_center_id=centres[0].id, amount=Decimal("1200.00"))],
        "manager",
    )
    with pytest.raises(WorkflowError, match="blokující|Rozúčtování"):
        submit_for_approval(db, invoice, "manager")
    mismatch = db.scalar(
        select(ValidationResult).where(
            ValidationResult.revision_id == invoice.current_revision.id,
            ValidationResult.code == "ALLOCATION_TOTAL_MISMATCH",
        )
    )
    assert mismatch is not None and mismatch.severity == ValidationSeverity.BLOCKING_ERROR

    replace_allocations(
        db,
        invoice,
        [AllocationInput(cost_center_id=centres[0].id, amount=Decimal("1210.00"))],
        "manager",
    )
    allocation = db.scalar(
        select(Allocation).where(
            Allocation.revision_id == invoice.current_revision.id,
            Allocation.active.is_(True),
        )
    )
    with pytest.raises(WorkflowError, match="nemá schvalovatele"):
        submit_for_approval(db, invoice, "manager")
    replace_approvers(db, invoice, allocation, ["approver-1"], "manager")
    db.get(UserIdentity, "approver-1").active = False
    with pytest.raises(WorkflowError, match="neaktivního"):
        submit_for_approval(db, invoice, "manager")


def test_blocking_invoice_validation_prevents_submit(db) -> None:
    invoice, centres = base_invoice(db)
    allocations, _ = configure_full_approval(db, invoice, centres)
    assert allocations
    update_invoice_data(db, invoice, {"currency": "CROWNS"}, "manager")
    confirm_original(db, invoice, "manager")
    with pytest.raises(WorkflowError, match="blokující"):
        submit_for_approval(db, invoice, "manager")


def test_return_reject_comments_and_reopen_history(db) -> None:
    invoice, centres = base_invoice(db)
    _, assignments = configure_full_approval(db, invoice, centres)
    confirm_original(db, invoice, "manager")
    submit_for_approval(db, invoice, "manager")
    with pytest.raises(WorkflowError, match="comment"):
        decide(db, assignments[0], ApprovalAction.RETURN, "approver-1", "")
    with pytest.raises(ValidationError):
        ApprovalRequest(action=ApprovalAction.REJECT, comment="")

    reject = decide(db, assignments[0], ApprovalAction.REJECT, "approver-1", "Plnění odmítnuto")
    assert invoice.status == InvoiceStatus.REJECTED
    assert db.scalar(
        select(AuditEvent.id).where(
            AuditEvent.invoice_id == invoice.id,
            AuditEvent.event_type == "REJECTED",
        )
    )
    with pytest.raises(WorkflowError, match="not awaiting"):
        decide(db, assignments[1], ApprovalAction.APPROVE, "approver-2", None)
    rejected_revision = invoice.current_revision_number
    reopen(db, invoice, "manager", "Nové posouzení")
    assert invoice.status == InvoiceStatus.NEEDS_REVIEW
    assert invoice.current_revision_number == rejected_revision + 1
    assert not reject.valid and reject.invalidated_at is not None
    assert db.scalar(
        select(AuditEvent.id).where(
            AuditEvent.invoice_id == invoice.id,
            AuditEvent.event_type == "REOPENED",
        )
    )


@pytest.mark.parametrize("change_kind", ["amount", "centre", "approver", "invoice_field"])
def test_significant_changes_invalidate_prior_approval(db, change_kind: str) -> None:
    invoice, centres = base_invoice(db)
    allocations, assignments = configure_full_approval(db, invoice, centres)
    confirm_original(db, invoice, "manager")
    submit_for_approval(db, invoice, "manager")
    decision = decide(db, assignments[0], ApprovalAction.APPROVE, "approver-1", None)
    old_assignment = assignments[0]
    old_revision = invoice.current_revision_number

    if change_kind == "amount":
        replace_allocations(
            db,
            invoice,
            [
                AllocationInput(cost_center_id=centres[1].id, amount=Decimal("701.00")),
                AllocationInput(cost_center_id=centres[2].id, amount=Decimal("509.00")),
            ],
            "manager",
        )
    elif change_kind == "centre":
        replace_allocations(
            db,
            invoice,
            [
                AllocationInput(cost_center_id=centres[0].id, amount=Decimal("700.00")),
                AllocationInput(cost_center_id=centres[2].id, amount=Decimal("510.00")),
            ],
            "manager",
        )
    elif change_kind == "approver":
        replace_approvers(db, invoice, allocations[0], ["approver-2"], "manager")
    else:
        update_invoice_data(db, invoice, {"invoice_number": "E-CHANGED"}, "manager")

    assert invoice.current_revision_number == old_revision + 1
    assert invoice.status == InvoiceStatus.NEEDS_REVIEW
    assert not decision.valid and decision.invalidated_at is not None
    assert not old_assignment.active
    assert old_assignment.status == ApprovalAssignmentStatus.INVALIDATED
    assert db.scalar(
        select(AuditEvent.id).where(
            AuditEvent.invoice_id == invoice.id,
            AuditEvent.event_type == "APPROVAL_INVALIDATED",
        )
    )


def test_repeated_approve_is_idempotent(db) -> None:
    invoice, centres = base_invoice(db)
    _, assignments = configure_full_approval(db, invoice, centres)
    confirm_original(db, invoice, "manager")
    submit_for_approval(db, invoice, "manager")
    first = decide(db, assignments[0], ApprovalAction.APPROVE, "approver-1", None)
    second = decide(db, assignments[0], ApprovalAction.APPROVE, "approver-1", None)
    assert second.id == first.id
    assert db.scalar(
        select(func.count(ApprovalDecision.id)).where(
            ApprovalDecision.assignment_id == assignments[0].id,
            ApprovalDecision.valid.is_(True),
        )
    ) == 1


def test_approver_cannot_open_or_decide_foreign_assignment(db) -> None:
    invoice, centres = base_invoice(db)
    _, assignments = configure_full_approval(db, invoice, centres)
    confirm_original(db, invoice, "manager")
    submit_for_approval(db, invoice, "manager")
    with pytest.raises(HTTPException) as view_error:
        get_invoice(invoice.id, db, approver("other-approver"))
    assert view_error.value.status_code == 403
    with pytest.raises(HTTPException) as decision_error:
        make_decision(
            assignments[0].id,
            ApprovalRequest(action=ApprovalAction.APPROVE),
            db,
            approver("other-approver"),
        )
    assert decision_error.value.status_code == 403


def test_cost_center_crud_is_audited(db) -> None:
    row = create_cost_center(
        db,
        {"code": "900", "name": "Test", "pohoda_code": "900", "active": True},
        "manager",
    )
    update_cost_center(
        db,
        row,
        {"code": "900", "name": "Test změna", "pohoda_code": "900", "active": False},
        "manager",
    )
    assert set(db.scalars(select(AuditEvent.event_type)).all()) >= {
        "COST_CENTER_CREATED",
        "COST_CENTER_CHANGED",
    }


def test_allocation_mutation_response_refreshes_relationship(db) -> None:
    invoice, centres = base_invoice(db)
    response = set_allocations(
        invoice.id,
        AllocationSet(
            allocations=[
                AllocationInput(cost_center_id=centres[1].id, amount=Decimal("700.00")),
                AllocationInput(cost_center_id=centres[2].id, amount=Decimal("510.00")),
            ]
        ),
        db,
        manager(),
    )
    assert {row["cost_center"]["code"] for row in response["allocations"]} == {"200", "300"}


def test_payment_details_are_explicitly_validated() -> None:
    incomplete = validate_invoice_data(
        {
            "supplier_name": "A",
            "invoice_number": "1",
            "issue_date": "2026-08-01",
            "currency": "CZK",
            "total_amount": "1",
            "bank_account": "123456",
        }
    )
    assert any(row.code == "DOMESTIC_ACCOUNT_INCOMPLETE" for row in incomplete)
    invalid_iban = validate_invoice_data(
        {
            "supplier_name": "A",
            "invoice_number": "1",
            "issue_date": "2026-08-01",
            "currency": "CZK",
            "total_amount": "1",
            "iban": "CZ0000000000000000000000",
        }
    )
    assert any(row.code == "IBAN_CHECKSUM" for row in invalid_iban)
