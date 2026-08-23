from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config import Settings


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
