from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import ROLE_APPROVER, require_csrf_roles, require_roles
from app.db import get_db
from app.models import Allocation, ApprovalAssignment, Invoice
from app.schemas import ApprovalRequest, CurrentUser
from app.services.workflow import WorkflowError, decide

router = APIRouter(prefix="/approvals", tags=["approvals"])


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
        )
        .order_by(ApprovalAssignment.created_at.desc())
    ).all()
    result: list[dict[str, Any]] = []
    for assignment in assignments:
        invoice = db.get(Invoice, assignment.invoice_id)
        revision = invoice.current_revision
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
                "decision": valid_decision.action if valid_decision else None,
                "comment": valid_decision.comment if valid_decision else None,
                "current": assignment.revision_id == revision.id,
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
    assignment = db.scalar(
        select(ApprovalAssignment).where(ApprovalAssignment.id == assignment_id).with_for_update()
    )
    if assignment is None:
        raise HTTPException(status_code=404, detail="Approval assignment not found")
    try:
        decision = decide(db, assignment, payload.action, user.subject, payload.comment)
        db.commit()
    except WorkflowError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "id": decision.id,
        "assignment_id": decision.assignment_id,
        "action": decision.action,
        "comment": decision.comment,
        "created_at": decision.created_at,
    }
