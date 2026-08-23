from __future__ import annotations

import hashlib
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    Allocation,
    ApprovalAction,
    ApprovalAssignment,
    CostCenter,
    ExportArtifact,
    InvoiceStatus,
    UserIdentity,
)
from app.services.exports import (
    create_export_batch,
    generate_export_artifact,
    mark_batch_imported,
    store_pohoda_response,
)
from app.services.validation import run_validations
from app.services.workflow import (
    WorkflowError,
    confirm_original,
    create_invoice,
    decide,
    submit_for_approval,
    transition,
    update_invoice_data,
)


class FakePaperless:
    async def download_pdf(self, document_id: int) -> bytes:
        return b"%PDF-1.4\n% synthetic fixture " + str(document_id).encode()


def approved_invoice(
    db: Session,
    paperless_id: int = 501,
    invoice_number: str = "EXP-TEST-1",
    centre_code: str = "GIS",
):
    invoice = create_invoice(db, paperless_id)
    update_invoice_data(
        db,
        invoice,
        {
            "supplier_name": "Dodavatel s.r.o.",
            "supplier_ico": "27082440",
            "supplier_dic": "CZ27082440",
            "supplier_street": "Testovací 1",
            "supplier_city": "Praha",
            "supplier_zip": "100 00",
            "invoice_number": invoice_number,
            "variable_symbol": "2026001",
            "issue_date": "2026-08-01",
            "taxable_supply_date": "2026-08-01",
            "due_date": "2026-08-15",
            "currency": "CZK",
            "total_amount": "121.00",
            "description": "Testovací licence",
            "vat_lines": [{"taxable_base": "100.00", "vat_rate": "21", "vat_amount": "21.00"}],
        },
        "manager",
    )
    transition(db, invoice, InvoiceStatus.VALIDATION, "system")
    centre = CostCenter(code=centre_code, name=centre_code, pohoda_code=centre_code)
    db.add(centre)
    db.flush()
    allocation = Allocation(
        invoice_id=invoice.id,
        revision_id=invoice.current_revision.id,
        cost_center_id=centre.id,
        amount=Decimal("121.00"),
    )
    db.add(allocation)
    db.flush()
    assignment = ApprovalAssignment(
        invoice_id=invoice.id,
        revision_id=invoice.current_revision.id,
        allocation_id=allocation.id,
        approver_subject="approver-1",
    )
    if db.get(UserIdentity, "approver-1") is None:
        db.add(UserIdentity(subject="approver-1", username="approver-1", roles=["APPROVER"]))
    db.add(assignment)
    db.flush()
    run_validations(db, invoice)
    transition(db, invoice, InvoiceStatus.QUEUE_REVIEW, "system")
    confirm_original(db, invoice, "manager")
    submit_for_approval(db, invoice, "manager")
    decide(db, assignment, ApprovalAction.APPROVE, "approver-1", None)
    return invoice


@pytest.mark.asyncio
async def test_export_zip_and_explicit_import_are_separate_states(
    db: Session, tmp_path: Path
) -> None:
    invoice = approved_invoice(db)
    xsd = Path(__file__).resolve().parents[2] / "schemas" / "pohoda" / "2025-10-16" / "data.xsd"
    settings = Settings(export_archive_dir=tmp_path, pohoda_xsd_path=xsd)
    batch = await create_export_batch(
        db, settings, FakePaperless(), [invoice], "manager", xsd
    )
    db.flush()
    assert invoice.status == InvoiceStatus.EXPORT_CREATED
    assert batch.imported_at is None
    assert batch.archive_sha256 == hashlib.sha256(Path(batch.archive_path).read_bytes()).hexdigest()
    assert len(batch.items) == 1
    artifact = db.get(ExportArtifact, batch.items[0].export_artifact_id)
    assert artifact is not None
    assert artifact.source_snapshot["revision_id"] == invoice.current_revision.id
    assert artifact.xml_sha256 == hashlib.sha256(Path(artifact.xml_path).read_bytes()).hexdigest()
    assert artifact.pdf_sha256 == hashlib.sha256(await FakePaperless().download_pdf(501)).hexdigest()
    with zipfile.ZipFile(batch.archive_path) as archive:
        names = archive.namelist()
        assert any(name.endswith(".pdf") for name in names)
        assert any(name.endswith(".xml") for name in names)
        xml_name = next(name for name in names if name.endswith(".xml"))
        assert b"receivedInvoice" in archive.read(xml_name)

    db.expire(batch, ["items"])
    mark_batch_imported(db, batch, "manager")
    assert invoice.status == InvoiceStatus.IMPORTED_TO_POHODA
    assert batch.imported_at is not None
    assert invoice.imported_export_id == artifact.id
    assert invoice.imported_to_pohoda_by == "manager"


@pytest.mark.asyncio
async def test_rejected_invoice_cannot_be_exported(db: Session, tmp_path: Path) -> None:
    invoice = approved_invoice(db)
    invoice.status = InvoiceStatus.REJECTED
    xsd = Path(__file__).resolve().parents[2] / "schemas" / "pohoda" / "2025-10-16" / "data.xsd"
    settings = Settings(export_archive_dir=tmp_path, pohoda_xsd_path=xsd)
    with pytest.raises(WorkflowError, match="Only an APPROVED"):
        await create_export_batch(db, settings, FakePaperless(), [invoice], "manager", xsd)


@pytest.mark.asyncio
async def test_batch_export_contains_pdf_and_xml_for_multiple_invoices(
    db: Session, tmp_path: Path
) -> None:
    first = approved_invoice(db, 601, "BATCH-1", "IT")
    second = approved_invoice(db, 602, "BATCH-2", "PROVOZ")
    xsd = Path(__file__).resolve().parents[2] / "schemas" / "pohoda" / "2025-10-16" / "data.xsd"
    settings = Settings(export_archive_dir=tmp_path, pohoda_xsd_path=xsd)
    batch = await create_export_batch(
        db, settings, FakePaperless(), [first, second], "manager", xsd
    )
    with zipfile.ZipFile(batch.archive_path) as archive:
        assert sorted(archive.namelist()) == [
            "invoice-BATCH-1/invoice.pdf",
            "invoice-BATCH-1/invoice.xml",
            "invoice-BATCH-2/invoice.pdf",
            "invoice-BATCH-2/invoice.xml",
        ]
    assert first.status == second.status == InvoiceStatus.EXPORT_CREATED


@pytest.mark.asyncio
async def test_reexport_keeps_revision_and_links_immutable_artifacts(
    db: Session, tmp_path: Path
) -> None:
    invoice = approved_invoice(db, 701, "REEXPORT-1", "IT-RE")
    xsd = Path(__file__).resolve().parents[2] / "schemas" / "pohoda" / "2025-10-16" / "data.xsd"
    settings = Settings(export_archive_dir=tmp_path, pohoda_xsd_path=xsd)
    first = await generate_export_artifact(db, settings, FakePaperless(), invoice, "manager")
    second = await generate_export_artifact(
        db,
        settings,
        FakePaperless(),
        invoice,
        "manager",
        reexport_reason="Opakovaný testovací import",
    )
    assert first.id != second.id
    assert second.source_export_id == first.id
    assert second.revision_id == first.revision_id == invoice.current_revision.id
    assert second.xml_sha256 == first.xml_sha256
    assert second.reexport_reason == "Opakovaný testovací import"


def test_response_upload_is_diagnostic_only(db: Session, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    content = (
        root
        / "schemas"
        / "pohoda"
        / "2025-10-16"
        / "samples"
        / "received-invoice-response.xml"
    ).read_bytes()
    settings = Settings(
        export_archive_dir=tmp_path,
        pohoda_xsd_path=root / "schemas" / "pohoda" / "2025-10-16" / "data.xsd",
    )
    upload = store_pohoda_response(db, settings, content, "response.xml", "manager")
    assert upload.parse_status == "PARSED"
    assert upload.parsed_result["state"] == "ok"
    assert upload.parsed_result["items"][0]["id"] == "POL001"
