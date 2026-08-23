from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import ROLE_QUEUE_MANAGER, require_csrf, require_roles
from app.db import get_db
from app.models import CostCenter
from app.schemas import CostCenterIn, CostCenterOut, CurrentUser

router = APIRouter(prefix="/cost-centers", tags=["cost-centers"])


@router.get("", response_model=list[CostCenterOut])
def list_cost_centers(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_roles(ROLE_QUEUE_MANAGER)),
) -> list[CostCenter]:
    query = select(CostCenter).order_by(CostCenter.code)
    if not include_inactive:
        query = query.where(CostCenter.active.is_(True))
    return list(db.scalars(query).all())


@router.post("", response_model=CostCenterOut, status_code=201)
def create_cost_center(
    payload: CostCenterIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
) -> CostCenter:
    if "QUEUE_MANAGER" not in user.roles:
        raise HTTPException(status_code=403, detail="QUEUE_MANAGER role required")
    row = CostCenter(**payload.model_dump())
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Cost center code or POHODA code already exists") from exc
    db.refresh(row)
    return row


@router.put("/{cost_center_id}", response_model=CostCenterOut)
def update_cost_center(
    cost_center_id: str,
    payload: CostCenterIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
) -> CostCenter:
    if "QUEUE_MANAGER" not in user.roles:
        raise HTTPException(status_code=403, detail="QUEUE_MANAGER role required")
    row = db.get(CostCenter, cost_center_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Cost center not found")
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Cost center code or POHODA code already exists") from exc
    db.refresh(row)
    return row
