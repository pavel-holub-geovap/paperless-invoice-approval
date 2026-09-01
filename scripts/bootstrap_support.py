#!/usr/bin/env python3
"""Pure, testable helpers for the Linux test-stack bootstrap scripts."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit

REQUIRED_VARIABLES = (
    "COMPOSE_PROJECT_NAME",
    "APP_ENV",
    "APP_BASE_URL",
    "APP_SECRET_KEY",
    "DATABASE_URL",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "KEYCLOAK_DB_PASSWORD",
    "PAPERLESS_DB_PASSWORD",
    "REDIS_PASSWORD",
    "KEYCLOAK_BASE_URL",
    "KEYCLOAK_PUBLIC_URL",
    "KEYCLOAK_REALM",
    "KEYCLOAK_CLIENT_ID",
    "KEYCLOAK_CLIENT_SECRET",
    "PAPERLESS_OIDC_CLIENT_ID",
    "PAPERLESS_OIDC_CLIENT_SECRET",
    "KEYCLOAK_ADMIN",
    "KEYCLOAK_ADMIN_PASSWORD",
    "TEST_QUEUE_MANAGER_PASSWORD",
    "TEST_APPROVER_1_PASSWORD",
    "TEST_APPROVER_2_PASSWORD",
    "TEST_APPROVER_3_PASSWORD",
    "PAPERLESS_PUBLIC_URL",
    "PAPERLESS_BASE_URL",
    "PAPERLESS_SECRET_KEY",
    "PAPERLESS_ADMIN_USER",
    "PAPERLESS_ADMIN_PASSWORD",
    "PAPERLESS_ADMIN_MAIL",
    "PAPERLESS_USERMAP_UID",
    "PAPERLESS_USERMAP_GID",
    "PAPERLESS_TIME_ZONE",
    "PAPERLESS_OCR_LANGUAGE",
    "PAPERLESS_OCR_LANGUAGES",
    "PAPERLESS_OCR_MODE",
    "PAPERLESS_API_TOKEN_FILE",
    "PAPERLESS_INBOX_TAG",
    "PAPERLESS_TAG_PROCESSING",
    "PAPERLESS_TAG_QUEUE_REVIEW",
    "PAPERLESS_TAG_APPROVAL",
    "PAPERLESS_TAG_APPROVED",
    "PAPERLESS_TAG_REJECTED",
    "PAPERLESS_TAG_POHODA_READY",
    "PAPERLESS_TAG_EXPORTED",
    "PAPERLESS_TAG_IMPORTED",
    "PAPERLESS_TAG_APPROVED_COPY",
    "PAPERLESS_TAG_DUPLICATE",
    "PAPERLESS_TAG_IGNORED",
    "ISDOC_XSD_PATH",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "OLLAMA_NUM_CTX",
    "OLLAMA_TIMEOUT_SECONDS",
    "OLLAMA_NUM_GPU",
    "OLLAMA_KEEP_ALIVE",
    "AI_EXTRACTION_ENABLED",
    "AI_EXTRACTION_MAX_ATTEMPTS",
    "WORKER_POLL_SECONDS",
    "PAPERLESS_SYNC_SECONDS",
    "EXPORT_ARCHIVE_DIR",
    "POHODA_XSD_PATH",
    "POHODA_XSD_BUNDLE_VERSION",
    "POHODA_XML_ENCODING",
    "POHODA_GENERATOR_VERSION",
    "ALLOCATION_TOLERANCE",
    "APPROVAL_HTTP_PORT",
    "PAPERLESS_HTTP_PORT",
    "KEYCLOAK_HTTP_PORT",
)

SECRET_VARIABLES = {
    "APP_SECRET_KEY",
    "POSTGRES_PASSWORD",
    "KEYCLOAK_DB_PASSWORD",
    "PAPERLESS_DB_PASSWORD",
    "REDIS_PASSWORD",
    "KEYCLOAK_CLIENT_SECRET",
    "PAPERLESS_OIDC_CLIENT_SECRET",
    "KEYCLOAK_ADMIN_PASSWORD",
    "TEST_QUEUE_MANAGER_PASSWORD",
    "TEST_APPROVER_1_PASSWORD",
    "TEST_APPROVER_2_PASSWORD",
    "TEST_APPROVER_3_PASSWORD",
    "PAPERLESS_SECRET_KEY",
    "PAPERLESS_ADMIN_PASSWORD",
    "PAPERLESS_API_TOKEN",
}

URL_VARIABLES = (
    "APP_BASE_URL",
    "KEYCLOAK_BASE_URL",
    "KEYCLOAK_PUBLIC_URL",
    "PAPERLESS_PUBLIC_URL",
    "PAPERLESS_BASE_URL",
    "OLLAMA_BASE_URL",
)

PORT_VARIABLES = (
    "APPROVAL_HTTP_PORT",
    "PAPERLESS_HTTP_PORT",
    "KEYCLOAK_HTTP_PORT",
)

DEFAULT_VALUES = {
    "PAPERLESS_TAG_APPROVED_COPY": "Approval - schválená kopie",
    "ISDOC_XSD_PATH": "/app/isdoc-xsd/isdoc-invoice-6.0.2.xsd",
    "POHODA_XSD_BUNDLE_VERSION": "2025-10-16",
    "POHODA_XML_ENCODING": "Windows-1250",
    "APPROVAL_HTTP_PORT": "80",
    "PAPERLESS_HTTP_PORT": "8000",
    "KEYCLOAK_HTTP_PORT": "8081",
}


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"Invalid .env line {number}: expected NAME=value")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"Invalid .env key on line {number}: {key!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("must not contain credentials")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("must not contain a path, query, or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("contains an invalid port") from exc
    del port
    return value.strip().rstrip("/")


def redact_value(key: str, value: str) -> str:
    if key in SECRET_VARIABLES or any(
        marker in key for marker in ("PASSWORD", "SECRET", "TOKEN")
    ):
        return "<redacted>" if value else "<empty>"
    return value


def select_compose_command(has_legacy: bool, has_plugin: bool) -> tuple[str, ...]:
    if has_legacy:
        return ("docker-compose",)
    if has_plugin:
        return ("docker", "compose")
    raise ValueError("Docker Compose v2 is not available")


def model_is_present(list_output: str, requested: str) -> bool:
    wanted = requested.strip()
    return any(line.split()[0] == wanted for line in list_output.splitlines()[1:] if line.split())


def alembic_revisions_match(current_output: str, heads_output: str) -> bool:
    current = current_output.strip().split(maxsplit=1)[0] if current_output.strip() else ""
    head = heads_output.strip().split(maxsplit=1)[0] if heads_output.strip() else ""
    return bool(current and head and current == head)


def _effective_port(url: str) -> int:
    parsed = urlsplit(normalize_url(url))
    return parsed.port or (443 if parsed.scheme == "https" else 80)


def validate_environment(values: dict[str, str]) -> tuple[list[str], list[str]]:
    values = dict(values)
    for key, default in DEFAULT_VALUES.items():
        values[key] = values.get(key) or default
    errors: list[str] = []
    warnings: list[str] = []
    for key in REQUIRED_VARIABLES:
        if not values.get(key, "").strip():
            errors.append(f"{key} is required")

    for key, value in values.items():
        lowered = value.lower()
        if value and any(marker in lowered for marker in ("change-me", "replace-me")):
            errors.append(f"{key} still contains a placeholder")

    for key in URL_VARIABLES:
        value = values.get(key, "")
        if not value:
            continue
        try:
            normalize_url(value)
        except ValueError as exc:
            errors.append(f"{key} {exc}")

    project = values.get("COMPOSE_PROJECT_NAME", "")
    if project and not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
        errors.append("COMPOSE_PROJECT_NAME must use lowercase letters, digits, '_' or '-'")
    if values.get("APP_ENV", "").lower() == "production":
        errors.append("bootstrap-test.sh refuses APP_ENV=production")

    parsed_ports: dict[str, int] = {}
    for key in PORT_VARIABLES:
        raw = values.get(key, "")
        if not raw:
            continue
        try:
            port = int(raw)
        except ValueError:
            errors.append(f"{key} must be an integer")
            continue
        if not 1 <= port <= 65535:
            errors.append(f"{key} must be between 1 and 65535")
        parsed_ports[key] = port
    if len(set(parsed_ports.values())) != len(parsed_ports):
        errors.append("Published HTTP ports must be distinct")

    url_port_pairs = (
        ("APP_BASE_URL", "APPROVAL_HTTP_PORT"),
        ("PAPERLESS_PUBLIC_URL", "PAPERLESS_HTTP_PORT"),
        ("KEYCLOAK_PUBLIC_URL", "KEYCLOAK_HTTP_PORT"),
    )
    for url_key, port_key in url_port_pairs:
        if values.get(url_key) and port_key in parsed_ports:
            try:
                actual_port = _effective_port(values[url_key])
            except ValueError:
                continue
            if actual_port != parsed_ports[port_key]:
                errors.append(f"{url_key} does not use {port_key}={parsed_ports[port_key]}")

    internal_expected = {
        "KEYCLOAK_BASE_URL": "keycloak",
        "PAPERLESS_BASE_URL": "paperless",
        "OLLAMA_BASE_URL": "ollama",
    }
    for key, hostname in internal_expected.items():
        value = values.get(key, "")
        if value and urlsplit(value).hostname != hostname:
            errors.append(f"{key} must address the isolated Compose service {hostname!r}")
    database_url = values.get("DATABASE_URL", "")
    if database_url and not re.match(
        r"^postgresql\+psycopg://approval:[^@]+@postgres:5432/approval$",
        database_url,
    ):
        errors.append("DATABASE_URL must use the dedicated approval user/database on postgres")
    expected_database_url = (
        "postgresql+psycopg://approval:"
        f"{values.get('POSTGRES_PASSWORD', '')}@postgres:5432/approval"
    )
    if database_url and values.get("POSTGRES_PASSWORD") and database_url != expected_database_url:
        errors.append("DATABASE_URL password must match POSTGRES_PASSWORD")

    for key in SECRET_VARIABLES - {"PAPERLESS_API_TOKEN"}:
        value = values.get(key, "")
        minimum = 32 if key in {"APP_SECRET_KEY", "PAPERLESS_SECRET_KEY"} else 12
        if value and len(value) < minimum:
            errors.append(f"{key} must contain at least {minimum} characters")
    populated_secrets = [
        values[key] for key in SECRET_VARIABLES if values.get(key, "") and key != "PAPERLESS_API_TOKEN"
    ]
    if len(populated_secrets) != len(set(populated_secrets)):
        errors.append("Every configured test secret must be independent")

    target_ico = values.get("POHODA_TARGET_ICO", "").replace(" ", "")
    if target_ico and not re.fullmatch(r"\d{8}", target_ico):
        errors.append("POHODA_TARGET_ICO must contain exactly 8 digits when configured")
    if not target_ico:
        warnings.append("POHODA_TARGET_ICO is empty; generated POHODA XML export is disabled")
    if values.get("OLLAMA_MODEL") != "qwen3:8b":
        warnings.append("OLLAMA_MODEL differs from the tested qwen3:8b baseline")
    if values.get("PAPERLESS_TAG_APPROVED_COPY") == values.get("PAPERLESS_INBOX_TAG"):
        errors.append("PAPERLESS_TAG_APPROVED_COPY must differ from PAPERLESS_INBOX_TAG")
    return errors, warnings


def generate_env(template: Path, destination: Path, host: str) -> None:
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing {destination}")
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", host) or "/" in host:
        raise ValueError("Host must be a hostname or IP address without a URL scheme")
    values = parse_env(template)
    approval_port = int(values.get("APPROVAL_HTTP_PORT", "80"))
    paperless_port = int(values.get("PAPERLESS_HTTP_PORT", "8000"))
    keycloak_port = int(values.get("KEYCLOAK_HTTP_PORT", "8081"))

    def public_url(port: int) -> str:
        return f"http://{host}" if port == 80 else f"http://{host}:{port}"

    replacements = {
        "APP_BASE_URL": public_url(approval_port),
        "PAPERLESS_PUBLIC_URL": public_url(paperless_port),
        "KEYCLOAK_PUBLIC_URL": public_url(keycloak_port),
        "PAPERLESS_USERMAP_UID": str(getattr(os, "getuid", lambda: 1000)()),
        "PAPERLESS_USERMAP_GID": str(getattr(os, "getgid", lambda: 1000)()),
    }
    for key in SECRET_VARIABLES - {"PAPERLESS_API_TOKEN"}:
        replacements[key] = secrets.token_urlsafe(32 if key in {"APP_SECRET_KEY", "PAPERLESS_SECRET_KEY"} else 20)
    replacements["PAPERLESS_SECRET_KEY"] = secrets.token_urlsafe(48)
    replacements["DATABASE_URL"] = (
        "postgresql+psycopg://approval:"
        f"{replacements['POSTGRES_PASSWORD']}@postgres:5432/approval"
    )
    rendered: list[str] = []
    for raw in template.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in raw:
            key = raw.split("=", 1)[0].strip()
            if key in replacements:
                raw = f"{key}={replacements[key]}"
        rendered.append(raw)
    destination.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    destination.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate-env")
    validate.add_argument("path", type=Path)
    get = sub.add_parser("get")
    get.add_argument("path", type=Path)
    get.add_argument("key")
    generate = sub.add_parser("generate-env")
    generate.add_argument("template", type=Path)
    generate.add_argument("destination", type=Path)
    generate.add_argument("host")
    args = parser.parse_args()
    try:
        if args.command == "validate-env":
            values = parse_env(args.path)
            errors, warnings = validate_environment(values)
            for warning in warnings:
                print(f"[WARN] {warning}")
            for error in errors:
                print(f"[ERROR] {error}", file=sys.stderr)
            if errors:
                return 1
            print(f"[OK] Environment validation ({len(values)} variables; secrets redacted)")
            return 0
        if args.command == "get":
            values = parse_env(args.path)
            print(values.get(args.key) or DEFAULT_VALUES.get(args.key, ""))
            return 0
        if args.command == "generate-env":
            generate_env(args.template, args.destination, args.host)
            print(f"Created {args.destination} with mode 0600; secrets were not printed.")
            return 0
    except (OSError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
