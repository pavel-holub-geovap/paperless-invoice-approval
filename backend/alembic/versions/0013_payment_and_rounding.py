"""Add revision-scoped payment and rounding business fields.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("invoice_revisions", sa.Column("payment_required", sa.Boolean(), nullable=True))
    op.add_column(
        "invoice_revisions",
        sa.Column("rounding_amount", sa.Numeric(precision=18, scale=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("invoice_revisions", "rounding_amount")
    op.drop_column("invoice_revisions", "payment_required")
