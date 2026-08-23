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

    paperless_base_url: str = "http://paperless:8000"
    paperless_api_token: SecretStr | None = None
    paperless_api_token_file: Path | None = None
    paperless_inbox_tag: str = "Přijatá faktura"
    paperless_tag_processing: str = "AI zpracování"
    paperless_tag_queue_review: str = "Kontrola správce"
    paperless_tag_approval: str = "Ke schválení"
    paperless_tag_approved: str = "Schváleno"
    paperless_tag_rejected: str = "Zamítnuto"
    paperless_tag_pohoda_ready: str = "Připraveno pro Pohodu"
    paperless_tag_exported: str = "Exportováno"
    paperless_tag_imported: str = "Importováno do Pohody"

    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen3:4b"
    ollama_num_ctx: int = 4096
    ollama_timeout_seconds: int = 300
    ollama_num_gpu: int = 0
    ollama_keep_alive: str = "5m"
    ai_extraction_enabled: bool = True
    ai_extraction_max_attempts: int = 3
    worker_poll_seconds: int = 3
    paperless_sync_seconds: int = 30
    export_archive_dir: Path = Path("./exports")
    pohoda_xsd_path: Path = Path("../schemas/pohoda/2025-10-16/data.xsd")
    pohoda_xsd_bundle_version: str = "2025-10-16"
    pohoda_xml_encoding: str = "Windows-1250"
    pohoda_generator_version: str = "pohoda-received-invoice.v1"
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

    def read_paperless_api_token(self) -> str:
        if self.paperless_api_token_file is not None:
            try:
                token = self.paperless_api_token_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise RuntimeError("Paperless API token file is not readable") from exc
            if token:
                return token
        if self.paperless_api_token is not None:
            token = self.paperless_api_token.get_secret_value().strip()
            if token and token != "change-me":
                return token
        raise RuntimeError("Paperless API token is not configured")


@lru_cache
def get_settings() -> Settings:
    return Settings()
