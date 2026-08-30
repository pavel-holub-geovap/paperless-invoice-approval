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
    generate_invoice_xml,
    parse_pohoda_response,
    validate_pohoda_target_unit,
    validate_xml_detailed,
)

ROOT = Path(__file__).resolve().parents[2]
XSD = ROOT / "schemas" / "pohoda" / "2025-10-16" / "data.xsd"
RESPONSE_XSD = XSD.with_name("response.xsd")
NS = {"dat": NS_DATA, "inv": NS_INV, "typ": NS_TYP}
TARGET_ICO = "15049248"


def revision(*, vat_lines: list[dict[str, str]], total: str) -> InvoiceRevision:
    return InvoiceRevision(
        invoice_id="00000000-0000-0000-0000-000000000001",
        number=1,
        data={
            "supplier_name": "Řešení ščěřžýáíé s.r.o.",
            "supplier_ico": "28652240",
            "supplier_dic": "CZ28652240",
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
    xml = generate_invoice_xml(row, [allocation(row, "IT", "121.00")], accounting_unit_ico=TARGET_ICO)
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
    assert address.xpath("string(typ:ico)", namespaces=NS) == "28652240"
    assert address.xpath("string(typ:dic)", namespaces=NS) == "CZ28652240"
    assert root.xpath("string(//inv:paymentAccount/typ:accountNo)", namespaces=NS) == "123456789"
    assert root.xpath("string(//inv:paymentAccount/typ:bankCode)", namespaces=NS) == "0100"
    assert root.get("ico") == TARGET_ICO
    assert root.get("key") is None
    assert root.get("ico") != address.xpath("string(typ:ico)", namespaces=NS)


def test_gmtech_dates_remain_iso_in_pohoda_xml() -> None:
    row = revision(
        vat_lines=[{"vat_rate": "21", "taxable_base": "100.00", "vat_amount": "21.00"}],
        total="121.00",
    )
    row.data.update(
        {
            "issue_date": "2026-07-08",
            "taxable_supply_date": "2026-06-30",
            "due_date": "2026-08-07",
        }
    )
    xml = generate_invoice_xml(
        row,
        [allocation(row, "IT", "121.00")],
        accounting_unit_ico=TARGET_ICO,
    )
    root = document(xml)

    assert validate_xml_detailed(xml, XSD) == []
    assert root.xpath("string(//inv:date)", namespaces=NS) == "2026-07-08"
    assert root.xpath("string(//inv:dateTax)", namespaces=NS) == "2026-06-30"
    assert root.xpath("string(//inv:dateDue)", namespaces=NS) == "2026-08-07"
    assert b"30.06.2026" not in xml


def test_xsd_validity_does_not_replace_target_unit_semantic_validation() -> None:
    row = revision(
        vat_lines=[{"vat_rate": "21", "taxable_base": "100.00", "vat_amount": "21.00"}],
        total="121.00",
    )
    root = document(
        generate_invoice_xml(
            row,
            [allocation(row, "IT", "121.00")],
            accounting_unit_ico=TARGET_ICO,
        )
    )
    del root.attrib["ico"]
    xml_without_target = etree.tostring(
        root, xml_declaration=True, encoding="Windows-1250"
    )

    assert validate_xml_detailed(xml_without_target, XSD) == []
    target = validate_pohoda_target_unit(
        xml_without_target,
        expected_ico=TARGET_ICO,
    )
    assert target["status"] == "TARGET_UNIT_INVALID"
    assert target["actual_ico"] is None
    assert "serialized value is missing" in target["errors"][0]


def test_allocations_are_informational_and_do_not_create_accounting_centres() -> None:
    row = revision(vat_lines=[{"vat_rate": "21", "taxable_base": "1000.00", "vat_amount": "210.00"}], total="1210.00")
    xml = generate_invoice_xml(
        row,
        [allocation(row, "200", "700.00"), allocation(row, "300", "510.00")],
        accounting_unit_ico=TARGET_ICO,
    )
    root = document(xml)

    assert validate_xml_detailed(xml, XSD) == []
    assert root.xpath("//inv:centre", namespaces=NS) == []
    assert item_values(xml) == [("", Decimal("1000.00"), Decimal("210.00"))]
    note = root.xpath("string(//inv:invoiceHeader/inv:text)", namespaces=NS)
    assert "200 - 700,00 CZK" in note
    assert "300 - 510,00 CZK" in note
    assert "Finální účetní rozúčtování provádí účetní" in note


def test_pixel_summary_vat_row_exports_once_without_false_rounding_item() -> None:
    row = revision(
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
        total="5203.00",
    )

    xml = generate_invoice_xml(
        row,
        [allocation(row, "200", "5203.00")],
        accounting_unit_ico=TARGET_ICO,
    )

    assert item_values(xml) == [("", Decimal("4300.00"), Decimal("903.00"))]


def test_actual_extracted_invoice_items_take_precedence_over_approval_allocations() -> None:
    row = revision(
        vat_lines=[
            {"vat_rate": "21", "taxable_base": "100.00", "vat_amount": "21.00"},
            {"vat_rate": "12", "taxable_base": "50.00", "vat_amount": "6.00"},
        ],
        total="177.00",
    )
    row.data["invoice_items"] = [
        {"description": "Licence", "quantity": "1", "unit_price": "100", "line_extension_amount": "100", "vat_rate": "21"},
        {"description": "Služba", "quantity": "1", "unit_price": "50", "line_extension_amount": "50", "vat_rate": "12"},
    ]
    xml = generate_invoice_xml(
        row,
        [allocation(row, "200", "121.00"), allocation(row, "300", "56.00")],
        accounting_unit_ico=TARGET_ICO,
    )
    assert item_values(xml) == [
        ("", Decimal("100.00"), Decimal("21.00")),
        ("", Decimal("50.00"), Decimal("6.00")),
    ]


@pytest.mark.parametrize("mutation", ["mandatory", "order", "datatype", "enum", "namespace"])
def test_xsd_negative_cases(mutation: str) -> None:
    row = revision(vat_lines=[{"vat_rate": "21", "taxable_base": "100", "vat_amount": "21"}], total="121")
    root = document(generate_invoice_xml(row, [allocation(row, "IT", "121")], accounting_unit_ico=TARGET_ICO))
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
