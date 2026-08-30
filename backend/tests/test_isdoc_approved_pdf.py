from __future__ import annotations

import hashlib
from decimal import Decimal
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    Allocation,
    ApprovalAssignment,
    CostCenter,
    DocumentType,
    ExtractionSource,
    InvoiceStatus,
    IsdocExtraction,
    IsdocStatus,
    PohodaImportMethod,
    ProcessingJob,
    ProcessingMode,
    UserIdentity,
)
from app.services.approval_setup import replace_approvers
from app.services.approved_pdf import create_approved_pdf, prepare_approved_pdf_artifact
from app.services.classification import classify_document
from app.services.exports import generate_export_artifact
from app.services.extraction import queue_ai_extraction
from app.services.isdoc import (
    apply_isdoc_inspection,
    enumerate_attachments,
    inspect_pdf_isdoc,
)
from app.services.workflow import WorkflowError, create_invoice

ISDOC_NAMESPACE = "http://isdoc.cz/namespace/2013"


def valid_isdoc(*, number: str = "ISDOC-2026-001") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="{ISDOC_NAMESPACE}" version="6.0.2">
  <DocumentID>{number}</DocumentID>
  <IssueDate>2026-08-20</IssueDate>
  <TaxPointDate>2026-08-20</TaxPointDate>
  <LocalCurrencyCode>CZK</LocalCurrencyCode>
  <AccountingSupplierParty><Party>
    <PartyName><Name>ISDOC Dodavatel s.r.o.</Name></PartyName>
    <CompanyID>28652240</CompanyID>
    <PartyTaxScheme><CompanyID>CZ28652240</CompanyID></PartyTaxScheme>
    <PostalAddress><StreetName>Testovací</StreetName><BuildingNumber>1</BuildingNumber><CityName>Praha</CityName><PostalZone>10000</PostalZone></PostalAddress>
  </Party></AccountingSupplierParty>
  <PaymentMeans><ID>123456789</ID><BankCode>0100</BankCode><VariableSymbol>2026001</VariableSymbol><PaymentDueDate>2026-09-03</PaymentDueDate></PaymentMeans>
  <TaxTotal><TaxAmount>210.00</TaxAmount><TaxSubTotal><TaxableAmount>1000.00</TaxableAmount><TaxAmount>210.00</TaxAmount><Percent>21</Percent></TaxSubTotal></TaxTotal>
  <LegalMonetaryTotal><TaxExclusiveAmount>1000.00</TaxExclusiveAmount><PayableAmount>1210.00</PayableAmount></LegalMonetaryTotal>
  <InvoiceLines><InvoiceLine><InvoicedQuantity>1</InvoicedQuantity><LineExtensionAmount>1000.00</LineExtensionAmount><Item><Description>Licence</Description><ClassifiedTaxCategory><Percent>21</Percent></ClassifiedTaxCategory></Item><UnitPrice>1000.00</UnitPrice></InvoiceLine></InvoiceLines>
</Invoice>""".encode()


def source_pdf(
    *,
    attachments: list[tuple[str, bytes]] | None = None,
    pages: int = 1,
    page_size: tuple[float, float] = A4,
) -> bytes:
    raw = BytesIO()
    pdf = canvas.Canvas(raw, pagesize=page_size, invariant=1)
    for page_number in range(1, pages + 1):
        pdf.setFont("Helvetica", 11)
        pdf.drawString(36, page_size[1] - 36, f"Original invoice page {page_number}")
        pdf.drawString(36, 20, "Original content at the lower edge")
        pdf.showPage()
    pdf.save()
    if not attachments:
        return raw.getvalue()
    reader = PdfReader(BytesIO(raw.getvalue()))
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    for filename, content in attachments:
        writer.add_attachment(filename, content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def approval_snapshot() -> dict[str, object]:
    return {
        "invoice_id": "invoice-workflow-123",
        "invoice_revision_id": "revision-2",
        "invoice_revision": 2,
        "invoice_number": "ISDOC-2026-001",
        "currency": "CZK",
        "total_approved": "1210.00",
        "approval_completed_at": "2026-08-20T12:34:00+00:00",
        "allocations": [
            {
                "allocation_id": "allocation-1",
                "cost_center": {"code": "200", "name": "Vývoj"},
                "amount": "1210.00",
                "approvers": [
                    {
                        "subject": "approver-1",
                        "name": "Jan Schvalovatel",
                        "decided_at": "2026-08-20T12:34:00+00:00",
                    },
                    {
                        "subject": "approver-2",
                        "name": "Jana Druhá Schvalovatelka",
                        "decided_at": "2026-08-20T12:35:00+00:00",
                    },
                ],
            }
        ],
    }


def test_pdf_without_attachment_uses_ocr_ai_fallback(db: Session) -> None:
    invoice = create_invoice(db, 8101)
    invoice.document_type = DocumentType.RECEIVED_INVOICE
    invoice.pohoda_eligible = True
    inspection = inspect_pdf_isdoc(source_pdf(), Settings())

    assert inspection.status == IsdocStatus.NOT_PRESENT
    assert apply_isdoc_inspection(db, invoice, inspection) is None
    assert invoice.extraction_source == ExtractionSource.OCR_AI
    assert invoice.pohoda_import_method == PohodaImportMethod.GENERATED_XML


def test_valid_isdoc_is_primary_immutable_extraction_and_ai_is_blocked(db: Session) -> None:
    payload = valid_isdoc()
    invoice = create_invoice(db, 8102)
    invoice.document_type = DocumentType.RECEIVED_INVOICE
    invoice.pohoda_eligible = True
    inspection = inspect_pdf_isdoc(
        source_pdf(attachments=[("../unsafe/Invoice.ISDOC", payload)]), Settings()
    )

    snapshot = apply_isdoc_inspection(db, invoice, inspection)

    assert inspection.status == IsdocStatus.VALID
    assert invoice.isdoc_filename == "Invoice.ISDOC"
    assert invoice.isdoc_sha256 == hashlib.sha256(payload).hexdigest()
    assert invoice.extraction_source == ExtractionSource.ISDOC
    assert invoice.pohoda_import_method == PohodaImportMethod.PDF_ISDOC
    assert invoice.current_revision.data["supplier_name"] == "ISDOC Dodavatel s.r.o."
    assert invoice.current_revision.data["supplier_ico"] == "28652240"
    assert invoice.current_revision.data["invoice_number"] == "ISDOC-2026-001"
    assert snapshot is not None
    assert snapshot.provenance["supplier_name"] == {"source": "ISDOC"}
    assert db.scalar(select(IsdocExtraction).where(IsdocExtraction.invoice_id == invoice.id))
    with pytest.raises(ValueError, match="ISDOC"):
        queue_ai_extraction(db, invoice, Settings(), "manager")
    assert not db.scalar(
        select(ProcessingJob).where(
            ProcessingJob.invoice_id == invoice.id,
            ProcessingJob.job_type == "AI_EXTRACT_INVOICE",
        )
    )


@pytest.mark.parametrize(
    ("name", "xml", "expected"),
    [
        ("broken.isdoc", b"<Invoice", IsdocStatus.INVALID),
        ("other.xml", b"<root><value>not ISDOC</value></root>", IsdocStatus.NOT_PRESENT),
        (
            "xxe.isdoc",
            b'<!DOCTYPE Invoice [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><Invoice xmlns="http://isdoc.cz/namespace/2013" version="6.0.2">&xxe;</Invoice>',
            IsdocStatus.INVALID,
        ),
    ],
)
def test_untrusted_embedded_xml_is_safely_classified(
    name: str, xml: bytes, expected: IsdocStatus
) -> None:
    result = inspect_pdf_isdoc(source_pdf(attachments=[(name, xml)]), Settings())
    assert result.status == expected


def test_multiple_valid_isdoc_candidates_are_rejected_as_ambiguous() -> None:
    result = inspect_pdf_isdoc(
        source_pdf(
            attachments=[
                ("first.isdoc", valid_isdoc(number="FIRST")),
                ("second.xml", valid_isdoc(number="SECOND")),
            ]
        ),
        Settings(),
    )
    assert result.status == IsdocStatus.INVALID
    assert "Multiple valid" in (result.error or "")


def test_isdoc_attachment_size_limit_is_blocking_for_candidate() -> None:
    payload = valid_isdoc() + b" " * 4096
    result = inspect_pdf_isdoc(
        source_pdf(attachments=[("large.isdoc", payload)]),
        Settings(isdoc_max_attachment_bytes=1024),
    )
    assert result.status == IsdocStatus.INVALID
    assert result.isdoc is not None
    assert result.isdoc.filename == "large.isdoc"
    assert "size limit" in (result.error or "")


@pytest.mark.parametrize("page_size", [A4, landscape(A4)])
@pytest.mark.parametrize("pages", [1, 3])
def test_approved_pdf_is_deterministic_non_overlapping_and_keeps_all_attachments(
    page_size: tuple[float, float], pages: int
) -> None:
    isdoc = valid_isdoc()
    note = b"unchanged supporting attachment"
    original = source_pdf(
        attachments=[("invoice.isdoc", isdoc), ("note.txt", note)],
        pages=pages,
        page_size=page_size,
    )
    original_hash = hashlib.sha256(original).hexdigest()
    original_reader = PdfReader(BytesIO(original))
    original_height = float(original_reader.pages[-1].mediabox.height)

    approved = create_approved_pdf(original, approval_snapshot())
    retry = create_approved_pdf(original, approval_snapshot())
    approved_reader = PdfReader(BytesIO(approved))

    assert hashlib.sha256(original).hexdigest() == original_hash
    assert hashlib.sha256(approved).hexdigest() != original_hash
    assert approved == retry
    assert len(approved_reader.pages) == pages
    assert float(approved_reader.pages[-1].mediabox.height) > original_height
    last_text = approved_reader.pages[-1].extract_text()
    assert "SCHVÁLENO" in last_text
    assert "Jan Schvalovatel" in last_text
    assert "Jana Druhá Schvalovatelka" in last_text
    assert "200 - Vývoj" in last_text
    assert "1 210,00 CZK" in last_text
    assert "revize 2" in last_text
    before = {(row.filename, row.sha256) for row in enumerate_attachments(original)}
    after = {(row.filename, row.sha256) for row in enumerate_attachments(approved)}
    assert after == before
    assert ("invoice.isdoc", hashlib.sha256(isdoc).hexdigest()) in after
    assert ("note.txt", hashlib.sha256(note).hexdigest()) in after


def test_approved_copy_service_rejects_unapproved_snapshot(db: Session) -> None:
    invoice = create_invoice(db, 8103)
    assert invoice.status != InvoiceStatus.APPROVED
    with pytest.raises(WorkflowError, match="final APPROVED"):
        prepare_approved_pdf_artifact(db, invoice, Settings())


@pytest.mark.asyncio
async def test_advance_and_central_documents_are_backend_blocked_from_pohoda(
    db: Session, tmp_path
) -> None:
    invoice = create_invoice(db, 8104)
    assert invoice.document_type == DocumentType.UNCLASSIFIED
    classify_document(
        db,
        invoice,
        document_type=DocumentType.RECEIVED_ADVANCE_INVOICE,
        processing_mode=ProcessingMode.FOR_APPROVAL,
        actor="manager",
    )
    assert invoice.processing_mode == ProcessingMode.FOR_APPROVAL
    assert invoice.pohoda_import_method == PohodaImportMethod.NONE
    with pytest.raises(WorkflowError, match="Zálohová faktura"):
        await generate_export_artifact(
            db,
            Settings(export_archive_dir=tmp_path, pohoda_target_ico="15049248"),
            object(),
            invoice,
            "manager",
        )

    with pytest.raises(WorkflowError, match="CENTRAL_MANUAL"):
        classify_document(
            db,
            invoice,
            document_type=DocumentType.RECEIPT,
            processing_mode=ProcessingMode.CENTRAL_MANUAL,
            actor="manager",
        )


def test_record_only_document_cannot_receive_approval_assignments(db: Session) -> None:
    invoice = create_invoice(db, 8105)
    classify_document(
        db,
        invoice,
        document_type=DocumentType.RECEIVED_INVOICE,
        processing_mode=ProcessingMode.FOR_APPROVAL,
        actor="manager",
    )
    centre = CostCenter(code="REC", name="Evidence", pohoda_code="REC")
    db.add(centre)
    db.flush()
    allocation = Allocation(
        invoice_id=invoice.id,
        revision_id=invoice.current_revision.id,
        cost_center_id=centre.id,
        amount=Decimal("100.00"),
    )
    db.add(allocation)
    db.flush()
    db.add(
        UserIdentity(
            subject="record-only-approver",
            username="Record Only Approver",
            roles=["APPROVER"],
            active=True,
        )
    )
    db.flush()
    replace_approvers(db, invoice, allocation, ["record-only-approver"], "manager")
    assert db.scalar(
        select(ApprovalAssignment).where(ApprovalAssignment.allocation_id == allocation.id)
    ).active

    classify_document(
        db,
        invoice,
        document_type=DocumentType.RECEIPT,
        processing_mode=ProcessingMode.RECORD_ONLY,
        actor="manager",
    )
    assert not db.scalar(
        select(ApprovalAssignment).where(ApprovalAssignment.allocation_id == allocation.id)
    ).active

    with pytest.raises(WorkflowError, match="FOR_APPROVAL"):
        replace_approvers(db, invoice, allocation, [], "manager")
