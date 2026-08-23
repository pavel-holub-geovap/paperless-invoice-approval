#!/usr/bin/env python3
"""Run the deployed Stage B smoke test without printing credentials."""

from __future__ import annotations

import json
import os
from html.parser import HTMLParser
from typing import Any

import httpx


class KeycloakLoginForm(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.action: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form" and attributes.get("id") == "kc-form-login":
            self.action = attributes.get("action")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def login(base_url: str, username: str, password: str) -> httpx.Client:
    client = httpx.Client(follow_redirects=True, timeout=30, trust_env=False)
    login_page = client.get(f"{base_url}/api/auth/login")
    require(login_page.status_code == 200, f"OIDC login page failed for {username}")
    parser = KeycloakLoginForm()
    parser.feed(login_page.text)
    require(parser.action is not None, f"Keycloak form was not found for {username}")
    callback = client.post(
        parser.action,
        data={"username": username, "password": password, "credentialId": ""},
    )
    require(callback.status_code == 200, f"OIDC callback failed for {username}")
    return client


def response_json(response: httpx.Response, context: str) -> Any:
    require(response.status_code == 200, f"{context} returned HTTP {response.status_code}")
    return response.json()


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    document_id = int(os.environ.get("SMOKE_PAPERLESS_DOCUMENT_ID", "1"))

    with login(
        base_url,
        "queue-manager",
        os.environ["TEST_QUEUE_MANAGER_PASSWORD"],
    ) as manager:
        manager_user = response_json(manager.get(f"{base_url}/api/auth/me"), "queue-manager /me")
        require("QUEUE_MANAGER" in manager_user["roles"], "queue-manager role is missing")
        invoices = response_json(manager.get(f"{base_url}/api/invoices"), "invoice dashboard")
        invoice = next(
            (row for row in invoices if row["paperless_document_id"] == document_id),
            None,
        )
        require(invoice is not None, f"Paperless document {document_id} is not on the dashboard")
        detail = response_json(
            manager.get(f"{base_url}/api/invoices/{invoice['id']}"),
            "invoice detail",
        )
        pdf = manager.get(f"{base_url}/api/invoices/{invoice['id']}/pdf")
        require(pdf.status_code == 200, f"PDF proxy returned HTTP {pdf.status_code}")
        require(pdf.headers.get("content-type", "").startswith("application/pdf"), "PDF MIME type is invalid")
        require(pdf.content.startswith(b"%PDF"), "PDF proxy did not return the original PDF")

    with login(
        base_url,
        "approver1",
        os.environ["TEST_APPROVER_1_PASSWORD"],
    ) as approver:
        approver_user = response_json(approver.get(f"{base_url}/api/auth/me"), "approver1 /me")
        require("APPROVER" in approver_user["roles"], "approver1 role is missing")
        tasks = response_json(approver.get(f"{base_url}/api/approvals/mine"), "approver1 tasks")
        invoice_list_status = approver.get(f"{base_url}/api/invoices").status_code
        require(invoice_list_status == 403, "approver1 unexpectedly sees the manager invoice list")

    print(
        json.dumps(
            {
                "app_url": base_url,
                "queue_manager_login": "OK",
                "queue_manager_roles": manager_user["roles"],
                "approver1_login": "OK",
                "approver1_roles": approver_user["roles"],
                "approver1_tasks": len(tasks),
                "approver_invoice_list_http": invoice_list_status,
                "paperless_document_id": document_id,
                "invoice_id": invoice["id"],
                "invoice_status": detail["status"],
                "sync_status": detail["paperless"]["sync_status"],
                "ocr_length": len(detail["paperless"]["ocr_text"]),
                "pdf_http": pdf.status_code,
                "pdf_content_type": pdf.headers["content-type"],
                "pdf_bytes": len(pdf.content),
                "dashboard_items": len(invoices),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
