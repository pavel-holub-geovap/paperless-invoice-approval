from __future__ import annotations

import re

ROUNDING_REJECTION_CODE = "ROUNDING_WITHOUT_EXPLICIT_EVIDENCE"
ROUNDING_REJECTION_REASON = (
    "LLM ROUNDING classification rejected: source_text does not contain an explicit "
    "rounding row label; VAT and invoice summary labels are not rounding evidence."
)

_EXPLICIT_ROUNDING_LINE = re.compile(
    r"^\s*(?:[-–—•*]\s*)?"
    r"(?:zaokrouhlen[ií]|zaokr\.?|hal[eé]řov[eé]\s+vyrovn[aá]n[ií]|"
    r"vyrovn[aá]n[ií]|rounding)(?=\s|:|=|$)",
    re.IGNORECASE,
)


def has_explicit_rounding_evidence(source_text: str | None) -> bool:
    """Accept rounding only when its evidence contains a dedicated printed label."""
    if not source_text:
        return False
    return any(
        _EXPLICIT_ROUNDING_LINE.search(" ".join(line.split()))
        for line in source_text.splitlines()
    )


def canonical_rounding_type(
    adjustment_type: str | None,
    source_text: str | None,
) -> str | None:
    """Treat the model's classification as a candidate, never as authority."""
    if has_explicit_rounding_evidence(source_text):
        return "ROUNDING"
    if adjustment_type is None or adjustment_type.casefold() in {"none", "null"}:
        return None
    if adjustment_type.casefold() != "rounding":
        return adjustment_type
    return None
