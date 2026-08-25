#!/usr/bin/env python3
"""Read-only inventory of Qwen schema failures without invoice contents."""

from __future__ import annotations

import json
import os
from collections import Counter
from typing import Any

from app.db import SessionLocal
from app.models import AIExtraction
from sqlalchemy import select
from sqlalchemy.orm import selectinload

DIAGNOSTIC_FIELDS = (
    "supplier_zip",
    "bank_account",
    "bank_code",
    "iban",
    "issue_date",
    "taxable_supply_date",
    "due_date",
    "vat_lines",
    "total_without_vat",
    "total_vat",
    "total_amount",
)


def selected_fields(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"invalid_payload_type": type(payload).__name__}
    return {field: payload.get(field) for field in DIAGNOSTIC_FIELDS}


def parsed_raw(raw_response: str) -> dict[str, Any]:
    try:
        return selected_fields(json.loads(raw_response))
    except (TypeError, json.JSONDecodeError) as exc:
        return {"invalid_json": str(exc)}


def main() -> None:
    diagnostic_document_id = int(
        os.environ.get("QWEN_DIAGNOSTIC_DOCUMENT_ID", "0")
    )
    with SessionLocal() as db:
        rows = db.scalars(
            select(AIExtraction)
            .where(
                AIExtraction.model == "qwen3:8b",
            )
            .options(selectinload(AIExtraction.invoice))
            .order_by(AIExtraction.created_at.desc())
            .limit(50)
        ).all()
        report = [
            {
                "extraction_id": row.id,
                "extraction_revision": row.extraction_revision,
                "paperless_document_id": row.invoice.paperless_document_id,
                "invoice_id": row.invoice_id,
                "title": row.invoice.paperless_title,
                "model": row.model,
                "schema_version": row.schema_version,
                "prompt_version": row.prompt_version,
                "status": row.status,
                "error_message": row.error_message,
                "raw_response_preserved": row.raw_response is not None,
                "schema_validation_errors": row.schema_validation_errors_json,
                "corrective_retry_count": row.corrective_retry_count,
                "created_at": row.created_at,
            }
            for row in rows
            if row.error_code == "SCHEMA_VALIDATION_FAILED"
        ]
        groups = Counter(
            (error.get("path", "unknown"), error.get("type", "unknown"))
            for row in report
            for error in row["schema_validation_errors"]
        )
        diagnostic = None
        if diagnostic_document_id:
            matching = [
                row
                for row in rows
                if row.invoice.paperless_document_id == diagnostic_document_id
            ]
            if matching:
                latest = matching[0]
                diagnostic = {
                    "paperless_document_id": diagnostic_document_id,
                    "extraction_id": latest.id,
                    "extraction_revision": latest.extraction_revision,
                    "status": latest.status,
                    "model": latest.model,
                    "prompt_version": latest.prompt_version,
                    "schema_version": latest.schema_version,
                    "corrective_retry_count": latest.corrective_retry_count,
                    "raw_attempts": [
                        {
                            "attempt": attempt.get("attempt"),
                            "selected_raw": parsed_raw(attempt.get("raw_response", "")),
                            "validation_errors": attempt.get("validation_errors", []),
                        }
                        for attempt in latest.raw_attempts_json
                    ],
                    "normalization_result": latest.normalization_result_json,
                    "selected_final": selected_fields(latest.parsed_result),
                }
        print(
            json.dumps(
                {
                    "failures": report,
                    "groups": [
                        {"path": path, "type": error_type, "count": count}
                        for (path, error_type), count in groups.most_common()
                    ],
                    "diagnostic": diagnostic,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )


if __name__ == "__main__":
    main()
