from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user, require_csrf
from app.config import Settings, get_settings
from app.db import get_db
from app.integrations.paperless import PaperlessClient
from app.models import (
    ExportArtifact,
    ExportBatch,
    ExportBatchItem,
    Invoice,
    PohodaResponseUpload,
    SourceDocumentStatus,
)
from app.schemas import CurrentUser, ExportCreate, ExportGenerate, ImportConfirmation
from app.services.audit import record_event
from app.services.exports import (
    create_export_batch,
    generate_export_artifact,
    mark_artifact_imported,
    mark_batch_imported,
    store_pohoda_response,
    validate_immutable_artifact_xml,
)
from app.services.workflow import WorkflowError

router = APIRouter(prefix="/exports", tags=["exports"])


def _manager(user: CurrentUser) -> None:
    if "QUEUE_MANAGER" not in user.roles:
        raise HTTPException(status_code=403, detail="QUEUE_MANAGER role required")


def _artifact_out(row: ExportArtifact) -> dict[str, Any]:
    return {
        "id": row.id,
        "invoice_id": row.invoice_id,
        "revision_id": row.revision_id,
        "source_export_id": row.source_export_id,
        "status": row.status,
        "generator_version": row.generator_version,
        "xsd_bundle_version": row.xsd_bundle_version,
        "encoding": row.encoding,
        "xml_sha256": row.xml_sha256,
        "xml_size": row.xml_size,
        "pdf_sha256": row.pdf_sha256,
        "validation_errors": row.validation_errors,
        "reexport_reason": row.reexport_reason,
        "generated_by": row.generated_by,
        "generated_at": row.generated_at,
        "imported_by": row.imported_by,
        "imported_at": row.imported_at,
        "pohoda_target_ico": row.source_snapshot.get("pohoda_target_ico"),
        "pohoda_target_key_configured": row.source_snapshot.get(
            "pohoda_target_key_configured", False
        ),
        "pohoda_target_validation": row.source_snapshot.get(
            "pohoda_target_validation",
            {"status": "NOT_RECORDED", "errors": []},
        ),
    }


def _batch_out(batch: ExportBatch) -> dict[str, Any]:
    return {
        "id": batch.id,
        "batch_number": batch.batch_number,
        "status": batch.status,
        "archive_sha256": batch.archive_sha256,
        "created_by": batch.created_by,
        "created_at": batch.created_at,
        "imported_by": batch.imported_by,
        "imported_at": batch.imported_at,
        "invoice_ids": [item.invoice_id for item in batch.items],
        "items": [
            {
                "invoice_id": item.invoice_id,
                "revision_id": item.revision_id,
                "export_artifact_id": item.export_artifact_id,
                "pdf_filename": item.pdf_filename,
                "xml_filename": item.xml_filename,
                "imported_at": item.imported_at,
            }
            for item in batch.items
        ],
    }


def _checked_artifact_path(row: ExportArtifact, settings: Settings) -> Path:
    path = Path(row.xml_path).resolve()
    root = settings.export_archive_dir.resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="XML artifact is unavailable")
    return path


@router.get("/config")
def export_config(
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _manager(user)
    return {
        "pohoda_target_ico": settings.pohoda_target_ico or None,
        "pohoda_target_key_configured": bool(settings.pohoda_target_key),
        "identification": (
            "ICO_AND_KEY" if settings.pohoda_target_key else "ICO_ONLY"
        )
        if settings.pohoda_target_ico
        else "NOT_CONFIGURED",
    }


@router.get("")
def list_exports(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    _manager(user)
    batches = db.scalars(
        select(ExportBatch)
        .options(selectinload(ExportBatch.items))
        .order_by(ExportBatch.created_at.desc())
    ).all()
    return [_batch_out(batch) for batch in batches]


@router.get("/artifacts")
def list_artifacts(
    invoice_id: str | None = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    _manager(user)
    query = select(ExportArtifact).order_by(ExportArtifact.generated_at.desc())
    if invoice_id:
        query = query.where(ExportArtifact.invoice_id == invoice_id)
    return [_artifact_out(row) for row in db.scalars(query).all()]


@router.post("/invoices/{invoice_id}/generate", status_code=201)
async def generate_invoice_export(
    invoice_id: str,
    payload: ExportGenerate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _manager(user)
    invoice = db.scalar(select(Invoice).where(Invoice.id == invoice_id).with_for_update())
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    record_event(
        db,
        "XML_GENERATION_REQUESTED",
        actor=user.subject,
        invoice=invoice,
        comment=payload.reason,
    )
    paperless = PaperlessClient(settings)
    try:
        artifact = await generate_export_artifact(
            db,
            settings,
            paperless,
            invoice,
            user.subject,
            reexport_reason=payload.reason,
        )
        db.commit()
    except WorkflowError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        await paperless.close()
    return _artifact_out(artifact)


@router.get("/artifacts/{artifact_id}/xml")
def download_xml_artifact(
    artifact_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> Response:
    _manager(user)
    artifact = db.get(ExportArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Export artifact not found")
    if artifact.status.value != "XSD_VALID":
        raise HTTPException(status_code=409, detail="XSD-invalid XML cannot be downloaded")
    path = _checked_artifact_path(artifact, settings)
    xml = path.read_bytes()
    try:
        target_validation = validate_immutable_artifact_xml(artifact, xml)
    except WorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    invoice = db.get(Invoice, artifact.invoice_id)
    record_event(
        db,
        "EXPORT_DOWNLOADED",
        actor=user.subject,
        invoice=invoice,
        metadata={
            "export_artifact_id": artifact.id,
            "xml_sha256": artifact.xml_sha256,
            "pohoda_target_ico": target_validation["actual_ico"],
            "pohoda_target_validation": target_validation["status"],
        },
    )
    db.commit()
    return Response(
        content=xml,
        media_type="application/xml",
        headers={
            "Content-Disposition": (
                f'attachment; filename="pohoda-{artifact.invoice_id}-'
                f'r{artifact.source_snapshot["revision_number"]}.xml"'
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/artifacts/{artifact_id}/mark-imported")
def confirm_artifact_import(
    artifact_id: str,
    payload: ImportConfirmation,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
) -> dict[str, Any]:
    _manager(user)
    artifact = db.scalar(
        select(ExportArtifact).where(ExportArtifact.id == artifact_id).with_for_update()
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail="Export artifact not found")
    invoice = db.scalar(
        select(Invoice).where(Invoice.id == artifact.invoice_id).with_for_update()
    )
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    try:
        mark_artifact_imported(db, artifact, invoice, user.subject)
        db.commit()
    except WorkflowError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _artifact_out(artifact)


@router.post("", status_code=201)
async def create_export(
    payload: ExportCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _manager(user)
    invoices = list(
        db.scalars(
            select(Invoice).where(Invoice.id.in_(payload.invoice_ids)).with_for_update()
        ).all()
    )
    if len(invoices) != len(set(payload.invoice_ids)):
        raise HTTPException(status_code=404, detail="Some invoices were not found")
    paperless = PaperlessClient(settings)
    try:
        batch = await create_export_batch(db, settings, paperless, invoices, user.subject)
        db.commit()
    except WorkflowError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        await paperless.close()
    return _batch_out(batch)


@router.post("/responses", status_code=201)
async def upload_response(
    response_file: UploadFile = File(...),
    export_artifact_id: str | None = Form(default=None),
    batch_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _manager(user)
    content = await response_file.read(5 * 1024 * 1024 + 1)
    try:
        upload = store_pohoda_response(
            db,
            settings,
            content,
            response_file.filename or "pohoda-response.xml",
            user.subject,
            export_artifact_id=export_artifact_id,
            batch_id=batch_id,
        )
        db.commit()
    except WorkflowError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "id": upload.id,
        "filename": upload.filename,
        "sha256": upload.sha256,
        "parse_status": upload.parse_status,
        "parsed_result": upload.parsed_result,
        "parse_errors": upload.parse_errors,
        "uploaded_at": upload.uploaded_at,
    }


@router.get("/responses")
def list_responses(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    _manager(user)
    rows = db.scalars(
        select(PohodaResponseUpload).order_by(PohodaResponseUpload.uploaded_at.desc())
    ).all()
    return [
        {
            "id": row.id,
            "filename": row.filename,
            "export_artifact_id": row.export_artifact_id,
            "batch_id": row.batch_id,
            "sha256": row.sha256,
            "parse_status": row.parse_status,
            "parsed_result": row.parsed_result,
            "parse_errors": row.parse_errors,
            "uploaded_by": row.uploaded_by,
            "uploaded_at": row.uploaded_at,
        }
        for row in rows
    ]


@router.get("/{batch_id}/download")
def download_export(
    batch_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    _manager(user)
    batch = db.get(ExportBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Export batch not found")
    missing_source = db.scalar(
        select(Invoice.id)
        .join(ExportBatchItem, Invoice.id == ExportBatchItem.invoice_id)
        .where(
            ExportBatchItem.batch_id == batch.id,
            Invoice.source_status == SourceDocumentStatus.MISSING,
        )
        .limit(1)
    )
    if missing_source:
        raise HTTPException(status_code=409, detail="A batch source document is missing in Paperless")
    path = Path(batch.archive_path).resolve()
    root = settings.export_archive_dir.resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Archived export is unavailable")
    for item in batch.items:
        invoice = db.get(Invoice, item.invoice_id)
        record_event(
            db,
            "ZIP_DOWNLOADED",
            actor=user.subject,
            invoice=invoice,
            metadata={"batch_id": batch.id, "batch_number": batch.batch_number},
        )
    db.commit()
    return FileResponse(path, media_type="application/zip", filename=f"{batch.batch_number}.zip")


@router.post("/{batch_id}/mark-imported")
def confirm_import(
    batch_id: str,
    payload: ImportConfirmation,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
) -> dict[str, Any]:
    _manager(user)
    batch = db.scalar(
        select(ExportBatch)
        .options(selectinload(ExportBatch.items))
        .where(ExportBatch.id == batch_id)
        .with_for_update()
    )
    if batch is None:
        raise HTTPException(status_code=404, detail="Export batch not found")
    try:
        mark_batch_imported(db, batch, user.subject)
        db.commit()
    except WorkflowError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _batch_out(batch)
