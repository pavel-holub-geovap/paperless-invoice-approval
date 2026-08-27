from decimal import Decimal

from app.models import ValidationSeverity
from app.services.validation import (
    run_validations,
    valid_czech_ico,
    valid_iban,
    validate_invoice_data,
)
from app.services.workflow import create_invoice, update_invoice_data


def valid_data() -> dict[str, object]:
    return {
        "supplier_name": "Test s.r.o.",
        "ico": "27082440",
        "dic": "CZ27082440",
        "invoice_number": "2026001",
        "variable_symbol": "2026001",
        "issue_date": "2026-08-01",
        "taxable_supply_date": "2026-08-01",
        "due_date": "2026-08-15",
        "currency": "CZK",
        "total_amount": "121.00",
        "vat_breakdown": [{"base": "100.00", "rate": "21", "vat": "21.00"}],
    }


def test_czech_ico_checksum() -> None:
    assert valid_czech_ico("27082440")
    assert not valid_czech_ico("27082441")
    assert not valid_czech_ico("123")


def test_valid_invoice_has_no_blocking_error() -> None:
    results = validate_invoice_data(valid_data())
    assert not [row for row in results if row.severity == ValidationSeverity.BLOCKING_ERROR]


def test_vat_total_mismatch_is_a_review_warning() -> None:
    data = valid_data()
    data["total_amount"] = "120.00"
    results = validate_invoice_data(data)
    mismatch = next(row for row in results if row.code == "VAT_TOTAL_MATH")
    assert mismatch.severity == ValidationSeverity.WARNING
    assert mismatch.expected == "121.00"
    assert mismatch.actual == "120.00"
    assert mismatch.details == {"difference": "-1.00"}


def test_missing_required_fields_are_blocking() -> None:
    results = validate_invoice_data({})
    assert {row.field_name for row in results if row.severity == ValidationSeverity.BLOCKING_ERROR} >= {
        "supplier_name", "invoice_number", "issue_date", "currency", "total_amount"
    }


def test_duplicate_supplier_number_and_amount_is_blocking(db) -> None:
    first = create_invoice(db, 1001)
    second = create_invoice(db, 1002)
    update_invoice_data(db, first, valid_data(), "manager")
    update_invoice_data(db, second, valid_data(), "manager")
    results = run_validations(db, second)
    assert any(row.code == "DUPLICATE_INVOICE" for row in results)


def test_czech_golden_values_validate_with_decimal_math() -> None:
    data = {
        "supplier_name": "TESTOVACÍ DODAVATEL s.r.o.",
        "supplier_ico": "00000019",
        "supplier_dic": "CZ00000019",
        "invoice_number": "TEST-2026-0001",
        "variable_symbol": "20260001",
        "issue_date": "2026-08-20",
        "taxable_supply_date": "2026-08-20",
        "due_date": "2026-09-03",
        "currency": "CZK",
        "bank_account": "0000000000",
        "bank_code": "0000",
        "vat_lines": [{"vat_rate": "21", "taxable_base": "1000.00", "vat_amount": "210.00"}],
        "total_without_vat": "1000.00",
        "total_vat": "210.00",
        "total_amount": "1210.00",
    }
    results = validate_invoice_data(data)
    assert not [row for row in results if row.severity == ValidationSeverity.BLOCKING_ERROR]
    assert any(row.code == "ICO_OK" for row in results)
    assert any(row.code == "TOTAL_MATH_OK" and row.expected == "1210.00" for row in results)


def test_iban_mod97_and_expected_actual_diagnostics() -> None:
    assert valid_iban("CZ6508000000192000145399")
    assert not valid_iban("CZ6508000000192000145398")
    data = valid_data()
    data["currency"] = "CROWNS"
    results = validate_invoice_data(data)
    failure = next(row for row in results if row.code == "CURRENCY_ISO")
    assert failure.expected == "ISO 4217"
    assert failure.actual == "CROWNS"


def test_exact_pixel_amounts_have_no_rounding_warning() -> None:
    data = valid_data()
    data.update(
        supplier_name="Pixel Design s.r.o.",
        total_without_vat="4300.00",
        total_vat="903.00",
        total_amount="5203.00",
        vat_lines=[
            {
                "vat_rate": "21",
                "taxable_base": "4300.00",
                "vat_amount": "903.00",
                "gross_amount": "5203.00",
                "adjustment_type": None,
                "source_text": (
                    "Sazba DPH Základ Výše DPH Celkem\n"
                    "21 % 4 300,00 Kč 903,00 Kč 5 203,00 Kč"
                ),
            }
        ],
    )

    results = validate_invoice_data(data)

    assert {row.code for row in results} >= {
        "VAT_ROW_OK",
        "VAT_BASE_TOTAL_OK",
        "VAT_TOTAL_OK",
        "TOTAL_MATH_OK",
    }
    assert not any(row.code == "VAT_ROUNDING_ADJUSTMENT" for row in results)
    assert Decimal("3600.00") + Decimal("1000.00") - Decimal("300.00") == Decimal(
        "4300.00"
    )
    assert Decimal("756.00") + Decimal("210.00") - Decimal("63.00") == Decimal(
        "903.00"
    )
