#!/usr/bin/env python3
"""Exercise the deployed Stage F POHODA export on the real synthetic invoice."""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from smoke_stage_b import login, require, response_json

NS = {
    "dat": "http://www.stormware.cz/schema/version_2/data.xsd",
    "inv": "http://www.stormware.cz/schema/version_2/invoice.xsd",
    "typ": "http://www.stormware.cz/schema/version_2/type.xsd",
}


def api(
    client: httpx.Client,
    method: str,
    url: str,
    user: dict[str, Any],
    *,
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
        f"{method} {url} returned HTTP {response.status_code}: {response.text[:500]}",
    )
    return response


def detail(client: httpx.Client, base_url: str, invoice_id: str) -> dict[str, Any]:
    return response_json(
        client.get(f"{base_url}/api/invoices/{invoice_id}"), "invoice detail"
    )


def approve_current(
    clients: dict[str, httpx.Client],
    users: dict[str, dict[str, Any]],
    base_url: str,
    invoice_id: str,
) -> None:
    for name in ("approver1", "approver2", "approver3"):
        rows = response_json(
            clients[name].get(f"{base_url}/api/approvals/mine"), f"{name} tasks"
        )
        task = next((row for row in rows if row["invoice_id"] == invoice_id), None)
        require(task is not None, f"Current assignment is missing for {name}")
        api(
            clients[name],
            "POST",
            f"{base_url}/api/approvals/{task['id']}/decision",
            users[name],
            payload={"action": "APPROVE", "comment": "Stage F export smoke test"},
        )


def xml_semantics(xml: bytes, expected: dict[str, Any]) -> dict[str, Any]:
    require(xml.startswith(b"<?xml"), "XML declaration is missing")
    require(b"Windows-1250" in xml.splitlines()[0], "XML does not declare Windows-1250")
    root = ET.fromstring(xml)
    invoice_type = root.findtext(".//inv:invoiceType", namespaces=NS)
    require(invoice_type == "receivedInvoice", "XML is not a receivedInvoice")
    require(
        root.find(".//inv:number", NS) is None,
        "Supplier invoice generated a POHODA internal number",
    )
    address = root.find(".//inv:partnerIdentity/typ:address", NS)
    require(
        address is not None and address.get("linkToAddress") == "false",
        "Free address is not explicit",
    )
    address_values = {
        key: address.findtext(f"typ:{key}", namespaces=NS)
        for key in ("company", "street", "city", "zip", "ico", "dic")
    }
    require(
        address_values["street"] == expected["supplier_street"],
        "Supplier street changed",
    )
    require(
        address_values["city"] == expected["supplier_city"], "Supplier city changed"
    )
    require(address_values["zip"] == expected["supplier_zip"], "Supplier ZIP changed")
    items: list[dict[str, str]] = []
    for item in root.findall(".//inv:invoiceItem", NS):
        base = Decimal(
            item.findtext("inv:homeCurrency/typ:price", namespaces=NS) or "0"
        )
        vat = Decimal(
            item.findtext("inv:homeCurrency/typ:priceVAT", namespaces=NS) or "0"
        )
        items.append(
            {
                "centre": item.findtext("inv:centre/typ:ids", namespaces=NS) or "",
                "base": f"{base:.2f}",
                "vat": f"{vat:.2f}",
                "gross": f"{base + vat:.2f}",
            }
        )
    require(
        {row["centre"] for row in items} == {"200", "300"},
        "Allocation centres are wrong",
    )
    require(
        sum((Decimal(row["gross"]) for row in items), Decimal(0)) == Decimal("1210.00"),
        "Item total is wrong",
    )
    require(xml.decode("windows-1250"), "XML cannot be decoded as Windows-1250")
    return {"invoice_type": invoice_type, "address": address_values, "items": items}


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    document_id = int(os.environ.get("SMOKE_PAPERLESS_DOCUMENT_ID", "1"))
    clients = {
        "manager": login(
            base_url, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"]
        ),
        "approver1": login(
            base_url, "approver1", os.environ["TEST_APPROVER_1_PASSWORD"]
        ),
        "approver2": login(
            base_url, "approver2", os.environ["TEST_APPROVER_2_PASSWORD"]
        ),
        "approver3": login(
            base_url, "approver3", os.environ["TEST_APPROVER_3_PASSWORD"]
        ),
    }
    try:
        users = {
            name: response_json(client.get(f"{base_url}/api/auth/me"), f"{name} /me")
            for name, client in clients.items()
        }
        manager = clients["manager"]
        manager_user = users["manager"]
        require("QUEUE_MANAGER" in manager_user["roles"], "QUEUE_MANAGER role missing")
        invoices = response_json(
            manager.get(f"{base_url}/api/invoices"), "invoice list"
        )
        row = next(
            (item for item in invoices if item["paperless_document_id"] == document_id),
            None,
        )
        require(row is not None, f"Paperless document {document_id} not found")
        invoice_id = row["id"]
        current = detail(manager, base_url, invoice_id)

        structured_address = {
            "supplier_street": "Fiktivní 123",
            "supplier_city": "Praha",
            "supplier_zip": "100 00",
            "invoice_number": "TEST-2026-0001",
        }
        changes = {
            key: value
            for key, value in structured_address.items()
            if current["data"].get(key) != value
        }
        if changes:
            current = response_json(
                api(
                    manager,
                    "PATCH",
                    f"{base_url}/api/invoices/{invoice_id}",
                    manager_user,
                    payload={
                        "changes": changes,
                        "comment": "Přesná adresa a číslo ověřeny v originálním PDF",
                    },
                ),
                "structured supplier address",
            )

        if current["status"] in {"NEEDS_REVIEW", "RETURNED", "QUEUE_REVIEW"}:
            if not current["original_review_confirmed"]:
                current = response_json(
                    api(
                        manager,
                        "POST",
                        f"{base_url}/api/invoices/{invoice_id}/confirm-original",
                        manager_user,
                    ),
                    "confirm original",
                )
            current = response_json(
                api(
                    manager,
                    "POST",
                    f"{base_url}/api/invoices/{invoice_id}/submit",
                    manager_user,
                ),
                "submit",
            )
        if current["status"] == "AWAITING_APPROVAL":
            approve_current(clients, users, base_url, invoice_id)
            current = detail(manager, base_url, invoice_id)
        require(
            current["status"] in {"APPROVED", "READY_FOR_EXPORT", "EXPORT_CREATED"},
            "Invoice is not exportable",
        )
        require(current["current_revision_number"] >= 1, "Current revision is missing")
        require(len(current["allocations"]) == 2, "Expected two current allocations")
        require(
            {row["cost_center"]["pohoda_code"] for row in current["allocations"]}
            == {"200", "300"},
            "POHODA cost centre codes are missing",
        )

        first = api(
            manager,
            "POST",
            f"{base_url}/api/exports/invoices/{invoice_id}/generate",
            manager_user,
            payload={},
            expected=201,
        ).json()
        require(
            first["status"] == "XSD_VALID",
            f"First XML is invalid: {first['validation_errors']}",
        )
        first_xml = manager.get(f"{base_url}/api/exports/artifacts/{first['id']}/xml")
        require(
            first_xml.status_code == 200, "First immutable XML cannot be downloaded"
        )
        require(
            hashlib.sha256(first_xml.content).hexdigest() == first["xml_sha256"],
            "First XML hash mismatch",
        )

        second = api(
            manager,
            "POST",
            f"{base_url}/api/exports/invoices/{invoice_id}/generate",
            manager_user,
            payload={"reason": "Stage F repeatability smoke test"},
            expected=201,
        ).json()
        require(
            second["source_export_id"] == first["id"],
            "Re-export did not link its source artifact",
        )
        require(
            second["xml_sha256"] == first["xml_sha256"],
            "Deterministic re-export changed XML bytes",
        )
        xml_response = manager.get(
            f"{base_url}/api/exports/artifacts/{second['id']}/xml"
        )
        require(xml_response.status_code == 200, "Re-export XML cannot be downloaded")
        semantics = xml_semantics(
            xml_response.content, {**current["data"], **structured_address}
        )

        pdf = manager.get(f"{base_url}/api/invoices/{invoice_id}/pdf")
        require(
            pdf.status_code == 200 and pdf.content.startswith(b"%PDF"),
            "Original Paperless PDF is unavailable",
        )
        require(
            hashlib.sha256(pdf.content).hexdigest() == second["pdf_sha256"],
            "PDF snapshot hash mismatch",
        )

        batch = api(
            manager,
            "POST",
            f"{base_url}/api/exports",
            manager_user,
            payload={"invoice_ids": [invoice_id]},
            expected=201,
        ).json()
        require(batch["status"] == "CREATED", "Batch has an unexpected status")
        require(
            batch["items"][0]["export_artifact_id"] == second["id"],
            "Batch did not use latest XML",
        )
        archive = manager.get(f"{base_url}/api/exports/{batch['id']}/download")
        require(archive.status_code == 200, "Batch ZIP cannot be downloaded")
        require(
            hashlib.sha256(archive.content).hexdigest() == batch["archive_sha256"],
            "ZIP hash mismatch",
        )
        with zipfile.ZipFile(io.BytesIO(archive.content)) as package:
            names = sorted(package.namelist())
            require(
                names
                == sorted(
                    [
                        batch["items"][0]["pdf_filename"],
                        batch["items"][0]["xml_filename"],
                    ]
                ),
                "ZIP layout is unstable",
            )
            require(
                package.read(batch["items"][0]["xml_filename"]) == xml_response.content,
                "ZIP XML differs",
            )
            require(
                package.read(batch["items"][0]["pdf_filename"]) == pdf.content,
                "ZIP PDF differs",
            )

        response_path = Path(
            os.environ.get(
                "POHODA_RESPONSE_SAMPLE",
                "/app/pohoda-xsd/samples/received-invoice-response.xml",
            )
        )
        require(
            response_path.is_file(),
            f"POHODA response fixture is missing: {response_path}",
        )
        response_upload = manager.post(
            f"{base_url}/api/exports/responses",
            headers={"X-CSRF-Token": manager_user["csrf_token"]},
            data={"export_artifact_id": second["id"], "batch_id": batch["id"]},
            files={
                "response_file": (
                    "received-invoice-response.xml",
                    response_path.read_bytes(),
                    "application/xml",
                )
            },
        )
        require(
            response_upload.status_code == 201,
            f"Response upload failed: {response_upload.text[:500]}",
        )
        parsed = response_upload.json()
        require(
            parsed["parse_status"] == "PARSED",
            f"Response parser failed: {parsed['parse_errors']}",
        )
        require(
            detail(manager, base_url, invoice_id)["status"] == "EXPORT_CREATED",
            "Response upload changed workflow state",
        )

        audit_rows = response_json(
            manager.get(f"{base_url}/api/invoices/{invoice_id}/audit"), "audit"
        )
        audit_types = {row["event_type"] for row in audit_rows}
        required_audit = {
            "XML_GENERATION_STARTED",
            "XML_GENERATED",
            "XML_VALIDATION_PASSED",
            "REEXPORTED",
            "EXPORT_CREATED",
            "POHODA_RESPONSE_UPLOADED",
            "POHODA_RESPONSE_PARSED",
        }
        require(required_audit <= audit_types, "Stage F audit events are incomplete")
        print(
            json.dumps(
                {
                    "app_url": base_url,
                    "paperless_document_id": document_id,
                    "invoice_id": invoice_id,
                    "invoice_revision": current["current_revision_number"],
                    "final_status": "EXPORT_CREATED",
                    "migration_expected": "0006",
                    "xsd_status": second["status"],
                    "xsd_bundle_version": second["xsd_bundle_version"],
                    "generator_version": second["generator_version"],
                    "encoding": second["encoding"],
                    "artifact_id": second["id"],
                    "source_artifact_id": first["id"],
                    "xml_bytes": len(xml_response.content),
                    "xml_sha256": second["xml_sha256"],
                    "pdf_bytes": len(pdf.content),
                    "pdf_sha256": second["pdf_sha256"],
                    "xml_semantics": semantics,
                    "batch_id": batch["id"],
                    "batch_number": batch["batch_number"],
                    "zip_sha256": batch["archive_sha256"],
                    "zip_entries": names,
                    "response_parse_status": parsed["parse_status"],
                    "import_confirmation": "NOT_PERFORMED; awaits manual POHODA import",
                    "audit_events": len(audit_rows),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        for client in clients.values():
            client.close()


if __name__ == "__main__":
    main()
