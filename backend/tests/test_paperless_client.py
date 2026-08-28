from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.integrations.paperless import (
    PaperlessClient,
    PaperlessError,
    PaperlessNotFound,
    PaperlessUnavailable,
)


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
async def test_fulltext_search_uses_paginated_document_query_without_n_plus_one() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/api/documents/"
        if len(requests) == 1:
            assert request.url.params["text"] == "unikátní OCR fráze"
            assert "query" not in request.url.params
            assert request.url.params["page_size"] == "100"
            return httpx.Response(
                200,
                json={
                    "results": [{"id": 10}, {"id": 11}],
                    "next": "http://paperless.test/api/documents/?page=2&text=unik%C3%A1tn%C3%AD",
                },
            )
        return httpx.Response(200, json={"results": [{"id": 12}], "next": None})

    settings = Settings(
        paperless_base_url="http://paperless.test",
        paperless_api_token=SecretStr("test-token"),
        paperless_api_token_file=None,
    )
    client = PaperlessClient(settings, transport=httpx.MockTransport(handler))
    try:
        result = await client.search_document_ids("unikátní OCR fráze")
    finally:
        await client.close()

    assert result == {10, 11, 12}
    assert len(requests) == 2


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


@pytest.mark.asyncio
async def test_pdf_upload_uses_official_task_endpoint_and_configured_tag() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags/":
            return httpx.Response(200, json={"results": [{"id": 17, "name": "Přijatá faktura"}]})
        if request.url.path == "/api/documents/post_document/":
            seen["content_type"] = request.headers["content-type"]
            seen["body"] = request.content
            return httpx.Response(200, json="task-upload-1")
        raise AssertionError(request.url)

    settings = Settings(
        paperless_base_url="http://paperless.test",
        paperless_api_token=SecretStr("test-token"),
        paperless_api_token_file=None,
    )
    client = PaperlessClient(settings, transport=httpx.MockTransport(handler))
    try:
        tag_id = await client.resolve_tag_id(settings.paperless_inbox_tag)
        task_id = await client.post_document(
            b"%PDF-1.7 test",
            filename="safe.pdf",
            title="safe",
            tag_id=tag_id,
        )
    finally:
        await client.close()

    assert task_id == "task-upload-1"
    assert "multipart/form-data" in str(seen["content_type"])
    body = bytes(seen["body"])
    assert b'name="tags"' in body and b"17" in body
    assert b'name="document"; filename="safe.pdf"' in body


@pytest.mark.asyncio
async def test_connect_failure_before_upload_is_retryable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    settings = Settings(
        paperless_base_url="http://paperless.test",
        paperless_api_token=SecretStr("test-token"),
        paperless_api_token_file=None,
    )
    client = PaperlessClient(settings, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(PaperlessUnavailable):
            await client.post_document(
                b"%PDF-1.7 test",
                filename="safe.pdf",
                title="safe",
                tag_id=17,
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_paperless_task_returns_created_document_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["task_id"] == "task-upload-1"
        return httpx.Response(
            200,
            json={
                "count": 1,
                "results": [
                    {
                        "task_id": "task-upload-1",
                        "status": "success",
                        "related_document_ids": [731],
                        "result_data": {"message": "ok"},
                    }
                ],
            },
        )

    settings = Settings(
        paperless_base_url="http://paperless.test",
        paperless_api_token=SecretStr("test-token"),
        paperless_api_token_file=None,
    )
    client = PaperlessClient(settings, transport=httpx.MockTransport(handler))
    try:
        task = await client.get_task("task-upload-1")
    finally:
        await client.close()

    assert task is not None
    assert task.status == "success"
    assert task.related_document_ids == (731,)
