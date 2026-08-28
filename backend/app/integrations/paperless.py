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


class PaperlessAuthError(PaperlessError):
    pass


class PaperlessValidationError(PaperlessError):
    pass


class PaperlessUnavailable(PaperlessError):
    """A failure known to have happened before Paperless accepted the request."""


class PaperlessSubmissionUnknown(PaperlessError):
    """Paperless may have accepted the upload, so automatic retry is unsafe."""


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


@dataclass(frozen=True)
class PaperlessTask:
    task_id: str
    status: str
    related_document_ids: tuple[int, ...]
    error: str | None


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

    async def search_document_ids(self, query: str) -> set[int]:
        """Search Paperless OCR/fulltext once per result page and return only IDs."""
        path: str | None = "/documents/"
        params: dict[str, Any] | None = {
            "query": query,
            "page_size": 100,
        }
        document_ids: set[int] = set()
        while path:
            response = await self._request("GET", path, params=params)
            payload = response.json()
            document_ids.update(int(row["id"]) for row in payload.get("results", []))
            next_url = payload.get("next")
            path = str(next_url) if next_url else None
            params = None
        return document_ids

    async def download_pdf(self, document_id: int) -> bytes:
        response = await self._request("GET", f"/documents/{document_id}/download/")
        content_type = response.headers.get("content-type", "")
        if "pdf" not in content_type.lower() and not response.content.startswith(b"%PDF"):
            raise PaperlessError("Paperless did not return a PDF")
        return response.content

    async def post_document(
        self,
        content: bytes,
        *,
        filename: str,
        title: str,
        tag_id: int,
    ) -> str:
        """Submit exactly once; callers decide whether a transport failure is retryable."""
        try:
            response = await self.client.post(
                "/documents/post_document/",
                data={"title": title, "tags": str(tag_id)},
                files={"document": (filename, content, "application/pdf")},
            )
        except httpx.ConnectError as exc:
            raise PaperlessUnavailable("Paperless is unavailable before upload") from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
            raise PaperlessSubmissionUnknown(
                "Paperless upload response timed out; acceptance is unknown"
            ) from exc
        except httpx.NetworkError as exc:
            raise PaperlessSubmissionUnknown(
                "Paperless upload connection was interrupted; acceptance is unknown"
            ) from exc
        if response.status_code in {401, 403}:
            raise PaperlessAuthError("Paperless rejected the technical credentials")
        if response.status_code in {400, 404, 409, 415, 422}:
            raise PaperlessValidationError("Paperless rejected the uploaded PDF")
        if response.status_code >= 500:
            raise PaperlessSubmissionUnknown(
                f"Paperless returned HTTP {response.status_code}; acceptance is unknown"
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PaperlessError(
                f"Paperless upload failed with HTTP {response.status_code}"
            ) from exc
        try:
            payload = response.json()
        except ValueError:
            payload = response.text.strip().strip('"')
        task_id = payload if isinstance(payload, str) else payload.get("task_id")
        if not task_id:
            raise PaperlessSubmissionUnknown(
                "Paperless accepted the upload but returned no task identifier"
            )
        return str(task_id)

    async def get_task(self, task_id: str) -> PaperlessTask | None:
        response = await self._request(
            "GET", "/tasks/", params={"task_id": task_id, "page_size": 2}
        )
        payload = response.json()
        rows = payload.get("results", []) if isinstance(payload, dict) else payload
        matching = [row for row in rows if str(row.get("task_id")) == task_id]
        if not matching:
            return None
        row = matching[0]
        related = row.get("related_document_ids") or []
        if not related:
            legacy = row.get("related_document")
            if legacy is not None:
                related = [legacy]
        result_data = row.get("result_data")
        if not related and isinstance(result_data, dict):
            document_id = result_data.get("document_id") or result_data.get("document")
            if document_id is not None:
                related = [document_id]
        error = None
        if str(row.get("status", "")).lower() in {"failure", "failed"}:
            if isinstance(result_data, dict):
                error = str(result_data.get("message") or result_data.get("error") or "")
            elif result_data is not None:
                error = str(result_data)
            error = (error or "Paperless document processing failed")[:1000]
        return PaperlessTask(
            task_id=task_id,
            status=str(row.get("status") or "pending").lower(),
            related_document_ids=tuple(int(value) for value in related),
            error=error,
        )

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
