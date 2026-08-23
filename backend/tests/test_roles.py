from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.auth import ROLE_APPROVER, ROLE_QUEUE_MANAGER, require_roles
from app.schemas import CurrentUser


def user(*roles: str) -> CurrentUser:
    return CurrentUser(subject="subject", username="user", roles=list(roles))


def test_queue_manager_role_is_accepted() -> None:
    dependency = require_roles(ROLE_QUEUE_MANAGER)
    assert dependency(user(ROLE_QUEUE_MANAGER)).username == "user"


def test_approver_cannot_use_queue_manager_dependency() -> None:
    dependency = require_roles(ROLE_QUEUE_MANAGER)
    with pytest.raises(HTTPException) as exc:
        dependency(user(ROLE_APPROVER))
    assert exc.value.status_code == 403


def test_approver_role_is_accepted() -> None:
    dependency = require_roles(ROLE_APPROVER)
    assert dependency(user(ROLE_APPROVER)).roles == [ROLE_APPROVER]
