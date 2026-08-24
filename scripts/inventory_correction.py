#!/usr/bin/env python3
"""Read-only inventory for the post-Stage-F correction smoke tests."""

from __future__ import annotations

import asyncio
import json
from collections import Counter

from app.config import get_settings
from app.db import SessionLocal
from app.integrations.paperless import PaperlessClient
from app.models import Invoice, ValidationResult
from sqlalchemy import select


async def main() -> None:
    paperless = PaperlessClient(get_settings())
    try:
        documents = []
        async for row in paperless.iter_documents():
            documents.append(
                {
                    "paperless_document_id": row.id,
                    "title": row.title,
                    "original_filename": row.original_filename,
                    "ocr_length": len(row.content),
                    "tag_ids": list(row.tags),
                }
            )
        available_ids = {row["paperless_document_id"] for row in documents}
    finally:
        await paperless.close()

    with SessionLocal() as db:
        invoices = db.scalars(select(Invoice).order_by(Invoice.paperless_document_id)).all()
        approval_rows = []
        for invoice in invoices:
            revision = invoice.current_revision
            validation_rows = db.scalars(
                select(ValidationResult).where(
                    ValidationResult.revision_id == revision.id
                )
            ).all()
            severities = Counter(row.severity.value for row in validation_rows)
            data = revision.data
            approval_rows.append(
                {
                    "invoice_id": invoice.id,
                    "paperless_document_id": invoice.paperless_document_id,
                    "paperless_exists": invoice.paperless_document_id in available_ids,
                    "workflow_status": invoice.status.value,
                    "disposition": invoice.disposition.value,
                    "disposition_reason": invoice.disposition_reason,
                    "duplicate_of_invoice_id": invoice.duplicate_of_invoice_id,
                    "source_status": invoice.source_status.value,
                    "source_missing_at": invoice.source_missing_at.isoformat()
                    if invoice.source_missing_at
                    else None,
                    "ai_status": invoice.ai_status.value,
                    "supplier": data.get("supplier_name"),
                    "invoice_number": data.get("invoice_number"),
                    "variable_symbol": data.get("variable_symbol"),
                    "total": data.get("total_amount"),
                    "currency": data.get("currency"),
                    "bank_account": data.get("bank_account"),
                    "bank_account_raw": data.get("bank_account_raw"),
                    "bank_account_prefix": data.get("bank_account_prefix"),
                    "bank_account_number": data.get("bank_account_number"),
                    "bank_code": data.get("bank_code"),
                    "iban": data.get("iban"),
                    "swift_bic": data.get("swift_bic"),
                    "validations": dict(severities),
                    "validation_findings": [
                        {
                            "code": row.code,
                            "severity": row.severity.value,
                            "field": row.field_name,
                            "expected": row.expected,
                            "actual": row.actual,
                            "details": row.details,
                        }
                        for row in validation_rows
                        if row.severity.value != "OK"
                    ],
                }
            )

    print(
        json.dumps(
            {
                "paperless_count": len(documents),
                "approval_count": len(approval_rows),
                "paperless_documents": documents,
                "approval_invoices": approval_rows,
                "orphan_paperless_document_ids": [
                    row["paperless_document_id"]
                    for row in approval_rows
                    if not row["paperless_exists"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
