"""Track Approval-to-Paperless document uploads without storing PDF contents.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("source_pdf_sha256", sa.String(64)))
    op.add_column("invoices", sa.Column("uploaded_by_subject", sa.String(255)))
    op.add_column("invoices", sa.Column("uploaded_by_username", sa.String(255)))
    op.create_index("ix_invoices_source_pdf_sha256", "invoices", ["source_pdf_sha256"])
    op.create_index("ix_invoices_uploaded_by_subject", "invoices", ["uploaded_by_subject"])
    op.create_table(
        "document_uploads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("actor_subject", sa.String(255), nullable=False),
        sa.Column("actor_username", sa.String(255), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "SUBMITTING",
                "PAPERLESS_PROCESSING",
                "WAITING_OCR",
                "OCR_COMPLETE",
                "FAILED_RETRYABLE",
                "FAILED",
                "SUBMISSION_UNKNOWN",
                name="documentuploadstatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("paperless_task_id", sa.String(100)),
        sa.Column("paperless_document_id", sa.Integer()),
        sa.Column("invoice_id", sa.String(36), sa.ForeignKey("invoices.id", ondelete="SET NULL")),
        sa.Column("correlation_id", sa.String(100), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("paperless_task_id"),
    )
    for name, columns in {
        "ix_document_uploads_idempotency_key": ["idempotency_key"],
        "ix_document_uploads_actor_subject": ["actor_subject"],
        "ix_document_uploads_sha256": ["sha256"],
        "ix_document_uploads_status": ["status"],
        "ix_document_uploads_paperless_task_id": ["paperless_task_id"],
        "ix_document_uploads_paperless_document_id": ["paperless_document_id"],
        "ix_document_uploads_invoice_id": ["invoice_id"],
        "ix_document_uploads_correlation_id": ["correlation_id"],
    }.items():
        op.create_index(name, "document_uploads", columns)


def downgrade() -> None:
    op.drop_table("document_uploads")
    op.drop_index("ix_invoices_uploaded_by_subject", table_name="invoices")
    op.drop_index("ix_invoices_source_pdf_sha256", table_name="invoices")
    op.drop_column("invoices", "uploaded_by_username")
    op.drop_column("invoices", "uploaded_by_subject")
    op.drop_column("invoices", "source_pdf_sha256")
