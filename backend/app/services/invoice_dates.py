from __future__ import annotations

import re
from datetime import date

from app.schemas import DateEvidence, InvoiceExtractionV1

_DATE = r"(?P<date>\d{1,2}\s*[.]\s*\d{1,2}\s*[.]\s*\d{4})"
_LABELS = {
    "issue_date": r"(?:datum\s+vystavení|vystaveno)",
    "taxable_supply_date": (
        r"(?:DUZP|datum\s+zd[.]?\s*plnění|datum\s+zdan[.]?\s*plnění|"
        r"datum\s+zdanitelného\s+plnění|datum\s+uskutečnění\s+zdanitelného\s+plnění)"
    ),
    "due_date": r"(?:datum\s+splatnosti|splatnost)",
}


def _explicit_date_evidence(ocr_text: str, field: str) -> DateEvidence | None:
    pattern = re.compile(
        rf"(?P<source>{_LABELS[field]}\s*:?[ \t]*{_DATE})",
        re.IGNORECASE,
    )
    match = pattern.search(ocr_text)
    if match is None:
        return None
    raw = re.sub(r"\s+", " ", match.group("date")).replace(" ", "")
    day, month, year = (int(part) for part in raw.split("."))
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None
    return DateEvidence(
        value=parsed,
        source_text=re.sub(r"\s+", " ", match.group("source")).strip(),
    )


def reconcile_extraction_dates(
    payload: InvoiceExtractionV1,
    ocr_text: str | None,
) -> InvoiceExtractionV1:
    """Bind Czech labeled dates to their own fields and provenance."""
    if ocr_text is None:
        return payload
    updates: dict[str, DateEvidence] = {}
    for field in ("issue_date", "taxable_supply_date", "due_date"):
        explicit = _explicit_date_evidence(ocr_text, field)
        if explicit is not None:
            updates[field] = explicit
        elif field == "taxable_supply_date":
            # DUZP must never be guessed from issue_date when no explicit label exists.
            updates[field] = DateEvidence(value=None, source_text=None)
    return payload.model_copy(update=updates)
