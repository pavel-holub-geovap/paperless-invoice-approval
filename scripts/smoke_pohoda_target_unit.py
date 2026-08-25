#!/usr/bin/env python3
"""Create a new GIRITON artifact and verify bytes from the normal download endpoint."""

from __future__ import annotations

import hashlib
import json
import os
from xml.etree import ElementTree as ET

from smoke_stage_b import api, login, require, response_json

NS = {
    "dat": "http://www.stormware.cz/schema/version_2/data.xsd",
    "inv": "http://www.stormware.cz/schema/version_2/invoice.xsd",
    "typ": "http://www.stormware.cz/schema/version_2/type.xsd",
}
EXPECTED_TARGET_ICO = "15049248"
EXPECTED_SUPPLIER_ICO = "28652240"


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    document_id = int(os.environ.get("POHODA_TARGET_SMOKE_DOCUMENT_ID", "11"))
    manager = login(
        base_url,
        "queue-manager",
        os.environ["TEST_QUEUE_MANAGER_PASSWORD"],
    )
    try:
        user = response_json(manager.get(f"{base_url}/api/auth/me"), "manager /me")
        config = response_json(
            manager.get(f"{base_url}/api/exports/config"),
            "POHODA export config",
        )
        require(
            config["pohoda_target_ico"] == EXPECTED_TARGET_ICO,
            "Unexpected POHODA_TARGET_ICO",
        )
        rows = response_json(
            manager.get(f"{base_url}/api/invoices?view=all"),
            "invoice list",
        )
        row = next(
            item for item in rows if item["paperless_document_id"] == document_id
        )
        invoice_id = row["id"]
        before = response_json(
            manager.get(f"{base_url}/api/invoices/{invoice_id}"),
            "invoice detail before re-export",
        )
        old_artifact_id = (
            before["pohoda_export"]["id"] if before.get("pohoda_export") else None
        )
        old_download_http = None
        old_data_pack_ico = None
        if old_artifact_id:
            old_download = manager.get(
                f"{base_url}/api/exports/artifacts/{old_artifact_id}/xml"
            )
            old_download_http = old_download.status_code
            if old_download.status_code == 200:
                old_data_pack_ico = ET.fromstring(old_download.content).get("ico")
        artifact = response_json(
            api(
                manager,
                "POST",
                f"{base_url}/api/exports/invoices/{invoice_id}/generate",
                user,
                payload={"reason": "Oprava cílové účetní jednotky dataPack/@ico"},
                expected=201,
            ),
            "new POHODA target artifact",
        )
        require(artifact["id"] != old_artifact_id, "A new immutable artifact was not created")
        require(artifact["status"] == "XSD_VALID", "New artifact is not XSD-valid")
        require(
            artifact["pohoda_target_validation"]["status"] == "TARGET_UNIT_VALID",
            "Target-unit semantic validation did not pass",
        )
        download_url = f"{base_url}/api/exports/artifacts/{artifact['id']}/xml"
        download = manager.get(download_url)
        require(download.status_code == 200, f"XML download failed: {download.status_code}")
        sha256 = hashlib.sha256(download.content).hexdigest()
        require(sha256 == artifact["xml_sha256"], "Downloaded XML hash differs from artifact")

        root = ET.fromstring(download.content)
        supplier_ico = root.findtext(
            ".//inv:partnerIdentity/typ:address/typ:ico",
            namespaces=NS,
        )
        require(root.tag == f"{{{NS['dat']}}}dataPack", "Root is not dat:dataPack")
        require(root.get("ico") == EXPECTED_TARGET_ICO, "Downloaded dataPack/@ico is wrong")
        require(supplier_ico == EXPECTED_SUPPLIER_ICO, "Downloaded supplier IČO is wrong")
        require(root.get("ico") != supplier_ico, "Supplier IČO leaked into dataPack/@ico")
        if not config["pohoda_target_key_configured"]:
            require(root.get("key") is None, "Unexpected dataPack/@key")

        first_lines = download.content.decode("windows-1250").splitlines()[:8]
        print(
            json.dumps(
                {
                    "app_url": base_url,
                    "paperless_document_id": document_id,
                    "invoice_id": invoice_id,
                    "old_artifact_id": old_artifact_id,
                    "old_download_http": old_download_http,
                    "old_data_pack_ico": old_data_pack_ico,
                    "new_artifact_id": artifact["id"],
                    "download_url": download_url,
                    "pohoda_target_ico": config["pohoda_target_ico"],
                    "data_pack_ico": root.get("ico"),
                    "data_pack_key": root.get("key"),
                    "supplier_ico": supplier_ico,
                    "xsd_status": artifact["status"],
                    "target_unit_status": artifact["pohoda_target_validation"]["status"],
                    "xml_sha256": sha256,
                    "xml_bytes": len(download.content),
                    "first_lines": first_lines,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        manager.close()


if __name__ == "__main__":
    main()
