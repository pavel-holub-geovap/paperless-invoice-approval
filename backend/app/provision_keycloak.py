from __future__ import annotations

import os
import time
from typing import Any

import httpx

from app.config import get_settings


def required(name: str) -> str:
    value = os.getenv(name)
    if not value or value.startswith("change-me"):
        raise RuntimeError(f"{name} must be set to a non-placeholder value")
    return value


def request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> httpx.Response:
    response = client.request(method, path, **kwargs)
    if response.status_code not in {200, 201, 204, 409}:
        response.raise_for_status()
    return response


def upsert_client(
    client: httpx.Client,
    realm: str,
    *,
    client_id: str,
    name: str,
    secret: str,
    redirect_uris: list[str],
    web_origins: list[str],
) -> dict[str, Any]:
    matches = request(
        client,
        "GET",
        f"/admin/realms/{realm}/clients",
        params={"clientId": client_id},
    ).json()
    payload = {
        "clientId": client_id,
        "name": name,
        "enabled": True,
        "protocol": "openid-connect",
        "publicClient": False,
        "secret": secret,
        "standardFlowEnabled": True,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "redirectUris": redirect_uris,
        "webOrigins": web_origins,
        "attributes": {"post.logout.redirect.uris": f"{web_origins[0]}/*"},
    }
    if not matches:
        request(client, "POST", f"/admin/realms/{realm}/clients", json=payload)
        matches = request(
            client,
            "GET",
            f"/admin/realms/{realm}/clients",
            params={"clientId": client_id},
        ).json()
    persisted = matches[0]
    request(
        client,
        "PUT",
        f"/admin/realms/{realm}/clients/{persisted['id']}",
        json={**persisted, **payload},
    )
    return {**persisted, **payload}


def ensure_group_mapper(client: httpx.Client, realm: str, client_uuid: str) -> None:
    mappers = request(
        client,
        "GET",
        f"/admin/realms/{realm}/clients/{client_uuid}/protocol-mappers/models",
    ).json()
    payload = {
        "name": "groups",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-group-membership-mapper",
        "consentRequired": False,
        "config": {
            "full.path": "false",
            "id.token.claim": "true",
            "access.token.claim": "true",
            "userinfo.token.claim": "true",
            "claim.name": "groups",
        },
    }
    existing = next((row for row in mappers if row.get("name") == "groups"), None)
    if existing:
        request(
            client,
            "PUT",
            f"/admin/realms/{realm}/clients/{client_uuid}/protocol-mappers/models/{existing['id']}",
            json={**existing, **payload},
        )
    else:
        request(
            client,
            "POST",
            f"/admin/realms/{realm}/clients/{client_uuid}/protocol-mappers/models",
            json=payload,
        )


def ensure_groups_client_scope(client: httpx.Client, realm: str, client_uuid: str) -> None:
    scopes = request(client, "GET", f"/admin/realms/{realm}/client-scopes").json()
    scope = next((row for row in scopes if row.get("name") == "groups"), None)
    if scope is None:
        request(
            client,
            "POST",
            f"/admin/realms/{realm}/client-scopes",
            json={"name": "groups", "protocol": "openid-connect", "attributes": {}},
        )
        scopes = request(client, "GET", f"/admin/realms/{realm}/client-scopes").json()
        scope = next(row for row in scopes if row.get("name") == "groups")

    mapper_path = f"/admin/realms/{realm}/client-scopes/{scope['id']}/protocol-mappers/models"
    mappers = request(client, "GET", mapper_path).json()
    payload = {
        "name": "groups",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-group-membership-mapper",
        "consentRequired": False,
        "config": {
            "full.path": "false",
            "id.token.claim": "true",
            "access.token.claim": "true",
            "userinfo.token.claim": "true",
            "claim.name": "groups",
        },
    }
    existing = next((row for row in mappers if row.get("name") == "groups"), None)
    if existing:
        request(client, "PUT", f"{mapper_path}/{existing['id']}", json={**existing, **payload})
    else:
        request(client, "POST", mapper_path, json=payload)
    request(
        client,
        "PUT",
        f"/admin/realms/{realm}/clients/{client_uuid}/default-client-scopes/{scope['id']}",
    )


def provision() -> None:
    settings = get_settings()
    admin = required("KEYCLOAK_ADMIN")
    admin_password = required("KEYCLOAK_ADMIN_PASSWORD")
    approval_secret = required("KEYCLOAK_CLIENT_SECRET")
    paperless_client_id = required("PAPERLESS_OIDC_CLIENT_ID")
    paperless_client_secret = required("PAPERLESS_OIDC_CLIENT_SECRET")
    paperless_public_url = required("PAPERLESS_PUBLIC_URL").rstrip("/")

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
        client.headers["Authorization"] = f"Bearer {token_response.json()['access_token']}"

        realm = settings.keycloak_realm
        realm_response = client.get(f"/admin/realms/{realm}")
        realm_payload = {
            "realm": realm,
            "enabled": True,
            "registrationAllowed": False,
            "sslRequired": "none",
            "loginWithEmailAllowed": True,
        }
        if realm_response.status_code == 404:
            request(client, "POST", "/admin/realms", json=realm_payload)
        else:
            request(
                client,
                "PUT",
                f"/admin/realms/{realm}",
                json={**realm_response.json(), **realm_payload},
            )

        approval_client = upsert_client(
            client,
            realm,
            client_id=settings.keycloak_client_id,
            name="Paperless Invoice Approval",
            secret=approval_secret,
            redirect_uris=[f"{settings.app_base_url}/api/auth/callback"],
            web_origins=[settings.app_base_url],
        )
        paperless_client = upsert_client(
            client,
            realm,
            client_id=paperless_client_id,
            name="Paperless-ngx",
            secret=paperless_client_secret,
            redirect_uris=[
                f"{paperless_public_url}/accounts/oidc/keycloak/login/callback/",
            ],
            web_origins=[paperless_public_url],
        )
        ensure_groups_client_scope(client, realm, paperless_client["id"])

        role_representations: dict[str, dict[str, Any]] = {}
        group_representations: dict[str, dict[str, Any]] = {}
        existing_groups = request(client, "GET", f"/admin/realms/{realm}/groups").json()
        for role in ("QUEUE_MANAGER", "APPROVER"):
            response = client.get(f"/admin/realms/{realm}/roles/{role}")
            if response.status_code == 404:
                request(client, "POST", f"/admin/realms/{realm}/roles", json={"name": role})
            role_representations[role] = request(
                client, "GET", f"/admin/realms/{realm}/roles/{role}"
            ).json()

            group = next((row for row in existing_groups if row.get("name") == role), None)
            if group is None:
                request(client, "POST", f"/admin/realms/{realm}/groups", json={"name": role})
                existing_groups = request(client, "GET", f"/admin/realms/{realm}/groups").json()
                group = next(row for row in existing_groups if row.get("name") == role)
            group_representations[role] = group

        users = (
            (
                "queue-manager",
                "queue-manager@example.test",
                "Queue",
                "Manager",
                "TEST_QUEUE_MANAGER_PASSWORD",
                "QUEUE_MANAGER",
            ),
            (
                "approver1",
                "approver1@example.test",
                "Approver",
                "One",
                "TEST_APPROVER_1_PASSWORD",
                "APPROVER",
            ),
            (
                "approver2",
                "approver2@example.test",
                "Approver",
                "Two",
                "TEST_APPROVER_2_PASSWORD",
                "APPROVER",
            ),
            (
                "approver3",
                "approver3@example.test",
                "Approver",
                "Three",
                "TEST_APPROVER_3_PASSWORD",
                "APPROVER",
            ),
        )
        for username, email, first_name, last_name, password_env, role in users:
            password = required(password_env)
            matches = request(
                client,
                "GET",
                f"/admin/realms/{realm}/users",
                params={"username": username, "exact": "true"},
            ).json()
            if not matches:
                request(
                    client,
                    "POST",
                    f"/admin/realms/{realm}/users",
                    json={
                        "username": username,
                        "email": email,
                        "firstName": first_name,
                        "lastName": last_name,
                        "enabled": True,
                        "emailVerified": True,
                        "requiredActions": [],
                    },
                )
                matches = request(
                    client,
                    "GET",
                    f"/admin/realms/{realm}/users",
                    params={"username": username, "exact": "true"},
                ).json()
            user_id = matches[0]["id"]
            request(
                client,
                "PUT",
                f"/admin/realms/{realm}/users/{user_id}",
                json={
                    **matches[0],
                    "email": email,
                    "firstName": first_name,
                    "lastName": last_name,
                    "emailVerified": True,
                    "enabled": True,
                    "requiredActions": [],
                },
            )
            request(
                client,
                "PUT",
                f"/admin/realms/{realm}/users/{user_id}/reset-password",
                json={"type": "password", "temporary": False, "value": password},
            )
            request(
                client,
                "POST",
                f"/admin/realms/{realm}/users/{user_id}/role-mappings/realm",
                json=[role_representations[role]],
            )
            request(
                client,
                "PUT",
                f"/admin/realms/{realm}/users/{user_id}/groups/{group_representations[role]['id']}",
            )

        # The approval client consumes realm roles from the standard realm_access claim.
        ensure_group_mapper(client, realm, approval_client["id"])


if __name__ == "__main__":
    provision()
