from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.paperless import (
    PaperlessAuthError,
    PaperlessClient,
    PaperlessError,
    PaperlessNotFound,
    PaperlessSubmissionUnknown,
    PaperlessUnavailable,
    PaperlessValidationError,
)
from app.models import (
    AIExtractionStatus,
    DocumentUpload,
    DocumentUploadStatus,
    Invoice,
    utcnow,
)
from app.request_context import get_correlation_id
from app.schemas import CurrentUser
from app.services.audit import record_event
from app.services.paperless_sync import sync_document_snapshot
from app.services.workflow import create_invoice

_CONTROL_OR_SEPARATOR = re.compile(r"[\x00-\x1f\x7f/\\]+")


def safe_filename(filename: str | None) -> str:
    cleaned = _CONTROL_OR_SEPARATOR.sub("_", (filename or "invoice.pdf").strip())
    cleaned = cleaned.lstrip(". ") or "invoice.pdf"
    if not cleaned.lower().endswith(".pdf"):
        cleaned += ".pdf"
    stem = cleaned[:-4].rstrip(". ") or "invoice"
    return f"{stem[:180]}.pdf"


def prepare_upload(
    db: Session,
    *,
    user: CurrentUser,
    idempotency_key: str,
    filename: str,
    file_size: int,
    mime_type: str,
    sha256: str,
) -> tuple[DocumentUpload, bool]:
    existing = db.scalar(
        select(DocumentUpload).where(DocumentUpload.idempotency_key == idempotency_key)
    )
    if existing:
        if existing.actor_subject != user.subject:
            raise ValueError("Idempotency key is already in use")
        if (
            existing.sha256 != sha256
            or existing.file_size != file_size
            or existing.filename != filename
        ):
            raise ValueError("Idempotency key does not match the original file")
        if existing.status == DocumentUploadStatus.FAILED_RETRYABLE:
            existing.status = DocumentUploadStatus.SUBMITTING
            existing.retryable = False
            existing.error_code = None
            existing.error_message = None
            existing.retry_count += 1
            return existing, True
        return existing, False

    upload = DocumentUpload(
        idempotency_key=idempotency_key,
        actor_subject=user.subject,
        actor_username=user.username,
        filename=filename,
        file_size=file_size,
        mime_type=mime_type,
        sha256=sha256,
        status=DocumentUploadStatus.SUBMITTING,
        correlation_id=get_correlation_id() or idempotency_key,
    )
    db.add(upload)
    db.flush()
    record_event(
        db,
        "DOCUMENT_UPLOAD_REQUESTED",
        actor=user.subject,
        metadata={
            "upload_id": upload.id,
            "filename": filename,
            "file_size": file_size,
            "mime_type": mime_type,
            "sha256": sha256,
            "correlation_id": upload.correlation_id,
        },
    )
    return upload, True


def mark_submission_accepted(db: Session, upload: DocumentUpload, task_id: str) -> None:
    upload.paperless_task_id = task_id
    upload.status = DocumentUploadStatus.PAPERLESS_PROCESSING
    upload.submitted_at = utcnow()
    upload.retryable = False
    upload.error_code = None
    upload.error_message = None


def mark_upload_failed(
    db: Session,
    upload: DocumentUpload,
    error: Exception,
    *,
    actor: str | None = None,
) -> None:
    if isinstance(error, PaperlessSubmissionUnknown):
        status = DocumentUploadStatus.SUBMISSION_UNKNOWN
        code = "PAPERLESS_SUBMISSION_UNKNOWN"
        retryable = False
    elif isinstance(error, PaperlessAuthError):
        status = DocumentUploadStatus.FAILED
        code = "PAPERLESS_AUTH_ERROR"
        retryable = False
    elif isinstance(error, PaperlessValidationError):
        status = DocumentUploadStatus.FAILED
        code = "PAPERLESS_VALIDATION_ERROR"
        retryable = False
    elif isinstance(error, PaperlessUnavailable):
        status = DocumentUploadStatus.FAILED_RETRYABLE
        code = "PAPERLESS_UNAVAILABLE"
        retryable = True
    else:
        status = DocumentUploadStatus.FAILED_RETRYABLE
        code = "PAPERLESS_UNAVAILABLE"
        retryable = True
    upload.status = status
    upload.error_code = code
    upload.error_message = str(error)[:1000]
    upload.retryable = retryable
    record_event(
        db,
        "DOCUMENT_UPLOAD_FAILED",
        actor=actor or upload.actor_subject,
        metadata={
            "upload_id": upload.id,
            "filename": upload.filename,
            "file_size": upload.file_size,
            "mime_type": upload.mime_type,
            "sha256": upload.sha256,
            "correlation_id": upload.correlation_id,
            "error_code": code,
            "error": upload.error_message,
            "retryable": retryable,
        },
    )


async def submit_upload(
    db: Session,
    upload: DocumentUpload,
    content: bytes,
    settings: Settings,
) -> None:
    client = PaperlessClient(settings)
    try:
        tag_id = await client.resolve_tag_id(settings.paperless_inbox_tag)
        task_id = await client.post_document(
            content,
            filename=upload.filename,
            title=upload.filename[:-4],
            tag_id=tag_id,
        )
    except PaperlessError as exc:
        mark_upload_failed(db, upload, exc)
    finally:
        await client.close()
    if "task_id" in locals():
        mark_submission_accepted(db, upload, task_id)


def _safe_task_error(message: str | None) -> str:
    return (message or "Paperless document processing failed")[:1000]


def link_upload_to_document(
    db: Session, upload: DocumentUpload, document_id: int
) -> Invoice:
    invoice = create_invoice(db, document_id, upload.actor_subject)
    invoice.paperless_title = invoice.paperless_title or upload.filename[:-4]
    invoice.paperless_original_filename = (
        invoice.paperless_original_filename or upload.filename
    )
    invoice.source_pdf_sha256 = upload.sha256
    invoice.uploaded_by_subject = upload.actor_subject
    invoice.uploaded_by_username = upload.actor_username
    first_link = upload.paperless_document_id is None
    upload.paperless_document_id = document_id
    upload.invoice_id = invoice.id
    upload.status = DocumentUploadStatus.WAITING_OCR
    if first_link:
        record_event(
            db,
            "DOCUMENT_UPLOADED_TO_PAPERLESS",
            actor=upload.actor_subject,
            invoice=invoice,
            metadata={
                "upload_id": upload.id,
                "filename": upload.filename,
                "file_size": upload.file_size,
                "mime_type": upload.mime_type,
                "sha256": upload.sha256,
                "paperless_task_id": upload.paperless_task_id,
                "paperless_document_id": document_id,
                "correlation_id": upload.correlation_id,
            },
        )
    return invoice


def sync_uploaded_document(
    db: Session,
    upload: DocumentUpload,
    document: Any,
) -> Invoice:
    invoice = link_upload_to_document(db, upload, document.id)
    sync_document_snapshot(db, invoice, document, upload.actor_subject)
    upload.status = (
        DocumentUploadStatus.OCR_COMPLETE
        if document.content.strip()
        else DocumentUploadStatus.WAITING_OCR
    )
    if document.content.strip():
        upload.completed_at = utcnow()
    return invoice


async def poll_pending_uploads(paperless: PaperlessClient) -> None:
    from app.db import SessionLocal

    with SessionLocal() as db:
        upload_ids = list(
            db.scalars(
                select(DocumentUpload.id)
                .where(
                    DocumentUpload.status.in_(
                        [
                            DocumentUploadStatus.PAPERLESS_PROCESSING,
                            DocumentUploadStatus.WAITING_OCR,
                        ]
                    )
                )
                .order_by(DocumentUpload.created_at)
                .limit(50)
            )
        )
    for upload_id in upload_ids:
        with SessionLocal() as db:
            snapshot = db.get(DocumentUpload, upload_id)
            if snapshot is None:
                continue
            task_id = snapshot.paperless_task_id
            document_id = snapshot.paperless_document_id

        if document_id is None:
            if not task_id:
                continue
            task = await paperless.get_task(task_id)
            if task is None or task.status in {"pending", "started", "running"}:
                continue
            if task.status in {"failure", "failed"}:
                with SessionLocal.begin() as db:
                    upload = db.get(DocumentUpload, upload_id)
                    if upload is not None:
                        mark_upload_failed(
                            db,
                            upload,
                            PaperlessValidationError(_safe_task_error(task.error)),
                        )
                continue
            if not task.related_document_ids:
                continue
            document_id = task.related_document_ids[0]
            with SessionLocal.begin() as db:
                upload = db.get(DocumentUpload, upload_id)
                if upload is None or upload.paperless_document_id is not None:
                    continue
                link_upload_to_document(db, upload, document_id)

        try:
            document = await paperless.get_document(document_id)
        except PaperlessNotFound:
            continue
        except PaperlessError:
            continue
        with SessionLocal.begin() as db:
            upload = db.get(DocumentUpload, upload_id)
            if upload is None:
                continue
            sync_uploaded_document(db, upload, document)


def serialize_upload(db: Session, upload: DocumentUpload) -> dict[str, Any]:
    invoice = db.get(Invoice, upload.invoice_id) if upload.invoice_id else None
    display_status = upload.status.value
    ai_status = invoice.ai_status.value if invoice else None
    workflow_status = invoice.status.value if invoice else None
    if upload.status == DocumentUploadStatus.SUBMITTING:
        display_status = "UPLOADING"
    elif upload.status == DocumentUploadStatus.PAPERLESS_PROCESSING:
        display_status = "PAPERLESS_PROCESSING"
    elif upload.status == DocumentUploadStatus.WAITING_OCR:
        display_status = "WAITING_OCR"
    elif upload.status == DocumentUploadStatus.OCR_COMPLETE and invoice:
        if invoice.ai_status in {
            AIExtractionStatus.AI_PENDING,
            AIExtractionStatus.AI_PROCESSING,
        }:
            display_status = "AI_PROCESSING"
        elif invoice.ai_status == AIExtractionStatus.AI_COMPLETED:
            display_status = "READY_FOR_REVIEW"
        elif invoice.ai_status == AIExtractionStatus.AI_FAILED:
            display_status = "ERROR"
        else:
            display_status = "OCR_COMPLETE"
    duplicate = db.scalar(
        select(Invoice.id)
        .where(
            Invoice.source_pdf_sha256 == upload.sha256,
            Invoice.id != upload.invoice_id,
        )
        .order_by(Invoice.created_at)
        .limit(1)
    )
    return {
        "id": upload.id,
        "idempotency_key": upload.idempotency_key,
        "filename": upload.filename,
        "file_size": upload.file_size,
        "mime_type": upload.mime_type,
        "sha256": upload.sha256,
        "status": display_status,
        "tracking_status": upload.status.value,
        "paperless_task_id": upload.paperless_task_id,
        "paperless_document_id": upload.paperless_document_id,
        "invoice_id": upload.invoice_id,
        "ai_status": ai_status,
        "workflow_status": workflow_status,
        "uploaded_by": upload.actor_username,
        "source_created_at": invoice.paperless_created_at if invoice else None,
        "approval_created_at": invoice.created_at if invoice else None,
        "error_code": upload.error_code,
        "error_message": upload.error_message,
        "retryable": upload.retryable,
        "retry_count": upload.retry_count,
        "exact_duplicate_invoice_id": duplicate,
        "created_at": upload.created_at,
        "updated_at": upload.updated_at,
    }
