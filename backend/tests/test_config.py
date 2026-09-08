from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import SecretStr

from app.auth import authorization_url
from app.config import Settings
from app.provision_keycloak import oidc_client_urls


def test_paperless_token_is_read_from_runtime_file(tmp_path: Path) -> None:
    token_file = tmp_path / "api-token"
    token_file.write_text("runtime-token\n", encoding="utf-8")
    settings = Settings(paperless_api_token=None, paperless_api_token_file=token_file)

    assert settings.read_paperless_api_token() == "runtime-token"


def test_manual_paperless_token_is_supported_without_file() -> None:
    settings = Settings(
        paperless_api_token=SecretStr("manual-token"),
        paperless_api_token_file=None,
    )

    assert settings.read_paperless_api_token() == "manual-token"


def test_missing_paperless_token_fails_closed() -> None:
    settings = Settings(paperless_api_token=None, paperless_api_token_file=None)

    with pytest.raises(RuntimeError, match="not configured"):
        settings.read_paperless_api_token()


def test_public_oidc_urls_use_custom_host_while_internal_urls_keep_compose_dns() -> None:
    settings = Settings(
        app_base_url="http://10.101.3.85",
        keycloak_base_url="http://keycloak:8080",
        keycloak_public_url="http://10.101.3.85:8081",
        paperless_base_url="http://paperless:8000",
        ollama_base_url="http://ollama:11434",
    )
    callback = f"{settings.app_base_url}/api/auth/callback"
    login_url = authorization_url(settings, callback, "state", "nonce")
    parsed_login = urlsplit(login_url)
    query = parse_qs(parsed_login.query)
    clients = oidc_client_urls(settings.app_base_url, "http://10.101.3.85:8000")

    assert parsed_login.netloc == "10.101.3.85:8081"
    assert query["redirect_uri"] == ["http://10.101.3.85/api/auth/callback"]
    assert clients["approval"] == {
        "redirect_uris": ["http://10.101.3.85/api/auth/callback"],
        "web_origins": ["http://10.101.3.85"],
    }
    assert clients["paperless"] == {
        "redirect_uris": [
            "http://10.101.3.85:8000/accounts/oidc/keycloak/login/callback/"
        ],
        "web_origins": ["http://10.101.3.85:8000"],
    }
    assert settings.keycloak_base_url == "http://keycloak:8080"
    assert settings.paperless_base_url == "http://paperless:8000"
    assert settings.ollama_base_url == "http://ollama:11434"
    assert "172.30.172.167" not in login_url
    assert "localhost" not in login_url
