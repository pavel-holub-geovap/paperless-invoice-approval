#!/usr/bin/env python3
"""Create and inspect POHODA XML for the already approved GIRITON smoke invoice."""

from __future__ import annotations

import hashlib
import json
import os
from decimal import Decimal
from xml.etree import ElementTree as ET

from smoke_stage_b import login, require, response_json

NS = {
    "inv": "http://www.stormware.cz/schema/version_2/invoice.xsd",
    "typ": "http://www.stormware.cz/schema/version_2/type.xsd",
}


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    manager = login(base_url, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"])
    try:
        user = response_json(manager.get(f"{base_url}/api/auth/me"), "manager /me")
        rows = response_json(manager.get(f"{base_url}/api/invoices?view=all"), "invoice list")
        row = next(item for item in rows if item["paperless_document_id"] == 11)
        invoice_id = row["id"]
        current = response_json(manager.get(f"{base_url}/api/invoices/{invoice_id}"), "detail")
        require(current["status"] in {"APPROVED", "READY_FOR_EXPORT", "EXPORT_CREATED"}, f"Expected exportable state, got {current['status']}")
        if current["status"] == "APPROVED":
            response = manager.post(
                f"{base_url}/api/exports/invoices/{invoice_id}/generate",
                headers={"X-CSRF-Token": user["csrf_token"]},
                json={},
            )
            require(response.status_code == 201, f"Export HTTP {response.status_code}: {response.text[:500]}")
            artifact = response.json()
        else:
            artifact = current["pohoda_export"]
        require(artifact["status"] == "XSD_VALID", "XML is not XSD valid")
        download = manager.get(f"{base_url}/api/exports/artifacts/{artifact['id']}/xml")
        require(download.status_code == 200, "XML download failed")
        require(hashlib.sha256(download.content).hexdigest() == artifact["xml_sha256"], "Hash mismatch")
        root = ET.fromstring(download.content)
        address = root.find(".//inv:partnerIdentity/typ:address", NS)
        payment = root.find(".//inv:paymentAccount", NS)
        require(address is not None and payment is not None, "Address or bank missing")
        base = sum((Decimal(node.text or "0") for node in root.findall(".//inv:invoiceItem/inv:homeCurrency/typ:price", NS)), Decimal(0))
        vat = sum((Decimal(node.text or "0") for node in root.findall(".//inv:invoiceItem/inv:homeCurrency/typ:priceVAT", NS)), Decimal(0))
        print(json.dumps({
            "invoice_id": invoice_id,
            "paperless_document_id": 11,
            "revision": current["current_revision_number"],
            "artifact_id": artifact["id"],
            "xml_sha256": artifact["xml_sha256"],
            "xsd_status": artifact["status"],
            "target_ico": root.get("ico"),
            "target_key": root.get("key"),
            "address": {key: address.findtext(f"typ:{key}", namespaces=NS) for key in ("company", "street", "city", "zip", "ico", "dic")},
            "bank": {"accountNo": payment.findtext("typ:accountNo", namespaces=NS), "bankCode": payment.findtext("typ:bankCode", namespaces=NS)},
            "totals": {"base": f"{base:.2f}", "vat": f"{vat:.2f}", "total": f"{base + vat:.2f}"},
        }, ensure_ascii=False, indent=2))
    finally:
        manager.close()


if __name__ == "__main__":
    main()
