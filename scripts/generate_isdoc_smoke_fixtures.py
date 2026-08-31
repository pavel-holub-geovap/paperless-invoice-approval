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
  <DocumentType>1</DocumentType><ID>SMOKE-ISDOC-2026-001</ID><UUID>77692e7d-77aa-40b2-b3bc-b7bb23432d15</UUID>
  <IssueDate>2026-08-20</IssueDate><TaxPointDate>2026-08-20</TaxPointDate><VATApplicable>true</VATApplicable>
  <ElectronicPossibilityAgreementReference>Smoke fixture</ElectronicPossibilityAgreementReference>
  <LocalCurrencyCode>CZK</LocalCurrencyCode><CurrRate>1</CurrRate><RefCurrRate>1</RefCurrRate>
  <AccountingSupplierParty><Party><PartyIdentification><ID>28652240</ID></PartyIdentification><PartyName><Name>Smoke ISDOC s.r.o.</Name></PartyName>
  <PostalAddress><StreetName>Testovací</StreetName><BuildingNumber>1</BuildingNumber><CityName>Praha</CityName><PostalZone>10000</PostalZone><Country><IdentificationCode>CZ</IdentificationCode><Name>Česká republika</Name></Country></PostalAddress>
  <PartyTaxScheme><CompanyID>CZ28652240</CompanyID><TaxScheme>VAT</TaxScheme></PartyTaxScheme></Party></AccountingSupplierParty>
  <AccountingCustomerParty><Party><PartyIdentification><ID>15049248</ID></PartyIdentification><PartyName><Name>Smoke odběratel s.r.o.</Name></PartyName>
  <PostalAddress><StreetName>Odběratelská</StreetName><BuildingNumber>2</BuildingNumber><CityName>Praha</CityName><PostalZone>10000</PostalZone><Country><IdentificationCode>CZ</IdentificationCode><Name>Česká republika</Name></Country></PostalAddress>
  <PartyTaxScheme><CompanyID>CZ15049248</CompanyID><TaxScheme>VAT</TaxScheme></PartyTaxScheme></Party></AccountingCustomerParty>
  <InvoiceLines><InvoiceLine><ID>0</ID><InvoicedQuantity unitCode="ks">1</InvoicedQuantity><LineExtensionAmount>1000.00</LineExtensionAmount><LineExtensionAmountTaxInclusive>1210.00</LineExtensionAmountTaxInclusive><LineExtensionTaxAmount>210.00</LineExtensionTaxAmount><UnitPrice>1000.00</UnitPrice><UnitPriceTaxInclusive>1210.00</UnitPriceTaxInclusive><ClassifiedTaxCategory><Percent>21</Percent><VATCalculationMethod>0</VATCalculationMethod></ClassifiedTaxCategory><Item><Description>Smoke služba</Description></Item></InvoiceLine></InvoiceLines>
  <TaxTotal><TaxSubTotal><TaxableAmount>1000.00</TaxableAmount><TaxAmount>210.00</TaxAmount><TaxInclusiveAmount>1210.00</TaxInclusiveAmount><AlreadyClaimedTaxableAmount>0.00</AlreadyClaimedTaxableAmount><AlreadyClaimedTaxAmount>0.00</AlreadyClaimedTaxAmount><AlreadyClaimedTaxInclusiveAmount>0.00</AlreadyClaimedTaxInclusiveAmount><DifferenceTaxableAmount>1000.00</DifferenceTaxableAmount><DifferenceTaxAmount>210.00</DifferenceTaxAmount><DifferenceTaxInclusiveAmount>1210.00</DifferenceTaxInclusiveAmount><TaxCategory><Percent>21</Percent><TaxScheme>VAT</TaxScheme><VATApplicable>true</VATApplicable></TaxCategory></TaxSubTotal><TaxAmount>210.00</TaxAmount></TaxTotal>
  <LegalMonetaryTotal><TaxExclusiveAmount>1000.00</TaxExclusiveAmount><TaxInclusiveAmount>1210.00</TaxInclusiveAmount><AlreadyClaimedTaxExclusiveAmount>0.00</AlreadyClaimedTaxExclusiveAmount><AlreadyClaimedTaxInclusiveAmount>0.00</AlreadyClaimedTaxInclusiveAmount><DifferenceTaxExclusiveAmount>1000.00</DifferenceTaxExclusiveAmount><DifferenceTaxInclusiveAmount>1210.00</DifferenceTaxInclusiveAmount><PayableRoundingAmount>0.00</PayableRoundingAmount><PaidDepositsAmount>0.00</PaidDepositsAmount><PayableAmount>1210.00</PayableAmount></LegalMonetaryTotal>
  <PaymentMeans><Payment><PaidAmount>1210.00</PaidAmount><PaymentMeansCode>42</PaymentMeansCode><Details><PaymentDueDate>2026-09-03</PaymentDueDate><ID>123456789</ID><BankCode>0100</BankCode><Name>Smoke banka</Name><IBAN>CZ6501000000000123456789</IBAN><BIC>KOMBCZPPXXX</BIC><VariableSymbol>2026001</VariableSymbol></Details></Payment></PaymentMeans>
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
