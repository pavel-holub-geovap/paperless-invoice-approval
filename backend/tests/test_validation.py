from app.models import ValidationSeverity
from app.services.validation import run_validations, valid_czech_ico, validate_invoice_data
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


def test_vat_total_is_blocking() -> None:
    data = valid_data()
    data["total_amount"] = "120.00"
    results = validate_invoice_data(data)
    assert any(row.code == "VAT_TOTAL_MATH" and row.severity == ValidationSeverity.BLOCKING_ERROR for row in results)


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
