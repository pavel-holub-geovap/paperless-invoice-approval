from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, date, datetime, time
from typing import Any, Literal

from sqlalchemy import String, and_, cast, exists, false, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Allocation,
    ApprovalAction,
    ApprovalAssignment,
    ApprovalAssignmentStatus,
    ApprovalDecision,
    CostCenter,
    Invoice,
    InvoiceRevision,
    SourceDocumentStatus,
)

HistoryDecisionFilter = Literal["APPROVE", "RETURN", "REJECT", "NONE"]


def user_can_access_invoice_history(db: Session, user_id: str, invoice_id: str) -> bool:
    """Return whether an approver was ever assigned to any revision of an invoice."""
    return bool(
        db.scalar(
            select(ApprovalAssignment.id)
            .where(
                ApprovalAssignment.invoice_id == invoice_id,
                ApprovalAssignment.approver_subject == user_id,
            )
            .limit(1)
        )
    )


def _assignment_event_time(assignment: ApprovalAssignment) -> datetime:
    decisions = sorted(assignment.decisions, key=lambda row: row.created_at)
    return decisions[-1].created_at if decisions else assignment.assigned_at


def _serialize_assignment(
    assignment: ApprovalAssignment,
    revisions: dict[str, InvoiceRevision] | None = None,
) -> dict[str, Any]:
    decisions = sorted(assignment.decisions, key=lambda row: row.created_at)
    decision = decisions[-1] if decisions else None
    invalidated = (
        not assignment.active
        or assignment.status == ApprovalAssignmentStatus.INVALIDATED
        or bool(decision and not decision.valid)
    )
    row: dict[str, Any] = {
        "assignment_id": assignment.id,
        "revision_id": assignment.revision_id,
        "revision": revisions[assignment.revision_id].number if revisions else None,
        "cost_center": {
            "id": assignment.allocation.cost_center.id,
            "code": assignment.allocation.cost_center.code,
            "name": assignment.allocation.cost_center.name,
        },
        "amount": assignment.allocation.amount,
        "percentage": assignment.allocation.percentage,
        "allocation_note": assignment.allocation.note,
        "assigned_at": assignment.assigned_at,
        "assignment_status": assignment.status,
        "decision": decision.action if decision else None,
        "decision_at": decision.created_at if decision else None,
        "comment": decision.comment if decision else assignment.comment,
        "decision_valid": decision.valid if decision else None,
        "invalidated": invalidated,
        "invalidated_at": (
            decision.invalidated_at if decision and decision.invalidated_at else assignment.invalidated_at
        ),
        "invalidation_reason": (
            decision.invalidation_reason
            if decision and decision.invalidation_reason
            else assignment.invalidation_reason
        ),
        "event_at": _assignment_event_time(assignment),
    }
    if revisions:
        row["revision_data"] = revisions[assignment.revision_id].data
    return row


def _snippet(text: str, query: str, radius: int = 90) -> str | None:
    compact = re.sub(r"\s+", " ", text or "").strip()
    position = compact.casefold().find(query.strip().casefold())
    if position < 0:
        return None
    start = max(0, position - radius)
    end = min(len(compact), position + len(query.strip()) + radius)
    prefix = "…" if start else ""
    suffix = "…" if end < len(compact) else ""
    return f"{prefix}{compact[start:end].strip()}{suffix}"


def _history_filters(
    subject: str,
    *,
    query: str | None,
    paperless_document_ids: set[int],
    decision: HistoryDecisionFilter | None,
    date_from: date | None,
    cost_center: str | None,
) -> list[Any]:
    filters: list[Any] = [
        exists(
            select(1).where(
                ApprovalAssignment.invoice_id == Invoice.id,
                ApprovalAssignment.approver_subject == subject,
            )
        )
    ]
    if query:
        pattern = f"%{query.casefold()}%"
        numeric_query = re.sub(r"\s*(?:kč|czk|eur)\s*$", "", query.casefold()).replace(
            "\u00a0", ""
        ).replace(" ", "").replace(",", ".")
        numeric_pattern = (
            f"%{numeric_query}%"
            if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", numeric_query)
            else None
        )
        structured_revision = exists(
            select(1).where(
                InvoiceRevision.invoice_id == Invoice.id,
                InvoiceRevision.number == Invoice.current_revision_number,
                or_(
                    func.lower(cast(InvoiceRevision.data, String)).like(pattern),
                    (
                        func.lower(cast(InvoiceRevision.data, String)).like(numeric_pattern)
                        if numeric_pattern
                        else false()
                    ),
                ),
            )
        )
        structured_assignment = exists(
            select(1)
            .select_from(ApprovalAssignment)
            .join(Allocation, Allocation.id == ApprovalAssignment.allocation_id)
            .join(CostCenter, CostCenter.id == Allocation.cost_center_id)
            .where(
                ApprovalAssignment.invoice_id == Invoice.id,
                ApprovalAssignment.approver_subject == subject,
                or_(
                    func.lower(CostCenter.code).like(pattern),
                    func.lower(CostCenter.name).like(pattern),
                    func.lower(cast(Allocation.amount, String)).like(pattern),
                    (
                        func.lower(cast(Allocation.amount, String)).like(numeric_pattern)
                        if numeric_pattern
                        else false()
                    ),
                    func.lower(func.coalesce(Allocation.note, "")).like(pattern),
                ),
            )
        )
        paperless_match = (
            Invoice.paperless_document_id.in_(paperless_document_ids)
            if paperless_document_ids
            else false()
        )
        filters.append(
            or_(
                func.lower(Invoice.paperless_title).like(pattern),
                func.lower(func.coalesce(Invoice.paperless_correspondent_name, "")).like(pattern),
                structured_revision,
                structured_assignment,
                paperless_match,
            )
        )
    if decision:
        decision_exists = (
            select(1)
            .select_from(ApprovalAssignment)
            .outerjoin(ApprovalDecision, ApprovalDecision.assignment_id == ApprovalAssignment.id)
            .where(
                ApprovalAssignment.invoice_id == Invoice.id,
                ApprovalAssignment.approver_subject == subject,
            )
        )
        if decision == "NONE":
            decision_exists = decision_exists.where(ApprovalDecision.id.is_(None))
        else:
            decision_exists = decision_exists.where(
                ApprovalDecision.action == ApprovalAction(decision)
            )
        filters.append(exists(decision_exists))
    if date_from:
        boundary = datetime.combine(date_from, time.min, tzinfo=UTC)
        filters.append(
            exists(
                select(1)
                .select_from(ApprovalAssignment)
                .outerjoin(
                    ApprovalDecision,
                    ApprovalDecision.assignment_id == ApprovalAssignment.id,
                )
                .where(
                    ApprovalAssignment.invoice_id == Invoice.id,
                    ApprovalAssignment.approver_subject == subject,
                    or_(
                        ApprovalDecision.created_at >= boundary,
                        and_(
                            ApprovalDecision.id.is_(None),
                            ApprovalAssignment.assigned_at >= boundary,
                        ),
                    ),
                )
            )
        )
    if cost_center:
        filters.append(
            exists(
                select(1)
                .select_from(ApprovalAssignment)
                .join(Allocation, Allocation.id == ApprovalAssignment.allocation_id)
                .join(CostCenter, CostCenter.id == Allocation.cost_center_id)
                .where(
                    ApprovalAssignment.invoice_id == Invoice.id,
                    ApprovalAssignment.approver_subject == subject,
                    CostCenter.code == cost_center,
                )
            )
        )
    return filters


def list_approver_history(
    db: Session,
    subject: str,
    *,
    page: int,
    page_size: int,
    query: str | None = None,
    paperless_document_ids: set[int] | None = None,
    decision: HistoryDecisionFilter | None = None,
    date_from: date | None = None,
    cost_center: str | None = None,
) -> dict[str, Any]:
    paperless_document_ids = paperless_document_ids or set()
    filters = _history_filters(
        subject,
        query=query,
        paperless_document_ids=paperless_document_ids,
        decision=decision,
        date_from=date_from,
        cost_center=cost_center,
    )
    latest_event = (
        select(
            func.max(
                func.coalesce(ApprovalDecision.created_at, ApprovalAssignment.assigned_at)
            )
        )
        .select_from(ApprovalAssignment)
        .outerjoin(ApprovalDecision, ApprovalDecision.assignment_id == ApprovalAssignment.id)
        .where(
            ApprovalAssignment.invoice_id == Invoice.id,
            ApprovalAssignment.approver_subject == subject,
        )
        .correlate(Invoice)
        .scalar_subquery()
    )
    total = int(db.scalar(select(func.count()).select_from(Invoice).where(*filters)) or 0)
    rows = db.execute(
        select(Invoice, latest_event.label("latest_event"))
        .options(selectinload(Invoice.revisions))
        .where(*filters)
        .order_by(latest_event.desc(), Invoice.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    invoice_ids = [invoice.id for invoice, _ in rows]
    assignments_by_invoice: dict[str, list[ApprovalAssignment]] = defaultdict(list)
    if invoice_ids:
        assignments = db.scalars(
            select(ApprovalAssignment)
            .options(
                selectinload(ApprovalAssignment.allocation).selectinload(Allocation.cost_center),
                selectinload(ApprovalAssignment.decisions),
            )
            .where(
                ApprovalAssignment.approver_subject == subject,
                ApprovalAssignment.invoice_id.in_(invoice_ids),
            )
        ).all()
        for assignment in assignments:
            assignments_by_invoice[assignment.invoice_id].append(assignment)

    items: list[dict[str, Any]] = []
    for invoice, event_at in rows:
        assignments = sorted(
            assignments_by_invoice[invoice.id], key=_assignment_event_time, reverse=True
        )
        latest = _serialize_assignment(assignments[0])
        data = invoice.current_revision.data
        items.append(
            {
                "invoice_id": invoice.id,
                "paperless_document_id": invoice.paperless_document_id,
                "invoice_number": data.get("invoice_number") or invoice.paperless_title,
                "supplier_name": data.get("supplier_name")
                or invoice.paperless_correspondent_name,
                "supplier_ico": data.get("supplier_ico") or data.get("ico"),
                "supplier_dic": data.get("supplier_dic") or data.get("dic"),
                "currency": data.get("currency") or "CZK",
                "current_status": invoice.status,
                "current_revision": invoice.current_revision_number,
                "source_status": invoice.source_status,
                "pdf_available": invoice.source_status == SourceDocumentStatus.AVAILABLE,
                "latest_event_at": event_at,
                "latest_assignment": latest,
                "assignment_count": len(assignments),
                "ocr_snippet": (
                    _snippet(invoice.paperless_ocr_text, query)
                    if query
                    and invoice.paperless_document_id in paperless_document_ids
                    else None
                ),
            }
        )
    available_centers = db.execute(
        select(CostCenter.code, CostCenter.name)
        .select_from(ApprovalAssignment)
        .join(Allocation, Allocation.id == ApprovalAssignment.allocation_id)
        .join(CostCenter, CostCenter.id == Allocation.cost_center_id)
        .where(ApprovalAssignment.approver_subject == subject)
        .distinct()
        .order_by(CostCenter.code)
    ).all()
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "filters": {
            "cost_centers": [
                {"code": code, "name": name} for code, name in available_centers
            ]
        },
    }


def get_approver_history_detail(
    db: Session, subject: str, invoice_id: str
) -> dict[str, Any] | None:
    invoice = db.scalar(
        select(Invoice)
        .options(selectinload(Invoice.revisions))
        .where(Invoice.id == invoice_id)
        .execution_options(populate_existing=True)
    )
    if invoice is None:
        return None
    assignments = db.scalars(
        select(ApprovalAssignment)
        .options(
            selectinload(ApprovalAssignment.allocation).selectinload(Allocation.cost_center),
            selectinload(ApprovalAssignment.decisions),
        )
        .where(
            ApprovalAssignment.invoice_id == invoice_id,
            ApprovalAssignment.approver_subject == subject,
        )
    ).all()
    if not assignments:
        raise PermissionError("Invoice has no historical assignment for this approver")
    revisions = {revision.id: revision for revision in invoice.revisions}
    history = sorted(
        (_serialize_assignment(row, revisions) for row in assignments),
        key=lambda row: row["event_at"],
        reverse=True,
    )
    data = invoice.current_revision.data
    return {
        "invoice_id": invoice.id,
        "paperless_document_id": invoice.paperless_document_id,
        "invoice_number": data.get("invoice_number") or invoice.paperless_title,
        "supplier_name": data.get("supplier_name") or invoice.paperless_correspondent_name,
        "currency": data.get("currency") or "CZK",
        "current_status": invoice.status,
        "current_revision": invoice.current_revision_number,
        "current_data": data,
        "source_status": invoice.source_status,
        "pdf_available": invoice.source_status == SourceDocumentStatus.AVAILABLE,
        "paperless": {
            "title": invoice.paperless_title,
            "created_at": invoice.paperless_created_at,
            "correspondent": invoice.paperless_correspondent_name,
            "tags": invoice.paperless_tags,
            "original_filename": invoice.paperless_original_filename,
        },
        "history": history,
    }
