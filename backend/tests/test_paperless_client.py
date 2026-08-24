from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.integrations.paperless import PaperlessClient, PaperlessError, PaperlessNotFound


@pytest.mark.asyncio
async def test_document_metadata_and_pdf_are_loaded_only_over_rest() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Token test-token"
        if request.url.path == "/api/documents/1/":
            return httpx.Response(
                200,
                json={
                    "id": 1,
                    "title": "Invoice",
                    "content": "OCR content",
                    "added": "2026-08-23T17:42:57+02:00",
                    "tags": [5],
                    "correspondent": 3,
                    "original_file_name": "invoice.pdf",
                },
            )
        if request.url.path == "/api/tags/5/":
            return httpx.Response(200, json={"id": 5, "name": "Přijatá faktura"})
        if request.url.path == "/api/correspondents/3/":
            return httpx.Response(200, json={"id": 3, "name": "Supplier"})
        if request.url.path == "/api/documents/1/download/":
            return httpx.Response(200, content=b"%PDF-1.7 test", headers={"content-type": "application/pdf"})
        return httpx.Response(404, content=json.dumps({"detail": "not found"}))

    settings = Settings(
        paperless_base_url="http://paperless.test",
        paperless_api_token=SecretStr("test-token"),
        paperless_api_token_file=None,
    )
    client = PaperlessClient(settings, transport=httpx.MockTransport(handler))
    try:
        document = await client.get_document(1)
        pdf = await client.download_pdf(1)
    finally:
        await client.close()

    assert document.title == "Invoice"
    assert document.tag_names == ("Přijatá faktura",)
    assert document.correspondent_name == "Supplier"
    assert document.original_filename == "invoice.pdf"
    assert pdf.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_only_http_404_is_classified_as_missing_source() -> None:
    settings = Settings(
        paperless_base_url="http://paperless.test",
        paperless_api_token=SecretStr("test-token"),
        paperless_api_token_file=None,
        external_retry_attempts=1,
    )

    not_found = PaperlessClient(
        settings,
        transport=httpx.MockTransport(lambda _: httpx.Response(404, json={"detail": "not found"})),
    )
    unavailable = PaperlessClient(
        settings,
        transport=httpx.MockTransport(lambda _: httpx.Response(503, json={"detail": "down"})),
    )
    try:
        with pytest.raises(PaperlessNotFound):
            await not_found.get_document(99)
        with pytest.raises(PaperlessError) as error:
            await unavailable.get_document(99)
        assert not isinstance(error.value, PaperlessNotFound)
    finally:
        await not_found.close()
        await unavailable.close()
