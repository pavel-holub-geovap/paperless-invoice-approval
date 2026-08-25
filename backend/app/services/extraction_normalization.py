from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from app.schemas import InvoiceExtractionRawV1, InvoiceExtractionV1

RAW_SCHEMA_VERSION = "invoice-extraction.raw.v1"
DATE_FIELDS = {"issue_date", "taxable_supply_date", "due_date"}
DECIMAL_FIELDS = {"total_without_vat", "total_vat", "total_amount"}
TEXT_FIELDS = {
    "supplier_name",
    "supplier_ico",
    "supplier_dic",
    "supplier_address_raw",
    "supplier_street",
    "supplier_city",
    "supplier_zip",
    "invoice_number",
    "variable_symbol",
    "currency",
    "bank_account",
    "bank_code",
    "iban",
    "swift_bic",
    "description",
}
_CURRENCY = re.compile(r"\s*(?:Kč|CZK|EUR)\s*$", re.IGNORECASE)
_CZECH_DATE = re.compile(r"^(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})$")
_POSTAL_CODE = re.compile(r"^(\d{3})\s?(\d{2})$")


class ExtractionNormalizationFailed(ValueError):
    def __init__(self, errors: list[dict[str, Any]]):
        self.errors = errors
        first = errors[0] if errors else {}
        super().__init__(
            f"{first.get('path', 'structured output')}: "
            f"{first.get('message', 'normalization failed')}"
        )


def _json_value(value: Any) -> Any:
    if isinstance(value, (Decimal, date)):
        return str(value)
    if isinstance(value, (str, int, bool, type(None), list, dict)):
        return value
    return repr(value)


def validation_error_details(
    exc: ValidationError,
    *,
    stage: str,
    attempt: int,
) -> list[dict[str, Any]]:
    expected_by_type = {
        "missing": "required field",
        "extra_forbidden": "no extra field",
        "decimal_parsing": "decimal",
        "decimal_type": "decimal",
        "date_from_datetime_parsing": "YYYY-MM-DD",
        "date_type": "YYYY-MM-DD",
        "string_type": "string or null",
        "list_type": "list",
        "literal_error": "allowed enum value",
        "bool_parsing": "boolean",
        "bool_type": "boolean",
    }
    details = []
    for error in exc.errors(include_url=False, include_context=False, include_input=True):
        error_type = str(error["type"])
        actual = error.get("input")
        details.append(
            {
                "stage": stage,
                "attempt": attempt,
                "path": ".".join(str(part) for part in error["loc"]),
                "type": error_type,
                "message": str(error["msg"]),
                "expected": expected_by_type.get(error_type, "value matching schema"),
                "actual": _json_value(actual),
                "actual_type": type(actual).__name__,
            }
        )
    return details


def _problem(path: str, expected: str, actual: Any, message: str) -> None:
    raise ExtractionNormalizationFailed(
        [
            {
                "stage": "normalization",
                "attempt": 1,
                "path": path,
                "type": "normalization_error",
                "message": message,
                "expected": expected,
                "actual": _json_value(actual),
                "actual_type": type(actual).__name__,
            }
        ]
    )


def _blank(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


def normalize_decimal(value: Any, *, path: str, allow_percent: bool) -> Decimal | None:
    value = _blank(value)
    if value is None:
        return None
    if isinstance(value, bool):
        _problem(path, "decimal", value, "Boolean is not a decimal")
    if isinstance(value, (Decimal, int)):
        return Decimal(value)
    if not isinstance(value, str):
        _problem(path, "decimal", value, "Expected a decimal or localized decimal string")
    raw = value.strip().replace("\u00a0", " ")
    if allow_percent:
        raw = re.sub(r"\s*%\s*$", "", raw)
    elif "%" in raw:
        _problem(path, "amount without percent", value, "Percent is allowed only for VAT rate")
    raw = _CURRENCY.sub("", raw)
    if re.search(r"[^0-9.,+\- ]", raw) or not raw.strip():
        _problem(path, "decimal", value, "Unsupported numeric format")
    raw = raw.strip()
    sign = ""
    if raw[:1] in {"+", "-"}:
        sign, raw = raw[0], raw[1:]
    if not raw or "+" in raw or "-" in raw:
        _problem(path, "decimal", value, "Invalid sign placement")
    if " " in raw:
        if not re.fullmatch(r"\d{1,3}(?: \d{3})+(?:[.,]\d+)?", raw):
            _problem(path, "unambiguous grouped decimal", value, "Invalid thousands grouping")
        raw = raw.replace(" ", "")
    comma_count = raw.count(",")
    dot_count = raw.count(".")
    if comma_count and dot_count:
        decimal_sep = "," if raw.rfind(",") > raw.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        integer, fraction = raw.rsplit(decimal_sep, 1)
        if not fraction.isdigit() or not re.fullmatch(
            rf"\d{{1,3}}(?:{re.escape(thousands_sep)}\d{{3}})+", integer
        ):
            _problem(path, "unambiguous decimal", value, "Invalid mixed separators")
        raw = integer.replace(thousands_sep, "") + "." + fraction
    elif comma_count or dot_count:
        separator = "," if comma_count else "."
        if raw.count(separator) > 1:
            if not re.fullmatch(rf"\d{{1,3}}(?:{re.escape(separator)}\d{{3}})+", raw):
                _problem(path, "unambiguous decimal", value, "Invalid repeated separator")
            raw = raw.replace(separator, "")
        else:
            integer, fraction = raw.split(separator)
            if not integer.isdigit() or not fraction.isdigit():
                _problem(path, "decimal", value, "Invalid decimal digits")
            if len(fraction) == 3 and len(integer) <= 3:
                _problem(
                    path,
                    "unambiguous decimal",
                    value,
                    "A single separator followed by three digits is ambiguous",
                )
            raw = integer + "." + fraction
    elif not raw.isdigit():
        _problem(path, "decimal", value, "Invalid decimal digits")
    try:
        return Decimal(sign + raw)
    except InvalidOperation:
        _problem(path, "decimal", value, "Invalid decimal value")


def normalize_date(value: Any, *, path: str) -> date | None:
    value = _blank(value)
    if value is None:
        return None
    if not isinstance(value, str):
        _problem(path, "YYYY-MM-DD or DD.MM.YYYY", value, "Date must be a string")
    raw = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return date.fromisoformat(raw)
        match = _CZECH_DATE.fullmatch(raw)
        if match:
            day, month, year = (int(part) for part in match.groups())
            return date(year, month, day)
    except ValueError:
        _problem(path, "real calendar date", value, "Date does not exist")
    _problem(path, "YYYY-MM-DD or DD.MM.YYYY", value, "Unsupported date format")


def _normalize_text(value: Any, *, path: str) -> str | None:
    value = _blank(value)
    if value is None:
        return None
    if path == "supplier_zip" and isinstance(value, (str, int)):
        match = _POSTAL_CODE.fullmatch(str(value).strip())
        if match:
            return f"{match.group(1)} {match.group(2)}"
    if not isinstance(value, str):
        _problem(path, "string or null", value, "Text field must be a string")
    return value.strip()


def _source(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None


def _record_change(changes: list[dict[str, Any]], path: str, raw: Any, normalized: Any) -> None:
    raw_json = _json_value(raw)
    normalized_json = _json_value(normalized)
    if raw_json != normalized_json:
        changes.append({"path": path, "raw": raw_json, "normalized": normalized_json})


def normalize_raw_extraction(
    structured: dict[str, Any],
    *,
    attempt: int = 1,
) -> tuple[InvoiceExtractionV1, dict[str, Any]]:
    try:
        raw = InvoiceExtractionRawV1.model_validate(structured)
    except ValidationError as exc:
        raise ExtractionNormalizationFailed(
            validation_error_details(exc, stage="raw_schema", attempt=attempt)
        ) from exc
    changes: list[dict[str, Any]] = []
    canonical: dict[str, Any] = {"schema_version": "invoice-extraction.v3"}
    for field in TEXT_FIELDS | DATE_FIELDS | DECIMAL_FIELDS:
        item = getattr(raw, field)
        value = item.value
        if field in DATE_FIELDS:
            normalized = normalize_date(value, path=field)
        elif field in DECIMAL_FIELDS:
            normalized = normalize_decimal(value, path=field, allow_percent=False)
        else:
            normalized = _normalize_text(value, path=field)
        source = _source(item.source_text)
        _record_change(changes, field, value, normalized)
        _record_change(changes, f"{field}.source_text", item.source_text, source)
        canonical[field] = {"value": normalized, "source_text": source}
    vat_lines = []
    for index, row in enumerate(raw.vat_lines):
        normalized_row: dict[str, Any] = {}
        for field in ("vat_rate", "taxable_base", "vat_amount", "gross_amount"):
            value = getattr(row, field)
            normalized = normalize_decimal(
                value,
                path=f"vat_lines.{index}.{field}",
                allow_percent=field == "vat_rate",
            )
            _record_change(changes, f"vat_lines.{index}.{field}", value, normalized)
            normalized_row[field] = normalized
        adjustment = _blank(row.adjustment_type)
        if isinstance(adjustment, str) and adjustment.casefold() == "rounding":
            adjustment = "ROUNDING"
        source = _source(row.source_text)
        _record_change(
            changes, f"vat_lines.{index}.adjustment_type", row.adjustment_type, adjustment
        )
        _record_change(changes, f"vat_lines.{index}.source_text", row.source_text, source)
        normalized_row.update(adjustment_type=adjustment, source_text=source)
        vat_lines.append(normalized_row)
    canonical["vat_lines"] = vat_lines
    try:
        payload = InvoiceExtractionV1.model_validate(canonical)
    except ValidationError as exc:
        raise ExtractionNormalizationFailed(
            validation_error_details(exc, stage="canonical_schema", attempt=attempt)
        ) from exc
    return payload, {
        "raw_schema_version": RAW_SCHEMA_VERSION,
        "canonical_schema_version": "invoice-extraction.v3",
        "changes": changes,
        "canonical": payload.model_dump(mode="json"),
    }
