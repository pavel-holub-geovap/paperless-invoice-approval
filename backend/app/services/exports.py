from __future__ import annotations

import hashlib
import re
import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.integrations.paperless import PaperlessClient
from app.models import (
    Allocation,
    ApprovalAssignment,
    ApprovalDecision,
    ExportArtifact,
    ExportArtifactStatus,
    ExportBatch,
    ExportBatchItem,
    ExportBatchStatus,
    Invoice,
    InvoiceRevision,
    InvoiceStatus,
    PohodaResponseUpload,
    ValidationResult,
    ValidationSeverity,
    new_id,
)
from app.services.audit import record_event
from app.services.pohoda import (
    PohodaInvoiceXmlGenerator,
    PohodaMappingError,
    build_source_snapshot,
    parse_pohoda_response,
    validate_xml_detailed,
)
from app.services.workflow import WorkflowError, all_required_approved, transition

CENT = Decimal("0.01")
REEXPORTABLE_STATES = {
    InvoiceStatus.READY_FOR_EXPORT,
    InvoiceStatus.EXPORT_CREATED,
    InvoiceStatus.IMPORTED_TO_POHODA,
}


def safe_stem(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return value[:80] or "invoice"


def _safe_path(root: Path, *parts: str) -> Path:
    root = root.resolve()
    candidate = root.joinpath(*parts).resolve()
    if root not in candidate.parents:
        raise WorkflowError("Unsafe export path")
    return candidate


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _active_allocations(db: Session, invoice: Invoice) -> list[Allocation]:
    revision = invoice.current_revision
    if revision is None:
        raise WorkflowError("Invoice has no current revision")
    return list(
        db.scalars(
            select(Allocation)
            .options(selectinload(Allocation.cost_center))
            .where(Allocation.revision_id == revision.id, Allocation.active.is_(True))
            .order_by(Allocation.created_at, Allocation.id)
        ).all()
    )


def _check_export_eligibility(db: Session, invoice: Invoice, allocations: list[Allocation]) -> None:
    revision = invoice.current_revision
    if revision is None:
        raise WorkflowError("Invoice has no current revision")
    if invoice.status != InvoiceStatus.APPROVED:
        raise WorkflowError("Only an APPROVED current revision can generate its first XML")
    if not invoice.original_review_confirmed:
        raise WorkflowError("Original document review is not confirmed")
    if db.scalar(
        select(ValidationResult.id).where(
            ValidationResult.revision_id == revision.id,
            ValidationResult.severity == ValidationSeverity.BLOCKING_ERROR,
        )
    ):
        raise WorkflowError("Current revision contains a blocking validation error")
    if not allocations:
        raise WorkflowError("At least one allocation is required")
    total = Decimal(str(revision.data.get("total_amount") or "0")).quantize(CENT)
    allocated = sum((Decimal(row.amount) for row in allocations), Decimal("0")).quantize(CENT)
    if abs(total - allocated) > CENT:
        raise WorkflowError("Allocation total does not equal invoice total")
    if not all_required_approved(db, invoice):
        raise WorkflowError("Every required assignment of the current revision must be approved")


def _add_duplicate_warning(db: Session, invoice: Invoice) -> list[str]:
    revision = invoice.current_revision
    if revision is None:
        return []
    keys = ("supplier_ico", "invoice_number", "total_amount", "issue_date")
    target = tuple(str(revision.data.get(key) or "").strip().casefold() for key in keys)
    if not all(target):
        return []
    matches: list[str] = []
    revisions = db.scalars(
        select(InvoiceRevision)
        .join(Invoice, Invoice.id == InvoiceRevision.invoice_id)
        .where(
            InvoiceRevision.invoice_id != invoice.id,
            InvoiceRevision.number == Invoice.current_revision_number,
        )
    ).all()
    for other in revisions:
        candidate = tuple(str(other.data.get(key) or "").strip().casefold() for key in keys)
        if candidate == target:
            matches.append(other.invoice_id)
    if matches and not db.scalar(
        select(ValidationResult.id).where(
            ValidationResult.revision_id == revision.id,
            ValidationResult.code == "POSSIBLE_DUPLICATE_INVOICE",
        )
    ):
        db.add(
            ValidationResult(
                revision_id=revision.id,
                code="POSSIBLE_DUPLICATE_INVOICE",
                severity=ValidationSeverity.WARNING,
                field_name="invoice_number",
                message="A possible duplicate invoice exists",
                actual=matches,
                details={"matching_invoice_ids": matches, "comparison_fields": list(keys)},
            )
        )
    return matches


def _approval_snapshot(db: Session, invoice: Invoice) -> list[dict[str, str]]:
    revision = invoice.current_revision
    if revision is None:
        return []
    rows = db.execute(
        select(ApprovalAssignment, ApprovalDecision)
        .join(
            ApprovalDecision,
            (ApprovalDecision.assignment_id == ApprovalAssignment.id)
            & ApprovalDecision.valid.is_(True),
        )
        .where(
            ApprovalAssignment.invoice_id == invoice.id,
            ApprovalAssignment.revision_id == revision.id,
            ApprovalAssignment.active.is_(True),
        )
    ).all()
    return [
        {
            "assignment_id": assignment.id,
            "allocation_id": assignment.allocation_id,
            "approver_subject": assignment.approver_subject,
            "decision_id": decision.id,
            "action": decision.action.value,
            "decided_at": decision.created_at.isoformat(),
        }
        for assignment, decision in rows
    ]


def latest_valid_artifact(db: Session, invoice: Invoice) -> ExportArtifact | None:
    revision = invoice.current_revision
    if revision is None:
        return None
    return db.scalar(
        select(ExportArtifact)
        .where(
            ExportArtifact.invoice_id == invoice.id,
            ExportArtifact.revision_id == revision.id,
            ExportArtifact.status == ExportArtifactStatus.XSD_VALID,
        )
        .order_by(ExportArtifact.generated_at.desc())
    )


async def generate_export_artifact(
    db: Session,
    settings: Settings,
    paperless: PaperlessClient,
    invoice: Invoice,
    actor: str,
    *,
    reexport_reason: str | None = None,
) -> ExportArtifact:
    previous = latest_valid_artifact(db, invoice)
    is_reexport = previous is not None
    if is_reexport:
        if invoice.status not in REEXPORTABLE_STATES:
            raise WorkflowError("Re-export requires an unchanged already exported current revision")
        if previous.revision_id != invoice.current_revision.id:
            raise WorkflowError("A changed invoice revision must be approved again")
    else:
        allocations = _active_allocations(db, invoice)
        _check_export_eligibility(db, invoice, allocations)
    allocations = _active_allocations(db, invoice)

    record_event(
        db,
        "XML_GENERATION_STARTED",
        actor=actor,
        invoice=invoice,
        metadata={"reexport": is_reexport},
    )
    duplicate_ids = _add_duplicate_warning(db, invoice)
    snapshot = build_source_snapshot(invoice.current_revision, allocations)
    snapshot.update(
        {
            "workflow_status": invoice.status.value,
            "original_review_confirmed": invoice.original_review_confirmed,
            "original_reviewed_by": invoice.original_reviewed_by,
            "original_reviewed_at": invoice.original_reviewed_at.isoformat()
            if invoice.original_reviewed_at
            else None,
            "approvals": _approval_snapshot(db, invoice),
            "possible_duplicate_invoice_ids": duplicate_ids,
        }
    )

    try:
        xml = PohodaInvoiceXmlGenerator(encoding=settings.pohoda_xml_encoding).generate(snapshot)
    except PohodaMappingError as exc:
        record_event(
            db,
            "XML_VALIDATION_FAILED",
            actor=actor,
            invoice=invoice,
            metadata={"mapping_error": str(exc)},
        )
        raise WorkflowError(str(exc)) from exc
    errors = validate_xml_detailed(xml, settings.pohoda_xsd_path)
    pdf = await paperless.download_pdf(invoice.paperless_document_id)
    artifact_id = new_id()
    settings.export_archive_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir = _safe_path(settings.export_archive_dir, "artifacts")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    xml_path = _safe_path(artifact_dir, f"{artifact_id}.xml")
    xml_path.write_bytes(xml)
    artifact = ExportArtifact(
        id=artifact_id,
        invoice_id=invoice.id,
        revision_id=invoice.current_revision.id,
        source_export_id=previous.id if previous else None,
        status=ExportArtifactStatus.XSD_INVALID if errors else ExportArtifactStatus.XSD_VALID,
        generator_version=settings.pohoda_generator_version,
        xsd_bundle_version=settings.pohoda_xsd_bundle_version,
        encoding=settings.pohoda_xml_encoding,
        source_snapshot=snapshot,
        xml_path=str(xml_path),
        xml_sha256=_sha256(xml),
        xml_size=len(xml),
        pdf_sha256=_sha256(pdf),
        validation_errors=errors,
        reexport_reason=reexport_reason,
        generated_by=actor,
    )
    db.add(artifact)
    db.flush()
    if errors:
        record_event(
            db,
            "XML_VALIDATION_FAILED",
            actor=actor,
            invoice=invoice,
            metadata={"export_id": artifact.id, "errors": errors[:20]},
        )
        return artifact

    record_event(
        db,
        "XML_GENERATED",
        actor=actor,
        invoice=invoice,
        metadata={"export_id": artifact.id, "sha256": artifact.xml_sha256},
    )
    record_event(
        db,
        "XML_VALIDATION_PASSED",
        actor=actor,
        invoice=invoice,
        metadata={
            "export_id": artifact.id,
            "xsd_bundle_version": artifact.xsd_bundle_version,
        },
    )
    if is_reexport:
        record_event(
            db,
            "REEXPORTED",
            actor=actor,
            invoice=invoice,
            comment=reexport_reason,
            metadata={"source_export_id": previous.id, "new_export_id": artifact.id},
        )
    else:
        transition(db, invoice, InvoiceStatus.XML_READY, actor)
        transition(db, invoice, InvoiceStatus.READY_FOR_EXPORT, actor)
    return artifact


async def create_export_batch(
    db: Session,
    settings: Settings,
    paperless: PaperlessClient,
    invoices: list[Invoice],
    actor: str,
    xsd_path: Path | None = None,
) -> ExportBatch:
    if not invoices:
        raise WorkflowError("Export needs at least one invoice")
    settings.export_archive_dir.mkdir(parents=True, exist_ok=True)
    sequence = (db.scalar(select(func.count()).select_from(ExportBatch)) or 0) + 1
    batch_number = f"EXP-{datetime.now(UTC).year}-{sequence:06d}"
    archive_path = _safe_path(settings.export_archive_dir, f"{batch_number}.zip")

    prepared: list[tuple[Invoice, ExportArtifact, bytes, bytes, str]] = []
    for invoice in invoices:
        artifact = latest_valid_artifact(db, invoice)
        if artifact is None:
            artifact = await generate_export_artifact(db, settings, paperless, invoice, actor)
        if artifact.status != ExportArtifactStatus.XSD_VALID:
            raise WorkflowError(f"Invoice {invoice.id} has no XSD-valid current XML")
        if artifact.revision_id != invoice.current_revision.id:
            raise WorkflowError("Export artifact does not belong to the current revision")
        xml_path = Path(artifact.xml_path)
        xml = xml_path.read_bytes()
        if _sha256(xml) != artifact.xml_sha256:
            raise WorkflowError("Immutable XML artifact hash mismatch")
        pdf = await paperless.download_pdf(invoice.paperless_document_id)
        if _sha256(pdf) != artifact.pdf_sha256:
            raise WorkflowError("Paperless PDF changed since XML snapshot generation")
        stem = safe_stem(str(invoice.current_revision.data.get("invoice_number") or invoice.id))
        prepared.append((invoice, artifact, pdf, xml, stem))

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for _, _, pdf, xml, stem in prepared:
            folder = f"invoice-{stem}"
            archive.writestr(f"{folder}/invoice.pdf", pdf)
            archive.writestr(f"{folder}/invoice.xml", xml)

    archive_hash = _sha256(archive_path.read_bytes())
    batch = ExportBatch(
        batch_number=batch_number,
        archive_path=str(archive_path),
        archive_sha256=archive_hash,
        created_by=actor,
    )
    db.add(batch)
    db.flush()
    for invoice, artifact, _, _, stem in prepared:
        db.add(
            ExportBatchItem(
                batch_id=batch.id,
                invoice_id=invoice.id,
                revision_id=invoice.current_revision.id,
                export_artifact_id=artifact.id,
                pdf_filename=f"invoice-{stem}/invoice.pdf",
                xml_filename=f"invoice-{stem}/invoice.xml",
            )
        )
        if invoice.status == InvoiceStatus.READY_FOR_EXPORT:
            transition(db, invoice, InvoiceStatus.EXPORT_CREATED, actor)
        record_event(
            db,
            "EXPORT_CREATED",
            actor=actor,
            invoice=invoice,
            metadata={"batch_id": batch.id, "batch_number": batch_number, "export_id": artifact.id},
        )
        record_event(
            db,
            "BATCH_EXPORT_CREATED",
            actor=actor,
            invoice=invoice,
            metadata={"batch_id": batch.id, "batch_number": batch_number},
        )
    return batch


def mark_batch_imported(db: Session, batch: ExportBatch, actor: str) -> None:
    if batch.status == ExportBatchStatus.IMPORTED:
        return
    now = datetime.now(UTC)
    for item in batch.items:
        invoice = db.get(Invoice, item.invoice_id)
        artifact = db.get(ExportArtifact, item.export_artifact_id)
        if invoice is None or artifact is None or invoice.status != InvoiceStatus.EXPORT_CREATED:
            raise WorkflowError("Every batch invoice must be in EXPORT_CREATED with an artifact")
        if artifact.revision_id != invoice.current_revision.id:
            raise WorkflowError("Cannot confirm import of an obsolete invoice revision")
        transition(db, invoice, InvoiceStatus.IMPORTED_TO_POHODA, actor)
        invoice.imported_to_pohoda_at = now
        invoice.imported_to_pohoda_by = actor
        invoice.imported_export_id = artifact.id
        artifact.imported_at = now
        artifact.imported_by = actor
        item.imported_at = now
        record_event(
            db,
            "IMPORTED_TO_POHODA",
            actor=actor,
            invoice=invoice,
            metadata={"batch_id": batch.id, "export_id": artifact.id},
        )
    batch.status = ExportBatchStatus.IMPORTED
    batch.imported_by = actor
    batch.imported_at = now


def mark_artifact_imported(
    db: Session, artifact: ExportArtifact, invoice: Invoice, actor: str
) -> None:
    if artifact.imported_at is not None and invoice.imported_export_id == artifact.id:
        return
    if invoice.status != InvoiceStatus.EXPORT_CREATED:
        raise WorkflowError("Invoice must be in EXPORT_CREATED")
    if artifact.status != ExportArtifactStatus.XSD_VALID:
        raise WorkflowError("Only an XSD-valid artifact can be confirmed as imported")
    if artifact.invoice_id != invoice.id or artifact.revision_id != invoice.current_revision.id:
        raise WorkflowError("Cannot confirm import of an obsolete or foreign export artifact")
    now = datetime.now(UTC)
    transition(db, invoice, InvoiceStatus.IMPORTED_TO_POHODA, actor)
    invoice.imported_to_pohoda_at = now
    invoice.imported_to_pohoda_by = actor
    invoice.imported_export_id = artifact.id
    artifact.imported_at = now
    artifact.imported_by = actor
    items = db.scalars(
        select(ExportBatchItem).where(ExportBatchItem.export_artifact_id == artifact.id)
    ).all()
    for item in items:
        item.imported_at = now
    record_event(
        db,
        "IMPORTED_TO_POHODA",
        actor=actor,
        invoice=invoice,
        metadata={"export_id": artifact.id, "confirmation": "individual"},
    )


def store_pohoda_response(
    db: Session,
    settings: Settings,
    content: bytes,
    filename: str,
    actor: str,
    *,
    export_artifact_id: str | None = None,
    batch_id: str | None = None,
) -> PohodaResponseUpload:
    if len(content) > 5 * 1024 * 1024:
        raise WorkflowError("POHODA response XML exceeds the 5 MiB limit")
    response_xsd = settings.pohoda_xsd_path.with_name("response.xsd")
    try:
        parsed = parse_pohoda_response(content, response_xsd_path=response_xsd)
        parse_status = "PARSED"
        parse_errors = parsed.get("schema_errors", [])
    except PohodaMappingError as exc:
        parsed = {}
        parse_status = "INVALID"
        parse_errors = [{"message": str(exc), "line": None, "column": None, "path": None}]

    upload_id = new_id()
    response_dir = _safe_path(settings.export_archive_dir, "responses")
    response_dir.mkdir(parents=True, exist_ok=True)
    path = _safe_path(response_dir, f"{upload_id}.xml")
    path.write_bytes(content)
    upload = PohodaResponseUpload(
        id=upload_id,
        export_artifact_id=export_artifact_id,
        batch_id=batch_id,
        filename=safe_stem(Path(filename).stem) + ".xml",
        artifact_path=str(path),
        sha256=_sha256(content),
        parse_status=parse_status,
        parsed_result=parsed,
        parse_errors=parse_errors,
        uploaded_by=actor,
    )
    db.add(upload)

    invoice = None
    if export_artifact_id:
        artifact = db.get(ExportArtifact, export_artifact_id)
        invoice = db.get(Invoice, artifact.invoice_id) if artifact else None
    record_event(
        db,
        "POHODA_RESPONSE_UPLOADED",
        actor=actor,
        invoice=invoice,
        metadata={"upload_id": upload.id, "sha256": upload.sha256},
    )
    record_event(
        db,
        "POHODA_RESPONSE_PARSED",
        actor=actor,
        invoice=invoice,
        metadata={"upload_id": upload.id, "parse_status": parse_status},
    )
    return upload
