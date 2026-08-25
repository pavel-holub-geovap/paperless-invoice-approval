from __future__ import annotations

import pytest

from app.schemas import DateEvidence, InvoiceExtractionV1
from app.services.invoice_dates import reconcile_extraction_dates


def payload() -> InvoiceExtractionV1:
    return InvoiceExtractionV1.model_construct(
        issue_date=DateEvidence(
            value="2026-07-08",
            source_text="Datum vystavení: 08.07.2026",
        ),
        taxable_supply_date=DateEvidence(
            value="2026-07-08",
            source_text="Datum vystavení: 08.07.2026",
        ),
        due_date=DateEvidence(
            value="2026-08-07",
            source_text="Datum splatnosti: 07.08.2026",
        ),
    )


@pytest.mark.parametrize(
    "label",
    [
        "DUZP",
        "Datum zd. plnění",
        "Datum zdan. plnění",
        "Datum uskutečnění zdanitelného plnění",
    ],
)
def test_explicit_duzp_is_distinct_from_issue_date_and_keeps_own_evidence(
    label: str,
) -> None:
    result = reconcile_extraction_dates(
        payload(),
        f"Datum vystavení: 08.07.2026\n{label}: 30.06.2026\n"
        "Datum splatnosti: 07.08.2026",
    )

    assert result.issue_date.value.isoformat() == "2026-07-08"
    assert result.taxable_supply_date.value.isoformat() == "2026-06-30"
    assert result.due_date.value.isoformat() == "2026-08-07"
    assert result.taxable_supply_date.source_text == f"{label}: 30.06.2026"
    assert result.taxable_supply_date.source_text != result.issue_date.source_text


def test_missing_duzp_is_null_instead_of_guessed_from_issue_date() -> None:
    result = reconcile_extraction_dates(
        payload(),
        "Datum vystavení: 08.07.2026\nDatum splatnosti: 07.08.2026",
    )

    assert result.issue_date.value.isoformat() == "2026-07-08"
    assert result.taxable_supply_date.value is None
    assert result.taxable_supply_date.source_text is None
    assert result.due_date.value.isoformat() == "2026-08-07"
