from decimal import Decimal

from lxml import etree

from app.models import Allocation, CostCenter, InvoiceRevision, ValidationSeverity
from app.schemas import InvoiceExtractionV1
from app.services.extraction import extraction_to_invoice_data
from app.services.pohoda import NS_INV, NS_TYP, generate_invoice_xml
from app.services.validation import validate_invoice_data


def evidence(value, source):
    return {"value": value, "source_text": source if value is not None else None}


def giriton_payload() -> dict:
    supplier_block = (
        "DODAVATEL GIRITON Systems s.r.o. Hornosušská 1399/4 "
        "735 64 Havířov - Prostřední Suchá"
    )
    return {
        "schema_version": "invoice-extraction.v2",
        "supplier_name": evidence("GIRITON Systems s.r.o.", supplier_block),
        "supplier_ico": evidence("28652240", "IČ 28652240"),
        "supplier_dic": evidence("CZ28652240", "DIČ CZ28652240"),
        "supplier_address_raw": evidence(
            "Hornosušská 1399/4 73564 Havířov - Prostřední Suchá", supplier_block
        ),
        "supplier_street": evidence(None, None),
        "supplier_city": evidence(None, None),
        "supplier_zip": evidence(None, None),
        "invoice_number": evidence("25081151", "Faktura číslo 25081151"),
        "variable_symbol": evidence("25081151", "Variabilní symbol 25081151"),
        "issue_date": evidence("2025-09-05", "Datum vystavení 5. 9. 2025"),
        "taxable_supply_date": evidence("2025-08-31", "DUZP 31. 8. 2025"),
        "due_date": evidence("2025-09-20", "Splatnost 20. 9. 2025"),
        "currency": evidence("CZK", "Kč"),
        "bank_account": evidence("2300122535/2010", "2300122535/2010"),
        "bank_code": evidence(None, None),
        "iban": evidence(None, None),
        "swift_bic": evidence(None, None),
        "vat_lines": [
            {
                "vat_rate": "21",
                "taxable_base": "4065.00",
                "vat_amount": "853.65",
                "adjustment_type": None,
                "source_text": "Základ 4 065,00 DPH 21 % 853,65 Celkem 4 918,65",
            },
            {
                "vat_rate": "21",
                "taxable_base": "0.29",
                "vat_amount": "0.06",
                "adjustment_type": "ROUNDING",
                "source_text": "Zaokrouhlení 0,29 0,06 0,35",
            },
        ],
        "total_without_vat": evidence("4065.29", "Základ celkem 4 065,29"),
        "total_vat": evidence("853.71", "DPH celkem 853,71"),
        "total_amount": evidence("4919.00", "Celkem k úhradě 4 919,00"),
        "description": evidence("Softwarové služby", "Softwarové služby"),
    }


def test_giriton_address_rounding_and_declared_totals_are_preserved() -> None:
    data = extraction_to_invoice_data(InvoiceExtractionV1.model_validate(giriton_payload()))

    assert data["supplier_address_raw"] == (
        "Hornosušská 1399/4 735 64 Havířov - Prostřední Suchá"
    )
    assert data["supplier_street"] == "Hornosušská 1399/4"
    assert data["supplier_zip"] == "735 64"
    assert data["supplier_city"] == "Havířov - Prostřední Suchá"
    assert data["bank_account_number"] == "2300122535"
    assert data["bank_code"] == "2010"
    assert data["total_without_vat"] == "4065.29"
    assert data["total_vat"] == "853.71"
    assert data["total_amount"] == "4919.00"
    assert data["vat_lines"][0]["vat_amount"] == "853.65"
    assert data["vat_lines"][1]["vat_amount"] == "0.06"
    assert data["vat_lines"][1]["adjustment_type"] == "ROUNDING"

    validations = validate_invoice_data(data)
    vat_results = [row for row in validations if row.code.startswith("VAT_")]
    assert any(row.code == "VAT_ROUNDING_ADJUSTMENT" for row in vat_results)
    assert not [row for row in vat_results if row.severity == ValidationSeverity.BLOCKING_ERROR]


def test_giriton_pohoda_xml_uses_structured_address_and_reconciled_vat() -> None:
    data = extraction_to_invoice_data(InvoiceExtractionV1.model_validate(giriton_payload()))
    revision = InvoiceRevision(
        invoice_id="00000000-0000-0000-0000-000000000151", number=1, data=data
    )
    centre = CostCenter(code="200", name="Test", pohoda_code="200")
    allocation = Allocation(
        invoice_id=revision.invoice_id,
        revision_id=revision.id,
        cost_center_id=centre.id,
        amount=Decimal("4919.00"),
        vat_breakdown=[],
        cost_center=centre,
    )
    xml = generate_invoice_xml(
        revision, [allocation], accounting_unit_ico="15049248"
    )
    root = etree.fromstring(xml)
    namespaces = {"inv": NS_INV, "typ": NS_TYP}
    address = root.xpath("//inv:partnerIdentity/typ:address", namespaces=namespaces)[0]
    assert address.xpath("string(typ:street)", namespaces=namespaces) == "Hornosušská 1399/4"
    assert address.xpath("string(typ:city)", namespaces=namespaces) == "Havířov - Prostřední Suchá"
    assert address.xpath("string(typ:zip)", namespaces=namespaces) == "735 64"
    assert root.xpath("string(//inv:paymentAccount/typ:accountNo)", namespaces=namespaces) == "2300122535"
    assert root.xpath("string(//inv:paymentAccount/typ:bankCode)", namespaces=namespaces) == "2010"
    assert root.xpath("string(//inv:invoiceItem/inv:homeCurrency/typ:price)", namespaces=namespaces) == "4065.29"
    assert root.xpath("string(//inv:invoiceItem/inv:homeCurrency/typ:priceVAT)", namespaces=namespaces) == "853.71"


def test_vat_row_reconciliation_difference_is_warning_not_blocking() -> None:
    payload = giriton_payload()
    payload["vat_lines"] = [
        {
            "vat_rate": "21",
            "taxable_base": "4065.00",
            "vat_amount": "853.71",
            "adjustment_type": None,
            "source_text": "DPH 21 %: 853,71",
        }
    ]
    results = validate_invoice_data(
        extraction_to_invoice_data(InvoiceExtractionV1.model_validate(payload))
    )
    mismatch = next(row for row in results if row.code == "VAT_ROW_MATH")
    assert mismatch.severity == ValidationSeverity.WARNING
    assert mismatch.expected == "853.65"
    assert mismatch.actual == "853.71"
    assert mismatch.details["difference"] == "0.06"
