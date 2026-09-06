#!/usr/bin/env python3
"""Verify deployed manual-save validation lifecycle without invoking AI."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any

from smoke_stage_b import login, require, response_json


def patch_invoice(
    client: Any,
    base_url: str,
    csrf_token: str,
    invoice_id: str,
    revision: int,
    changes: dict[str, Any],
) -> dict[str, Any]:
    return response_json(
        client.patch(
            f"{base_url}/api/invoices/{invoice_id}",
            headers={"X-CSRF-Token": csrf_token},
            json={"expected_revision": revision, "changes": changes},
        ),
        "manual invoice save",
    )


def validation_codes(invoice: dict[str, Any]) -> set[str]:
    return {row["code"] for row in invoice["validations"]}


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    paperless_document_id = int(
        os.environ.get("MANUAL_VALIDATION_PAPERLESS_DOCUMENT_ID", "24")
    )
    manager = login(
        base_url, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"]
    )
    try:
        user = response_json(manager.get(f"{base_url}/api/auth/me"), "manager /me")
        invoices = response_json(
            manager.get(f"{base_url}/api/invoices?view=all&sort=source_desc"),
            "invoice list",
        )
        row = next(
            (
                item
                for item in invoices
                if item["paperless_document_id"] == paperless_document_id
            ),
            None,
        )
        require(row is not None, f"Paperless document {paperless_document_id} is missing")
        before = response_json(
            manager.get(f"{base_url}/api/invoices/{row['id']}"), "invoice detail"
        )
        ai_runs_before = len(before["ai"]["history"])

        stale = patch_invoice(
            manager,
            base_url,
            user["csrf_token"],
            before["id"],
            before["current_revision_number"],
            {
                "total_without_vat": "4000.00",
                "total_vat": "800.00",
                "total_amount": "4800.00",
                "vat_lines": [
                    {
                        "vat_rate": "21",
                        "taxable_base": "4000.00",
                        "vat_amount": "840.00",
                        "gross_amount": "4840.00",
                        "adjustment_type": "ROUNDING",
                        "source_text": "Smoke: chybný AI-like snapshot",
                    }
                ],
            },
        )
        stale_codes = validation_codes(stale)
        require(
            "VAT_ROUNDING_ADJUSTMENT" in stale_codes,
            "The intentionally stale snapshot did not produce a rounding warning",
        )

        corrected = patch_invoice(
            manager,
            base_url,
            user["csrf_token"],
            before["id"],
            stale["current_revision_number"],
            {
                "total_without_vat": "4300.00",
                "total_vat": "903.00",
                "total_amount": "5203.00",
                "vat_lines": [
                    {
                        "vat_rate": "21",
                        "taxable_base": "4300.00",
                        "vat_amount": "903.00",
                        "gross_amount": "5203.00",
                        "adjustment_type": None,
                        "source_text": "Smoke: ručně ověřený řádek DPH 21 %",
                    }
                ],
            },
        )
        corrected_codes = validation_codes(corrected)
        required_codes = {
            "VAT_ROW_OK",
            "VAT_BASE_TOTAL_OK",
            "VAT_TOTAL_OK",
            "TOTAL_MATH_OK",
        }
        require(
            required_codes <= corrected_codes,
            f"Corrected validation is incomplete: {sorted(corrected_codes)}",
        )
        require(
            "VAT_ROUNDING_ADJUSTMENT" not in corrected_codes,
            "Stale rounding warning remained current after manual save",
        )
        require(
            "VAT_TOTAL_MATH" not in corrected_codes,
            "Corrected totals still have a VAT total mismatch",
        )
        totals = tuple(
            Decimal(str(corrected["data"][field]))
            for field in ("total_without_vat", "total_vat", "total_amount")
        )
        require(
            totals == (Decimal("4300.00"), Decimal("903.00"), Decimal("5203.00")),
            f"Corrected totals were not persisted: {totals}",
        )
        require(
            corrected["current_revision_number"] > stale["current_revision_number"],
            "Manual correction did not create a new current revision",
        )
        require(
            len(corrected["ai"]["history"]) == ai_runs_before,
            "Manual save unexpectedly started or removed an AI run",
        )
        require(
            corrected["classification"]["extraction_source"] == "MANUAL",
            "Manual provenance was not recorded",
        )

        print(
            json.dumps(
                {
                    "app_url": base_url,
                    "paperless_document_id": paperless_document_id,
                    "invoice_id": corrected["id"],
                    "revision_before": before["current_revision_number"],
                    "stale_revision": stale["current_revision_number"],
                    "corrected_revision": corrected["current_revision_number"],
                    "corrected_values": {
                        "base": str(totals[0]),
                        "vat": str(totals[1]),
                        "total": str(totals[2]),
                    },
                    "stale_validation_codes": sorted(stale_codes),
                    "current_validation_codes": sorted(corrected_codes),
                    "stale_rounding_removed": True,
                    "ai_runs_before": ai_runs_before,
                    "ai_runs_after": len(corrected["ai"]["history"]),
                    "extraction_source": corrected["classification"][
                        "extraction_source"
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        manager.close()


if __name__ == "__main__":
    main()
