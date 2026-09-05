from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from app.api.routes import uploads as upload_routes
from app.config import Settings
from app.integrations.paperless import PaperlessDocument, PaperlessUnavailable
from app.models import AuditEvent, DocumentUpload, DocumentUploadStatus
from app.schemas import CurrentUser
from app.services.uploads import (
    link_upload_to_document,
    mark_submission_accepted,
    mark_upload_failed,
    prepare_upload,
    safe_filename,
    sync_uploaded_document,
)

PDF = b"%PDF-1.7\nsynthetic upload test\n%%EOF"


def manager() -> CurrentUser:
    return CurrentUser(
        subject="manager-subject",
        username="queue-manager",
        roles=["QUEUE_MANAGER"],
        csrf_token="csrf",
    )


def approver() -> CurrentUser:
    return CurrentUser(
        subject="approver-subject",
        username="approver1",
        roles=["APPROVER"],
        csrf_token="csrf",
    )


def file(name: str = "invoice.pdf", content: bytes = PDF, mime: str = "application/pdf"):
    return UploadFile(
        filename=name,
        file=BytesIO(content),
        headers=Headers({"content-type": mime}),
    )


@pytest.mark.asyncio
async def test_queue_manager_upload_pdf_is_accepted(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def accepted(db: Session, upload: DocumentUpload, _: bytes, __: Settings) -> None:
        mark_submission_accepted(db, upload, "task-accepted")

    monkeypatch.setattr(upload_routes, "submit_upload", accepted)
    result = await upload_routes.upload_invoice(
        document=file(),
        idempotency_key="upload-test-accepted",
        db=db,
        user=manager(),
        settings=Settings(upload_max_bytes=1024),
    )

    assert result["status"] == "PAPERLESS_PROCESSING"
    assert result["paperless_task_id"] == "task-accepted"
    assert result["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert db.scalar(select(func.count()).select_from(DocumentUpload)) == 1


@pytest.mark.asyncio
async def test_approver_upload_uses_the_same_pipeline_with_provenance(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def accepted(db: Session, upload: DocumentUpload, _: bytes, __: Settings) -> None:
        mark_submission_accepted(db, upload, "task-approver")

    monkeypatch.setattr(upload_routes, "submit_upload", accepted)
    result = await upload_routes.upload_invoice(
        document=file(),
        idempotency_key="upload-test-approver",
        db=db,
        user=approver(),
        settings=Settings(upload_max_bytes=1024),
    )
    assert result["status"] == "PAPERLESS_PROCESSING"
    assert result["upload_origin"] == "APPROVER"
    assert db.scalar(select(DocumentUpload.actor_role)) == "APPROVER"


@pytest.mark.asyncio
async def test_non_pdf_is_rejected_and_audited(db: Session) -> None:
    with pytest.raises(HTTPException) as caught:
        await upload_routes.upload_invoice(
            document=file("notes.txt", b"not a PDF", "text/plain"),
            idempotency_key="upload-test-non-pdf",
            db=db,
            user=manager(),
            settings=Settings(upload_max_bytes=1024),
        )
    assert caught.value.status_code == 415
    event = db.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "DOCUMENT_UPLOAD_FAILED")
    )
    assert event is not None
    assert event.metadata_json["error_code"] == "UNSUPPORTED_FILE_TYPE"


@pytest.mark.asyncio
async def test_oversize_pdf_is_rejected(db: Session) -> None:
    with pytest.raises(HTTPException) as caught:
        await upload_routes.upload_invoice(
            document=file(content=PDF + b"x" * 100),
            idempotency_key="upload-test-oversize",
            db=db,
            user=manager(),
            settings=Settings(upload_max_bytes=len(PDF)),
        )
    assert caught.value.status_code == 413
    assert caught.value.detail["code"] == "FILE_TOO_LARGE"


@pytest.mark.asyncio
async def test_paperless_unavailable_is_controlled_and_retryable(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unavailable(db: Session, upload: DocumentUpload, _: bytes, __: Settings) -> None:
        mark_upload_failed(db, upload, PaperlessUnavailable("Paperless unavailable"))

    monkeypatch.setattr(upload_routes, "submit_upload", unavailable)
    result = await upload_routes.upload_invoice(
        document=file(),
        idempotency_key="upload-test-unavailable",
        db=db,
        user=manager(),
        settings=Settings(upload_max_bytes=1024),
    )
    assert result["status"] == "FAILED_RETRYABLE"
    assert result["error_code"] == "PAPERLESS_UNAVAILABLE"
    assert result["retryable"] is True


def test_successful_paperless_upload_creates_tracking_invoice_and_audit(db: Session) -> None:
    upload, _ = prepare_upload(
        db,
        user=manager(),
        idempotency_key="upload-test-tracking",
        filename="tracking.pdf",
        file_size=len(PDF),
        mime_type="application/pdf",
        sha256=hashlib.sha256(PDF).hexdigest(),
    )
    mark_submission_accepted(db, upload, "task-tracking")
    invoice = link_upload_to_document(db, upload, 731)
    document = PaperlessDocument(
        id=731,
        title="Tracking invoice",
        content="OCR complete",
        created_at=datetime(2026, 8, 26, 8, 15, tzinfo=UTC),
        tags=(1,),
        tag_names=("Přijatá faktura",),
        correspondent=None,
        correspondent_name=None,
        original_filename="tracking.pdf",
    )
    synced = sync_uploaded_document(db, upload, document)

    assert synced.id == invoice.id
    assert upload.status == DocumentUploadStatus.OCR_COMPLETE
    assert upload.invoice_id == invoice.id
    assert invoice.paperless_document_id == 731
    assert invoice.paperless_ocr_text == "OCR complete"
    assert invoice.source_pdf_sha256 == hashlib.sha256(PDF).hexdigest()
    assert invoice.uploaded_by_username == "queue-manager"
    events = list(db.scalars(select(AuditEvent).order_by(AuditEvent.created_at)))
    assert sum(event.event_type == "DOCUMENT_UPLOADED_TO_PAPERLESS" for event in events) == 1
    upload_event = next(
        event for event in events if event.event_type == "DOCUMENT_UPLOADED_TO_PAPERLESS"
    )
    assert upload_event.metadata_json["paperless_document_id"] == 731
    assert "content" not in upload_event.metadata_json


def test_duplicate_retry_reuses_one_tracking_record(db: Session) -> None:
    arguments = {
        "user": manager(),
        "idempotency_key": "upload-test-idempotent",
        "filename": "same.pdf",
        "file_size": len(PDF),
        "mime_type": "application/pdf",
        "sha256": hashlib.sha256(PDF).hexdigest(),
    }
    first, should_submit = prepare_upload(db, **arguments)
    assert should_submit
    mark_upload_failed(db, first, PaperlessUnavailable("connect failed"))

    retried, should_retry = prepare_upload(db, **arguments)
    duplicate, should_duplicate = prepare_upload(db, **arguments)

    assert retried.id == first.id == duplicate.id
    assert should_retry is True
    assert should_duplicate is False
    assert retried.retry_count == 1
    assert db.scalar(select(func.count()).select_from(DocumentUpload)) == 1


def test_filename_is_never_used_as_an_unsafe_path() -> None:
    cleaned = safe_filename("../folder\\evil\x00name.pdf")
    assert cleaned == "_folder_evil_name.pdf"
    assert "/" not in cleaned and "\\" not in cleaned and ".." not in cleaned
    assert len(safe_filename("x" * 500 + ".pdf")) <= 184
