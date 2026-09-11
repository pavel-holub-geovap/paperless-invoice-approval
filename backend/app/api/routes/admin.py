from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import ROLE_ADMIN, require_csrf_roles, require_roles
from app.config import Settings, get_settings
from app.db import get_db
from app.integrations.paperless import PaperlessClient
from app.models import AdminPurgeAudit, Invoice
from app.schemas import AdminBulkPurgeRequest, AdminPurgeRequest, CurrentUser
from app.services.admin_purge import AdminPurgeError, AdminPurgeExternalError, purge_invoice

router = APIRouter(prefix="/admin", tags=["admin"])


def _admin(user: CurrentUser) -> None:
    if ROLE_ADMIN not in user.roles:
        raise HTTPException(status_code=403, detail="ADMIN role required")


def _result_payload(result: Any) -> dict[str, Any]:
    return {
        "invoice_id": result.invoice_id,
        "status": result.status,
        "audit_id": result.audit_id,
        "paperless_document_ids": result.paperless_document_ids,
        "artifact_counts": result.artifact_counts,
    }


@router.get("/invoices")
def list_admin_invoices(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(ROLE_ADMIN)),
) -> list[dict[str, Any]]:
    _admin(user)
    invoices = db.scalars(
        select(Invoice)
        .options(selectinload(Invoice.revisions))
        .order_by(Invoice.created_at.desc())
    ).all()
    return [
        {
            "id": invoice.id,
            "paperless_document_id": invoice.paperless_document_id,
            "invoice_number": (
                invoice.current_revision.data.get("invoice_number")
                if invoice.current_revision
                else None
            ),
            "supplier_name": (
                invoice.current_revision.data.get("supplier_name")
                if invoice.current_revision
                else None
            ),
            "paperless_title": invoice.paperless_title,
            "paperless_created_at": invoice.paperless_created_at,
            "status": invoice.status,
            "upload_origin": invoice.upload_origin,
            "uploaded_by": invoice.uploaded_by_username,
        }
        for invoice in invoices
    ]


@router.get("/purge-audits")
def list_purge_audits(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles(ROLE_ADMIN)),
) -> list[dict[str, Any]]:
    _admin(user)
    rows = db.scalars(
        select(AdminPurgeAudit).order_by(AdminPurgeAudit.created_at.desc()).limit(500)
    ).all()
    return [
        {
            "id": row.id,
            "invoice_id": row.original_invoice_id,
            "paperless_document_ids": row.paperless_document_ids,
            "actor_subject": row.actor_subject,
            "actor_display_name": row.actor_display_name,
            "reason": row.reason,
            "result": row.result,
            "artifact_counts": row.artifact_counts,
            "correlation_id": row.correlation_id,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.post("/invoices/{invoice_id}/purge")
async def purge_admin_invoice(
    invoice_id: str,
    payload: AdminPurgeRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: CurrentUser = Depends(require_csrf_roles(ROLE_ADMIN)),
) -> dict[str, Any]:
    _admin(user)
    client = PaperlessClient(settings)
    try:
        result = await purge_invoice(
            db,
            settings,
            client,
            invoice_id=invoice_id,
            actor_subject=user.subject,
            actor_display_name=user.username,
            reason=payload.reason,
        )
        db.commit()
        return _result_payload(result)
    except AdminPurgeExternalError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except AdminPurgeError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        await client.close()


@router.post("/invoices/purge")
async def bulk_purge_admin_invoices(
    payload: AdminBulkPurgeRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: CurrentUser = Depends(require_csrf_roles(ROLE_ADMIN)),
) -> dict[str, Any]:
    _admin(user)
    results: list[dict[str, Any]] = []
    client = PaperlessClient(settings)
    try:
        for invoice_id in payload.invoice_ids:
            try:
                result = await purge_invoice(
                    db,
                    settings,
                    client,
                    invoice_id=invoice_id,
                    actor_subject=user.subject,
                    actor_display_name=user.username,
                    reason=payload.reason,
                )
                db.commit()
                results.append(_result_payload(result))
            except AdminPurgeError as exc:
                db.rollback()
                results.append(
                    {"invoice_id": invoice_id, "status": "FAILED", "error": str(exc)}
                )
    finally:
        await client.close()
    succeeded = sum(row["status"] in {"PURGED", "ALREADY_PURGED"} for row in results)
    return {"succeeded": succeeded, "failed": len(results) - succeeded, "results": results}
