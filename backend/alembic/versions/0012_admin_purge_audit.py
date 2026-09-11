"""Minimal audit for explicit ADMIN invoice purge.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_purge_audits",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("original_invoice_id", sa.String(length=36), nullable=False),
        sa.Column("original_paperless_document_id", sa.Integer()),
        sa.Column("paperless_document_ids", sa.JSON(), nullable=False),
        sa.Column("actor_subject", sa.String(length=255), nullable=False),
        sa.Column("actor_display_name", sa.String(length=255)),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("artifact_counts", sa.JSON(), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("correlation_id", sa.String(length=100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_admin_purge_audit_invoice_created",
        "admin_purge_audits",
        ["original_invoice_id", "created_at"],
    )
    op.create_index(
        "ix_admin_purge_audit_actor_created",
        "admin_purge_audits",
        ["actor_subject", "created_at"],
    )
    op.create_index(
        "ix_admin_purge_audits_correlation_id",
        "admin_purge_audits",
        ["correlation_id"],
    )
    op.create_index(
        "ix_admin_purge_audits_created_at",
        "admin_purge_audits",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("admin_purge_audits")
