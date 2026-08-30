"""Document classification, ISDOC extraction and approved PDF artifacts.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("document_type", sa.String(length=64), nullable=False, server_default="UNCLASSIFIED"),
    )
    op.add_column(
        "invoices",
        sa.Column("processing_mode", sa.String(length=32), nullable=False, server_default="FOR_APPROVAL"),
    )
    op.add_column(
        "invoices",
        sa.Column("extraction_source", sa.String(length=32), nullable=False, server_default="UNDETERMINED"),
    )
    op.add_column(
        "invoices", sa.Column("has_embedded_isdoc", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column(
        "invoices", sa.Column("isdoc_status", sa.String(length=32), nullable=False, server_default="UNCHECKED")
    )
    op.add_column("invoices", sa.Column("isdoc_version", sa.String(length=32)))
    op.add_column("invoices", sa.Column("isdoc_filename", sa.String(length=255)))
    op.add_column("invoices", sa.Column("isdoc_sha256", sa.String(length=64)))
    op.add_column("invoices", sa.Column("isdoc_validation_error", sa.Text()))
    op.add_column(
        "invoices", sa.Column("pohoda_eligible", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column(
        "invoices",
        sa.Column("pohoda_import_method", sa.String(length=32), nullable=False, server_default="NONE"),
    )
    op.add_column("invoices", sa.Column("manual_handoff_at", sa.DateTime(timezone=True)))
    op.add_column("invoices", sa.Column("manual_handoff_by", sa.String(length=255)))
    op.add_column("invoices", sa.Column("manual_handoff_note", sa.Text()))

    # Existing rows are the established received-invoice workflow. New rows retain
    # the model default UNCLASSIFIED and must be classified by a queue manager.
    op.execute(
        "UPDATE invoices SET document_type='RECEIVED_INVOICE', "
        "processing_mode='FOR_APPROVAL', extraction_source='OCR_AI', "
        "isdoc_status='NOT_PRESENT', pohoda_eligible=TRUE, "
        "pohoda_import_method='GENERATED_XML'"
    )
    op.create_index("ix_invoices_document_type", "invoices", ["document_type"])
    op.create_index("ix_invoices_processing_mode", "invoices", ["processing_mode"])
    op.create_index("ix_invoices_extraction_source", "invoices", ["extraction_source"])
    op.create_index("ix_invoices_isdoc_status", "invoices", ["isdoc_status"])
    op.create_index("ix_invoices_isdoc_sha256", "invoices", ["isdoc_sha256"])
    op.create_index("ix_invoices_pohoda_import_method", "invoices", ["pohoda_import_method"])

    op.create_table(
        "isdoc_extractions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("invoice_id", sa.String(length=36), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invoice_revision_id", sa.String(length=36), sa.ForeignKey("invoice_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("isdoc_sha256", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("mapped_data", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("attachment_metadata", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("invoice_id", "isdoc_sha256", name="uq_invoice_isdoc_sha256"),
    )
    op.create_index("ix_isdoc_extractions_invoice_id", "isdoc_extractions", ["invoice_id"])
    op.create_index("ix_isdoc_extractions_invoice_revision_id", "isdoc_extractions", ["invoice_revision_id"])
    op.create_index("ix_isdoc_extractions_isdoc_sha256", "isdoc_extractions", ["isdoc_sha256"])

    op.create_table(
        "approved_pdf_artifacts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("invoice_id", sa.String(length=36), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_id", sa.String(length=36), sa.ForeignKey("invoice_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stamp_version", sa.String(length=32), nullable=False),
        sa.Column("approval_snapshot", sa.JSON(), nullable=False),
        sa.Column("approval_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("original_pdf_sha256", sa.String(length=64), nullable=False),
        sa.Column("approved_pdf_sha256", sa.String(length=64)),
        sa.Column("approved_pdf_size", sa.Integer()),
        sa.Column("paperless_document_id", sa.Integer()),
        sa.Column("paperless_task_id", sa.String(length=100)),
        sa.Column("attachment_manifest", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stored_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "revision_id", "approval_snapshot_sha256", "stamp_version",
            name="uq_approved_pdf_identity",
        ),
        sa.UniqueConstraint("paperless_document_id"),
        sa.UniqueConstraint("paperless_task_id"),
    )
    op.create_index("ix_approved_pdf_artifacts_invoice_id", "approved_pdf_artifacts", ["invoice_id"])
    op.create_index("ix_approved_pdf_artifacts_revision_id", "approved_pdf_artifacts", ["revision_id"])
    op.create_index("ix_approved_pdf_artifacts_status", "approved_pdf_artifacts", ["status"])
    op.create_index("ix_approved_pdf_artifacts_approved_pdf_sha256", "approved_pdf_artifacts", ["approved_pdf_sha256"])
    op.create_index("ix_approved_pdf_artifacts_paperless_document_id", "approved_pdf_artifacts", ["paperless_document_id"])
    op.create_index("ix_approved_pdf_artifacts_paperless_task_id", "approved_pdf_artifacts", ["paperless_task_id"])


def downgrade() -> None:
    op.drop_table("approved_pdf_artifacts")
    op.drop_table("isdoc_extractions")
    for name in (
        "pohoda_import_method", "isdoc_sha256", "isdoc_status", "extraction_source",
        "processing_mode", "document_type",
    ):
        op.drop_index(f"ix_invoices_{name}", table_name="invoices")
    for name in (
        "manual_handoff_note", "manual_handoff_by", "manual_handoff_at",
        "pohoda_import_method", "pohoda_eligible", "isdoc_validation_error",
        "isdoc_sha256", "isdoc_filename", "isdoc_version", "isdoc_status",
        "has_embedded_isdoc", "extraction_source", "processing_mode", "document_type",
    ):
        op.drop_column("invoices", name)
