#!/usr/bin/env python3
"""Real Paperless/Ollama/API smoke for form population and re-extraction safety."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.integrations.paperless import PaperlessClient
from smoke_correction import task_rows
from smoke_stage_b import login, require, response_json

EXPECTED = {
    "supplier_name": "TESTOVACÍ DODAVATEL s.r.o.",
    "supplier_ico": "00000019",
    "supplier_dic": "CZ00000019",
    "supplier_address_raw": "Fiktivní 123, 100 00 Praha",
    "supplier_street": "Fiktivní 123",
    "supplier_city": "Praha",
    "supplier_zip": "100 00",
    "invoice_number": "TEST-2026-0001",
    "variable_symbol": "20260001",
    "issue_date": "2026-08-20",
    "taxable_supply_date": "2026-08-20",
    "due_date": "2026-09-03",
    "currency": "CZK",
    "bank_account_number": "0000000000",
    "bank_code": "0000",
    "total_without_vat": "1000.00",
    "total_vat": "210.00",
    "total_amount": "1210.00",
}


async def upload_fixture(paperless: PaperlessClient, fixture: Path, title: str) -> int:
    inbox_id = await paperless.resolve_tag_id(get_settings().paperless_inbox_tag)
    with fixture.open("rb") as stream:
        response = await paperless._request(
            "POST",
            "/documents/post_document/",
            files={"document": (f"{title}.pdf", stream, "application/pdf")},
            data={"title": title, "tags": str(inbox_id)},
        )
    task_id: Any = response.json()
    if isinstance(task_id, dict):
        task_id = task_id.get("task_id") or task_id.get("id")
    require(bool(task_id), "Paperless upload did not return a task ID")
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        payload = (
            await paperless._request("GET", "/tasks/", params={"task_id": str(task_id)})
        ).json()
        for row in task_rows(payload):
            if row.get("related_document"):
                return int(row["related_document"])
            if str(row.get("status") or "").upper() in {"FAILURE", "FAILED"}:
                raise RuntimeError("Paperless consumption task failed")
        async for document in paperless.iter_documents():
            if document.title == title and document.original_filename == f"{title}.pdf":
                return document.id
        await asyncio.sleep(2)
    raise RuntimeError("Paperless upload was not consumed within 180 seconds")


def wait_invoice(manager, base_url: str, document_id: int) -> dict[str, Any]:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        rows = response_json(
            manager.get(f"{base_url}/api/invoices", params={"view": "all"}),
            "invoice inventory",
        )
        row = next(
            (item for item in rows if item["paperless_document_id"] == document_id),
            None,
        )
        if row:
            return row
        time.sleep(2)
    raise RuntimeError("Approval did not discover the new Paperless document")


def detail(manager, base_url: str, invoice_id: str) -> dict[str, Any]:
    return response_json(
        manager.get(f"{base_url}/api/invoices/{invoice_id}"), "invoice detail"
    )


def wait_ai(
    manager,
    base_url: str,
    invoice_id: str,
    *,
    after_revision: int = 0,
) -> dict[str, Any]:
    timeout = int(os.environ.get("FORM_SMOKE_AI_TIMEOUT_SECONDS", "1200"))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = detail(manager, base_url, invoice_id)
        latest = current["ai"]["latest"]
        if latest and latest["extraction_revision"] > after_revision:
            if latest["status"] == "AI_COMPLETED":
                return current
            if latest["status"] == "AI_FAILED":
                raise RuntimeError(f"AI extraction failed: {latest['error_code']}")
        time.sleep(3)
    raise RuntimeError(f"AI extraction did not finish within {timeout} seconds")


def csrf(user: dict[str, Any]) -> dict[str, str]:
    return {"X-CSRF-Token": user["csrf_token"]}


def required_ids() -> tuple[int, str]:
    return int(os.environ["FORM_SMOKE_DOCUMENT_ID"]), os.environ["FORM_SMOKE_INVOICE_ID"]


async def create_phase() -> dict[str, Any]:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    fixture = Path(os.environ.get("FORM_SMOKE_PDF", "/fixtures/synthetic-invoice-cs-en.pdf"))
    require(fixture.is_file(), "Synthetic PDF fixture is unavailable")
    title = f"codex-form-population-{uuid.uuid4().hex[:12]}"
    paperless = PaperlessClient(get_settings())
    manager = login(base_url, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"])
    try:
        document_id = await upload_fixture(paperless, fixture, title)
        row = wait_invoice(manager, base_url, document_id)
        current = detail(manager, base_url, row["id"])
        return {
            "phase": "create",
            "title": title,
            "paperless_document_id": document_id,
            "invoice_id": row["id"],
            "detail_url": f"{base_url}/invoices/{row['id']}",
            "ai_status_when_openable": current["ai_status"],
            "current_revision": current["current_revision_number"],
            "current_values_when_openable": {
                key: current["data"].get(key) for key in EXPECTED
            },
        }
    finally:
        await paperless.close()
        manager.close()


def first_phase() -> dict[str, Any]:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    document_id, invoice_id = required_ids()
    manager = login(base_url, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"])
    try:
        current = wait_ai(manager, base_url, invoice_id)
        latest = current["ai"]["latest"]
        require(latest["extraction_revision"] == 1, "First extraction is not revision 1")
        require(latest["applied"], "First extraction was not auto-applied")
        require(latest["applied_by"] == "system", "First extraction actor is not system")
        require(current["current_revision_number"] == 1, "Auto-apply unexpectedly forked revision")
        for field, expected in EXPECTED.items():
            require(str(current["data"].get(field)) == expected, f"Current {field} is wrong")
        evidence = {row["field_name"]: row for row in current["extracted_fields"]}
        for field in EXPECTED:
            require(field in evidence, f"Evidence for {field} is missing")
        candidate = latest.get("candidate_data") or {}
        for field, expected in EXPECTED.items():
            require(str(candidate.get(field)) == expected, f"Normalized candidate {field} is wrong")
        audit = response_json(
            manager.get(f"{base_url}/api/invoices/{invoice_id}/audit"), "invoice audit"
        )
        applied = [row for row in audit if row["event_type"] == "AI_EXTRACTION_APPLIED"]
        require(applied, "Automatic apply audit is missing")
        return {
            "phase": "first",
            "paperless_document_id": document_id,
            "invoice_id": invoice_id,
            "ocr_length": len(current["paperless"]["ocr_text"]),
            "ai_status": current["ai_status"],
            "extraction_revision": latest["extraction_revision"],
            "first_extraction_auto_applied": latest["applied"],
            "current_revision": current["current_revision_number"],
            "candidate_data": {key: candidate.get(key) for key in EXPECTED},
            "api_current_data": {key: current["data"].get(key) for key in EXPECTED},
            "evidence_sources_present": {key: bool(evidence[key].get("source_text")) for key in EXPECTED},
            "automatic_apply_audit": applied[-1]["event_type"],
        }
    finally:
        manager.close()


def reextract_phase() -> dict[str, Any]:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    document_id, invoice_id = required_ids()
    manager = login(base_url, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"])
    try:
        user = response_json(manager.get(f"{base_url}/api/auth/me"), "manager identity")
        headers = csrf(user)
        before = detail(manager, base_url, invoice_id)
        first_revision = before["current_revision_number"]
        before_data = dict(before["data"])
        previous_extraction = before["ai"]["latest"]["extraction_revision"]
        queued = manager.post(
            f"{base_url}/api/invoices/{invoice_id}/ai-extractions", headers=headers
        )
        require(queued.status_code == 202, "Re-extraction was not queued")
        candidate_detail = wait_ai(
            manager,
            base_url,
            invoice_id,
            after_revision=previous_extraction,
        )
        candidate = candidate_detail["ai"]["latest"]
        require(candidate["requires_confirmation"], "Re-extraction is not a candidate")
        require(not candidate["applied"], "Re-extraction overwrote current data")
        require(candidate_detail["data"] == before_data, "Candidate changed current data")
        applied_response = manager.post(
            f"{base_url}/api/invoices/{invoice_id}/ai-extractions/{candidate['id']}/apply",
            headers=headers,
            json={"confirm_overwrite": True},
        )
        require(applied_response.status_code == 200, "Candidate apply failed")
        applied = applied_response.json()
        require(candidate["candidate_data"] == applied["data"], "Applied data differ from normalized candidate")
        require(applied["current_revision_number"] > first_revision, "Candidate apply did not fork revision")

        manual_value = f"MANUAL FORM OVERRIDE {uuid.uuid4().hex[:8]}"
        patched = manager.patch(
            f"{base_url}/api/invoices/{invoice_id}",
            headers=headers,
            json={
                "changes": {"description": manual_value},
                "comment": "Form population smoke manual override",
                "expected_revision": applied["current_revision_number"],
            },
        )
        require(patched.status_code == 200, "Manual override patch failed")
        manual = patched.json()
        require(manual["data"]["description"] == manual_value, "Manual override was not stored")
        second_extraction = manual["ai"]["latest"]["extraction_revision"]
        queued_again = manager.post(
            f"{base_url}/api/invoices/{invoice_id}/ai-extractions", headers=headers
        )
        require(queued_again.status_code == 202, "Second re-extraction was not queued")
        protected = wait_ai(
            manager,
            base_url,
            invoice_id,
            after_revision=second_extraction,
        )
        latest = protected["ai"]["latest"]
        require(not latest["applied"], "Later re-extraction auto-applied")
        require(latest["requires_confirmation"], "Later re-extraction needs no confirmation")
        require(protected["data"]["description"] == manual_value, "Manual override was overwritten")
        audit = response_json(
            manager.get(f"{base_url}/api/invoices/{invoice_id}/audit"), "invoice audit"
        )
        audit_types = [row["event_type"] for row in audit]
        require("AI_REEXTRACTION_APPLIED" in audit_types, "Re-extraction apply audit missing")
        require("INVOICE_FIELD_CHANGED" in audit_types, "Manual override audit missing")
        return {
            "phase": "reextract",
            "paperless_document_id": document_id,
            "invoice_id": invoice_id,
            "candidate_created": True,
            "candidate_applied": True,
            "revision_before_apply": first_revision,
            "revision_after_apply": applied["current_revision_number"],
            "manual_override": manual_value,
            "protected_extraction_revision": latest["extraction_revision"],
            "protected_candidate_applied": latest["applied"],
            "manual_override_preserved": protected["data"]["description"] == manual_value,
            "audit": {
                "AI_REEXTRACTION_APPLIED": "AI_REEXTRACTION_APPLIED" in audit_types,
                "INVOICE_FIELD_CHANGED": "INVOICE_FIELD_CHANGED" in audit_types,
            },
        }
    finally:
        manager.close()


async def main() -> None:
    phase = os.environ.get("FORM_SMOKE_PHASE", "create")
    if phase == "create":
        report = await create_phase()
    elif phase == "first":
        report = first_phase()
    elif phase == "reextract":
        report = reextract_phase()
    else:
        raise RuntimeError(f"Unknown FORM_SMOKE_PHASE: {phase}")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
