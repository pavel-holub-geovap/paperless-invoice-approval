from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
from authlib.jose import JsonWebKey, jwt
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_db
from app.models import OidcSession
from app.schemas import CurrentUser

SESSION_COOKIE = "pia_session"
STATE_COOKIE = "pia_oidc_state"
ROLE_QUEUE_MANAGER = "QUEUE_MANAGER"
ROLE_APPROVER = "APPROVER"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_state(payload: dict[str, str | int], settings: Settings) -> str:
    encoded = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(
        settings.app_secret_key.get_secret_value().encode(), encoded.encode(), hashlib.sha256
    ).digest()
    return f"{encoded}.{_b64(signature)}"


def verify_state(token: str, settings: Settings, max_age: int = 600) -> dict[str, str | int]:
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(
            settings.app_secret_key.get_secret_value().encode(), encoded.encode(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _unb64(signature)):
            raise ValueError("signature")
        payload = json.loads(_unb64(encoded))
        if int(payload["iat"]) + max_age < int(time.time()):
            raise ValueError("expired")
        return payload
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired OIDC state") from exc


def authorization_url(settings: Settings, redirect_uri: str, state: str, nonce: str) -> str:
    query = urlencode(
        {
            "client_id": settings.keycloak_client_id,
            "response_type": "code",
            "scope": "openid profile email",
            "redirect_uri": redirect_uri,
            "state": state,
            "nonce": nonce,
        }
    )
    return f"{settings.oidc_issuer_public}/protocol/openid-connect/auth?{query}"


async def exchange_and_validate_code(
    settings: Settings, code: str, redirect_uri: str, expected_nonce: str
) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=20) as client:
        token_response = await client.post(
            f"{settings.oidc_issuer_internal}/protocol/openid-connect/token",
            data={
                "grant_type": "authorization_code",
                "client_id": settings.keycloak_client_id,
                "client_secret": settings.keycloak_client_secret.get_secret_value(),
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        token_response.raise_for_status()
        token_payload = token_response.json()
        jwks_response = await client.get(
            f"{settings.oidc_issuer_internal}/protocol/openid-connect/certs"
        )
        jwks_response.raise_for_status()
    key_set = JsonWebKey.import_key_set(jwks_response.json())
    claims = jwt.decode(
        token_payload["id_token"],
        key_set,
        claims_options={
            "iss": {"essential": True, "value": settings.oidc_issuer_public},
            "aud": {"essential": True, "value": settings.keycloak_client_id},
        },
    )
    claims.validate()
    if claims.get("nonce") != expected_nonce:
        raise HTTPException(status_code=400, detail="OIDC nonce mismatch")
    return dict(claims)


def roles_from_claims(claims: dict[str, object], client_id: str) -> list[str]:
    realm_access = claims.get("realm_access") or {}
    resource_access = claims.get("resource_access") or {}
    groups = claims.get("groups") or []
    realm_roles = realm_access.get("roles", []) if isinstance(realm_access, dict) else []
    client = resource_access.get(client_id, {}) if isinstance(resource_access, dict) else {}
    client_roles = client.get("roles", []) if isinstance(client, dict) else []
    group_roles = [str(group).removeprefix("/") for group in groups] if isinstance(groups, list) else []
    return sorted({str(role) for role in [*realm_roles, *client_roles, *group_roles]})


def get_current_user(
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    db: Session = Depends(get_db),
) -> CurrentUser:
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    oidc_session = db.get(OidcSession, session_id)
    if oidc_session is None or oidc_session.expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    user = oidc_session.user
    if not user.active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is disabled")
    return CurrentUser(
        subject=user.subject,
        username=user.username,
        email=user.email,
        roles=user.roles,
        csrf_token=oidc_session.csrf_token,
    )


def require_roles(*roles: str):
    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not set(roles).intersection(user.roles):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return dependency


def require_csrf_roles(*roles: str):
    def dependency(user: CurrentUser = Depends(require_csrf)) -> CurrentUser:
        if not set(roles).intersection(user.roles):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return dependency


def require_csrf(
    request: Request,
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not csrf_token or not hmac.compare_digest(csrf_token, user.csrf_token or ""):
            raise HTTPException(status_code=403, detail="Invalid CSRF token")
    return user


def new_session_id() -> str:
    return secrets.token_urlsafe(32)
