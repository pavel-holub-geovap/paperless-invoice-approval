from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import (
    AIExtractionStatus,
    ApprovalAction,
    InvoiceDisposition,
    InvoiceStatus,
    PaperlessSyncStatus,
    SourceDocumentStatus,
    ValidationSeverity,
)


class InvoiceData(BaseModel):
    supplier_name: str | None = None
    ico: str | None = None
    dic: str | None = None
    address: str | None = None
    invoice_number: str | None = None
    variable_symbol: str | None = None
    issue_date: date | None = None
    taxable_supply_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    bank_account_raw: str | None = None
    bank_account_prefix: str | None = None
    bank_account_number: str | None = None
    bank_account: str | None = None
    bank_code: str | None = None
    iban: str | None = None
    swift_bic: str | None = None
    vat_breakdown: list[dict[str, Any]] = Field(default_factory=list)
    total_amount: Decimal | None = None
    description: str | None = None


class EvidenceValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Any | None
    source_text: str | None

    @model_validator(mode="after")
    def provenance_for_value(self) -> EvidenceValue:
        if self.value is not None and not (self.source_text and self.source_text.strip()):
            raise ValueError("A non-null extracted value requires source_text provenance")
        return self


class TextEvidence(EvidenceValue):
    value: str | None


class DateEvidence(EvidenceValue):
    value: date | None


_LOCALIZED_DECIMAL = re.compile(
    r"^[+-]?(?:\d{1,3}(?:[ .\u00a0]\d{3})+|\d+)(?:[.,]\d+)?\s*(?:%|Kč|CZK|EUR)?$",
    re.IGNORECASE,
)


def _normalize_localized_decimal(value: Any) -> Any:
    """Accept only unambiguous localized numeric strings emitted by the LLM."""
    if not isinstance(value, str) or not _LOCALIZED_DECIMAL.fullmatch(value.strip()):
        return value
    normalized = re.sub(r"\s*(?:%|Kč|CZK|EUR)\s*$", "", value.strip(), flags=re.IGNORECASE)
    normalized = normalized.replace(" ", "").replace("\u00a0", "")
    if "," in normalized and "." in normalized:
        if normalized.rfind(",") > normalized.rfind("."):
            normalized = normalized.replace(".", "").replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
    elif "," in normalized:
        normalized = normalized.replace(",", ".")
    return normalized


class DecimalEvidence(EvidenceValue):
    value: Decimal | None

    @field_validator("value", mode="before")
    @classmethod
    def localized_decimal(cls, value: Any) -> Any:
        return _normalize_localized_decimal(value)


class VatLineExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vat_rate: Decimal | None
    taxable_base: Decimal | None
    vat_amount: Decimal | None
    gross_amount: Decimal | None = None
    adjustment_type: Literal["ROUNDING"] | None = None
    source_text: str | None

    @field_validator("vat_rate", "taxable_base", "vat_amount", "gross_amount", mode="before")
    @classmethod
    def localized_decimals(cls, value: Any) -> Any:
        return _normalize_localized_decimal(value)

    @model_validator(mode="after")
    def provenance_for_values(self) -> VatLineExtraction:
        if any(
            value is not None
            for value in (self.vat_rate, self.taxable_base, self.vat_amount, self.gross_amount)
        ) and not (self.source_text and self.source_text.strip()):
            raise ValueError("A non-empty VAT line requires source_text provenance")
        return self


type RawScalar = str | int | Decimal | bool | None


class RawEvidenceValue(BaseModel):
    """Strict structural boundary while preserving the LLM's scalar formatting."""

    model_config = ConfigDict(extra="forbid")

    value: RawScalar
    source_text: str | None


class RawVatLineExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vat_rate: RawScalar
    taxable_base: RawScalar
    vat_amount: RawScalar
    gross_amount: RawScalar = None
    adjustment_type: str | None = None
    source_text: str | None


class InvoiceExtractionRawV1(BaseModel):
    """Known Qwen JSON shape before deterministic accounting normalization."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["invoice-extraction.v3"]
    supplier_name: RawEvidenceValue
    supplier_ico: RawEvidenceValue
    supplier_dic: RawEvidenceValue
    supplier_address_raw: RawEvidenceValue
    supplier_street: RawEvidenceValue
    supplier_city: RawEvidenceValue
    supplier_zip: RawEvidenceValue
    invoice_number: RawEvidenceValue
    variable_symbol: RawEvidenceValue
    issue_date: RawEvidenceValue
    taxable_supply_date: RawEvidenceValue
    due_date: RawEvidenceValue
    currency: RawEvidenceValue
    bank_account: RawEvidenceValue
    bank_code: RawEvidenceValue
    iban: RawEvidenceValue
    swift_bic: RawEvidenceValue
    vat_lines: list[RawVatLineExtraction]
    total_without_vat: RawEvidenceValue
    total_vat: RawEvidenceValue
    total_amount: RawEvidenceValue
    description: RawEvidenceValue


class InvoiceExtractionV1(BaseModel):
    """Current v2 output; the historical class name is retained for API compatibility."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["invoice-extraction.v3"]
    supplier_name: TextEvidence
    supplier_ico: TextEvidence
    supplier_dic: TextEvidence
    supplier_address_raw: TextEvidence
    supplier_street: TextEvidence
    supplier_city: TextEvidence
    supplier_zip: TextEvidence
    invoice_number: TextEvidence
    variable_symbol: TextEvidence
    issue_date: DateEvidence
    taxable_supply_date: DateEvidence
    due_date: DateEvidence
    currency: TextEvidence
    bank_account: TextEvidence
    bank_code: TextEvidence
    iban: TextEvidence
    swift_bic: TextEvidence
    vat_lines: list[VatLineExtraction]
    total_without_vat: DecimalEvidence
    total_vat: DecimalEvidence
    total_amount: DecimalEvidence
    description: TextEvidence


# Compatibility name for modules outside the AI boundary; new code uses the versioned name.
ExtractionPayload = InvoiceExtractionV1


class InvoiceCreate(BaseModel):
    paperless_document_id: int = Field(gt=0)


class InvoicePatch(BaseModel):
    changes: dict[str, Any]
    comment: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class InvoiceDispositionSet(BaseModel):
    disposition: Literal["IGNORED_DUPLICATE", "IGNORED_OTHER"]
    reason: str = Field(min_length=1, max_length=100)
    comment: str | None = Field(default=None, max_length=2000)
    duplicate_of_invoice_id: str | None = None

    @model_validator(mode="after")
    def duplicate_target(self) -> InvoiceDispositionSet:
        if self.disposition == "IGNORED_DUPLICATE" and not self.duplicate_of_invoice_id:
            raise ValueError("IGNORED_DUPLICATE requires duplicate_of_invoice_id")
        if self.disposition == "IGNORED_OTHER" and self.duplicate_of_invoice_id:
            raise ValueError("IGNORED_OTHER cannot reference a duplicate")
        return self


class InvoiceDispositionRestore(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class AIExtractionApply(BaseModel):
    confirm_overwrite: bool = False


class AllocationVatInput(BaseModel):
    rate: Decimal = Field(ge=0, le=100)
    base: Decimal
    vat: Decimal


class AllocationInput(BaseModel):
    cost_center_id: str
    amount: Decimal | None = Field(default=None, ge=0)
    percentage: Decimal | None = Field(default=None, ge=0, le=100)
    note: str | None = Field(default=None, max_length=2000)
    vat_breakdown: list[AllocationVatInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def one_mode(self) -> AllocationInput:
        if (self.amount is None) == (self.percentage is None):
            raise ValueError("Provide exactly one of amount or percentage")
        return self


class AllocationSet(BaseModel):
    allocations: list[AllocationInput] = Field(min_length=1)
    expected_revision: int | None = Field(default=None, ge=1)


class ApproverSet(BaseModel):
    approver_subjects: list[str] = Field(default_factory=list)
    expected_revision: int | None = Field(default=None, ge=1)


class ApprovalRequest(BaseModel):
    action: ApprovalAction
    comment: str | None = None

    @model_validator(mode="after")
    def comment_for_negative_action(self) -> ApprovalRequest:
        if self.action in {ApprovalAction.RETURN, ApprovalAction.REJECT} and not (
            self.comment and self.comment.strip()
        ):
            raise ValueError("RETURN and REJECT require a comment")
        return self


class ValidationOut(BaseModel):
    code: str
    severity: ValidationSeverity
    field_name: str | None
    message: str
    expected: Any | None = None
    actual: Any | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class InvoiceListItem(BaseModel):
    id: str
    paperless_document_id: int
    status: InvoiceStatus
    disposition: InvoiceDisposition
    source_status: SourceDocumentStatus
    source_missing_at: datetime | None = None
    current_revision_number: int
    title: str
    correspondent: str | None
    paperless_created_at: datetime | None
    sync_status: PaperlessSyncStatus
    ai_status: AIExtractionStatus
    supplier_name: str | None
    invoice_number: str | None
    total_amount: Decimal | None
    due_date: str | None
    approvals_done: int = 0
    approvals_required: int = 0
    warning_count: int = 0
    blocking_error_count: int = 0
    updated_at: datetime


class CostCenterIn(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=255)
    pohoda_code: str = Field(min_length=1, max_length=100)
    active: bool = True


class CostCenterOut(CostCenterIn):
    id: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ExportCreate(BaseModel):
    invoice_ids: list[str] = Field(min_length=1)


class ExportGenerate(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class ImportConfirmation(BaseModel):
    confirmed: bool

    @model_validator(mode="after")
    def must_be_confirmed(self) -> ImportConfirmation:
        if not self.confirmed:
            raise ValueError("Explicit import confirmation is required")
        return self


class CurrentUser(BaseModel):
    subject: str
    username: str
    email: str | None = None
    roles: list[str]
    csrf_token: str | None = None


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    component: str
