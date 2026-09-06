#!/usr/bin/env python3
"""Verify zero rounding cleanup and preserve a legitimate GIRITON adjustment."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any

from smoke_stage_b import login, require, response_json


def invoice_for_document(
    client: Any,
    base_url: str,
    rows: list[dict[str, Any]],
    paperless_document_id: int,
) -> dict[str, Any]:
    row = next(
        (
            item
            for item in rows
            if item["paperless_document_id"] == paperless_document_id
        ),
        None,
    )
    require(row is not None, f"Paperless document {paperless_document_id} is missing")
    return response_json(
        client.get(f"{base_url}/api/invoices/{row['id']}"), "invoice detail"
    )


def rounding_rows(invoice: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in invoice["data"].get("vat_lines", [])
        if row.get("adjustment_type") == "ROUNDING"
    ]


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    zero_document_id = int(
        os.environ.get("ZERO_ROUNDING_PAPERLESS_DOCUMENT_ID", "58")
    )
    giriton_document_id = int(
        os.environ.get("GIRITON_PAPERLESS_DOCUMENT_ID", "11")
    )
    manager = login(
        base_url, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"]
    )
    try:
        user = response_json(manager.get(f"{base_url}/api/auth/me"), "manager /me")
        rows = response_json(
            manager.get(f"{base_url}/api/invoices?view=all&sort=source_desc"),
            "invoice list",
        )
        before = invoice_for_document(manager, base_url, rows, zero_document_id)
        ai_runs_before = len(before["ai"]["history"])
        require(
            "Zaokrouhlení 0,00" in before["paperless"]["ocr_text"],
            "The zero-rounding source evidence is missing",
        )

        response = manager.patch(
            f"{base_url}/api/invoices/{before['id']}",
            headers={"X-CSRF-Token": user["csrf_token"]},
            json={
                "expected_revision": before["current_revision_number"],
                "changes": {
                    "total_without_vat": "159.82",
                    "total_vat": "19.18",
                    "total_amount": "179.00",
                },
            },
        )
        current = response_json(response, "zero-rounding manual save")
        totals = tuple(
            Decimal(str(current["data"][field]))
            for field in ("total_without_vat", "total_vat", "total_amount")
        )
        require(
            totals == (Decimal("159.82"), Decimal("19.18"), Decimal("179.00")),
            f"Unexpected corrected totals: {totals}",
        )
        codes = {row["code"] for row in current["validations"]}
        require(
            {"VAT_ROW_OK", "VAT_BASE_TOTAL_OK", "VAT_TOTAL_OK", "TOTAL_MATH_OK"}
            <= codes,
            f"Zero-rounding VAT checks are incomplete: {sorted(codes)}",
        )
        require(
            "VAT_ROUNDING_ADJUSTMENT" not in codes,
            "Zero rounding still creates VAT_ROUNDING_ADJUSTMENT",
        )
        require(
            not rounding_rows(current),
            "The stale 179.00 rounding candidate remains current",
        )
        require(
            len(current["ai"]["history"]) == ai_runs_before,
            "Manual cleanup changed append-only AI history",
        )

        giriton = invoice_for_document(manager, base_url, rows, giriton_document_id)
        giriton_rows = rounding_rows(giriton)
        require(len(giriton_rows) == 1, "GIRITON rounding row is missing")
        giriton_rounding = giriton_rows[0]
        require(
            Decimal(str(giriton_rounding["gross_amount"])) == Decimal("0.35"),
            "GIRITON rounding amount changed",
        )
        require(
            any(
                row["code"] == "VAT_ROUNDING_ADJUSTMENT"
                and Decimal(str(row["actual"])) == Decimal("0.35")
                for row in giriton["validations"]
            ),
            "GIRITON rounding validation is missing",
        )

        print(
            json.dumps(
                {
                    "app_url": base_url,
                    "zero_rounding": {
                        "paperless_document_id": current["paperless_document_id"],
                        "invoice_id": current["id"],
                        "revision_before": before["current_revision_number"],
                        "revision_after": current["current_revision_number"],
                        "base": str(totals[0]),
                        "vat": str(totals[1]),
                        "total": str(totals[2]),
                        "rounding": "0.00",
                        "rounding_rows": rounding_rows(current),
                        "validation_codes": sorted(codes),
                        "ai_runs_before": ai_runs_before,
                        "ai_runs_after": len(current["ai"]["history"]),
                    },
                    "giriton": {
                        "paperless_document_id": giriton["paperless_document_id"],
                        "invoice_id": giriton["id"],
                        "rounding": giriton_rounding["gross_amount"],
                        "source_text": giriton_rounding["source_text"],
                        "validation": "VAT_ROUNDING_ADJUSTMENT",
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        manager.close()


if __name__ == "__main__":
    main()
