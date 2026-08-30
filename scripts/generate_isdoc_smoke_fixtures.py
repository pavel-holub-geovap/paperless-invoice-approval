from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path

from app.services.approved_pdf import create_approved_pdf
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ISDOC_NAMESPACE = "http://isdoc.cz/namespace/2013"


def isdoc_xml() -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="{ISDOC_NAMESPACE}" version="6.0.2">
  <DocumentID>SMOKE-ISDOC-2026-001</DocumentID><IssueDate>2026-08-20</IssueDate>
  <TaxPointDate>2026-08-20</TaxPointDate><LocalCurrencyCode>CZK</LocalCurrencyCode>
  <AccountingSupplierParty><Party><PartyName><Name>Smoke ISDOC s.r.o.</Name></PartyName>
  <CompanyID>28652240</CompanyID><PartyTaxScheme><CompanyID>CZ28652240</CompanyID></PartyTaxScheme>
  <PostalAddress><StreetName>Testovací</StreetName><BuildingNumber>1</BuildingNumber><CityName>Praha</CityName><PostalZone>10000</PostalZone></PostalAddress>
  </Party></AccountingSupplierParty>
  <PaymentMeans><ID>123456789</ID><BankCode>0100</BankCode><VariableSymbol>2026001</VariableSymbol><PaymentDueDate>2026-09-03</PaymentDueDate></PaymentMeans>
  <TaxTotal><TaxAmount>210.00</TaxAmount><TaxSubTotal><TaxableAmount>1000.00</TaxableAmount><TaxAmount>210.00</TaxAmount><Percent>21</Percent></TaxSubTotal></TaxTotal>
  <LegalMonetaryTotal><TaxExclusiveAmount>1000.00</TaxExclusiveAmount><PayableAmount>1210.00</PayableAmount></LegalMonetaryTotal>
</Invoice>""".encode()


def base_pdf(title: str) -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4, invariant=1)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(42, 790, title)
    pdf.setFont("Helvetica", 11)
    pdf.drawString(42, 755, "Synthetic invoice for isolated Approval smoke testing")
    pdf.drawString(42, 25, "Original lower-edge content - approval stamp must not overlap")
    pdf.showPage()
    pdf.save()
    return output.getvalue()


def with_attachment(pdf: bytes, filename: str, content: bytes) -> bytes:
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(BytesIO(pdf)))
    writer.add_attachment(filename, content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    plain = base_pdf("Smoke invoice without ISDOC")
    valid = with_attachment(base_pdf("Smoke invoice with ISDOC"), "invoice.isdoc", isdoc_xml())
    invalid = with_attachment(
        base_pdf("Smoke invoice with invalid ISDOC"),
        "invalid.isdoc",
        b'<Invoice xmlns="http://isdoc.cz/namespace/2013" version="6.0.2"><DocumentID>',
    )
    snapshot = {
        "invoice_id": "visual-smoke",
        "invoice_revision_id": "visual-smoke-r1",
        "invoice_revision": 1,
        "invoice_number": "SMOKE-ISDOC-2026-001",
        "currency": "CZK",
        "total_approved": "1210.00",
        "approval_completed_at": "2026-08-20T12:34:00+00:00",
        "allocations": [{
            "allocation_id": "visual-allocation",
            "cost_center": {"code": "200", "name": "Vývoj"},
            "amount": "1210.00",
            "approvers": [{
                "subject": "approver-1",
                "name": "Jan Schvalovatel",
                "decided_at": "2026-08-20T12:34:00+00:00",
            }, {
                "subject": "approver-2",
                "name": "Jana Druhá Schvalovatelka",
                "decided_at": "2026-08-20T12:35:00+00:00",
            }],
        }],
    }
    files = {
        "smoke-no-isdoc.pdf": plain,
        "smoke-valid-isdoc.pdf": valid,
        "smoke-invalid-isdoc.pdf": invalid,
        "smoke-approved-isdoc.pdf": create_approved_pdf(valid, snapshot),
    }
    for name, content in files.items():
        (args.output / name).write_bytes(content)


if __name__ == "__main__":
    main()
