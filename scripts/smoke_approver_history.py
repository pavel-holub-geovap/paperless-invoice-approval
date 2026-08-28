#!/usr/bin/env python3
"""Real OIDC, Paperless OCR fulltext, PDF and historical RBAC smoke."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from smoke_stage_b import login, require, response_json


def history(client, base_url: str, **params: str) -> dict[str, Any]:
    response = client.get(
        f"{base_url}/api/approvals/history",
        params={"page": "1", "page_size": "100", **params},
    )
    return response_json(response, "approver history")


def ocr_only_phrases(invoice: dict[str, Any]) -> list[str]:
    ocr = str(invoice["paperless"].get("ocr_text") or "")
    structured = json.dumps(
        {
            "data": invoice.get("data"),
            "title": invoice["paperless"].get("title"),
            "correspondent": invoice["paperless"].get("correspondent"),
            "tags": invoice["paperless"].get("tags"),
        },
        ensure_ascii=False,
    ).casefold()
    words = re.findall(r"[^\W\d_]{4,}", ocr, flags=re.UNICODE)
    phrases: list[str] = []
    for size in (4, 3, 2):
        for index in range(len(words) - size + 1):
            phrase = " ".join(words[index : index + size])
            if phrase.casefold() not in structured and phrase not in phrases:
                phrases.append(phrase)
    return phrases


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    clients = {
        "manager": login(
            base_url,
            "queue-manager",
            os.environ["TEST_QUEUE_MANAGER_PASSWORD"],
        ),
        "approver1": login(
            base_url, "approver1", os.environ["TEST_APPROVER_1_PASSWORD"]
        ),
        "approver2": login(
            base_url, "approver2", os.environ["TEST_APPROVER_2_PASSWORD"]
        ),
    }
    try:
        users = {
            name: response_json(client.get(f"{base_url}/api/auth/me"), f"{name} /me")
            for name, client in clients.items()
        }
        require("APPROVER" in users["approver1"]["roles"], "approver1 role missing")
        require("APPROVER" in users["approver2"]["roles"], "approver2 role missing")

        first = history(clients["approver1"], base_url)
        second = history(clients["approver2"], base_url)
        second_ids = {row["invoice_id"] for row in second["items"]}
        target = None
        target_detail = None
        manager_detail = None
        for row in first["items"]:
            if row["invoice_id"] in second_ids or not row["pdf_available"]:
                continue
            detail_response = clients["approver1"].get(
                f"{base_url}/api/approvals/history/{row['invoice_id']}"
            )
            if detail_response.status_code != 200:
                continue
            candidate_detail = detail_response.json()
            if not any(item["invalidated"] for item in candidate_detail["history"]):
                continue
            candidate_manager = response_json(
                clients["manager"].get(f"{base_url}/api/invoices/{row['invoice_id']}"),
                "manager invoice detail",
            )
            if not candidate_manager["paperless"].get("ocr_text"):
                continue
            target = row
            target_detail = candidate_detail
            manager_detail = candidate_manager
            break
        require(target is not None, "No exclusive historical invoice with invalidation and PDF")
        require(target_detail is not None and manager_detail is not None, "Target detail missing")

        search_query = None
        search_result = None
        for phrase in ocr_only_phrases(manager_detail)[:80]:
            response = clients["approver1"].get(
                f"{base_url}/api/approvals/history",
                params={"q": phrase, "page": 1, "page_size": 100},
            )
            if response.status_code != 200:
                continue
            candidate = response.json()
            matching = [
                row for row in candidate["items"] if row["invoice_id"] == target["invoice_id"]
            ]
            if matching and matching[0].get("ocr_snippet"):
                search_query = phrase
                search_result = candidate
                break
        require(search_query is not None, "No OCR-only Paperless fulltext phrase found")
        require(search_result is not None, "Fulltext result missing")

        second_search = history(clients["approver2"], base_url, q=search_query)
        require(
            target["invoice_id"] not in {row["invoice_id"] for row in second_search["items"]},
            "Unauthorized invoice leaked into approver2 search",
        )
        foreign_detail = clients["approver2"].get(
            f"{base_url}/api/approvals/history/{target['invoice_id']}"
        )
        foreign_pdf = clients["approver2"].get(
            f"{base_url}/api/invoices/{target['invoice_id']}/pdf"
        )
        require(foreign_detail.status_code == 403, "Foreign history detail was not forbidden")
        require(foreign_pdf.status_code == 403, "Foreign PDF was not forbidden")

        pdf = clients["approver1"].get(
            f"{base_url}/api/invoices/{target['invoice_id']}/pdf"
        )
        require(pdf.status_code == 200, "Authorized historical PDF failed")
        require(pdf.content.startswith(b"%PDF"), "Historical PDF is not a PDF")

        invalidated = next(row for row in target_detail["history"] if row["invalidated"])
        print(
            json.dumps(
                {
                    "app_url": base_url,
                    "approver": users["approver1"]["username"],
                    "invoice_id": target["invoice_id"],
                    "paperless_document_id": target["paperless_document_id"],
                    "historical_assignment": invalidated["assignment_id"],
                    "historical_decision": invalidated["decision"],
                    "historical_revision": invalidated["revision"],
                    "invalidated": invalidated["invalidated"],
                    "allocation": {
                        "cost_center": invalidated["cost_center"]["code"],
                        "amount": str(invalidated["amount"]),
                    },
                    "fulltext_query": search_query,
                    "found_via_paperless_ocr": True,
                    "ocr_snippet_present": bool(
                        next(
                            row
                            for row in search_result["items"]
                            if row["invoice_id"] == target["invoice_id"]
                        ).get("ocr_snippet")
                    ),
                    "pdf_available": True,
                    "pdf_size": len(pdf.content),
                    "approver2_search_hidden": True,
                    "approver2_history_detail_http": foreign_detail.status_code,
                    "approver2_pdf_http": foreign_pdf.status_code,
                    "approver1_history_total": first["total"],
                    "approver2_history_total": second["total"],
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
