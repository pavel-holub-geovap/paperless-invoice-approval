from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import Settings
from app.schemas import ExtractionPayload

SYSTEM_PROMPT = """Jsi deterministický extraktor českých přijatých faktur.
Vrať pouze JSON odpovídající schématu. Hodnotu použij jen tehdy, je-li jednoznačně v OCR.
Jinak vrať null. Ke každé hodnotě vrať krátký doslovný source_text z OCR.
Nevymýšlej účetní data, nic nepočítej a nevytvářej XML."""


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.ollama_base_url,
            timeout=httpx.Timeout(settings.ollama_timeout_seconds, connect=10),
            transport=transport,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def extract_invoice(self, ocr_text: str) -> ExtractionPayload:
        schema: dict[str, Any] = ExtractionPayload.model_json_schema()
        try:
            response = await self.client.post(
                "/api/chat",
                json={
                    "model": self.settings.ollama_model,
                    "stream": False,
                    "format": schema,
                    "options": {"temperature": 0, "num_ctx": self.settings.ollama_num_ctx},
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": ocr_text},
                    ],
                },
            )
            response.raise_for_status()
            content = response.json()["message"]["content"]
            return ExtractionPayload.model_validate(json.loads(content))
        except (httpx.HTTPError, KeyError, json.JSONDecodeError, ValueError) as exc:
            raise OllamaError("Ollama extraction failed or returned invalid structured output") from exc

