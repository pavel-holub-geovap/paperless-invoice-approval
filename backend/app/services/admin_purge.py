from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.paperless import PaperlessClient, PaperlessError, PaperlessNotFound
from app.models import (
    AdminPurgeAudit,
    AIExtraction,
    Allocation,
    ApprovalAssignment,
    ApprovalDecision,
    ApprovedPdfArtifact,
    AuditEvent,
    DocumentUpload,
    ExportArtifact,
    ExportBatch,
    ExportBatchItem,
    ExtractedField,
    Invoice,
    InvoiceDisposition,
    InvoiceRevision,
    IsdocExtraction,
    PohodaResponseUpload,
    ProcessingJob,
    ValidationResult,
)
from app.request_context import get_correlation_id


class AdminPurgeError(RuntimeError):
    pass


class AdminPurgeExternalError(AdminPurgeError):
    pass


@dataclass(frozen=True)
class AdminPurgeResult:
    invoice_id: str
    status: str
    audit_id: str | None
    paperless_document_ids: list[int]
    artifact_counts: dict[str, int]


def _safe_artifact_paths(settings: Settings, raw_paths: list[str]) -> list[Path]:
    root = settings.export_archive_dir.resolve()
    paths: list[Path] = []
    for raw in dict.fromkeys(raw_paths):
        path = Path(raw).resolve()
        if path != root and root not in path.parents:
            raise AdminPurgeError("Invoice artifact path is outside the configured archive")
        if path.exists() and not path.is_file():
            raise AdminPurgeError("Invoice artifact path does not point to a regular file")
        paths.append(path)
    return paths


def _already_purged(db: Session, invoice_id: str) -> AdminPurgeAudit | None:
    return db.scalar(
        select(AdminPurgeAudit)
        .where(AdminPurgeAudit.original_invoice_id == invoice_id)
        .order_by(AdminPurgeAudit.created_at.desc())
    )


async def purge_invoice(
    db: Session,
    settings: Settings,
    paperless: PaperlessClient,
    *,
    invoice_id: str,
    actor_subject: str,
    actor_display_name: str | None,
    reason: str,
) -> AdminPurgeResult:
    """Delete one invoice aggregate only after every Paperless delete is safe to continue."""
    invoice = db.scalar(
        select(Invoice).where(Invoice.id == invoice_id).with_for_update()
    )
    if invoice is None:
        prior = _already_purged(db, invoice_id)
        return AdminPurgeResult(
            invoice_id=invoice_id,
            status="ALREADY_PURGED" if prior else "NOT_FOUND",
            audit_id=prior.id if prior else None,
            paperless_document_ids=list(prior.paperless_document_ids) if prior else [],
            artifact_counts=dict(prior.artifact_counts) if prior else {},
        )

    revisions = list(
        db.scalars(select(InvoiceRevision).where(InvoiceRevision.invoice_id == invoice.id)).all()
    )
    revision_ids = [row.id for row in revisions]
    assignments = list(
        db.scalars(
            select(ApprovalAssignment).where(ApprovalAssignment.invoice_id == invoice.id)
        ).all()
    )
    assignment_ids = [row.id for row in assignments]
    approved = list(
        db.scalars(
            select(ApprovedPdfArtifact).where(ApprovedPdfArtifact.invoice_id == invoice.id)
        ).all()
    )
    exports = list(
        db.scalars(select(ExportArtifact).where(ExportArtifact.invoice_id == invoice.id)).all()
    )
    export_ids = [row.id for row in exports]
    batch_ids = list(
        db.scalars(
            select(ExportBatchItem.batch_id)
            .where(ExportBatchItem.invoice_id == invoice.id)
            .distinct()
        ).all()
    )
    batches = (
        list(db.scalars(select(ExportBatch).where(ExportBatch.id.in_(batch_ids))).all())
        if batch_ids
        else []
    )
    response_filter = []
    if export_ids:
        response_filter.append(PohodaResponseUpload.export_artifact_id.in_(export_ids))
    if batch_ids:
        response_filter.append(PohodaResponseUpload.batch_id.in_(batch_ids))
    responses = (
        list(db.scalars(select(PohodaResponseUpload).where(or_(*response_filter))).all())
        if response_filter
        else []
    )
    paths = _safe_artifact_paths(
        settings,
        [row.xml_path for row in exports]
        + [row.archive_path for row in batches]
        + [row.artifact_path for row in responses],
    )

    approved_paperless_ids = sorted(
        {
            row.paperless_document_id
            for row in approved
            if row.paperless_document_id
            and row.paperless_document_id != invoice.paperless_document_id
        }
    )
    # Remove derived copies first and the authoritative original last.  A partial
    # external failure therefore preserves the original whenever possible.
    paperless_ids = [*approved_paperless_ids, invoice.paperless_document_id]
    already_absent = 0
    for document_id in paperless_ids:
        try:
            await paperless.delete_document(document_id)
        except PaperlessNotFound:
            already_absent += 1
        except PaperlessError as exc:
            raise AdminPurgeExternalError(
                f"Paperless deletion failed for document {document_id}; local invoice was retained"
            ) from exc

    try:
        for path in paths:
            if path.exists():
                path.unlink()
    except OSError as exc:
        raise AdminPurgeError(
            "Artifact deletion failed; local invoice was retained and the operation can be retried"
        ) from exc

    artifact_counts = {
        "paperless_documents": len(paperless_ids),
        "paperless_already_absent": already_absent,
        "approved_pdf_artifacts": len(approved),
        "export_artifacts": len(exports),
        "export_batches": len(batches),
        "pohoda_responses": len(responses),
        "revisions": len(revisions),
        "assignments": len(assignments),
    }

    if response_filter:
        db.execute(delete(PohodaResponseUpload).where(or_(*response_filter)))
    if batch_ids:
        db.execute(delete(ExportBatchItem).where(ExportBatchItem.batch_id.in_(batch_ids)))
        db.execute(delete(ExportBatch).where(ExportBatch.id.in_(batch_ids)))
    db.execute(delete(ExportBatchItem).where(ExportBatchItem.invoice_id == invoice.id))
    if export_ids:
        db.execute(
            update(ExportArtifact)
            .where(ExportArtifact.source_export_id.in_(export_ids))
            .values(source_export_id=None)
        )
        db.execute(delete(ExportArtifact).where(ExportArtifact.id.in_(export_ids)))
    db.execute(delete(ApprovedPdfArtifact).where(ApprovedPdfArtifact.invoice_id == invoice.id))
    if assignment_ids:
        db.execute(
            delete(ApprovalDecision).where(ApprovalDecision.assignment_id.in_(assignment_ids))
        )
    db.execute(delete(ApprovalAssignment).where(ApprovalAssignment.invoice_id == invoice.id))
    db.execute(delete(Allocation).where(Allocation.invoice_id == invoice.id))
    if revision_ids:
        db.execute(delete(ValidationResult).where(ValidationResult.revision_id.in_(revision_ids)))
        db.execute(delete(ExtractedField).where(ExtractedField.revision_id.in_(revision_ids)))
    db.execute(delete(IsdocExtraction).where(IsdocExtraction.invoice_id == invoice.id))
    db.execute(delete(AIExtraction).where(AIExtraction.invoice_id == invoice.id))
    # Normal audit is immutable in every ordinary workflow. ADMIN PURGE is the sole
    # explicit business exception and preserves only the separate minimal audit below.
    db.execute(delete(AuditEvent).where(AuditEvent.invoice_id == invoice.id))
    db.execute(delete(ProcessingJob).where(ProcessingJob.invoice_id == invoice.id))
    db.execute(delete(DocumentUpload).where(DocumentUpload.invoice_id == invoice.id))
    db.execute(delete(InvoiceRevision).where(InvoiceRevision.invoice_id == invoice.id))
    db.execute(
        update(Invoice)
        .where(Invoice.duplicate_of_invoice_id == invoice.id)
        .values(
            duplicate_of_invoice_id=None,
            disposition=InvoiceDisposition.IGNORED_OTHER,
            disposition_reason="PURGED_DUPLICATE_REFERENCE",
        )
    )
    db.execute(delete(Invoice).where(Invoice.id == invoice.id))

    audit = AdminPurgeAudit(
        original_invoice_id=invoice.id,
        original_paperless_document_id=invoice.paperless_document_id,
        paperless_document_ids=paperless_ids,
        actor_subject=actor_subject,
        actor_display_name=actor_display_name,
        reason=reason.strip(),
        artifact_counts=artifact_counts,
        result="PURGED",
        correlation_id=get_correlation_id(),
    )
    db.add(audit)
    db.flush()
    return AdminPurgeResult(
        invoice_id=invoice.id,
        status="PURGED",
        audit_id=audit.id,
        paperless_document_ids=paperless_ids,
        artifact_counts=artifact_counts,
    )
