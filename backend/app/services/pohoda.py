from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from lxml import etree

from app.models import Allocation, InvoiceRevision
from app.services.allocations import allocate_proportionally

NS_DATA = "http://www.stormware.cz/schema/version_2/data.xsd"
NS_INV = "http://www.stormware.cz/schema/version_2/invoice.xsd"
NS_TYP = "http://www.stormware.cz/schema/version_2/type.xsd"
NSMAP = {"dat": NS_DATA, "inv": NS_INV, "typ": NS_TYP}


class PohodaMappingError(ValueError):
    pass


def _q(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _text(parent: etree._Element, namespace: str, name: str, value: Any | None) -> etree._Element | None:
    if value in (None, ""):
        return None
    child = etree.SubElement(parent, _q(namespace, name))
    child.text = str(value)
    return child


def _rate_name(rate: Decimal) -> str:
    if rate == 0:
        return "none"
    if rate == 21:
        return "high"
    if rate == 12:
        return "low"
    raise PohodaMappingError(f"Unsupported VAT rate {rate}; add an explicit tested mapping")


def _split_address(value: str | None) -> tuple[str | None, str | None, str | None]:
    if not value:
        return None, None, None
    parts = [part.strip() for part in value.split(",") if part.strip()]
    street = parts[0] if parts else None
    city = parts[-1] if len(parts) > 1 else None
    return street, city, None


def _vat_allocations(
    data: dict[str, Any], allocations: list[Allocation]
) -> list[list[tuple[Decimal, Decimal, Decimal]]]:
    weights = [Decimal(allocation.amount) for allocation in allocations]
    rows = data.get("vat_breakdown") or []
    result: list[list[tuple[Decimal, Decimal, Decimal]]] = [[] for _ in allocations]
    if not rows:
        for index, allocation in enumerate(allocations):
            result[index].append((Decimal(allocation.amount), Decimal("0"), Decimal("0")))
        return result

    gross_from_rows = sum(
        (Decimal(str(row["base"])) + Decimal(str(row["vat"])) for row in rows), Decimal("0")
    )
    allocation_total = sum(weights, Decimal("0"))
    if abs(gross_from_rows - allocation_total) > Decimal("0.02"):
        raise PohodaMappingError("VAT breakdown does not equal allocation total")

    for row in rows:
        base_parts = allocate_proportionally(Decimal(str(row["base"])), weights)
        vat_parts = allocate_proportionally(Decimal(str(row["vat"])), weights)
        rate = Decimal(str(row["rate"]))
        for index in range(len(allocations)):
            result[index].append((base_parts[index], vat_parts[index], rate))
    return result


def generate_invoice_xml(
    revision: InvoiceRevision,
    allocations: list[Allocation],
    *,
    accounting_unit_ico: str | None = None,
) -> bytes:
    if not allocations:
        raise PohodaMappingError("At least one allocation is required")
    if any(not allocation.cost_center for allocation in allocations):
        raise PohodaMappingError("Every allocation needs a cost center")
    data = revision.data
    currency = str(data.get("currency") or "CZK").upper()
    if currency != "CZK" and not data.get("exchange_rate"):
        raise PohodaMappingError("Foreign currency requires a reviewed exchange_rate")

    root = etree.Element(_q(NS_DATA, "dataPack"), nsmap=NSMAP)
    root.set("version", "2.0")
    root.set("id", f"invoice-{revision.invoice_id}-r{revision.number}")
    root.set("application", "paperless-invoice-approval")
    root.set("note", "Ruční import schválené přijaté faktury")
    if accounting_unit_ico:
        root.set("ico", accounting_unit_ico)

    pack_item = etree.SubElement(root, _q(NS_DATA, "dataPackItem"))
    pack_item.set("version", "2.0")
    pack_item.set("id", f"inv-{revision.invoice_id}-r{revision.number}")
    invoice = etree.SubElement(pack_item, _q(NS_INV, "invoice"))
    invoice.set("version", "2.0")
    header = etree.SubElement(invoice, _q(NS_INV, "invoiceHeader"))
    _text(header, NS_INV, "invoiceType", "receivedInvoice")
    _text(header, NS_INV, "symVar", data.get("variable_symbol"))
    _text(header, NS_INV, "originalDocument", data.get("invoice_number"))
    _text(header, NS_INV, "date", data.get("issue_date"))
    _text(header, NS_INV, "dateTax", data.get("taxable_supply_date"))
    _text(header, NS_INV, "dateDue", data.get("due_date"))
    _text(header, NS_INV, "text", data.get("description") or f"Přijatá faktura {data.get('invoice_number', '')}")

    partner = etree.SubElement(header, _q(NS_INV, "partnerIdentity"))
    address = etree.SubElement(partner, _q(NS_TYP, "address"))
    _text(address, NS_TYP, "company", data.get("supplier_name"))
    street, city, zip_code = _split_address(data.get("address"))
    _text(address, NS_TYP, "street", street)
    _text(address, NS_TYP, "city", city)
    _text(address, NS_TYP, "zip", zip_code)
    _text(address, NS_TYP, "ico", data.get("ico"))
    _text(address, NS_TYP, "dic", data.get("dic"))

    detail = etree.SubElement(invoice, _q(NS_INV, "invoiceDetail"))
    split_rows = _vat_allocations(data, allocations)
    for allocation, allocation_rows in zip(allocations, split_rows, strict=True):
        for base, vat, rate in allocation_rows:
            item = etree.SubElement(detail, _q(NS_INV, "invoiceItem"))
            suffix = f" / DPH {rate}%" if len(allocation_rows) > 1 else ""
            _text(item, NS_INV, "text", f"{data.get('description') or 'Přijatá faktura'} / {allocation.cost_center.code}{suffix}"[:90])
            _text(item, NS_INV, "quantity", "1")
            _text(item, NS_INV, "payVAT", "false")
            _text(item, NS_INV, "rateVAT", _rate_name(rate))
            if rate not in {Decimal("0"), Decimal("12"), Decimal("21")}:
                _text(item, NS_INV, "percentVAT", rate)
            money_tag = "homeCurrency" if currency == "CZK" else "foreignCurrency"
            money = etree.SubElement(item, _q(NS_INV, money_tag))
            _text(money, NS_TYP, "unitPrice", base)
            _text(money, NS_TYP, "price", base)
            _text(money, NS_TYP, "priceVAT", vat)
            _text(money, NS_TYP, "priceSum", base + vat)
            centre = etree.SubElement(item, _q(NS_INV, "centre"))
            _text(centre, NS_TYP, "ids", allocation.cost_center.pohoda_code)

    if currency != "CZK":
        summary = etree.SubElement(invoice, _q(NS_INV, "invoiceSummary"))
        foreign = etree.SubElement(summary, _q(NS_INV, "foreignCurrency"))
        currency_ref = etree.SubElement(foreign, _q(NS_TYP, "currency"))
        _text(currency_ref, NS_TYP, "ids", currency)
        _text(foreign, NS_TYP, "rate", data["exchange_rate"])
        _text(foreign, NS_TYP, "amount", data.get("exchange_rate_amount", 1))
        _text(foreign, NS_TYP, "priceSum", data["total_amount"])

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def validate_xml(xml_bytes: bytes, xsd_path: Path) -> list[str]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        schema_document = etree.parse(str(xsd_path), parser)
        schema = etree.XMLSchema(schema_document)
        document = etree.fromstring(xml_bytes, parser)
    except (etree.XMLSyntaxError, etree.XMLSchemaParseError) as exc:
        return [str(exc)]
    if schema.validate(document):
        return []
    return [str(error) for error in schema.error_log]
