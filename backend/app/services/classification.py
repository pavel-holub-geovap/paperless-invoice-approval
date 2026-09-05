from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ApprovalAssignment,
    DocumentType,
    ExtractionSource,
    Invoice,
    InvoiceStatus,
    IsdocStatus,
    PohodaImportMethod,
    ProcessingMode,
)
from app.services.audit import record_event
from app.services.workflow import WorkflowError, fork_revision

POHODA_DOCUMENT_TYPES = {DocumentType.RECEIVED_INVOICE}
NON_POHODA_DOCUMENT_TYPES = {
    DocumentType.UNCLASSIFIED,
    DocumentType.RECEIVED_ADVANCE_INVOICE,
    DocumentType.ADVANCE_PAYMENT_TAX_DOCUMENT,
    DocumentType.FINAL_SETTLEMENT,
    DocumentType.RECEIPT,
    DocumentType.CARD_EXPENSE,
    DocumentType.EMPLOYEE_EXPENSE,
    DocumentType.CENTRAL_DOCUMENT,
    DocumentType.OTHER_SUPPORTING_DOCUMENT,
}


def determine_pohoda_import_method(invoice: Invoice) -> PohodaImportMethod:
    if not invoice.pohoda_eligible or invoice.document_type not in POHODA_DOCUMENT_TYPES:
        return PohodaImportMethod.NONE
    if invoice.isdoc_status == IsdocStatus.VALID:
        return PohodaImportMethod.PDF_ISDOC
    return PohodaImportMethod.GENERATED_XML


def refresh_business_routing(invoice: Invoice) -> None:
    if invoice.document_type in NON_POHODA_DOCUMENT_TYPES:
        invoice.pohoda_eligible = False
    if invoice.processing_mode == ProcessingMode.CENTRAL_MANUAL:
        invoice.pohoda_eligible = False
    invoice.pohoda_import_method = determine_pohoda_import_method(invoice)


def classify_document(
    db: Session,
    invoice: Invoice,
    *,
    document_type: DocumentType,
    processing_mode: ProcessingMode,
    actor: str,
    pohoda_eligible: bool | None = None,
) -> None:
    if processing_mode == ProcessingMode.CENTRAL_MANUAL and document_type != DocumentType.CENTRAL_DOCUMENT:
        raise WorkflowError("CENTRAL_MANUAL je povolen pouze pro centrální dokument")
    old = {
        "document_type": invoice.document_type.value,
        "processing_mode": invoice.processing_mode.value,
        "pohoda_eligible": invoice.pohoda_eligible,
        "pohoda_import_method": invoice.pohoda_import_method.value,
    }
    changed = invoice.document_type != document_type or invoice.processing_mode != processing_mode
    progressed = invoice.status not in {
        InvoiceStatus.NEW,
        InvoiceStatus.AI_PROCESSING,
        InvoiceStatus.VALIDATION,
        InvoiceStatus.QUEUE_REVIEW,
        InvoiceStatus.NEEDS_REVIEW,
    }
    revision_was_submitted = bool(
        invoice.current_revision
        and (
            invoice.current_revision.submitted_to_queue_at is not None
            or invoice.current_revision.queue_manager_reviewed_at is not None
        )
    )
    if changed and (progressed or revision_was_submitted):
        fork_revision(db, invoice, actor, "Změna typu dokladu nebo režimu zpracování")
    invoice.document_type = document_type
    invoice.processing_mode = processing_mode
    if processing_mode != ProcessingMode.FOR_APPROVAL and invoice.current_revision is not None:
        assignments = db.scalars(
            select(ApprovalAssignment).where(
                ApprovalAssignment.revision_id == invoice.current_revision.id,
                ApprovalAssignment.active.is_(True),
            )
        ).all()
        for assignment in assignments:
            assignment.active = False
        if assignments:
            record_event(
                db,
                "APPROVAL_ASSIGNMENTS_DEACTIVATED_FOR_PROCESSING_MODE",
                actor=actor,
                invoice=invoice,
                metadata={"assignment_ids": [assignment.id for assignment in assignments]},
            )
    if document_type == DocumentType.RECEIVED_INVOICE:
        if processing_mode == ProcessingMode.FOR_APPROVAL:
            invoice.pohoda_eligible = True
        elif processing_mode == ProcessingMode.RECORD_ONLY:
            invoice.pohoda_eligible = bool(pohoda_eligible)
    else:
        invoice.pohoda_eligible = False
    refresh_business_routing(invoice)
    new = {
        "document_type": invoice.document_type.value,
        "processing_mode": invoice.processing_mode.value,
        "pohoda_eligible": invoice.pohoda_eligible,
        "pohoda_import_method": invoice.pohoda_import_method.value,
    }
    if old == new:
        return
    if old["document_type"] != new["document_type"]:
        record_event(
            db, "DOCUMENT_CLASSIFIED", actor=actor, invoice=invoice,
            old_value={"document_type": old["document_type"]},
            new_value={"document_type": new["document_type"]},
        )
    if old["processing_mode"] != new["processing_mode"]:
        record_event(
            db, "PROCESSING_MODE_CHANGED", actor=actor, invoice=invoice,
            old_value={"processing_mode": old["processing_mode"]},
            new_value={"processing_mode": new["processing_mode"]},
        )
    if old["pohoda_import_method"] != new["pohoda_import_method"]:
        record_event(
            db, "POHODA_IMPORT_METHOD_SELECTED", actor=actor, invoice=invoice,
            old_state=old["pohoda_import_method"], new_state=new["pohoda_import_method"],
        )


def set_extraction_source(db: Session, invoice: Invoice, source: ExtractionSource) -> None:
    if invoice.extraction_source == source:
        return
    old = invoice.extraction_source
    invoice.extraction_source = source
    record_event(
        db, "EXTRACTION_SOURCE_SELECTED", invoice=invoice,
        old_state=old.value, new_state=source.value,
    )
