from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user, require_csrf
from app.config import Settings, get_settings
from app.db import get_db
from app.integrations.paperless import PaperlessClient
from app.models import ExportBatch, Invoice
from app.schemas import CurrentUser, ExportCreate
from app.services.exports import create_export_batch, mark_batch_imported
from app.services.workflow import WorkflowError

router = APIRouter(prefix="/exports", tags=["exports"])


def _manager(user: CurrentUser) -> None:
    if "QUEUE_MANAGER" not in user.roles:
        raise HTTPException(status_code=403, detail="QUEUE_MANAGER role required")


@router.get("")
def list_exports(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    _manager(user)
    batches = db.scalars(
        select(ExportBatch).options(selectinload(ExportBatch.items)).order_by(ExportBatch.created_at.desc())
    ).all()
    return [
        {
            "id": batch.id,
            "batch_number": batch.batch_number,
            "status": batch.status,
            "created_by": batch.created_by,
            "created_at": batch.created_at,
            "imported_by": batch.imported_by,
            "imported_at": batch.imported_at,
            "invoice_ids": [item.invoice_id for item in batch.items],
        }
        for batch in batches
    ]


@router.post("", status_code=201)
async def create_export(
    payload: ExportCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _manager(user)
    invoices = list(db.scalars(select(Invoice).where(Invoice.id.in_(payload.invoice_ids)).with_for_update()).all())
    if len(invoices) != len(set(payload.invoice_ids)):
        raise HTTPException(status_code=404, detail="Some invoices were not found")
    paperless = PaperlessClient(settings)
    try:
        batch = await create_export_batch(
            db, settings, paperless, invoices, user.subject, settings.pohoda_xsd_path
        )
        db.commit()
    except WorkflowError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        await paperless.close()
    return {"id": batch.id, "batch_number": batch.batch_number, "status": batch.status}


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
    path = Path(batch.archive_path).resolve()
    root = settings.export_archive_dir.resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Archived export is unavailable")
    return FileResponse(path, media_type="application/zip", filename=f"{batch.batch_number}.zip")


@router.post("/{batch_id}/mark-imported")
def confirm_import(
    batch_id: str,
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
    return {"id": batch.id, "batch_number": batch.batch_number, "status": batch.status}

