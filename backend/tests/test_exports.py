from __future__ import annotations

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
    InvoiceStatus,
    UserIdentity,
)
from app.services.exports import create_export_batch, mark_batch_imported
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
            "ico": "27082440",
            "dic": "CZ27082440",
            "invoice_number": invoice_number,
            "variable_symbol": "2026001",
            "issue_date": "2026-08-01",
            "taxable_supply_date": "2026-08-01",
            "due_date": "2026-08-15",
            "currency": "CZK",
            "total_amount": "121.00",
            "description": "Testovací licence",
            "vat_breakdown": [{"base": "100.00", "rate": "21", "vat": "21.00"}],
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
    xsd = Path(__file__).resolve().parents[2] / "fixtures" / "pohoda" / "data.xsd"
    settings = Settings(export_archive_dir=tmp_path, pohoda_xsd_path=xsd)
    batch = await create_export_batch(
        db, settings, FakePaperless(), [invoice], "manager", xsd
    )
    db.flush()
    assert invoice.status == InvoiceStatus.EXPORT_CREATED
    assert batch.imported_at is None
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


@pytest.mark.asyncio
async def test_rejected_invoice_cannot_be_exported(db: Session, tmp_path: Path) -> None:
    invoice = approved_invoice(db)
    invoice.status = InvoiceStatus.REJECTED
    xsd = Path(__file__).resolve().parents[2] / "fixtures" / "pohoda" / "data.xsd"
    settings = Settings(export_archive_dir=tmp_path, pohoda_xsd_path=xsd)
    with pytest.raises(WorkflowError, match="not ready"):
        await create_export_batch(db, settings, FakePaperless(), [invoice], "manager", xsd)


@pytest.mark.asyncio
async def test_batch_export_contains_pdf_and_xml_for_multiple_invoices(
    db: Session, tmp_path: Path
) -> None:
    first = approved_invoice(db, 601, "BATCH-1", "IT")
    second = approved_invoice(db, 602, "BATCH-2", "PROVOZ")
    xsd = Path(__file__).resolve().parents[2] / "fixtures" / "pohoda" / "data.xsd"
    settings = Settings(export_archive_dir=tmp_path, pohoda_xsd_path=xsd)
    batch = await create_export_batch(
        db, settings, FakePaperless(), [first, second], "manager", xsd
    )
    with zipfile.ZipFile(batch.archive_path) as archive:
        assert sorted(archive.namelist()) == [
            "BATCH-1.pdf",
            "BATCH-1.xml",
            "BATCH-2.pdf",
            "BATCH-2.xml",
        ]
    assert first.status == second.status == InvoiceStatus.EXPORT_CREATED
