from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class InvoiceStatus(enum.StrEnum):
    NEW = "NEW"
    AI_PROCESSING = "AI_PROCESSING"
    VALIDATION = "VALIDATION"
    QUEUE_REVIEW = "QUEUE_REVIEW"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    RETURNED = "RETURNED"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"
    XML_READY = "XML_READY"
    READY_FOR_EXPORT = "READY_FOR_EXPORT"
    EXPORT_CREATED = "EXPORT_CREATED"
    IMPORTED_TO_POHODA = "IMPORTED_TO_POHODA"


class ValidationSeverity(enum.StrEnum):
    OK = "OK"
    WARNING = "WARNING"
    BLOCKING_ERROR = "BLOCKING_ERROR"


class ApprovalAction(enum.StrEnum):
    APPROVE = "APPROVE"
    RETURN = "RETURN"
    REJECT = "REJECT"


class ApprovalAssignmentStatus(enum.StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    RETURNED = "RETURNED"
    REJECTED = "REJECTED"
    INVALIDATED = "INVALIDATED"


class JobStatus(enum.StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class PaperlessSyncStatus(enum.StrEnum):
    PENDING = "PENDING"
    SYNCED = "SYNCED"
    ERROR = "ERROR"


class DocumentUploadStatus(enum.StrEnum):
    SUBMITTING = "SUBMITTING"
    PAPERLESS_PROCESSING = "PAPERLESS_PROCESSING"
    WAITING_OCR = "WAITING_OCR"
    OCR_COMPLETE = "OCR_COMPLETE"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED = "FAILED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"


class InvoiceDisposition(enum.StrEnum):
    ACTIVE = "ACTIVE"
    IGNORED_DUPLICATE = "IGNORED_DUPLICATE"
    IGNORED_OTHER = "IGNORED_OTHER"


class SourceDocumentStatus(enum.StrEnum):
    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"


class AIExtractionStatus(enum.StrEnum):
    AI_PENDING = "AI_PENDING"
    AI_PROCESSING = "AI_PROCESSING"
    AI_COMPLETED = "AI_COMPLETED"
    AI_FAILED = "AI_FAILED"


class ExportBatchStatus(enum.StrEnum):
    CREATED = "CREATED"
    IMPORTED = "IMPORTED"


class ExportArtifactStatus(enum.StrEnum):
    XSD_VALID = "XSD_VALID"
    XSD_INVALID = "XSD_INVALID"


class UserIdentity(Base):
    __tablename__ = "user_identities"

    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    roles: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class OidcSession(Base):
    __tablename__ = "oidc_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(ForeignKey("user_identities.subject"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    user: Mapped[UserIdentity] = relationship()


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    paperless_document_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    paperless_title: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    paperless_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paperless_correspondent_id: Mapped[int | None] = mapped_column(Integer)
    paperless_correspondent_name: Mapped[str | None] = mapped_column(String(255))
    paperless_tag_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    paperless_tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    paperless_ocr_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    paperless_original_filename: Mapped[str | None] = mapped_column(String(255))
    source_pdf_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    uploaded_by_subject: Mapped[str | None] = mapped_column(String(255), index=True)
    uploaded_by_username: Mapped[str | None] = mapped_column(String(255))
    sync_status: Mapped[PaperlessSyncStatus] = mapped_column(
        Enum(PaperlessSyncStatus, native_enum=False),
        default=PaperlessSyncStatus.PENDING,
        nullable=False,
        index=True,
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_error: Mapped[str | None] = mapped_column(Text)
    disposition: Mapped[InvoiceDisposition] = mapped_column(
        Enum(InvoiceDisposition, native_enum=False),
        default=InvoiceDisposition.ACTIVE,
        nullable=False,
        index=True,
    )
    disposition_reason: Mapped[str | None] = mapped_column(String(100))
    disposition_comment: Mapped[str | None] = mapped_column(Text)
    disposition_actor: Mapped[str | None] = mapped_column(String(255))
    disposition_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duplicate_of_invoice_id: Mapped[str | None] = mapped_column(
        ForeignKey("invoices.id"), index=True
    )
    source_status: Mapped[SourceDocumentStatus] = mapped_column(
        Enum(SourceDocumentStatus, native_enum=False),
        default=SourceDocumentStatus.AVAILABLE,
        nullable=False,
        index=True,
    )
    source_missing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ai_status: Mapped[AIExtractionStatus] = mapped_column(
        Enum(AIExtractionStatus, native_enum=False),
        default=AIExtractionStatus.AI_PENDING,
        nullable=False,
        index=True,
    )
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, native_enum=False), default=InvoiceStatus.NEW, index=True
    )
    current_revision_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    original_review_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    original_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    original_reviewed_by: Mapped[str | None] = mapped_column(String(255))
    imported_to_pohoda_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_to_pohoda_by: Mapped[str | None] = mapped_column(String(255))
    imported_export_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    revisions: Mapped[list[InvoiceRevision]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceRevision.number"
    )
    allocations: Mapped[list[Allocation]] = relationship(back_populates="invoice")
    ai_extractions: Mapped[list[AIExtraction]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="AIExtraction.extraction_revision",
    )

    @property
    def current_revision(self) -> InvoiceRevision | None:
        return next(
            (r for r in reversed(self.revisions) if r.number == self.current_revision_number), None
        )


class InvoiceRevision(Base):
    __tablename__ = "invoice_revisions"
    __table_args__ = (UniqueConstraint("invoice_id", "number", name="uq_invoice_revision"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    invoice: Mapped[Invoice] = relationship(back_populates="revisions")
    extracted_fields: Mapped[list[ExtractedField]] = relationship(cascade="all, delete-orphan")
    validation_results: Mapped[list[ValidationResult]] = relationship(cascade="all, delete-orphan")


class ExtractedField(Base):
    __tablename__ = "extracted_fields"
    __table_args__ = (UniqueConstraint("revision_id", "field_name", name="uq_extracted_field"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("invoice_revisions.id", ondelete="CASCADE"), index=True
    )
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Any | None] = mapped_column(JSON)
    source_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ValidationResult(Base):
    __tablename__ = "validation_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("invoice_revisions.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[ValidationSeverity] = mapped_column(
        Enum(ValidationSeverity, native_enum=False), index=True
    )
    field_name: Mapped[str | None] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    expected: Mapped[Any | None] = mapped_column(JSON)
    actual: Mapped[Any | None] = mapped_column(JSON)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AIExtraction(Base):
    __tablename__ = "ai_extractions"
    __table_args__ = (
        UniqueConstraint("invoice_id", "extraction_revision", name="uq_ai_extraction_revision"),
        Index("ix_ai_extraction_invoice_created", "invoice_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    invoice_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("invoice_revisions.id", ondelete="SET NULL"), index=True
    )
    extraction_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[AIExtractionStatus] = mapped_column(
        Enum(AIExtractionStatus, native_enum=False), nullable=False, index=True
    )
    raw_response: Mapped[str | None] = mapped_column(Text)
    raw_attempts_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    parsed_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    schema_validation_errors_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    normalization_result_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    corrective_retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    validation_results_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    validation_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_by: Mapped[str | None] = mapped_column(String(255))
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    invoice: Mapped[Invoice] = relationship(back_populates="ai_extractions")
    invoice_revision: Mapped[InvoiceRevision | None] = relationship()


class CostCenter(Base):
    __tablename__ = "cost_centers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    pohoda_code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Allocation(Base):
    __tablename__ = "allocations"
    __table_args__ = (
        Index(
            "uq_active_revision_cost_center",
            "revision_id",
            "cost_center_id",
            unique=True,
            postgresql_where=text("active"),
            sqlite_where=text("active = 1"),
        ),
        Index("ix_allocation_invoice_revision", "invoice_id", "revision_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("invoice_revisions.id", ondelete="CASCADE"), index=True
    )
    cost_center_id: Mapped[str] = mapped_column(ForeignKey("cost_centers.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    percentage: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    note: Mapped[str | None] = mapped_column(Text)
    vat_breakdown: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), default="system", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    invoice: Mapped[Invoice] = relationship(back_populates="allocations")
    cost_center: Mapped[CostCenter] = relationship()
    assignments: Mapped[list[ApprovalAssignment]] = relationship(back_populates="allocation")


class ApprovalAssignment(Base):
    __tablename__ = "approval_assignments"
    __table_args__ = (
        UniqueConstraint(
            "revision_id",
            "allocation_id",
            "approver_subject",
            name="uq_revision_allocation_approver",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("invoice_revisions.id", ondelete="CASCADE"), index=True
    )
    allocation_id: Mapped[str] = mapped_column(
        ForeignKey("allocations.id", ondelete="CASCADE"), index=True
    )
    approver_subject: Mapped[str] = mapped_column(String(255), index=True)
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[ApprovalAssignmentStatus] = mapped_column(
        Enum(ApprovalAssignmentStatus, native_enum=False),
        default=ApprovalAssignmentStatus.PENDING,
        nullable=False,
        index=True,
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    assigned_by: Mapped[str] = mapped_column(String(255), default="system", nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidation_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    allocation: Mapped[Allocation] = relationship(back_populates="assignments")
    decisions: Mapped[list[ApprovalDecision]] = relationship(back_populates="assignment")


class ApprovalDecision(Base):
    __tablename__ = "approval_decisions"
    __table_args__ = (
        Index(
            "uq_approval_decision_valid_assignment",
            "assignment_id",
            unique=True,
            postgresql_where=text("valid"),
            sqlite_where=text("valid = 1"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("approval_assignments.id"), index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("invoice_revisions.id"), index=True)
    action: Mapped[ApprovalAction] = mapped_column(Enum(ApprovalAction, native_enum=False))
    actor_subject: Mapped[str] = mapped_column(String(255), index=True)
    comment: Mapped[str | None] = mapped_column(Text)
    valid: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidation_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    assignment: Mapped[ApprovalAssignment] = relationship(back_populates="decisions")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_invoice_created", "invoice_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoices.id"), index=True)
    revision_number: Mapped[int | None] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    actor_subject: Mapped[str] = mapped_column(String(255), default="system")
    old_state: Mapped[str | None] = mapped_column(String(50))
    new_state: Mapped[str | None] = mapped_column(String(50))
    old_value: Mapped[Any | None] = mapped_column(JSON)
    new_value: Mapped[Any | None] = mapped_column(JSON)
    comment: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class DocumentUpload(Base):
    __tablename__ = "document_uploads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    actor_subject: Mapped[str] = mapped_column(String(255), index=True)
    actor_username: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[DocumentUploadStatus] = mapped_column(
        Enum(DocumentUploadStatus, native_enum=False),
        default=DocumentUploadStatus.SUBMITTING,
        nullable=False,
        index=True,
    )
    paperless_task_id: Mapped[str | None] = mapped_column(
        String(100), unique=True, index=True
    )
    paperless_document_id: Mapped[int | None] = mapped_column(Integer, index=True)
    invoice_id: Mapped[str | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"), index=True
    )
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_type: Mapped[str] = mapped_column(String(100), index=True)
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoices.id"), index=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False), default=JobStatus.PENDING, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SystemHeartbeat(Base):
    __tablename__ = "system_heartbeats"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ExportBatch(Base):
    __tablename__ = "export_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    status: Mapped[ExportBatchStatus] = mapped_column(
        Enum(ExportBatchStatus, native_enum=False), default=ExportBatchStatus.CREATED
    )
    archive_path: Mapped[str] = mapped_column(Text, nullable=False)
    archive_sha256: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    imported_by: Mapped[str | None] = mapped_column(String(255))
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list[ExportBatchItem]] = relationship(cascade="all, delete-orphan")


class ExportBatchItem(Base):
    __tablename__ = "export_batch_items"
    __table_args__ = (UniqueConstraint("batch_id", "invoice_id", name="uq_batch_invoice"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("export_batches.id", ondelete="CASCADE"), index=True
    )
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("invoice_revisions.id"))
    export_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("export_artifacts.id"), index=True
    )
    pdf_filename: Mapped[str] = mapped_column(String(255))
    xml_filename: Mapped[str] = mapped_column(String(255))
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExportArtifact(Base):
    __tablename__ = "export_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("invoice_revisions.id"), index=True)
    source_export_id: Mapped[str | None] = mapped_column(
        ForeignKey("export_artifacts.id"), index=True
    )
    status: Mapped[ExportArtifactStatus] = mapped_column(
        Enum(ExportArtifactStatus, native_enum=False), nullable=False, index=True
    )
    generator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    xsd_bundle_version: Mapped[str] = mapped_column(String(32), nullable=False)
    encoding: Mapped[str] = mapped_column(String(32), nullable=False)
    source_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    xml_path: Mapped[str] = mapped_column(Text, nullable=False)
    xml_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    xml_size: Mapped[int] = mapped_column(Integer, nullable=False)
    pdf_sha256: Mapped[str | None] = mapped_column(String(64))
    validation_errors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    reexport_reason: Mapped[str | None] = mapped_column(Text)
    generated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    imported_by: Mapped[str | None] = mapped_column(String(255))
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PohodaResponseUpload(Base):
    __tablename__ = "pohoda_response_uploads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    export_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("export_artifacts.id"), index=True
    )
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("export_batches.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parse_status: Mapped[str] = mapped_column(String(32), nullable=False)
    parsed_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    parse_errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
