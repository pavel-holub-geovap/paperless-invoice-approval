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
            "APP_HOST_PORT": "18080",
            "PAPERLESS_HOST_PORT": "18000",
            "KEYCLOAK_HOST_PORT": "18081",
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
    assert not any("KEYCLOAK_HOST_PORT" in error for error in errors)
    assert any(
        "KEYCLOAK_HOST_PORT" in warning
        for warning in bootstrap.validate_environment(values)[1]
    )


def test_environment_validation_accepts_documented_defaults_for_legacy_env() -> None:
    values = valid_environment()
    values["APP_BASE_URL"] = "http://test.example"
    values["PAPERLESS_PUBLIC_URL"] = "http://test.example:8000"
    values["KEYCLOAK_PUBLIC_URL"] = "http://test.example:8081"
    for key in bootstrap.DEFAULT_VALUES:
        values.pop(key, None)
    errors, warnings = bootstrap.validate_environment(values)
    assert errors == []
    assert warnings == ["POHODA_TARGET_ICO is empty; generated POHODA XML export is disabled"]


@pytest.mark.parametrize("version", ["v2.35.1", "Docker Compose version v5.1.3"])
def test_compose_detection_accepts_plugin_major_two_or_newer(version: str) -> None:
    assert bootstrap.select_compose_command(version, "") == ("docker", "compose")


def test_compose_detection_prefers_v5_plugin_over_v1_standalone() -> None:
    assert bootstrap.select_compose_command("v5.1.3", "1.29.2") == (
        "docker",
        "compose",
    )


def test_compose_detection_falls_back_to_standalone_v2() -> None:
    assert bootstrap.select_compose_command("", "Docker Compose version v2.40.0") == (
        "docker-compose",
    )


def test_compose_detection_rejects_standalone_v1_only() -> None:
    with pytest.raises(ValueError, match=r">= 2"):
        bootstrap.select_compose_command("", "docker-compose version 1.29.2")


def test_legacy_port_names_are_mapped_with_warnings() -> None:
    values = valid_environment()
    for current, legacy in bootstrap.LEGACY_PORT_ALIASES.items():
        values[legacy] = values.pop(current)
    errors, warnings = bootstrap.validate_environment(values)
    assert errors == []
    assert sum("deprecated" in warning for warning in warnings) == 3


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


def test_generate_env_can_create_an_isolated_shared_host_configuration(
    tmp_path: Path,
) -> None:
    destination = tmp_path / ".env"
    bootstrap.generate_env(
        ROOT / ".env.example",
        destination,
        "shared.example.test",
        project_name="paperless-invoice-test2",
        app_host_port=28080,
        paperless_host_port=28000,
        keycloak_host_port=28081,
    )
    generated = bootstrap.parse_env(destination)
    assert generated["COMPOSE_PROJECT_NAME"] == "paperless-invoice-test2"
    assert generated["APP_HOST_PORT"] == "28080"
    assert generated["PAPERLESS_PUBLIC_URL"].endswith(":28000")
    assert generated["KEYCLOAK_PUBLIC_URL"].endswith(":28081")


def test_rendered_compose_model_port_validation() -> None:
    model = {
        "name": "paperless-invoice-test2",
        "services": {
            "backend": {},
            "reverse-proxy": {
                "ports": [
                    {"target": 80, "published": "28080"},
                    {"target": 8000, "published": "28000"},
                    {"target": 8081, "published": "28081"},
                ]
            },
        },
    }
    bootstrap.validate_compose_model_ports(
        model,
        project_name="paperless-invoice-test2",
        app_host_port=28080,
        paperless_host_port=28000,
        keycloak_host_port=28081,
    )
    model["services"]["reverse-proxy"]["ports"][2]["published"] = "8081"
    with pytest.raises(ValueError, match="Rendered host ports"):
        bootstrap.validate_compose_model_ports(
            model,
            project_name="paperless-invoice-test2",
            app_host_port=28080,
            paperless_host_port=28000,
            keycloak_host_port=28081,
        )
