from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree

from app.models import Allocation, CostCenter, InvoiceRevision
from app.services.pohoda import (
    NS_DATA,
    NS_INV,
    NS_TYP,
    PohodaMappingError,
    generate_invoice_xml,
    parse_pohoda_response,
    validate_xml_detailed,
)

ROOT = Path(__file__).resolve().parents[2]
XSD = ROOT / "schemas" / "pohoda" / "2025-10-16" / "data.xsd"
RESPONSE_XSD = XSD.with_name("response.xsd")
NS = {"dat": NS_DATA, "inv": NS_INV, "typ": NS_TYP}


def revision(*, vat_lines: list[dict[str, str]], total: str) -> InvoiceRevision:
    return InvoiceRevision(
        invoice_id="00000000-0000-0000-0000-000000000001",
        number=1,
        data={
            "supplier_name": "Řešení ščěřžýáíé s.r.o.",
            "supplier_ico": "27082440",
            "supplier_dic": "CZ27082440",
            "supplier_street": "Testovací 1",
            "supplier_city": "Praha",
            "supplier_zip": "100 00",
            "invoice_number": "DOD-2026-001",
            "variable_symbol": "2026001",
            "issue_date": "2026-08-01",
            "taxable_supply_date": "2026-08-01",
            "due_date": "2026-08-15",
            "currency": "CZK",
            "bank_account": "123456789",
            "bank_code": "0100",
            "total_amount": total,
            "description": "Testovací licence",
            "vat_lines": vat_lines,
        },
    )


def allocation(
    row_revision: InvoiceRevision,
    code: str,
    amount: str,
    *,
    vat_breakdown: list[dict[str, str]] | None = None,
) -> Allocation:
    centre = CostCenter(code=code, name=code, pohoda_code=code)
    return Allocation(
        invoice_id=row_revision.invoice_id,
        revision_id=row_revision.id,
        cost_center_id=centre.id,
        amount=Decimal(amount),
        vat_breakdown=vat_breakdown or [],
        cost_center=centre,
    )


def document(xml: bytes) -> etree._Element:
    return etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True))


def item_values(xml: bytes) -> list[tuple[str, Decimal, Decimal]]:
    root = document(xml)
    values = []
    for item in root.xpath("//inv:invoiceItem", namespaces=NS):
        centre = item.xpath("string(inv:centre/typ:ids)", namespaces=NS)
        base = Decimal(item.xpath("string(inv:homeCurrency/typ:price)", namespaces=NS))
        vat = Decimal(item.xpath("string(inv:homeCurrency/typ:priceVAT)", namespaces=NS))
        values.append((centre, base, vat))
    return values


def test_received_invoice_semantics_address_payment_and_encoding() -> None:
    row = revision(vat_lines=[{"vat_rate": "21", "taxable_base": "100.00", "vat_amount": "21.00"}], total="121.00")
    xml = generate_invoice_xml(row, [allocation(row, "IT", "121.00")])
    root = document(xml)

    assert validate_xml_detailed(xml, XSD) == []
    assert b"encoding='Windows-1250'" in xml.splitlines()[0]
    assert "Řešení ščěřžýáíé" in xml.decode("windows-1250")
    assert root.xpath("string(//inv:invoiceType)", namespaces=NS) == "receivedInvoice"
    assert root.xpath("string(//inv:originalDocument)", namespaces=NS) == "DOD-2026-001"
    assert not root.xpath("//inv:number", namespaces=NS)
    address = root.xpath("//inv:partnerIdentity/typ:address", namespaces=NS)[0]
    assert address.get("linkToAddress") == "false"
    assert address.xpath("string(typ:company)", namespaces=NS) == "Řešení ščěřžýáíé s.r.o."
    assert address.xpath("string(typ:street)", namespaces=NS) == "Testovací 1"
    assert address.xpath("string(typ:city)", namespaces=NS) == "Praha"
    assert address.xpath("string(typ:zip)", namespaces=NS) == "100 00"
    assert address.xpath("string(typ:ico)", namespaces=NS) == "27082440"
    assert address.xpath("string(typ:dic)", namespaces=NS) == "CZ27082440"
    assert root.xpath("string(//inv:paymentAccount/typ:accountNo)", namespaces=NS) == "123456789"
    assert root.xpath("string(//inv:paymentAccount/typ:bankCode)", namespaces=NS) == "0100"


def test_one_centre_one_rate() -> None:
    row = revision(vat_lines=[{"vat_rate": "21", "taxable_base": "100.00", "vat_amount": "21.00"}], total="121.00")
    values = item_values(generate_invoice_xml(row, [allocation(row, "100", "121.00")]))
    assert values == [("100", Decimal("100.00"), Decimal("21.00"))]


def test_multiple_centres_one_rate_reconstructs_exact_totals() -> None:
    row = revision(vat_lines=[{"vat_rate": "21", "taxable_base": "1000.00", "vat_amount": "210.00"}], total="1210.00")
    values = item_values(
        generate_invoice_xml(row, [allocation(row, "200", "700.00"), allocation(row, "300", "510.00")])
    )
    assert len(values) == 2
    assert [(code, base + vat) for code, base, vat in values] == [
        ("200", Decimal("700.00")),
        ("300", Decimal("510.00")),
    ]
    assert sum((base for _, base, _ in values), Decimal("0")) == Decimal("1000.00")
    assert sum((vat for _, _, vat in values), Decimal("0")) == Decimal("210.00")


def test_one_centre_multiple_rates() -> None:
    row = revision(
        vat_lines=[
            {"vat_rate": "21", "taxable_base": "100.00", "vat_amount": "21.00"},
            {"vat_rate": "12", "taxable_base": "50.00", "vat_amount": "6.00"},
        ],
        total="177.00",
    )
    values = item_values(generate_invoice_xml(row, [allocation(row, "100", "177.00")]))
    assert values == [
        ("100", Decimal("100.00"), Decimal("21.00")),
        ("100", Decimal("50.00"), Decimal("6.00")),
    ]


def test_multiple_centres_multiple_rates_require_and_use_explicit_split() -> None:
    row = revision(
        vat_lines=[
            {"vat_rate": "21", "taxable_base": "100.00", "vat_amount": "21.00"},
            {"vat_rate": "12", "taxable_base": "50.00", "vat_amount": "6.00"},
        ],
        total="177.00",
    )
    with pytest.raises(PohodaMappingError, match="EXPLICIT_VAT_SPLIT"):
        generate_invoice_xml(row, [allocation(row, "200", "121.00"), allocation(row, "300", "56.00")])

    xml = generate_invoice_xml(
        row,
        [
            allocation(row, "200", "121.00", vat_breakdown=[{"rate": "21", "base": "100", "vat": "21"}]),
            allocation(row, "300", "56.00", vat_breakdown=[{"rate": "12", "base": "50", "vat": "6"}]),
        ],
    )
    assert item_values(xml) == [
        ("200", Decimal("100.00"), Decimal("21.00")),
        ("300", Decimal("50.00"), Decimal("6.00")),
    ]


def test_largest_remainder_is_stable_and_exact() -> None:
    row = revision(vat_lines=[{"vat_rate": "21", "taxable_base": "0.83", "vat_amount": "0.17"}], total="1.00")
    values = item_values(
        generate_invoice_xml(
            row,
            [allocation(row, "100", "0.34"), allocation(row, "200", "0.33"), allocation(row, "300", "0.33")],
        )
    )
    assert [(base, vat) for _, base, vat in values] == [
        (Decimal("0.28"), Decimal("0.06")),
        (Decimal("0.28"), Decimal("0.05")),
        (Decimal("0.27"), Decimal("0.06")),
    ]
    assert sum((base + vat for _, base, vat in values), Decimal("0")) == Decimal("1.00")


@pytest.mark.parametrize("mutation", ["mandatory", "order", "datatype", "enum", "namespace"])
def test_xsd_negative_cases(mutation: str) -> None:
    row = revision(vat_lines=[{"vat_rate": "21", "taxable_base": "100", "vat_amount": "21"}], total="121")
    root = document(generate_invoice_xml(row, [allocation(row, "IT", "121")]))
    header = root.xpath("//inv:invoiceHeader", namespaces=NS)[0]
    invoice = root.xpath("//inv:invoice", namespaces=NS)[0]
    if mutation == "mandatory":
        header.remove(header.xpath("inv:invoiceType", namespaces=NS)[0])
    elif mutation == "order":
        detail = invoice.xpath("inv:invoiceDetail", namespaces=NS)[0]
        invoice.remove(detail)
        invoice.insert(0, detail)
    elif mutation == "datatype":
        header.xpath("inv:date", namespaces=NS)[0].text = "01.08.2026"
    elif mutation == "enum":
        header.xpath("inv:invoiceType", namespaces=NS)[0].text = "notAnInvoice"
    else:
        root.tag = "{urn:invalid}dataPack"
    invalid = etree.tostring(root, xml_declaration=True, encoding="Windows-1250")
    assert validate_xml_detailed(invalid, XSD)


def test_official_response_parser() -> None:
    sample = XSD.parent / "samples" / "received-invoice-response.xml"
    parsed = parse_pohoda_response(sample.read_bytes(), response_xsd_path=RESPONSE_XSD)
    assert parsed["id"] == "fa002"
    assert parsed["state"] == "ok"
    assert parsed["xsd_valid"] is True
    assert parsed["items"][0]["id"] == "POL001"
    assert parsed["items"][0]["produced_details"][0]["id"] == "98"
