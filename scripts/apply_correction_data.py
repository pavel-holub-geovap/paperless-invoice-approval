#!/usr/bin/env python3
"""Apply explicitly supplied, audit-commented correction data through Approval APIs.

The utility is intentionally generic: document IDs and changes are provided as
JSON environment variables, and it never modifies or deletes Paperless sources.
"""

from __future__ import annotations

import json
import os
from typing import Any

from smoke_stage_b import login, require, response_json


def invoice_by_document(rows: list[dict[str, Any]], document_id: int) -> dict[str, Any]:
    row = next(
        (item for item in rows if item["paperless_document_id"] == document_id),
        None,
    )
    require(row is not None, f"Approval invoice for Paperless document {document_id} absent")
    return row


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    patches = json.loads(os.environ.get("CORRECTION_DATA_PATCHES", "[]"))
    duplicate = json.loads(os.environ.get("CORRECTION_DUPLICATE", "null"))
    manager = login(base_url, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"])
    try:
        identity = response_json(manager.get(f"{base_url}/api/auth/me"), "manager identity")
        headers = {"X-CSRF-Token": identity["csrf_token"]}
        rows = response_json(
            manager.get(f"{base_url}/api/invoices", params={"view": "all"}),
            "all invoices",
        )
        report: dict[str, Any] = {"patched": [], "duplicate": None}
        for patch in patches:
            document_id = int(patch["paperless_document_id"])
            invoice = invoice_by_document(rows, document_id)
            response = manager.patch(
                f"{base_url}/api/invoices/{invoice['id']}",
                headers=headers,
                json={"changes": patch["changes"], "comment": patch["comment"]},
            )
            require(response.status_code == 200, f"Correction patch failed for {document_id}")
            detail = response.json()
            data = detail["data"]
            report["patched"].append(
                {
                    "paperless_document_id": document_id,
                    "invoice_id": invoice["id"],
                    "revision": detail["current_revision_number"],
                    "supplier": data.get("supplier_name"),
                    "invoice_number": data.get("invoice_number"),
                    "variable_symbol": data.get("variable_symbol"),
                    "total_without_vat": data.get("total_without_vat"),
                    "total_vat": data.get("total_vat"),
                    "total_amount": data.get("total_amount"),
                    "currency": data.get("currency"),
                    "bank_account_raw": data.get("bank_account_raw"),
                    "bank_account_prefix": data.get("bank_account_prefix"),
                    "bank_account_number": data.get("bank_account_number"),
                    "bank_code": data.get("bank_code"),
                    "iban": data.get("iban"),
                    "swift_bic": data.get("swift_bic"),
                    "validation_findings": [
                        {
                            "code": row["code"],
                            "severity": row["severity"],
                            "expected": row.get("expected"),
                            "actual": row.get("actual"),
                            "difference": (row.get("details") or {}).get("difference"),
                        }
                        for row in detail["validations"]
                        if row["severity"] != "OK"
                    ],
                }
            )

        if duplicate:
            source = invoice_by_document(rows, int(duplicate["paperless_document_id"]))
            target = invoice_by_document(
                rows, int(duplicate["duplicate_of_paperless_document_id"])
            )
            current = response_json(
                manager.get(f"{base_url}/api/invoices/{source['id']}"),
                "duplicate source",
            )
            if current["disposition"]["status"] == "ACTIVE":
                response = manager.post(
                    f"{base_url}/api/invoices/{source['id']}/disposition",
                    headers=headers,
                    json={
                        "disposition": "IGNORED_DUPLICATE",
                        "reason": duplicate["reason"],
                        "comment": duplicate["comment"],
                        "duplicate_of_invoice_id": target["id"],
                    },
                )
                require(response.status_code == 200, "Existing orphan disposition failed")
                current = response.json()
            report["duplicate"] = {
                "paperless_document_id": source["paperless_document_id"],
                "invoice_id": source["id"],
                "source_status": current["source"]["status"],
                "disposition": current["disposition"]["status"],
                "duplicate_of_invoice_id": current["disposition"][
                    "duplicate_of_invoice_id"
                ],
            }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        manager.close()


if __name__ == "__main__":
    main()
