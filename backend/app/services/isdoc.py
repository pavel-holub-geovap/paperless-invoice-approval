from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath
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
from app.services.classification import refresh_business_routing, set_extraction_source
from app.services.validation import run_validations

ISDOC_NAMESPACE = "http://isdoc.cz/namespace/2013"
SUPPORTED_ISDOC_VERSIONS = {"6.0.2"}


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
    error: str | None = None


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
    nodes = root.xpath(path, namespaces={"i": ISDOC_NAMESPACE})
    if not nodes:
        return None
    value = nodes[0]
    text = value if isinstance(value, str) else value.text
    compact = str(text or "").strip()
    return compact or None


def _all_text(root: etree._Element, path: str) -> list[str]:
    values: list[str] = []
    for node in root.xpath(path, namespaces={"i": ISDOC_NAMESPACE}):
        value = node if isinstance(node, str) else node.text
        compact = str(value or "").strip()
        if compact:
            values.append(compact)
    return values


def _map_isdoc(root: etree._Element) -> dict[str, Any]:
    street = _text(root, ".//i:AccountingSupplierParty/i:Party//i:PostalAddress/i:StreetName")
    number = _text(root, ".//i:AccountingSupplierParty/i:Party//i:PostalAddress/i:BuildingNumber")
    supplier_street = " ".join(value for value in (street, number) if value) or None
    vat_lines: list[dict[str, Any]] = []
    for subtotal in root.xpath(".//i:TaxTotal/i:TaxSubTotal", namespaces={"i": ISDOC_NAMESPACE}):
        vat_lines.append(
            {
                "rate": _text(subtotal, ".//i:Percent"),
                "taxable_base": _text(subtotal, "./i:TaxableAmount"),
                "vat_amount": _text(subtotal, "./i:TaxAmount"),
                "gross_amount": None,
                "adjustment_type": None,
                "source_text": "ISDOC TaxSubTotal",
            }
        )
    items: list[dict[str, Any]] = []
    for line in root.xpath(".//i:InvoiceLines/i:InvoiceLine", namespaces={"i": ISDOC_NAMESPACE}):
        items.append(
            {
                "description": _text(line, ".//i:Item/i:Description"),
                "quantity": _text(line, "./i:InvoicedQuantity"),
                "unit_price": _text(line, ".//i:UnitPrice"),
                "line_extension_amount": _text(line, "./i:LineExtensionAmount"),
                "vat_rate": _text(line, ".//i:ClassifiedTaxCategory/i:Percent"),
            }
        )
    currency = _text(root, "./i:ForeignCurrencyCode") or _text(root, "./i:LocalCurrencyCode")
    account = _text(root, ".//i:PaymentMeans//i:ID")
    bank_code = _text(root, ".//i:PaymentMeans//i:BankCode")
    data = {
        "supplier_name": _text(root, ".//i:AccountingSupplierParty/i:Party//i:PartyName/i:Name"),
        "supplier_ico": _text(root, ".//i:AccountingSupplierParty/i:Party//i:CompanyID"),
        "supplier_dic": _text(root, ".//i:AccountingSupplierParty/i:Party//i:PartyTaxScheme/i:CompanyID"),
        "supplier_address_raw": None,
        "supplier_street": supplier_street,
        "supplier_city": _text(root, ".//i:AccountingSupplierParty/i:Party//i:PostalAddress/i:CityName"),
        "supplier_zip": _text(root, ".//i:AccountingSupplierParty/i:Party//i:PostalAddress/i:PostalZone"),
        "invoice_number": _text(root, "./i:DocumentID"),
        "variable_symbol": _text(root, ".//i:PaymentMeans//i:VariableSymbol"),
        "issue_date": _text(root, "./i:IssueDate"),
        "taxable_supply_date": _text(root, "./i:TaxPointDate"),
        "due_date": _text(root, ".//i:PaymentMeans//i:PaymentDueDate"),
        "currency": currency,
        "bank_account": account,
        "bank_code": bank_code,
        "iban": _text(root, ".//i:PaymentMeans//i:IBAN"),
        "swift_bic": _text(root, ".//i:PaymentMeans//i:BIC"),
        "vat_lines": vat_lines,
        "total_without_vat": _text(root, ".//i:LegalMonetaryTotal/i:TaxExclusiveAmount"),
        "total_vat": _text(root, ".//i:TaxTotal/i:TaxAmount"),
        "total_amount": _text(root, ".//i:LegalMonetaryTotal/i:PayableAmount"),
        "description": "; ".join(_all_text(root, "./i:Note")) or None,
        "invoice_items": items,
    }
    address_parts = [data["supplier_street"], data["supplier_zip"], data["supplier_city"]]
    data["supplier_address_raw"] = ", ".join(value for value in address_parts if value) or None
    return data


def _parse_candidate(attachment: EmbeddedAttachment, max_bytes: int) -> tuple[etree._Element, str]:
    if len(attachment.content) > max_bytes:
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
    mapped = _map_isdoc(root)
    required = ("supplier_name", "invoice_number", "issue_date", "currency", "total_amount")
    missing = [field for field in required if not mapped.get(field)]
    if missing:
        raise ValueError("ISDOC semantic profile is missing: " + ", ".join(missing))
    return root, version


def inspect_pdf_isdoc(pdf: bytes, settings: Settings) -> IsdocInspection:
    try:
        attachments = enumerate_attachments(pdf)
    except Exception as exc:
        return IsdocInspection(IsdocStatus.ERROR, (), error=f"PDF attachment read failed: {exc}")
    valid: list[tuple[EmbeddedAttachment, etree._Element, str]] = []
    invalid_candidates: list[EmbeddedAttachment] = []
    invalid_errors: list[str] = []
    detected = False
    for attachment in attachments:
        xmlish = attachment.filename.casefold().endswith((".isdoc", ".xml")) or attachment.content.lstrip().startswith(b"<")
        if not xmlish:
            continue
        try:
            root, version = _parse_candidate(attachment, settings.isdoc_max_attachment_bytes)
        except LookupError:
            continue
        except Exception as exc:
            if ISDOC_NAMESPACE.encode() in attachment.content[:8192] or attachment.filename.casefold().endswith(".isdoc"):
                detected = True
                invalid_candidates.append(attachment)
                invalid_errors.append(f"{attachment.filename}: {exc}")
        else:
            detected = True
            valid.append((attachment, root, version))
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
        attachment, root, version = valid[0]
        return IsdocInspection(
            IsdocStatus.VALID,
            attachments,
            isdoc=attachment,
            version=version,
            namespace=ISDOC_NAMESPACE,
            mapped_data=_map_isdoc(root),
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
    provenance = {field: {"source": "ISDOC"} for field in data}
    revision.data = data
    db.execute(delete(ExtractedField).where(ExtractedField.revision_id == revision.id))
    for field, value in data.items():
        db.add(
            ExtractedField(
                revision_id=revision.id,
                field_name=field,
                value=value,
                source_text="ISDOC",
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
