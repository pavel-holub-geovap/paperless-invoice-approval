"""Initial invoice approval schema.

Revision ID: 0001
Revises: None

Keep this revision self-contained. Importing the live ORM metadata here makes a
fresh database receive columns that belong to later revisions.
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cost_centers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("pohoda_code", sa.String(length=100), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        sa.UniqueConstraint("pohoda_code"),
    )
    op.create_table(
        "export_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("batch_number", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum("CREATED", "IMPORTED", name="exportbatchstatus", native_enum=False),
            nullable=False,
        ),
        sa.Column("archive_path", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_by", sa.String(length=255)),
        sa.Column("imported_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_export_batches_batch_number", "export_batches", ["batch_number"], unique=True)
    op.create_table(
        "invoices",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("paperless_document_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "NEW",
                "AI_PROCESSING",
                "VALIDATION",
                "QUEUE_REVIEW",
                "NEEDS_REVIEW",
                "READY_FOR_APPROVAL",
                "AWAITING_APPROVAL",
                "RETURNED",
                "REJECTED",
                "APPROVED",
                "XML_READY",
                "READY_FOR_EXPORT",
                "EXPORT_CREATED",
                "IMPORTED_TO_POHODA",
                name="invoicestatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("current_revision_number", sa.Integer(), nullable=False),
        sa.Column("original_checked_at", sa.DateTime(timezone=True)),
        sa.Column("original_checked_by", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_invoices_paperless_document_id",
        "invoices",
        ["paperless_document_id"],
        unique=True,
    )
    op.create_index("ix_invoices_status", "invoices", ["status"])
    op.create_table(
        "system_heartbeats",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "user_identities",
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320)),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("subject"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.String(length=36)),
        sa.Column("revision_number", sa.Integer()),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("actor_subject", sa.String(length=255), nullable=False),
        sa.Column("old_state", sa.String(length=50)),
        sa.Column("new_state", sa.String(length=50)),
        sa.Column("old_value", sa.JSON()),
        sa.Column("new_value", sa.JSON()),
        sa.Column("comment", sa.Text()),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_invoice_id", "audit_events", ["invoice_id"])
    op.create_index("ix_audit_invoice_created", "audit_events", ["invoice_id", "created_at"])
    op.create_table(
        "invoice_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_id", "number", name="uq_invoice_revision"),
    )
    op.create_index("ix_invoice_revisions_invoice_id", "invoice_revisions", ["invoice_id"])
    op.create_table(
        "oidc_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text()),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["subject"], ["user_identities.subject"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_oidc_sessions_expires_at", "oidc_sessions", ["expires_at"])
    op.create_index("ix_oidc_sessions_subject", "oidc_sessions", ["subject"])
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("invoice_id", sa.String(length=36)),
        sa.Column(
            "status",
            sa.Enum("PENDING", "RUNNING", "DONE", "FAILED", name="jobstatus", native_enum=False),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for index_name, columns in (
        ("ix_processing_jobs_available_at", ["available_at"]),
        ("ix_processing_jobs_invoice_id", ["invoice_id"]),
        ("ix_processing_jobs_job_type", ["job_type"]),
        ("ix_processing_jobs_locked_until", ["locked_until"]),
        ("ix_processing_jobs_status", ["status"]),
    ):
        op.create_index(index_name, "processing_jobs", columns)
    op.create_table(
        "allocations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("cost_center_id", sa.String(length=36), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("percentage", sa.Numeric(precision=9, scale=6)),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["cost_center_id"], ["cost_centers.id"]),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revision_id"], ["invoice_revisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_id", "cost_center_id", name="uq_revision_cost_center"),
    )
    op.create_index("ix_allocation_invoice_revision", "allocations", ["invoice_id", "revision_id"])
    op.create_index("ix_allocations_cost_center_id", "allocations", ["cost_center_id"])
    op.create_index("ix_allocations_invoice_id", "allocations", ["invoice_id"])
    op.create_index("ix_allocations_revision_id", "allocations", ["revision_id"])
    op.create_table(
        "export_batch_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("pdf_filename", sa.String(length=255), nullable=False),
        sa.Column("xml_filename", sa.String(length=255), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["batch_id"], ["export_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["invoice_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "invoice_id", name="uq_batch_invoice"),
    )
    op.create_index("ix_export_batch_items_batch_id", "export_batch_items", ["batch_id"])
    op.create_index("ix_export_batch_items_invoice_id", "export_batch_items", ["invoice_id"])
    op.create_table(
        "extracted_fields",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("field_name", sa.String(length=100), nullable=False),
        sa.Column("value", sa.JSON()),
        sa.Column("source_text", sa.Text()),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["revision_id"], ["invoice_revisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_id", "field_name", name="uq_extracted_field"),
    )
    op.create_index("ix_extracted_fields_revision_id", "extracted_fields", ["revision_id"])
    op.create_table(
        "validation_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column(
            "severity",
            sa.Enum(
                "OK", "WARNING", "BLOCKING_ERROR", name="validationseverity", native_enum=False
            ),
            nullable=False,
        ),
        sa.Column("field_name", sa.String(length=100)),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["revision_id"], ["invoice_revisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_validation_results_revision_id", "validation_results", ["revision_id"])
    op.create_index("ix_validation_results_severity", "validation_results", ["severity"])
    op.create_table(
        "approval_assignments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("allocation_id", sa.String(length=36), nullable=False),
        sa.Column("approver_subject", sa.String(length=255), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["allocation_id"], ["allocations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revision_id"], ["invoice_revisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "revision_id",
            "allocation_id",
            "approver_subject",
            name="uq_revision_allocation_approver",
        ),
    )
    for index_name, columns in (
        ("ix_approval_assignments_allocation_id", ["allocation_id"]),
        ("ix_approval_assignments_approver_subject", ["approver_subject"]),
        ("ix_approval_assignments_invoice_id", ["invoice_id"]),
        ("ix_approval_assignments_revision_id", ["revision_id"]),
    ):
        op.create_index(index_name, "approval_assignments", columns)
    op.create_table(
        "approval_decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("assignment_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column(
            "action",
            sa.Enum("APPROVE", "RETURN", "REJECT", name="approvalaction", native_enum=False),
            nullable=False,
        ),
        sa.Column("actor_subject", sa.String(length=255), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("valid", sa.Boolean(), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.Column("invalidation_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assignment_id"], ["approval_assignments.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["invoice_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns in (
        ("ix_approval_decisions_actor_subject", ["actor_subject"]),
        ("ix_approval_decisions_assignment_id", ["assignment_id"]),
        ("ix_approval_decisions_revision_id", ["revision_id"]),
        ("ix_approval_decisions_valid", ["valid"]),
    ):
        op.create_index(index_name, "approval_decisions", columns)


def downgrade() -> None:
    for table_name in (
        "approval_decisions",
        "approval_assignments",
        "validation_results",
        "extracted_fields",
        "export_batch_items",
        "allocations",
        "processing_jobs",
        "oidc_sessions",
        "invoice_revisions",
        "audit_events",
        "user_identities",
        "system_heartbeats",
        "invoices",
        "export_batches",
        "cost_centers",
    ):
        op.drop_table(table_name)
