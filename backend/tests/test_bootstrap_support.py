from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "bootstrap_support", ROOT / "scripts" / "bootstrap_support.py"
)
assert SPEC and SPEC.loader
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def valid_environment() -> dict[str, str]:
    values = {key: f"value-{key.lower()}-1234567890" for key in bootstrap.REQUIRED_VARIABLES}
    values.update(
        {
            "COMPOSE_PROJECT_NAME": "paperless-invoice-test",
            "APP_ENV": "development",
            "APP_BASE_URL": "http://test.example:18080/",
            "KEYCLOAK_BASE_URL": "http://keycloak:8080",
            "KEYCLOAK_PUBLIC_URL": "http://test.example:18081",
            "PAPERLESS_PUBLIC_URL": "http://test.example:18000",
            "PAPERLESS_BASE_URL": "http://paperless:8000",
            "OLLAMA_BASE_URL": "http://ollama:11434",
            "DATABASE_URL": (
                "postgresql+psycopg://approval:postgres-password-123"
                "@postgres:5432/approval"
            ),
            "APP_SECRET_KEY": "a" * 40,
            "PAPERLESS_SECRET_KEY": "b" * 64,
            "POSTGRES_PASSWORD": "postgres-password-123",
            "KEYCLOAK_DB_PASSWORD": "keycloak-db-password-123",
            "PAPERLESS_DB_PASSWORD": "paperless-db-password-123",
            "REDIS_PASSWORD": "redis-password-123",
            "KEYCLOAK_CLIENT_SECRET": "approval-client-secret-123",
            "PAPERLESS_OIDC_CLIENT_SECRET": "paperless-client-secret-123",
            "KEYCLOAK_ADMIN_PASSWORD": "admin-password-123",
            "TEST_QUEUE_MANAGER_PASSWORD": "manager-password-123",
            "TEST_APPROVER_1_PASSWORD": "approver-one-password-123",
            "TEST_APPROVER_2_PASSWORD": "approver-two-password-123",
            "TEST_APPROVER_3_PASSWORD": "approver-three-password-123",
            "PAPERLESS_ADMIN_PASSWORD": "paperless-admin-password-123",
            "PAPERLESS_API_TOKEN": "",
            "PAPERLESS_INBOX_TAG": "Přijatá faktura",
            "PAPERLESS_TAG_APPROVED_COPY": "Approval - schválená kopie",
            "OLLAMA_MODEL": "qwen3:8b",
            "POHODA_TARGET_ICO": "",
            "APPROVAL_HTTP_PORT": "18080",
            "PAPERLESS_HTTP_PORT": "18000",
            "KEYCLOAK_HTTP_PORT": "18081",
        }
    )
    return values


def test_env_parser_and_secret_redaction(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# comment\nAPP_ENV=development\nexport APP_SECRET_KEY='do-not-print-me'\n",
        encoding="utf-8",
    )
    assert bootstrap.parse_env(path) == {
        "APP_ENV": "development",
        "APP_SECRET_KEY": "do-not-print-me",
    }
    assert bootstrap.redact_value("APP_SECRET_KEY", "do-not-print-me") == "<redacted>"
    assert bootstrap.redact_value("APP_ENV", "development") == "development"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://server.example/", "http://server.example"),
        ("https://server.example:8443", "https://server.example:8443"),
    ],
)
def test_url_normalization(value: str, expected: str) -> None:
    assert bootstrap.normalize_url(value) == expected


@pytest.mark.parametrize(
    "value",
    ["server.example", "ftp://server.example", "http://user:pass@host", "http://host/path"],
)
def test_url_normalization_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValueError):
        bootstrap.normalize_url(value)


def test_environment_validation_covers_required_values_and_public_ports() -> None:
    values = valid_environment()
    errors, warnings = bootstrap.validate_environment(values)
    assert errors == []
    assert warnings == ["POHODA_TARGET_ICO is empty; generated POHODA XML export is disabled"]

    values["APP_SECRET_KEY"] = "change-me"
    values["PAPERLESS_TAG_APPROVED_COPY"] = values["PAPERLESS_INBOX_TAG"]
    values["KEYCLOAK_PUBLIC_URL"] = "http://test.example:9999"
    errors, _ = bootstrap.validate_environment(values)
    assert any("placeholder" in error for error in errors)
    assert any("must differ" in error for error in errors)
    assert any("KEYCLOAK_HTTP_PORT" in error for error in errors)


def test_compose_detection_prefers_legacy_v2_binary_then_plugin() -> None:
    assert bootstrap.select_compose_command(True, True) == ("docker-compose",)
    assert bootstrap.select_compose_command(False, True) == ("docker", "compose")
    with pytest.raises(ValueError):
        bootstrap.select_compose_command(False, False)


def test_model_and_alembic_readiness_detection() -> None:
    model_list = "NAME ID SIZE\nqwen3:8b abc 5 GB\n"
    assert bootstrap.model_is_present(model_list, "qwen3:8b")
    assert not bootstrap.model_is_present(model_list, "qwen3:14b")
    assert bootstrap.alembic_revisions_match("0010 (head)\n", "0010 (head)\n")
    assert not bootstrap.alembic_revisions_match("0009\n", "0010 (head)\n")


def test_generate_env_refuses_overwrite_and_keeps_secrets_out_of_output(
    tmp_path: Path,
) -> None:
    template = ROOT / ".env.example"
    destination = tmp_path / ".env"
    bootstrap.generate_env(template, destination, "bootstrap.example.test")
    generated = bootstrap.parse_env(destination)
    assert generated["APP_BASE_URL"].startswith("http://bootstrap.example.test")
    assert not any("change-me" in value for value in generated.values())
    assert generated["DATABASE_URL"].startswith("postgresql+psycopg://approval:")
    with pytest.raises(FileExistsError):
        bootstrap.generate_env(template, destination, "bootstrap.example.test")
