"""Add audited allocation and approval assignment lifecycle metadata.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    timestamp_default = sa.text("CURRENT_TIMESTAMP")
    op.add_column(
        "cost_centers",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=timestamp_default),
    )
    op.add_column(
        "cost_centers",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=timestamp_default),
    )

    op.add_column("allocations", sa.Column("note", sa.Text()))
    op.add_column(
        "allocations",
        sa.Column("created_by", sa.String(length=255), nullable=False, server_default="system"),
    )
    op.add_column(
        "allocations",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=timestamp_default),
    )
    op.add_column(
        "allocations",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=timestamp_default),
    )
    with op.batch_alter_table("allocations") as batch_op:
        batch_op.drop_constraint("uq_revision_cost_center", type_="unique")
    op.create_index(
        "uq_active_revision_cost_center",
        "allocations",
        ["revision_id", "cost_center_id"],
        unique=True,
        postgresql_where=sa.text("active"),
        sqlite_where=sa.text("active = 1"),
    )

    op.add_column(
        "approval_assignments",
        sa.Column("status", sa.String(length=11), nullable=False, server_default="PENDING"),
    )
    op.add_column(
        "approval_assignments",
        sa.Column("assigned_by", sa.String(length=255), nullable=False, server_default="system"),
    )
    op.add_column(
        "approval_assignments",
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=timestamp_default),
    )
    op.add_column("approval_assignments", sa.Column("decided_at", sa.DateTime(timezone=True)))
    op.add_column("approval_assignments", sa.Column("comment", sa.Text()))
    op.add_column("approval_assignments", sa.Column("invalidated_at", sa.DateTime(timezone=True)))
    op.add_column("approval_assignments", sa.Column("invalidation_reason", sa.Text()))
    op.create_index("ix_approval_assignments_status", "approval_assignments", ["status"])
    op.create_index(
        "uq_approval_decision_valid_assignment",
        "approval_decisions",
        ["assignment_id"],
        unique=True,
        postgresql_where=sa.text("valid"),
        sqlite_where=sa.text("valid = 1"),
    )
    op.execute(
        sa.text(
            "UPDATE approval_assignments SET assigned_at = created_at, "
            "status = CASE "
            "WHEN EXISTS (SELECT 1 FROM approval_decisions d WHERE d.assignment_id = approval_assignments.id AND d.valid = true AND d.action = 'REJECT') THEN 'REJECTED' "
            "WHEN EXISTS (SELECT 1 FROM approval_decisions d WHERE d.assignment_id = approval_assignments.id AND d.valid = true AND d.action = 'RETURN') THEN 'RETURNED' "
            "WHEN EXISTS (SELECT 1 FROM approval_decisions d WHERE d.assignment_id = approval_assignments.id AND d.valid = true AND d.action = 'APPROVE') THEN 'APPROVED' "
            "ELSE 'PENDING' END"
        )
    )


def downgrade() -> None:
    op.drop_index("uq_approval_decision_valid_assignment", table_name="approval_decisions")
    op.drop_index("ix_approval_assignments_status", table_name="approval_assignments")
    for column in (
        "invalidation_reason",
        "invalidated_at",
        "comment",
        "decided_at",
        "assigned_at",
        "assigned_by",
        "status",
    ):
        op.drop_column("approval_assignments", column)
    op.drop_index("uq_active_revision_cost_center", table_name="allocations")
    with op.batch_alter_table("allocations") as batch_op:
        batch_op.create_unique_constraint(
            "uq_revision_cost_center", ["revision_id", "cost_center_id"]
        )
    for column in ("updated_at", "created_at", "created_by", "note"):
        op.drop_column("allocations", column)
    op.drop_column("cost_centers", "updated_at")
    op.drop_column("cost_centers", "created_at")
