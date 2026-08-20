from __future__ import annotations

import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.integrations.paperless import PaperlessClient
from app.models import (
    Allocation,
    ExportBatch,
    ExportBatchItem,
    ExportBatchStatus,
    Invoice,
    InvoiceStatus,
)
from app.services.audit import record_event
from app.services.pohoda import generate_invoice_xml, validate_xml
from app.services.workflow import WorkflowError, transition


def safe_stem(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return value[:80] or "invoice"


async def create_export_batch(
    db: Session,
    settings: Settings,
    paperless: PaperlessClient,
    invoices: list[Invoice],
    actor: str,
    xsd_path: Path,
) -> ExportBatch:
    if not invoices:
        raise WorkflowError("Export needs at least one invoice")
    settings.export_archive_dir.mkdir(parents=True, exist_ok=True)
    sequence = (db.scalar(select(func.count()).select_from(ExportBatch)) or 0) + 1
    batch_number = f"EXP-{datetime.now(UTC).year}-{sequence:06d}"
    archive_path = (settings.export_archive_dir / f"{batch_number}.zip").resolve()
    root = settings.export_archive_dir.resolve()
    if root not in archive_path.parents:
        raise WorkflowError("Unsafe export path")

    prepared: list[tuple[Invoice, bytes, bytes, str, str]] = []
    for invoice in invoices:
        if invoice.status == InvoiceStatus.APPROVED:
            revision = invoice.current_revision
            allocations = db.scalars(
                select(Allocation)
                .options(selectinload(Allocation.cost_center))
                .where(Allocation.revision_id == revision.id, Allocation.active.is_(True))
            ).all()
            xml = generate_invoice_xml(revision, list(allocations))
            errors = validate_xml(xml, xsd_path)
            if errors:
                record_event(db, "XML_VALIDATION_FAILED", actor=actor, invoice=invoice, metadata={"errors": errors[:20]})
                raise WorkflowError("POHODA XML XSD validation failed: " + "; ".join(errors[:3]))
            transition(db, invoice, InvoiceStatus.XML_READY, actor)
            record_event(db, "XML_GENERATED", actor=actor, invoice=invoice)
            transition(db, invoice, InvoiceStatus.READY_FOR_EXPORT, actor)
        if invoice.status != InvoiceStatus.READY_FOR_EXPORT:
            raise WorkflowError(f"Invoice {invoice.id} is not ready for export")
        revision = invoice.current_revision
        allocations = db.scalars(
            select(Allocation)
            .options(selectinload(Allocation.cost_center))
            .where(Allocation.revision_id == revision.id, Allocation.active.is_(True))
        ).all()
        xml = generate_invoice_xml(revision, list(allocations))
        errors = validate_xml(xml, xsd_path)
        if errors:
            raise WorkflowError("POHODA XML no longer validates")
        pdf = await paperless.download_pdf(invoice.paperless_document_id)
        invoice_number = safe_stem(str(revision.data.get("invoice_number") or invoice.id))
        prepared.append((invoice, pdf, xml, f"{invoice_number}.pdf", f"{invoice_number}.xml"))

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for _, pdf, xml, pdf_name, xml_name in prepared:
            archive.writestr(pdf_name, pdf)
            archive.writestr(xml_name, xml)

    batch = ExportBatch(
        batch_number=batch_number,
        archive_path=str(archive_path),
        created_by=actor,
    )
    db.add(batch)
    db.flush()
    for invoice, _, _, pdf_name, xml_name in prepared:
        db.add(
            ExportBatchItem(
                batch_id=batch.id,
                invoice_id=invoice.id,
                revision_id=invoice.current_revision.id,
                pdf_filename=pdf_name,
                xml_filename=xml_name,
            )
        )
        transition(db, invoice, InvoiceStatus.EXPORT_CREATED, actor)
        record_event(db, "EXPORT_CREATED", actor=actor, invoice=invoice, metadata={"batch_number": batch_number})
    return batch


def mark_batch_imported(db: Session, batch: ExportBatch, actor: str) -> None:
    if batch.status == ExportBatchStatus.IMPORTED:
        return
    now = datetime.now(UTC)
    for item in batch.items:
        invoice = db.get(Invoice, item.invoice_id)
        if invoice is None or invoice.status != InvoiceStatus.EXPORT_CREATED:
            raise WorkflowError("Every batch invoice must be in EXPORT_CREATED")
        transition(db, invoice, InvoiceStatus.IMPORTED_TO_POHODA, actor)
        item.imported_at = now
        record_event(db, "IMPORTED_TO_POHODA", actor=actor, invoice=invoice, metadata={"batch_number": batch.batch_number})
    batch.status = ExportBatchStatus.IMPORTED
    batch.imported_by = actor
    batch.imported_at = now

