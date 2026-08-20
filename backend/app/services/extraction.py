from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.models import ExtractedField, Invoice, InvoiceStatus
from app.schemas import ExtractionPayload
from app.services.audit import record_event
from app.services.validation import run_validations
from app.services.workflow import transition

DATE_FIELDS = {"issue_date", "taxable_supply_date", "due_date"}
DECIMAL_FIELDS = {"total_amount"}


def _normalize(field: str, value: Any) -> Any:
    if value is None:
        return None
    if field in DATE_FIELDS:
        return date.fromisoformat(str(value)).isoformat()
    if field in DECIMAL_FIELDS:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    if field == "vat_breakdown":
        return [row.model_dump(mode="json") for row in value]
    if field == "currency":
        return str(value).upper()
    return value


def apply_extraction(db: Session, invoice: Invoice, payload: ExtractionPayload) -> None:
    revision = invoice.current_revision
    if revision is None:
        raise ValueError("Invoice has no revision")
    data: dict[str, Any] = {}
    for field, evidence in payload:
        try:
            normalized = _normalize(field, evidence.value)
        except (ValueError, InvalidOperation):
            normalized = evidence.value
        data[field] = normalized
        db.add(
            ExtractedField(
                revision_id=revision.id,
                field_name=field,
                value=normalized,
                source_text=evidence.source_text,
            )
        )
    revision.data = data
    record_event(db, "AI_EXTRACTED", invoice=invoice, new_value=data)
    transition(db, invoice, InvoiceStatus.VALIDATION, "system")
    results = run_validations(db, invoice)
    has_blocking = any(result.severity.value == "BLOCKING_ERROR" for result in results)
    transition(
        db,
        invoice,
        InvoiceStatus.NEEDS_REVIEW if has_blocking else InvoiceStatus.QUEUE_REVIEW,
        "system",
    )
