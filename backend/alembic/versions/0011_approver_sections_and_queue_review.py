"""Approver section permissions and revision-bound queue review.

Revision ID: 0011
Revises: 0010
"""

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column(
            "upload_origin",
            sa.String(length=32),
            nullable=False,
            server_default="PAPERLESS_SYNC",
        ),
    )
    op.execute(
        "UPDATE invoices SET upload_origin='QUEUE_MANAGER' "
        "WHERE uploaded_by_subject IS NOT NULL"
    )
    op.create_index("ix_invoices_upload_origin", "invoices", ["upload_origin"])

    op.add_column(
        "document_uploads",
        sa.Column("actor_role", sa.String(length=32), nullable=False, server_default="QUEUE_MANAGER"),
    )
    op.add_column(
        "invoice_revisions", sa.Column("submitted_to_queue_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "invoice_revisions", sa.Column("submitted_to_queue_by", sa.String(length=255))
    )
    op.add_column(
        "invoice_revisions", sa.Column("queue_manager_reviewed_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "invoice_revisions", sa.Column("queue_manager_reviewed_by", sa.String(length=255))
    )

    op.create_table(
        "approver_section_permissions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "approver_subject",
            sa.String(length=255),
            sa.ForeignKey("user_identities.subject"),
            nullable=False,
        ),
        sa.Column(
            "cost_center_id",
            sa.String(length=36),
            sa.ForeignKey("cost_centers.id"),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("granted_by", sa.String(length=255), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_by", sa.String(length=255)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("approver_subject", "cost_center_id", name="uq_approver_section"),
    )
    op.create_index(
        "ix_approver_section_permissions_approver_subject",
        "approver_section_permissions",
        ["approver_subject"],
    )
    op.create_index(
        "ix_approver_section_permissions_cost_center_id",
        "approver_section_permissions",
        ["cost_center_id"],
    )
    op.create_index(
        "ix_approver_section_active",
        "approver_section_permissions",
        ["approver_subject", "active"],
    )
    # Preserve all currently usable assignments. Administrators can revoke these
    # explicit migrated grants later without rewriting approval history.
    bind = op.get_bind()
    existing_pairs = bind.execute(
        sa.text(
            "SELECT DISTINCT aa.approver_subject, a.cost_center_id "
            "FROM approval_assignments aa "
            "JOIN allocations a ON a.id = aa.allocation_id "
            "JOIN user_identities u ON u.subject = aa.approver_subject "
            "WHERE u.active = TRUE"
        )
    ).fetchall()
    now = datetime.now(UTC)
    permission_table = sa.table(
        "approver_section_permissions",
        sa.column("id", sa.String),
        sa.column("approver_subject", sa.String),
        sa.column("cost_center_id", sa.String),
        sa.column("active", sa.Boolean),
        sa.column("granted_by", sa.String),
        sa.column("granted_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    if existing_pairs:
        op.bulk_insert(
            permission_table,
            [
                {
                    "id": str(uuid4()),
                    "approver_subject": subject,
                    "cost_center_id": cost_center_id,
                    "active": True,
                    "granted_by": "migration:0011",
                    "granted_at": now,
                    "updated_at": now,
                }
                for subject, cost_center_id in existing_pairs
            ],
        )


def downgrade() -> None:
    op.drop_table("approver_section_permissions")
    for name in (
        "queue_manager_reviewed_by",
        "queue_manager_reviewed_at",
        "submitted_to_queue_by",
        "submitted_to_queue_at",
    ):
        op.drop_column("invoice_revisions", name)
    op.drop_column("document_uploads", "actor_role")
    op.drop_index("ix_invoices_upload_origin", table_name="invoices")
    op.drop_column("invoices", "upload_origin")
