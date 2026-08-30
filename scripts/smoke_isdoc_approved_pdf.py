#!/usr/bin/env python3
"""Real OIDC/API smoke for classification, ISDOC, approved PDF and POHODA routing."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from app.db import SessionLocal
from app.models import AIExtraction, ApprovedPdfArtifact
from app.services.isdoc import enumerate_attachments
from generate_isdoc_smoke_fixtures import base_pdf, isdoc_xml, with_attachment
from smoke_stage_b import login, require, response_json
from sqlalchemy import select

NS = {
    "inv": "http://www.stormware.cz/schema/version_2/invoice.xsd",
}


def api(
    client: httpx.Client,
    method: str,
    url: str,
    user: dict[str, Any],
    payload: dict[str, Any] | None = None,
    expected: int = 200,
) -> httpx.Response:
    response = client.request(
        method,
        url,
        headers={"X-CSRF-Token": user["csrf_token"]},
        json=payload,
    )
    require(
        response.status_code == expected,
        f"{method} {url} returned {response.status_code}: {response.text[:600]}",
    )
    return response


def detail(client: httpx.Client, base: str, invoice_id: str) -> dict[str, Any]:
    return response_json(client.get(f"{base}/api/invoices/{invoice_id}"), "invoice detail")


def wait_detail(
    client: httpx.Client,
    base: str,
    invoice_id: str,
    predicate,
    label: str,
    timeout: int = 1200,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    current: dict[str, Any] = {}
    while time.monotonic() < deadline:
        current = detail(client, base, invoice_id)
        if predicate(current):
            return current
        time.sleep(3)
    raise RuntimeError(f"Timed out waiting for {label}: {json.dumps(current, ensure_ascii=False)[:800]}")


def upload(
    client: httpx.Client,
    base: str,
    user: dict[str, Any],
    filename: str,
    content: bytes,
) -> dict[str, Any]:
    response = client.post(
        f"{base}/api/uploads",
        headers={"X-CSRF-Token": user["csrf_token"]},
        data={"idempotency_key": f"isdoc-smoke-{uuid.uuid4()}"},
        files={"document": (filename, content, "application/pdf")},
    )
    require(response.status_code == 202, f"Upload failed: {response.status_code} {response.text[:500]}")
    upload_id = response.json()["id"]
    deadline = time.monotonic() + 300
    tracking: dict[str, Any] = {}
    while time.monotonic() < deadline:
        tracking = response_json(client.get(f"{base}/api/uploads/{upload_id}"), "upload tracking")
        if tracking.get("invoice_id"):
            return tracking
        require(
            tracking.get("status") not in {"FAILED", "FAILED_RETRYABLE", "SUBMISSION_UNKNOWN", "ERROR"},
            f"Upload failed: {tracking}",
        )
        time.sleep(3)
    raise RuntimeError(f"Upload did not produce invoice: {tracking}")


def classify(
    manager: httpx.Client,
    base: str,
    user: dict[str, Any],
    invoice: dict[str, Any],
    document_type: str,
) -> dict[str, Any]:
    return response_json(
        api(
            manager,
            "PUT",
            f"{base}/api/invoices/{invoice['id']}/classification",
            user,
            {
                "document_type": document_type,
                "processing_mode": "FOR_APPROVAL",
                "expected_revision": invoice["current_revision_number"],
            },
        ),
        "classification",
    )


def ensure_fields(
    manager: httpx.Client,
    base: str,
    user: dict[str, Any],
    invoice: dict[str, Any],
    suffix: str,
) -> dict[str, Any]:
    current = invoice["data"]
    changes = {
        "supplier_name": current.get("supplier_name") or "Smoke Dodavatel s.r.o.",
        "supplier_ico": current.get("supplier_ico") or "28652240",
        "supplier_dic": current.get("supplier_dic") or "CZ28652240",
        "supplier_street": current.get("supplier_street") or "Testovací 1",
        "supplier_city": current.get("supplier_city") or "Praha",
        "supplier_zip": current.get("supplier_zip") or "10000",
        "invoice_number": current.get("invoice_number") or f"SMOKE-{suffix}-{uuid.uuid4().hex[:8]}",
        "variable_symbol": current.get("variable_symbol") or str(int(time.time()))[-10:],
        "issue_date": current.get("issue_date") or "2026-08-20",
        "taxable_supply_date": current.get("taxable_supply_date") or "2026-08-20",
        "due_date": current.get("due_date") or "2026-09-03",
        "currency": "CZK",
        "total_without_vat": current.get("total_without_vat") or "1000.00",
        "total_vat": current.get("total_vat") or "210.00",
        "total_amount": current.get("total_amount") or "1210.00",
        "vat_lines": current.get("vat_lines") or [
            {"vat_rate": "21", "taxable_base": "1000.00", "vat_amount": "210.00"}
        ],
        "description": current.get("description") or "ISDOC/Approval smoke",
    }
    return response_json(
        api(
            manager,
            "PATCH",
            f"{base}/api/invoices/{invoice['id']}",
            user,
            {"changes": changes, "expected_revision": invoice["current_revision_number"]},
        ),
        "invoice fields",
    )


def approve(
    manager: httpx.Client,
    approver: httpx.Client,
    base: str,
    manager_user: dict[str, Any],
    approver_user: dict[str, Any],
    invoice: dict[str, Any],
    centre_id: str,
) -> dict[str, Any]:
    total = str(Decimal(str(invoice["data"]["total_amount"])).quantize(Decimal("0.01")))
    configured = response_json(
        api(
            manager,
            "PUT",
            f"{base}/api/invoices/{invoice['id']}/allocations",
            manager_user,
            {"allocations": [{"cost_center_id": centre_id, "amount": total, "note": "Smoke"}], "expected_revision": invoice["current_revision_number"]},
        ),
        "allocations",
    )
    allocation = configured["allocations"][0]
    api(
        manager,
        "PUT",
        f"{base}/api/invoices/{invoice['id']}/allocations/{allocation['id']}/approvers",
        manager_user,
        {"approver_subjects": [approver_user["subject"]], "expected_revision": configured["current_revision_number"]},
    )
    api(manager, "POST", f"{base}/api/invoices/{invoice['id']}/confirm-original", manager_user)
    api(manager, "POST", f"{base}/api/invoices/{invoice['id']}/submit", manager_user)
    tasks = response_json(approver.get(f"{base}/api/approvals/mine"), "approver tasks")
    task = next((row for row in tasks if row["invoice_id"] == invoice["id"]), None)
    require(task is not None, "Approver task was not created")
    api(
        approver,
        "POST",
        f"{base}/api/approvals/{task['id']}/decision",
        approver_user,
        {"action": "APPROVE", "comment": "ISDOC approved-PDF smoke"},
    )
    return wait_detail(
        manager,
        base,
        invoice["id"],
        lambda row: (row.get("approved_pdf") or {}).get("status") == "STORED",
        "approved PDF stored",
        360,
    )


def ai_runs(invoice_id: str) -> int:
    with SessionLocal() as db:
        return len(list(db.scalars(select(AIExtraction).where(AIExtraction.invoice_id == invoice_id))))


def artifacts(invoice_id: str) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(ApprovedPdfArtifact)
                .where(ApprovedPdfArtifact.invoice_id == invoice_id)
                .order_by(ApprovedPdfArtifact.created_at)
            )
        )
        return [
            {
                "id": row.id,
                "revision_id": row.revision_id,
                "status": row.status.value,
                "paperless_document_id": row.paperless_document_id,
                "original_sha256": row.original_pdf_sha256,
                "approved_sha256": row.approved_pdf_sha256,
            }
            for row in rows
        ]


def main() -> None:
    base = os.environ["APP_BASE_URL"].rstrip("/")
    manager = login(base, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"])
    approver = login(base, "approver1", os.environ["TEST_APPROVER_1_PASSWORD"])
    try:
        manager_user = response_json(manager.get(f"{base}/api/auth/me"), "manager /me")
        approver_user = response_json(approver.get(f"{base}/api/auth/me"), "approver /me")
        centres = response_json(manager.get(f"{base}/api/cost-centers"), "cost centres")
        centre_id = next(row["id"] for row in centres if row["active"])
        valid_xml = isdoc_xml()
        fixtures = {
            "plain": base_pdf("No ISDOC smoke"),
            "valid": with_attachment(base_pdf("Valid ISDOC smoke"), "invoice.isdoc", valid_xml),
            "invalid": with_attachment(
                base_pdf("Invalid ISDOC smoke"),
                "invalid.isdoc",
                b'<Invoice xmlns="http://isdoc.cz/namespace/2013" version="6.0.2"><DocumentID>',
            ),
            "advance": base_pdf("Advance invoice smoke"),
        }
        rows: dict[str, dict[str, Any]] = {}
        for name, content in fixtures.items():
            tracking = upload(manager, base, manager_user, f"codex-{name}-{uuid.uuid4().hex[:6]}.pdf", content)
            rows[name] = {"tracking": tracking, "original_bytes": content}

        for name in ("plain", "valid", "invalid", "advance"):
            invoice_id = rows[name]["tracking"]["invoice_id"]
            inspected = wait_detail(
                manager,
                base,
                invoice_id,
                lambda row: row["isdoc"]["status"] != "UNCHECKED",
                f"{name} ISDOC inspection",
            )
            if name != "valid":
                inspected = wait_detail(
                    manager,
                    base,
                    invoice_id,
                    lambda row: row["ai_status"] in {"AI_COMPLETED", "AI_FAILED"},
                    f"{name} AI completion",
                )
            rows[name]["inspected"] = inspected

        plain = classify(manager, base, manager_user, rows["plain"]["inspected"], "RECEIVED_INVOICE")
        plain = ensure_fields(manager, base, manager_user, plain, "PLAIN")
        plain = approve(manager, approver, base, manager_user, approver_user, plain, centre_id)
        generated = response_json(
            api(manager, "POST", f"{base}/api/exports/invoices/{plain['id']}/generate", manager_user, {"reason": None}),
            "generated POHODA XML",
        )
        xml_response = manager.get(f"{base}/api/exports/artifacts/{generated['id']}/xml")
        require(xml_response.status_code == 200, "Generated XML download failed")
        xml_root = ET.fromstring(xml_response.content)
        require(xml_root.findall(".//inv:centre", NS) == [], "Approval allocation leaked into POHODA centre")
        xml_note = xml_root.findtext(".//inv:invoiceHeader/inv:text", namespaces=NS) or ""
        require("Finální účetní rozúčtování provádí účetní" in xml_note, "Allocation note is missing")

        valid = classify(manager, base, manager_user, rows["valid"]["inspected"], "RECEIVED_INVOICE")
        valid = approve(manager, approver, base, manager_user, approver_user, valid, centre_id)
        require(valid["classification"]["pohoda_import_method"] == "PDF_ISDOC", "PDF_ISDOC routing missing")
        rejected_xml = api(
            manager,
            "POST",
            f"{base}/api/exports/invoices/{valid['id']}/generate",
            manager_user,
            {"reason": None},
            expected=409,
        )
        approved_response = manager.get(f"{base}/api/invoices/{valid['id']}/approved-pdf")
        require(approved_response.status_code == 200, "Approved PDF download failed")
        original_response = manager.get(f"{base}/api/invoices/{valid['id']}/pdf")
        require(original_response.status_code == 200, "Original PDF download failed")
        original_manifest = {row.filename: row.sha256 for row in enumerate_attachments(original_response.content)}
        approved_manifest = {row.filename: row.sha256 for row in enumerate_attachments(approved_response.content)}
        require(original_manifest == approved_manifest, "Approved PDF changed embedded attachments")
        require(ai_runs(valid["id"]) == 0, "AI was called for valid ISDOC")

        invalid = classify(manager, base, manager_user, rows["invalid"]["inspected"], "RECEIVED_INVOICE")
        require(invalid["isdoc"]["status"] == "INVALID", "Invalid ISDOC warning missing")
        require(invalid["classification"]["extraction_source"] == "OCR_AI", "Invalid ISDOC did not fall back")
        require(ai_runs(invalid["id"]) >= 1, "AI fallback was not called for invalid ISDOC")

        advance = classify(manager, base, manager_user, rows["advance"]["inspected"], "RECEIVED_ADVANCE_INVOICE")
        advance = ensure_fields(manager, base, manager_user, advance, "ADVANCE")
        advance = approve(manager, approver, base, manager_user, approver_user, advance, centre_id)
        blocked = api(
            manager,
            "POST",
            f"{base}/api/exports/invoices/{advance['id']}/generate",
            manager_user,
            {"reason": None},
            expected=409,
        )

        first_artifacts = artifacts(valid["id"])
        changed = response_json(
            api(
                manager,
                "PATCH",
                f"{base}/api/invoices/{valid['id']}",
                manager_user,
                {"changes": {"description": "Revision smoke changed"}, "expected_revision": valid["current_revision_number"]},
            ),
            "new revision",
        )
        changed = approve(manager, approver, base, manager_user, approver_user, changed, centre_id)
        second_artifacts = artifacts(valid["id"])
        require(len(second_artifacts) == len(first_artifacts) + 1, "New revision did not create a new approved artifact")
        require(first_artifacts[-1]["id"] != second_artifacts[-1]["id"], "Approved artifact was overwritten")
        require(any(row["status"] == "HISTORICAL" for row in second_artifacts[:-1]), "Old artifact is not historical")

        result = {
            "app_url": base,
            "oidc": {"manager": manager_user["roles"], "approver": approver_user["roles"]},
            "plain": {
                "paperless_document_id": plain["paperless_document_id"],
                "invoice_id": plain["id"],
                "isdoc_status": plain["isdoc"]["status"],
                "ai_runs": ai_runs(plain["id"]),
                "approved_pdf": plain["approved_pdf"],
                "xml_artifact_id": generated["id"],
                "xsd": generated["status"],
                "target_unit": generated["pohoda_target_validation"]["status"],
                "data_pack_ico": xml_root.get("ico"),
                "allocation_centres_removed": True,
                "allocation_note_present": True,
            },
            "valid_isdoc": {
                "paperless_document_id": valid["paperless_document_id"],
                "invoice_id": valid["id"],
                "filename": valid["isdoc"]["filename"],
                "version": valid["isdoc"]["version"],
                "original_isdoc_sha256": original_manifest["invoice.isdoc"],
                "approved_isdoc_sha256": approved_manifest["invoice.isdoc"],
                "hashes_equal": original_manifest == approved_manifest,
                "ai_runs": ai_runs(valid["id"]),
                "method": valid["classification"]["pohoda_import_method"],
                "generated_xml_http": rejected_xml.status_code,
                "original_pdf_sha256": hashlib.sha256(original_response.content).hexdigest(),
                "approved_pdf_sha256": hashlib.sha256(approved_response.content).hexdigest(),
            },
            "invalid_isdoc": {
                "paperless_document_id": invalid["paperless_document_id"],
                "invoice_id": invalid["id"],
                "status": invalid["isdoc"]["status"],
                "source": invalid["classification"]["extraction_source"],
                "ai_runs": ai_runs(invalid["id"]),
            },
            "advance": {
                "paperless_document_id": advance["paperless_document_id"],
                "invoice_id": advance["id"],
                "status": advance["status"],
                "approved_pdf": advance["approved_pdf"],
                "pohoda_method": advance["classification"]["pohoda_import_method"],
                "export_http": blocked.status_code,
            },
            "revision": {
                "invoice_id": changed["id"],
                "revision": changed["current_revision_number"],
                "artifacts": second_artifacts,
            },
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        manager.close()
        approver.close()


if __name__ == "__main__":
    main()
