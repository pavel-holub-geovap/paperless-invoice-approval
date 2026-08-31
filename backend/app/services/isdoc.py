from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from io import BytesIO
from pathlib import Path, PurePath
from typing import Any

from lxml import etree
from pypdf import PdfReader
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    AIExtractionStatus,
    ExtractedField,
    ExtractionSource,
    Invoice,
    IsdocExtraction,
    IsdocStatus,
)
from app.services.audit import record_event
from app.services.bank_accounts import normalize_payment_data
from app.services.classification import refresh_business_routing, set_extraction_source
from app.services.validation import run_validations
from app.services.workflow import update_invoice_data

ISDOC_NAMESPACE = "http://isdoc.cz/namespace/2013"
SUPPORTED_ISDOC_VERSIONS = {"6.0.2"}
NS = {"i": ISDOC_NAMESPACE}


@dataclass(frozen=True)
class EmbeddedAttachment:
    filename: str
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True)
class IsdocInspection:
    status: IsdocStatus
    attachments: tuple[EmbeddedAttachment, ...]
    isdoc: EmbeddedAttachment | None = None
    version: str | None = None
    namespace: str | None = None
    mapped_data: dict[str, Any] | None = None
    provenance: dict[str, Any] | None = None
    error: str | None = None


@dataclass(frozen=True)
class IsdocMapping:
    data: dict[str, Any]
    provenance: dict[str, Any]


def _safe_filename(value: str) -> str:
    normalized = value.replace("\\", "/")
    leaf = PurePath(normalized).name
    leaf = re.sub(r"[\x00-\x1f\x7f]", "", leaf).strip()
    return leaf[:255] or "attachment.bin"


def enumerate_attachments(pdf: bytes) -> tuple[EmbeddedAttachment, ...]:
    reader = PdfReader(BytesIO(pdf), strict=False)
    result: list[EmbeddedAttachment] = []
    for filename, contents in reader.attachments.items():
        values = contents if isinstance(contents, list) else [contents]
        for content in values:
            result.append(EmbeddedAttachment(_safe_filename(str(filename)), bytes(content)))
    return tuple(result)


def attachment_manifest(pdf: bytes) -> list[dict[str, Any]]:
    return [
        {"filename": row.filename, "sha256": row.sha256, "size": len(row.content)}
        for row in enumerate_attachments(pdf)
    ]


def _text(root: etree._Element, path: str) -> str | None:
    nodes = root.xpath(path, namespaces=NS)
    if not nodes:
        return None
    value = nodes[0]
    text = value if isinstance(value, str) else value.text
    compact = str(text or "").strip()
    return compact or None


def _all_text(root: etree._Element, path: str) -> list[str]:
    values: list[str] = []
    for node in root.xpath(path, namespaces=NS):
        value = node if isinstance(node, str) else node.text
        compact = str(value or "").strip()
        if compact:
            values.append(compact)
    return values


def _money(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return f"{Decimal(value).quantize(Decimal('0.01')):.2f}"
    except InvalidOperation as exc:
        raise ValueError(f"ISDOC amount is not decimal: {value}") from exc


def _number(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        normalized = Decimal(value).normalize()
    except InvalidOperation as exc:
        raise ValueError(f"ISDOC number is not decimal: {value}") from exc
    return format(normalized, "f")


def _source(path: str, raw_value: Any) -> dict[str, Any]:
    return {"source": "ISDOC", "path": path, "raw_value": raw_value}


def _amount(
    parent: etree._Element,
    name: str,
    base_path: str,
    *,
    foreign_currency: bool,
) -> tuple[str | None, str]:
    if foreign_currency:
        foreign_name = f"{name}Curr"
        foreign_value = _text(parent, f"./i:{foreign_name}")
        if foreign_value is not None:
            return _money(foreign_value), f"{base_path}/{foreign_name}"
    value = _text(parent, f"./i:{name}")
    return _money(value), f"{base_path}/{name}"


def _map_isdoc_602(root: etree._Element) -> IsdocMapping:
    supplier_nodes = root.xpath("./i:AccountingSupplierParty/i:Party", namespaces=NS)
    if len(supplier_nodes) != 1:
        raise ValueError("ISDOC supplier Party is missing or ambiguous")
    supplier = supplier_nodes[0]
    address_nodes = supplier.xpath("./i:PostalAddress", namespaces=NS)
    address = address_nodes[0] if address_nodes else None
    details_nodes = root.xpath("./i:PaymentMeans/i:Payment/i:Details", namespaces=NS)
    details = details_nodes[0] if details_nodes else None

    def at(element: etree._Element | None, path: str) -> str | None:
        return _text(element, path) if element is not None else None

    street_name = at(address, "./i:StreetName")
    building_number = at(address, "./i:BuildingNumber")
    supplier_street = " ".join(row for row in (street_name, building_number) if row) or None
    supplier_city = at(address, "./i:CityName")
    supplier_zip = at(address, "./i:PostalZone")
    supplier_country = at(address, "./i:Country/i:IdentificationCode")
    supplier_country_name = at(address, "./i:Country/i:Name")
    address_parts = [
        supplier_street,
        " ".join(row for row in (supplier_zip, supplier_city) if row) or None,
        supplier_country_name or supplier_country,
    ]
    supplier_address_raw = ", ".join(row for row in address_parts if row) or None

    foreign_currency = _text(root, "./i:ForeignCurrencyCode") is not None
    currency_path = "/Invoice/ForeignCurrencyCode" if foreign_currency else "/Invoice/LocalCurrencyCode"
    currency = _text(root, "./i:ForeignCurrencyCode") or _text(root, "./i:LocalCurrencyCode")

    data: dict[str, Any] = {
        "supplier_name": _text(supplier, "./i:PartyName/i:Name"),
        "supplier_ico": _text(supplier, "./i:PartyIdentification/i:ID"),
        "supplier_dic": _text(supplier, "./i:PartyTaxScheme/i:CompanyID"),
        "supplier_address_raw": supplier_address_raw,
        "supplier_street": supplier_street,
        "supplier_city": supplier_city,
        "supplier_zip": supplier_zip,
        "supplier_country": supplier_country,
        "invoice_number": _text(root, "./i:ID"),
        "variable_symbol": at(details, "./i:VariableSymbol"),
        "issue_date": _text(root, "./i:IssueDate"),
        "taxable_supply_date": _text(root, "./i:TaxPointDate"),
        "due_date": at(details, "./i:PaymentDueDate"),
        "currency": currency,
        "bank_account": at(details, "./i:ID"),
        "bank_code": at(details, "./i:BankCode"),
        "iban": at(details, "./i:IBAN"),
        "swift_bic": at(details, "./i:BIC"),
        "description": "; ".join(_all_text(root, "./i:Note")) or None,
    }
    provenance: dict[str, Any] = {
        "supplier_name": _source("/Invoice/AccountingSupplierParty/Party/PartyName/Name", data["supplier_name"]),
        "supplier_ico": _source("/Invoice/AccountingSupplierParty/Party/PartyIdentification/ID", data["supplier_ico"]),
        "supplier_dic": _source("/Invoice/AccountingSupplierParty/Party/PartyTaxScheme/CompanyID", data["supplier_dic"]),
        "supplier_street": _source("/Invoice/AccountingSupplierParty/Party/PostalAddress/{StreetName,BuildingNumber}", supplier_street),
        "supplier_city": _source("/Invoice/AccountingSupplierParty/Party/PostalAddress/CityName", supplier_city),
        "supplier_zip": _source("/Invoice/AccountingSupplierParty/Party/PostalAddress/PostalZone", supplier_zip),
        "supplier_country": _source("/Invoice/AccountingSupplierParty/Party/PostalAddress/Country/IdentificationCode", supplier_country),
        "supplier_address_raw": {
            "source": "ISDOC",
            "paths": [
                "/Invoice/AccountingSupplierParty/Party/PostalAddress/StreetName",
                "/Invoice/AccountingSupplierParty/Party/PostalAddress/BuildingNumber",
                "/Invoice/AccountingSupplierParty/Party/PostalAddress/PostalZone",
                "/Invoice/AccountingSupplierParty/Party/PostalAddress/CityName",
                "/Invoice/AccountingSupplierParty/Party/PostalAddress/Country",
            ],
            "raw_value": supplier_address_raw,
        },
        "invoice_number": _source("/Invoice/ID", data["invoice_number"]),
        "variable_symbol": _source("/Invoice/PaymentMeans/Payment/Details/VariableSymbol", data["variable_symbol"]),
        "issue_date": _source("/Invoice/IssueDate", data["issue_date"]),
        "taxable_supply_date": _source("/Invoice/TaxPointDate", data["taxable_supply_date"]),
        "due_date": _source("/Invoice/PaymentMeans/Payment/Details/PaymentDueDate", data["due_date"]),
        "currency": _source(currency_path, currency),
        "bank_account": _source("/Invoice/PaymentMeans/Payment/Details/ID", data["bank_account"]),
        "bank_code": _source("/Invoice/PaymentMeans/Payment/Details/BankCode", data["bank_code"]),
        "iban": _source("/Invoice/PaymentMeans/Payment/Details/IBAN", data["iban"]),
        "swift_bic": _source("/Invoice/PaymentMeans/Payment/Details/BIC", data["swift_bic"]),
        "description": _source("/Invoice/Note", data["description"]),
    }

    vat_lines: list[dict[str, Any]] = []
    vat_provenance: list[dict[str, Any]] = []
    for index, subtotal in enumerate(
        root.xpath("./i:TaxTotal/i:TaxSubTotal", namespaces=NS), start=1
    ):
        base_path = f"/Invoice/TaxTotal/TaxSubTotal[{index}]"
        taxable_base, taxable_path = _amount(
            subtotal, "TaxableAmount", base_path, foreign_currency=foreign_currency
        )
        vat_amount, vat_path = _amount(
            subtotal, "TaxAmount", base_path, foreign_currency=foreign_currency
        )
        gross_amount, gross_path = _amount(
            subtotal, "TaxInclusiveAmount", base_path, foreign_currency=foreign_currency
        )
        rate = _number(_text(subtotal, "./i:TaxCategory/i:Percent"))
        vat_lines.append(
            {
                "vat_rate": rate,
                "taxable_base": taxable_base,
                "vat_amount": vat_amount,
                "gross_amount": gross_amount,
                "adjustment_type": None,
                "source_text": base_path,
            }
        )
        vat_provenance.append(
            {
                "source": "ISDOC",
                "path": base_path,
                "fields": {
                    "vat_rate": _source(f"{base_path}/TaxCategory/Percent", rate),
                    "taxable_base": _source(taxable_path, taxable_base),
                    "vat_amount": _source(vat_path, vat_amount),
                    "gross_amount": _source(gross_path, gross_amount),
                },
            }
        )
    data["vat_lines"] = vat_lines
    provenance["vat_lines"] = vat_provenance

    items: list[dict[str, Any]] = []
    item_provenance: list[dict[str, Any]] = []
    for index, line in enumerate(
        root.xpath("./i:InvoiceLines/i:InvoiceLine", namespaces=NS), start=1
    ):
        base_path = f"/Invoice/InvoiceLines/InvoiceLine[{index}]"
        item = {
            "line_id": _text(line, "./i:ID"),
            "description": _text(line, "./i:Item/i:Description"),
            "quantity": _number(_text(line, "./i:InvoicedQuantity")),
            "unit_code": _text(line, "./i:InvoicedQuantity/@unitCode"),
            "unit_price": _money(_text(line, "./i:UnitPrice")),
            "unit_price_tax_inclusive": _money(_text(line, "./i:UnitPriceTaxInclusive")),
            "line_extension_amount": _money(_text(line, "./i:LineExtensionAmount")),
            "line_vat_amount": _money(_text(line, "./i:LineExtensionTaxAmount")),
            "line_gross_amount": _money(_text(line, "./i:LineExtensionAmountTaxInclusive")),
            "vat_rate": _number(_text(line, "./i:ClassifiedTaxCategory/i:Percent")),
        }
        items.append(item)
        item_provenance.append(
            {
                "source": "ISDOC",
                "path": base_path,
                "raw_value": item,
            }
        )
    data["invoice_items"] = items
    provenance["invoice_items"] = item_provenance

    monetary_nodes = root.xpath("./i:LegalMonetaryTotal", namespaces=NS)
    tax_nodes = root.xpath("./i:TaxTotal", namespaces=NS)
    if len(monetary_nodes) != 1 or len(tax_nodes) != 1:
        raise ValueError("ISDOC monetary or tax totals are missing or ambiguous")
    total_without_vat, base_total_path = _amount(
        monetary_nodes[0],
        "TaxExclusiveAmount",
        "/Invoice/LegalMonetaryTotal",
        foreign_currency=foreign_currency,
    )
    total_vat, vat_total_path = _amount(
        tax_nodes[0], "TaxAmount", "/Invoice/TaxTotal", foreign_currency=foreign_currency
    )
    total_amount, total_path = _amount(
        monetary_nodes[0],
        "PayableAmount",
        "/Invoice/LegalMonetaryTotal",
        foreign_currency=foreign_currency,
    )
    rounding, rounding_path = _amount(
        monetary_nodes[0],
        "PayableRoundingAmount",
        "/Invoice/LegalMonetaryTotal",
        foreign_currency=foreign_currency,
    )
    data.update(
        {
            "total_without_vat": total_without_vat,
            "total_vat": total_vat,
            "total_amount": total_amount,
            "payable_rounding_amount": rounding,
        }
    )
    provenance.update(
        {
            "total_without_vat": _source(base_total_path, total_without_vat),
            "total_vat": _source(vat_total_path, total_vat),
            "total_amount": _source(total_path, total_amount),
            "payable_rounding_amount": _source(rounding_path, rounding),
        }
    )

    data = normalize_payment_data(data)
    account_path = "/Invoice/PaymentMeans/Payment/Details/ID"
    for field in ("bank_account_raw", "bank_account_prefix", "bank_account_number"):
        provenance[field] = _source(account_path, data.get(field))
    return IsdocMapping(data=data, provenance=provenance)


def _resolve_schema_path(configured: Path) -> Path:
    candidates = [configured]
    if not configured.is_absolute():
        candidates.append(Path(__file__).resolve().parents[3] / configured)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError(f"ISDOC XSD schema was not found: {configured}")


@lru_cache(maxsize=4)
def _load_schema(path: str) -> etree.XMLSchema:
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    return etree.XMLSchema(etree.parse(path, parser=parser))


def _validate_xsd(root: etree._Element, settings: Settings) -> None:
    schema_path = _resolve_schema_path(settings.isdoc_xsd_path)
    schema = _load_schema(str(schema_path))
    if schema.validate(root.getroottree()):
        return
    error = schema.error_log.last_error
    message = error.message if error is not None else "unknown structural error"
    raise ValueError(f"ISDOC 6.0.2 XSD validation failed: {message}")


def _parse_candidate(
    attachment: EmbeddedAttachment, settings: Settings
) -> tuple[etree._Element, str, IsdocMapping]:
    if len(attachment.content) > settings.isdoc_max_attachment_bytes:
        raise ValueError("ISDOC attachment exceeds configured size limit")
    prefix = attachment.content[:4096].upper()
    if b"<!DOCTYPE" in prefix or b"<!ENTITY" in prefix:
        raise ValueError("DTD and entity declarations are forbidden")
    parser = etree.XMLParser(
        resolve_entities=False, load_dtd=False, no_network=True, recover=False, huge_tree=False
    )
    root = etree.fromstring(attachment.content, parser=parser)
    qname = etree.QName(root)
    if qname.localname != "Invoice" or qname.namespace != ISDOC_NAMESPACE:
        raise LookupError("Attachment is not an ISDOC invoice")
    version = str(root.get("version") or "").strip()
    if version not in SUPPORTED_ISDOC_VERSIONS:
        raise ValueError(f"Unsupported ISDOC version: {version or 'missing'}")
    _validate_xsd(root, settings)
    mapped = _map_isdoc_602(root)
    required = ("supplier_name", "invoice_number", "issue_date", "currency", "total_amount")
    missing = [field for field in required if not mapped.data.get(field)]
    if missing:
        raise ValueError("ISDOC semantic profile is missing: " + ", ".join(missing))
    return root, version, mapped


def inspect_pdf_isdoc(pdf: bytes, settings: Settings) -> IsdocInspection:
    try:
        attachments = enumerate_attachments(pdf)
    except Exception as exc:
        return IsdocInspection(IsdocStatus.ERROR, (), error=f"PDF attachment read failed: {exc}")
    valid: list[tuple[EmbeddedAttachment, etree._Element, str, IsdocMapping]] = []
    invalid_candidates: list[EmbeddedAttachment] = []
    invalid_errors: list[str] = []
    detected = False
    for attachment in attachments:
        xmlish = attachment.filename.casefold().endswith((".isdoc", ".xml")) or attachment.content.lstrip().startswith(b"<")
        if not xmlish:
            continue
        try:
            root, version, mapping = _parse_candidate(attachment, settings)
        except LookupError:
            continue
        except Exception as exc:
            if ISDOC_NAMESPACE.encode() in attachment.content[:8192] or attachment.filename.casefold().endswith(".isdoc"):
                detected = True
                invalid_candidates.append(attachment)
                invalid_errors.append(f"{attachment.filename}: {exc}")
        else:
            detected = True
            valid.append((attachment, root, version, mapping))
    if len(valid) > 1:
        return IsdocInspection(
            IsdocStatus.INVALID,
            attachments,
            isdoc=valid[0][0],
            version=valid[0][2],
            namespace=ISDOC_NAMESPACE,
            error="Multiple valid ISDOC candidates are ambiguous",
        )
    if valid:
        attachment, _root, version, mapping = valid[0]
        return IsdocInspection(
            IsdocStatus.VALID,
            attachments,
            isdoc=attachment,
            version=version,
            namespace=ISDOC_NAMESPACE,
            mapped_data=mapping.data,
            provenance=mapping.provenance,
        )
    if detected:
        return IsdocInspection(
            IsdocStatus.INVALID,
            attachments,
            isdoc=invalid_candidates[0] if invalid_candidates else None,
            namespace=ISDOC_NAMESPACE,
            error="; ".join(invalid_errors)[:4000],
        )
    return IsdocInspection(IsdocStatus.NOT_PRESENT, attachments)


def apply_isdoc_inspection(
    db: Session,
    invoice: Invoice,
    inspection: IsdocInspection,
    actor: str = "system",
) -> IsdocExtraction | None:
    old_status = invoice.isdoc_status
    invoice.has_embedded_isdoc = inspection.isdoc is not None or inspection.status == IsdocStatus.INVALID
    invoice.isdoc_status = inspection.status
    invoice.isdoc_validation_error = inspection.error
    invoice.isdoc_version = inspection.version
    invoice.isdoc_filename = inspection.isdoc.filename if inspection.isdoc else None
    invoice.isdoc_sha256 = inspection.isdoc.sha256 if inspection.isdoc else None
    event_type = {
        IsdocStatus.VALID: "ISDOC_VALIDATED",
        IsdocStatus.INVALID: "ISDOC_VALIDATION_FAILED",
        IsdocStatus.ERROR: "ISDOC_VALIDATION_FAILED",
        IsdocStatus.NOT_PRESENT: "ISDOC_NOT_PRESENT",
    }[inspection.status]
    metadata = {
        "filename": invoice.isdoc_filename,
        "isdoc_sha256": invoice.isdoc_sha256,
        "version": invoice.isdoc_version,
        "error": inspection.error,
    }
    if inspection.isdoc:
        record_event(
            db, "ISDOC_DETECTED", actor=actor, invoice=invoice,
            old_state=old_status.value, new_state=IsdocStatus.DETECTED.value,
            metadata=metadata,
        )
    record_event(
        db, event_type, actor=actor, invoice=invoice,
        old_state=IsdocStatus.DETECTED.value if inspection.isdoc else old_status.value,
        new_state=inspection.status.value, metadata=metadata,
    )
    if inspection.status != IsdocStatus.VALID or not inspection.isdoc or not inspection.mapped_data:
        set_extraction_source(db, invoice, ExtractionSource.OCR_AI)
        refresh_business_routing(invoice)
        return None
    revision = invoice.current_revision
    if revision is None:
        raise ValueError("Invoice has no current revision")
    existing = db.scalar(
        select(IsdocExtraction).where(
            IsdocExtraction.invoice_id == invoice.id,
            IsdocExtraction.isdoc_sha256 == inspection.isdoc.sha256,
        )
    )
    if existing:
        set_extraction_source(db, invoice, ExtractionSource.ISDOC)
        refresh_business_routing(invoice)
        return existing
    data = dict(inspection.mapped_data)
    provenance = dict(inspection.provenance or {})
    if any(value not in (None, "", [], {}) for value in revision.data.values()):
        replacement = {field: None for field in revision.data}
        replacement.update(data)
        revision = update_invoice_data(
            db,
            invoice,
            replacement,
            actor,
            comment="Použití validního ISDOC 6.0.2 jako primárního zdroje dat",
        )
    else:
        revision.data = data
    db.execute(delete(ExtractedField).where(ExtractedField.revision_id == revision.id))
    for field, value in data.items():
        field_provenance = provenance.get(field) or {}
        source_text = None
        if isinstance(field_provenance, dict):
            source_text = field_provenance.get("path")
            if not source_text and field_provenance.get("paths"):
                source_text = " | ".join(field_provenance["paths"])
        elif isinstance(field_provenance, list):
            source_text = " | ".join(
                str(row["path"])
                for row in field_provenance
                if isinstance(row, dict) and row.get("path")
            ) or None
        db.add(
            ExtractedField(
                revision_id=revision.id,
                field_name=field,
                value=value,
                source_text=source_text or "ISDOC",
                confidence=1,
            )
        )
    snapshot = IsdocExtraction(
        invoice_id=invoice.id,
        invoice_revision_id=revision.id,
        filename=inspection.isdoc.filename,
        isdoc_sha256=inspection.isdoc.sha256,
        version=inspection.version or "",
        namespace=inspection.namespace or "",
        mapped_data=data,
        provenance=provenance,
        attachment_metadata={
            "attachments": [
                {"filename": row.filename, "sha256": row.sha256, "size": len(row.content)}
                for row in inspection.attachments
            ]
        },
        created_by=actor,
    )
    db.add(snapshot)
    db.flush()
    invoice.ai_status = AIExtractionStatus.AI_COMPLETED
    set_extraction_source(db, invoice, ExtractionSource.ISDOC)
    refresh_business_routing(invoice)
    run_validations(db, invoice, actor)
    record_event(
        db,
        "EXTRACTION_FROM_ISDOC_CREATED",
        actor=actor,
        invoice=invoice,
        metadata={
            "isdoc_extraction_id": snapshot.id,
            "isdoc_sha256": snapshot.isdoc_sha256,
            "version": snapshot.version,
            "invoice_revision": revision.number,
        },
    )
    return snapshot
