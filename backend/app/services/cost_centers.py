from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import CostCenter
from app.services.audit import record_event


def create_cost_center(
    db: Session,
    values: dict[str, Any],
    actor: str,
) -> CostCenter:
    row = CostCenter(**values)
    db.add(row)
    db.flush()
    record_event(
        db,
        "COST_CENTER_CREATED",
        actor=actor,
        new_value={
            "id": row.id,
            "code": row.code,
            "name": row.name,
            "pohoda_code": row.pohoda_code,
            "active": row.active,
        },
    )
    return row


def update_cost_center(
    db: Session,
    row: CostCenter,
    values: dict[str, Any],
    actor: str,
) -> CostCenter:
    old = {
        "code": row.code,
        "name": row.name,
        "pohoda_code": row.pohoda_code,
        "active": row.active,
    }
    for key, value in values.items():
        setattr(row, key, value)
    new = {
        "code": row.code,
        "name": row.name,
        "pohoda_code": row.pohoda_code,
        "active": row.active,
    }
    if old != new:
        record_event(
            db,
            "COST_CENTER_CHANGED",
            actor=actor,
            old_value={"id": row.id, **old},
            new_value={"id": row.id, **new},
        )
    return row
