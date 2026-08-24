from __future__ import annotations

import pytest
from lxml import etree

from app.models import Allocation, CostCenter, InvoiceRevision
from app.services.bank_accounts import (
    normalize_bank_account,
    normalize_payment_data,
    valid_czech_account_checksum,
)
from app.services.pohoda import NS_INV, NS_TYP, generate_invoice_xml


def revision(*, vat_lines: list[dict[str, str]], total: str) -> InvoiceRevision:
    return InvoiceRevision(
        invoice_id="00000000-0000-0000-0000-000000000001",
        number=1,
        data={
            "supplier_name": "Test s.r.o.",
            "supplier_street": "Testovací 1",
            "supplier_city": "Praha",
            "supplier_zip": "100 00",
            "invoice_number": "BANK-1",
            "issue_date": "2026-08-23",
            "currency": "CZK",
            "total_amount": total,
            "vat_lines": vat_lines,
        },
    )


def allocation(row: InvoiceRevision, code: str, amount: str) -> Allocation:
    centre = CostCenter(code=code, name=code, pohoda_code=code)
    return Allocation(
        invoice_id=row.invoice_id,
        revision_id=row.id,
        cost_center_id=centre.id,
        amount=amount,
        vat_breakdown=[],
        cost_center=centre,
    )


@pytest.mark.parametrize(
    ("account", "code", "raw", "prefix", "number", "bank_code", "legacy"),
    [
        ("19-2000145399/0800", None, "19-2000145399/0800", "19", "2000145399", "0800", "19-2000145399"),
        (" 19 - 2000145399 / 0800 ", "", "19-2000145399/0800", "19", "2000145399", "0800", "19-2000145399"),
        ("2000145399/0800", None, "2000145399/0800", None, "2000145399", "0800", "2000145399"),
        ("19-2000145399", "0800", "19-2000145399/0800", "19", "2000145399", "0800", "19-2000145399"),
        ("2000145399", "0800", "2000145399/0800", None, "2000145399", "0800", "2000145399"),
        ("bad/account", "0800", "bad/account", None, None, "0800", None),
        (None, None, None, None, None, None, None),
    ],
)
def test_deterministic_domestic_account_normalization(
    account: str | None,
    code: str | None,
    raw: str | None,
    prefix: str | None,
    number: str | None,
    bank_code: str | None,
    legacy: str | None,
) -> None:
    result = normalize_bank_account(account, code)
    assert (result.raw, result.prefix, result.number, result.bank_code, result.account) == (
        raw,
        prefix,
        number,
        bank_code,
        legacy,
    )


def test_combined_value_duplicated_by_llm_is_repaired_without_losing_raw_evidence() -> None:
    value = "19-2000145399/0800"
    data = normalize_payment_data({"bank_account": value, "bank_code": value})
    assert data == {
        "bank_account_raw": value,
        "bank_account_prefix": "19",
        "bank_account_number": "2000145399",
        "bank_account": "19-2000145399",
        "bank_code": "0800",
    }


def test_iban_only_does_not_invent_domestic_account() -> None:
    data = normalize_payment_data(
        {"bank_account": None, "bank_code": None, "iban": "CZ65 0800 0000 1920 0014 5399"}
    )
    assert data["bank_account_raw"] is None
    assert data["bank_account_number"] is None
    assert data["bank_code"] is None
    assert data["iban"] == "CZ6508000000192000145399"


def test_czech_checksum_is_safe_and_non_blocking_input_signal() -> None:
    assert valid_czech_account_checksum("19", "2000145399")
    assert not valid_czech_account_checksum("19", "2000145398")


def test_pohoda_receives_account_without_slash_and_separate_bank_code() -> None:
    row = revision(
        vat_lines=[{"vat_rate": "21", "taxable_base": "100.00", "vat_amount": "21.00"}],
        total="121.00",
    )
    combined = "19-2000145399/0800"
    row.data.update({"bank_account": combined, "bank_code": combined})
    xml = generate_invoice_xml(row, [allocation(row, "IT", "121.00")])
    root = etree.fromstring(xml)
    namespaces = {"inv": NS_INV, "typ": NS_TYP}
    assert root.xpath("string(//inv:paymentAccount/typ:accountNo)", namespaces=namespaces) == "19-2000145399"
    assert root.xpath("string(//inv:paymentAccount/typ:bankCode)", namespaces=namespaces) == "0800"
