from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import ROLE_APPROVER, ROLE_QUEUE_MANAGER, require_csrf_roles, require_roles
from app.db import get_db
from app.models import ApproverSectionPermission
from app.schemas import CurrentUser, SectionPermissionSet
from app.services.section_permissions import serialize_permission, set_section_permission
from app.services.workflow import WorkflowError

router = APIRouter(prefix="/section-permissions", tags=["section-permissions"])


@router.get("")
def list_section_permissions(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(ROLE_QUEUE_MANAGER, ROLE_APPROVER)),
) -> list[dict[str, object]]:
    query = select(ApproverSectionPermission).options(
        selectinload(ApproverSectionPermission.approver),
        selectinload(ApproverSectionPermission.cost_center),
    )
    if ROLE_QUEUE_MANAGER not in user.roles:
        query = query.where(ApproverSectionPermission.approver_subject == user.subject)
    if not include_inactive:
        query = query.where(ApproverSectionPermission.active.is_(True))
    rows = db.scalars(query.order_by(ApproverSectionPermission.approver_subject)).all()
    return [serialize_permission(row) for row in rows]


@router.put("")
def update_section_permission(
    payload: SectionPermissionSet,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf_roles(ROLE_QUEUE_MANAGER)),
) -> dict[str, object]:
    try:
        row = set_section_permission(
            db,
            approver_subject=payload.approver_subject,
            cost_center_id=payload.cost_center_id,
            active=payload.active,
            actor=user.subject,
        )
        db.commit()
    except WorkflowError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    row = db.scalar(
        select(ApproverSectionPermission)
        .options(
            selectinload(ApproverSectionPermission.approver),
            selectinload(ApproverSectionPermission.cost_center),
        )
        .where(ApproverSectionPermission.id == row.id)
    )
    assert row is not None
    return serialize_permission(row)
