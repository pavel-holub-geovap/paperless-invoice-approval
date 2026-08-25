from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Allocation,
    ApprovalAction,
    ApprovalAssignment,
    ApprovalAssignmentStatus,
    ApprovalDecision,
    Invoice,
    InvoiceDisposition,
    InvoiceRevision,
    InvoiceStatus,
    SourceDocumentStatus,
    UserIdentity,
    ValidationResult,
    ValidationSeverity,
)
from app.services.audit import record_event
from app.services.bank_accounts import normalize_payment_data


class WorkflowError(ValueError):
    pass


ALLOCATION_TOLERANCE = Decimal("0.01")

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
    "supplier_address_raw",
    "supplier_street",
    "supplier_city",
    "supplier_zip",
    "ico",
    "dic",
    "address",
    "invoice_number",
    "variable_symbol",
    "issue_date",
    "taxable_supply_date",
    "due_date",
    "currency",
    "bank_account",
    "bank_account_raw",
    "bank_account_prefix",
    "bank_account_number",
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
    InvoiceStatus.RETURNED: "paperless_tag_queue_review",
    InvoiceStatus.AWAITING_APPROVAL: "paperless_tag_approval",
    InvoiceStatus.APPROVED: "paperless_tag_approved",
    InvoiceStatus.REJECTED: "paperless_tag_rejected",
    InvoiceStatus.XML_READY: "paperless_tag_pohoda_ready",
    InvoiceStatus.READY_FOR_EXPORT: "paperless_tag_pohoda_ready",
    InvoiceStatus.EXPORT_CREATED: "paperless_tag_exported",
    InvoiceStatus.IMPORTED_TO_POHODA: "paperless_tag_imported",
}


def transition(
    db: Session,
    invoice: Invoice,
    target: InvoiceStatus,
    actor: str,
    comment: str | None = None,
) -> None:
    if target == invoice.status:
        return
    if target not in ALLOWED_TRANSITIONS[invoice.status]:
        raise WorkflowError(f"Transition {invoice.status.value} -> {target.value} is not allowed")
    if target in {
        InvoiceStatus.READY_FOR_APPROVAL,
        InvoiceStatus.AWAITING_APPROVAL,
        InvoiceStatus.APPROVED,
        InvoiceStatus.XML_READY,
        InvoiceStatus.READY_FOR_EXPORT,
        InvoiceStatus.EXPORT_CREATED,
        InvoiceStatus.IMPORTED_TO_POHODA,
    }:
        if invoice.disposition != InvoiceDisposition.ACTIVE:
            raise WorkflowError("Ignored invoice cannot advance in workflow")
        if invoice.source_status == SourceDocumentStatus.MISSING:
            raise WorkflowError("Invoice with a missing Paperless source cannot advance in workflow")
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
    record_event(
        db,
        "REVISION_CREATED",
        actor=actor,
        invoice=invoice,
        revision_number=1,
        comment="Počáteční revize",
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
    allowed = SIGNIFICANT_FIELDS | {"description"}
    unknown = set(changes) - set(current.data) - allowed
    if unknown:
        raise WorkflowError(f"Unknown invoice fields: {', '.join(sorted(unknown))}")
    merged = {**current.data, **changes}
    if set(changes) & {
        "bank_account",
        "bank_account_raw",
        "bank_account_prefix",
        "bank_account_number",
        "bank_code",
        "iban",
        "swift_bic",
    }:
        if set(changes) & {
            "bank_account_raw",
            "bank_account_prefix",
            "bank_account_number",
        } and "bank_account" not in changes:
            prefix = str(merged.get("bank_account_prefix") or "").strip()
            number = str(merged.get("bank_account_number") or "").strip()
            raw = str(merged.get("bank_account_raw") or "").strip()
            merged["bank_account"] = (
                f"{prefix}-{number}" if prefix and number else number or raw or None
            )
        merged = normalize_payment_data(merged)
    changed = {key: value for key, value in merged.items() if current.data.get(key) != value}
    if not changed:
        return current
    old_values = {key: current.data.get(key) for key in changed}

    if set(changed) & SIGNIFICANT_FIELDS:
        new_revision = fork_revision(
            db,
            invoice,
            actor,
            f"Významná změna polí: {', '.join(sorted(changed))}",
            data=merged,
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
            "INVOICE_FIELD_CHANGED",
            actor=actor,
            invoice=invoice,
            revision_number=new_revision.number,
            old_value={field: old_values[field]},
            new_value={field: new_value},
            comment=comment,
            metadata={"entity": "invoice", "field": field},
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
    _copy_allocations_and_assignments(db, invoice, current.id, new_revision, actor)
    invoice.current_revision_number = new_revision.number
    invalidate_approvals(db, invoice, actor, reason, keep_revision_id=new_revision.id)
    record_event(
        db,
        "REVISION_CREATED",
        actor=actor,
        invoice=invoice,
        revision_number=new_revision.number,
        old_value={"revision": current.number},
        new_value={"revision": new_revision.number},
        comment=reason,
    )
    if invoice.status not in {
        InvoiceStatus.NEW,
        InvoiceStatus.AI_PROCESSING,
        InvoiceStatus.VALIDATION,
        InvoiceStatus.NEEDS_REVIEW,
        InvoiceStatus.QUEUE_REVIEW,
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
            comment=reason,
        )
        from app.services.jobs import enqueue_job

        enqueue_job(
            db,
            "SYNC_PAPERLESS_STATUS",
            f"paperless-status:{invoice.id}:r{new_revision.number}:NEEDS_REVIEW",
            invoice_id=invoice.id,
            payload={
                "target_status": InvoiceStatus.NEEDS_REVIEW.value,
                "tag_setting": "paperless_tag_queue_review",
            },
        )
    return new_revision


def _copy_allocations_and_assignments(
    db: Session,
    invoice: Invoice,
    old_revision_id: str,
    new_revision: InvoiceRevision,
    actor: str,
) -> None:
    old_allocations = db.scalars(
        select(Allocation).where(
            Allocation.revision_id == old_revision_id,
            Allocation.active.is_(True),
        )
    ).all()
    for old in old_allocations:
        new = Allocation(
            invoice_id=invoice.id,
            revision_id=new_revision.id,
            cost_center_id=old.cost_center_id,
            amount=old.amount,
            percentage=old.percentage,
            note=old.note,
            vat_breakdown=list(old.vat_breakdown),
            created_by=actor,
        )
        db.add(new)
        db.flush()
        assignments = db.scalars(
            select(ApprovalAssignment).where(
                ApprovalAssignment.allocation_id == old.id,
                ApprovalAssignment.active.is_(True),
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
                    assigned_by=actor,
                )
            )


def invalidate_approvals(
    db: Session,
    invoice: Invoice,
    actor: str,
    reason: str,
    *,
    keep_revision_id: str | None = None,
) -> int:
    decision_query = (
        select(ApprovalDecision)
        .join(ApprovalAssignment)
        .where(
            ApprovalAssignment.invoice_id == invoice.id,
            ApprovalDecision.valid.is_(True),
        )
    )
    assignment_query = select(ApprovalAssignment).where(
        ApprovalAssignment.invoice_id == invoice.id,
        ApprovalAssignment.active.is_(True),
    )
    if keep_revision_id:
        decision_query = decision_query.where(ApprovalAssignment.revision_id != keep_revision_id)
        assignment_query = assignment_query.where(ApprovalAssignment.revision_id != keep_revision_id)
    decisions = db.scalars(decision_query).all()
    assignments = db.scalars(assignment_query).all()
    now = datetime.now(UTC)
    for decision in decisions:
        decision.valid = False
        decision.invalidated_at = now
        decision.invalidation_reason = reason
    for assignment in assignments:
        assignment.active = False
        assignment.status = ApprovalAssignmentStatus.INVALIDATED
        assignment.invalidated_at = now
        assignment.invalidation_reason = reason
    if decisions or assignments:
        record_event(
            db,
            "APPROVAL_INVALIDATED",
            actor=actor,
            invoice=invoice,
            comment=reason,
            metadata={
                "decision_ids": [decision.id for decision in decisions],
                "assignment_ids": [assignment.id for assignment in assignments],
            },
        )
    return len(decisions)


def confirm_original(db: Session, invoice: Invoice, actor: str) -> None:
    from app.services.disposition import ensure_actionable

    ensure_actionable(invoice, "confirm source review")
    invoice.original_review_confirmed = True
    invoice.original_reviewed_at = datetime.now(UTC)
    invoice.original_reviewed_by = actor
    record_event(db, "ORIGINAL_REVIEW_CONFIRMED", actor=actor, invoice=invoice)


def allocation_totals(db: Session, invoice: Invoice) -> tuple[Decimal, Decimal, Decimal]:
    revision = invoice.current_revision
    if revision is None:
        return Decimal("0"), Decimal("0"), Decimal("0")
    try:
        total = Decimal(str(revision.data.get("total_amount") or "0")).quantize(Decimal("0.01"))
    except InvalidOperation:
        total = Decimal("0")
    allocated = sum(
        db.scalars(
            select(Allocation.amount).where(
                Allocation.revision_id == revision.id,
                Allocation.active.is_(True),
            )
        ).all(),
        Decimal("0"),
    ).quantize(Decimal("0.01"))
    return total, allocated, (total - allocated).quantize(Decimal("0.01"))


def ready_for_approval(db: Session, invoice: Invoice) -> tuple[bool, list[str]]:
    revision = invoice.current_revision
    if revision is None:
        return False, ["Faktura nemá revizi."]
    errors: list[str] = []
    if invoice.disposition != InvoiceDisposition.ACTIVE:
        errors.append("Faktura je ignorovaná.")
    if invoice.source_status == SourceDocumentStatus.MISSING:
        errors.append("Zdrojový dokument v Paperless chybí.")
    if not revision.data:
        errors.append("Faktura nemá vytěžená ani ručně doplněná data.")
    if not invoice.original_review_confirmed or invoice.original_reviewed_at is None:
        errors.append("Originál nebyl zkontrolován.")
    if db.scalar(
        select(ValidationResult.id)
        .where(
            ValidationResult.revision_id == revision.id,
            ValidationResult.severity == ValidationSeverity.BLOCKING_ERROR,
        )
        .limit(1)
    ):
        errors.append("Faktura má blokující validační chyby.")
    allocations = db.scalars(
        select(Allocation).where(
            Allocation.revision_id == revision.id,
            Allocation.active.is_(True),
        )
    ).all()
    total, allocated, _ = allocation_totals(db, invoice)
    if not allocations:
        errors.append("Faktura nemá rozúčtování.")
    elif abs(allocated - total) > ALLOCATION_TOLERANCE:
        errors.append("Rozúčtování neodpovídá částce faktury.")
    for allocation in allocations:
        assignments = db.scalars(
            select(ApprovalAssignment).where(
                ApprovalAssignment.allocation_id == allocation.id,
                ApprovalAssignment.revision_id == revision.id,
                ApprovalAssignment.active.is_(True),
                ApprovalAssignment.required.is_(True),
            )
        ).all()
        if not assignments:
            errors.append(f"Středisko {allocation.cost_center_id} nemá schvalovatele.")
            continue
        subjects = {assignment.approver_subject for assignment in assignments}
        valid_users = {
            user.subject
            for user in db.scalars(
                select(UserIdentity).where(
                    UserIdentity.subject.in_(subjects),
                    UserIdentity.active.is_(True),
                )
            ).all()
            if "APPROVER" in user.roles
        }
        if subjects != valid_users:
            errors.append(
                f"Středisko {allocation.cost_center_id} obsahuje neaktivního nebo neoprávněného schvalovatele."
            )
    return not errors, errors


def submit_for_approval(db: Session, invoice: Invoice, actor: str) -> None:
    if invoice.status == InvoiceStatus.RETURNED:
        fork_revision(db, invoice, actor, "Opětovné předání po RETURN")
    from app.services.validation import run_validations

    run_validations(db, invoice, actor)
    db.flush()
    ok, errors = ready_for_approval(db, invoice)
    if not ok:
        raise WorkflowError(" ".join(errors))
    if invoice.status not in {
        InvoiceStatus.QUEUE_REVIEW,
        InvoiceStatus.NEEDS_REVIEW,
        InvoiceStatus.RETURNED,
        InvoiceStatus.READY_FOR_APPROVAL,
    }:
        raise WorkflowError(f"Invoice cannot be submitted from {invoice.status.value}")
    if invoice.status != InvoiceStatus.READY_FOR_APPROVAL:
        transition(db, invoice, InvoiceStatus.READY_FOR_APPROVAL, actor)
    transition(db, invoice, InvoiceStatus.AWAITING_APPROVAL, actor)
    record_event(
        db,
        "SENT_FOR_APPROVAL",
        actor=actor,
        invoice=invoice,
        new_state=InvoiceStatus.AWAITING_APPROVAL.value,
    )


def decide(
    db: Session,
    assignment: ApprovalAssignment,
    action: ApprovalAction,
    actor: str,
    comment: str | None,
) -> ApprovalDecision:
    if actor != assignment.approver_subject:
        raise WorkflowError("Only the assigned approver may decide this task")
    invoice = db.scalar(select(Invoice).where(Invoice.id == assignment.invoice_id).with_for_update())
    if invoice is None:
        raise WorkflowError("Invoice does not exist")
    from app.services.disposition import ensure_actionable

    ensure_actionable(invoice, "be approved")
    assignment = db.scalar(
        select(ApprovalAssignment)
        .where(ApprovalAssignment.id == assignment.id)
        .with_for_update()
    )
    if assignment is None or actor != assignment.approver_subject:
        raise WorkflowError("Approval assignment is not available to this approver")
    existing = db.scalar(
        select(ApprovalDecision).where(
            ApprovalDecision.assignment_id == assignment.id,
            ApprovalDecision.valid.is_(True),
        )
    )
    if existing:
        if existing.action == action and existing.actor_subject == actor:
            return existing
        raise WorkflowError("This assignment already has a valid decision")
    if invoice.status != InvoiceStatus.AWAITING_APPROVAL:
        raise WorkflowError("Invoice is not awaiting approval")
    revision = invoice.current_revision
    if revision is None or assignment.revision_id != revision.id or not assignment.active:
        raise WorkflowError("Approval assignment does not belong to the current revision")
    if assignment.status != ApprovalAssignmentStatus.PENDING:
        raise WorkflowError("Approval assignment is no longer pending")
    if action in {ApprovalAction.RETURN, ApprovalAction.REJECT} and not (comment and comment.strip()):
        raise WorkflowError("RETURN and REJECT require a comment")

    now = datetime.now(UTC)
    decision = ApprovalDecision(
        assignment_id=assignment.id,
        revision_id=assignment.revision_id,
        action=action,
        actor_subject=actor,
        comment=comment,
    )
    assignment.status = {
        ApprovalAction.APPROVE: ApprovalAssignmentStatus.APPROVED,
        ApprovalAction.RETURN: ApprovalAssignmentStatus.RETURNED,
        ApprovalAction.REJECT: ApprovalAssignmentStatus.REJECTED,
    }[action]
    assignment.decided_at = now
    assignment.comment = comment
    db.add(decision)
    db.flush()
    record_event(
        db,
        {
            ApprovalAction.APPROVE: "APPROVED",
            ApprovalAction.RETURN: "RETURNED",
            ApprovalAction.REJECT: "REJECTED",
        }[action],
        actor=actor,
        invoice=invoice,
        comment=comment,
        metadata={"assignment_id": assignment.id, "allocation_id": assignment.allocation_id},
    )

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
    return all(assignment.status == ApprovalAssignmentStatus.APPROVED for assignment in assignments)


def reopen(db: Session, invoice: Invoice, actor: str, comment: str | None = None) -> None:
    if invoice.status != InvoiceStatus.REJECTED:
        raise WorkflowError("Only a rejected invoice can be reopened")
    old = invoice.status
    fork_revision(db, invoice, actor, "Znovuotevření zamítnuté faktury")
    invoice.original_review_confirmed = False
    invoice.original_reviewed_at = None
    invoice.original_reviewed_by = None
    record_event(
        db,
        "REOPENED",
        actor=actor,
        invoice=invoice,
        old_state=old.value,
        new_state=invoice.status.value,
        comment=comment,
    )
