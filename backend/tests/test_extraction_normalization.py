from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.bank_accounts import normalize_payment_data
from app.services.extraction_normalization import (
    ExtractionNormalizationFailed,
    normalize_date,
    normalize_decimal,
    normalize_raw_extraction,
)
from tests.test_ai_extraction import structured


@pytest.mark.parametrize("raw", ["21%", "21 %", "21"])
def test_vat_rate_localization(raw: str) -> None:
    assert normalize_decimal(raw, path="vat_lines.0.vat_rate", allow_percent=True) == Decimal("21")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 000,00", Decimal("1000.00")),
        ("1000,00", Decimal("1000.00")),
        ("1000.00", Decimal("1000.00")),
        ("12 366,69 Kč", Decimal("12366.69")),
        ("4 919,00 CZK", Decimal("4919.00")),
    ],
)
def test_amount_localization_without_float(raw: str, expected: Decimal) -> None:
    assert normalize_decimal(raw, path="total_amount", allow_percent=False) == expected


@pytest.mark.parametrize("raw", ["30.06.2026", "30. 06. 2026", "2026-06-30"])
def test_date_localization(raw: str) -> None:
    assert normalize_date(raw, path="taxable_supply_date").isoformat() == "2026-06-30"


@pytest.mark.parametrize("raw", ["1,234", "1.234", "12 34,00", "twelve"])
def test_ambiguous_or_invalid_amount_is_not_guessed(raw: str) -> None:
    with pytest.raises(ExtractionNormalizationFailed):
        normalize_decimal(raw, path="total_amount", allow_percent=False)


def test_blank_nullable_values_and_postal_code_are_normalized() -> None:
    raw = structured()
    raw["swift_bic"] = {"value": "   ", "source_text": "   "}
    raw["supplier_zip"] = {"value": "14000", "source_text": "PSČ 14000"}

    payload, result = normalize_raw_extraction(raw)

    assert payload.swift_bic.value is None
    assert payload.swift_bic.source_text is None
    assert payload.supplier_zip.value == "140 00"
    assert {row["path"] for row in result["changes"]} >= {
        "swift_bic",
        "swift_bic.source_text",
        "supplier_zip",
    }


def test_existing_bank_normalization_splits_account_and_code() -> None:
    normalized = normalize_payment_data({"bank_account": "217058123/0300"})
    assert normalized["bank_account_number"] == "217058123"
    assert normalized["bank_code"] == "0300"
