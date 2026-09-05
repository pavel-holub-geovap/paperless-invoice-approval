from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Allocation,
    ApprovalAssignment,
    ApprovalAssignmentStatus,
    CostCenter,
    Invoice,
    InvoiceStatus,
    ProcessingMode,
    UserIdentity,
)
from app.services.allocations import allocate_by_percentages
from app.services.audit import record_event
from app.services.section_permissions import has_section_permission
from app.services.validation import run_validations
from app.services.workflow import WorkflowError, fork_revision

CENT = Decimal("0.01")
REVISION_SENSITIVE_STATES = {
    InvoiceStatus.AWAITING_APPROVAL,
    InvoiceStatus.RETURNED,
    InvoiceStatus.REJECTED,
    InvoiceStatus.APPROVED,
    InvoiceStatus.XML_READY,
    InvoiceStatus.READY_FOR_EXPORT,
    InvoiceStatus.EXPORT_CREATED,
}


def _invalidate_assignment(assignment: ApprovalAssignment, reason: str) -> None:
    assignment.active = False
    assignment.status = ApprovalAssignmentStatus.INVALIDATED
    assignment.invalidated_at = datetime.now(UTC)
    assignment.invalidation_reason = reason


def replace_allocations(
    db: Session,
    invoice: Invoice,
    items: Sequence[Any],
    actor: str,
    *,
    self_assign_subject: str | None = None,
) -> None:
    revision = invoice.current_revision
    if revision is None:
        raise WorkflowError("Invoice has no revision")
    if invoice.status in REVISION_SENSITIVE_STATES or (
        revision.submitted_to_queue_at is not None
        or revision.queue_manager_reviewed_at is not None
    ):
        revision = fork_revision(db, invoice, actor, "Změna rozúčtování")
    try:
        total = Decimal(str(revision.data.get("total_amount") or "0")).quantize(CENT)
    except Exception as exc:
        raise WorkflowError("Invoice total is not a valid decimal amount") from exc

    percentage_mode = all(item.percentage is not None for item in items)
    amount_mode = all(item.amount is not None for item in items)
    if not (percentage_mode or amount_mode):
        raise WorkflowError("All allocations must use the same input mode")
    if percentage_mode:
        percentages = [item.percentage for item in items]
        if sum(percentages, Decimal("0")) == Decimal("100"):
            amounts = allocate_by_percentages(total, percentages)
        else:
            amounts = [
                (total * percentage / Decimal("100")).quantize(CENT, rounding=ROUND_HALF_UP)
                for percentage in percentages
            ]
    else:
        amounts = [item.amount.quantize(CENT, rounding=ROUND_HALF_UP) for item in items]

    centre_ids = [item.cost_center_id for item in items]
    if len(centre_ids) != len(set(centre_ids)):
        raise WorkflowError("A cost center can occur only once")
    centres = {
        row.id: row
        for row in db.scalars(
            select(CostCenter).where(
                CostCenter.id.in_(centre_ids),
                CostCenter.active.is_(True),
            )
        ).all()
    }
    if set(centre_ids) != set(centres):
        raise WorkflowError("Unknown or inactive cost center")
    if self_assign_subject and any(
        not has_section_permission(db, self_assign_subject, center_id)
        for center_id in centre_ids
    ):
        raise WorkflowError("Schvalovatel smí použít pouze povolené sekce")

    existing = db.scalars(
        select(Allocation).where(
            Allocation.revision_id == revision.id,
            Allocation.active.is_(True),
        )
    ).all()
    old_value = [
        {
            "id": row.id,
            "cost_center": row.cost_center_id,
            "amount": str(row.amount),
            "percentage": str(row.percentage) if row.percentage is not None else None,
        }
        for row in existing
    ]
    for row in existing:
        row.active = False
        for assignment in db.scalars(
            select(ApprovalAssignment).where(
                ApprovalAssignment.allocation_id == row.id,
                ApprovalAssignment.active.is_(True),
            )
        ).all():
            _invalidate_assignment(assignment, "Rozúčtování bylo změněno")
        record_event(
            db,
            "ALLOCATION_REMOVED",
            actor=actor,
            invoice=invoice,
            old_value={"id": row.id, "cost_center": row.cost_center_id, "amount": str(row.amount)},
        )
    db.flush()

    new_value: list[dict[str, Any]] = []
    for item, amount in zip(items, amounts, strict=True):
        vat_breakdown = [
            {
                "rate": str(row.rate),
                "base": str(row.base.quantize(CENT, rounding=ROUND_HALF_UP)),
                "vat": str(row.vat.quantize(CENT, rounding=ROUND_HALF_UP)),
            }
            for row in item.vat_breakdown
        ]
        if vat_breakdown:
            vat_total = sum(
                (Decimal(row["base"]) + Decimal(row["vat"]) for row in vat_breakdown),
                Decimal("0"),
            )
            if abs(vat_total - amount) > CENT:
                raise WorkflowError(
                    "Allocation VAT breakdown must equal the allocation amount within 0.01"
                )
            rates = [row["rate"] for row in vat_breakdown]
            if len(rates) != len(set(rates)):
                raise WorkflowError("An allocation VAT rate can occur only once")
        allocation = Allocation(
            invoice_id=invoice.id,
            revision_id=revision.id,
            cost_center_id=item.cost_center_id,
            amount=amount,
            percentage=item.percentage,
            note=item.note,
            vat_breakdown=vat_breakdown,
            created_by=actor,
        )
        db.add(allocation)
        db.flush()
        if self_assign_subject:
            assignment = ApprovalAssignment(
                invoice_id=invoice.id,
                revision_id=revision.id,
                allocation_id=allocation.id,
                approver_subject=self_assign_subject,
                assigned_by=actor,
            )
            db.add(assignment)
            record_event(
                db,
                "APPROVER_ADDED",
                actor=actor,
                invoice=invoice,
                new_value={
                    "allocation_id": allocation.id,
                    "approver": self_assign_subject,
                    "source": "UPLOADER_SELF_ASSIGNMENT",
                },
            )
        value = {
            "id": allocation.id,
            "cost_center": centres[item.cost_center_id].code,
            "amount": str(amount),
            "percentage": str(item.percentage) if item.percentage is not None else None,
            "note": item.note,
            "vat_breakdown": vat_breakdown,
        }
        new_value.append(value)
        record_event(db, "ALLOCATION_CREATED", actor=actor, invoice=invoice, new_value=value)
    record_event(
        db,
        "ALLOCATION_CHANGED",
        actor=actor,
        invoice=invoice,
        old_value=old_value,
        new_value=new_value,
    )
    run_validations(db, invoice, actor)


def replace_approvers(
    db: Session,
    invoice: Invoice,
    allocation: Allocation,
    subjects: Sequence[str],
    actor: str,
) -> Allocation:
    if invoice.processing_mode != ProcessingMode.FOR_APPROVAL:
        raise WorkflowError(
            "Schvalovatelé mohou být přiřazeni pouze dokladu v režimu FOR_APPROVAL"
        )
    revision = invoice.current_revision
    if invoice.status in REVISION_SENSITIVE_STATES or (
        revision is not None
        and (
            revision.submitted_to_queue_at is not None
            or revision.queue_manager_reviewed_at is not None
        )
    ):
        old_cost_center_id = allocation.cost_center_id
        revision = fork_revision(db, invoice, actor, "Změna seznamu schvalovatelů")
        allocation = db.scalar(
            select(Allocation).where(
                Allocation.revision_id == revision.id,
                Allocation.cost_center_id == old_cost_center_id,
                Allocation.active.is_(True),
            )
        )
        if allocation is None:
            raise WorkflowError("Copied allocation was not found")
    unique_subjects = list(dict.fromkeys(subjects))
    identities = {
        row.subject: row
        for row in db.scalars(
            select(UserIdentity).where(UserIdentity.subject.in_(unique_subjects))
        ).all()
    }
    invalid = [
        subject
        for subject in unique_subjects
        if subject not in identities
        or not identities[subject].active
        or "APPROVER" not in identities[subject].roles
    ]
    if invalid:
        raise WorkflowError("Unknown, inactive, or unauthorized approver")
    if any(
        not has_section_permission(db, subject, allocation.cost_center_id)
        for subject in unique_subjects
    ):
        raise WorkflowError("Schvalovatel nemá oprávnění pro zvolenou sekci")

    rows = db.scalars(
        select(ApprovalAssignment).where(ApprovalAssignment.allocation_id == allocation.id)
    ).all()
    active_by_subject = {row.approver_subject: row for row in rows if row.active}
    any_by_subject = {row.approver_subject: row for row in rows}
    for subject, row in active_by_subject.items():
        if subject not in unique_subjects:
            _invalidate_assignment(row, "Schvalovatel byl odebrán")
            record_event(
                db,
                "APPROVER_REMOVED",
                actor=actor,
                invoice=invoice,
                old_value={"allocation_id": allocation.id, "approver": subject},
            )
    for subject in unique_subjects:
        if subject in active_by_subject:
            continue
        row = any_by_subject.get(subject)
        if row is None:
            row = ApprovalAssignment(
                invoice_id=invoice.id,
                revision_id=invoice.current_revision.id,
                allocation_id=allocation.id,
                approver_subject=subject,
                assigned_by=actor,
            )
            db.add(row)
        else:
            row.active = True
            row.status = ApprovalAssignmentStatus.PENDING
            row.assigned_by = actor
            row.assigned_at = datetime.now(UTC)
            row.decided_at = None
            row.comment = None
            row.invalidated_at = None
            row.invalidation_reason = None
        record_event(
            db,
            "APPROVER_ADDED",
            actor=actor,
            invoice=invoice,
            new_value={"allocation_id": allocation.id, "approver": subject},
        )
    return allocation
