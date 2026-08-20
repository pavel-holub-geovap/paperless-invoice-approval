from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: str = "development"
    app_base_url: str = "http://localhost:8080"
    app_secret_key: SecretStr = SecretStr("development-only-change-me")
    database_url: str = "sqlite:///./paperless_invoice.db"

    keycloak_base_url: str = "http://keycloak:8080"
    keycloak_public_url: str = "http://localhost:8081"
    keycloak_realm: str = "paperless-invoice"
    keycloak_client_id: str = "paperless-invoice-app"
    keycloak_client_secret: SecretStr = SecretStr("change-me")

    paperless_base_url: str = "https://paperless.example.invalid"
    paperless_api_token: SecretStr = SecretStr("change-me")
    paperless_inbox_tag: str = "invoice-received"
    paperless_tag_processing: str = "invoice-processing"
    paperless_tag_queue_review: str = "invoice-queue-review"
    paperless_tag_approval: str = "invoice-approval"
    paperless_tag_approved: str = "invoice-approved"
    paperless_tag_rejected: str = "invoice-rejected"
    paperless_tag_pohoda_ready: str = "pohoda-ready"
    paperless_tag_exported: str = "pohoda-exported"
    paperless_tag_imported: str = "pohoda-imported"

    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen3:4b"
    ollama_num_ctx: int = 4096
    ollama_timeout_seconds: int = 180
    worker_poll_seconds: int = 3
    export_archive_dir: Path = Path("./exports")
    pohoda_xsd_path: Path = Path("../fixtures/pohoda/data.xsd")
    allocation_tolerance: str = "0.01"
    session_ttl_seconds: int = 28800
    external_retry_attempts: int = 3

    @field_validator("paperless_base_url", "ollama_base_url", "keycloak_base_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def oidc_issuer_internal(self) -> str:
        return f"{self.keycloak_base_url}/realms/{self.keycloak_realm}"

    @property
    def oidc_issuer_public(self) -> str:
        return f"{self.keycloak_public_url}/realms/{self.keycloak_realm}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
