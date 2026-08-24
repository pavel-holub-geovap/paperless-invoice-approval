#!/usr/bin/env python3
"""Append-only re-extraction and normalized address/VAT report for real GIRITON PDFs."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from app.schemas import InvoiceExtractionV1
from app.services.extraction import extraction_to_invoice_data
from smoke_stage_b import login, require, response_json


def detail(client, base_url: str, invoice_id: str) -> dict[str, Any]:
    return response_json(
        client.get(f"{base_url}/api/invoices/{invoice_id}"), "invoice detail"
    )


def wait_for_run(client, base_url: str, invoice_id: str, after_revision: int) -> dict[str, Any]:
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        current = detail(client, base_url, invoice_id)
        latest = current["ai"]["latest"]
        if latest and latest["extraction_revision"] > after_revision:
            if latest["status"] == "AI_COMPLETED":
                return current
            if latest["status"] == "AI_FAILED":
                raise RuntimeError(
                    f"Paperless {current['paperless_document_id']}: "
                    f"{latest['error_code']} {latest['error_message']}"
                )
        time.sleep(3)
    raise RuntimeError(f"AI extraction timeout for {invoice_id}")


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    requested_number = os.environ.get("GIRITON_INVOICE_NUMBER", "").strip()
    count = int(os.environ.get("GIRITON_SMOKE_COUNT", "2"))
    skip_extraction = os.environ.get("GIRITON_SKIP_EXTRACTION") == "1"
    manager = login(
        base_url, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"]
    )
    try:
        user = response_json(manager.get(f"{base_url}/api/auth/me"), "manager /me")
        rows = response_json(
            manager.get(f"{base_url}/api/invoices?view=all&sort=source_desc"),
            "invoice list",
        )
        selected: list[dict[str, Any]] = []
        for row in rows:
            current = detail(manager, base_url, row["id"])
            ocr_upper = current["paperless"]["ocr_text"].upper()
            if current["source"]["status"] != "AVAILABLE" or "GIRITON" not in ocr_upper:
                continue
            if requested_number and requested_number not in current["paperless"]["ocr_text"]:
                continue
            selected.append(current)
            if len(selected) == count:
                break
        require(selected, "No matching available GIRITON invoice found")

        report: list[dict[str, Any]] = []
        for current in selected:
            latest = current["ai"].get("latest")
            if skip_extraction:
                require(latest and latest["status"] == "AI_COMPLETED", "No completed extraction")
                completed = current
            else:
                after_revision = latest["extraction_revision"] if latest else 0
                queued = manager.post(
                    f"{base_url}/api/invoices/{current['id']}/ai-extractions",
                    headers={"X-CSRF-Token": user["csrf_token"]},
                )
                require(queued.status_code == 202, f"Queue returned HTTP {queued.status_code}")
                completed = wait_for_run(manager, base_url, current["id"], after_revision)
            run = completed["ai"]["latest"]
            require(run["model"] == "qwen3:8b", "Unexpected model")
            require(run["schema_version"] == "invoice-extraction.v3", "Unexpected schema")
            parsed = run["parsed_result"]
            normalized = extraction_to_invoice_data(
                InvoiceExtractionV1.model_validate(parsed), completed["paperless"]["ocr_text"]
            )
            vat_validations = [
                row for row in run.get("validation_results", [])
                if row["code"].startswith("VAT_") or row["code"] == "TOTAL_MATH_OK"
            ]
            report.append(
                {
                    "paperless_document_id": completed["paperless_document_id"],
                    "invoice_id": completed["id"],
                    "workflow_preserved": completed["status"],
                    "extraction_revision": run["extraction_revision"],
                    "duration_ms": run["duration_ms"],
                    "candidate_applied": run["applied"],
                    "raw_evidence": {
                        key: parsed.get(key)
                        for key in ("total_without_vat", "total_vat", "total_amount")
                    },
                    "normalized": {
                        key: normalized.get(key)
                        for key in (
                            "supplier_name", "supplier_address_raw", "supplier_street",
                            "supplier_city", "supplier_zip", "invoice_number",
                            "variable_symbol", "bank_account_number", "bank_code",
                            "vat_lines", "total_without_vat", "total_vat", "total_amount",
                        )
                    },
                    "vat_validations": vat_validations,
                    "blocking_vat_validations": [
                        row["code"] for row in vat_validations
                        if row["severity"] == "BLOCKING_ERROR"
                    ],
                }
            )
        print(json.dumps({"documents": report}, ensure_ascii=False, indent=2))
    finally:
        manager.close()


if __name__ == "__main__":
    main()
