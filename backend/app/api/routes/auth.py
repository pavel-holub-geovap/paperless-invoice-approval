from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import (
    SESSION_COOKIE,
    STATE_COOKIE,
    authorization_url,
    exchange_and_validate_code,
    get_current_user,
    new_session_id,
    require_csrf,
    roles_from_claims,
    sign_state,
    verify_state,
)
from app.config import Settings, get_settings
from app.db import get_db
from app.models import OidcSession, UserIdentity
from app.schemas import CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
def login(settings: Settings = Depends(get_settings)) -> Response:
    nonce = secrets.token_urlsafe(24)
    state_value = secrets.token_urlsafe(24)
    signed = sign_state({"state": state_value, "nonce": nonce, "iat": int(time.time())}, settings)
    redirect_uri = f"{settings.app_base_url}/api/auth/callback"
    response = RedirectResponse(authorization_url(settings, redirect_uri, state_value, nonce), status_code=302)
    response.set_cookie(
        STATE_COOKIE,
        signed,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=600,
        path="/api/auth/callback",
    )
    return response


@router.get("/callback")
async def callback(
    request: Request,
    code: str = Query(),
    state: str = Query(),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    state_cookie = request.cookies.get(STATE_COOKIE)
    if not state_cookie:
        raise HTTPException(status_code=400, detail="Missing OIDC state cookie")
    state_payload = verify_state(state_cookie, settings)
    if not secrets.compare_digest(str(state_payload["state"]), state):
        raise HTTPException(status_code=400, detail="OIDC state mismatch")
    redirect_uri = f"{settings.app_base_url}/api/auth/callback"
    claims = await exchange_and_validate_code(settings, code, redirect_uri, str(state_payload["nonce"]))
    subject = str(claims["sub"])
    user = db.get(UserIdentity, subject)
    roles = roles_from_claims(claims, settings.keycloak_client_id)
    if user is None:
        user = UserIdentity(
            subject=subject,
            username=str(claims.get("preferred_username") or subject),
            email=str(claims["email"]) if claims.get("email") else None,
            roles=roles,
        )
        db.add(user)
    else:
        user.username = str(claims.get("preferred_username") or subject)
        user.email = str(claims["email"]) if claims.get("email") else None
        user.roles = roles
    session_id = new_session_id()
    db.add(
        OidcSession(
            id=session_id,
            subject=subject,
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds),
            csrf_token=secrets.token_urlsafe(24),
        )
    )
    db.commit()
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(STATE_COOKIE, path="/api/auth/callback")
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=settings.session_ttl_seconds,
        path="/",
    )
    return response


@router.get("/me", response_model=CurrentUser)
def me(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    return user


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_csrf),
) -> None:
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        session = db.get(OidcSession, session_id)
        if session:
            db.delete(session)
            db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
