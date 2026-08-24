#!/usr/bin/env python3
"""Apply verified GIRITON candidate, approve one allocation, and create immutable XML."""

from __future__ import annotations

import hashlib
import json
import os
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from smoke_stage_b import login, require, response_json

NS = {
    "dat": "http://www.stormware.cz/schema/version_2/data.xsd",
    "inv": "http://www.stormware.cz/schema/version_2/invoice.xsd",
    "typ": "http://www.stormware.cz/schema/version_2/type.xsd",
}


def api(client: httpx.Client, method: str, url: str, user: dict[str, Any], payload=None, expected=200):
    response = client.request(
        method, url, headers={"X-CSRF-Token": user["csrf_token"]}, json=payload
    )
    require(
        response.status_code == expected,
        f"{method} {url} returned HTTP {response.status_code}: {response.text[:500]}",
    )
    return response


def detail(client: httpx.Client, base_url: str, invoice_id: str) -> dict[str, Any]:
    return response_json(client.get(f"{base_url}/api/invoices/{invoice_id}"), "invoice detail")


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    document_id = int(os.environ.get("GIRITON_PAPERLESS_DOCUMENT_ID", "11"))
    manager = login(base_url, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"])
    approver = login(base_url, "approver1", os.environ["TEST_APPROVER_1_PASSWORD"])
    try:
        manager_user = response_json(manager.get(f"{base_url}/api/auth/me"), "manager /me")
        approver_user = response_json(approver.get(f"{base_url}/api/auth/me"), "approver /me")
        rows = response_json(manager.get(f"{base_url}/api/invoices?view=all"), "invoice list")
        listed = next((row for row in rows if row["paperless_document_id"] == document_id), None)
        require(listed is not None, f"Paperless document {document_id} is absent")
        invoice_id = listed["id"]
        current = detail(manager, base_url, invoice_id)
        latest = current["ai"]["latest"]
        require(latest and latest["status"] == "AI_COMPLETED", "Latest extraction is not complete")
        require(latest["schema_version"] == "invoice-extraction.v3", "Latest extraction is not v3")

        if not latest["applied"]:
            current = response_json(
                api(
                    manager,
                    "POST",
                    f"{base_url}/api/invoices/{invoice_id}/ai-extractions/{latest['id']}/apply",
                    manager_user,
                    {"confirm_overwrite": True},
                ),
                "apply extraction",
            )

        expected = {
            "supplier_address_raw": "Hornosušská 1399/4 735 64 Havířov - Prostřední Suchá",
            "supplier_street": "Hornosušská 1399/4",
            "supplier_city": "Havířov - Prostřední Suchá",
            "supplier_zip": "735 64",
            "bank_account_number": "2300122535",
            "bank_code": "2010",
            "total_without_vat": "4065.29",
            "total_vat": "853.71",
            "total_amount": "4919.00",
        }
        for key, value in expected.items():
            require(str(current["data"].get(key)) == value, f"Applied {key} is wrong")
        vat_lines = current["data"]["vat_lines"]
        require(vat_lines[0]["vat_amount"] == "853.65", "Main VAT is wrong")
        require(vat_lines[1]["vat_amount"] == "0.06", "Rounding VAT is wrong")
        require(vat_lines[1]["adjustment_type"] == "ROUNDING", "Rounding is not explicit")
        vat_validations = [row for row in current["validations"] if row["code"].startswith("VAT_") or row["code"] == "TOTAL_MATH_OK"]
        require(
            not [row for row in vat_validations if row["severity"] == "BLOCKING_ERROR"],
            "VAT reconciliation is blocking",
        )

        centres = response_json(manager.get(f"{base_url}/api/cost-centers"), "cost centres")
        centre = next((row for row in centres if row["code"] == "200"), None)
        require(centre is not None and centre["pohoda_code"], "Cost centre 200 is missing")
        current = response_json(
            api(
                manager,
                "PUT",
                f"{base_url}/api/invoices/{invoice_id}/allocations",
                manager_user,
                {"allocations": [{"cost_center_id": centre["id"], "amount": "4919.00", "note": "GIRITON VAT/address smoke"}]},
            ),
            "set allocation",
        )
        allocation = current["allocations"][0]
        api(
            manager,
            "PUT",
            f"{base_url}/api/invoices/{invoice_id}/allocations/{allocation['id']}/approvers",
            manager_user,
            {"approver_subjects": [approver_user["subject"]]},
        )
        current = detail(manager, base_url, invoice_id)
        if not current["original_review_confirmed"]:
            api(manager, "POST", f"{base_url}/api/invoices/{invoice_id}/confirm-original", manager_user)
        submitted = response_json(
            api(manager, "POST", f"{base_url}/api/invoices/{invoice_id}/submit", manager_user),
            "submit",
        )
        require(submitted["status"] == "AWAITING_APPROVAL", "Invoice was not submitted")
        tasks = response_json(approver.get(f"{base_url}/api/approvals/mine"), "approver tasks")
        task = next((row for row in tasks if row["invoice_id"] == invoice_id), None)
        require(task is not None, "Approver task is missing")
        approved = response_json(
            api(
                approver,
                "POST",
                f"{base_url}/api/approvals/{task['id']}/decision",
                approver_user,
                {"action": "APPROVE", "comment": "Ověřena adresa, DPH a zaokrouhlení"},
            ),
            "approve",
        )
        require(approved and detail(manager, base_url, invoice_id)["status"] == "APPROVED", "Invoice is not approved")
        artifact = response_json(
            api(
                manager,
                "POST",
                f"{base_url}/api/exports/invoices/{invoice_id}/generate",
                manager_user,
                {},
                expected=201,
            ),
            "generate export",
        )
        require(artifact["status"] == "XSD_VALID", "Generated XML is not XSD valid")
        xml_response = manager.get(f"{base_url}/api/exports/artifacts/{artifact['id']}/xml")
        require(xml_response.status_code == 200, "XML cannot be downloaded")
        require(hashlib.sha256(xml_response.content).hexdigest() == artifact["xml_sha256"], "XML hash mismatch")
        root = ET.fromstring(xml_response.content)
        address = root.find(".//inv:partnerIdentity/typ:address", NS)
        require(address is not None, "POHODA address is missing")
        xml_address = {key: address.findtext(f"typ:{key}", namespaces=NS) for key in ("street", "city", "zip")}
        require(xml_address == {"street": expected["supplier_street"], "city": expected["supplier_city"], "zip": expected["supplier_zip"]}, "POHODA address is wrong")
        payment = root.find(".//inv:paymentAccount", NS)
        require(payment is not None, "POHODA bank account is missing")
        bank = {
            "accountNo": payment.findtext("typ:accountNo", namespaces=NS),
            "bankCode": payment.findtext("typ:bankCode", namespaces=NS),
        }
        base = sum((Decimal(node.text or "0") for node in root.findall(".//inv:invoiceItem/inv:homeCurrency/typ:price", NS)), Decimal(0))
        vat = sum((Decimal(node.text or "0") for node in root.findall(".//inv:invoiceItem/inv:homeCurrency/typ:priceVAT", NS)), Decimal(0))
        print(json.dumps({
            "paperless_document_id": document_id,
            "invoice_id": invoice_id,
            "revision": detail(manager, base_url, invoice_id)["current_revision_number"],
            "status": detail(manager, base_url, invoice_id)["status"],
            "applied_extraction_revision": latest["extraction_revision"],
            "data": expected,
            "vat_lines": vat_lines,
            "vat_validations": vat_validations,
            "pohoda": {
                "artifact_id": artifact["id"], "xml_sha256": artifact["xml_sha256"],
                "xsd_status": artifact["status"], "address": xml_address, "bank": bank,
                "base": f"{base:.2f}", "vat": f"{vat:.2f}", "total": f"{base + vat:.2f}",
                "target_ico": root.get("ico"), "target_key": root.get("key"),
            },
        }, ensure_ascii=False, indent=2))
    finally:
        manager.close()
        approver.close()


if __name__ == "__main__":
    main()
