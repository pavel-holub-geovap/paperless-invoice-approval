from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Allocation,
    ApprovalAction,
    ApprovalAssignment,
    ApprovalDecision,
    Invoice,
    InvoiceRevision,
    InvoiceStatus,
    ValidationResult,
    ValidationSeverity,
)
from app.services.audit import record_event


class WorkflowError(ValueError):
    pass


ALLOWED_TRANSITIONS: dict[InvoiceStatus, set[InvoiceStatus]] = {
    InvoiceStatus.NEW: {InvoiceStatus.AI_PROCESSING, InvoiceStatus.VALIDATION},
    InvoiceStatus.AI_PROCESSING: {InvoiceStatus.VALIDATION, InvoiceStatus.NEEDS_REVIEW},
    InvoiceStatus.VALIDATION: {InvoiceStatus.QUEUE_REVIEW, InvoiceStatus.NEEDS_REVIEW},
    InvoiceStatus.QUEUE_REVIEW: {InvoiceStatus.NEEDS_REVIEW, InvoiceStatus.READY_FOR_APPROVAL},
    InvoiceStatus.NEEDS_REVIEW: {InvoiceStatus.VALIDATION, InvoiceStatus.READY_FOR_APPROVAL},
    InvoiceStatus.READY_FOR_APPROVAL: {InvoiceStatus.AWAITING_APPROVAL},
    InvoiceStatus.AWAITING_APPROVAL: {
        InvoiceStatus.RETURNED,
        InvoiceStatus.REJECTED,
        InvoiceStatus.APPROVED,
    },
    InvoiceStatus.RETURNED: {InvoiceStatus.VALIDATION, InvoiceStatus.READY_FOR_APPROVAL},
    InvoiceStatus.REJECTED: {InvoiceStatus.NEEDS_REVIEW},
    InvoiceStatus.APPROVED: {InvoiceStatus.XML_READY, InvoiceStatus.AWAITING_APPROVAL},
    InvoiceStatus.XML_READY: {InvoiceStatus.READY_FOR_EXPORT, InvoiceStatus.AWAITING_APPROVAL},
    InvoiceStatus.READY_FOR_EXPORT: {InvoiceStatus.EXPORT_CREATED, InvoiceStatus.AWAITING_APPROVAL},
    InvoiceStatus.EXPORT_CREATED: {InvoiceStatus.IMPORTED_TO_POHODA, InvoiceStatus.AWAITING_APPROVAL},
    InvoiceStatus.IMPORTED_TO_POHODA: set(),
}

SIGNIFICANT_FIELDS = {
    "supplier_name",
    "supplier_ico",
    "supplier_dic",
    "supplier_address",
    "ico",
    "dic",
    "invoice_number",
    "issue_date",
    "taxable_supply_date",
    "due_date",
    "currency",
    "bank_account",
    "bank_code",
    "iban",
    "swift_bic",
    "vat_lines",
    "vat_breakdown",
    "total_without_vat",
    "total_vat",
    "total_amount",
}

PAPERLESS_TAG_SETTING: dict[InvoiceStatus, str] = {
    InvoiceStatus.AI_PROCESSING: "paperless_tag_processing",
    InvoiceStatus.QUEUE_REVIEW: "paperless_tag_queue_review",
    InvoiceStatus.NEEDS_REVIEW: "paperless_tag_queue_review",
    InvoiceStatus.AWAITING_APPROVAL: "paperless_tag_approval",
    InvoiceStatus.APPROVED: "paperless_tag_approved",
    InvoiceStatus.REJECTED: "paperless_tag_rejected",
    InvoiceStatus.XML_READY: "paperless_tag_pohoda_ready",
    InvoiceStatus.READY_FOR_EXPORT: "paperless_tag_pohoda_ready",
    InvoiceStatus.EXPORT_CREATED: "paperless_tag_exported",
    InvoiceStatus.IMPORTED_TO_POHODA: "paperless_tag_imported",
}


def transition(db: Session, invoice: Invoice, target: InvoiceStatus, actor: str, comment: str | None = None) -> None:
    if target == invoice.status:
        return
    if target not in ALLOWED_TRANSITIONS[invoice.status]:
        raise WorkflowError(f"Transition {invoice.status.value} -> {target.value} is not allowed")
    old = invoice.status
    invoice.status = target
    record_event(
        db,
        "WORKFLOW_TRANSITION",
        actor=actor,
        invoice=invoice,
        old_state=old.value,
        new_state=target.value,
        comment=comment,
    )
    setting_name = PAPERLESS_TAG_SETTING.get(target)
    if setting_name:
        from app.services.jobs import enqueue_job

        enqueue_job(
            db,
            "SYNC_PAPERLESS_STATUS",
            f"paperless-status:{invoice.id}:r{invoice.current_revision_number}:{target.value}",
            invoice_id=invoice.id,
            payload={"target_status": target.value, "tag_setting": setting_name},
        )


def create_invoice(db: Session, paperless_document_id: int, actor: str = "system") -> Invoice:
    existing = db.scalar(select(Invoice).where(Invoice.paperless_document_id == paperless_document_id))
    if existing:
        return existing
    invoice = Invoice(paperless_document_id=paperless_document_id)
    revision = InvoiceRevision(number=1, data={}, created_by=actor)
    invoice.revisions.append(revision)
    db.add(invoice)
    db.flush()
    record_event(
        db,
        "DOCUMENT_DISCOVERED",
        actor=actor,
        invoice=invoice,
        metadata={"paperless_document_id": paperless_document_id},
    )
    return invoice


def update_invoice_data(
    db: Session,
    invoice: Invoice,
    changes: dict[str, Any],
    actor: str,
    comment: str | None = None,
) -> InvoiceRevision:
    current = invoice.current_revision
    if current is None:
        raise WorkflowError("Invoice has no revision")
    unknown = set(changes) - set(current.data) - SIGNIFICANT_FIELDS - {"address", "variable_symbol", "description"}
    if unknown:
        raise WorkflowError(f"Unknown invoice fields: {', '.join(sorted(unknown))}")
    changed = {key: value for key, value in changes.items() if current.data.get(key) != value}
    if not changed:
        return current
    old_values = {key: current.data.get(key) for key in changed}

    has_significant_change = bool(set(changed) & SIGNIFICANT_FIELDS)
    if has_significant_change:
        new_revision = fork_revision(
            db,
            invoice,
            actor,
            f"Významná změna polí: {', '.join(sorted(changed))}",
            data={**current.data, **changed},
        )
        invoice.original_review_confirmed = False
        invoice.original_reviewed_at = None
        invoice.original_reviewed_by = None
    else:
        current.data = {**current.data, **changed}
        new_revision = current

    for field, new_value in changed.items():
        record_event(
            db,
            "FIELD_CHANGED",
            actor=actor,
            invoice=invoice,
            revision_number=new_revision.number,
            old_value={field: old_values[field]},
            new_value={field: new_value},
            comment=comment,
        )
    return new_revision


def fork_revision(
    db: Session,
    invoice: Invoice,
    actor: str,
    reason: str,
    *,
    data: dict[str, Any] | None = None,
) -> InvoiceRevision:
    current = invoice.current_revision
    if current is None:
        raise WorkflowError("Invoice has no revision")
    new_revision = InvoiceRevision(
        invoice=invoice,
        number=invoice.current_revision_number + 1,
        data=data if data is not None else dict(current.data),
        created_by=actor,
    )
    db.add(new_revision)
    db.flush()
    _copy_allocations_and_assignments(db, invoice, current.id, new_revision)
    invoice.current_revision_number = new_revision.number
    invalidate_approvals(db, invoice, actor, reason)
    if invoice.status not in {
        InvoiceStatus.NEW,
        InvoiceStatus.AI_PROCESSING,
        InvoiceStatus.VALIDATION,
        InvoiceStatus.NEEDS_REVIEW,
    }:
        old = invoice.status
        invoice.status = InvoiceStatus.NEEDS_REVIEW
        record_event(
            db,
            "WORKFLOW_TRANSITION",
            actor=actor,
            invoice=invoice,
            old_state=old.value,
            new_state=invoice.status.value,
        )
    return new_revision


def _copy_allocations_and_assignments(
    db: Session, invoice: Invoice, old_revision_id: str, new_revision: InvoiceRevision
) -> None:
    old_allocations = db.scalars(
        select(Allocation).where(Allocation.revision_id == old_revision_id, Allocation.active.is_(True))
    ).all()
    for old in old_allocations:
        new = Allocation(
            invoice_id=invoice.id,
            revision_id=new_revision.id,
            cost_center_id=old.cost_center_id,
            amount=old.amount,
            percentage=old.percentage,
        )
        db.add(new)
        db.flush()
        assignments = db.scalars(
            select(ApprovalAssignment).where(
                ApprovalAssignment.allocation_id == old.id, ApprovalAssignment.active.is_(True)
            )
        ).all()
        for assignment in assignments:
            db.add(
                ApprovalAssignment(
                    invoice_id=invoice.id,
                    revision_id=new_revision.id,
                    allocation_id=new.id,
                    approver_subject=assignment.approver_subject,
                    required=assignment.required,
                )
            )


def invalidate_approvals(db: Session, invoice: Invoice, actor: str, reason: str) -> int:
    decisions = db.scalars(
        select(ApprovalDecision)
        .join(ApprovalAssignment)
        .where(ApprovalAssignment.invoice_id == invoice.id, ApprovalDecision.valid.is_(True))
    ).all()
    now = datetime.now(UTC)
    for decision in decisions:
        decision.valid = False
        decision.invalidated_at = now
        decision.invalidation_reason = reason
    if decisions:
        record_event(
            db,
            "APPROVAL_INVALIDATED",
            actor=actor,
            invoice=invoice,
            comment=reason,
            metadata={"decision_ids": [decision.id for decision in decisions]},
        )
    return len(decisions)


def confirm_original(db: Session, invoice: Invoice, actor: str) -> None:
    invoice.original_review_confirmed = True
    invoice.original_reviewed_at = datetime.now(UTC)
    invoice.original_reviewed_by = actor
    record_event(db, "ORIGINAL_REVIEW_CONFIRMED", actor=actor, invoice=invoice)


def ready_for_approval(db: Session, invoice: Invoice) -> tuple[bool, list[str]]:
    revision = invoice.current_revision
    if revision is None:
        return False, ["Faktura nemá revizi."]
    errors: list[str] = []
    if not invoice.original_review_confirmed or invoice.original_reviewed_at is None:
        errors.append("Originál nebyl zkontrolován.")
    if db.scalar(
        select(ValidationResult.id).where(
            ValidationResult.revision_id == revision.id,
            ValidationResult.severity == ValidationSeverity.BLOCKING_ERROR,
        ).limit(1)
    ):
        errors.append("Faktura má blokující validační chyby.")
    allocations = db.scalars(
        select(Allocation).where(Allocation.revision_id == revision.id, Allocation.active.is_(True))
    ).all()
    total = Decimal(str(revision.data.get("total_amount") or "0"))
    if not allocations:
        errors.append("Faktura nemá rozúčtování.")
    elif abs(sum((row.amount for row in allocations), Decimal("0")) - total) > Decimal("0.01"):
        errors.append("Rozúčtování neodpovídá částce faktury.")
    for allocation in allocations:
        assigned = db.scalar(
            select(ApprovalAssignment.id).where(
                ApprovalAssignment.allocation_id == allocation.id,
                ApprovalAssignment.revision_id == revision.id,
                ApprovalAssignment.active.is_(True),
                ApprovalAssignment.required.is_(True),
            ).limit(1)
        )
        if not assigned:
            errors.append(f"Středisko {allocation.cost_center_id} nemá schvalovatele.")
    return not errors, errors


def submit_for_approval(db: Session, invoice: Invoice, actor: str) -> None:
    if invoice.status == InvoiceStatus.RETURNED:
        fork_revision(db, invoice, actor, "Opětovné předání po RETURN")
        from app.services.validation import run_validations

        run_validations(db, invoice, actor)
    ok, errors = ready_for_approval(db, invoice)
    if not ok:
        raise WorkflowError(" ".join(errors))
    if invoice.status not in {InvoiceStatus.QUEUE_REVIEW, InvoiceStatus.NEEDS_REVIEW, InvoiceStatus.RETURNED, InvoiceStatus.READY_FOR_APPROVAL}:
        raise WorkflowError(f"Invoice cannot be submitted from {invoice.status.value}")
    if invoice.status != InvoiceStatus.READY_FOR_APPROVAL:
        old = invoice.status
        invoice.status = InvoiceStatus.READY_FOR_APPROVAL
        record_event(db, "WORKFLOW_TRANSITION", actor=actor, invoice=invoice, old_state=old.value, new_state=invoice.status.value)
    transition(db, invoice, InvoiceStatus.AWAITING_APPROVAL, actor)


def decide(db: Session, assignment: ApprovalAssignment, action: ApprovalAction, actor: str, comment: str | None) -> ApprovalDecision:
    if actor != assignment.approver_subject:
        raise WorkflowError("Only the assigned approver may decide this task")
    invoice = db.get(Invoice, assignment.invoice_id)
    if invoice is None or invoice.status != InvoiceStatus.AWAITING_APPROVAL:
        raise WorkflowError("Invoice is not awaiting approval")
    if assignment.revision_id != invoice.current_revision.id or not assignment.active:
        raise WorkflowError("Approval assignment does not belong to the current revision")
    if action in {ApprovalAction.RETURN, ApprovalAction.REJECT} and not (comment and comment.strip()):
        raise WorkflowError("RETURN and REJECT require a comment")
    existing = db.scalar(
        select(ApprovalDecision).where(
            ApprovalDecision.assignment_id == assignment.id,
            ApprovalDecision.valid.is_(True),
        )
    )
    if existing:
        raise WorkflowError("This assignment already has a valid decision")

    decision = ApprovalDecision(
        assignment_id=assignment.id,
        revision_id=assignment.revision_id,
        action=action,
        actor_subject=actor,
        comment=comment,
    )
    db.add(decision)
    db.flush()
    record_event(db, action.value if action != ApprovalAction.APPROVE else "APPROVED", actor=actor, invoice=invoice, comment=comment, metadata={"assignment_id": assignment.id, "allocation_id": assignment.allocation_id})

    if action == ApprovalAction.RETURN:
        transition(db, invoice, InvoiceStatus.RETURNED, actor, comment)
    elif action == ApprovalAction.REJECT:
        transition(db, invoice, InvoiceStatus.REJECTED, actor, comment)
    elif all_required_approved(db, invoice):
        transition(db, invoice, InvoiceStatus.APPROVED, actor)
    return decision


def all_required_approved(db: Session, invoice: Invoice) -> bool:
    revision = invoice.current_revision
    if revision is None:
        return False
    assignments = db.scalars(
        select(ApprovalAssignment).where(
            ApprovalAssignment.invoice_id == invoice.id,
            ApprovalAssignment.revision_id == revision.id,
            ApprovalAssignment.active.is_(True),
            ApprovalAssignment.required.is_(True),
        )
    ).all()
    if not assignments:
        return False
    for assignment in assignments:
        approved = db.scalar(
            select(ApprovalDecision.id).where(
                ApprovalDecision.assignment_id == assignment.id,
                ApprovalDecision.revision_id == revision.id,
                ApprovalDecision.action == ApprovalAction.APPROVE,
                ApprovalDecision.valid.is_(True),
            ).limit(1)
        )
        if not approved:
            return False
    return True


def reopen(db: Session, invoice: Invoice, actor: str, comment: str | None = None) -> None:
    if invoice.status != InvoiceStatus.REJECTED:
        raise WorkflowError("Only a rejected invoice can be reopened")
    old = invoice.status
    fork_revision(db, invoice, actor, "Znovuotevření zamítnuté faktury")
    invoice.original_review_confirmed = False
    invoice.original_reviewed_at = None
    invoice.original_reviewed_by = None
    record_event(db, "REOPENED", actor=actor, invoice=invoice, old_state=old.value, new_state=invoice.status.value, comment=comment)
