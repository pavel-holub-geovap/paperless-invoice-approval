from __future__ import annotations

from sqlalchemy.orm import Session

from app.auth import roles_from_claims
from app.models import UserIdentity


class UnsupportedApplicationRole(ValueError):
    pass


def synchronize_oidc_identity(
    db: Session,
    claims: dict[str, object],
    client_id: str,
) -> UserIdentity:
    """Create or refresh an identity projection from one validated OIDC token."""
    roles = roles_from_claims(claims, client_id)
    if not roles:
        raise UnsupportedApplicationRole("User has no supported application role")
    subject = str(claims["sub"])
    username = str(claims.get("preferred_username") or subject)
    email = str(claims["email"]) if claims.get("email") else None
    user = db.get(UserIdentity, subject)
    if user is None:
        user = UserIdentity(subject=subject, username=username, email=email, roles=roles)
        db.add(user)
    else:
        user.username = username
        user.email = email
        user.roles = roles
    db.flush()
    return user
