#!/usr/bin/env python3
"""Destructive-only-to-self smoke for the post-Stage-F correction iteration.

The script creates uniquely named Paperless documents, records their exact IDs, and
deletes only those IDs in cleanup. It never prints credentials, tokens, PDF bytes, or
OCR text.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.integrations.paperless import PaperlessClient, PaperlessNotFound
from smoke_stage_b import login, require, response_json


def task_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("results", payload)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
        if isinstance(rows, dict):
            return [rows]
    return []


async def upload_fixture(
    paperless: PaperlessClient,
    fixture: Path,
    title: str,
) -> int:
    inbox_id = await paperless.resolve_tag_id(get_settings().paperless_inbox_tag)
    with fixture.open("rb") as stream:
        response = await paperless._request(
            "POST",
            "/documents/post_document/",
            files={"document": (f"{title}.pdf", stream, "application/pdf")},
            data={"title": title, "tags": str(inbox_id)},
        )
    task_id = response.json()
    if isinstance(task_id, dict):
        task_id = task_id.get("task_id") or task_id.get("id")
    require(bool(task_id), "Paperless upload did not return a task ID")
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        payload = (
            await paperless._request("GET", "/tasks/", params={"task_id": str(task_id)})
        ).json()
        for row in task_rows(payload):
            related = row.get("related_document")
            if related:
                return int(related)
            if str(row.get("status") or "").upper() in {"FAILURE", "FAILED"}:
                raise RuntimeError("Paperless consumption task failed")
        await asyncio.sleep(2)
    raise RuntimeError("Paperless upload was not consumed within 180 seconds")


def wait_approval_invoice(manager, base_url: str, document_id: int) -> dict[str, Any]:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        rows = response_json(
            manager.get(f"{base_url}/api/invoices", params={"view": "all"}),
            "all invoice rows",
        )
        row = next((item for item in rows if item["paperless_document_id"] == document_id), None)
        if row:
            return row
        time.sleep(2)
    raise RuntimeError(f"Approval did not discover Paperless document {document_id}")


def wait_ai(manager, base_url: str, invoice_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 720
    while time.monotonic() < deadline:
        detail = response_json(
            manager.get(f"{base_url}/api/invoices/{invoice_id}"), "AI detail poll"
        )
        if detail["ai_status"] == "AI_COMPLETED":
            return detail
        if detail["ai_status"] == "AI_FAILED":
            raise RuntimeError(
                f"AI extraction failed for own smoke document: "
                f"{detail['ai']['latest']['error_code']}"
            )
        time.sleep(3)
    raise RuntimeError("AI extraction did not finish within 720 seconds")


def wait_missing(manager, base_url: str, invoice_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        detail = response_json(
            manager.get(f"{base_url}/api/invoices/{invoice_id}"), "missing source poll"
        )
        if detail["source"]["status"] == "MISSING":
            return detail
        time.sleep(2)
    raise RuntimeError("Deleted own Paperless document was not reconciled as MISSING")


async def main() -> None:
    settings = get_settings()
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    fixture = Path(
        os.environ.get(
            "CORRECTION_SMOKE_PDF", "/fixtures/synthetic-invoice-cs-en.pdf"
        )
    )
    require(fixture.is_file(), "Correction smoke PDF fixture is unavailable")
    prefix = f"codex-correction-{uuid.uuid4().hex[:12]}"
    created_document_ids: list[int] = []
    paperless = PaperlessClient(settings)
    manager = login(base_url, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"])
    report: dict[str, Any] = {"unique_prefix": prefix}
    try:
        user = response_json(manager.get(f"{base_url}/api/auth/me"), "manager identity")
        headers = {"X-CSRF-Token": user["csrf_token"]}
        for suffix in ("source", "duplicate"):
            document_id = await upload_fixture(paperless, fixture, f"{prefix}-{suffix}")
            created_document_ids.append(document_id)
        first_row = wait_approval_invoice(manager, base_url, created_document_ids[0])
        second_row = wait_approval_invoice(manager, base_url, created_document_ids[1])
        first = wait_ai(manager, base_url, first_row["id"])
        second = wait_ai(manager, base_url, second_row["id"])

        # Exercise the exact observed LLM failure shape through the real API.
        combined = "19-2000145399/0800"
        normalized_response = manager.patch(
            f"{base_url}/api/invoices/{first_row['id']}",
            headers=headers,
            json={
                "changes": {"bank_account": combined, "bank_code": combined},
                "comment": "Correction smoke: combined account copied into both LLM fields",
            },
        )
        require(normalized_response.status_code == 200, "Bank normalization API patch failed")
        normalized = normalized_response.json()
        account = normalized["data"]
        require(account["bank_account_raw"] == combined, "Raw account evidence was lost")
        require(account["bank_account_prefix"] == "19", "Account prefix is wrong")
        require(account["bank_account_number"] == "2000145399", "Account number is wrong")
        require(account["bank_code"] == "0800", "Bank code is wrong")

        disposition = manager.post(
            f"{base_url}/api/invoices/{second_row['id']}/disposition",
            headers=headers,
            json={
                "disposition": "IGNORED_DUPLICATE",
                "reason": "correction smoke duplicate",
                "comment": "Own synthetic duplicate; safe to remove from active queue",
                "duplicate_of_invoice_id": first_row["id"],
            },
        )
        require(disposition.status_code == 200, "Duplicate disposition failed")
        ignored = disposition.json()
        ignored_disposition = ignored["disposition"]["status"]
        active_rows = response_json(
            manager.get(f"{base_url}/api/invoices", params={"view": "active"}),
            "active queue",
        )
        ignored_rows = response_json(
            manager.get(f"{base_url}/api/invoices", params={"view": "ignored"}),
            "ignored queue",
        )
        require(
            all(row["id"] != second_row["id"] for row in active_rows),
            "Ignored duplicate remained in active queue",
        )
        require(
            any(row["id"] == second_row["id"] for row in ignored_rows),
            "Ignored duplicate is absent from ignored view",
        )

        tag_deadline = time.monotonic() + 120
        duplicate_tag_present = False
        while time.monotonic() < tag_deadline:
            duplicate_document = await paperless.get_document(created_document_ids[1])
            if settings.paperless_tag_duplicate in duplicate_document.tag_names:
                duplicate_tag_present = True
                break
            await asyncio.sleep(2)
        require(duplicate_tag_present, "Paperless duplicate tag was not synchronized")

        # A disposition must not remove the source document, and restoring it must
        # be explicit and auditable. Re-apply the disposition afterwards so the
        # remaining guard checks exercise an ignored invoice.
        preserved_document = await paperless.get_document(created_document_ids[1])
        require(
            preserved_document.id == created_document_ids[1],
            "Disposition unexpectedly removed the Paperless document",
        )
        restore = manager.post(
            f"{base_url}/api/invoices/{second_row['id']}/restore",
            headers=headers,
            json={"comment": "Correction smoke: verify explicit restore"},
        )
        require(restore.status_code == 200, "Duplicate restore failed")
        restored = restore.json()
        require(
            restored["disposition"]["status"] == "ACTIVE",
            "Restore did not reactivate invoice",
        )
        audit = response_json(
            manager.get(f"{base_url}/api/invoices/{second_row['id']}/audit"),
            "duplicate restore audit",
        )
        require(
            any(row["event_type"] == "INVOICE_RESTORED" for row in audit),
            "Restore audit event is absent",
        )
        redisposition = manager.post(
            f"{base_url}/api/invoices/{second_row['id']}/disposition",
            headers=headers,
            json={
                "disposition": "IGNORED_DUPLICATE",
                "reason": "correction smoke duplicate after restore",
                "comment": "Correction smoke: re-ignore for workflow guard checks",
                "duplicate_of_invoice_id": first_row["id"],
            },
        )
        require(redisposition.status_code == 200, "Duplicate re-disposition failed")
        ignored_submit_status = manager.post(
            f"{base_url}/api/invoices/{second_row['id']}/submit",
            headers=headers,
        ).status_code
        ignored_export_status = manager.post(
            f"{base_url}/api/exports/invoices/{second_row['id']}/generate",
            headers=headers,
            json={"reason": "must be blocked for ignored duplicate"},
        ).status_code
        require(ignored_submit_status == 409, "Ignored invoice submission was not blocked")
        require(ignored_export_status == 409, "Ignored invoice export was not blocked")

        await paperless._request("DELETE", f"/documents/{created_document_ids[0]}/")
        missing = wait_missing(manager, base_url, first_row["id"])
        missing_codes = {row["code"] for row in missing["validations"]}
        require("SOURCE_DOCUMENT_MISSING" in missing_codes, "Missing source validation absent")
        pdf_status = manager.get(f"{base_url}/api/invoices/{first_row['id']}/pdf").status_code
        export_status = manager.post(
            f"{base_url}/api/exports/invoices/{first_row['id']}/generate",
            headers=headers,
            json={"reason": "must be blocked for missing source"},
        ).status_code
        require(pdf_status == 409, "Missing source PDF was not blocked")
        require(export_status == 409, "Missing source export was not blocked")

        report.update(
            {
                "created_document_ids": created_document_ids,
                "approval_invoice_ids": [first_row["id"], second_row["id"]],
                "ocr_lengths": [
                    len(first["paperless"]["ocr_text"]),
                    len(second["paperless"]["ocr_text"]),
                ],
                "ai_statuses": [first["ai_status"], second["ai_status"]],
                "normalized_account": {
                    key: account.get(key)
                    for key in (
                        "bank_account_raw",
                        "bank_account_prefix",
                        "bank_account_number",
                        "bank_account",
                        "bank_code",
                        "iban",
                        "swift_bic",
                    )
                },
                "duplicate_disposition": ignored_disposition,
                "duplicate_tag_present": duplicate_tag_present,
                "paperless_preserved_after_disposition": True,
                "duplicate_restore_disposition": restored["disposition"]["status"],
                "duplicate_restore_audited": True,
                "ignored_submit_http": ignored_submit_status,
                "ignored_export_http": ignored_export_status,
                "deleted_source_status": missing["source"],
                "missing_validation": "SOURCE_DOCUMENT_MISSING" in missing_codes,
                "missing_pdf_http": pdf_status,
                "missing_export_http": export_status,
            }
        )
    finally:
        # Delete only documents whose IDs were returned by this run's upload tasks.
        for document_id in created_document_ids:
            try:
                await paperless._request("DELETE", f"/documents/{document_id}/")
            except PaperlessNotFound:
                pass
        await paperless.close()
        manager.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
