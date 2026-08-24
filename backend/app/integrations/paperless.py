from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from app.config import Settings


class PaperlessError(RuntimeError):
    pass


class PaperlessNotFound(PaperlessError):
    pass


@dataclass(frozen=True)
class PaperlessDocument:
    id: int
    title: str
    content: str
    created_at: datetime | None
    tags: tuple[int, ...]
    tag_names: tuple[str, ...]
    correspondent: int | None
    correspondent_name: str | None
    original_filename: str | None


class PaperlessClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=f"{settings.paperless_base_url}/api",
            headers={
                "Authorization": f"Token {settings.read_paperless_api_token()}",
                "Accept": "application/json; version=10",
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
            transport=transport,
        )
        self._tag_names: dict[int, str] = {}
        self._correspondent_names: dict[int, str] = {}

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.settings.external_retry_attempts):
            try:
                response = await self.client.request(method, path, **kwargs)
                if response.status_code == 404:
                    raise PaperlessNotFound(f"Paperless resource {path} was not found")
                response.raise_for_status()
                return response
            except PaperlessNotFound:
                raise
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
        tag_id = int(results[0]["id"])
        self._tag_names[tag_id] = str(results[0].get("name") or name)
        return tag_id

    async def resolve_tag_name(self, tag_id: int) -> str:
        if tag_id not in self._tag_names:
            payload = (await self._request("GET", f"/tags/{tag_id}/")).json()
            self._tag_names[tag_id] = str(payload.get("name") or tag_id)
        return self._tag_names[tag_id]

    async def resolve_correspondent_name(self, correspondent_id: int | None) -> str | None:
        if correspondent_id is None:
            return None
        if correspondent_id not in self._correspondent_names:
            payload = (
                await self._request("GET", f"/correspondents/{correspondent_id}/")
            ).json()
            self._correspondent_names[correspondent_id] = str(
                payload.get("name") or correspondent_id
            )
        return self._correspondent_names[correspondent_id]

    async def _document_from_payload(self, payload: dict[str, Any]) -> PaperlessDocument:
        tag_ids = tuple(int(tag) for tag in payload.get("tags", []))
        correspondent_id = payload.get("correspondent")
        raw_created_at = payload.get("added") or payload.get("created")
        created_at = None
        if raw_created_at:
            created_at = datetime.fromisoformat(str(raw_created_at).replace("Z", "+00:00"))
        return PaperlessDocument(
            id=int(payload["id"]),
            title=str(payload.get("title") or ""),
            content=str(payload.get("content") or ""),
            created_at=created_at,
            tags=tag_ids,
            tag_names=tuple([await self.resolve_tag_name(tag_id) for tag_id in tag_ids]),
            correspondent=int(correspondent_id) if correspondent_id is not None else None,
            correspondent_name=await self.resolve_correspondent_name(
                int(correspondent_id) if correspondent_id is not None else None
            ),
            original_filename=(
                str(payload["original_file_name"])
                if payload.get("original_file_name")
                else None
            ),
        )

    async def iter_documents_with_tag(self, tag_name: str) -> AsyncIterator[PaperlessDocument]:
        tag_id = await self.resolve_tag_id(tag_name)
        async for document in self.iter_documents(tag_id=tag_id):
            yield document

    async def iter_documents(self, *, tag_id: int | None = None) -> AsyncIterator[PaperlessDocument]:
        path: str | None = "/documents/"
        params: dict[str, Any] | None = {"page_size": 100}
        if tag_id is not None:
            params["tags__id__all"] = tag_id
        while path:
            response = await self._request("GET", path, params=params)
            payload = response.json()
            for row in payload.get("results", []):
                yield await self._document_from_payload(row)
            next_url = payload.get("next")
            path = str(next_url) if next_url else None
            params = None

    async def get_document(self, document_id: int) -> PaperlessDocument:
        payload = (await self._request("GET", f"/documents/{document_id}/")).json()
        return await self._document_from_payload(payload)

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
            self.settings.paperless_tag_duplicate,
            self.settings.paperless_tag_ignored,
        }
        managed_ids = {await self.resolve_tag_id(name) for name in managed_names}
        target_id = await self.resolve_tag_id(target_tag_name)
        new_tags = [tag for tag in document.tags if tag not in managed_ids]
        new_tags.append(target_id)
        await self._request("PATCH", f"/documents/{document_id}/", json={"tags": sorted(set(new_tags))})
