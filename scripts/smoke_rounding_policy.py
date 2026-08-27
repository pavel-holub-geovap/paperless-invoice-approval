#!/usr/bin/env python3
"""Real Pixel re-extraction/apply and GIRITON rounding regression smoke."""

from __future__ import annotations

import json
import os
import time
from decimal import Decimal
from typing import Any

from smoke_stage_b import login, require, response_json


def detail(client, base_url: str, invoice_id: str) -> dict[str, Any]:
    return response_json(
        client.get(f"{base_url}/api/invoices/{invoice_id}"), "invoice detail"
    )


def invoice_for_document(
    client,
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
    require(row is not None, f"Paperless document {paperless_document_id} was not found")
    return detail(client, base_url, row["id"])


def wait_for_run(
    client, base_url: str, invoice_id: str, after_revision: int
) -> dict[str, Any]:
    deadline = time.monotonic() + int(
        os.environ.get("ROUNDING_SMOKE_AI_TIMEOUT_SECONDS", "1200")
    )
    while time.monotonic() < deadline:
        current = detail(client, base_url, invoice_id)
        latest = current["ai"]["latest"]
        if latest and latest["extraction_revision"] > after_revision:
            if latest["status"] == "AI_COMPLETED":
                return current
            if latest["status"] == "AI_FAILED":
                raise RuntimeError(
                    f"AI extraction failed: {latest['error_code']} {latest['error_message']}"
                )
        time.sleep(3)
    raise RuntimeError("Pixel AI re-extraction timed out")


def rounding_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in data.get("vat_lines", [])
        if row.get("adjustment_type") == "ROUNDING"
    ]


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    manager = login(
        base_url, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"]
    )
    try:
        user = response_json(manager.get(f"{base_url}/api/auth/me"), "manager /me")
        headers = {"X-CSRF-Token": user["csrf_token"]}
        rows = response_json(
            manager.get(f"{base_url}/api/invoices?view=all&sort=source_desc"),
            "invoice list",
        )
        pixel = invoice_for_document(
            manager,
            base_url,
            rows,
            int(os.environ.get("PIXEL_PAPERLESS_DOCUMENT_ID", "24")),
        )
        giriton = invoice_for_document(
            manager,
            base_url,
            rows,
            int(os.environ.get("GIRITON_PAPERLESS_DOCUMENT_ID", "11")),
        )
        require(
            pixel["data"].get("supplier_name") == "Pixel Design s.r.o.",
            "Configured Pixel document has a different supplier",
        )
        require(
            giriton["data"].get("invoice_number") == "25081151",
            "Configured GIRITON document has a different invoice number",
        )
        before_history = list(pixel["ai"]["history"])
        before_latest_revision = pixel["ai"]["latest"]["extraction_revision"]
        before_invoice_revision = pixel["current_revision_number"]
        skip_extraction = os.environ.get("ROUNDING_SMOKE_SKIP_EXTRACTION") == "1"

        if skip_extraction:
            candidate = pixel["ai"]["latest"]
            require(candidate["applied"], "Latest Pixel extraction is not applied")
            applied = pixel
        else:
            queued = manager.post(
                f"{base_url}/api/invoices/{pixel['id']}/ai-extractions",
                headers=headers,
            )
            require(queued.status_code == 202, f"Queue returned HTTP {queued.status_code}")
            candidate_detail = wait_for_run(
                manager, base_url, pixel["id"], before_latest_revision
            )
            candidate = candidate_detail["ai"]["latest"]
            require(candidate["requires_confirmation"], "Re-extraction was not a candidate")
            require(not candidate["applied"], "Candidate was unexpectedly auto-applied")
        require(candidate["model"] == "qwen3:8b", "Unexpected model")
        require(
            candidate["prompt_version"] == "invoice-extraction.cs-en.v6",
            "Unexpected prompt version",
        )
        require(candidate["raw_response_preserved"], "Raw Qwen response was not preserved")
        candidate_data = candidate["candidate_data"]
        require(not rounding_rows(candidate_data), "Pixel candidate contains ROUNDING")
        require(
            not any(
                row["code"] == "VAT_ROUNDING_ADJUSTMENT"
                for row in candidate["validation_results"]
            ),
            "Pixel candidate has VAT_ROUNDING_ADJUSTMENT",
        )

        if not skip_extraction:
            applied_response = manager.post(
                f"{base_url}/api/invoices/{pixel['id']}/ai-extractions/{candidate['id']}/apply",
                headers=headers,
                json={"confirm_overwrite": True},
            )
            applied = response_json(applied_response, "apply Pixel extraction")
        data = applied["data"]
        base = Decimal(str(data["total_without_vat"]))
        vat = Decimal(str(data["total_vat"]))
        total = Decimal(str(data["total_amount"]))
        codes = {row["code"] for row in applied["validations"]}
        require((base, vat, total) == (Decimal("4300.00"), Decimal("903.00"), Decimal("5203.00")), "Pixel totals changed")
        require(total - base - vat == Decimal("0.00"), "Pixel arithmetic differs")
        require(not rounding_rows(data), "Applied Pixel data contains ROUNDING")
        require("VAT_ROUNDING_ADJUSTMENT" not in codes, "Applied Pixel warning remains")
        require(
            {"VAT_ROW_OK", "VAT_BASE_TOTAL_OK", "VAT_TOTAL_OK", "TOTAL_MATH_OK"}
            <= codes,
            "Pixel OK validations are incomplete",
        )
        if not skip_extraction:
            require(
                applied["current_revision_number"] > before_invoice_revision,
                "Applying Pixel candidate did not create a revision",
            )
            require(
                len(applied["ai"]["history"]) == len(before_history) + 1,
                "AI history was not preserved",
            )
        giriton_rounding = rounding_rows(giriton["data"])
        require(len(giriton_rounding) == 1, "GIRITON explicit rounding is missing")
        giriton_row = giriton_rounding[0]
        require(
            (
                Decimal(str(giriton_row["taxable_base"])),
                Decimal(str(giriton_row["vat_amount"])),
                Decimal(str(giriton_row["gross_amount"])),
            )
            == (Decimal("0.29"), Decimal("0.06"), Decimal("0.35")),
            "GIRITON rounding values changed",
        )
        require(
            any(
                row["code"] == "VAT_ROUNDING_ADJUSTMENT"
                for row in giriton["validations"]
            ),
            "GIRITON rounding warning is missing",
        )

        rejections = candidate.get("normalization_result", {}).get("rejections", [])
        print(
            json.dumps(
                {
                    "app_url": base_url,
                    "pixel": {
                        "paperless_document_id": applied["paperless_document_id"],
                        "invoice_id": applied["id"],
                        "extraction_id": candidate["id"],
                        "extraction_revision": candidate["extraction_revision"],
                        "prompt_version": candidate["prompt_version"],
                        "duration_ms": candidate["duration_ms"],
                        "raw_response_preserved": candidate["raw_response_preserved"],
                        "normalization_rejections": rejections,
                        "extraction_skipped": skip_extraction,
                        "invoice_revision_before": before_invoice_revision,
                        "invoice_revision_after": applied["current_revision_number"],
                        "base": str(base),
                        "vat": str(vat),
                        "total": str(total),
                        "arithmetic_difference": str(total - base - vat),
                        "applied_rounding": None,
                        "validation_codes": sorted(codes),
                    },
                    "giriton": {
                        "paperless_document_id": giriton["paperless_document_id"],
                        "invoice_id": giriton["id"],
                        "invoice_number": giriton["data"]["invoice_number"],
                        "rounding": {
                            key: giriton_row[key]
                            for key in (
                                "taxable_base",
                                "vat_amount",
                                "gross_amount",
                                "source_text",
                            )
                        },
                        "rounding_warning_present": True,
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
