from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.auth import ROLE_APPROVER, require_csrf_roles, require_roles
from app.config import Settings, get_settings
from app.db import get_db
from app.integrations.paperless import PaperlessClient, PaperlessError
from app.models import (
    Allocation,
    ApprovalAssignment,
    ApprovalAssignmentStatus,
    Invoice,
    InvoiceDisposition,
    InvoiceStatus,
    SourceDocumentStatus,
)
from app.schemas import ApprovalRequest, CurrentUser
from app.services.approver_history import (
    get_approver_history_detail,
    list_approver_history,
    user_can_access_invoice_history,
)
from app.services.section_permissions import has_section_permission
from app.services.workflow import WorkflowError, decide

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("/history")
async def my_history(
    q: str | None = Query(default=None, max_length=200),
    decision: Literal["APPROVE", "RETURN", "REJECT", "NONE"] | None = None,
    date_from: date | None = None,
    cost_center: str | None = Query(default=None, max_length=50),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(ROLE_APPROVER)),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    query = q.strip() if q and q.strip() else None
    paperless_document_ids: set[int] = set()
    if query:
        client = PaperlessClient(settings)
        try:
            paperless_document_ids = await client.search_document_ids(query)
        except PaperlessError as exc:
            raise HTTPException(
                status_code=502, detail="Paperless fulltext is temporarily unavailable"
            ) from exc
        finally:
            await client.close()
    return list_approver_history(
        db,
        user.subject,
        page=page,
        page_size=page_size,
        query=query,
        paperless_document_ids=paperless_document_ids,
        decision=decision,
        date_from=date_from,
        cost_center=cost_center,
    )


@router.get("/history/{invoice_id}")
def my_history_detail(
    invoice_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(ROLE_APPROVER)),
) -> dict[str, Any]:
    if not user_can_access_invoice_history(db, user.subject, invoice_id):
        invoice_exists = db.scalar(select(Invoice.id).where(Invoice.id == invoice_id))
        if invoice_exists is None:
            raise HTTPException(status_code=404, detail="Invoice not found")
        raise HTTPException(status_code=403, detail="Invoice is not in this approver's history")
    try:
        detail = get_approver_history_detail(db, user.subject, invoice_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return detail


@router.get("/mine")
def my_approvals(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(ROLE_APPROVER)),
) -> list[dict[str, Any]]:
    assignments = db.scalars(
        select(ApprovalAssignment)
        .options(
            selectinload(ApprovalAssignment.allocation).selectinload(Allocation.cost_center),
            selectinload(ApprovalAssignment.decisions),
        )
        .where(
            ApprovalAssignment.approver_subject == user.subject,
            ApprovalAssignment.active.is_(True),
            ApprovalAssignment.status == ApprovalAssignmentStatus.PENDING,
        )
        .order_by(ApprovalAssignment.created_at.desc())
    ).all()
    result: list[dict[str, Any]] = []
    for assignment in assignments:
        invoice = db.get(Invoice, assignment.invoice_id)
        revision = invoice.current_revision
        pre_review = (
            invoice.uploaded_by_subject == user.subject
            and invoice.status in {InvoiceStatus.NEEDS_REVIEW, InvoiceStatus.QUEUE_REVIEW}
            and revision.queue_manager_reviewed_at is None
        )
        if (
            (invoice.status != InvoiceStatus.AWAITING_APPROVAL and not pre_review)
            or invoice.disposition != InvoiceDisposition.ACTIVE
            or invoice.source_status != SourceDocumentStatus.AVAILABLE
            or assignment.revision_id != revision.id
            or not has_section_permission(db, user.subject, assignment.allocation.cost_center_id)
        ):
            continue
        valid_decision = next((row for row in reversed(assignment.decisions) if row.valid), None)
        result.append(
            {
                "id": assignment.id,
                "invoice_id": invoice.id,
                "invoice_status": invoice.status,
                "revision": revision.number,
                "supplier_name": revision.data.get("supplier_name"),
                "invoice_number": revision.data.get("invoice_number"),
                "invoice_total": revision.data.get("total_amount"),
                "currency": revision.data.get("currency"),
                "cost_center": assignment.allocation.cost_center.code,
                "allocation_amount": assignment.allocation.amount,
                "allocation_percentage": assignment.allocation.percentage,
                "allocation_note": assignment.allocation.note,
                "invoice_data": revision.data,
                "assignment_status": assignment.status,
                "decision": valid_decision.action if valid_decision else None,
                "comment": valid_decision.comment if valid_decision else None,
                "current": True,
                "pre_review": pre_review,
            }
        )
    return result


@router.post("/{assignment_id}/decision")
def make_decision(
    assignment_id: str,
    payload: ApprovalRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf_roles(ROLE_APPROVER)),
) -> dict[str, Any]:
    assignment = db.scalar(select(ApprovalAssignment).where(ApprovalAssignment.id == assignment_id))
    if assignment is None:
        raise HTTPException(status_code=404, detail="Approval assignment not found")
    if assignment.approver_subject != user.subject:
        raise HTTPException(status_code=403, detail="Approval assignment belongs to another user")
    try:
        decision = decide(db, assignment, payload.action, user.subject, payload.comment)
        db.commit()
    except WorkflowError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="The assignment was decided concurrently") from exc
    return {
        "id": decision.id,
        "assignment_id": decision.assignment_id,
        "action": decision.action,
        "comment": decision.comment,
        "created_at": decision.created_at,
    }
