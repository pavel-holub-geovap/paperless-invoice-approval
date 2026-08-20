from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Allocation, CostCenter, InvoiceRevision
from app.services.pohoda import generate_invoice_xml, validate_xml


def test_generated_received_invoice_validates_against_official_xsd(db: Session) -> None:
    revision = InvoiceRevision(
        invoice_id="00000000-0000-0000-0000-000000000001",
        number=1,
        data={
            "supplier_name": "Dodavatel s.r.o.",
            "ico": "27082440",
            "dic": "CZ27082440",
            "address": "Testovací 1, Praha",
            "invoice_number": "2026001",
            "variable_symbol": "2026001",
            "issue_date": "2026-08-01",
            "taxable_supply_date": "2026-08-01",
            "due_date": "2026-08-15",
            "currency": "CZK",
            "total_amount": "121.00",
            "description": "Testovací licence",
            "vat_breakdown": [{"base": "100.00", "rate": "21", "vat": "21.00"}],
        },
    )
    centre = CostCenter(code="IT", name="IT", pohoda_code="IT")
    allocation = Allocation(
        invoice_id=revision.invoice_id,
        revision_id=revision.id,
        cost_center_id=centre.id,
        amount=Decimal("121.00"),
        cost_center=centre,
    )
    xml = generate_invoice_xml(revision, [allocation])
    xsd = Path(__file__).resolve().parents[2] / "fixtures" / "pohoda" / "data.xsd"
    assert validate_xml(xml, xsd) == []
    assert b"receivedInvoice" in xml
    assert b">IT<" in xml

