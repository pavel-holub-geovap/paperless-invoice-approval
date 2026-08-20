from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    Allocation,
    Invoice,
    InvoiceRevision,
    ValidationResult,
    ValidationSeverity,
)
from app.services.audit import record_event

ICO_RE = re.compile(r"^\d{8}$")
DIC_RE = re.compile(r"^(CZ)?\d{8,10}$", re.IGNORECASE)
VS_RE = re.compile(r"^\d{1,10}$")
SUPPORTED_CURRENCIES = {"CZK", "EUR", "USD", "GBP", "PLN"}


def valid_czech_ico(value: str) -> bool:
    if not ICO_RE.fullmatch(value):
        return False
    digits = [int(ch) for ch in value]
    weighted = sum(digits[i] * (8 - i) for i in range(7))
    remainder = weighted % 11
    expected = 1 if remainder == 0 else 0 if remainder == 1 else 11 - remainder
    return digits[7] == expected


def as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _result(
    code: str,
    severity: ValidationSeverity,
    message: str,
    field_name: str | None = None,
    details: dict[str, Any] | None = None,
) -> ValidationResult:
    return ValidationResult(
        code=code,
        severity=severity,
        message=message,
        field_name=field_name,
        details=details or {},
    )


def validate_invoice_data(data: dict[str, Any]) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    required = ("supplier_name", "invoice_number", "issue_date", "currency", "total_amount")
    for field in required:
        if data.get(field) in (None, "", []):
            results.append(
                _result(
                    f"REQUIRED_{field.upper()}",
                    ValidationSeverity.BLOCKING_ERROR,
                    f"Povinné pole {field} není vyplněno.",
                    field,
                )
            )

    ico = str(data.get("ico") or "").replace(" ", "")
    if ico and not ICO_RE.fullmatch(ico):
        results.append(_result("ICO_FORMAT", ValidationSeverity.BLOCKING_ERROR, "IČO musí mít 8 číslic.", "ico"))
    elif ico and not valid_czech_ico(ico):
        results.append(_result("ICO_CHECKSUM", ValidationSeverity.BLOCKING_ERROR, "IČO nemá platný český kontrolní součet.", "ico"))

    dic = str(data.get("dic") or "").replace(" ", "")
    if dic and not DIC_RE.fullmatch(dic):
        results.append(_result("DIC_FORMAT", ValidationSeverity.WARNING, "DIČ nemá očekávaný formát.", "dic"))

    invoice_number = str(data.get("invoice_number") or "")
    if invoice_number and len(invoice_number) > 100:
        results.append(_result("INVOICE_NUMBER_LENGTH", ValidationSeverity.BLOCKING_ERROR, "Číslo faktury je příliš dlouhé.", "invoice_number"))

    variable_symbol = str(data.get("variable_symbol") or "")
    if variable_symbol and not VS_RE.fullmatch(variable_symbol):
        results.append(_result("VARIABLE_SYMBOL_FORMAT", ValidationSeverity.WARNING, "Variabilní symbol má mít nejvýše 10 číslic.", "variable_symbol"))

    currency = str(data.get("currency") or "").upper()
    if currency and currency not in SUPPORTED_CURRENCIES:
        results.append(_result("CURRENCY_UNSUPPORTED", ValidationSeverity.BLOCKING_ERROR, "Měna není podporována.", "currency"))

    total = as_decimal(data.get("total_amount"))
    if data.get("total_amount") is not None and total is None:
        results.append(_result("TOTAL_FORMAT", ValidationSeverity.BLOCKING_ERROR, "Celková částka není číslo.", "total_amount"))
    elif total is not None and total <= 0:
        results.append(_result("TOTAL_POSITIVE", ValidationSeverity.BLOCKING_ERROR, "Celková částka musí být kladná.", "total_amount"))

    def parse_date(field: str) -> date | None:
        raw = data.get(field)
        if raw in (None, ""):
            return None
        if isinstance(raw, date):
            return raw
        try:
            return date.fromisoformat(str(raw))
        except ValueError:
            results.append(_result(f"{field.upper()}_FORMAT", ValidationSeverity.BLOCKING_ERROR, f"{field} nemá ISO formát YYYY-MM-DD.", field))
            return None

    issue_date = parse_date("issue_date")
    due_date = parse_date("due_date")
    taxable_date = parse_date("taxable_supply_date")
    if issue_date and due_date and due_date < issue_date:
        results.append(_result("DUE_BEFORE_ISSUE", ValidationSeverity.WARNING, "Datum splatnosti je před datem vystavení.", "due_date"))
    if issue_date and taxable_date and abs((taxable_date - issue_date).days) > 366:
        results.append(_result("DUZP_DISTANCE", ValidationSeverity.WARNING, "DUZP je neobvykle vzdáleno datu vystavení.", "taxable_supply_date"))

    vat_rows = data.get("vat_breakdown") or []
    if not isinstance(vat_rows, list):
        results.append(_result("VAT_FORMAT", ValidationSeverity.BLOCKING_ERROR, "DPH rozpad musí být seznam.", "vat_breakdown"))
    else:
        computed = Decimal("0")
        valid_rows = True
        for index, row in enumerate(vat_rows):
            if not isinstance(row, dict):
                valid_rows = False
                continue
            base = as_decimal(row.get("base"))
            vat = as_decimal(row.get("vat"))
            rate = as_decimal(row.get("rate"))
            if base is None or vat is None or rate is None:
                valid_rows = False
                continue
            expected_vat = (base * rate / Decimal("100")).quantize(Decimal("0.01"))
            if abs(expected_vat - vat) > Decimal("0.02"):
                results.append(_result("VAT_ROW_MATH", ValidationSeverity.BLOCKING_ERROR, f"DPH řádek {index + 1} matematicky nesedí.", "vat_breakdown"))
            computed += base + vat
        if not valid_rows:
            results.append(_result("VAT_ROW_FORMAT", ValidationSeverity.BLOCKING_ERROR, "Některý DPH řádek nemá base, rate a vat.", "vat_breakdown"))
        if vat_rows and total is not None and abs(computed - total) > Decimal("0.02"):
            results.append(_result("VAT_TOTAL_MATH", ValidationSeverity.BLOCKING_ERROR, "Součet základů a DPH neodpovídá celkové částce.", "vat_breakdown"))

    if not results:
        results.append(_result("INVOICE_DATA_OK", ValidationSeverity.OK, "Základní účetní údaje jsou konzistentní."))
    return results


def run_validations(db: Session, invoice: Invoice, actor: str = "system") -> list[ValidationResult]:
    revision = invoice.current_revision
    if revision is None:
        raise ValueError("Invoice has no current revision")
    db.execute(delete(ValidationResult).where(ValidationResult.revision_id == revision.id))
    results = validate_invoice_data(revision.data)

    candidates = db.scalars(
        select(InvoiceRevision)
        .join(Invoice, Invoice.id == InvoiceRevision.invoice_id)
        .where(
            Invoice.id != invoice.id,
            Invoice.current_revision_number == InvoiceRevision.number,
        )
    ).all()
    duplicate_keys = ("ico", "invoice_number", "total_amount")
    if all(revision.data.get(key) not in (None, "") for key in duplicate_keys) and any(
        all(str(other.data.get(key)) == str(revision.data.get(key)) for key in duplicate_keys)
        for other in candidates
    ):
        results.append(
            _result(
                "DUPLICATE_INVOICE",
                ValidationSeverity.BLOCKING_ERROR,
                "Jiná faktura má stejné IČO, číslo dokladu a částku.",
            )
        )

    allocations = db.scalars(
        select(Allocation).where(Allocation.revision_id == revision.id, Allocation.active.is_(True))
    ).all()
    total = as_decimal(revision.data.get("total_amount"))
    if allocations and total is not None:
        allocated = sum((row.amount for row in allocations), Decimal("0"))
        if abs(allocated - total) > Decimal("0.01"):
            results.append(_result("ALLOCATION_TOTAL", ValidationSeverity.BLOCKING_ERROR, "Součet rozúčtování neodpovídá částce faktury.", details={"allocated": str(allocated), "total": str(total)}))
        else:
            results.append(_result("ALLOCATION_TOTAL", ValidationSeverity.OK, "Součet rozúčtování odpovídá faktuře."))

    for result in results:
        result.revision_id = revision.id
        db.add(result)
    record_event(
        db,
        "VALIDATION_FINISHED",
        actor=actor,
        invoice=invoice,
        metadata={
            "ok": sum(r.severity == ValidationSeverity.OK for r in results),
            "warnings": sum(r.severity == ValidationSeverity.WARNING for r in results),
            "blocking": sum(r.severity == ValidationSeverity.BLOCKING_ERROR for r in results),
        },
    )
    return results


def has_blocking_errors(db: Session, revision: InvoiceRevision) -> bool:
    return (
        db.scalar(
            select(ValidationResult.id)
            .where(
                ValidationResult.revision_id == revision.id,
                ValidationResult.severity == ValidationSeverity.BLOCKING_ERROR,
            )
            .limit(1)
        )
        is not None
    )
