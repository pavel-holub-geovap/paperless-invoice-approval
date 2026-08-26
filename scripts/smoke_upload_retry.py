#!/usr/bin/env python3
"""Exercise a real Paperless-unavailable upload and its idempotent retry."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from smoke_stage_b import login, require, response_json


def csrf(user: dict[str, Any]) -> dict[str, str]:
    return {"X-CSRF-Token": user["csrf_token"]}


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    mode = os.environ["UPLOAD_RETRY_SMOKE_MODE"]
    key = os.environ["UPLOAD_RETRY_SMOKE_KEY"]
    fixture = Path(
        os.environ.get("APPROVAL_UPLOAD_SMOKE_PDF", "/fixtures/synthetic-invoice-cs-en.pdf")
    ).read_bytes()
    content = fixture + f"\n% Controlled retry smoke {key}\n".encode()
    filename = f"codex-controlled-retry-{key[-8:]}.pdf"
    client = login(base_url, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"])
    try:
        user = response_json(client.get(f"{base_url}/api/auth/me"), "manager /me")
        response = client.post(
            f"{base_url}/api/uploads",
            headers=csrf(user),
            data={"idempotency_key": key},
            files={"document": (filename, content, "application/pdf")},
        )
        require(response.status_code == 202, f"Upload returned HTTP {response.status_code}")
        row = response.json()
        if mode == "failure":
            require(row["status"] == "FAILED_RETRYABLE", "Failure is not retryable")
            require(row["error_code"] == "PAPERLESS_UNAVAILABLE", "Wrong failure code")
            require(row["retryable"] is True, "Retry flag is false")
        elif mode == "retry":
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline and not row.get("invoice_id"):
                time.sleep(3)
                row = response_json(
                    client.get(f"{base_url}/api/uploads/{row['id']}"),
                    "retry tracking",
                )
                require(
                    row["status"] not in {"FAILED", "FAILED_RETRYABLE", "SUBMISSION_UNKNOWN", "ERROR"},
                    f"Retry failed: {row.get('error_code')} {row.get('error_message')}",
                )
            require(row.get("paperless_document_id"), "Retry has no Paperless document ID")
            require(row.get("invoice_id"), "Retry has no Approval invoice ID")
            require(row["retry_count"] == 1, "Retry did not reuse the original tracking row")
        else:
            raise RuntimeError(f"Unsupported smoke mode: {mode}")
        print(json.dumps({
            "mode": mode,
            "filename": filename,
            "upload_id": row["id"],
            "status": row["status"],
            "error_code": row.get("error_code"),
            "retryable": row["retryable"],
            "retry_count": row["retry_count"],
            "paperless_document_id": row.get("paperless_document_id"),
            "invoice_id": row.get("invoice_id"),
        }, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
