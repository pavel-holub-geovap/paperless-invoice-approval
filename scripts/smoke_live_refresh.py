#!/usr/bin/env python3
"""Verify cross-session approval visibility through the same polling API used by React."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from smoke_stage_b import login, require, response_json


def request(client, method: str, url: str, user: dict[str, Any], payload=None):
    response = client.request(
        method,
        url,
        headers={"X-CSRF-Token": user["csrf_token"]},
        json=payload,
    )
    require(
        response.status_code == 200,
        f"{method} {url} returned {response.status_code}: {response.text[:300]}",
    )
    return response.json()


def invoice_row(client, base_url: str, invoice_id: str) -> dict[str, Any]:
    rows = response_json(client.get(f"{base_url}/api/invoices"), "manager queue")
    row = next((item for item in rows if item["id"] == invoice_id), None)
    require(row is not None, "Invoice disappeared from manager queue")
    return row


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    document_id = int(os.environ.get("SMOKE_PAPERLESS_DOCUMENT_ID", "1"))
    names = ("manager", "approver1", "approver2", "approver3")
    credentials = {
        "manager": ("queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"]),
        "approver1": ("approver1", os.environ["TEST_APPROVER_1_PASSWORD"]),
        "approver2": ("approver2", os.environ["TEST_APPROVER_2_PASSWORD"]),
        "approver3": ("approver3", os.environ["TEST_APPROVER_3_PASSWORD"]),
    }
    clients = {
        name: login(base_url, *credentials[name])
        for name in names
    }
    try:
        users = {
            name: response_json(client.get(f"{base_url}/api/auth/me"), f"{name} /me")
            for name, client in clients.items()
        }
        manager = clients["manager"]
        rows = response_json(manager.get(f"{base_url}/api/invoices"), "manager queue")
        row = next(
            (item for item in rows if item["paperless_document_id"] == document_id),
            None,
        )
        require(row is not None, f"Document {document_id} is not in the queue")
        invoice_id = row["id"]
        current = response_json(
            manager.get(f"{base_url}/api/invoices/{invoice_id}"), "manager detail"
        )
        changed_zip = "100 01" if current["data"].get("supplier_zip") != "100 01" else "100 02"
        current = request(
            manager,
            "PATCH",
            f"{base_url}/api/invoices/{invoice_id}",
            users["manager"],
            {
                "changes": {"supplier_zip": changed_zip},
                "comment": "Cross-session polling smoke",
                "expected_revision": current["current_revision_number"],
            },
        )
        if not current["original_review_confirmed"]:
            current = request(
                manager,
                "POST",
                f"{base_url}/api/invoices/{invoice_id}/confirm-original",
                users["manager"],
            )
        current = request(
            manager,
            "POST",
            f"{base_url}/api/invoices/{invoice_id}/submit",
            users["manager"],
        )
        require(current["status"] == "AWAITING_APPROVAL", "Invoice was not submitted")
        before = invoice_row(manager, base_url, invoice_id)
        manager_opened_at = time.monotonic()

        tasks = response_json(
            clients["approver1"].get(f"{base_url}/api/approvals/mine"),
            "approver1 tasks",
        )
        task = next((item for item in tasks if item["invoice_id"] == invoice_id), None)
        require(task is not None, "Approver1 did not receive the new revision")
        request(
            clients["approver1"],
            "POST",
            f"{base_url}/api/approvals/{task['id']}/decision",
            users["approver1"],
            {"action": "APPROVE", "comment": "Cross-session polling smoke"},
        )

        deadline = time.monotonic() + 10
        after = before
        polls = 0
        while time.monotonic() < deadline:
            polls += 1
            after = invoice_row(manager, base_url, invoice_id)
            if after["approvals_done"] > before["approvals_done"]:
                break
            time.sleep(0.5)
        require(
            after["approvals_done"] == before["approvals_done"] + 1,
            "Manager polling session did not observe approver1 decision",
        )

        for name in ("approver2", "approver3"):
            tasks = response_json(
                clients[name].get(f"{base_url}/api/approvals/mine"), f"{name} tasks"
            )
            task = next((item for item in tasks if item["invoice_id"] == invoice_id), None)
            require(task is not None, f"{name} task is missing")
            request(
                clients[name],
                "POST",
                f"{base_url}/api/approvals/{task['id']}/decision",
                users[name],
                {"action": "APPROVE", "comment": "Cross-session polling cleanup"},
            )
        final = response_json(
            manager.get(f"{base_url}/api/invoices/{invoice_id}"), "final detail"
        )
        require(final["status"] == "APPROVED", "Cleanup approvals did not finish")
        print(
            json.dumps(
                {
                    "invoice_id": invoice_id,
                    "invoice_revision": final["current_revision_number"],
                    "manager_session_remained_open": True,
                    "manual_reload_used": False,
                    "polls": polls,
                    "observed_after_ms": round((time.monotonic() - manager_opened_at) * 1000),
                    "before": {
                        "approvals_done": before["approvals_done"],
                        "approvals_required": before["approvals_required"],
                        "status": before["status"],
                    },
                    "after_approver1": {
                        "approvals_done": after["approvals_done"],
                        "approvals_required": after["approvals_required"],
                        "status": after["status"],
                    },
                    "final_status": final["status"],
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
