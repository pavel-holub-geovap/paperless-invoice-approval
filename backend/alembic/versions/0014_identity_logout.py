"""Add optional identity display name and encrypted OIDC logout hint."""

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_identities", sa.Column("display_name", sa.String(255), nullable=True))
    op.add_column("oidc_sessions", sa.Column("id_token_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("oidc_sessions", "id_token_encrypted")
    op.drop_column("user_identities", "display_name")
