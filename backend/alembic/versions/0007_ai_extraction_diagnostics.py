"""Preserve AI raw attempts, schema diagnostics, and normalization.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_extractions",
        sa.Column("raw_attempts_json", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "ai_extractions",
        sa.Column(
            "schema_validation_errors_json",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "ai_extractions",
        sa.Column(
            "normalization_result_json",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "ai_extractions",
        sa.Column(
            "corrective_retry_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_extractions", "corrective_retry_count")
    op.drop_column("ai_extractions", "normalization_result_json")
    op.drop_column("ai_extractions", "schema_validation_errors_json")
    op.drop_column("ai_extractions", "raw_attempts_json")
