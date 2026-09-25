#!/usr/bin/env python3
"""Live INVOICE_SUBMITTER provenance, RBAC, approval and PDF smoke."""

from __future__ import annotations

import json
import os
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from smoke_approver_upload_sections import (
    assignment_for,
    mutate,
    wait_for_approved_pdf,
    wait_for_upload,
)
from smoke_stage_b import login, require, response_json


def main() -> None:
    base = os.environ["APP_BASE_URL"].rstrip("/")
    credentials = {
        "admin": ("admin1", os.environ["TEST_ADMIN_PASSWORD"]),
        "manager": ("queue-manager", os.environ["TEST_QUEUE_MANAGER_PASSWORD"]),
        "submitter": ("submitter1", os.environ["TEST_SUBMITTER_PASSWORD"]),
        "approver1": ("approver1", os.environ["TEST_APPROVER_1_PASSWORD"]),
        "approver2": ("approver2", os.environ["TEST_APPROVER_2_PASSWORD"]),
        "approver3": ("approver3", os.environ["TEST_APPROVER_3_PASSWORD"]),
    }
    clients = {name: login(base, *values) for name, values in credentials.items()}
    users = {
        name: response_json(client.get(f"{base}/api/auth/me"), f"{name} /me")
        for name, client in clients.items()
    }
    marker = uuid.uuid4().hex[:8]
    try:
        require(
            users["submitter"]["roles"] == ["INVOICE_SUBMITTER"],
            f"submitter1 roles are not isolated: {users['submitter']['roles']}",
        )
        require(
            clients["submitter"].get(f"{base}/api/approvals/mine").status_code == 403,
            "INVOICE_SUBMITTER can access approval tasks",
        )
        require(
            clients["submitter"].get(f"{base}/api/admin/invoices").status_code == 403,
            "INVOICE_SUBMITTER can access administration",
        )

        centers: list[dict[str, Any]] = []
        for suffix in ("A", "B", "C"):
            response = mutate(
                clients["admin"],
                "POST",
                f"{base}/api/cost-centers",
                users["admin"],
                {
                    "code": f"SUB-{suffix}-{marker}",
                    "name": f"Submitter smoke {suffix} {marker}",
                    "pohoda_code": f"SUB-{suffix}-{marker}",
                    "active": True,
                },
                expected=201,
            )
            centers.append(response.json())
        for name, center in zip(("approver1", "approver2", "approver3"), centers, strict=True):
            mutate(
                clients["admin"],
                "PUT",
                f"{base}/api/section-permissions",
                users["admin"],
                {
                    "approver_subject": users[name]["subject"],
                    "cost_center_id": center["id"],
                    "active": True,
                },
            )

        original = Path(
            os.environ.get(
                "SUBMITTER_SMOKE_PDF",
                "/fixtures/synthetic/synthetic-invoice-cs-en.pdf",
            )
        ).read_bytes() + f"\n% invoice submitter smoke {marker}\n".encode()
        upload_response = clients["submitter"].post(
            f"{base}/api/uploads",
            headers={"X-CSRF-Token": users["submitter"]["csrf_token"]},
            data={
                "idempotency_key": f"submitter-{uuid.uuid4()}",
                "submission_mode": "INVOICE_SUBMITTER",
            },
            files={"document": (f"submitter-{marker}.pdf", original, "application/pdf")},
        )
        require(upload_response.status_code == 202, upload_response.text[:500])
        upload = wait_for_upload(
            clients["submitter"], base, upload_response.json()["id"]
        )
        require(upload["upload_origin"] == "INVOICE_SUBMITTER", "Wrong upload provenance")
        invoice_id = upload["invoice_id"]
        detail = response_json(
            clients["submitter"].get(f"{base}/api/invoices/{invoice_id}"),
            "submitter own invoice",
        )
        detail = response_json(
            mutate(
                clients["submitter"],
                "PATCH",
                f"{base}/api/invoices/{invoice_id}",
                users["submitter"],
                {
                    "expected_revision": detail["current_revision_number"],
                    "changes": {
                        "supplier_name": "Submitter Smoke Supplier s.r.o.",
                        "supplier_ico": "28652240",
                        "invoice_number": f"SUB-{marker}",
                        "issue_date": "2026-09-01",
                        "due_date": "2026-09-30",
                        "currency": "CZK",
                        "description": "Licence Microsoft Teams pro zaměstnance společnosti.",
                        "total_without_vat": "82644.63",
                        "total_vat": "17355.37",
                        "total_amount": "100000.00",
                        "rounding_amount": "0.00",
                        "vat_lines": [
                            {
                                "vat_rate": "21",
                                "taxable_base": "82644.63",
                                "vat_amount": "17355.37",
                            }
                        ],
                    },
                },
            ),
            "submitter invoice data",
        )
        detail = response_json(
            mutate(
                clients["submitter"],
                "PUT",
                f"{base}/api/invoices/{invoice_id}/classification",
                users["submitter"],
                {
                    "document_type": "RECEIVED_INVOICE",
                    "processing_mode": "FOR_APPROVAL",
                    "payment_required": True,
                    "expected_revision": detail["current_revision_number"],
                },
            ),
            "submitter classification",
        )
        percentages = ("60", "25", "15")
        notes = (
            "Rozdělení 60 % podle aktivních licencí k 09/2026.",
            "Rozdělení 25 % podle aktivních licencí k 09/2026.",
            "Rozdělení 15 % podle aktivních licencí k 09/2026.",
        )
        detail = response_json(
            mutate(
                clients["submitter"],
                "PUT",
                f"{base}/api/invoices/{invoice_id}/allocations",
                users["submitter"],
                {
                    "expected_revision": detail["current_revision_number"],
                    "allocations": [
                        {
                            "cost_center_id": center["id"],
                            "percentage": percentage,
                            "note": note,
                        }
                        for center, percentage, note in zip(
                            centers, percentages, notes, strict=True
                        )
                    ],
                },
            ),
            "submitter allocations",
        )
        require(
            all(not assignment_for(detail, center["id"]) for center in centers),
            "Submitter proposal created assignments before submit",
        )
        mutate(
            clients["submitter"],
            "POST",
            f"{base}/api/invoices/{invoice_id}/confirm-original",
            users["submitter"],
        )
        submitted = response_json(
            mutate(
                clients["submitter"],
                "POST",
                f"{base}/api/invoices/{invoice_id}/submit-for-review",
                users["submitter"],
            ),
            "submitter queue submission",
        )
        require(submitted["status"] == "QUEUE_REVIEW", "Document did not enter queue review")
        require(
            all(not assignment_for(submitted, center["id"]) for center in centers),
            "Submitter submission created an approval assignment",
        )
        denied_edit = clients["submitter"].patch(
            f"{base}/api/invoices/{invoice_id}",
            headers={"X-CSRF-Token": users["submitter"]["csrf_token"]},
            json={
                "expected_revision": submitted["current_revision_number"],
                "changes": {"description": "must stay read-only"},
            },
        )
        require(denied_edit.status_code == 403, "Submitter can edit after queue submission")
        require(
            clients["submitter"].get(f"{base}/api/invoices/{invoice_id}/pdf").status_code
            == 200,
            "Submitter cannot open the original PDF",
        )

        manager_detail = response_json(
            clients["manager"].get(f"{base}/api/invoices/{invoice_id}"),
            "manager proposal detail",
        )
        require(
            manager_detail["paperless"]["uploaded_by"] == "submitter1"
            and manager_detail["paperless"]["upload_origin"] == "INVOICE_SUBMITTER",
            "Manager cannot see submitter provenance",
        )
        final_notes = (notes[0], f"{notes[1]} Ověřeno správcem.", notes[2])
        manager_detail = response_json(
            mutate(
                clients["manager"],
                "PUT",
                f"{base}/api/invoices/{invoice_id}/allocations",
                users["manager"],
                {
                    "expected_revision": manager_detail["current_revision_number"],
                    "allocations": [
                        {
                            "cost_center_id": center["id"],
                            "percentage": percentage,
                            "note": note,
                        }
                        for center, percentage, note in zip(
                            centers, percentages, final_notes, strict=True
                        )
                    ],
                },
            ),
            "manager allocation proposal change",
        )
        for name, center in zip(("approver1", "approver2", "approver3"), centers, strict=True):
            allocation = next(
                row
                for row in manager_detail["allocations"]
                if row["cost_center"]["id"] == center["id"]
            )
            manager_detail = response_json(
                mutate(
                    clients["manager"],
                    "PUT",
                    f"{base}/api/invoices/{invoice_id}/allocations/{allocation['id']}/approvers",
                    users["manager"],
                    {
                        "approver_subjects": [users[name]["subject"]],
                        "expected_revision": manager_detail["current_revision_number"],
                    },
                ),
                f"assign {name}",
            )
        mutate(
            clients["manager"],
            "POST",
            f"{base}/api/invoices/{invoice_id}/confirm-original",
            users["manager"],
        )
        mutate(
            clients["manager"],
            "POST",
            f"{base}/api/invoices/{invoice_id}/submit",
            users["manager"],
        )
        for name in ("approver1", "approver2", "approver3"):
            tasks = response_json(
                clients[name].get(f"{base}/api/approvals/mine"), f"{name} tasks"
            )
            task = next(row for row in tasks if row["invoice_id"] == invoice_id)
            mutate(
                clients[name],
                "POST",
                f"{base}/api/approvals/{task['id']}/decision",
                users[name],
                {"action": "APPROVE", "comment": "Submitter live smoke"},
            )

        approved = wait_for_approved_pdf(clients["manager"], base, invoice_id)
        pdf_response = clients["manager"].get(
            f"{base}/api/invoices/{invoice_id}/approved-pdf"
        )
        require(pdf_response.status_code == 200, pdf_response.text[:500])
        pdf_text = "\n".join(
            page.extract_text() or ""
            for page in PdfReader(BytesIO(pdf_response.content), strict=False).pages
        )
        for expected in (
            "submitter1",
            "approver1",
            "approver2",
            "approver3",
            "Rozdělení 60 %",
            "Ověřeno správcem.",
            "Rozdělení 15 %",
        ):
            require(expected in pdf_text, f"Approved PDF is missing {expected!r}")
        audit = response_json(
            clients["manager"].get(f"{base}/api/invoices/{invoice_id}/audit"), "audit"
        )
        submitter_decisions = [
            row
            for row in audit
            if row["actor"] == users["submitter"]["subject"]
            and row["event_type"] in {"APPROVED", "UPLOADER_SECTION_AUTO_APPROVED"}
        ]
        require(not submitter_decisions, "Submitter generated an approval audit event")
        submission_event = next(
            row for row in audit if row["event_type"] == "SUBMITTED_TO_QUEUE_MANAGER"
        )
        require(
            submission_event["metadata"]["submission_mode"] == "INVOICE_SUBMITTER"
            and submission_event["metadata"]["auto_approved_allocations"] == 0,
            "Submission provenance audit is incomplete",
        )
        own_rows = response_json(
            clients["submitter"].get(f"{base}/api/invoices?scope=uploaded&view=all"),
            "submitter own list",
        )
        require(any(row["id"] == invoice_id for row in own_rows), "Own invoice missing")
        print(
            json.dumps(
                {
                    "oidc_submitter": "OK",
                    "submitter_roles": users["submitter"]["roles"],
                    "upload_origin": upload["upload_origin"],
                    "paperless_document_id": upload["paperless_document_id"],
                    "invoice_id": invoice_id,
                    "ocr_length": len(submitted["paperless"]["ocr_text"]),
                    "allocation_percentages": list(percentages),
                    "submitter_assignments_after_submit": 0,
                    "submitter_approval_decisions": 0,
                    "post_submit_edit": "HTTP 403",
                    "original_pdf": "OK",
                    "manager_provenance": "OK",
                    "manager_allocation_change": "OK",
                    "approved_pdf_artifact_id": approved["approved_pdf"]["id"],
                    "approved_pdf_sha256": approved["approved_pdf"]["approved_pdf_sha256"],
                    "approved_pdf_submitter_separate": "OK",
                    "final_status": approved["status"],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    finally:
        for client in clients.values():
            client.close()


if __name__ == "__main__":
    main()
