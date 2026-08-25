#!/usr/bin/env python3
"""Run append-only Qwen3 8B candidate extraction on several existing invoices."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from smoke_stage_b import login, require, response_json

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
    "bank_account",
    "bank_code",
    "iban",
    "issue_date",
    "due_date",
    "vat_lines",
    "total_without_vat",
    "total_vat",
    "total_amount",
)


def value(result: dict[str, Any], field: str) -> Any:
    wrapped = result.get(field)
    return wrapped.get("value") if isinstance(wrapped, dict) else wrapped


def detail(client, base_url: str, invoice_id: str) -> dict[str, Any]:
    return response_json(
        client.get(f"{base_url}/api/invoices/{invoice_id}"), "invoice detail"
    )


def wait_for_run(
    client, base_url: str, invoice_id: str, after_revision: int
) -> dict[str, Any]:
    deadline = time.monotonic() + 1900
    while time.monotonic() < deadline:
        current = detail(client, base_url, invoice_id)
        latest = current["ai"]["latest"]
        if latest and latest["extraction_revision"] > after_revision:
            if latest["status"] == "AI_COMPLETED":
                return current
            if latest["status"] == "AI_FAILED":
                raise RuntimeError(
                    f"Document {current['paperless_document_id']} failed: "
                    f"{latest['error_code']} {latest['error_message']}"
                )
        time.sleep(3)
    raise RuntimeError(f"AI extraction timeout for {invoice_id}")


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    count = int(os.environ.get("QWEN8_SMOKE_COUNT", "3"))
    excluded = int(os.environ.get("QWEN8_EXCLUDE_DOCUMENT_ID", "0"))
    requested_ids = [
        int(value)
        for value in os.environ.get("QWEN8_DOCUMENT_IDS", "").split(",")
        if value.strip()
    ]
    manager = login(
        base_url,
        "queue-manager",
        os.environ["TEST_QUEUE_MANAGER_PASSWORD"],
    )
    try:
        user = response_json(manager.get(f"{base_url}/api/auth/me"), "manager /me")
        rows = response_json(
            manager.get(f"{base_url}/api/invoices?view=all&sort=source_asc"),
            "invoice list",
        )
        candidates: list[dict[str, Any]] = []
        for row in rows:
            if row["paperless_document_id"] == excluded:
                continue
            if requested_ids and row["paperless_document_id"] not in requested_ids:
                continue
            current = detail(manager, base_url, row["id"])
            if (
                current["source"]["status"] == "AVAILABLE"
                and current["disposition"]["status"] == "ACTIVE"
                and current["paperless"]["ocr_text"]
            ):
                candidates.append(current)
            if len(candidates) == (len(requested_ids) or count):
                break
        expected_count = len(requested_ids) or count
        require(
            len(candidates) == expected_count,
            f"Only {len(candidates)} of {expected_count} usable invoices found",
        )
        if requested_ids:
            candidates.sort(
                key=lambda row: requested_ids.index(row["paperless_document_id"])
            )

        report: list[dict[str, Any]] = []
        for current in candidates:
            latest = current["ai"].get("latest")
            if latest and latest["status"] in {"AI_PENDING", "AI_PROCESSING"}:
                current = wait_for_run(
                    manager,
                    base_url,
                    current["id"],
                    latest["extraction_revision"] - 1,
                )
                latest = current["ai"].get("latest")
            after_revision = latest["extraction_revision"] if latest else 0
            status_before = current["status"]
            queued = manager.post(
                f"{base_url}/api/invoices/{current['id']}/ai-extractions",
                headers={"X-CSRF-Token": user["csrf_token"]},
            )
            require(
                queued.status_code == 202,
                f"Queue extraction returned {queued.status_code}: {queued.text[:300]}",
            )
            completed = wait_for_run(manager, base_url, current["id"], after_revision)
            run = completed["ai"]["latest"]
            require(run["model"] == "qwen3:8b", "New extraction did not use Qwen3 8B")
            require(
                run["schema_version"] == "invoice-extraction.v3",
                "Canonical schema version is wrong",
            )
            require(
                run["raw_response_preserved"], "Raw Qwen response was not preserved"
            )
            require(
                run["normalization_result"].get("raw_schema_version")
                == "invoice-extraction.raw.v1",
                "Raw schema normalization metadata is missing",
            )
            require(completed["status"] == status_before, "Candidate changed workflow")
            parsed = run.get("parsed_result") or {}
            report.append(
                {
                    "paperless_document_id": completed["paperless_document_id"],
                    "invoice_id": completed["id"],
                    "ocr_length": len(completed["paperless"]["ocr_text"]),
                    "model": run["model"],
                    "extraction_revision": run["extraction_revision"],
                    "duration_ms": run["duration_ms"],
                    "schema_version": run["schema_version"],
                    "raw_schema_version": run["normalization_result"].get(
                        "raw_schema_version"
                    ),
                    "corrective_retry_count": run["corrective_retry_count"],
                    "prior_schema_errors": run["schema_validation_errors"],
                    "normalization_changes": run["normalization_result"].get(
                        "changes", []
                    ),
                    "canonical_validation": "PASSED",
                    "workflow_preserved": completed["status"],
                    "extracted": {field: value(parsed, field) for field in FIELDS},
                }
            )
        print(json.dumps({"documents": report}, ensure_ascii=False, indent=2))
    finally:
        manager.close()


if __name__ == "__main__":
    main()
