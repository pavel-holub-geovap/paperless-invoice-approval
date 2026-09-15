#!/usr/bin/env python3
"""Live uploader auto-approval, revision and approved-PDF business smoke."""

from __future__ import annotations

import json
import os
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader
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


def wait_for_upload(approver, base: str, upload_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + int(
        os.environ.get("APPROVAL_UPLOAD_AI_TIMEOUT_SECONDS", "1900")
    )
    while time.monotonic() < deadline:
        upload = response_json(
            approver.get(f"{base}/api/uploads/{upload_id}"),
            "approver upload tracking",
        )
        if upload["status"] == "READY_FOR_REVIEW":
            return upload
        require(
            upload["status"]
            not in {"FAILED", "FAILED_RETRYABLE", "ERROR", "SUBMISSION_UNKNOWN"},
            f"Upload failed in {upload['status']}: {upload.get('error_message')}",
        )
        time.sleep(3)
    raise AssertionError("Approver upload extraction timed out")


def wait_for_approved_pdf(manager, base: str, invoice_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        detail = response_json(
            manager.get(f"{base}/api/invoices/{invoice_id}"),
            "approved PDF detail",
        )
        artifact = detail.get("approved_pdf") or {}
        if artifact.get("status") == "STORED":
            return detail
        require(
            artifact.get("status") not in {"FAILED", "INVALID"},
            f"Approved PDF failed: {artifact}",
        )
        time.sleep(2)
    raise AssertionError("Approved PDF was not stored in time")


def assignment_for(detail: dict[str, Any], center_id: str) -> list[dict[str, Any]]:
    allocation = next(
        row for row in detail["allocations"] if row["cost_center"]["id"] == center_id
    )
    return allocation["assignments"]


def main() -> None:
    base = os.environ["APP_BASE_URL"].rstrip("/")
    admin = login(base, "admin1", os.environ["TEST_ADMIN_PASSWORD"])
    manager = login(base, "queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"])
    approver1 = login(base, "approver1", os.environ["TEST_APPROVER_1_PASSWORD"])
    approver2 = login(base, "approver2", os.environ["TEST_APPROVER_2_PASSWORD"])
    admin_user = response_json(admin.get(f"{base}/api/auth/me"), "admin /me")
    manager_user = response_json(manager.get(f"{base}/api/auth/me"), "manager /me")
    approver1_user = response_json(approver1.get(f"{base}/api/auth/me"), "approver1 /me")
    approver2_user = response_json(approver2.get(f"{base}/api/auth/me"), "approver2 /me")
    marker = uuid.uuid4().hex[:8]
    try:
        centers = []
        for suffix in ("A", "B"):
            center_response = mutate(
                admin,
                "POST",
                f"{base}/api/cost-centers",
                admin_user,
                {
                    "code": f"SMK-{suffix}-{marker}",
                    "name": f"Smoke sekce {suffix} {marker}",
                    "pohoda_code": f"SMK-{suffix}-{marker}",
                    "active": True,
                },
                expected=201,
            )
            centers.append(center_response.json())
        for subject, center in (
            (approver1_user["subject"], centers[0]),
            (approver2_user["subject"], centers[1]),
        ):
            mutate(
                admin,
                "PUT",
                f"{base}/api/section-permissions",
                admin_user,
                {
                    "approver_subject": subject,
                    "cost_center_id": center["id"],
                    "active": True,
                },
            )

        original_pdf = Path(
            os.environ.get(
                "APPROVER_UPLOAD_SMOKE_PDF",
                "/fixtures/synthetic/synthetic-invoice-cs-en.pdf",
            )
        ).read_bytes() + f"\n% approver section smoke {marker}\n".encode()
        upload_response = approver1.post(
            f"{base}/api/uploads",
            headers={"X-CSRF-Token": approver1_user["csrf_token"]},
            data={"idempotency_key": f"approver-sections-{uuid.uuid4()}"},
            files={
                "document": (
                    f"approver-sections-{marker}.pdf",
                    original_pdf,
                    "application/pdf",
                )
            },
        )
        require(upload_response.status_code == 202, upload_response.text[:500])
        upload = wait_for_upload(approver1, base, upload_response.json()["id"])
        require(upload["upload_origin"] == "APPROVER", "Upload origin is not APPROVER")
        require(upload.get("invoice_id"), "Upload did not create an Approval invoice")
        invoice_id = upload["invoice_id"]
        detail = response_json(approver1.get(f"{base}/api/invoices/{invoice_id}"), "own detail")

        detail = response_json(
            mutate(
                approver1,
                "PATCH",
                f"{base}/api/invoices/{invoice_id}",
                approver1_user,
                {
                    "expected_revision": detail["current_revision_number"],
                    "changes": {
                        "total_without_vat": "826.45",
                        "total_vat": "173.55",
                        "total_amount": "1000.00",
                        "vat_lines": [
                            {
                                "vat_rate": "21",
                                "taxable_base": "826.45",
                                "vat_amount": "173.55",
                            }
                        ],
                        "rounding_amount": "0.00",
                    },
                },
            ),
            "uploader financial fields",
        )
        detail = response_json(
            mutate(
                approver1,
                "PUT",
                f"{base}/api/invoices/{invoice_id}/classification",
                approver1_user,
                {
                    "document_type": "RECEIVED_INVOICE",
                    "processing_mode": "FOR_APPROVAL",
                    "payment_required": True,
                    "expected_revision": detail["current_revision_number"],
                },
            ),
            "uploader classification and payment",
        )
        detail = response_json(
            mutate(
                approver1,
                "PUT",
                f"{base}/api/invoices/{invoice_id}/allocations",
                approver1_user,
                {
                    "expected_revision": detail["current_revision_number"],
                    "allocations": [
                        {
                            "cost_center_id": centers[0]["id"],
                            "amount": "400.00",
                            "note": "Vlastní sekce",
                        },
                        {
                            "cost_center_id": centers[1]["id"],
                            "amount": "600.00",
                            "note": "Jiná sekce [B]",
                        },
                    ],
                },
            ),
            "all active uploader sections",
        )
        require(not assignment_for(detail, centers[0]["id"]), "Auto-approval ran before submit")
        require(not assignment_for(detail, centers[1]["id"]), "Foreign section was pre-assigned")
        mutate(
            approver1,
            "POST",
            f"{base}/api/invoices/{invoice_id}/confirm-original",
            approver1_user,
        )
        submitted = response_json(
            mutate(
                approver1,
                "POST",
                f"{base}/api/invoices/{invoice_id}/submit-for-review",
                approver1_user,
            ),
            "uploader submit-for-review",
        )
        assignments_a = assignment_for(submitted, centers[0]["id"])
        assignments_b = assignment_for(submitted, centers[1]["id"])
        require(submitted["status"] == "QUEUE_REVIEW", "Queue-manager gate was bypassed")
        require(
            len(assignments_a) == 1
            and assignments_a[0]["approver_subject"] == approver1_user["subject"]
            and assignments_a[0]["decision"] == "APPROVE",
            "Section A was not auto-approved with a standard decision",
        )
        require(not assignments_b, "Section B was auto-approved without uploader permission")

        manager_detail = response_json(
            manager.get(f"{base}/api/invoices/{invoice_id}"), "manager queue detail"
        )
        before_revision = manager_detail["current_revision_number"]
        changed = response_json(
            mutate(
                manager,
                "PATCH",
                f"{base}/api/invoices/{invoice_id}",
                manager_user,
                {
                    "expected_revision": before_revision,
                    "changes": {"payment_required": False, "rounding_amount": "-0.20"},
                },
            ),
            "manager significant change",
        )
        require(
            changed["current_revision_number"] == before_revision + 1,
            "Manager change did not create a revision",
        )
        require(changed["data"]["payment_required"] is False, "Manager payment edit was lost")
        changed = response_json(
            mutate(
                manager,
                "PATCH",
                f"{base}/api/invoices/{invoice_id}",
                manager_user,
                {
                    "expected_revision": changed["current_revision_number"],
                    "changes": {"payment_required": True, "rounding_amount": "0.00"},
                },
            ),
            "manager final payment and rounding",
        )
        require(changed["data"]["rounding_amount"] == "0.00", "Canonical rounding was lost")
        copied_a = assignment_for(changed, centers[0]["id"])
        require(
            len(copied_a) == 1 and copied_a[0]["status"] == "PENDING",
            "Manager revision did not invalidate and copy section A assignment",
        )
        allocation_b = next(
            row for row in changed["allocations"] if row["cost_center"]["id"] == centers[1]["id"]
        )
        configured = response_json(
            mutate(
                manager,
                "PUT",
                f"{base}/api/invoices/{invoice_id}/allocations/{allocation_b['id']}/approvers",
                manager_user,
                {
                    "approver_subjects": [approver2_user["subject"]],
                    "expected_revision": changed["current_revision_number"],
                },
            ),
            "section B approver",
        )
        mutate(
            manager,
            "POST",
            f"{base}/api/invoices/{invoice_id}/confirm-original",
            manager_user,
        )
        mutate(
            manager,
            "POST",
            f"{base}/api/invoices/{invoice_id}/submit",
            manager_user,
        )
        for client, user in ((approver1, approver1_user), (approver2, approver2_user)):
            tasks = response_json(client.get(f"{base}/api/approvals/mine"), "approver tasks")
            task = next(row for row in tasks if row["invoice_id"] == invoice_id)
            mutate(
                client,
                "POST",
                f"{base}/api/approvals/{task['id']}/decision",
                user,
                {"action": "APPROVE", "comment": "Live payment/rounding PDF smoke"},
            )

        approved = wait_for_approved_pdf(manager, base, invoice_id)
        approved_response = manager.get(f"{base}/api/invoices/{invoice_id}/approved-pdf")
        require(approved_response.status_code == 200, approved_response.text[:500])
        approved_pdf = approved_response.content
        approved_reader = PdfReader(BytesIO(approved_pdf), strict=False)
        original_reader = PdfReader(BytesIO(original_pdf), strict=False)
        extracted_text = "\n".join(page.extract_text() or "" for page in approved_reader.pages)
        for expected in (
            "K ZAPLACENÍ: ANO",
            centers[0]["code"],
            centers[1]["code"],
            "Vlastní sekce",
            "Jiná sekce [B]",
            "approver1",
            "approver2",
        ):
            require(expected in extracted_text, f"Approved PDF is missing {expected!r}")
        require("[Vlastní sekce]" not in extracted_text, "Renderer added square brackets")
        require(
            float(approved_reader.pages[-1].mediabox.height)
            > float(original_reader.pages[-1].mediabox.height),
            "Approval band did not add space outside the original page",
        )
        audit = response_json(manager.get(f"{base}/api/invoices/{invoice_id}/audit"), "audit")
        auto_events = [row for row in audit if row["event_type"] == "UPLOADER_SECTION_AUTO_APPROVED"]
        require(auto_events, "Auto-approval provenance audit is missing")
        require(
            any(row["event_type"] == "APPROVAL_INVALIDATED" for row in audit),
            "Historical auto-approval invalidation is missing",
        )
        allocations = {
            row["cost_center"]["code"]: {"amount": str(row["amount"]), "note": row["note"]}
            for row in approved["allocations"]
        }
        print(
            json.dumps(
                {
                    "upload": "OK",
                    "upload_id": upload["id"],
                    "paperless_document_id": upload["paperless_document_id"],
                    "invoice_id": invoice_id,
                    "ocr_length": len(submitted["paperless"]["ocr_text"]),
                    "all_active_sections": "OK",
                    "section_a_auto_approval": "APPROVE",
                    "section_b_uploader_approval": None,
                    "queue_manager_review": "OK",
                    "revision_before_manager_edit": before_revision,
                    "revision_final": configured["current_revision_number"],
                    "payment_required": approved["data"]["payment_required"],
                    "rounding_amount": approved["data"]["rounding_amount"],
                    "allocations": allocations,
                    "approved_pdf_artifact_id": approved["approved_pdf"]["id"],
                    "approved_pdf_sha256": approved["approved_pdf"]["approved_pdf_sha256"],
                    "approved_pdf_size": len(approved_pdf),
                    "approved_pdf_text": "OK",
                    "approval_band_outside_original": "OK",
                    "final_status": approved["status"],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    finally:
        admin.close()
        manager.close()
        approver1.close()
        approver2.close()


if __name__ == "__main__":
    main()
