#!/usr/bin/env python3
"""Read-only runtime and provisioning smoke for a bootstrapped test stack."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import httpx
from app.config import Settings
from app.db import SessionLocal
from lxml import etree
from sqlalchemy import text

TAG_SETTINGS = (
    "paperless_inbox_tag",
    "paperless_tag_processing",
    "paperless_tag_queue_review",
    "paperless_tag_approval",
    "paperless_tag_approved",
    "paperless_tag_rejected",
    "paperless_tag_pohoda_ready",
    "paperless_tag_exported",
    "paperless_tag_imported",
    "paperless_tag_approved_copy",
    "paperless_tag_duplicate",
    "paperless_tag_ignored",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def request_json(client: httpx.Client, path: str, label: str, **kwargs: Any) -> Any:
    response = client.get(path, **kwargs)
    require(response.status_code == 200, f"{label} returned HTTP {response.status_code}")
    return response.json()


def paperless_checks(settings: Settings) -> None:
    token = settings.read_paperless_api_token()
    with httpx.Client(
        base_url=f"{settings.paperless_base_url}/api",
        headers={"Authorization": f"Token {token}", "Accept": "application/json; version=10"},
        timeout=20,
    ) as client:
        found: dict[str, dict[str, Any]] = {}
        for setting_name in TAG_SETTINGS:
            name = str(getattr(settings, setting_name))
            payload = request_json(
                client,
                "/tags/",
                f"Paperless tag {name!r}",
                params={"name__iexact": name, "page_size": 2},
            )
            require(
                payload.get("count") == 1 and len(payload.get("results", [])) == 1,
                f"Paperless tag {name!r} is missing or duplicated",
            )
            found[setting_name] = payload["results"][0]
        require(
            bool(found["paperless_inbox_tag"].get("is_inbox_tag")),
            "Configured Paperless inbox tag is not marked as inbox",
        )
        require(
            not bool(found["paperless_tag_approved_copy"].get("is_inbox_tag")),
            "Approved-copy technical tag must not be marked as inbox",
        )
    print(f"[OK] Paperless REST API and {len(TAG_SETTINGS)} unique technical tags")


def keycloak_availability(settings: Settings) -> None:
    url = (
        f"{settings.keycloak_base_url}/realms/{settings.keycloak_realm}"
        "/.well-known/openid-configuration"
    )
    with httpx.Client(timeout=20) as client:
        metadata = request_json(client, url, "Keycloak OIDC metadata")
    require(metadata.get("authorization_endpoint"), "OIDC authorization endpoint is missing")
    require(metadata.get("token_endpoint"), "OIDC token endpoint is missing")
    print("[OK] Keycloak OIDC metadata")


def keycloak_provisioning(settings: Settings) -> None:
    admin = os.environ.get("KEYCLOAK_ADMIN", "")
    password = os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "")
    require(admin and password, "Keycloak provisioning verification credentials are unavailable")
    with httpx.Client(base_url=settings.keycloak_base_url, timeout=20) as client:
        token_response = client.post(
            "/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": admin,
                "password": password,
            },
        )
        require(token_response.status_code == 200, "Keycloak admin token request failed")
        client.headers["Authorization"] = f"Bearer {token_response.json()['access_token']}"
        realm = settings.keycloak_realm
        require(client.get(f"/admin/realms/{realm}").status_code == 200, "Keycloak realm is missing")
        for role in ("QUEUE_MANAGER", "APPROVER"):
            require(
                client.get(f"/admin/realms/{realm}/roles/{role}").status_code == 200,
                f"Keycloak role {role} is missing",
            )
        client_ids = (
            settings.keycloak_client_id,
            os.environ.get("PAPERLESS_OIDC_CLIENT_ID", "paperless"),
        )
        for client_id in client_ids:
            rows = request_json(
                client,
                f"/admin/realms/{realm}/clients",
                f"Keycloak client {client_id}",
                params={"clientId": client_id},
            )
            require(len(rows) == 1, f"Keycloak client {client_id!r} is missing or duplicated")
        for username in ("queue-manager", "approver1", "approver2", "approver3"):
            rows = request_json(
                client,
                f"/admin/realms/{realm}/users",
                f"Keycloak user {username}",
                params={"username": username, "exact": "true"},
            )
            require(len(rows) == 1, f"Keycloak user {username!r} is missing or duplicated")
    print("[OK] Keycloak realm, 2 clients, 2 roles and 4 unique test users")


def database_check() -> None:
    with SessionLocal() as db:
        require(db.execute(text("SELECT 1")).scalar_one() == 1, "PostgreSQL SELECT 1 failed")
    print("[OK] Approval PostgreSQL connectivity")


def application_health() -> None:
    with httpx.Client(timeout=20) as client:
        backend = request_json(client, "http://backend:8000/api/health", "Approval health")
        worker = request_json(client, "http://backend:8000/api/health/worker", "Worker health")
    require(backend.get("status") == "ok", "Approval backend is not healthy")
    require(worker.get("status") == "ok", "Approval worker is not healthy")
    print("[OK] Approval backend and worker health")


def ollama_check(settings: Settings) -> None:
    with httpx.Client(timeout=20) as client:
        payload = request_json(client, f"{settings.ollama_base_url}/api/tags", "Ollama tags")
    names = {str(row.get("name")) for row in payload.get("models", [])}
    require(settings.ollama_model in names, f"Ollama model {settings.ollama_model!r} is missing")
    print(f"[OK] Ollama and {settings.ollama_model} ready")


def schema_checks(settings: Settings) -> None:
    isdoc_path = Path(settings.isdoc_xsd_path)
    pohoda_path = Path(settings.pohoda_xsd_path)
    require(isdoc_path.is_file(), f"ISDOC XSD is missing: {isdoc_path}")
    require(pohoda_path.is_file(), f"POHODA XSD is missing: {pohoda_path}")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    etree.XMLSchema(etree.parse(str(isdoc_path), parser))
    etree.XMLSchema(etree.parse(str(pohoda_path), parser))
    print("[OK] Local ISDOC 6.0.2 and POHODA XSD resources")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provisioning", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    try:
        database_check()
        application_health()
        keycloak_availability(settings)
        paperless_checks(settings)
        ollama_check(settings)
        schema_checks(settings)
        if args.provisioning:
            keycloak_provisioning(settings)
        target = settings.pohoda_target_ico or "not configured (XML export disabled)"
        print(f"[INFO] POHODA target IČO: {target}")
        print("[OK] Bootstrap runtime smoke passed")
        return 0
    except (
        RuntimeError,
        httpx.HTTPError,
        OSError,
        etree.XMLSchemaError,
        etree.XMLSyntaxError,
    ) as exc:
        print(f"[ERROR] Bootstrap runtime smoke failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
