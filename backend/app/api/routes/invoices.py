from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user, require_csrf
from app.config import Settings, get_settings
from app.db import get_db
from app.integrations.paperless import PaperlessClient
from app.models import (
    AIExtraction,
    Allocation,
    ApprovalAction,
    ApprovalAssignment,
    ApprovalDecision,
    AuditEvent,
    CostCenter,
    Invoice,
    InvoiceStatus,
    UserIdentity,
    ValidationResult,
)
from app.schemas import (
    AIExtractionApply,
    AllocationSet,
    ApproverSet,
    CurrentUser,
    InvoiceCreate,
    InvoiceListItem,
    InvoicePatch,
)
from app.services.allocations import allocate_by_percentages
from app.services.audit import record_event
from app.services.extraction import apply_ai_extraction, queue_ai_extraction
from app.services.pohoda import generate_invoice_xml, validate_xml
from app.services.validation import run_validations
from app.services.workflow import (
    WorkflowError,
    confirm_original,
    create_invoice,
    fork_revision,
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
    invoice = db.scalar(query)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


def serialize_invoice(db: Session, invoice: Invoice) -> dict[str, Any]:
    revision = invoice.current_revision
    allocations = [row for row in invoice.allocations if row.revision_id == revision.id and row.active]
    validations = db.scalars(
        select(ValidationResult).where(ValidationResult.revision_id == revision.id)
    ).all()
    ai_history = sorted(invoice.ai_extractions, key=lambda row: row.extraction_revision, reverse=True)
    latest_ai = ai_history[0] if ai_history else None

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
        },
        "status": invoice.status,
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
            }
            for result in validations
        ],
        "allocations": [
            {
                "id": allocation.id,
                "amount": allocation.amount,
                "percentage": allocation.percentage,
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
        "created_at": invoice.created_at,
        "updated_at": invoice.updated_at,
    }


@router.get("")
def list_invoices(
    status_filter: InvoiceStatus | None = Query(default=None, alias="status"),
    supplier: str | None = None,
    approver: str | None = None,
    cost_center: str | None = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[InvoiceListItem]:
    _manager(user)
    query = select(Invoice).options(selectinload(Invoice.revisions)).order_by(Invoice.updated_at.desc())
    if status_filter:
        query = query.where(Invoice.status == status_filter)
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
        result.append(
            InvoiceListItem(
                id=invoice.id,
                paperless_document_id=invoice.paperless_document_id,
                status=invoice.status,
                current_revision_number=invoice.current_revision_number,
                title=invoice.paperless_title,
                correspondent=invoice.paperless_correspondent_name,
                paperless_created_at=invoice.paperless_created_at,
                sync_status=invoice.sync_status,
                ai_status=invoice.ai_status,
                supplier_name=data.get("supplier_name"),
                invoice_number=data.get("invoice_number"),
                total_amount=data.get("total_amount"),
                due_date=data.get("due_date"),
                approvals_done=sum(row.id in approved_ids for row in assignments),
                approvals_required=len(assignments),
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
    client = PaperlessClient(settings)
    try:
        pdf = await client.download_pdf(invoice.paperless_document_id)
    finally:
        await client.close()
    return Response(pdf, media_type="application/pdf", headers={"Cache-Control": "private, no-store"})


@router.get("/{invoice_id}/pohoda.xml")
def download_pohoda_xml(
    invoice_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> Response:
    _manager(user)
    invoice = _invoice_or_404(db, invoice_id)
    if invoice.status not in {
        InvoiceStatus.APPROVED,
        InvoiceStatus.XML_READY,
        InvoiceStatus.READY_FOR_EXPORT,
        InvoiceStatus.EXPORT_CREATED,
        InvoiceStatus.IMPORTED_TO_POHODA,
    }:
        raise HTTPException(status_code=409, detail="Only an approved invoice can produce XML")
    allocations = db.scalars(
        select(Allocation)
        .options(selectinload(Allocation.cost_center))
        .where(
            Allocation.revision_id == invoice.current_revision.id,
            Allocation.active.is_(True),
        )
    ).all()
    xml = generate_invoice_xml(invoice.current_revision, list(allocations))
    errors = validate_xml(xml, settings.pohoda_xsd_path)
    if errors:
        raise HTTPException(status_code=409, detail={"message": "XSD validation failed", "errors": errors[:20]})
    stem = str(invoice.current_revision.data.get("invoice_number") or invoice.id)
    return Response(
        xml,
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="invoice-{stem}.xml"',
            "Cache-Control": "private, no-store",
        },
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
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _manager(user)
    invoice = _invoice_or_404(db, invoice_id, lock=True)
    revision = invoice.current_revision
    if invoice.status in {
        InvoiceStatus.AWAITING_APPROVAL,
        InvoiceStatus.RETURNED,
        InvoiceStatus.REJECTED,
        InvoiceStatus.APPROVED,
        InvoiceStatus.XML_READY,
        InvoiceStatus.READY_FOR_EXPORT,
        InvoiceStatus.EXPORT_CREATED,
    }:
        revision = fork_revision(db, invoice, user.subject, "Změna rozúčtování")
    total = Decimal(str(revision.data.get("total_amount") or "0")).quantize(Decimal("0.01"))
    percentage_mode = all(row.percentage is not None for row in payload.allocations)
    amount_mode = all(row.amount is not None for row in payload.allocations)
    if not (percentage_mode or amount_mode):
        raise HTTPException(status_code=422, detail="All allocations must use the same input mode")
    amounts = (
        allocate_by_percentages(total, [row.percentage for row in payload.allocations])
        if percentage_mode
        else [row.amount.quantize(Decimal("0.01")) for row in payload.allocations]
    )
    if abs(sum(amounts, Decimal("0")) - total) > Decimal(settings.allocation_tolerance):
        raise HTTPException(status_code=409, detail="Allocation total does not equal invoice total")
    centre_ids = [row.cost_center_id for row in payload.allocations]
    if len(centre_ids) != len(set(centre_ids)):
        raise HTTPException(status_code=422, detail="A cost center can occur only once")
    centres = {row.id: row for row in db.scalars(select(CostCenter).where(CostCenter.id.in_(centre_ids), CostCenter.active.is_(True))).all()}
    if set(centre_ids) != set(centres):
        raise HTTPException(status_code=422, detail="Unknown or inactive cost center")
    for existing in db.scalars(select(Allocation).where(Allocation.revision_id == revision.id, Allocation.active.is_(True))).all():
        existing.active = False
        record_event(db, "ALLOCATION_REMOVED", actor=user.subject, invoice=invoice, old_value={"id": existing.id, "amount": str(existing.amount)})
    for item, amount in zip(payload.allocations, amounts, strict=True):
        allocation = Allocation(
            invoice_id=invoice.id,
            revision_id=revision.id,
            cost_center_id=item.cost_center_id,
            amount=amount,
            percentage=item.percentage,
        )
        db.add(allocation)
        db.flush()
        record_event(db, "ALLOCATION_CREATED", actor=user.subject, invoice=invoice, new_value={"id": allocation.id, "cost_center": centres[item.cost_center_id].code, "amount": str(amount)})
    run_validations(db, invoice, user.subject)
    db.commit()
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
    allocation = db.get(Allocation, allocation_id)
    if not allocation or allocation.invoice_id != invoice.id or not allocation.active:
        raise HTTPException(status_code=404, detail="Allocation not found")
    if invoice.status in {
        InvoiceStatus.AWAITING_APPROVAL,
        InvoiceStatus.RETURNED,
        InvoiceStatus.REJECTED,
        InvoiceStatus.APPROVED,
        InvoiceStatus.XML_READY,
        InvoiceStatus.READY_FOR_EXPORT,
        InvoiceStatus.EXPORT_CREATED,
    }:
        old_cost_center_id = allocation.cost_center_id
        revision = fork_revision(db, invoice, user.subject, "Změna seznamu schvalovatelů")
        allocation = db.scalar(
            select(Allocation).where(
                Allocation.revision_id == revision.id,
                Allocation.cost_center_id == old_cost_center_id,
                Allocation.active.is_(True),
            )
        )
    subjects = list(dict.fromkeys(payload.approver_subjects))
    identities = {
        row.subject: row
        for row in db.scalars(select(UserIdentity).where(UserIdentity.subject.in_(subjects))).all()
    }
    invalid_subjects = [
        subject
        for subject in subjects
        if subject not in identities or "APPROVER" not in identities[subject].roles
    ]
    if invalid_subjects:
        raise HTTPException(status_code=422, detail="Unknown or unauthorized approver")
    existing = db.scalars(
        select(ApprovalAssignment).where(
            ApprovalAssignment.allocation_id == allocation.id,
            ApprovalAssignment.active.is_(True),
        )
    ).all()
    existing_subjects = {row.approver_subject for row in existing}
    for row in existing:
        if row.approver_subject not in subjects:
            row.active = False
            record_event(db, "APPROVER_REMOVED", actor=user.subject, invoice=invoice, old_value={"allocation_id": allocation.id, "approver": row.approver_subject})
    for subject in subjects:
        if subject not in existing_subjects:
            assignment = ApprovalAssignment(
                invoice_id=invoice.id,
                revision_id=invoice.current_revision.id,
                allocation_id=allocation.id,
                approver_subject=subject,
            )
            db.add(assignment)
            record_event(db, "APPROVER_ADDED", actor=user.subject, invoice=invoice, new_value={"allocation_id": allocation.id, "approver": subject})
    db.commit()
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
    events = db.scalars(select(AuditEvent).where(AuditEvent.invoice_id == invoice_id).order_by(AuditEvent.created_at)).all()
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
