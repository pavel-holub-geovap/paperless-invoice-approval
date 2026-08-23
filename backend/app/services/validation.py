from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
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
BANK_ACCOUNT_RE = re.compile(r"^(?:\d{1,6}-)?\d{1,10}$")
BANK_CODE_RE = re.compile(r"^\d{4}$")
BIC_RE = re.compile(r"^[A-Z0-9]{8}(?:[A-Z0-9]{3})?$")
IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")
MONEY_TOLERANCE = Decimal("0.02")
ISO_4217_CODES = {
    "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AUD", "AWG", "AZN",
    "BAM", "BBD", "BDT", "BGN", "BHD", "BIF", "BMD", "BND", "BOB", "BOV",
    "BRL", "BSD", "BTN", "BWP", "BYN", "BZD", "CAD", "CDF", "CHE", "CHF",
    "CHW", "CLF", "CLP", "CNY", "COP", "COU", "CRC", "CUP", "CVE", "CZK",
    "DJF", "DKK", "DOP", "DZD", "EGP", "ERN", "ETB", "EUR", "FJD", "FKP",
    "GBP", "GEL", "GHS", "GIP", "GMD", "GNF", "GTQ", "GYD", "HKD", "HNL",
    "HTG", "HUF", "IDR", "ILS", "INR", "IQD", "IRR", "ISK", "JMD", "JOD",
    "JPY", "KES", "KGS", "KHR", "KMF", "KPW", "KRW", "KWD", "KYD", "KZT",
    "LAK", "LBP", "LKR", "LRD", "LSL", "LYD", "MAD", "MDL", "MGA", "MKD",
    "MMK", "MNT", "MOP", "MRU", "MUR", "MVR", "MWK", "MXN", "MXV", "MYR",
    "MZN", "NAD", "NGN", "NIO", "NOK", "NPR", "NZD", "OMR", "PAB", "PEN",
    "PGK", "PHP", "PKR", "PLN", "PYG", "QAR", "RON", "RSD", "RUB", "RWF",
    "SAR", "SBD", "SCR", "SDG", "SEK", "SGD", "SHP", "SLE", "SOS", "SRD",
    "SSP", "STN", "SVC", "SYP", "SZL", "THB", "TJS", "TMT", "TND", "TOP",
    "TRY", "TTD", "TWD", "TZS", "UAH", "UGX", "USD", "USN", "UYI", "UYU",
    "UYW", "UZS", "VED", "VES", "VND", "VUV", "WST", "XAF", "XAG", "XAU",
    "XBA", "XBB", "XBC", "XBD", "XCD", "XDR", "XOF", "XPD", "XPF", "XPT",
    "XSU", "XTS", "XUA", "XXX", "YER", "ZAR", "ZMW", "ZWG",
}


def valid_czech_ico(value: str) -> bool:
    if not ICO_RE.fullmatch(value):
        return False
    digits = [int(ch) for ch in value]
    weighted = sum(digits[index] * (8 - index) for index in range(7))
    remainder = weighted % 11
    expected = 1 if remainder == 0 else 0 if remainder == 1 else 11 - remainder
    return digits[7] == expected


def valid_iban(value: str) -> bool:
    compact = re.sub(r"\s+", "", value).upper()
    if not IBAN_RE.fullmatch(compact):
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(ord(char) - 55) if char.isalpha() else char for char in rearranged)
    return int(numeric) % 97 == 1


def as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _value(data: dict[str, Any], field: str, *legacy: str) -> Any:
    if field in data:
        return data.get(field)
    for name in legacy:
        if name in data:
            return data.get(name)
    return None


def _result(
    code: str,
    severity: ValidationSeverity,
    message: str,
    field_name: str | None = None,
    *,
    expected: Any | None = None,
    actual: Any | None = None,
    details: dict[str, Any] | None = None,
) -> ValidationResult:
    return ValidationResult(
        code=code,
        severity=severity,
        message=message,
        field_name=field_name,
        expected=expected,
        actual=actual,
        details=details or {},
    )


def validate_invoice_data(data: dict[str, Any]) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    required = ("supplier_name", "invoice_number", "issue_date", "currency", "total_amount")
    missing = [field for field in required if _value(data, field) in (None, "", [])]
    for field in missing:
        results.append(
            _result(
                f"REQUIRED_{field.upper()}",
                ValidationSeverity.BLOCKING_ERROR,
                f"Povinné pole {field} není vyplněno.",
                field,
                expected="non-null",
                actual=_value(data, field),
            )
        )
    if not missing:
        results.append(
            _result(
                "REQUIRED_FIELDS_OK",
                ValidationSeverity.OK,
                "Všechna povinná pole jsou vyplněna.",
            )
        )

    ico = str(_value(data, "supplier_ico", "ico") or "").replace(" ", "")
    if ico and not ICO_RE.fullmatch(ico):
        results.append(
            _result(
                "ICO_FORMAT",
                ValidationSeverity.BLOCKING_ERROR,
                "IČO musí mít 8 číslic.",
                "supplier_ico",
                expected="8 digits",
                actual=ico,
            )
        )
    elif ico and not valid_czech_ico(ico):
        results.append(
            _result(
                "ICO_CHECKSUM",
                ValidationSeverity.BLOCKING_ERROR,
                "IČO nemá platný český kontrolní součet.",
                "supplier_ico",
                expected="valid Czech checksum",
                actual=ico,
            )
        )
    elif ico:
        results.append(
            _result(
                "ICO_OK", ValidationSeverity.OK, "IČO má platný český kontrolní součet.",
                "supplier_ico", expected="valid Czech checksum", actual=ico
            )
        )

    dic = str(_value(data, "supplier_dic", "dic") or "").replace(" ", "").upper()
    if dic and not DIC_RE.fullmatch(dic):
        results.append(
            _result(
                "DIC_FORMAT", ValidationSeverity.WARNING, "DIČ nemá očekávaný formát.",
                "supplier_dic", expected="CZ followed by 8-10 digits", actual=dic
            )
        )
    elif dic:
        results.append(
            _result(
                "DIC_OK", ValidationSeverity.OK, "DIČ má očekávaný formát.",
                "supplier_dic", expected="valid format", actual=dic
            )
        )

    invoice_number = str(_value(data, "invoice_number") or "")
    if invoice_number and len(invoice_number) > 100:
        results.append(
            _result(
                "INVOICE_NUMBER_LENGTH", ValidationSeverity.BLOCKING_ERROR,
                "Číslo faktury je příliš dlouhé.", "invoice_number", expected="<= 100 chars",
                actual=len(invoice_number)
            )
        )

    variable_symbol = str(_value(data, "variable_symbol") or "")
    if variable_symbol and not VS_RE.fullmatch(variable_symbol):
        results.append(
            _result(
                "VARIABLE_SYMBOL_FORMAT", ValidationSeverity.WARNING,
                "Variabilní symbol má mít nejvýše 10 číslic.", "variable_symbol",
                expected="1-10 digits", actual=variable_symbol
            )
        )
    elif variable_symbol:
        results.append(
            _result(
                "VARIABLE_SYMBOL_OK", ValidationSeverity.OK,
                "Variabilní symbol má platný formát.", "variable_symbol",
                expected="1-10 digits", actual=variable_symbol
            )
        )

    currency = str(_value(data, "currency") or "").upper()
    if currency and currency not in ISO_4217_CODES:
        results.append(
            _result(
                "CURRENCY_ISO", ValidationSeverity.BLOCKING_ERROR,
                "Měna není platný ISO 4217 kód.", "currency", expected="ISO 4217", actual=currency
            )
        )
    elif currency:
        results.append(
            _result(
                "CURRENCY_OK", ValidationSeverity.OK, "Měna je platný ISO 4217 kód.",
                "currency", expected="ISO 4217", actual=currency
            )
        )

    amounts = {
        "total_without_vat": as_decimal(_value(data, "total_without_vat")),
        "total_vat": as_decimal(_value(data, "total_vat")),
        "total_amount": as_decimal(_value(data, "total_amount")),
    }
    for field, parsed in amounts.items():
        raw = _value(data, field)
        if raw is not None and parsed is None:
            results.append(
                _result(
                    f"{field.upper()}_FORMAT", ValidationSeverity.BLOCKING_ERROR,
                    f"{field} není numerická hodnota.", field, expected="decimal", actual=raw
                )
            )
        elif parsed is not None and parsed < 0:
            results.append(
                _result(
                    f"{field.upper()}_POSITIVE", ValidationSeverity.BLOCKING_ERROR,
                    f"{field} nesmí být záporné.", field, expected=">= 0", actual=str(parsed)
                )
            )
    total = amounts["total_amount"]
    if total is not None and total == 0:
        results.append(
            _result(
                "TOTAL_POSITIVE", ValidationSeverity.BLOCKING_ERROR,
                "Celková částka musí být kladná.", "total_amount", expected="> 0", actual="0"
            )
        )
    elif total is not None:
        results.append(
            _result(
                "AMOUNTS_NUMERIC_OK", ValidationSeverity.OK,
                "Celkové částky jsou numerické a nezáporné."
            )
        )

    parsed_dates: dict[str, date | None] = {}
    for field in ("issue_date", "taxable_supply_date", "due_date"):
        raw = _value(data, field)
        if raw in (None, ""):
            parsed_dates[field] = None
            continue
        if isinstance(raw, date):
            parsed_dates[field] = raw
            continue
        try:
            parsed_dates[field] = date.fromisoformat(str(raw))
        except ValueError:
            parsed_dates[field] = None
            results.append(
                _result(
                    f"{field.upper()}_FORMAT", ValidationSeverity.BLOCKING_ERROR,
                    f"{field} nemá ISO formát YYYY-MM-DD.", field,
                    expected="YYYY-MM-DD", actual=raw
                )
            )
    issue_date = parsed_dates["issue_date"]
    due_date = parsed_dates["due_date"]
    taxable_date = parsed_dates["taxable_supply_date"]
    if issue_date and due_date and due_date < issue_date:
        results.append(
            _result(
                "DUE_BEFORE_ISSUE", ValidationSeverity.WARNING,
                "Datum splatnosti je před datem vystavení.", "due_date",
                expected=f">= {issue_date.isoformat()}", actual=due_date.isoformat()
            )
        )
    elif issue_date and due_date:
        results.append(
            _result("DATE_ORDER_OK", ValidationSeverity.OK, "Pořadí dat je logické.")
        )
    if issue_date and taxable_date and abs((taxable_date - issue_date).days) > 366:
        results.append(
            _result(
                "DUZP_DISTANCE", ValidationSeverity.WARNING,
                "DUZP je neobvykle vzdáleno datu vystavení.", "taxable_supply_date",
                expected="within 366 days", actual=taxable_date.isoformat()
            )
        )

    vat_rows = _value(data, "vat_lines", "vat_breakdown") or []
    sum_base = Decimal("0")
    sum_vat = Decimal("0")
    if not isinstance(vat_rows, list):
        results.append(
            _result(
                "VAT_FORMAT", ValidationSeverity.BLOCKING_ERROR,
                "DPH rozpad musí být seznam.", "vat_lines", expected="array", actual=vat_rows
            )
        )
    else:
        for index, row in enumerate(vat_rows):
            if not isinstance(row, dict):
                results.append(
                    _result(
                        "VAT_ROW_FORMAT", ValidationSeverity.BLOCKING_ERROR,
                        f"DPH řádek {index + 1} není objekt.", "vat_lines",
                        expected="object", actual=row
                    )
                )
                continue
            base = as_decimal(row.get("taxable_base", row.get("base")))
            vat = as_decimal(row.get("vat_amount", row.get("vat")))
            rate = as_decimal(row.get("vat_rate", row.get("rate")))
            if base is None or vat is None or rate is None:
                results.append(
                    _result(
                        "VAT_ROW_FORMAT", ValidationSeverity.BLOCKING_ERROR,
                        f"DPH řádek {index + 1} nemá sazbu, základ a DPH.", "vat_lines",
                        expected="decimal vat_rate/taxable_base/vat_amount", actual=row
                    )
                )
                continue
            expected_vat = (base * rate / Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if abs(expected_vat - vat) > MONEY_TOLERANCE:
                results.append(
                    _result(
                        "VAT_ROW_MATH", ValidationSeverity.BLOCKING_ERROR,
                        f"DPH řádek {index + 1} matematicky nesedí.", "vat_lines",
                        expected=str(expected_vat), actual=str(vat), details={"row": index + 1}
                    )
                )
            else:
                results.append(
                    _result(
                        "VAT_ROW_OK", ValidationSeverity.OK,
                        f"DPH řádek {index + 1} matematicky sedí.", "vat_lines",
                        expected=str(expected_vat), actual=str(vat), details={"row": index + 1}
                    )
                )
            sum_base += base
            sum_vat += vat

    declared_base = amounts["total_without_vat"]
    declared_vat = amounts["total_vat"]
    if vat_rows and declared_base is not None:
        severity = (
            ValidationSeverity.OK
            if abs(sum_base - declared_base) <= MONEY_TOLERANCE
            else ValidationSeverity.BLOCKING_ERROR
        )
        results.append(
            _result(
                "VAT_BASE_TOTAL_OK" if severity == ValidationSeverity.OK else "VAT_BASE_TOTAL_MISMATCH",
                severity,
                "Součet základů DPH odpovídá celkovému základu."
                if severity == ValidationSeverity.OK
                else "Součet základů DPH neodpovídá celkovému základu.",
                "total_without_vat", expected=str(sum_base), actual=str(declared_base)
            )
        )
    if vat_rows and declared_vat is not None:
        severity = (
            ValidationSeverity.OK
            if abs(sum_vat - declared_vat) <= MONEY_TOLERANCE
            else ValidationSeverity.BLOCKING_ERROR
        )
        results.append(
            _result(
                "VAT_TOTAL_OK" if severity == ValidationSeverity.OK else "VAT_TOTAL_MISMATCH",
                severity,
                "Součet DPH odpovídá celkovému DPH."
                if severity == ValidationSeverity.OK
                else "Součet DPH neodpovídá celkovému DPH.",
                "total_vat", expected=str(sum_vat), actual=str(declared_vat)
            )
        )
    math_base = declared_base if declared_base is not None else (sum_base if vat_rows else None)
    math_vat = declared_vat if declared_vat is not None else (sum_vat if vat_rows else None)
    if math_base is not None and math_vat is not None and total is not None:
        expected_total = math_base + math_vat
        severity = (
            ValidationSeverity.OK
            if abs(expected_total - total) <= MONEY_TOLERANCE
            else ValidationSeverity.BLOCKING_ERROR
        )
        results.append(
            _result(
                "TOTAL_MATH_OK" if severity == ValidationSeverity.OK else "VAT_TOTAL_MATH",
                severity,
                "Základ a DPH odpovídají celkové částce."
                if severity == ValidationSeverity.OK
                else "Základ a DPH neodpovídají celkové částce.",
                "total_amount", expected=str(expected_total), actual=str(total)
            )
        )

    account = str(_value(data, "bank_account") or "").replace(" ", "")
    bank_code = str(_value(data, "bank_code") or "").replace(" ", "")
    if bool(account) != bool(bank_code):
        results.append(
            _result(
                "DOMESTIC_ACCOUNT_INCOMPLETE",
                ValidationSeverity.WARNING,
                "Český domácí účet musí obsahovat číslo účtu i čtyřmístný kód banky.",
                "bank_account" if account else "bank_code",
                expected="[prefix-]account / bank code",
                actual={"bank_account": account or None, "bank_code": bank_code or None},
            )
        )
    if account and not BANK_ACCOUNT_RE.fullmatch(account):
        results.append(
            _result(
                "BANK_ACCOUNT_FORMAT", ValidationSeverity.WARNING,
                "Číslo účtu nemá očekávaný český formát.", "bank_account",
                expected="[prefix-]account", actual=account
            )
        )
    elif account and bank_code and BANK_CODE_RE.fullmatch(bank_code):
        results.append(
            _result(
                "DOMESTIC_ACCOUNT_OK",
                ValidationSeverity.OK,
                "Český domácí účet má platný základní formát.",
                "bank_account",
                expected="[prefix-]account / 4 digit bank code",
                actual=f"{account}/{bank_code}",
            )
        )
    if bank_code and not BANK_CODE_RE.fullmatch(bank_code):
        results.append(
            _result(
                "BANK_CODE_FORMAT", ValidationSeverity.WARNING, "Kód banky musí mít 4 číslice.",
                "bank_code", expected="4 digits", actual=bank_code
            )
        )
    iban = str(_value(data, "iban") or "")
    if iban and not valid_iban(iban):
        results.append(
            _result(
                "IBAN_CHECKSUM", ValidationSeverity.WARNING, "IBAN nemá platný formát nebo checksum.",
                "iban", expected="valid IBAN mod-97", actual=iban
            )
        )
    elif iban:
        results.append(
            _result("IBAN_OK", ValidationSeverity.OK, "IBAN má platný checksum.")
        )
    bic = str(_value(data, "swift_bic") or "").upper()
    if bic and not BIC_RE.fullmatch(bic):
        results.append(
            _result(
                "BIC_FORMAT", ValidationSeverity.WARNING, "SWIFT/BIC nemá platný formát.",
                "swift_bic", expected="8 or 11 alphanumeric characters", actual=bic
            )
        )
    elif bic:
        results.append(
            _result("BIC_OK", ValidationSeverity.OK, "SWIFT/BIC má platný formát.")
        )
    if bic and not iban:
        results.append(
            _result(
                "BIC_WITHOUT_IBAN",
                ValidationSeverity.WARNING,
                "SWIFT/BIC je uveden bez IBAN; platební údaj je potřeba ručně ověřit.",
                "swift_bic",
                expected="IBAN with BIC",
                actual=bic,
            )
        )
    if not account and not bank_code and not iban:
        results.append(
            _result(
                "PAYMENT_DETAILS_MISSING",
                ValidationSeverity.WARNING,
                "Není uveden domácí účet ani IBAN; správce musí platební údaje ověřit v originálu.",
                "bank_account",
                expected="complete domestic account or valid IBAN",
                actual=None,
            )
        )

    if not results:
        results.append(
            _result("INVOICE_DATA_OK", ValidationSeverity.OK, "Účetní údaje jsou konzistentní.")
        )
    return results


def run_validations(
    db: Session, invoice: Invoice, actor: str = "system"
) -> list[ValidationResult]:
    revision = invoice.current_revision
    if revision is None:
        raise ValueError("Invoice has no current revision")
    db.execute(delete(ValidationResult).where(ValidationResult.revision_id == revision.id))
    results = validate_invoice_data(revision.data)

    candidates = db.scalars(
        select(InvoiceRevision)
        .join(Invoice, Invoice.id == InvoiceRevision.invoice_id)
        .where(Invoice.id != invoice.id, Invoice.current_revision_number == InvoiceRevision.number)
    ).all()
    duplicate_keys = ("supplier_ico", "invoice_number", "total_amount")

    def duplicate_value(row: dict[str, Any], key: str) -> Any:
        return _value(row, key, "ico") if key == "supplier_ico" else row.get(key)

    if all(duplicate_value(revision.data, key) not in (None, "") for key in duplicate_keys) and any(
        all(
            str(duplicate_value(other.data, key)) == str(duplicate_value(revision.data, key))
            for key in duplicate_keys
        )
        for other in candidates
    ):
        results.append(
            _result(
                "DUPLICATE_INVOICE", ValidationSeverity.BLOCKING_ERROR,
                "Jiná faktura má stejné IČO, číslo dokladu a částku."
            )
        )

    allocations = db.scalars(
        select(Allocation).where(
            Allocation.revision_id == revision.id, Allocation.active.is_(True)
        )
    ).all()
    total = as_decimal(revision.data.get("total_amount"))
    if allocations and total is not None:
        allocated = sum((row.amount for row in allocations), Decimal("0"))
        if abs(allocated - total) > Decimal("0.01"):
            results.append(
                _result(
                    "ALLOCATION_TOTAL_MISMATCH", ValidationSeverity.BLOCKING_ERROR,
                    "Součet rozúčtování neodpovídá částce faktury.",
                    expected=str(total), actual=str(allocated)
                )
            )
        else:
            results.append(
                _result(
                    "ALLOCATION_TOTAL_OK", ValidationSeverity.OK,
                    "Součet rozúčtování odpovídá faktuře.",
                    expected=str(total), actual=str(allocated)
                )
            )

    for result in results:
        result.revision_id = revision.id
        db.add(result)
    record_event(
        db,
        "VALIDATION_FINISHED",
        actor=actor,
        invoice=invoice,
        metadata={
            "ok": sum(row.severity == ValidationSeverity.OK for row in results),
            "warning": sum(row.severity == ValidationSeverity.WARNING for row in results),
            "blocking_error": sum(
                row.severity == ValidationSeverity.BLOCKING_ERROR for row in results
            ),
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
