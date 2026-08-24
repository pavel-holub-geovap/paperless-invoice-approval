from __future__ import annotations

import re
from typing import Any

POSTAL_CODE_RE = re.compile(r"(?<!\d)(\d{3})\s?(\d{2})(?!\d)")


def normalize_czech_postal_code(value: Any) -> str | None:
    if value in (None, ""):
        return None
    compact = re.sub(r"\s+", "", str(value))
    if not re.fullmatch(r"\d{5}", compact):
        return str(value).strip() or None
    return f"{compact[:3]} {compact[3:]}"


def normalize_supplier_address(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize only an already isolated supplier address, never the whole OCR text."""
    result = dict(data)
    raw = result.get("supplier_address_raw", result.get("supplier_address"))
    raw_text = re.sub(r"\s+", " ", str(raw)).strip() if raw not in (None, "") else None
    if raw_text:
        raw_text = POSTAL_CODE_RE.sub(lambda match: f"{match.group(1)} {match.group(2)}", raw_text)
    street = str(result.get("supplier_street") or "").strip() or None
    city = str(result.get("supplier_city") or "").strip() or None
    postal_code = normalize_czech_postal_code(result.get("supplier_zip"))

    # A fallback split is intentionally conservative: exactly one postal code and
    # non-empty text on both sides must exist inside the supplier-only value returned
    # by structured extraction. We never search the complete OCR document.
    matches = list(POSTAL_CODE_RE.finditer(raw_text or ""))
    if raw_text and len(matches) == 1:
        match = matches[0]
        before = raw_text[: match.start()].strip(" ,;")
        after = raw_text[match.end() :].strip(" ,;")
        if before and after:
            street = street or before
            postal_code = postal_code or f"{match.group(1)} {match.group(2)}"
            city = city or after

    result["supplier_address_raw"] = raw_text
    # Keep the legacy key readable for old API consumers; POHODA never uses it.
    result["supplier_address"] = raw_text
    result["supplier_street"] = street
    result["supplier_city"] = city
    result["supplier_zip"] = postal_code
    return result
