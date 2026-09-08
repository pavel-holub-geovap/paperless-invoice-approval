#!/usr/bin/env python3
"""Run the deployed Stage B smoke test without printing credentials."""

from __future__ import annotations

import json
import os
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

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


def redirect_hosts(*responses: httpx.Response) -> list[str]:
    urls = [
        item.url
        for response in responses
        for item in [*response.history, response]
    ]
    return list(dict.fromkeys(urlsplit(str(url)).hostname or "" for url in urls))


def login(
    base_url: str,
    username: str,
    password: str,
    *,
    include_redirect_hosts: bool = False,
) -> httpx.Client | tuple[httpx.Client, list[str]]:
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
    hosts = redirect_hosts(login_page, callback)
    forbidden_hosts = {"172.30.172.167", "localhost", "127.0.0.1"}
    require(
        not forbidden_hosts.intersection(hosts),
        f"Forbidden OIDC redirect host for {username}",
    )
    require(
        urlsplit(str(callback.url)).hostname == urlsplit(base_url).hostname,
        f"OIDC callback did not return to the Approval host for {username}",
    )
    return (client, hosts) if include_redirect_hosts else client


def response_json(response: httpx.Response, context: str) -> Any:
    require(response.status_code == 200, f"{context} returned HTTP {response.status_code}")
    return response.json()


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    document_id = int(os.environ.get("SMOKE_PAPERLESS_DOCUMENT_ID", "1"))

    manager, manager_redirect_hosts = login(
        base_url,
        "queue-manager",
        os.environ["TEST_QUEUE_MANAGER_PASSWORD"],
        include_redirect_hosts=True,
    )
    try:
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
    finally:
        manager.close()

    approver, approver_redirect_hosts = login(
        base_url,
        "approver1",
        os.environ["TEST_APPROVER_1_PASSWORD"],
        include_redirect_hosts=True,
    )
    try:
        approver_user = response_json(approver.get(f"{base_url}/api/auth/me"), "approver1 /me")
        require("APPROVER" in approver_user["roles"], "approver1 role is missing")
        tasks = response_json(approver.get(f"{base_url}/api/approvals/mine"), "approver1 tasks")
        history = response_json(
            approver.get(
                f"{base_url}/api/approvals/history",
                params={"page": 1, "page_size": 20},
            ),
            "approver1 history",
        )
        approver_invoice_list = response_json(
            approver.get(f"{base_url}/api/invoices"), "approver1 scoped invoice list"
        )
        invoice_list_status = 200
        require(
            all(
                row.get("uploaded_by") == approver_user["username"]
                or row.get("approvals_required", 0) > 0
                for row in approver_invoice_list
            ),
            "approver1 scoped list contains an unrelated invoice",
        )
    finally:
        approver.close()

    print(
        json.dumps(
            {
                "app_url": base_url,
                "queue_manager_login": "OK",
                "queue_manager_redirect_hosts": manager_redirect_hosts,
                "queue_manager_roles": manager_user["roles"],
                "approver1_login": "OK",
                "approver1_redirect_hosts": approver_redirect_hosts,
                "callback_host": urlsplit(base_url).hostname,
                "approver1_roles": approver_user["roles"],
                "approver1_tasks": len(tasks),
                "approver1_history_total": history["total"],
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
