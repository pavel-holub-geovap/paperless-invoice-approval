from __future__ import annotations

import hashlib
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import ROLE_QUEUE_MANAGER, require_csrf_roles, require_roles
from app.config import Settings, get_settings
from app.db import get_db
from app.models import DocumentUpload
from app.schemas import CurrentUser
from app.services.audit import record_event
from app.services.uploads import prepare_upload, safe_filename, serialize_upload, submit_upload

router = APIRouter(prefix="/uploads", tags=["uploads"])
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9._:-]{8,100}$")


def _manager(user: CurrentUser) -> None:
    if ROLE_QUEUE_MANAGER not in user.roles:
        raise HTTPException(status_code=403, detail="QUEUE_MANAGER role required")


def _failure_detail(code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "message": message, "retryable": retryable}


def _audit_rejected(
    db: Session,
    user: CurrentUser,
    filename: str,
    mime_type: str,
    code: str,
    message: str,
    file_size: int | None = None,
) -> None:
    record_event(
        db,
        "DOCUMENT_UPLOAD_FAILED",
        actor=user.subject,
        comment=message,
        metadata={
            "filename": filename,
            "file_size": file_size,
            "mime_type": mime_type,
            "error_code": code,
            "error": message,
            "retryable": False,
        },
    )
    db.commit()


@router.get("/config")
def upload_config(
    settings: Settings = Depends(get_settings),
    _: CurrentUser = Depends(require_roles(ROLE_QUEUE_MANAGER)),
) -> dict[str, Any]:
    return {
        "max_file_size": settings.upload_max_bytes,
        "supported_mime_types": ["application/pdf"],
        "supported_extensions": [".pdf"],
        "multi_upload": True,
    }


@router.get("")
def list_uploads(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(ROLE_QUEUE_MANAGER)),
) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(DocumentUpload)
        .where(DocumentUpload.actor_subject == user.subject)
        .order_by(DocumentUpload.created_at.desc())
        .limit(limit)
    ).all()
    return [serialize_upload(db, row) for row in rows]


@router.get("/{upload_id}")
def get_upload(
    upload_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(ROLE_QUEUE_MANAGER)),
) -> dict[str, Any]:
    upload = db.get(DocumentUpload, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    if upload.actor_subject != user.subject:
        raise HTTPException(status_code=403, detail="Upload is not available to this user")
    return serialize_upload(db, upload)


@router.post("", status_code=202)
async def upload_invoice(
    document: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Form()],
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf_roles(ROLE_QUEUE_MANAGER)),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _manager(user)
    original_filename = document.filename or ""
    filename = safe_filename(document.filename)
    mime_type = (document.content_type or "application/octet-stream").lower()
    if not _IDEMPOTENCY.fullmatch(idempotency_key):
        raise HTTPException(
            status_code=400,
            detail=_failure_detail("INVALID_IDEMPOTENCY_KEY", "Neplatný identifikátor uploadu."),
        )
    if not original_filename.lower().endswith(".pdf") or mime_type not in {
        "application/pdf",
        "application/x-pdf",
        "application/octet-stream",
    }:
        message = "Podporovány jsou pouze PDF soubory."
        _audit_rejected(db, user, filename, mime_type, "UNSUPPORTED_FILE_TYPE", message)
        raise HTTPException(
            status_code=415,
            detail=_failure_detail("UNSUPPORTED_FILE_TYPE", message),
        )

    chunks: list[bytes] = []
    digest = hashlib.sha256()
    size = 0
    while chunk := await document.read(1024 * 1024):
        size += len(chunk)
        if size > settings.upload_max_bytes:
            message = f"Soubor překračuje limit {settings.upload_max_bytes} B."
            _audit_rejected(
                db, user, filename, mime_type, "FILE_TOO_LARGE", message, size
            )
            raise HTTPException(
                status_code=413,
                detail=_failure_detail("FILE_TOO_LARGE", message),
            )
        digest.update(chunk)
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content.startswith(b"%PDF-"):
        message = "Soubor nemá platnou PDF signaturu."
        _audit_rejected(db, user, filename, mime_type, "INVALID_PDF", message, size)
        raise HTTPException(status_code=415, detail=_failure_detail("INVALID_PDF", message))

    try:
        upload, should_submit = prepare_upload(
            db,
            user=user,
            idempotency_key=idempotency_key,
            filename=filename,
            file_size=size,
            mime_type="application/pdf",
            sha256=digest.hexdigest(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    if should_submit:
        upload = db.get(DocumentUpload, upload.id)
        assert upload is not None
        await submit_upload(db, upload, content, settings)
        db.commit()
    upload = db.get(DocumentUpload, upload.id)
    assert upload is not None
    return serialize_upload(db, upload)
