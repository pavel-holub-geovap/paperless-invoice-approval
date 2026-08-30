from __future__ import annotations

import hashlib
import json
import textwrap
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import reportlab
from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.models import (
    Allocation,
    ApprovalAssignment,
    ApprovalAssignmentStatus,
    ApprovedPdfArtifact,
    ApprovedPdfStatus,
    Invoice,
    InvoiceStatus,
    PohodaImportMethod,
    UserIdentity,
    new_id,
)
from app.services.audit import record_event
from app.services.isdoc import attachment_manifest, enumerate_attachments
from app.services.workflow import WorkflowError, all_required_approved, transition

STAMP_MARGIN = 12
PRAGUE = ZoneInfo("Europe/Prague")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_hash(value: Any) -> str:
    return _sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def _format_money(value: Decimal, currency: str) -> str:
    rendered = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
    return f"{rendered} {currency}"


def _format_time(value: datetime) -> str:
    return value.astimezone(PRAGUE).strftime("%d.%m.%Y %H:%M")


def build_approval_snapshot(db: Session, invoice: Invoice) -> dict[str, Any]:
    revision = invoice.current_revision
    if revision is None:
        raise WorkflowError("Invoice has no current revision")
    allocations = db.scalars(
        select(Allocation)
        .options(
            selectinload(Allocation.cost_center),
            selectinload(Allocation.assignments).selectinload(ApprovalAssignment.decisions),
        )
        .where(Allocation.revision_id == revision.id, Allocation.active.is_(True))
        .order_by(Allocation.created_at, Allocation.id)
    ).all()
    subjects = {
        assignment.approver_subject
        for allocation in allocations
        for assignment in allocation.assignments
        if assignment.active and assignment.required
    }
    users = {
        row.subject: row
        for row in db.scalars(select(UserIdentity).where(UserIdentity.subject.in_(subjects))).all()
    }
    rows: list[dict[str, Any]] = []
    completion: list[datetime] = []
    for allocation in allocations:
        approvers: list[dict[str, Any]] = []
        for assignment in allocation.assignments:
            if not assignment.active or not assignment.required:
                continue
            if assignment.status != ApprovalAssignmentStatus.APPROVED:
                raise WorkflowError("Every required assignment must be approved")
            decision = next(
                (row for row in assignment.decisions if row.valid and row.action.value == "APPROVE"),
                None,
            )
            if decision is None:
                raise WorkflowError("Approved assignment has no valid APPROVE decision")
            completion.append(decision.created_at)
            identity = users.get(assignment.approver_subject)
            approvers.append(
                {
                    "subject": assignment.approver_subject,
                    "name": identity.username if identity else assignment.approver_subject,
                    "decided_at": decision.created_at.isoformat(),
                }
            )
        rows.append(
            {
                "allocation_id": allocation.id,
                "cost_center": {
                    "code": allocation.cost_center.code,
                    "name": allocation.cost_center.name,
                },
                "amount": str(Decimal(allocation.amount).quantize(Decimal("0.01"))),
                "approvers": approvers,
            }
        )
    currency = str(revision.data.get("currency") or "CZK").upper()
    return {
        "invoice_id": invoice.id,
        "invoice_revision_id": revision.id,
        "invoice_revision": revision.number,
        "invoice_number": revision.data.get("invoice_number"),
        "currency": currency,
        "total_approved": str(
            sum((Decimal(row["amount"]) for row in rows), Decimal("0")).quantize(
                Decimal("0.01")
            )
        ),
        "approval_completed_at": max(completion).isoformat() if completion else None,
        "allocations": rows,
    }


def prepare_approved_pdf_artifact(
    db: Session, invoice: Invoice, settings: Settings, actor: str = "system"
) -> ApprovedPdfArtifact:
    if invoice.status != InvoiceStatus.APPROVED:
        raise WorkflowError("Approved PDF can be created only for final APPROVED revision")
    if not all_required_approved(db, invoice):
        raise WorkflowError("Current revision is not fully approved")
    revision = invoice.current_revision
    if revision is None:
        raise WorkflowError("Invoice has no current revision")
    snapshot = build_approval_snapshot(db, invoice)
    snapshot_hash = _canonical_hash(snapshot)
    existing = db.scalar(
        select(ApprovedPdfArtifact).where(
            ApprovedPdfArtifact.revision_id == revision.id,
            ApprovedPdfArtifact.approval_snapshot_sha256 == snapshot_hash,
            ApprovedPdfArtifact.stamp_version == settings.approved_pdf_stamp_version,
        )
    )
    if existing:
        return existing
    artifact = ApprovedPdfArtifact(
        id=new_id(),
        invoice_id=invoice.id,
        revision_id=revision.id,
        status=ApprovedPdfStatus.PENDING,
        stamp_version=settings.approved_pdf_stamp_version,
        approval_snapshot=snapshot,
        approval_snapshot_sha256=snapshot_hash,
        original_pdf_sha256=invoice.source_pdf_sha256 or "",
        created_by=actor,
    )
    db.add(artifact)
    db.flush()
    return artifact


def _stamp_lines(snapshot: dict[str, Any]) -> list[str]:
    currency = snapshot["currency"]
    lines = ["SCHVÁLENO - interní schvalovací informace"]
    for allocation in snapshot["allocations"]:
        centre = allocation["cost_center"]
        approvers = ", ".join(
            f"{row['name']} ({_format_time(datetime.fromisoformat(row['decided_at']))})"
            for row in allocation["approvers"]
        )
        lines.append(
            f"{centre['code']} - {centre['name']}: "
            f"{_format_money(Decimal(allocation['amount']), currency)} | {approvers}"
        )
    completed = snapshot.get("approval_completed_at")
    completed_text = _format_time(datetime.fromisoformat(completed)) if completed else "-"
    lines.extend(
        [
            f"Celkem schváleno: {_format_money(Decimal(snapshot['total_approved']), currency)}",
            f"Dokončeno: {completed_text}",
            f"Workflow ID: {snapshot['invoice_id']} | revize {snapshot['invoice_revision']}",
        ]
    )
    return lines


def _stamp_overlay(width: float, full_height: float, band_height: float, lines: list[str]) -> bytes:
    output = BytesIO()
    font_path = Path(reportlab.__file__).resolve().parent / "fonts" / "Vera.ttf"
    if "ApprovalSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("ApprovalSans", str(font_path)))
    pdf = canvas.Canvas(output, pagesize=(width, full_height), invariant=1, pageCompression=1)
    pdf.setFillColorRGB(1, 1, 1)
    pdf.rect(0, 0, width, band_height, fill=1, stroke=0)
    pdf.setStrokeColorRGB(0.15, 0.35, 0.2)
    pdf.setLineWidth(0.7)
    pdf.rect(STAMP_MARGIN, STAMP_MARGIN, width - 2 * STAMP_MARGIN, band_height - 2 * STAMP_MARGIN)
    y = band_height - STAMP_MARGIN - 13
    for index, line in enumerate(lines):
        pdf.setFont("ApprovalSans", 9 if index == 0 else 7.5)
        pdf.setFillColorRGB(0.08, 0.22, 0.12)
        pdf.drawString(STAMP_MARGIN + 8, y, line)
        y -= 12 if index == 0 else 10
    pdf.save()
    return output.getvalue()


def create_approved_pdf(original_pdf: bytes, snapshot: dict[str, Any]) -> bytes:
    original_attachments = enumerate_attachments(original_pdf)
    reader = PdfReader(BytesIO(original_pdf), strict=False)
    if not reader.pages:
        raise WorkflowError("Original PDF has no pages")
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    page = writer.pages[-1]
    width = float(page.mediabox.width)
    original_height = float(page.mediabox.height)
    logical_lines = _stamp_lines(snapshot)
    max_chars = max(32, int((width - 2 * STAMP_MARGIN - 16) / 4.5))
    lines = [
        wrapped
        for line in logical_lines
        for wrapped in (textwrap.wrap(line, width=max_chars) or [""])
    ]
    band_height = max(96.0, 38.0 + len(lines) * 10.0)
    original_bottom = float(page.mediabox.bottom)
    page.mediabox.bottom = original_bottom - band_height
    page.cropbox.bottom = min(float(page.cropbox.bottom), original_bottom - band_height)
    overlay = PdfReader(BytesIO(_stamp_overlay(width, original_height + band_height, band_height, lines))).pages[0]
    page.merge_transformed_page(
        overlay,
        Transformation().translate(tx=float(page.mediabox.left), ty=original_bottom - band_height),
        over=True,
        expand=False,
    )

    def render() -> bytes:
        output = BytesIO()
        writer.write(output)
        return output.getvalue()

    approved = render()
    expected = {(row.filename, row.sha256) for row in original_attachments}
    actual = {
        (row.filename, row.sha256) for row in enumerate_attachments(approved)
    }
    missing = expected - actual
    if missing:
        for attachment in original_attachments:
            if (attachment.filename, attachment.sha256) in missing:
                writer.add_attachment(attachment.filename, attachment.content)
        approved = render()
        actual = {
            (row.filename, row.sha256) for row in enumerate_attachments(approved)
        }
    if actual != expected:
        raise WorkflowError("Approved PDF did not preserve embedded attachments byte-for-byte")
    return approved


def finalize_approved_pdf_bytes(
    db: Session,
    artifact: ApprovedPdfArtifact,
    original_pdf: bytes,
    approved_pdf: bytes,
) -> None:
    original_hash = _sha256(original_pdf)
    if artifact.original_pdf_sha256 and artifact.original_pdf_sha256 != original_hash:
        raise WorkflowError("Original PDF hash changed before approved-copy creation")
    artifact.original_pdf_sha256 = original_hash
    artifact.approved_pdf_sha256 = _sha256(approved_pdf)
    artifact.approved_pdf_size = len(approved_pdf)
    artifact.attachment_manifest = attachment_manifest(approved_pdf)
    artifact.status = ApprovedPdfStatus.CREATED
    record_event(
        db,
        "APPROVED_PDF_CREATED",
        invoice=artifact.invoice,
        revision_number=artifact.revision.number,
        metadata={
            "artifact_id": artifact.id,
            "original_sha256": artifact.original_pdf_sha256,
            "approved_sha256": artifact.approved_pdf_sha256,
            "isdoc_sha256": artifact.invoice.isdoc_sha256,
            "stamp_version": artifact.stamp_version,
        },
    )


def mark_approved_pdf_stored(
    db: Session, artifact: ApprovedPdfArtifact, paperless_document_id: int
) -> None:
    artifact.paperless_document_id = paperless_document_id
    artifact.status = ApprovedPdfStatus.STORED
    artifact.stored_at = datetime.now(UTC)
    if artifact.invoice.pohoda_import_method == PohodaImportMethod.PDF_ISDOC:
        if artifact.invoice.status == InvoiceStatus.APPROVED:
            transition(db, artifact.invoice, InvoiceStatus.READY_FOR_EXPORT, "system")
        if artifact.invoice.status == InvoiceStatus.READY_FOR_EXPORT:
            transition(db, artifact.invoice, InvoiceStatus.EXPORT_CREATED, "system")
    record_event(
        db,
        "APPROVED_PDF_STORED",
        invoice=artifact.invoice,
        revision_number=artifact.revision.number,
        metadata={
            "artifact_id": artifact.id,
            "paperless_document_id": paperless_document_id,
            "approved_sha256": artifact.approved_pdf_sha256,
        },
    )


def mark_pdf_isdoc_imported(
    db: Session, invoice: Invoice, artifact: ApprovedPdfArtifact, actor: str
) -> None:
    if invoice.pohoda_import_method != PohodaImportMethod.PDF_ISDOC:
        raise WorkflowError("Faktura nepoužívá importní metodu PDF + ISDOC")
    if artifact.invoice_id != invoice.id or artifact.revision_id != invoice.current_revision.id:
        raise WorkflowError("Schválená kopie nepatří aktuální revizi")
    if artifact.status != ApprovedPdfStatus.STORED:
        raise WorkflowError("Schválená PDF kopie není bezpečně uložena v Paperless")
    if invoice.status != InvoiceStatus.EXPORT_CREATED:
        raise WorkflowError("PDF + ISDOC není připraveno k potvrzení importu")
    transition(db, invoice, InvoiceStatus.IMPORTED_TO_POHODA, actor)
    invoice.imported_to_pohoda_at = datetime.now(UTC)
    invoice.imported_to_pohoda_by = actor
    invoice.imported_export_id = artifact.id
    record_event(
        db, "IMPORTED_TO_POHODA", actor=actor, invoice=invoice,
        metadata={"approved_pdf_artifact_id": artifact.id, "method": "PDF_ISDOC"},
    )
