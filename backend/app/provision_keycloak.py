from __future__ import annotations

import os
import time
from typing import Any

import httpx

from app.config import get_settings
from app.db import SessionLocal
from app.models import UserIdentity


def required(name: str) -> str:
    value = os.getenv(name)
    if not value or value == "change-me":
        raise RuntimeError(f"{name} must be set to a non-placeholder value")
    return value


def request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> httpx.Response:
    response = client.request(method, path, **kwargs)
    if response.status_code not in {200, 201, 204, 409}:
        response.raise_for_status()
    return response


def provision() -> None:
    settings = get_settings()
    admin = required("KEYCLOAK_ADMIN")
    admin_password = required("KEYCLOAK_ADMIN_PASSWORD")
    client_secret = settings.keycloak_client_secret.get_secret_value()
    if client_secret == "change-me":
        raise RuntimeError("KEYCLOAK_CLIENT_SECRET must be changed")

    with httpx.Client(base_url=settings.keycloak_base_url, timeout=20) as client:
        for attempt in range(30):
            try:
                token_response = client.post(
                    "/realms/master/protocol/openid-connect/token",
                    data={
                        "grant_type": "password",
                        "client_id": "admin-cli",
                        "username": admin,
                        "password": admin_password,
                    },
                )
                token_response.raise_for_status()
                break
            except httpx.HTTPError:
                if attempt == 29:
                    raise
                time.sleep(2)
        token = token_response.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"

        realm = settings.keycloak_realm
        if client.get(f"/admin/realms/{realm}").status_code == 404:
            request(client, "POST", "/admin/realms", json={"realm": realm, "enabled": True, "registrationAllowed": False})

        clients = request(
            client,
            "GET",
            f"/admin/realms/{realm}/clients",
            params={"clientId": settings.keycloak_client_id},
        ).json()
        client_payload = {
            "clientId": settings.keycloak_client_id,
            "name": "Paperless Invoice Approval",
            "enabled": True,
            "protocol": "openid-connect",
            "publicClient": False,
            "secret": client_secret,
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": False,
            "redirectUris": [f"{settings.app_base_url}/api/auth/callback"],
            "webOrigins": [settings.app_base_url],
        }
        if not clients:
            request(client, "POST", f"/admin/realms/{realm}/clients", json=client_payload)
            clients = request(
                client,
                "GET",
                f"/admin/realms/{realm}/clients",
                params={"clientId": settings.keycloak_client_id},
            ).json()
        client_uuid = clients[0]["id"]
        request(client, "PUT", f"/admin/realms/{realm}/clients/{client_uuid}", json={**clients[0], **client_payload})

        role_representations: dict[str, dict[str, Any]] = {}
        for role in ("QUEUE_MANAGER", "APPROVER"):
            response = client.get(f"/admin/realms/{realm}/clients/{client_uuid}/roles/{role}")
            if response.status_code == 404:
                request(client, "POST", f"/admin/realms/{realm}/clients/{client_uuid}/roles", json={"name": role})
            role_representations[role] = request(
                client, "GET", f"/admin/realms/{realm}/clients/{client_uuid}/roles/{role}"
            ).json()

        users = (
            ("queue-manager", "TEST_QUEUE_MANAGER_PASSWORD", "QUEUE_MANAGER"),
            ("approver1", "TEST_APPROVER_1_PASSWORD", "APPROVER"),
            ("approver2", "TEST_APPROVER_2_PASSWORD", "APPROVER"),
            ("approver3", "TEST_APPROVER_3_PASSWORD", "APPROVER"),
        )
        for username, password_env, role in users:
            password = required(password_env)
            matches = request(client, "GET", f"/admin/realms/{realm}/users", params={"username": username, "exact": "true"}).json()
            if not matches:
                request(
                    client,
                    "POST",
                    f"/admin/realms/{realm}/users",
                    json={"username": username, "enabled": True, "emailVerified": True},
                )
                matches = request(client, "GET", f"/admin/realms/{realm}/users", params={"username": username, "exact": "true"}).json()
            user_id = matches[0]["id"]
            request(
                client,
                "PUT",
                f"/admin/realms/{realm}/users/{user_id}/reset-password",
                json={"type": "password", "temporary": False, "value": password},
            )
            request(
                client,
                "POST",
                f"/admin/realms/{realm}/users/{user_id}/role-mappings/clients/{client_uuid}",
                json=[role_representations[role]],
            )
            with SessionLocal.begin() as db:
                identity = db.get(UserIdentity, user_id)
                if identity is None:
                    db.add(UserIdentity(subject=user_id, username=username, roles=[role]))
                else:
                    identity.username = username
                    identity.roles = sorted(set(identity.roles) | {role})


if __name__ == "__main__":
    provision()
