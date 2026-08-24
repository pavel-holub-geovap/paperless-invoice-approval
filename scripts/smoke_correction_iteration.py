#!/usr/bin/env python3
"""Smoke target identity, timestamps, conflict protection and enriched download audit."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from smoke_stage_b import login, require, response_json


def source_time(row: dict[str, Any]) -> datetime:
    value = row.get("paperless_created_at")
    require(bool(value), f"Invoice {row.get('id')} has no source timestamp")
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    document_id = int(os.environ.get("SMOKE_PAPERLESS_DOCUMENT_ID", "1"))
    manager = login(
        base_url,
        "queue-manager",
        os.environ["TEST_QUEUE_MANAGER_PASSWORD"],
    )
    try:
        user = response_json(manager.get(f"{base_url}/api/auth/me"), "manager /me")
        config = response_json(
            manager.get(f"{base_url}/api/exports/config"), "export config"
        )
        require(config["pohoda_target_ico"], "POHODA target IČO is missing")
        newest = response_json(
            manager.get(f"{base_url}/api/invoices?view=all&sort=source_desc"),
            "newest invoice list",
        )
        oldest = response_json(
            manager.get(f"{base_url}/api/invoices?view=all&sort=source_asc"),
            "oldest invoice list",
        )
        newest_times = [source_time(row) for row in newest if row.get("paperless_created_at")]
        oldest_times = [source_time(row) for row in oldest if row.get("paperless_created_at")]
        require(newest_times == sorted(newest_times, reverse=True), "Newest sort is wrong")
        require(oldest_times == sorted(oldest_times), "Oldest sort is wrong")
        row = next(
            (item for item in newest if item["paperless_document_id"] == document_id),
            None,
        )
        require(row is not None, f"Paperless document {document_id} is missing")
        detail = response_json(
            manager.get(f"{base_url}/api/invoices/{row['id']}"), "invoice detail"
        )

        stale = manager.patch(
            f"{base_url}/api/invoices/{row['id']}",
            headers={"X-CSRF-Token": user["csrf_token"]},
            json={
                "changes": {"supplier_name": detail["data"].get("supplier_name")},
                "expected_revision": max(1, detail["current_revision_number"] - 1),
            },
        )
        if detail["current_revision_number"] > 1:
            require(stale.status_code == 409, "Stale revision did not return HTTP 409")
            require(stale.json()["detail"]["code"] == "STALE_REVISION", "Conflict code is wrong")

        request_id = "correction-smoke-pdf-download"
        pdf = manager.get(
            f"{base_url}/api/invoices/{row['id']}/pdf",
            headers={"X-Request-ID": request_id},
        )
        require(pdf.status_code == 200, f"PDF download returned {pdf.status_code}")
        require(pdf.headers.get("X-Request-ID") == request_id, "Request ID was not echoed")
        audit = response_json(
            manager.get(f"{base_url}/api/invoices/{row['id']}/audit"), "invoice audit"
        )
        download = next(
            (event for event in reversed(audit) if event["event_type"] == "PDF_DOWNLOADED"),
            None,
        )
        require(download is not None, "PDF download audit is missing")
        require(download["metadata"].get("correlation_id") == request_id, "Audit correlation ID is missing")
        require(download["metadata"].get("actor_username") == "queue-manager", "Audit username is missing")

        print(
            json.dumps(
                {
                    "app_url": base_url,
                    "invoice_id": row["id"],
                    "paperless_document_id": document_id,
                    "source_created_at": detail["paperless"]["created_at"],
                    "approval_created_at": detail["created_at"],
                    "synced_at": detail["paperless"]["last_synced_at"],
                    "sort_newest": "OK",
                    "sort_oldest": "OK",
                    "stale_revision_http": stale.status_code,
                    "request_id": request_id,
                    "audit_event": download,
                    "pohoda_target": config,
                    "ocr_length": len(detail["paperless"]["ocr_text"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        manager.close()


if __name__ == "__main__":
    main()
