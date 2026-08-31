#!/usr/bin/env python3
"""Reprocess and verify the existing Pixel Design ISDOC invoice end to end."""

from __future__ import annotations

import hashlib
import json
import os

from app.db import SessionLocal
from app.models import AIExtraction, IsdocExtraction, ValidationResult
from app.services.isdoc import enumerate_attachments
from smoke_isdoc_approved_pdf import api, approve, classify, detail, wait_detail
from smoke_stage_b import login, require, response_json
from sqlalchemy import func, select

INVOICE_ID = "5ea0bd1a-7694-42de-9693-7cc242252455"
PAPERLESS_DOCUMENT_ID = 50
ISDOC_FILENAME = "Vydaná faktura - 260104-invoice.isdoc"


def database_state(invoice_id: str) -> dict[str, object]:
    with SessionLocal() as db:
        extraction = db.scalar(
            select(IsdocExtraction)
            .where(IsdocExtraction.invoice_id == invoice_id)
            .order_by(IsdocExtraction.created_at.desc())
        )
        ai_runs = db.scalar(
            select(func.count()).select_from(AIExtraction).where(
                AIExtraction.invoice_id == invoice_id
            )
        )
        validation_codes = db.scalars(
            select(ValidationResult.code).where(
                ValidationResult.revision_id == extraction.invoice_revision_id
            )
        ).all() if extraction else []
        return {
            "ai_runs": int(ai_runs or 0),
            "isdoc_extraction_id": extraction.id if extraction else None,
            "isdoc_revision_id": extraction.invoice_revision_id if extraction else None,
            "invoice_number_provenance": (
                extraction.provenance.get("invoice_number") if extraction else None
            ),
            "validation_codes": sorted(validation_codes),
        }


def main() -> None:
    base = os.environ["APP_BASE_URL"].rstrip("/")
    manager = login(base, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"])
    approver = login(base, "approver1", os.environ["TEST_APPROVER_1_PASSWORD"])
    try:
        manager_user = response_json(manager.get(f"{base}/api/auth/me"), "manager /me")
        approver_user = response_json(approver.get(f"{base}/api/auth/me"), "approver /me")
        before = detail(manager, base, INVOICE_ID)
        require(
            before["paperless_document_id"] == PAPERLESS_DOCUMENT_ID,
            "Unexpected Pixel Design Paperless document",
        )
        before_db = database_state(INVOICE_ID)
        original_before = manager.get(f"{base}/api/invoices/{INVOICE_ID}/pdf")
        require(original_before.status_code == 200, "Original Pixel PDF download failed")

        api(
            manager,
            "POST",
            f"{base}/api/invoices/{INVOICE_ID}/isdoc-reprocess",
            manager_user,
            expected=202,
        )
        current = wait_detail(
            manager,
            base,
            INVOICE_ID,
            lambda row: (
                row["isdoc"]["status"] == "VALID"
                and row["classification"]["extraction_source"] == "ISDOC"
                and row["current_revision_number"] > before["current_revision_number"]
            ),
            "Pixel Design ISDOC reprocessing",
            180,
        )
        expected = {
            "invoice_number": "260104",
            "supplier_name": "Pixel Design s.r.o.",
            "supplier_ico": "06668712",
            "supplier_dic": "CZ06668712",
            "variable_symbol": "260104",
            "issue_date": "2026-03-02",
            "taxable_supply_date": "2026-03-02",
            "due_date": "2026-03-09",
            "bank_account_prefix": "115",
            "bank_account_number": "5596880207",
            "bank_code": "0100",
            "iban": "CZ9001000001155596880207",
            "swift_bic": "KOMBCZPPXXX",
            "total_without_vat": "4300.00",
            "total_vat": "903.00",
            "total_amount": "5203.00",
        }
        for field, value in expected.items():
            require(current["data"].get(field) == value, f"{field} mapping mismatch")
        require(current["isdoc"]["filename"] == ISDOC_FILENAME, "ISDOC filename mismatch")
        require(current["isdoc"]["version"] == "6.0.2", "ISDOC version mismatch")
        require(current["data"]["vat_lines"][0]["vat_rate"] == "21", "VAT rate mismatch")
        require(
            current["data"]["vat_lines"][0]["adjustment_type"] is None,
            "False ISDOC rounding row detected",
        )
        after_reprocess_db = database_state(INVOICE_ID)
        require(
            after_reprocess_db["ai_runs"] == before_db["ai_runs"],
            "Reprocessing unexpectedly queued Qwen3",
        )
        require(
            "VAT_ROUNDING_ADJUSTMENT" not in after_reprocess_db["validation_codes"],
            "False rounding validation detected",
        )

        if current["classification"]["document_type"] != "RECEIVED_INVOICE" or current[
            "classification"
        ]["processing_mode"] != "FOR_APPROVAL":
            current = classify(
                manager, base, manager_user, current, "RECEIVED_INVOICE"
            )
        centres = response_json(manager.get(f"{base}/api/cost-centers"), "cost centres")
        centre_id = next(row["id"] for row in centres if row["active"])
        approved = approve(
            manager,
            approver,
            base,
            manager_user,
            approver_user,
            current,
            centre_id,
        )
        require(
            approved["classification"]["pohoda_import_method"] == "PDF_ISDOC",
            "Pixel invoice is not routed as PDF_ISDOC",
        )
        generated = api(
            manager,
            "POST",
            f"{base}/api/exports/invoices/{INVOICE_ID}/generate",
            manager_user,
            {"reason": None},
            expected=409,
        )
        approved_pdf = manager.get(f"{base}/api/invoices/{INVOICE_ID}/approved-pdf")
        original_after = manager.get(f"{base}/api/invoices/{INVOICE_ID}/pdf")
        require(approved_pdf.status_code == 200, "Approved Pixel PDF download failed")
        require(original_after.status_code == 200, "Original Pixel PDF re-download failed")
        require(original_after.content == original_before.content, "Original Pixel PDF changed")
        original_manifest = {
            row.filename: row.sha256 for row in enumerate_attachments(original_after.content)
        }
        approved_manifest = {
            row.filename: row.sha256 for row in enumerate_attachments(approved_pdf.content)
        }
        require(original_manifest == approved_manifest, "Approved PDF changed attachments")
        require(ISDOC_FILENAME in original_manifest, "Pixel ISDOC attachment is missing")

        result = {
            "app_url": base,
            "paperless_document_id": PAPERLESS_DOCUMENT_ID,
            "invoice_id": INVOICE_ID,
            "revision_before": before["current_revision_number"],
            "revision_after_isdoc": current["current_revision_number"],
            "embedded_filename": approved["isdoc"]["filename"],
            "isdoc_version": approved["isdoc"]["version"],
            "isdoc_status": approved["isdoc"]["status"],
            "extraction_source": approved["classification"]["extraction_source"],
            "mapped_data": {field: approved["data"].get(field) for field in expected},
            "vat_lines": approved["data"]["vat_lines"],
            "invoice_items": approved["data"]["invoice_items"],
            "new_qwen_runs": after_reprocess_db["ai_runs"] - before_db["ai_runs"],
            "historical_ai_runs_preserved": after_reprocess_db["ai_runs"],
            "isdoc_extraction_id": after_reprocess_db["isdoc_extraction_id"],
            "invoice_number_provenance": after_reprocess_db[
                "invoice_number_provenance"
            ],
            "rounding_validation_present": "VAT_ROUNDING_ADJUSTMENT"
            in after_reprocess_db["validation_codes"],
            "approved_pdf": approved["approved_pdf"],
            "original_pdf_sha256": hashlib.sha256(original_after.content).hexdigest(),
            "approved_pdf_sha256": hashlib.sha256(approved_pdf.content).hexdigest(),
            "original_isdoc_sha256": original_manifest[ISDOC_FILENAME],
            "approved_isdoc_sha256": approved_manifest[ISDOC_FILENAME],
            "embedded_hashes_equal": original_manifest[ISDOC_FILENAME]
            == approved_manifest[ISDOC_FILENAME],
            "pohoda_import_method": approved["classification"]["pohoda_import_method"],
            "generated_xml_http": generated.status_code,
            "generated_xml_error": generated.json().get("detail"),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        manager.close()
        approver.close()


if __name__ == "__main__":
    main()
