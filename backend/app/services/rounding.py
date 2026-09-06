from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

ROUNDING_REJECTION_CODE = "ROUNDING_WITHOUT_EXPLICIT_EVIDENCE"
ROUNDING_REJECTION_REASON = (
    "LLM ROUNDING classification rejected: source_text must contain a non-zero amount "
    "on the same explicit rounding row and that amount must match the candidate; VAT "
    "and invoice summary labels are not rounding evidence."
)

ROUNDING_EVIDENCE_TOLERANCE = Decimal("0.01")

_EXPLICIT_ROUNDING_LINE = re.compile(
    r"^\s*(?:[-–—•*]\s*)?"
    r"(?:zaokrouhlen[ií]|zaokr\.?|hal[eé]řov[eé]\s+vyrovn[aá]n[ií]|"
    r"vyrovn[aá]n[ií]|rounding)(?=\s|:|=|$)",
    re.IGNORECASE,
)

_AMOUNT_TOKEN = re.compile(
    r"(?<!\d)[+-]?(?:\d{1,3}(?:[ .\u00a0]\d{3})+|\d+)(?:[,.]\d{1,2})?(?!\d)"
)


def _decimal_token(value: str) -> Decimal | None:
    compact = value.replace(" ", "").replace("\u00a0", "")
    if "," in compact:
        compact = compact.replace(".", "").replace(",", ".")
    try:
        return Decimal(compact)
    except InvalidOperation:
        return None


def explicit_rounding_amount(source_text: str | None) -> Decimal | None:
    """Read the amount only from the same line as an explicit rounding label."""
    if not source_text:
        return None
    for source_line in source_text.splitlines():
        line = " ".join(source_line.split())
        label = _EXPLICIT_ROUNDING_LINE.search(line)
        if label is None:
            continue
        remainder = line[label.end() :]
        values = []
        for token in _AMOUNT_TOKEN.finditer(remainder):
            if remainder[token.end() :].lstrip().startswith("%"):
                continue
            parsed = _decimal_token(token.group())
            if parsed is not None:
                values.append(parsed)
        if values:
            # A VAT rounding row can contain rate, base, VAT and gross. Its printed
            # adjustment is the last monetary value on that same dedicated row.
            return values[-1]
    return None


def canonical_rounding_type(
    adjustment_type: str | None,
    source_text: str | None,
    *,
    taxable_base: Any = None,
    vat_amount: Any = None,
    gross_amount: Any = None,
) -> str | None:
    """Accept a material rounding candidate only when same-line evidence agrees."""
    evidence_amount = explicit_rounding_amount(source_text)
    candidate_amount = _as_decimal(gross_amount)
    if candidate_amount is None:
        base = _as_decimal(taxable_base)
        vat = _as_decimal(vat_amount)
        if base is not None and vat is not None:
            candidate_amount = base + vat
    if (
        evidence_amount is not None
        and evidence_amount != 0
        and candidate_amount is not None
        and abs(evidence_amount - candidate_amount) <= ROUNDING_EVIDENCE_TOLERANCE
    ):
        return "ROUNDING"
    if adjustment_type is None or adjustment_type.casefold() in {"none", "null"}:
        return None
    if adjustment_type.casefold() != "rounding":
        return adjustment_type
    return None


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def normalize_invoice_rounding(data: dict[str, Any]) -> dict[str, Any]:
    """Revalidate derived rounding flags before they become current business data."""
    rows = data.get("vat_lines")
    if not isinstance(rows, list):
        return data
    normalized_rows: list[Any] = []
    changed = False
    for row in rows:
        if not isinstance(row, dict):
            normalized_rows.append(row)
            continue
        normalized = dict(row)
        current = normalized.get("adjustment_type")
        canonical = canonical_rounding_type(
            str(current) if current is not None else None,
            normalized.get("source_text"),
            taxable_base=normalized.get("taxable_base", normalized.get("base")),
            vat_amount=normalized.get("vat_amount", normalized.get("vat")),
            gross_amount=normalized.get("gross_amount", normalized.get("gross")),
        )
        if current != canonical:
            normalized["adjustment_type"] = canonical
            changed = True
        normalized_rows.append(normalized)
    if not changed:
        return data
    result = dict(data)
    result["vat_lines"] = normalized_rows
    return result
