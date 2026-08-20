from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings


class PaperlessError(RuntimeError):
    pass


@dataclass(frozen=True)
class PaperlessDocument:
    id: int
    title: str
    content: str
    tags: tuple[int, ...]
    correspondent: int | None


class PaperlessClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=f"{settings.paperless_base_url}/api",
            headers={"Authorization": f"Token {settings.paperless_api_token.get_secret_value()}"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            transport=transport,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.settings.external_retry_attempts):
            try:
                response = await self.client.request(method, path, **kwargs)
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    break
                if attempt + 1 < self.settings.external_retry_attempts:
                    await asyncio.sleep(0.5 * (2**attempt))
        raise PaperlessError(f"Paperless request {method} {path} failed") from last_error

    async def resolve_tag_id(self, name: str) -> int:
        response = await self._request("GET", "/tags/", params={"name__iexact": name, "page_size": 2})
        results = response.json().get("results", [])
        if len(results) != 1:
            raise PaperlessError(f"Expected exactly one Paperless tag named {name!r}")
        return int(results[0]["id"])

    async def iter_documents_with_tag(self, tag_name: str) -> AsyncIterator[PaperlessDocument]:
        tag_id = await self.resolve_tag_id(tag_name)
        path: str | None = "/documents/"
        params: dict[str, Any] | None = {"tags__id__all": tag_id, "page_size": 100}
        while path:
            response = await self._request("GET", path, params=params)
            payload = response.json()
            for row in payload.get("results", []):
                yield PaperlessDocument(
                    id=int(row["id"]),
                    title=str(row.get("title") or ""),
                    content=str(row.get("content") or ""),
                    tags=tuple(int(tag) for tag in row.get("tags", [])),
                    correspondent=row.get("correspondent"),
                )
            next_url = payload.get("next")
            path = str(next_url) if next_url else None
            params = None

    async def get_document(self, document_id: int) -> PaperlessDocument:
        payload = (await self._request("GET", f"/documents/{document_id}/")).json()
        return PaperlessDocument(
            id=int(payload["id"]),
            title=str(payload.get("title") or ""),
            content=str(payload.get("content") or ""),
            tags=tuple(int(tag) for tag in payload.get("tags", [])),
            correspondent=payload.get("correspondent"),
        )

    async def download_pdf(self, document_id: int) -> bytes:
        response = await self._request("GET", f"/documents/{document_id}/download/")
        content_type = response.headers.get("content-type", "")
        if "pdf" not in content_type.lower() and not response.content.startswith(b"%PDF"):
            raise PaperlessError("Paperless did not return a PDF")
        return response.content

    async def set_managed_status_tag(self, document_id: int, target_tag_name: str) -> None:
        document = await self.get_document(document_id)
        managed_names = {
            self.settings.paperless_inbox_tag,
            self.settings.paperless_tag_processing,
            self.settings.paperless_tag_queue_review,
            self.settings.paperless_tag_approval,
            self.settings.paperless_tag_approved,
            self.settings.paperless_tag_rejected,
            self.settings.paperless_tag_pohoda_ready,
            self.settings.paperless_tag_exported,
            self.settings.paperless_tag_imported,
        }
        managed_ids = {await self.resolve_tag_id(name) for name in managed_names}
        target_id = await self.resolve_tag_id(target_tag_name)
        new_tags = [tag for tag in document.tags if tag not in managed_ids]
        new_tags.append(target_id)
        await self._request("PATCH", f"/documents/{document_id}/", json={"tags": sorted(set(new_tags))})

