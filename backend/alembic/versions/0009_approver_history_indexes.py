"""Add indexes used by paginated approver history queries.

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_approval_assignment_approver_invoice",
        "approval_assignments",
        ["approver_subject", "invoice_id"],
    )
    op.create_index(
        "ix_approval_decision_assignment_created",
        "approval_decisions",
        ["assignment_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_approval_decision_assignment_created", table_name="approval_decisions"
    )
    op.drop_index(
        "ix_approval_assignment_approver_invoice", table_name="approval_assignments"
    )
