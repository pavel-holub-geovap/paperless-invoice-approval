from __future__ import annotations

import hashlib
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from lxml import etree

from app.models import Allocation, InvoiceRevision
from app.services.allocations import allocate_proportionally
from app.services.bank_accounts import normalize_payment_data

NS_DATA = "http://www.stormware.cz/schema/version_2/data.xsd"
NS_INV = "http://www.stormware.cz/schema/version_2/invoice.xsd"
NS_TYP = "http://www.stormware.cz/schema/version_2/type.xsd"
NS_RESPONSE = "http://www.stormware.cz/schema/version_2/response.xsd"
NSMAP = {"dat": NS_DATA, "inv": NS_INV, "typ": NS_TYP}
CENT = Decimal("0.01")


class PohodaMappingError(ValueError):
    pass


def validate_pohoda_target_unit(
    xml_bytes: bytes,
    *,
    expected_ico: str,
    key_configured: bool = False,
    expected_key_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate the serialized dataPack target independently from its XSD."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    errors: list[str] = []
    actual_ico: str | None = None
    actual_key: str | None = None
    try:
        root = etree.fromstring(xml_bytes, parser)
        if root.tag != _q(NS_DATA, "dataPack"):
            errors.append("XML root is not dat:dataPack")
        actual_ico = root.get("ico")
        actual_key = root.get("key")
    except etree.XMLSyntaxError as exc:
        errors.append(f"Serialized XML cannot be parsed: {exc}")

    if not expected_ico:
        errors.append("POHODA_TARGET_ICO is not configured")
    elif actual_ico != expected_ico:
        errors.append(
            f"dat:dataPack/@ico must equal POHODA_TARGET_ICO {expected_ico}; "
            f"serialized value is {actual_ico or 'missing'}"
        )
    if key_configured:
        if not actual_key:
            errors.append("dat:dataPack/@key is missing although POHODA_TARGET_KEY is configured")
        elif expected_key_sha256 and hashlib.sha256(actual_key.encode()).hexdigest() != expected_key_sha256:
            errors.append("dat:dataPack/@key differs from the configured POHODA_TARGET_KEY")
    elif actual_key is not None:
        errors.append("dat:dataPack/@key must be absent when POHODA_TARGET_KEY is not configured")

    return {
        "status": "TARGET_UNIT_INVALID" if errors else "TARGET_UNIT_VALID",
        "expected_ico": expected_ico or None,
        "actual_ico": actual_ico,
        "key_configured": key_configured,
        "actual_key_present": actual_key is not None,
        "errors": errors,
    }


def _q(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _text(
    parent: etree._Element, namespace: str, name: str, value: Any | None
) -> etree._Element | None:
    if value in (None, ""):
        return None
    child = etree.SubElement(parent, _q(namespace, name))
    child.text = str(value)
    return child


def _decimal(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value)).quantize(CENT)
    except Exception as exc:
        raise PohodaMappingError(f"{field} is not a valid decimal amount") from exc


def _money(value: Decimal) -> str:
    return format(value.quantize(CENT), ".2f")


def _rate_name(rate: Decimal) -> str:
    if rate == 0:
        return "none"
    if rate == 21:
        return "high"
    if rate == 12:
        return "low"
    raise PohodaMappingError(
        f"Unsupported VAT rate {rate}; an explicit tested POHODA mapping is required"
    )


def _vat_rows(data: dict[str, Any]) -> list[dict[str, str]]:
    source = data.get("vat_lines") or data.get("vat_breakdown") or []
    by_rate: dict[Decimal, dict[str, Decimal]] = defaultdict(
        lambda: {"base": Decimal("0"), "vat": Decimal("0")}
    )
    for row in source:
        rate = row.get("vat_rate", row.get("rate"))
        base = row.get("taxable_base", row.get("base"))
        vat = row.get("vat_amount", row.get("vat"))
        if rate is None or base is None or vat is None:
            raise PohodaMappingError("Every VAT row requires rate, taxable base, and VAT amount")
        normalized_rate = Decimal(str(rate))
        by_rate[normalized_rate]["base"] += _decimal(base, "VAT base")
        by_rate[normalized_rate]["vat"] += _decimal(vat, "VAT amount")
    rows = [
        {"rate": str(rate), "base": _money(values["base"]), "vat": _money(values["vat"])}
        for rate, values in by_rate.items()
    ]
    if not rows:
        total = _decimal(data.get("total_amount"), "total_amount")
        rows.append({"rate": "0", "base": _money(total), "vat": "0.00"})
    return rows


def build_source_snapshot(
    revision: InvoiceRevision,
    allocations: list[Allocation],
) -> dict[str, Any]:
    return {
        "invoice_id": revision.invoice_id,
        "revision_id": revision.id,
        "revision_number": revision.number,
        "data": dict(revision.data),
        "allocations": [
            {
                "id": row.id,
                "cost_center_code": row.cost_center.code,
                "pohoda_code": row.cost_center.pohoda_code,
                "amount": _money(Decimal(row.amount)),
                "percentage": str(row.percentage) if row.percentage is not None else None,
                "note": row.note,
                "vat_breakdown": list(row.vat_breakdown),
            }
            for row in allocations
        ],
    }


def _split_vat(snapshot: dict[str, Any]) -> list[list[dict[str, Decimal]]]:
    allocations = snapshot["allocations"]
    data = snapshot["data"]
    invoice_rows = _vat_rows(data)
    weights = [_decimal(row["amount"], "allocation amount") for row in allocations]
    allocation_total = sum(weights, Decimal("0"))
    invoice_total = _decimal(data.get("total_amount"), "total_amount")
    vat_total = sum(
        (_decimal(row["base"], "VAT base") + _decimal(row["vat"], "VAT amount") for row in invoice_rows),
        Decimal("0"),
    )
    if abs(allocation_total - invoice_total) > CENT:
        raise PohodaMappingError("Allocation total does not equal invoice total")
    if abs(vat_total - invoice_total) > CENT:
        raise PohodaMappingError("VAT breakdown does not equal invoice total")

    result: list[list[dict[str, Decimal]]] = [[] for _ in allocations]
    if len(allocations) == 1:
        result[0] = [
            {
                "rate": Decimal(row["rate"]),
                "base": _decimal(row["base"], "VAT base"),
                "vat": _decimal(row["vat"], "VAT amount"),
            }
            for row in invoice_rows
        ]
        return result

    if len(invoice_rows) == 1:
        row = invoice_rows[0]
        base_parts = allocate_proportionally(_decimal(row["base"], "VAT base"), weights)
        rate = Decimal(row["rate"])
        for index, allocation_amount in enumerate(weights):
            result[index].append(
                {
                    "rate": rate,
                    "base": base_parts[index],
                    "vat": allocation_amount - base_parts[index],
                }
            )
        return result

    # Multiple VAT rates across multiple centres are an accounting decision. The
    # generator accepts only an explicit, manager-reviewed split for every allocation.
    aggregate: dict[Decimal, dict[str, Decimal]] = defaultdict(
        lambda: {"base": Decimal("0"), "vat": Decimal("0")}
    )
    for index, allocation in enumerate(allocations):
        explicit = allocation.get("vat_breakdown") or []
        if not explicit:
            raise PohodaMappingError(
                "MULTI_RATE_ALLOCATION_REQUIRES_EXPLICIT_VAT_SPLIT"
            )
        allocation_rows: list[dict[str, Decimal]] = []
        for row in explicit:
            normalized = {
                "rate": Decimal(str(row["rate"])),
                "base": _decimal(row["base"], "allocation VAT base"),
                "vat": _decimal(row["vat"], "allocation VAT amount"),
            }
            allocation_rows.append(normalized)
            aggregate[normalized["rate"]]["base"] += normalized["base"]
            aggregate[normalized["rate"]]["vat"] += normalized["vat"]
        gross = sum((row["base"] + row["vat"] for row in allocation_rows), Decimal("0"))
        if abs(gross - weights[index]) > CENT:
            raise PohodaMappingError("Explicit allocation VAT split does not equal allocation amount")
        result[index] = allocation_rows

    expected = {
        Decimal(row["rate"]): {
            "base": _decimal(row["base"], "VAT base"),
            "vat": _decimal(row["vat"], "VAT amount"),
        }
        for row in invoice_rows
    }
    if set(aggregate) != set(expected) or any(
        abs(aggregate[rate][key] - expected[rate][key]) > CENT
        for rate in expected
        for key in ("base", "vat")
    ):
        raise PohodaMappingError(
            "Explicit allocation VAT split does not reconstruct the invoice VAT breakdown"
        )
    return result


class PohodaInvoiceXmlGenerator:
    def __init__(self, *, encoding: str = "Windows-1250") -> None:
        self.encoding = encoding

    def generate(
        self,
        snapshot: dict[str, Any],
        *,
        accounting_unit_ico: str,
        accounting_unit_key: str | None = None,
    ) -> bytes:
        if not accounting_unit_ico:
            raise PohodaMappingError(
                "Cílová účetní jednotka POHODA není nakonfigurována (POHODA_TARGET_ICO)."
            )
        allocations = snapshot.get("allocations") or []
        data = snapshot["data"]
        currency = str(data.get("currency") or "CZK").upper()
        if currency != "CZK" and not data.get("exchange_rate"):
            raise PohodaMappingError("Foreign currency requires a reviewed exchange_rate")

        required = {
            "supplier_name": data.get("supplier_name"),
            "supplier_street": data.get("supplier_street"),
            "supplier_city": data.get("supplier_city"),
            "supplier_zip": data.get("supplier_zip"),
            "invoice_number": data.get("invoice_number"),
            "issue_date": data.get("issue_date"),
            "total_amount": data.get("total_amount"),
        }
        missing = [name for name, value in required.items() if value in (None, "")]
        if missing:
            raise PohodaMappingError("Missing reviewed export fields: " + ", ".join(missing))

        root = etree.Element(_q(NS_DATA, "dataPack"), nsmap=NSMAP)
        root.set("version", "2.0")
        root.set("id", f"invoice-{snapshot['invoice_id']}-r{snapshot['revision_number']}")
        root.set("application", "paperless-invoice-approval")
        root.set("note", "Ruční import schválené přijaté faktury")
        root.set("ico", accounting_unit_ico)
        if accounting_unit_key:
            root.set("key", accounting_unit_key)

        pack_item = etree.SubElement(root, _q(NS_DATA, "dataPackItem"))
        pack_item.set("version", "2.0")
        pack_item.set("id", f"inv-{snapshot['invoice_id']}-r{snapshot['revision_number']}")
        invoice = etree.SubElement(pack_item, _q(NS_INV, "invoice"))
        invoice.set("version", "2.0")
        header = etree.SubElement(invoice, _q(NS_INV, "invoiceHeader"))
        _text(header, NS_INV, "invoiceType", "receivedInvoice")
        _text(header, NS_INV, "symVar", data.get("variable_symbol"))
        _text(header, NS_INV, "originalDocument", data.get("invoice_number"))
        _text(header, NS_INV, "date", data.get("issue_date"))
        _text(header, NS_INV, "dateTax", data.get("taxable_supply_date"))
        _text(header, NS_INV, "dateDue", data.get("due_date"))
        allocation_summary = []
        for row in allocations:
            amount = f"{Decimal(str(row['amount'])):,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
            allocation_summary.append(
                f"středisko {row['cost_center_code']} - {amount} {currency}"
            )
        handoff = "Finální účetní rozúčtování provádí účetní."
        base_text = str(
            data.get("description") or f"Přijatá faktura {data.get('invoice_number', '')}"
        )[:60]
        if allocation_summary:
            prefix = f"{base_text}. Interní schválení: "
            available = max(0, 240 - len(prefix) - len(handoff) - 2)
            summary = "; ".join(allocation_summary)[:available].rstrip(" ;")
            header_text = f"{prefix}{summary}. {handoff}"
        else:
            header_text = f"{base_text}. {handoff}"
        _text(header, NS_INV, "text", header_text)

        partner = etree.SubElement(header, _q(NS_INV, "partnerIdentity"))
        address = etree.SubElement(partner, _q(NS_TYP, "address"))
        address.set("linkToAddress", "false")
        _text(address, NS_TYP, "company", data.get("supplier_name"))
        _text(address, NS_TYP, "street", data.get("supplier_street"))
        _text(address, NS_TYP, "city", data.get("supplier_city"))
        _text(address, NS_TYP, "zip", data.get("supplier_zip"))
        _text(address, NS_TYP, "ico", data.get("supplier_ico", data.get("ico")))
        _text(address, NS_TYP, "dic", data.get("supplier_dic", data.get("dic")))

        data = normalize_payment_data(data)
        iban = str(data.get("iban") or "").replace(" ", "")
        bic = str(data.get("swift_bic") or "").replace(" ", "")
        domestic_account = str(data.get("bank_account") or "").replace(" ", "")
        domestic_code = str(data.get("bank_code") or "").replace(" ", "")
        account_no: str | None = None
        bank_code: str | None = None
        if iban and bic:
            account_no, bank_code = iban, bic
        elif domestic_account and domestic_code:
            account_no, bank_code = domestic_account, domestic_code
        elif any((iban, bic, domestic_account, domestic_code)):
            raise PohodaMappingError("Reviewed payment account is incomplete for POHODA paymentAccount")
        if account_no and bank_code:
            payment_account = etree.SubElement(header, _q(NS_INV, "paymentAccount"))
            _text(payment_account, NS_TYP, "accountNo", account_no)
            _text(payment_account, NS_TYP, "bankCode", bank_code)

        detail = etree.SubElement(invoice, _q(NS_INV, "invoiceDetail"))
        extracted_items = data.get("invoice_items") or []
        if extracted_items and all(
            row.get("line_extension_amount") not in (None, "")
            and row.get("vat_rate") not in (None, "")
            for row in extracted_items
        ):
            detail_rows = [
                {
                    "text": row.get("description") or "Položka přijaté faktury",
                    "quantity": row.get("quantity") or "1",
                    "base": Decimal(str(row["line_extension_amount"])),
                    "vat": Decimal(str(row["line_extension_amount"]))
                    * Decimal(str(row["vat_rate"])) / Decimal("100"),
                    "rate": Decimal(str(row["vat_rate"])),
                }
                for row in extracted_items
            ]
        else:
            detail_rows = [
                {
                    "text": data.get("description") or "Přijatá faktura",
                    "quantity": "1",
                    "base": _decimal(row["base"], "VAT base"),
                    "vat": _decimal(row["vat"], "VAT amount"),
                    "rate": Decimal(str(row["rate"])),
                }
                for row in _vat_rows(data)
            ]
        for row in detail_rows:
            item = etree.SubElement(detail, _q(NS_INV, "invoiceItem"))
            _text(item, NS_INV, "text", str(row["text"])[:90])
            _text(item, NS_INV, "quantity", row["quantity"])
            _text(item, NS_INV, "payVAT", "false")
            _text(item, NS_INV, "rateVAT", _rate_name(row["rate"]))
            money_tag = "homeCurrency" if currency == "CZK" else "foreignCurrency"
            money = etree.SubElement(item, _q(NS_INV, money_tag))
            unit_price = row["base"] / Decimal(str(row["quantity"]))
            _text(money, NS_TYP, "unitPrice", _money(unit_price))
            _text(money, NS_TYP, "price", _money(row["base"]))
            _text(money, NS_TYP, "priceVAT", _money(row["vat"]))

        summary = etree.SubElement(invoice, _q(NS_INV, "invoiceSummary"))
        if currency == "CZK":
            home = etree.SubElement(summary, _q(NS_INV, "homeCurrency"))
            for row in _vat_rows(data):
                rate = Decimal(row["rate"])
                base = row["base"]
                vat = row["vat"]
                gross = _money(Decimal(base) + Decimal(vat))
                if rate == 0:
                    _text(home, NS_TYP, "priceNone", base)
                elif rate == 12:
                    _text(home, NS_TYP, "priceLow", base)
                    _text(home, NS_TYP, "priceLowVAT", vat)
                    _text(home, NS_TYP, "priceLowSum", gross)
                elif rate == 21:
                    _text(home, NS_TYP, "priceHigh", base)
                    _text(home, NS_TYP, "priceHighVAT", vat)
                    _text(home, NS_TYP, "priceHighSum", gross)
                else:
                    _rate_name(rate)
        else:
            foreign = etree.SubElement(summary, _q(NS_INV, "foreignCurrency"))
            currency_ref = etree.SubElement(foreign, _q(NS_TYP, "currency"))
            _text(currency_ref, NS_TYP, "ids", currency)
            _text(foreign, NS_TYP, "rate", data["exchange_rate"])
            _text(foreign, NS_TYP, "amount", data.get("exchange_rate_amount", 1))
            _text(foreign, NS_TYP, "priceSum", data["total_amount"])

        return etree.tostring(
            root,
            xml_declaration=True,
            encoding=self.encoding,
            pretty_print=True,
        )


def generate_invoice_xml(
    revision: InvoiceRevision,
    allocations: list[Allocation],
    *,
    accounting_unit_ico: str,
    accounting_unit_key: str | None = None,
    encoding: str = "Windows-1250",
) -> bytes:
    snapshot = build_source_snapshot(revision, allocations)
    return PohodaInvoiceXmlGenerator(encoding=encoding).generate(
        snapshot,
        accounting_unit_ico=accounting_unit_ico,
        accounting_unit_key=accounting_unit_key,
    )


def validate_xml_detailed(xml_bytes: bytes, xsd_path: Path) -> list[dict[str, Any]]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        schema_document = etree.parse(str(xsd_path), parser)
        schema = etree.XMLSchema(schema_document)
        document = etree.fromstring(xml_bytes, parser)
    except (etree.XMLSyntaxError, etree.XMLSchemaParseError, OSError) as exc:
        line, column = getattr(exc, "position", (None, None))
        return [{"line": line, "column": column, "message": str(exc), "path": None}]
    if schema.validate(document):
        return []
    return [
        {
            "line": error.line,
            "column": error.column,
            "message": error.message,
            "path": error.path,
        }
        for error in schema.error_log
    ]


def validate_xml(xml_bytes: bytes, xsd_path: Path) -> list[str]:
    return [row["message"] for row in validate_xml_detailed(xml_bytes, xsd_path)]


def parse_pohoda_response(
    xml_bytes: bytes,
    *,
    response_xsd_path: Path | None = None,
) -> dict[str, Any]:
    schema_errors = (
        validate_xml_detailed(xml_bytes, response_xsd_path) if response_xsd_path else []
    )
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        root = etree.fromstring(xml_bytes, parser)
    except etree.XMLSyntaxError as exc:
        raise PohodaMappingError(f"Invalid POHODA response XML: {exc}") from exc
    if etree.QName(root).localname != "responsePack" or etree.QName(root).namespace != NS_RESPONSE:
        raise PohodaMappingError("The uploaded document is not a POHODA responsePack 2.x")

    items: list[dict[str, Any]] = []
    for item in root.xpath("./rsp:responsePackItem", namespaces={"rsp": NS_RESPONSE}):
        produced: list[dict[str, str | None]] = []
        details: list[dict[str, str | None]] = []
        for node in item.iter():
            name = etree.QName(node).localname
            if name == "producedDetails":
                values = {
                    etree.QName(child).localname: child.text
                    for child in node
                    if child.text is not None
                }
                produced.append(values)
            elif name == "detail":
                values = {
                    etree.QName(child).localname: child.text
                    for child in node
                    if child.text is not None
                }
                values["state"] = node.get("state")
                details.append(values)
        response_nodes = [node for node in item if etree.QName(node).localname.endswith("Response")]
        items.append(
            {
                "id": item.get("id"),
                "state": item.get("state"),
                "response_state": response_nodes[0].get("state") if response_nodes else None,
                "produced_details": produced,
                "import_details": details,
            }
        )
    return {
        "id": root.get("id"),
        "state": root.get("state"),
        "note": root.get("note"),
        "program_version": root.get("programVersion"),
        "xsd_valid": not schema_errors,
        "schema_errors": schema_errors,
        "items": items,
    }
