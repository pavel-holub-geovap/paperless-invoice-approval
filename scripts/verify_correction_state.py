#!/usr/bin/env python3
"""Read-only final evidence for source/disposition reconciliation."""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from typing import Any

from app.config import get_settings
from app.db import SessionLocal
from app.integrations.paperless import PaperlessClient, PaperlessNotFound
from app.models import AuditEvent, Invoice, ValidationResult
from smoke_stage_b import login, response_json
from sqlalchemy import func, select


async def main() -> None:
    settings = get_settings()
    paperless = PaperlessClient(settings)
    try:
        paperless_ids = set()
        async for document in paperless.iter_documents():
            paperless_ids.add(document.id)

        with SessionLocal() as db:
            invoices = db.scalars(select(Invoice).order_by(Invoice.paperless_document_id)).all()
            source_counts = Counter(row.source_status.value for row in invoices)
            disposition_counts = Counter(row.disposition.value for row in invoices)
            missing_rows: list[dict[str, Any]] = []
            for invoice in invoices:
                if invoice.source_status.value != "MISSING":
                    continue
                direct_get = "UNEXPECTED_AVAILABLE"
                try:
                    await paperless.get_document(invoice.paperless_document_id)
                except PaperlessNotFound:
                    direct_get = "HTTP_404"
                missing_audits = db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.invoice_id == invoice.id,
                        AuditEvent.event_type == "SOURCE_DOCUMENT_MISSING",
                    )
                )
                restored_audits = db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.invoice_id == invoice.id,
                        AuditEvent.event_type == "SOURCE_DOCUMENT_RESTORED",
                    )
                )
                validation_codes = set(
                    db.scalars(
                        select(ValidationResult.code).where(
                            ValidationResult.revision_id == invoice.current_revision.id
                        )
                    ).all()
                )
                missing_rows.append(
                    {
                        "invoice_id": invoice.id,
                        "paperless_document_id": invoice.paperless_document_id,
                        "workflow_status": invoice.status.value,
                        "disposition": invoice.disposition.value,
                        "source_missing_at": invoice.source_missing_at,
                        "paperless_get": direct_get,
                        "source_missing_audit_count": missing_audits,
                        "source_restored_audit_count": restored_audits,
                        "blocking_validation_present": "SOURCE_DOCUMENT_MISSING"
                        in validation_codes,
                    }
                )

        base_url = os.environ["APP_BASE_URL"].rstrip("/")
        manager = login(base_url, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"])
        try:
            identity = response_json(
                manager.get(f"{base_url}/api/auth/me"), "manager identity"
            )
            headers = {"X-CSRF-Token": identity["csrf_token"]}
            orphan_document_id = int(os.environ.get("CORRECTION_ORPHAN_DOCUMENT_ID", "3"))
            all_rows = response_json(
                manager.get(f"{base_url}/api/invoices", params={"view": "all"}),
                "all invoices",
            )
            orphan = next(
                row
                for row in all_rows
                if row["paperless_document_id"] == orphan_document_id
            )
            detail = response_json(
                manager.get(f"{base_url}/api/invoices/{orphan['id']}"),
                "orphan detail",
            )
            guards = {
                "pdf_http": manager.get(
                    f"{base_url}/api/invoices/{orphan['id']}/pdf"
                ).status_code,
                "submit_http": manager.post(
                    f"{base_url}/api/invoices/{orphan['id']}/submit", headers=headers
                ).status_code,
                "export_http": manager.post(
                    f"{base_url}/api/exports/invoices/{orphan['id']}/generate",
                    headers=headers,
                    json={"reason": "final missing-source guard evidence"},
                ).status_code,
                "active_assignments": sum(
                    len(allocation["assignments"])
                    for allocation in detail["allocations"]
                ),
            }
        finally:
            manager.close()

        print(
            json.dumps(
                {
                    "paperless_document_count": len(paperless_ids),
                    "approval_invoice_count": sum(source_counts.values()),
                    "source_counts": dict(source_counts),
                    "disposition_counts": dict(disposition_counts),
                    "missing": missing_rows,
                    "orphan_guards": guards,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    finally:
        await paperless.close()


if __name__ == "__main__":
    asyncio.run(main())
