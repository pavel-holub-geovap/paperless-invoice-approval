from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

MONEY = r"(?:\d{1,3}(?:[ .]\d{3})*|\d+),\d{2}"
VAT_TABLE_ROW_RE = re.compile(
    rf"^(?P<label>.+?)\s+(?P<rate>\d{{1,2}})\s*%\s+"
    rf"(?P<base>{MONEY})\s*Kč\s+(?P<gross>{MONEY})\s*Kč\s*$",
    re.IGNORECASE,
)
BASE_TOTAL_RE = re.compile(
    rf"(?P<amount>{MONEY})\s*Kč\s+Sumář\s+Celkem bez DPH",
    re.IGNORECASE | re.DOTALL,
)
VAT_TOTAL_RE = re.compile(rf"DPH\s+\d{{1,2}}\s*%\s+(?P<amount>{MONEY})\s*Kč", re.IGNORECASE)
PRICE_TOTAL_RE = re.compile(rf"(?P<amount>{MONEY})\s*Kč\s*Cena\b", re.IGNORECASE)


def _decimal(value: str) -> Decimal:
    return Decimal(value.replace(" ", "").replace(".", "").replace(",", "."))


def reconcile_printed_invoice_amounts(data: dict[str, Any], ocr_text: str | None) -> dict[str, Any]:
    """Prefer explicitly printed VAT table/summary values over LLM arithmetic."""
    result = dict(data)
    if not ocr_text:
        return result

    rows: list[dict[str, Any]] = []
    for source_line in ocr_text.splitlines():
        line = " ".join(source_line.split())
        match = VAT_TABLE_ROW_RE.fullmatch(line)
        if not match:
            continue
        base = _decimal(match.group("base"))
        gross = _decimal(match.group("gross"))
        vat = gross - base
        label = match.group("label").strip(" ,.;")
        rows.append(
            {
                "vat_rate": str(Decimal(match.group("rate"))),
                "taxable_base": str(base),
                "vat_amount": str(vat),
                "gross_amount": str(gross),
                "adjustment_type": (
                    "ROUNDING"
                    if re.search(r"\b(?:zaokrouhlení|zaokr\.?|rounding)\b", label, re.IGNORECASE)
                    else None
                ),
                "source_text": line,
                "normalization": "printed_ocr_vat_table",
            }
        )
    if rows:
        result["vat_lines"] = rows

    totals = {
        "total_without_vat": BASE_TOTAL_RE.search(ocr_text),
        "total_vat": VAT_TOTAL_RE.search(ocr_text),
        "total_amount": PRICE_TOTAL_RE.search(ocr_text),
    }
    for field, match in totals.items():
        if match:
            result[field] = str(_decimal(match.group("amount")))
    return result
