#!/usr/bin/env python3
"""Optional clean-stack OIDC, OCR/AI and ISDOC smoke with synthetic PDFs."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from app.db import SessionLocal
from app.models import AIExtraction
from generate_isdoc_smoke_fixtures import base_pdf, isdoc_xml, with_attachment
from smoke_stage_b import login, require, response_json
from sqlalchemy import func, select


def upload(client, base: str, user: dict[str, Any], name: str, content: bytes) -> str:
    response = client.post(
        f"{base}/api/uploads",
        headers={"X-CSRF-Token": user["csrf_token"]},
        data={"idempotency_key": f"bootstrap-full-{uuid.uuid4()}"},
        files={"document": (name, content, "application/pdf")},
    )
    require(response.status_code == 202, f"Upload returned HTTP {response.status_code}")
    return str(response.json()["id"])


def wait_invoice(client, base: str, upload_id: str, *, ai: bool) -> dict[str, Any]:
    deadline = time.monotonic() + int(os.environ.get("BOOTSTRAP_FULL_SMOKE_TIMEOUT", "1800"))
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        tracking = response_json(client.get(f"{base}/api/uploads/{upload_id}"), "upload tracking")
        require(
            tracking["status"] not in {"FAILED", "FAILED_RETRYABLE", "SUBMISSION_UNKNOWN", "ERROR"},
            f"Upload failed: {tracking.get('error_code')} {tracking.get('error_message')}",
        )
        if tracking.get("invoice_id"):
            last = response_json(
                client.get(f"{base}/api/invoices/{tracking['invoice_id']}"),
                "invoice detail",
            )
            if ai and last["ai_status"] in {"AI_COMPLETED", "AI_FAILED"}:
                require(last["ai_status"] == "AI_COMPLETED", "Qwen extraction failed")
                return last
            if not ai and last["isdoc"]["status"] != "UNCHECKED":
                return last
        time.sleep(3)
    raise RuntimeError(f"Timed out waiting for synthetic invoice: {json.dumps(last)[:500]}")


def ai_run_count(invoice_id: str) -> int:
    with SessionLocal() as db:
        return int(
            db.scalar(
                select(func.count()).select_from(AIExtraction).where(
                    AIExtraction.invoice_id == invoice_id
                )
            )
            or 0
        )


def main() -> None:
    base = os.environ["APP_BASE_URL"].rstrip("/")
    manager = login(base, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"])
    approver = login(base, "approver1", os.environ["TEST_APPROVER_1_PASSWORD"])
    try:
        manager_user = response_json(manager.get(f"{base}/api/auth/me"), "manager /me")
        approver_user = response_json(approver.get(f"{base}/api/auth/me"), "approver /me")
        require("QUEUE_MANAGER" in manager_user["roles"], "QUEUE_MANAGER role is missing")
        require("APPROVER" in approver_user["roles"], "APPROVER role is missing")

        plain_fixture = Path("/fixtures/synthetic/synthetic-invoice-cs-en.pdf").read_bytes()
        marker = uuid.uuid4().hex[:10]
        plain_fixture += f"\n% bootstrap-smoke-{marker}\n".encode()
        plain = wait_invoice(
            manager,
            base,
            upload(manager, base, manager_user, f"bootstrap-ai-{marker}.pdf", plain_fixture),
            ai=True,
        )
        require(plain["paperless"]["ocr_text"], "Paperless OCR text is empty")
        require(ai_run_count(plain["id"]) >= 1, "Qwen run is missing")

        isdoc_pdf = with_attachment(
            base_pdf("Bootstrap valid ISDOC smoke"),
            "bootstrap-invoice.isdoc",
            isdoc_xml(),
        )
        valid = wait_invoice(
            manager,
            base,
            upload(manager, base, manager_user, f"bootstrap-isdoc-{marker}.pdf", isdoc_pdf),
            ai=False,
        )
        require(valid["isdoc"]["status"] == "VALID", "Valid ISDOC was not accepted")
        require(valid["isdoc"]["version"] == "6.0.2", "Unexpected ISDOC version")
        require(
            valid["classification"]["extraction_source"] == "ISDOC",
            "Valid ISDOC is not the extraction source",
        )
        require(ai_run_count(valid["id"]) == 0, "Qwen ran for a valid ISDOC invoice")

        print(
            json.dumps(
                {
                    "oidc": {"queue_manager": "OK", "approver1": "OK"},
                    "ocr_ai": {
                        "paperless_document_id": plain["paperless_document_id"],
                        "ocr_length": len(plain["paperless"]["ocr_text"]),
                        "ai_status": plain["ai_status"],
                        "ai_runs": ai_run_count(plain["id"]),
                    },
                    "isdoc": {
                        "paperless_document_id": valid["paperless_document_id"],
                        "status": valid["isdoc"]["status"],
                        "version": valid["isdoc"]["version"],
                        "extraction_source": valid["classification"]["extraction_source"],
                        "ai_runs": ai_run_count(valid["id"]),
                    },
                    "result": "PASS",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        manager.close()
        approver.close()


if __name__ == "__main__":
    main()
