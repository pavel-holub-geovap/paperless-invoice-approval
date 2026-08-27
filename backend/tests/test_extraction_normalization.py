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
from app.services.rounding import ROUNDING_REJECTION_CODE
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


@pytest.mark.parametrize(
    "source_text",
    [
        "CELKEM 10 000,00 Kč",
        "CELKEM K ÚHRADĚ 12 100,00 Kč",
        "Celkem s DPH 12 100,00 Kč",
        "Základ 10 000,00 Kč Výše DPH 2 100,00 Kč Cena celkem 12 100,00 Kč",
        "Sazba DPH Základ Výše DPH Celkem\n21 % 4 300,00 Kč 903,00 Kč 5 203,00 Kč",
    ],
)
def test_rounding_candidate_with_summary_evidence_is_rejected(source_text: str) -> None:
    raw = structured()
    raw["vat_lines"][0].update(
        taxable_base="4300.00",
        vat_amount="903.00",
        gross_amount="5203.00",
        adjustment_type="ROUNDING",
        source_text=source_text,
    )

    payload, diagnostics = normalize_raw_extraction(raw)

    assert payload.vat_lines[0].adjustment_type is None
    assert diagnostics["rejections"] == [
        {
            "path": "vat_lines.0.adjustment_type",
            "code": ROUNDING_REJECTION_CODE,
            "raw": "ROUNDING",
            "normalized": None,
            "source_text": source_text,
            "reason": diagnostics["rejections"][0]["reason"],
        }
    ]
    assert "summary labels are not rounding evidence" in diagnostics["rejections"][0]["reason"]


@pytest.mark.parametrize(
    "source_text",
    [
        "Zaokrouhlení +0,30 Kč",
        "Zaokrouhlení -0,25 Kč",
        "Haléřové vyrovnání: 0,30 Kč",
        "Vyrovnání -0,25 Kč",
        "Rounding 0.30",
    ],
)
def test_explicit_rounding_evidence_is_accepted(source_text: str) -> None:
    raw = structured()
    raw["vat_lines"][0].update(
        taxable_base="0.25",
        vat_amount="0.05",
        gross_amount="0.30",
        adjustment_type="ROUNDING",
        source_text=source_text,
    )

    payload, diagnostics = normalize_raw_extraction(raw)

    assert payload.vat_lines[0].adjustment_type == "ROUNDING"
    assert diagnostics["rejections"] == []


def test_explicit_rounding_label_is_deterministically_classified() -> None:
    raw = structured()
    raw["vat_lines"][0].update(
        taxable_base="0.25",
        vat_amount="0.05",
        gross_amount="0.30",
        adjustment_type=None,
        source_text="Zaokr. 0,30 Kč",
    )

    payload, _ = normalize_raw_extraction(raw)

    assert payload.vat_lines[0].adjustment_type == "ROUNDING"


def test_unknown_adjustment_enum_remains_a_schema_error() -> None:
    raw = structured()
    raw["vat_lines"][0]["adjustment_type"] = "ZakladCZK"

    with pytest.raises(ExtractionNormalizationFailed) as caught:
        normalize_raw_extraction(raw)

    assert caught.value.errors[0]["path"] == "vat_lines.0.adjustment_type"
    assert caught.value.errors[0]["stage"] == "canonical_schema"
