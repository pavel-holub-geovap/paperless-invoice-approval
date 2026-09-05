from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApproverSectionPermission, CostCenter, UserIdentity
from app.services.audit import record_event
from app.services.workflow import WorkflowError


def has_section_permission(db: Session, subject: str, cost_center_id: str) -> bool:
    return bool(
        db.scalar(
            select(ApproverSectionPermission.id)
            .join(CostCenter, CostCenter.id == ApproverSectionPermission.cost_center_id)
            .where(
                ApproverSectionPermission.approver_subject == subject,
                ApproverSectionPermission.cost_center_id == cost_center_id,
                ApproverSectionPermission.active.is_(True),
                CostCenter.active.is_(True),
            )
            .limit(1)
        )
    )


def set_section_permission(
    db: Session,
    *,
    approver_subject: str,
    cost_center_id: str,
    active: bool,
    actor: str,
) -> ApproverSectionPermission:
    identity = db.get(UserIdentity, approver_subject)
    if identity is None or not identity.active or "APPROVER" not in identity.roles:
        raise WorkflowError("Unknown, inactive, or unauthorized approver")
    center = db.get(CostCenter, cost_center_id)
    if center is None:
        raise WorkflowError("Unknown section")
    row = db.scalar(
        select(ApproverSectionPermission).where(
            ApproverSectionPermission.approver_subject == approver_subject,
            ApproverSectionPermission.cost_center_id == cost_center_id,
        )
    )
    now = datetime.now(UTC)
    old_active = bool(row and row.active)
    if row is None:
        row = ApproverSectionPermission(
            approver_subject=approver_subject,
            cost_center_id=cost_center_id,
            active=active,
            granted_by=actor,
            granted_at=now,
            revoked_by=None if active else actor,
            revoked_at=None if active else now,
        )
        db.add(row)
    elif row.active != active:
        row.active = active
        if active:
            row.granted_by = actor
            row.granted_at = now
            row.revoked_by = None
            row.revoked_at = None
        else:
            row.revoked_by = actor
            row.revoked_at = now
    if old_active != active:
        record_event(
            db,
            "APPROVER_SECTION_PERMISSION_GRANTED"
            if active
            else "APPROVER_SECTION_PERMISSION_REVOKED",
            actor=actor,
            old_value={"approver_subject": approver_subject, "section_id": cost_center_id, "active": old_active},
            new_value={"approver_subject": approver_subject, "section_id": cost_center_id, "active": active},
        )
    return row


def serialize_permission(row: ApproverSectionPermission) -> dict[str, object]:
    return {
        "id": row.id,
        "approver_subject": row.approver_subject,
        "approver_username": row.approver.username,
        "cost_center": {
            "id": row.cost_center.id,
            "code": row.cost_center.code,
            "name": row.cost_center.name,
            "active": row.cost_center.active,
        },
        "active": row.active,
        "granted_by": row.granted_by,
        "granted_at": row.granted_at,
        "revoked_by": row.revoked_by,
        "revoked_at": row.revoked_at,
    }
