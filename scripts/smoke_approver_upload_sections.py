#!/usr/bin/env python3
"""Live approver upload, section permission, self-approval and review-gate smoke."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from smoke_stage_b import login, require, response_json


def mutate(client, method: str, url: str, user: dict[str, Any], payload=None, expected=200):
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


def main() -> None:
    base = os.environ["APP_BASE_URL"].rstrip("/")
    manager = login(base, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"])
    approver = login(base, "approver1", os.environ["TEST_APPROVER_1_PASSWORD"])
    manager_user = response_json(manager.get(f"{base}/api/auth/me"), "manager /me")
    approver_user = response_json(approver.get(f"{base}/api/auth/me"), "approver /me")
    marker = uuid.uuid4().hex[:8]
    try:
        centers = []
        for suffix in ("A", "B"):
            center = response_json(
                mutate(
                    manager,
                    "POST",
                    f"{base}/api/cost-centers",
                    manager_user,
                    {
                        "code": f"SMK-{suffix}-{marker}",
                        "name": f"Smoke sekce {suffix} {marker}",
                        "pohoda_code": f"SMK-{suffix}-{marker}",
                        "active": True,
                    },
                    expected=201,
                ),
                f"create section {suffix}",
            )
            centers.append(center)
        mutate(
            manager,
            "PUT",
            f"{base}/api/section-permissions",
            manager_user,
            {
                "approver_subject": approver_user["subject"],
                "cost_center_id": centers[0]["id"],
                "active": True,
            },
        )

        fixture = Path(
            os.environ.get(
                "APPROVER_UPLOAD_SMOKE_PDF",
                "/fixtures/synthetic/synthetic-invoice-cs-en.pdf",
            )
        ).read_bytes() + f"\n% approver section smoke {marker}\n".encode()
        upload_response = approver.post(
            f"{base}/api/uploads",
            headers={"X-CSRF-Token": approver_user["csrf_token"]},
            data={"idempotency_key": f"approver-sections-{uuid.uuid4()}"},
            files={
                "document": (
                    f"approver-sections-{marker}.pdf",
                    fixture,
                    "application/pdf",
                )
            },
        )
        require(upload_response.status_code == 202, upload_response.text[:500])
        upload = upload_response.json()
        require(upload["upload_origin"] == "APPROVER", "Upload origin is not APPROVER")
        deadline = time.monotonic() + int(os.environ.get("APPROVAL_UPLOAD_AI_TIMEOUT_SECONDS", "1900"))
        while time.monotonic() < deadline:
            upload = response_json(
                approver.get(f"{base}/api/uploads/{upload['id']}"), "approver upload tracking"
            )
            if upload["status"] == "READY_FOR_REVIEW":
                break
            require(
                upload["status"] not in {"FAILED", "FAILED_RETRYABLE", "ERROR", "SUBMISSION_UNKNOWN"},
                f"Upload failed in {upload['status']}: {upload.get('error_message')}",
            )
            time.sleep(3)
        require(upload["status"] == "READY_FOR_REVIEW", "Approver upload extraction timed out")
        require(upload.get("invoice_id"), "Upload did not create an Approval invoice")
        invoice_id = upload["invoice_id"]
        detail = response_json(approver.get(f"{base}/api/invoices/{invoice_id}"), "own detail")
        require(detail["paperless"]["upload_origin"] == "APPROVER", "Invoice provenance lost")
        total = str(detail["data"]["total_amount"])

        unauthorized = mutate(
            approver,
            "PUT",
            f"{base}/api/invoices/{invoice_id}/allocations",
            approver_user,
            {
                "expected_revision": detail["current_revision_number"],
                "allocations": [{"cost_center_id": centers[1]["id"], "amount": total}],
            },
            expected=409,
        )
        require("sekce" in unauthorized.text.lower(), "Unauthorized-section error is unclear")
        detail = response_json(
            mutate(
                approver,
                "PUT",
                f"{base}/api/invoices/{invoice_id}/allocations",
                approver_user,
                {
                    "expected_revision": detail["current_revision_number"],
                    "allocations": [{"cost_center_id": centers[0]["id"], "amount": total}],
                },
            ),
            "authorized allocation",
        )
        mutate(
            approver,
            "POST",
            f"{base}/api/invoices/{invoice_id}/confirm-original",
            approver_user,
        )
        tasks = response_json(approver.get(f"{base}/api/approvals/mine"), "approver tasks")
        assignment = next(row for row in tasks if row["invoice_id"] == invoice_id)
        require(assignment["pre_review"], "Uploader assignment is not marked pre-review")
        mutate(
            approver,
            "POST",
            f"{base}/api/approvals/{assignment['id']}/decision",
            approver_user,
            {"action": "APPROVE", "comment": None},
        )
        detail = response_json(approver.get(f"{base}/api/invoices/{invoice_id}"), "self-approved detail")
        require(detail["status"] != "APPROVED", "Self-approval bypassed queue review")
        mutate(
            approver,
            "POST",
            f"{base}/api/invoices/{invoice_id}/submit-for-review",
            approver_user,
        )

        manager_rows = response_json(
            manager.get(f"{base}/api/invoices", params={"view": "all"}), "manager queue"
        )
        manager_row = next(row for row in manager_rows if row["id"] == invoice_id)
        require(manager_row["upload_origin"] == "APPROVER", "Manager queue lacks provenance")
        before = response_json(manager.get(f"{base}/api/invoices/{invoice_id}"), "manager detail")
        changed = response_json(
            mutate(
                manager,
                "PUT",
                f"{base}/api/invoices/{invoice_id}/classification",
                manager_user,
                {
                    "document_type": "RECEIVED_ADVANCE_INVOICE",
                    "processing_mode": "FOR_APPROVAL",
                    "expected_revision": before["current_revision_number"],
                },
            ),
            "advance reclassification",
        )
        require(
            changed["current_revision_number"] == before["current_revision_number"] + 1,
            "Manager change did not create a revision",
        )
        require(
            changed["classification"]["pohoda_import_method"] == "NONE",
            "Advance invoice POHODA method is not NONE",
        )
        mutate(
            manager,
            "POST",
            f"{base}/api/invoices/{invoice_id}/submit",
            manager_user,
        )
        reviewed = response_json(manager.get(f"{base}/api/invoices/{invoice_id}"), "reviewed detail")
        require(reviewed["queue_review"]["reviewed_by"] == manager_user["subject"], "Review gate missing")
        history = response_json(
            approver.get(f"{base}/api/approvals/history/{invoice_id}"), "approver history"
        )
        require(
            any(row["decision"] == "APPROVE" and row["invalidated"] for row in history["history"]),
            "Historical self-approval was not preserved as invalidated",
        )
        print(
            json.dumps(
                {
                    "upload": "OK",
                    "upload_id": upload["id"],
                    "paperless_document_id": upload["paperless_document_id"],
                    "invoice_id": invoice_id,
                    "ocr_length": len(detail["paperless"]["ocr_text"]),
                    "section_permission": centers[0]["code"],
                    "unauthorized_section_http": unauthorized.status_code,
                    "self_approval": "HISTORICAL_INVALIDATED",
                    "queue_submit": "OK",
                    "queue_manager_review": "OK",
                    "revision_before": before["current_revision_number"],
                    "revision_after": changed["current_revision_number"],
                    "advance_pohoda_method": changed["classification"]["pohoda_import_method"],
                    "final_status": reviewed["status"],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    finally:
        manager.close()
        approver.close()


if __name__ == "__main__":
    main()
