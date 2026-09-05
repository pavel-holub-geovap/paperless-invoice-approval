#!/usr/bin/env python3
"""Real Approval BFF upload → Paperless → OCR → AI smoke using synthetic PDFs only."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.db import SessionLocal
from app.models import AuditEvent, DocumentUpload
from smoke_stage_b import login, require, response_json
from sqlalchemy import select


def csrf(user: dict[str, Any]) -> dict[str, str]:
    return {"X-CSRF-Token": user["csrf_token"]}


def synthetic_pdf(fixture: bytes, marker: str) -> bytes:
    return fixture + f"\n% Approval upload smoke {marker}\n".encode()


def submit(client, base_url: str, user: dict[str, Any], filename: str, content: bytes):
    return client.post(
        f"{base_url}/api/uploads",
        headers=csrf(user),
        data={"idempotency_key": f"smoke-{uuid.uuid4()}"},
        files={"document": (filename, content, "application/pdf")},
    )


def tracking(client, base_url: str, upload_id: str) -> dict[str, Any]:
    return response_json(
        client.get(f"{base_url}/api/uploads/{upload_id}"),
        f"upload tracking {upload_id}",
    )


def wait_for(
    client,
    base_url: str,
    upload_id: str,
    predicate,
    timeout: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = tracking(client, base_url, upload_id)
        if last["status"] in {"FAILED", "FAILED_RETRYABLE", "SUBMISSION_UNKNOWN", "ERROR"}:
            raise RuntimeError(
                f"Upload {upload_id} failed: {last.get('error_code')} {last.get('error_message')}"
            )
        if predicate(last):
            return last
        time.sleep(3)
    raise RuntimeError(f"Upload {upload_id} timed out in {last.get('status')}")


def invoice_detail(client, base_url: str, invoice_id: str) -> dict[str, Any]:
    return response_json(
        client.get(f"{base_url}/api/invoices/{invoice_id}"),
        f"invoice detail {invoice_id}",
    )


def audit_evidence(upload_id: str, invoice_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        upload = db.get(DocumentUpload, upload_id)
        events = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.invoice_id == invoice_id)
            .order_by(AuditEvent.created_at)
        ).all()
    require(upload is not None, "Upload tracking disappeared from DB")
    success = next(
        (row for row in events if row.event_type == "DOCUMENT_UPLOADED_TO_PAPERLESS"),
        None,
    )
    require(success is not None, "DOCUMENT_UPLOADED_TO_PAPERLESS audit is missing")
    return {
        "event_type": success.event_type,
        "actor_subject": success.actor_subject,
        "actor_username": success.metadata_json.get("actor_username"),
        "correlation_id": success.metadata_json.get("correlation_id"),
        "filename": success.metadata_json.get("filename"),
        "file_size": success.metadata_json.get("file_size"),
        "mime_type": success.metadata_json.get("mime_type"),
        "paperless_document_id": success.metadata_json.get("paperless_document_id"),
    }


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    fixture_path = Path(
        os.environ.get(
            "APPROVAL_UPLOAD_SMOKE_PDF",
            "/fixtures/synthetic-invoice-cs-en.pdf",
        )
    )
    fixture = fixture_path.read_bytes()
    run_id = uuid.uuid4().hex[:10]
    manager = login(
        base_url,
        "queue-manager",
        os.environ["TEST_QUEUE_MANAGER_PASSWORD"],
    )
    approver = login(
        base_url,
        "approver1",
        os.environ["TEST_APPROVER_1_PASSWORD"],
    )
    try:
        manager_user = response_json(manager.get(f"{base_url}/api/auth/me"), "manager /me")
        approver_user = response_json(approver.get(f"{base_url}/api/auth/me"), "approver /me")

        approver_upload = submit(
            approver,
            base_url,
            approver_user,
            f"codex-approver-upload-{run_id}.pdf",
            synthetic_pdf(fixture, f"approver-{run_id}"),
        )
        require(approver_upload.status_code == 202, "Approver upload was not accepted")
        require(
            approver_upload.json().get("upload_origin") == "APPROVER",
            "Approver upload provenance is missing",
        )

        before_rows = response_json(
            manager.get(f"{base_url}/api/invoices", params={"view": "all"}),
            "queue before upload",
        )
        single_name = f"codex-approval-upload-{run_id}.pdf"
        single_bytes = synthetic_pdf(fixture, f"single-{run_id}")
        single_response = submit(
            manager, base_url, manager_user, single_name, single_bytes
        )
        require(single_response.status_code == 202, "Single upload was not accepted")
        single_initial = single_response.json()
        single = wait_for(
            manager,
            base_url,
            single_initial["id"],
            lambda row: row["status"] == "READY_FOR_REVIEW",
            int(os.environ.get("APPROVAL_UPLOAD_AI_TIMEOUT_SECONDS", "1900")),
        )
        require(single["paperless_document_id"], "Single upload has no Paperless ID")
        require(single["invoice_id"], "Single upload has no Approval invoice ID")
        detail = invoice_detail(manager, base_url, single["invoice_id"])
        require(detail["paperless"]["ocr_text"], "Single upload OCR is empty")
        require(detail["ai_status"] == "AI_COMPLETED", "Single upload AI did not complete")
        require(detail["status"] == "QUEUE_REVIEW", "Single upload workflow is not QUEUE_REVIEW")
        require(detail["paperless"]["uploaded_by"] == "queue-manager", "uploaded_by is wrong")
        require(
            detail["paperless"]["source_pdf_sha256"] == hashlib.sha256(single_bytes).hexdigest(),
            "Stored source PDF hash is wrong",
        )
        pdf_response = manager.get(
            f"{base_url}/api/invoices/{single['invoice_id']}/pdf"
        )
        require(pdf_response.status_code == 200, "Approval PDF proxy failed")
        require(pdf_response.content.startswith(b"%PDF-"), "Approval PDF proxy is not PDF")
        after_rows = response_json(
            manager.get(f"{base_url}/api/invoices", params={"view": "all"}),
            "queue after upload",
        )
        require(
            not any(row["id"] == single["invoice_id"] for row in before_rows)
            and any(row["id"] == single["invoice_id"] for row in after_rows),
            "Polling inventory did not expose the new invoice without a new login",
        )

        multi_payloads = [
            (
                f"codex-approval-multi-{run_id}-{index}.pdf",
                synthetic_pdf(fixture, f"multi-{run_id}-{index}"),
            )
            for index in range(1, 4)
        ]
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(submit, manager, base_url, manager_user, name, content)
                for name, content in multi_payloads
            ]
            invalid_future = executor.submit(
                manager.post,
                f"{base_url}/api/uploads",
                headers=csrf(manager_user),
                data={"idempotency_key": f"smoke-{uuid.uuid4()}"},
                files={"document": (f"codex-invalid-{run_id}.txt", b"not a PDF", "text/plain")},
            )
            multi_responses = [future.result() for future in futures]
            invalid_response = invalid_future.result()
        require(invalid_response.status_code == 415, "Invalid multi file was not rejected")
        require(
            all(response.status_code == 202 for response in multi_responses),
            "One of three valid concurrent uploads was not accepted",
        )
        multi_initial = [response.json() for response in multi_responses]
        multi_done = [
            wait_for(
                manager,
                base_url,
                row["id"],
                lambda current: bool(current["invoice_id"] and current["paperless_document_id"]),
                300,
            )
            for row in multi_initial
        ]
        for row in multi_done:
            current = invoice_detail(manager, base_url, row["invoice_id"])
            require(current["paperless"]["ocr_text"], "Multi upload OCR is empty")
            require(
                manager.get(f"{base_url}/api/invoices/{row['invoice_id']}/pdf").status_code == 200,
                "Multi upload PDF proxy failed",
            )

        report = {
            "single": {
                "filename": single_name,
                "sha256": hashlib.sha256(single_bytes).hexdigest(),
                "upload_id": single["id"],
                "paperless_task_id": single["paperless_task_id"],
                "paperless_document_id": single["paperless_document_id"],
                "approval_invoice_id": single["invoice_id"],
                "upload_status": single["status"],
                "ocr_status": "OCR_COMPLETE",
                "ocr_length": len(detail["paperless"]["ocr_text"]),
                "ai_status": detail["ai_status"],
                "workflow_status": detail["status"],
                "model": detail["ai"]["latest"]["model"],
                "source_created_at": single["source_created_at"],
                "approval_created_at": single["approval_created_at"],
                "uploaded_by": single["uploaded_by"],
                "pdf_proxy_sha256": hashlib.sha256(pdf_response.content).hexdigest(),
                "queue_updated_without_f5": True,
                "detail_available": True,
                "audit": audit_evidence(single["id"], single["invoice_id"]),
            },
            "authorization": {"approver_upload_http": approver_upload.status_code},
            "multi": {
                "accepted": len(multi_done),
                "invalid_http": invalid_response.status_code,
                "independent_success": len(multi_done) == 3,
                "documents": [
                    {
                        "filename": row["filename"],
                        "sha256": row["sha256"],
                        "upload_id": row["id"],
                        "paperless_document_id": row["paperless_document_id"],
                        "invoice_id": row["invoice_id"],
                        "status": row["status"],
                        "ai_status": row["ai_status"],
                    }
                    for row in multi_done
                ],
            },
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    finally:
        manager.close()
        approver.close()


if __name__ == "__main__":
    main()
