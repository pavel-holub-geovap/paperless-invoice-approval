"""Add versioned AI extraction persistence and explicit original review fields.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("ai_status", sa.String(length=13), nullable=False, server_default="AI_PENDING"),
    )
    op.create_index("ix_invoices_ai_status", "invoices", ["ai_status"])
    op.alter_column("invoices", "original_checked_at", new_column_name="original_reviewed_at")
    op.alter_column("invoices", "original_checked_by", new_column_name="original_reviewed_by")
    op.add_column(
        "invoices",
        sa.Column(
            "original_review_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.execute(
        sa.text(
            "UPDATE invoices SET original_review_confirmed = true "
            "WHERE original_reviewed_at IS NOT NULL"
        )
    )
    op.add_column("validation_results", sa.Column("expected", sa.JSON()))
    op.add_column("validation_results", sa.Column("actual", sa.JSON()))

    op.create_table(
        "ai_extractions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("invoice_revision_id", sa.String(length=36)),
        sa.Column("extraction_revision", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("schema_version", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=13), nullable=False),
        sa.Column("raw_response", sa.Text()),
        sa.Column("parsed_result", sa.JSON()),
        sa.Column("validation_results_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("validation_summary", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("applied_by", sa.String(length=255)),
        sa.Column("requires_confirmation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["invoice_revision_id"], ["invoice_revisions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "invoice_id", "extraction_revision", name="uq_ai_extraction_revision"
        ),
    )
    op.create_index("ix_ai_extractions_invoice_id", "ai_extractions", ["invoice_id"])
    op.create_index(
        "ix_ai_extractions_invoice_revision_id", "ai_extractions", ["invoice_revision_id"]
    )
    op.create_index("ix_ai_extractions_status", "ai_extractions", ["status"])
    op.create_index(
        "ix_ai_extraction_invoice_created", "ai_extractions", ["invoice_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("ai_extractions")
    op.drop_column("validation_results", "actual")
    op.drop_column("validation_results", "expected")
    op.drop_column("invoices", "original_review_confirmed")
    op.alter_column("invoices", "original_reviewed_by", new_column_name="original_checked_by")
    op.alter_column("invoices", "original_reviewed_at", new_column_name="original_checked_at")
    op.drop_index("ix_invoices_ai_status", table_name="invoices")
    op.drop_column("invoices", "ai_status")
