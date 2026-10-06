"""Optional, bounded ARES lookup. Never mutates invoice data or validations."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

import httpx

ARES_URL = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/"


def valid_ico(value: str) -> bool:
    if not re.fullmatch(r"\d{8}", value, flags=re.ASCII):
        return False
    check = (
        11 - sum(int(n) * weight for n, weight in zip(value[:7], range(8, 1, -1), strict=True)) % 11
    ) % 10
    return int(value[-1]) == check


def _normalized(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value).casefold() if c.isalnum())


async def verify_supplier(
    ico: str, name: str | None, address_text: str | None = None, *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {"ico": ico, "blocking": False, "source": "ARES"}
    if not valid_ico(ico):
        return {
            **result,
            "status": "INVALID_ICO",
            "message": "IČO nemá platný formát nebo kontrolní číslici.",
        }
    try:
        async with httpx.AsyncClient(
            timeout=5.0, follow_redirects=False, transport=transport
        ) as client:
            async with client.stream(
                "GET", ARES_URL + ico, headers={"Accept": "application/json"}
            ) as response:
                if response.status_code == 404:
                    return {
                        **result,
                        "status": "NOT_FOUND",
                        "message": "Subjekt nebyl v ARES nalezen.",
                    }
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 256 * 1024:
                        raise ValueError("ARES response exceeds limit")
                payload = json.loads(content)
        if not isinstance(payload, dict) or str(payload.get("ico")) != ico:
            raise ValueError("Unexpected ARES identity")
        official_name = str(payload.get("obchodniJmeno") or "")[:500]
        address = payload.get("sidlo") or {}
        if not isinstance(address, dict) or not official_name:
            raise ValueError("Unexpected ARES data")
        name_matched = _normalized(name or "") == _normalized(official_name)
        official_address = str(address.get("textovaAdresa") or "")[:1000]
        address_matched = _normalized(address_text) == _normalized(official_address) if address_text else None
        matched = name_matched and address_matched is not False
        return {
            **result,
            "status": "MATCH" if matched else "MISMATCH",
            "official_name": official_name,
            "official_address": official_address,
            "name_match": name_matched,
            "address_match": address_matched,
            "message": "Název dodavatele odpovídá ARES."
            if matched
            else "Porovnejte název/adresu dodavatele s registrem; údaje nebyly přepsány.",
        }
    except (httpx.HTTPError, ValueError, TypeError):
        return {
            **result,
            "status": "UNAVAILABLE",
            "message": "ARES je dočasně nedostupný. Kontrola neblokuje práci ani export.",
        }
