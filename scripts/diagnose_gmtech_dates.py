#!/usr/bin/env python3
"""Read-only end-to-end date diagnostics for the GMtech invoice."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from app.db import SessionLocal
from app.models import Invoice
from app.services.extraction import stored_extraction_to_invoice_data
from smoke_stage_b import login, require, response_json
from sqlalchemy import select
from sqlalchemy.orm import selectinload

DATE_FIELDS = ("issue_date", "taxable_supply_date", "due_date")
DATE_LABEL = re.compile(
    r"datum\s+(?:vystaven|splat|zd\.?\s*pln|zdan\.?\s*pln|uskutečnění)|\bduzp\b",
    re.IGNORECASE,
)


def evidence(payload: dict[str, Any], field: str) -> dict[str, Any]:
    item = payload.get(field)
    return item if isinstance(item, dict) else {"value": item, "source_text": None}


def main() -> None:
    with SessionLocal() as db:
        invoices = db.scalars(
            select(Invoice).options(
                selectinload(Invoice.revisions),
                selectinload(Invoice.ai_extractions),
            )
        ).all()
        invoice = next(
            (
                row
                for row in invoices
                if "gmtech" in row.paperless_title.casefold()
                or "gmtech" in str((row.current_revision.data if row.current_revision else {})).casefold()
                or "gmtech" in row.paperless_ocr_text.casefold()
            ),
            None,
        )
        require(invoice is not None, "GMtech invoice was not found")
        latest = max(invoice.ai_extractions, key=lambda row: row.extraction_revision)
        payload = latest.parsed_result or {}
        normalized = stored_extraction_to_invoice_data(latest, invoice.paperless_ocr_text)
        current = invoice.current_revision
        db_result = {
            "paperless_document_id": invoice.paperless_document_id,
            "invoice_id": invoice.id,
            "paperless_title": invoice.paperless_title,
            "ocr_date_lines": [
                line.strip()
                for line in invoice.paperless_ocr_text.splitlines()
                if DATE_LABEL.search(line)
            ],
            "latest_extraction_revision": latest.extraction_revision,
            "latest_extraction_applied": latest.applied,
            "raw_ai": {field: evidence(payload, field) for field in DATE_FIELDS},
            "normalized": {field: normalized.get(field) for field in DATE_FIELDS},
            "db_current_revision": current.number if current else None,
            "db_current": {
                field: current.data.get(field) if current else None for field in DATE_FIELDS
            },
            "extraction_history": [
                {
                    "revision": row.extraction_revision,
                    "applied": row.applied,
                    "dates": {
                        field: evidence(row.parsed_result or {}, field)
                        for field in DATE_FIELDS
                    },
                }
                for row in sorted(
                    invoice.ai_extractions,
                    key=lambda row: row.extraction_revision,
                )
            ],
        }

    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    manager = login(
        base_url,
        "queue-manager",
        os.environ["TEST_QUEUE_MANAGER_PASSWORD"],
    )
    try:
        detail = response_json(
            manager.get(f"{base_url}/api/invoices/{db_result['invoice_id']}"),
            "GMtech API detail",
        )
        db_result["api"] = {field: detail["data"].get(field) for field in DATE_FIELDS}
        db_result["api_evidence"] = {
            field: next(
                (
                    {
                        "value": item["value"],
                        "source_text": item["source_text"],
                    }
                    for item in detail["extracted_fields"]
                    if item["field_name"] == field
                ),
                None,
            )
            for field in DATE_FIELDS
        }
        print(json.dumps(db_result, ensure_ascii=False, indent=2, default=str))
    finally:
        manager.close()


if __name__ == "__main__":
    main()
