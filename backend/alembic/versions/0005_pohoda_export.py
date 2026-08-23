"""immutable POHODA export artifacts

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("allocations", sa.Column("vat_breakdown", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("invoices", sa.Column("imported_to_pohoda_at", sa.DateTime(timezone=True)))
    op.add_column("invoices", sa.Column("imported_to_pohoda_by", sa.String(length=255)))
    op.add_column("invoices", sa.Column("imported_export_id", sa.String(length=36)))
    op.create_index("ix_invoices_imported_export_id", "invoices", ["imported_export_id"])

    op.create_table(
        "export_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("source_export_id", sa.String(length=36)),
        sa.Column(
            "status",
            sa.Enum("XSD_VALID", "XSD_INVALID", name="exportartifactstatus", native_enum=False),
            nullable=False,
        ),
        sa.Column("generator_version", sa.String(length=64), nullable=False),
        sa.Column("xsd_bundle_version", sa.String(length=32), nullable=False),
        sa.Column("encoding", sa.String(length=32), nullable=False),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("xml_path", sa.Text(), nullable=False),
        sa.Column("xml_sha256", sa.String(length=64), nullable=False),
        sa.Column("xml_size", sa.Integer(), nullable=False),
        sa.Column("pdf_sha256", sa.String(length=64)),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column("reexport_reason", sa.Text()),
        sa.Column("generated_by", sa.String(length=255), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_by", sa.String(length=255)),
        sa.Column("imported_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["invoice_revisions.id"]),
        sa.ForeignKeyConstraint(["source_export_id"], ["export_artifacts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_export_artifacts_invoice_id", ["invoice_id"]),
        ("ix_export_artifacts_revision_id", ["revision_id"]),
        ("ix_export_artifacts_source_export_id", ["source_export_id"]),
        ("ix_export_artifacts_status", ["status"]),
    ):
        op.create_index(name, "export_artifacts", columns)

    op.add_column("export_batches", sa.Column("archive_sha256", sa.String(length=64)))
    with op.batch_alter_table("export_batch_items") as batch_op:
        batch_op.add_column(sa.Column("export_artifact_id", sa.String(length=36)))
        batch_op.create_foreign_key(
            "fk_export_batch_items_artifact",
            "export_artifacts",
            ["export_artifact_id"],
            ["id"],
        )
    op.create_index(
        "ix_export_batch_items_export_artifact_id",
        "export_batch_items",
        ["export_artifact_id"],
    )

    op.create_table(
        "pohoda_response_uploads",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("export_artifact_id", sa.String(length=36)),
        sa.Column("batch_id", sa.String(length=36)),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("parse_status", sa.String(length=32), nullable=False),
        sa.Column("parsed_result", sa.JSON(), nullable=False),
        sa.Column("parse_errors", sa.JSON(), nullable=False),
        sa.Column("uploaded_by", sa.String(length=255), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["export_artifact_id"], ["export_artifacts.id"]),
        sa.ForeignKeyConstraint(["batch_id"], ["export_batches.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pohoda_response_uploads_export_artifact_id", "pohoda_response_uploads", ["export_artifact_id"])
    op.create_index("ix_pohoda_response_uploads_batch_id", "pohoda_response_uploads", ["batch_id"])


def downgrade() -> None:
    op.drop_table("pohoda_response_uploads")
    op.drop_index("ix_export_batch_items_export_artifact_id", table_name="export_batch_items")
    with op.batch_alter_table("export_batch_items") as batch_op:
        batch_op.drop_constraint("fk_export_batch_items_artifact", type_="foreignkey")
        batch_op.drop_column("export_artifact_id")
    op.drop_column("export_batches", "archive_sha256")
    op.drop_table("export_artifacts")
    op.drop_index("ix_invoices_imported_export_id", table_name="invoices")
    op.drop_column("invoices", "imported_export_id")
    op.drop_column("invoices", "imported_to_pohoda_by")
    op.drop_column("invoices", "imported_to_pohoda_at")
    op.drop_column("allocations", "vat_breakdown")
