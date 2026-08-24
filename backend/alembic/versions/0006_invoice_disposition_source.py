"""invoice disposition and Paperless source availability

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column(
            "disposition",
            sa.Enum(
                "ACTIVE",
                "IGNORED_DUPLICATE",
                "IGNORED_OTHER",
                name="invoicedisposition",
                native_enum=False,
            ),
            nullable=False,
            server_default="ACTIVE",
        ),
    )
    op.add_column("invoices", sa.Column("disposition_reason", sa.String(length=100)))
    op.add_column("invoices", sa.Column("disposition_comment", sa.Text()))
    op.add_column("invoices", sa.Column("disposition_actor", sa.String(length=255)))
    op.add_column("invoices", sa.Column("disposition_changed_at", sa.DateTime(timezone=True)))
    op.add_column("invoices", sa.Column("duplicate_of_invoice_id", sa.String(length=36)))
    op.add_column(
        "invoices",
        sa.Column(
            "source_status",
            sa.Enum(
                "AVAILABLE", "MISSING", name="sourcedocumentstatus", native_enum=False
            ),
            nullable=False,
            server_default="AVAILABLE",
        ),
    )
    op.add_column("invoices", sa.Column("source_missing_at", sa.DateTime(timezone=True)))
    with op.batch_alter_table("invoices") as batch_op:
        batch_op.create_foreign_key(
            "fk_invoices_duplicate_of_invoice",
            "invoices",
            ["duplicate_of_invoice_id"],
            ["id"],
        )
    op.create_index("ix_invoices_disposition", "invoices", ["disposition"])
    op.create_index("ix_invoices_source_status", "invoices", ["source_status"])
    op.create_index(
        "ix_invoices_duplicate_of_invoice_id", "invoices", ["duplicate_of_invoice_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_invoices_duplicate_of_invoice_id", table_name="invoices")
    op.drop_index("ix_invoices_source_status", table_name="invoices")
    op.drop_index("ix_invoices_disposition", table_name="invoices")
    with op.batch_alter_table("invoices") as batch_op:
        batch_op.drop_constraint("fk_invoices_duplicate_of_invoice", type_="foreignkey")
    for column in (
        "source_missing_at",
        "source_status",
        "duplicate_of_invoice_id",
        "disposition_changed_at",
        "disposition_actor",
        "disposition_comment",
        "disposition_reason",
        "disposition",
    ):
        op.drop_column("invoices", column)
