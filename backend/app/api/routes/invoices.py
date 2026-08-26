from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user, require_csrf
from app.config import Settings, get_settings
from app.db import get_db
from app.integrations.paperless import PaperlessClient, PaperlessError, PaperlessNotFound
from app.models import (
    AIExtraction,
    Allocation,
    ApprovalAction,
    ApprovalAssignment,
    ApprovalDecision,
    AuditEvent,
    CostCenter,
    ExportArtifact,
    Invoice,
    InvoiceDisposition,
    InvoiceStatus,
    SourceDocumentStatus,
    ValidationResult,
    ValidationSeverity,
)
from app.schemas import (
    AIExtractionApply,
    AllocationSet,
    ApproverSet,
    CurrentUser,
    InvoiceCreate,
    InvoiceDispositionRestore,
    InvoiceDispositionSet,
    InvoiceListItem,
    InvoicePatch,
)
from app.services.approval_setup import replace_allocations, replace_approvers
from app.services.audit import record_event
from app.services.disposition import restore_disposition, set_disposition
from app.services.exports import latest_valid_artifact
from app.services.extraction import (
    apply_ai_extraction,
    queue_ai_extraction,
    stored_extraction_to_invoice_data,
)
from app.services.paperless_sync import mark_source_missing
from app.services.validation import run_validations
from app.services.workflow import (
    WorkflowError,
    allocation_totals,
    confirm_original,
    create_invoice,
    reopen,
    submit_for_approval,
    update_invoice_data,
)

router = APIRouter(prefix="/invoices", tags=["invoices"])


def _manager(user: CurrentUser) -> None:
    if "QUEUE_MANAGER" not in user.roles:
        raise HTTPException(status_code=403, detail="QUEUE_MANAGER role required")


def _viewer(db: Session, invoice: Invoice, user: CurrentUser) -> None:
    if "QUEUE_MANAGER" in user.roles:
        return
    if "APPROVER" in user.roles and db.scalar(
        select(ApprovalAssignment.id)
        .where(
            ApprovalAssignment.invoice_id == invoice.id,
            ApprovalAssignment.revision_id == invoice.current_revision.id,
            ApprovalAssignment.approver_subject == user.subject,
            ApprovalAssignment.active.is_(True),
        )
        .limit(1)
    ):
        return
    raise HTTPException(status_code=403, detail="Invoice is not available to this user")


def _invoice_or_404(db: Session, invoice_id: str, lock: bool = False) -> Invoice:
    query = (
        select(Invoice)
        .options(
            selectinload(Invoice.revisions),
            selectinload(Invoice.ai_extractions),
            selectinload(Invoice.allocations).selectinload(Allocation.cost_center),
            selectinload(Invoice.allocations)
            .selectinload(Allocation.assignments)
            .selectinload(ApprovalAssignment.decisions),
        )
        .where(Invoice.id == invoice_id)
    )
    if lock:
        query = query.with_for_update()
    invoice = db.scalar(query.execution_options(populate_existing=True))
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


def _require_current_revision(invoice: Invoice, expected_revision: int | None) -> None:
    if expected_revision is not None and expected_revision != invoice.current_revision_number:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STALE_REVISION",
                "message": "Faktura byla mezitím změněna. Načtěte aktuální revizi.",
                "expected_revision": expected_revision,
                "current_revision": invoice.current_revision_number,
            },
        )


def serialize_invoice(db: Session, invoice: Invoice) -> dict[str, Any]:
    revision = invoice.current_revision
    allocations = [
        row for row in invoice.allocations if row.revision_id == revision.id and row.active
    ]
    validations = db.scalars(
        select(ValidationResult).where(ValidationResult.revision_id == revision.id)
    ).all()
    ai_history = sorted(
        invoice.ai_extractions, key=lambda row: row.extraction_revision, reverse=True
    )
    latest_ai = ai_history[0] if ai_history else None
    total, allocated, remaining = allocation_totals(db, invoice)
    latest_export = db.scalar(
        select(ExportArtifact)
        .where(
            ExportArtifact.invoice_id == invoice.id,
            ExportArtifact.revision_id == revision.id,
        )
        .order_by(ExportArtifact.generated_at.desc())
    )

    def serialize_ai(row: AIExtraction, *, include_result: bool = False) -> dict[str, Any]:
        result = {
            "id": row.id,
            "extraction_revision": row.extraction_revision,
            "invoice_revision": row.invoice_revision.number if row.invoice_revision else None,
            "model": row.model,
            "schema_version": row.schema_version,
            "prompt_version": row.prompt_version,
            "status": row.status,
            "validation_summary": row.validation_summary,
            "error_code": row.error_code,
            "error_message": row.error_message,
            "schema_validation_errors": row.schema_validation_errors_json,
            "normalization_result": row.normalization_result_json,
            "corrective_retry_count": row.corrective_retry_count,
            "raw_response_preserved": row.raw_response is not None,
            "queued_at": row.queued_at,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
            "duration_ms": row.duration_ms,
            "applied": row.applied,
            "applied_at": row.applied_at,
            "applied_by": row.applied_by,
            "requires_confirmation": row.requires_confirmation,
        }
        if include_result:
            result["parsed_result"] = row.parsed_result
            result["validation_results"] = row.validation_results_json
            try:
                result["candidate_data"] = stored_extraction_to_invoice_data(
                    row, invoice.paperless_ocr_text
                )
            except (TypeError, ValueError):
                result["candidate_data"] = None
        return result

    return {
        "id": invoice.id,
        "paperless_document_id": invoice.paperless_document_id,
        "paperless": {
            "title": invoice.paperless_title,
            "created_at": invoice.paperless_created_at,
            "correspondent_id": invoice.paperless_correspondent_id,
            "correspondent": invoice.paperless_correspondent_name,
            "tag_ids": invoice.paperless_tag_ids,
            "tags": invoice.paperless_tags,
            "ocr_text": invoice.paperless_ocr_text,
            "original_filename": invoice.paperless_original_filename,
            "sync_status": invoice.sync_status,
            "last_synced_at": invoice.last_synced_at,
            "sync_error": invoice.sync_error,
            "source_pdf_sha256": invoice.source_pdf_sha256,
            "uploaded_by": invoice.uploaded_by_username,
        },
        "status": invoice.status,
        "disposition": {
            "status": invoice.disposition,
            "reason": invoice.disposition_reason,
            "comment": invoice.disposition_comment,
            "actor": invoice.disposition_actor,
            "changed_at": invoice.disposition_changed_at,
            "duplicate_of_invoice_id": invoice.duplicate_of_invoice_id,
        },
        "source": {
            "status": invoice.source_status,
            "missing_at": invoice.source_missing_at,
        },
        "ai_status": invoice.ai_status,
        "ai": {
            "latest": serialize_ai(latest_ai, include_result=True) if latest_ai else None,
            "history": [serialize_ai(row) for row in ai_history],
        },
        "current_revision_number": invoice.current_revision_number,
        "original_review_confirmed": invoice.original_review_confirmed,
        "original_reviewed_at": invoice.original_reviewed_at,
        "original_reviewed_by": invoice.original_reviewed_by,
        "data": revision.data,
        "extracted_fields": [
            {"field_name": field.field_name, "value": field.value, "source_text": field.source_text}
            for field in revision.extracted_fields
        ],
        "validations": [
            {
                "code": result.code,
                "severity": result.severity,
                "field_name": result.field_name,
                "message": result.message,
                "expected": result.expected,
                "actual": result.actual,
                "details": result.details,
            }
            for result in validations
        ],
        "allocations": [
            {
                "id": allocation.id,
                "amount": allocation.amount,
                "percentage": allocation.percentage,
                "note": allocation.note,
                "vat_breakdown": allocation.vat_breakdown,
                "created_by": allocation.created_by,
                "cost_center": {
                    "id": allocation.cost_center.id,
                    "code": allocation.cost_center.code,
                    "name": allocation.cost_center.name,
                    "pohoda_code": allocation.cost_center.pohoda_code,
                },
                "assignments": [
                    {
                        "id": assignment.id,
                        "approver_subject": assignment.approver_subject,
                        "required": assignment.required,
                        "status": assignment.status,
                        "assigned_by": assignment.assigned_by,
                        "assigned_at": assignment.assigned_at,
                        "decided_at": assignment.decided_at,
                        "comment": assignment.comment,
                        "decision": next(
                            (
                                decision.action
                                for decision in reversed(assignment.decisions)
                                if decision.valid
                            ),
                            None,
                        ),
                    }
                    for assignment in allocation.assignments
                    if assignment.active and assignment.revision_id == revision.id
                ],
            }
            for allocation in allocations
        ],
        "allocation_summary": {
            "invoice_total": total,
            "allocated": allocated,
            "remaining": remaining,
        },
        "pohoda_export": (
            {
                "id": latest_export.id,
                "status": latest_export.status,
                "generator_version": latest_export.generator_version,
                "xsd_bundle_version": latest_export.xsd_bundle_version,
                "encoding": latest_export.encoding,
                "xml_sha256": latest_export.xml_sha256,
                "xml_size": latest_export.xml_size,
                "generated_by": latest_export.generated_by,
                "generated_at": latest_export.generated_at,
                "validation_errors": latest_export.validation_errors,
                "source_export_id": latest_export.source_export_id,
                "imported_by": latest_export.imported_by,
                "imported_at": latest_export.imported_at,
                "pohoda_target_ico": latest_export.source_snapshot.get("pohoda_target_ico"),
                "pohoda_target_key_configured": latest_export.source_snapshot.get(
                    "pohoda_target_key_configured", False
                ),
                "pohoda_target_validation": latest_export.source_snapshot.get(
                    "pohoda_target_validation",
                    {"status": "NOT_RECORDED", "errors": []},
                ),
            }
            if latest_export
            else None
        ),
        "created_at": invoice.created_at,
        "updated_at": invoice.updated_at,
    }


@router.get("")
def list_invoices(
    status_filter: InvoiceStatus | None = Query(default=None, alias="status"),
    supplier: str | None = None,
    approver: str | None = None,
    cost_center: str | None = None,
    view: Literal["active", "ignored", "missing", "all"] = "active",
    sort: Literal["source_desc", "source_asc"] = "source_desc",
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[InvoiceListItem]:
    _manager(user)
    source_order = (
        Invoice.paperless_created_at.asc().nullslast()
        if sort == "source_asc"
        else Invoice.paperless_created_at.desc().nullslast()
    )
    query = (
        select(Invoice)
        .options(selectinload(Invoice.revisions))
        .order_by(source_order, Invoice.updated_at.desc())
    )
    if status_filter:
        query = query.where(Invoice.status == status_filter)
    if view == "active":
        query = query.where(
            Invoice.disposition == InvoiceDisposition.ACTIVE,
            Invoice.source_status == SourceDocumentStatus.AVAILABLE,
        )
    elif view == "ignored":
        query = query.where(Invoice.disposition != InvoiceDisposition.ACTIVE)
    elif view == "missing":
        query = query.where(Invoice.source_status == SourceDocumentStatus.MISSING)
    invoices = db.scalars(query).unique().all()
    result: list[InvoiceListItem] = []
    for invoice in invoices:
        revision = invoice.current_revision
        data = revision.data
        if supplier and supplier.lower() not in str(data.get("supplier_name") or "").lower():
            continue
        assignment_query = select(ApprovalAssignment).where(
            ApprovalAssignment.invoice_id == invoice.id,
            ApprovalAssignment.revision_id == revision.id,
            ApprovalAssignment.active.is_(True),
            ApprovalAssignment.required.is_(True),
        )
        assignments = db.scalars(assignment_query).all()
        if approver and not any(row.approver_subject == approver for row in assignments):
            continue
        if cost_center:
            matching = db.scalar(
                select(Allocation.id)
                .join(CostCenter)
                .where(
                    Allocation.revision_id == revision.id,
                    Allocation.active.is_(True),
                    CostCenter.code == cost_center,
                )
                .limit(1)
            )
            if not matching:
                continue
        approved_ids = set(
            db.scalars(
                select(ApprovalDecision.assignment_id).where(
                    ApprovalDecision.revision_id == revision.id,
                    ApprovalDecision.action == ApprovalAction.APPROVE,
                    ApprovalDecision.valid.is_(True),
                )
            ).all()
        )
        severities = db.scalars(
            select(ValidationResult.severity).where(ValidationResult.revision_id == revision.id)
        ).all()
        result.append(
            InvoiceListItem(
                id=invoice.id,
                paperless_document_id=invoice.paperless_document_id,
                status=invoice.status,
                disposition=invoice.disposition,
                source_status=invoice.source_status,
                source_missing_at=invoice.source_missing_at,
                current_revision_number=invoice.current_revision_number,
                title=invoice.paperless_title,
                correspondent=invoice.paperless_correspondent_name,
                paperless_created_at=invoice.paperless_created_at,
                approval_created_at=invoice.created_at,
                uploaded_by=invoice.uploaded_by_username,
                source_pdf_sha256=invoice.source_pdf_sha256,
                sync_status=invoice.sync_status,
                ai_status=invoice.ai_status,
                supplier_name=data.get("supplier_name"),
                invoice_number=data.get("invoice_number"),
                total_amount=data.get("total_amount"),
                due_date=data.get("due_date"),
                approvals_done=sum(row.id in approved_ids for row in assignments),
                approvals_required=len(assignments),
                warning_count=sum(value == ValidationSeverity.WARNING for value in severities),
                blocking_error_count=sum(
                    value == ValidationSeverity.BLOCKING_ERROR for value in severities
                ),
                updated_at=invoice.updated_at,
            )
        )
    return result


@router.post("", status_code=201)
def manually_register_invoice(
    payload: InvoiceCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
) -> dict[str, Any]:
    _manager(user)
    invoice = create_invoice(db, payload.paperless_document_id, user.subject)
    db.commit()
    return serialize_invoice(db, _invoice_or_404(db, invoice.id))


@router.get("/{invoice_id}")
def get_invoice(
    invoice_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    invoice = _invoice_or_404(db, invoice_id)
    _viewer(db, invoice, user)
    return serialize_invoice(db, invoice)


@router.get("/{invoice_id}/pdf")
async def proxy_pdf(
    invoice_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> Response:
    invoice = _invoice_or_404(db, invoice_id)
    _viewer(db, invoice, user)
    if invoice.source_status == SourceDocumentStatus.MISSING:
        raise HTTPException(status_code=409, detail="Paperless source document is missing")
    client = PaperlessClient(settings)
    try:
        try:
            pdf = await client.download_pdf(invoice.paperless_document_id)
        except PaperlessNotFound as exc:
            mark_source_missing(db, invoice, user.subject)
            run_validations(db, invoice, user.subject)
            db.commit()
            raise HTTPException(
                status_code=409, detail="Paperless source document is missing"
            ) from exc
        except PaperlessError as exc:
            raise HTTPException(
                status_code=502, detail="Paperless is temporarily unavailable"
            ) from exc
    finally:
        await client.close()
    record_event(
        db,
        "PDF_DOWNLOADED",
        actor=user.subject,
        invoice=invoice,
        metadata={"paperless_document_id": invoice.paperless_document_id},
    )
    db.commit()
    return Response(
        pdf, media_type="application/pdf", headers={"Cache-Control": "private, no-store"}
    )


@router.post("/{invoice_id}/disposition")
def ignore_invoice(
    invoice_id: str,
    payload: InvoiceDispositionSet,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
) -> dict[str, Any]:
    _manager(user)
    invoice = _invoice_or_404(db, invoice_id, lock=True)
    duplicate_of = None
    if payload.duplicate_of_invoice_id:
        duplicate_of = _invoice_or_404(db, payload.duplicate_of_invoice_id)
    try:
        set_disposition(
            db,
            invoice,
            InvoiceDisposition(payload.disposition),
            user.subject,
            payload.reason,
            comment=payload.comment,
            duplicate_of=duplicate_of,
        )
        db.commit()
    except WorkflowError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return serialize_invoice(db, _invoice_or_404(db, invoice_id))


@router.post("/{invoice_id}/restore")
def restore_invoice(
    invoice_id: str,
    payload: InvoiceDispositionRestore,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
) -> dict[str, Any]:
    _manager(user)
    invoice = _invoice_or_404(db, invoice_id, lock=True)
    restore_disposition(db, invoice, user.subject, payload.comment)
    db.commit()
    return serialize_invoice(db, _invoice_or_404(db, invoice_id))


@router.get("/{invoice_id}/pohoda.xml")
def download_pohoda_xml(
    invoice_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    _manager(user)
    invoice = _invoice_or_404(db, invoice_id)
    artifact = latest_valid_artifact(db, invoice)
    if artifact is None:
        raise HTTPException(status_code=409, detail="Generate and validate XML explicitly first")
    path = Path(artifact.xml_path).resolve()
    root = settings.export_archive_dir.resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="XML artifact is unavailable")
    stem = str(invoice.current_revision.data.get("invoice_number") or invoice.id)
    record_event(
        db,
        "EXPORT_DOWNLOADED",
        actor=user.subject,
        invoice=invoice,
        metadata={"export_artifact_id": artifact.id},
    )
    db.commit()
    return FileResponse(
        path,
        media_type="application/xml",
        filename=f"invoice-{stem}.xml",
        headers={"Cache-Control": "private, no-store"},
    )


@router.patch("/{invoice_id}")
def patch_invoice(
    invoice_id: str,
    payload: InvoicePatch,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
) -> dict[str, Any]:
    _manager(user)
    invoice = _invoice_or_404(db, invoice_id, lock=True)
    _require_current_revision(invoice, payload.expected_revision)
    try:
        update_invoice_data(db, invoice, payload.changes, user.subject, payload.comment)
        run_validations(db, invoice, user.subject)
        db.commit()
    except WorkflowError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return serialize_invoice(db, _invoice_or_404(db, invoice_id))


@router.post("/{invoice_id}/confirm-original")
def confirm_invoice_original(
    invoice_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
) -> dict[str, Any]:
    _manager(user)
    invoice = _invoice_or_404(db, invoice_id, lock=True)
    confirm_original(db, invoice, user.subject)
    db.commit()
    return serialize_invoice(db, _invoice_or_404(db, invoice_id))


@router.post("/{invoice_id}/ai-extractions", status_code=202)
def request_ai_extraction(
    invoice_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _manager(user)
    invoice = _invoice_or_404(db, invoice_id, lock=True)
    try:
        extraction = queue_ai_extraction(
            db,
            invoice,
            settings,
            actor=user.subject,
            reextraction=bool(invoice.ai_extractions),
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": extraction.id, "status": extraction.status}


@router.post("/{invoice_id}/ai-extractions/{extraction_id}/apply")
def apply_ai_candidate(
    invoice_id: str,
    extraction_id: str,
    payload: AIExtractionApply,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
) -> dict[str, Any]:
    _manager(user)
    invoice = _invoice_or_404(db, invoice_id, lock=True)
    extraction = db.get(AIExtraction, extraction_id)
    if extraction is None or extraction.invoice_id != invoice.id:
        raise HTTPException(status_code=404, detail="AI extraction not found")
    try:
        apply_ai_extraction(
            db,
            invoice,
            extraction,
            user.subject,
            confirm_overwrite=payload.confirm_overwrite,
        )
        db.commit()
    except (ValueError, WorkflowError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return serialize_invoice(db, _invoice_or_404(db, invoice_id))


@router.put("/{invoice_id}/allocations")
def set_allocations(
    invoice_id: str,
    payload: AllocationSet,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
) -> dict[str, Any]:
    _manager(user)
    invoice = _invoice_or_404(db, invoice_id, lock=True)
    _require_current_revision(invoice, payload.expected_revision)
    try:
        replace_allocations(db, invoice, payload.allocations, user.subject)
        db.commit()
    except WorkflowError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return serialize_invoice(db, _invoice_or_404(db, invoice_id))


@router.put("/{invoice_id}/allocations/{allocation_id}/approvers")
def set_approvers(
    invoice_id: str,
    allocation_id: str,
    payload: ApproverSet,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
) -> dict[str, Any]:
    _manager(user)
    invoice = _invoice_or_404(db, invoice_id, lock=True)
    _require_current_revision(invoice, payload.expected_revision)
    allocation = db.get(Allocation, allocation_id)
    if not allocation or allocation.invoice_id != invoice.id or not allocation.active:
        raise HTTPException(status_code=404, detail="Allocation not found")
    try:
        replace_approvers(
            db,
            invoice,
            allocation,
            payload.approver_subjects,
            user.subject,
        )
        db.commit()
    except WorkflowError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return serialize_invoice(db, _invoice_or_404(db, invoice_id))


@router.post("/{invoice_id}/submit")
def submit(
    invoice_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
) -> dict[str, Any]:
    _manager(user)
    invoice = _invoice_or_404(db, invoice_id, lock=True)
    try:
        submit_for_approval(db, invoice, user.subject)
        db.commit()
    except WorkflowError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return serialize_invoice(db, _invoice_or_404(db, invoice_id))


@router.post("/{invoice_id}/reopen")
def reopen_invoice(
    invoice_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_csrf),
) -> dict[str, Any]:
    _manager(user)
    invoice = _invoice_or_404(db, invoice_id, lock=True)
    try:
        reopen(db, invoice, user.subject)
        db.commit()
    except WorkflowError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return serialize_invoice(db, _invoice_or_404(db, invoice_id))


@router.get("/{invoice_id}/audit")
def invoice_audit(
    invoice_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    invoice = _invoice_or_404(db, invoice_id)
    _viewer(db, invoice, user)
    events = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.invoice_id == invoice_id)
        .order_by(AuditEvent.created_at)
    ).all()
    return [
        {
            "id": row.id,
            "timestamp": row.created_at,
            "actor": row.actor_subject,
            "revision": row.revision_number,
            "event_type": row.event_type,
            "old_state": row.old_state,
            "new_state": row.new_state,
            "old_value": row.old_value,
            "new_value": row.new_value,
            "comment": row.comment,
            "metadata": row.metadata_json,
        }
        for row in events
    ]
