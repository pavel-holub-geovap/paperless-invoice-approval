#!/usr/bin/env python3
"""Read-only DB diagnostics for AI extraction/current invoice form values."""

from __future__ import annotations

import json
import os

from app.db import SessionLocal
from app.models import Invoice
from app.schemas import InvoiceExtractionV1
from app.services.extraction import extraction_to_invoice_data
from sqlalchemy import select
from sqlalchemy.orm import selectinload

FIELDS = (
    "supplier_name",
    "supplier_ico",
    "supplier_dic",
    "supplier_address_raw",
    "supplier_street",
    "supplier_city",
    "supplier_zip",
    "invoice_number",
    "variable_symbol",
    "issue_date",
    "taxable_supply_date",
    "due_date",
    "currency",
    "bank_account_prefix",
    "bank_account_number",
    "bank_code",
    "iban",
    "swift_bic",
    "total_without_vat",
    "total_vat",
    "total_amount",
)


def candidate_value(payload: dict, field: str):
    value = payload.get(field)
    return value.get("value") if isinstance(value, dict) else value


def compatible_v3(payload: dict) -> dict:
    stored = dict(payload)
    if stored.get("schema_version") == "invoice-extraction.v1":
        legacy_address = stored.pop(
            "supplier_address", {"value": None, "source_text": None}
        )
        stored.update(
            {
                "schema_version": "invoice-extraction.v3",
                "supplier_address_raw": legacy_address,
                "supplier_street": {"value": None, "source_text": None},
                "supplier_city": {"value": None, "source_text": None},
                "supplier_zip": {"value": None, "source_text": None},
            }
        )
    elif stored.get("schema_version") == "invoice-extraction.v2":
        stored["schema_version"] = "invoice-extraction.v3"
    for row in stored.get("vat_lines", []):
        row.setdefault("gross_amount", None)
    return stored


def main() -> None:
    document_ids = {
        int(value)
        for value in os.environ.get("FORM_DIAGNOSTIC_DOCUMENT_IDS", "10,11").split(",")
        if value.strip()
    }
    with SessionLocal() as db:
        invoices = db.scalars(
            select(Invoice).options(
                selectinload(Invoice.revisions),
                selectinload(Invoice.ai_extractions),
            )
        ).all()
        output = []
        backfill_candidates = []
        for invoice in invoices:
            current = invoice.current_revision
            for extraction in invoice.ai_extractions:
                if (
                    not extraction.applied
                    or not extraction.parsed_result
                    or current is None
                    or extraction.invoice_revision_id != current.id
                ):
                    continue
                try:
                    payload = InvoiceExtractionV1.model_validate(
                        compatible_v3(extraction.parsed_result)
                    )
                    normalized = extraction_to_invoice_data(
                        payload, invoice.paperless_ocr_text
                    )
                except (TypeError, ValueError) as exc:
                    backfill_candidates.append(
                        {
                            "invoice_id": invoice.id,
                            "paperless_document_id": invoice.paperless_document_id,
                            "error": type(exc).__name__,
                        }
                    )
                    continue
                missing = {
                    field: normalized.get(field)
                    for field in FIELDS
                    if current.data.get(field) in (None, "")
                    and normalized.get(field) not in (None, "")
                }
                if missing:
                    backfill_candidates.append(
                        {
                            "invoice_id": invoice.id,
                            "paperless_document_id": invoice.paperless_document_id,
                            "extraction_revision": extraction.extraction_revision,
                            "unambiguous_empty_current_fields": missing,
                        }
                    )
            if invoice.paperless_document_id not in document_ids:
                continue
            latest = invoice.ai_extractions[-1] if invoice.ai_extractions else None
            payload = (latest.parsed_result or {}) if latest else {}
            output.append(
                {
                    "paperless_document_id": invoice.paperless_document_id,
                    "invoice_id": invoice.id,
                    "current_revision": invoice.current_revision_number,
                    "revision_created_by": current.created_by if current else None,
                    "latest_extraction_revision": latest.extraction_revision if latest else None,
                    "latest_extraction_status": latest.status if latest else None,
                    "latest_extraction_applied": latest.applied if latest else None,
                    "latest_extraction_applied_by": latest.applied_by if latest else None,
                    "candidate": {field: candidate_value(payload, field) for field in FIELDS},
                    "current": {
                        field: current.data.get(field) if current else None for field in FIELDS
                    },
                }
            )
        print(
            json.dumps(
                {"documents": output, "backfill_candidates": backfill_candidates},
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )


if __name__ == "__main__":
    main()
