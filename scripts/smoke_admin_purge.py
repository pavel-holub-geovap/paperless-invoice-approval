#!/usr/bin/env python3
"""Live ADMIN/RBAC/Paperless purge smoke using only a document created by this run."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.integrations.paperless import PaperlessClient, PaperlessNotFound
from smoke_stage_b import login, require, response_json


def mutate(
    client: Any,
    method: str,
    url: str,
    user: dict[str, Any],
    payload: dict[str, Any],
    *,
    expected: int = 200,
):
    response = client.request(
        method,
        url,
        headers={"X-CSRF-Token": user["csrf_token"]},
        json=payload,
    )
    require(
        response.status_code == expected,
        f"{method} {url} returned {response.status_code}: {response.text[:500]}",
    )
    return response


async def paperless_absent(document_id: int) -> bool:
    client = PaperlessClient(get_settings())
    try:
        try:
            await client.get_document(document_id)
        except PaperlessNotFound:
            return True
        return False
    finally:
        await client.close()


def main() -> None:
    base = os.environ["APP_BASE_URL"].rstrip("/")
    admin = login(base, "admin1", os.environ["TEST_ADMIN_PASSWORD"])
    manager = login(base, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"])
    approver = login(base, "approver1", os.environ["TEST_APPROVER_1_PASSWORD"])
    marker = uuid.uuid4().hex[:10]
    try:
        admin_user = response_json(admin.get(f"{base}/api/auth/me"), "admin /me")
        manager_user = response_json(manager.get(f"{base}/api/auth/me"), "manager /me")
        approver_user = response_json(approver.get(f"{base}/api/auth/me"), "approver /me")
        require(admin_user["roles"] == ["ADMIN"], "admin1 must be ADMIN-only")
        require("QUEUE_MANAGER" in manager_user["roles"], "manager role missing")
        require("APPROVER" in approver_user["roles"], "approver role missing")
        require(admin.get(f"{base}/admin").status_code == 200, "Admin SPA route unavailable")
        require(admin.get(f"{base}/api/admin/invoices").status_code == 200, "Admin API unavailable")
        require(
            admin.get(f"{base}/api/invoices").status_code == 403,
            "ADMIN-only user unexpectedly received queue access",
        )

        centers = response_json(
            admin.get(f"{base}/api/cost-centers", params={"include_inactive": "true"}),
            "admin sections",
        )
        code = "ADMIN-SMOKE"
        center = next((row for row in centers if row["code"] == code), None)
        payload = {
            "code": code,
            "name": "ADMIN live smoke",
            "pohoda_code": code,
            "active": True,
        }
        if center is None:
            center = mutate(
                admin,
                "POST",
                f"{base}/api/cost-centers",
                admin_user,
                payload,
                expected=201,
            ).json()
        else:
            center = mutate(
                admin,
                "PUT",
                f"{base}/api/cost-centers/{center['id']}",
                admin_user,
                payload,
            ).json()
        mutate(
            admin,
            "PUT",
            f"{base}/api/section-permissions",
            admin_user,
            {
                "approver_subject": approver_user["subject"],
                "cost_center_id": center["id"],
                "active": True,
            },
        )
        denied = manager.put(
            f"{base}/api/section-permissions",
            headers={"X-CSRF-Token": manager_user["csrf_token"]},
            json={
                "approver_subject": approver_user["subject"],
                "cost_center_id": center["id"],
                "active": False,
            },
        )
        require(denied.status_code == 403, "QUEUE_MANAGER changed global permission")

        fixture = Path(
            os.environ.get(
                "ADMIN_PURGE_SMOKE_PDF",
                "/fixtures/synthetic/synthetic-invoice-cs-en.pdf",
            )
        ).read_bytes() + f"\n% ADMIN purge smoke {marker}\n".encode()
        uploaded = manager.post(
            f"{base}/api/uploads",
            headers={"X-CSRF-Token": manager_user["csrf_token"]},
            data={"idempotency_key": f"admin-purge-{uuid.uuid4()}"},
            files={
                "document": (
                    f"admin-purge-smoke-{marker}.pdf",
                    fixture,
                    "application/pdf",
                )
            },
        )
        require(uploaded.status_code == 202, uploaded.text[:500])
        tracking = uploaded.json()
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            tracking = response_json(
                manager.get(f"{base}/api/uploads/{tracking['id']}"),
                "synthetic upload tracking",
            )
            require(
                tracking["status"]
                not in {"FAILED", "FAILED_RETRYABLE", "SUBMISSION_UNKNOWN", "ERROR"},
                f"Synthetic upload failed: {tracking.get('error_message')}",
            )
            if tracking.get("invoice_id") and tracking.get("paperless_document_id"):
                break
            time.sleep(2)
        invoice_id = tracking.get("invoice_id")
        paperless_id = tracking.get("paperless_document_id")
        require(bool(invoice_id and paperless_id), "Synthetic upload was not synchronized")
        require(
            any(
                row["id"] == invoice_id
                for row in response_json(
                    admin.get(f"{base}/api/admin/invoices"), "admin invoice list"
                )
            ),
            "Synthetic invoice is missing in ADMIN list",
        )

        purged = mutate(
            admin,
            "POST",
            f"{base}/api/admin/invoices/{invoice_id}/purge",
            admin_user,
            {
                "confirmation": "SMAZAT",
                "reason": f"Automatický ADMIN live smoke {marker}",
            },
        ).json()
        require(purged["status"] == "PURGED", "Purge did not report PURGED")
        require(
            manager.get(f"{base}/api/invoices/{invoice_id}").status_code == 404,
            "Purged invoice remains available in Approval",
        )
        require(
            asyncio.run(paperless_absent(int(paperless_id))),
            "Purged original remains available in Paperless",
        )
        audits = response_json(
            admin.get(f"{base}/api/admin/purge-audits"), "purge audit list"
        )
        audit = next((row for row in audits if row["invoice_id"] == invoice_id), None)
        require(audit is not None, "Minimal purge audit is missing")
        require(audit["paperless_document_ids"] == [paperless_id], "Audit Paperless ID differs")

        print(
            json.dumps(
                {
                    "admin_login": "OK",
                    "admin_roles": admin_user["roles"],
                    "admin_ui_http": 200,
                    "admin_api": "OK",
                    "admin_queue_http": 403,
                    "section": center["code"],
                    "permission_subject": approver_user["subject"],
                    "queue_manager_global_permission_http": denied.status_code,
                    "synthetic_upload_id": tracking["id"],
                    "synthetic_invoice_id": invoice_id,
                    "paperless_document_id": paperless_id,
                    "paperless_before_purge": "AVAILABLE",
                    "purge_status": purged["status"],
                    "approval_after_purge_http": 404,
                    "paperless_after_purge_http": 404,
                    "purge_audit_id": audit["id"],
                    "purge_audit_sensitive_content": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        admin.close()
        manager.close()
        approver.close()


if __name__ == "__main__":
    main()
