from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import ApprovalAction, InvoiceStatus, PaperlessSyncStatus, ValidationSeverity


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
    bank_account: str | None = None
    iban: str | None = None
    vat_breakdown: list[dict[str, Any]] = Field(default_factory=list)
    total_amount: Decimal | None = None
    description: str | None = None


class EvidenceValue(BaseModel):
    value: Any | None = None
    source_text: str | None = None


class TextEvidence(EvidenceValue):
    value: str | None = None


class DateEvidence(EvidenceValue):
    value: date | None = None


class DecimalEvidence(EvidenceValue):
    value: Decimal | None = None


class VatRow(BaseModel):
    base: Decimal
    rate: Decimal
    vat: Decimal


class VatEvidence(EvidenceValue):
    value: list[VatRow] | None = None


class ExtractionPayload(BaseModel):
    supplier_name: TextEvidence = Field(default_factory=TextEvidence)
    ico: TextEvidence = Field(default_factory=TextEvidence)
    dic: TextEvidence = Field(default_factory=TextEvidence)
    address: TextEvidence = Field(default_factory=TextEvidence)
    invoice_number: TextEvidence = Field(default_factory=TextEvidence)
    variable_symbol: TextEvidence = Field(default_factory=TextEvidence)
    issue_date: DateEvidence = Field(default_factory=DateEvidence)
    taxable_supply_date: DateEvidence = Field(default_factory=DateEvidence)
    due_date: DateEvidence = Field(default_factory=DateEvidence)
    currency: TextEvidence = Field(default_factory=TextEvidence)
    bank_account: TextEvidence = Field(default_factory=TextEvidence)
    iban: TextEvidence = Field(default_factory=TextEvidence)
    vat_breakdown: VatEvidence = Field(default_factory=VatEvidence)
    total_amount: DecimalEvidence = Field(default_factory=DecimalEvidence)
    description: TextEvidence = Field(default_factory=TextEvidence)


class InvoiceCreate(BaseModel):
    paperless_document_id: int = Field(gt=0)


class InvoicePatch(BaseModel):
    changes: dict[str, Any]
    comment: str | None = None


class AllocationInput(BaseModel):
    cost_center_id: str
    amount: Decimal | None = Field(default=None, ge=0)
    percentage: Decimal | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def one_mode(self) -> AllocationInput:
        if (self.amount is None) == (self.percentage is None):
            raise ValueError("Provide exactly one of amount or percentage")
        return self


class AllocationSet(BaseModel):
    allocations: list[AllocationInput] = Field(min_length=1)


class ApproverSet(BaseModel):
    approver_subjects: list[str] = Field(default_factory=list)


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

    model_config = ConfigDict(from_attributes=True)


class InvoiceListItem(BaseModel):
    id: str
    paperless_document_id: int
    status: InvoiceStatus
    current_revision_number: int
    title: str
    correspondent: str | None
    paperless_created_at: datetime | None
    sync_status: PaperlessSyncStatus
    supplier_name: str | None
    invoice_number: str | None
    total_amount: Decimal | None
    due_date: str | None
    approvals_done: int = 0
    approvals_required: int = 0
    updated_at: datetime


class CostCenterIn(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=255)
    pohoda_code: str = Field(min_length=1, max_length=100)
    active: bool = True


class CostCenterOut(CostCenterIn):
    id: str
    model_config = ConfigDict(from_attributes=True)


class ExportCreate(BaseModel):
    invoice_ids: list[str] = Field(min_length=1)


class CurrentUser(BaseModel):
    subject: str
    username: str
    email: str | None = None
    roles: list[str]
    csrf_token: str | None = None


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    component: str
