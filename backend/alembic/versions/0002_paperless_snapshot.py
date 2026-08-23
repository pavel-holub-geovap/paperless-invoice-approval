"""Store the Paperless metadata and OCR snapshot used by the approval UI.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("paperless_title", sa.String(length=512), nullable=False, server_default=""),
    )
    op.add_column("invoices", sa.Column("paperless_created_at", sa.DateTime(timezone=True)))
    op.add_column("invoices", sa.Column("paperless_correspondent_id", sa.Integer()))
    op.add_column("invoices", sa.Column("paperless_correspondent_name", sa.String(length=255)))
    op.add_column(
        "invoices",
        sa.Column("paperless_tag_ids", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "invoices",
        sa.Column("paperless_tags", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "invoices",
        sa.Column("paperless_ocr_text", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column("invoices", sa.Column("paperless_original_filename", sa.String(length=255)))
    op.add_column(
        "invoices",
        sa.Column("sync_status", sa.String(length=7), nullable=False, server_default="PENDING"),
    )
    op.add_column("invoices", sa.Column("last_synced_at", sa.DateTime(timezone=True)))
    op.add_column("invoices", sa.Column("sync_error", sa.Text()))
    op.create_index("ix_invoices_sync_status", "invoices", ["sync_status"])


def downgrade() -> None:
    op.drop_index("ix_invoices_sync_status", table_name="invoices")
    for column in (
        "sync_error",
        "last_synced_at",
        "sync_status",
        "paperless_original_filename",
        "paperless_ocr_text",
        "paperless_tags",
        "paperless_tag_ids",
        "paperless_correspondent_name",
        "paperless_correspondent_id",
        "paperless_created_at",
        "paperless_title",
    ):
        op.drop_column("invoices", column)
