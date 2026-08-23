from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.ollama import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    OllamaExtractionResult,
)
from app.models import (
    AIExtraction,
    AIExtractionStatus,
    ExtractedField,
    Invoice,
    ValidationResult,
    ValidationSeverity,
)
from app.schemas import EvidenceValue, InvoiceExtractionV1
from app.services.audit import record_event
from app.services.jobs import enqueue_job
from app.services.validation import run_validations, validate_invoice_data
from app.services.workflow import update_invoice_data

AI_JOB_TYPE = "AI_EXTRACT_INVOICE"


def extraction_to_invoice_data(payload: InvoiceExtractionV1) -> dict[str, Any]:
    def scalar(field: str) -> Any:
        evidence = getattr(payload, field)
        value = evidence.value
        if isinstance(value, Decimal):
            return str(value)
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return value

    currency = scalar("currency")
    return {
        "supplier_name": scalar("supplier_name"),
        "supplier_ico": scalar("supplier_ico"),
        "supplier_dic": scalar("supplier_dic"),
        "supplier_address": scalar("supplier_address"),
        "invoice_number": scalar("invoice_number"),
        "variable_symbol": scalar("variable_symbol"),
        "issue_date": scalar("issue_date"),
        "taxable_supply_date": scalar("taxable_supply_date"),
        "due_date": scalar("due_date"),
        "currency": str(currency).upper() if currency else None,
        "bank_account": scalar("bank_account"),
        "bank_code": scalar("bank_code"),
        "iban": scalar("iban"),
        "swift_bic": scalar("swift_bic"),
        "vat_lines": [row.model_dump(mode="json") for row in payload.vat_lines],
        "total_without_vat": scalar("total_without_vat"),
        "total_vat": scalar("total_vat"),
        "total_amount": scalar("total_amount"),
        "description": scalar("description"),
    }


def _evidence(payload: InvoiceExtractionV1) -> dict[str, tuple[Any, str | None]]:
    result: dict[str, tuple[Any, str | None]] = {}
    for field in (
        "supplier_name",
        "supplier_ico",
        "supplier_dic",
        "supplier_address",
        "invoice_number",
        "variable_symbol",
        "issue_date",
        "taxable_supply_date",
        "due_date",
        "currency",
        "bank_account",
        "bank_code",
        "iban",
        "swift_bic",
        "total_without_vat",
        "total_vat",
        "total_amount",
        "description",
    ):
        item: EvidenceValue = getattr(payload, field)
        value = item.value
        if isinstance(value, Decimal):
            value = str(value)
        elif hasattr(value, "isoformat"):
            value = value.isoformat()
        result[field] = (value, item.source_text)
    result["vat_lines"] = (
        [row.model_dump(mode="json") for row in payload.vat_lines],
        " | ".join(row.source_text for row in payload.vat_lines if row.source_text) or None,
    )
    return result


def _validation_json(row: ValidationResult) -> dict[str, Any]:
    return {
        "code": row.code,
        "severity": row.severity.value,
        "field": row.field_name,
        "message": row.message,
        "expected": row.expected,
        "actual": row.actual,
    }


def _validation_summary(rows: list[ValidationResult]) -> dict[str, int]:
    return {
        "ok": sum(row.severity == ValidationSeverity.OK for row in rows),
        "warning": sum(row.severity == ValidationSeverity.WARNING for row in rows),
        "blocking_error": sum(
            row.severity == ValidationSeverity.BLOCKING_ERROR for row in rows
        ),
    }


def queue_ai_extraction(
    db: Session,
    invoice: Invoice,
    settings: Settings,
    actor: str = "system",
    *,
    reextraction: bool = False,
) -> AIExtraction:
    if not settings.ai_extraction_enabled:
        raise ValueError("AI extraction is disabled")
    if not invoice.paperless_ocr_text.strip():
        raise ValueError("Invoice has no OCR text")
    active = db.scalar(
        select(AIExtraction)
        .where(
            AIExtraction.invoice_id == invoice.id,
            AIExtraction.status.in_(
                [AIExtractionStatus.AI_PENDING, AIExtractionStatus.AI_PROCESSING]
            ),
        )
        .order_by(AIExtraction.extraction_revision.desc())
        .limit(1)
    )
    if active is not None:
        return active

    revision = (
        db.scalar(
            select(func.max(AIExtraction.extraction_revision)).where(
                AIExtraction.invoice_id == invoice.id
            )
        )
        or 0
    ) + 1
    extraction = AIExtraction(
        invoice_id=invoice.id,
        invoice_revision_id=invoice.current_revision.id if invoice.current_revision else None,
        extraction_revision=revision,
        model=settings.ollama_model,
        schema_version=SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        status=AIExtractionStatus.AI_PENDING,
        requires_confirmation=revision > 1,
    )
    db.add(extraction)
    db.flush()
    invoice.ai_status = AIExtractionStatus.AI_PENDING
    enqueue_job(
        db,
        AI_JOB_TYPE,
        f"ai-extraction:{extraction.id}",
        invoice_id=invoice.id,
        payload={"ai_extraction_id": extraction.id},
        max_attempts=settings.ai_extraction_max_attempts,
    )
    if reextraction or revision > 1:
        record_event(
            db,
            "AI_REEXTRACTION_REQUESTED",
            actor=actor,
            invoice=invoice,
            metadata={"ai_extraction_id": extraction.id, "extraction_revision": revision},
        )
    record_event(
        db,
        "AI_EXTRACTION_QUEUED",
        actor=actor,
        invoice=invoice,
        metadata={
            "ai_extraction_id": extraction.id,
            "extraction_revision": revision,
            "model": settings.ollama_model,
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
        },
    )
    return extraction


def start_ai_extraction(db: Session, extraction: AIExtraction) -> None:
    extraction.status = AIExtractionStatus.AI_PROCESSING
    extraction.started_at = datetime.now(UTC)
    extraction.error_code = None
    extraction.error_message = None
    extraction.invoice.ai_status = AIExtractionStatus.AI_PROCESSING
    record_event(
        db,
        "AI_EXTRACTION_STARTED",
        invoice=extraction.invoice,
        metadata={
            "ai_extraction_id": extraction.id,
            "extraction_revision": extraction.extraction_revision,
            "model": extraction.model,
        },
    )


def _replace_current_evidence(
    db: Session, invoice: Invoice, extraction: AIExtraction, payload: InvoiceExtractionV1
) -> None:
    revision = invoice.current_revision
    if revision is None:
        raise ValueError("Invoice has no current revision")
    db.execute(delete(ExtractedField).where(ExtractedField.revision_id == revision.id))
    for field, (value, source_text) in _evidence(payload).items():
        db.add(
            ExtractedField(
                revision_id=revision.id,
                field_name=field,
                value=value,
                source_text=source_text,
            )
        )
    extraction.invoice_revision_id = revision.id


def complete_ai_extraction(
    db: Session,
    extraction: AIExtraction,
    result: OllamaExtractionResult,
) -> list[ValidationResult]:
    invoice = extraction.invoice
    payload = result.payload
    data = extraction_to_invoice_data(payload)
    candidate_validations = validate_invoice_data(data)
    extraction.raw_response = result.raw_response
    extraction.parsed_result = payload.model_dump(mode="json")
    extraction.validation_results_json = [_validation_json(row) for row in candidate_validations]
    extraction.validation_summary = _validation_summary(candidate_validations)
    extraction.duration_ms = result.duration_ms
    extraction.completed_at = datetime.now(UTC)
    extraction.status = AIExtractionStatus.AI_COMPLETED
    extraction.error_code = None
    extraction.error_message = None
    invoice.ai_status = AIExtractionStatus.AI_COMPLETED

    current = invoice.current_revision
    prior_applied = db.scalar(
        select(AIExtraction.id)
        .where(
            AIExtraction.invoice_id == invoice.id,
            AIExtraction.id != extraction.id,
            AIExtraction.applied.is_(True),
        )
        .limit(1)
    )
    auto_apply = bool(
        current is not None
        and prior_applied is None
        and not any(value not in (None, "", [], {}) for value in current.data.values())
    )
    if auto_apply:
        current.data = data
        _replace_current_evidence(db, invoice, extraction, payload)
        extraction.applied = True
        extraction.applied_at = datetime.now(UTC)
        extraction.applied_by = "system"
        extraction.requires_confirmation = False
        candidate_validations = run_validations(db, invoice)
        extraction.validation_results_json = [
            _validation_json(row) for row in candidate_validations
        ]
        extraction.validation_summary = _validation_summary(candidate_validations)
        record_event(
            db,
            "AI_EXTRACTION_APPLIED",
            invoice=invoice,
            metadata={
                "ai_extraction_id": extraction.id,
                "extraction_revision": extraction.extraction_revision,
                "automatic": True,
            },
        )

    record_event(
        db,
        "AI_EXTRACTED",
        invoice=invoice,
        metadata={
            "ai_extraction_id": extraction.id,
            "extraction_revision": extraction.extraction_revision,
            "model": extraction.model,
            "schema_version": extraction.schema_version,
            "prompt_version": extraction.prompt_version,
            "duration_ms": extraction.duration_ms,
            "applied": extraction.applied,
            "requires_confirmation": extraction.requires_confirmation,
        },
    )
    if not auto_apply:
        record_event(
            db,
            "VALIDATION_FINISHED",
            invoice=invoice,
            metadata={
                "ai_extraction_id": extraction.id,
                **extraction.validation_summary,
                "candidate_only": True,
            },
        )
    return candidate_validations


def apply_ai_extraction(
    db: Session,
    invoice: Invoice,
    extraction: AIExtraction,
    actor: str,
    *,
    confirm_overwrite: bool,
) -> None:
    if extraction.invoice_id != invoice.id:
        raise ValueError("AI extraction does not belong to the invoice")
    if extraction.status != AIExtractionStatus.AI_COMPLETED or extraction.parsed_result is None:
        raise ValueError("AI extraction is not completed")
    if extraction.applied:
        return
    if extraction.requires_confirmation and not confirm_overwrite:
        raise ValueError("Applying re-extraction requires explicit overwrite confirmation")

    payload = InvoiceExtractionV1.model_validate(extraction.parsed_result)
    data = extraction_to_invoice_data(payload)
    update_invoice_data(
        db,
        invoice,
        data,
        actor,
        comment=f"Potvrzeno použití AI extrakce {extraction.extraction_revision}",
    )
    _replace_current_evidence(db, invoice, extraction, payload)
    run_validations(db, invoice, actor)
    extraction.applied = True
    extraction.applied_at = datetime.now(UTC)
    extraction.applied_by = actor
    extraction.requires_confirmation = False
    record_event(
        db,
        "AI_EXTRACTION_APPLIED",
        actor=actor,
        invoice=invoice,
        metadata={
            "ai_extraction_id": extraction.id,
            "extraction_revision": extraction.extraction_revision,
            "automatic": False,
        },
    )


def mark_ai_extraction_failed(
    db: Session,
    extraction: AIExtraction,
    *,
    code: str,
    message: str,
    final: bool,
) -> None:
    extraction.error_code = code
    extraction.error_message = message[:4000]
    if final:
        extraction.status = AIExtractionStatus.AI_FAILED
        extraction.completed_at = datetime.now(UTC)
        extraction.invoice.ai_status = AIExtractionStatus.AI_FAILED
        record_event(
            db,
            "AI_EXTRACTION_FAILED",
            invoice=extraction.invoice,
            comment=extraction.error_message,
            metadata={
                "ai_extraction_id": extraction.id,
                "extraction_revision": extraction.extraction_revision,
                "error_code": code,
            },
        )
    else:
        extraction.status = AIExtractionStatus.AI_PENDING
        extraction.invoice.ai_status = AIExtractionStatus.AI_PENDING
        record_event(
            db,
            "AI_EXTRACTION_RETRY_SCHEDULED",
            invoice=extraction.invoice,
            comment=extraction.error_message,
            metadata={
                "ai_extraction_id": extraction.id,
                "extraction_revision": extraction.extraction_revision,
                "error_code": code,
            },
        )
