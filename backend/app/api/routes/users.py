from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import UserIdentity
from app.schemas import CurrentUser

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
def list_users(
    role: str | None = None,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> list[dict[str, object]]:
    users = db.scalars(select(UserIdentity).where(UserIdentity.active.is_(True)).order_by(UserIdentity.username)).all()
    if role:
        users = [user for user in users if role in user.roles]
    return [
        {"subject": user.subject, "username": user.username, "email": user.email, "roles": user.roles}
        for user in users
    ]
