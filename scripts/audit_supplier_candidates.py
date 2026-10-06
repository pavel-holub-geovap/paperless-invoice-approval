"""Read-only audit of supplier/customer confusion; no OCR, tokens or raw responses dumped."""
from __future__ import annotations

import json

from app.config import get_settings
from app.db import SessionLocal
from app.models import AIExtraction, Invoice
from sqlalchemy import select


def main():
    target = get_settings().pohoda_target_ico
    matches = []
    with SessionLocal() as db:
        for run, invoice in db.execute(select(AIExtraction, Invoice).join(Invoice, Invoice.id == AIExtraction.invoice_id)):
            parsed = run.parsed_result or {}
            ico = parsed.get("supplier_ico") or parsed.get("ico") or {}
            value = ico.get("value") if isinstance(ico, dict) else ico
            name = parsed.get("supplier_name") or {}
            name_value = name.get("value") if isinstance(name, dict) else name
            if str(value or "").replace(" ", "") != target and "geovap" not in str(name_value or "").casefold():
                continue
            matches.append({
                "invoice_id": invoice.id, "paperless_document_id": invoice.paperless_document_id,
                "extraction_id": run.id, "prompt_version": run.prompt_version,
                "supplier_name": name_value,
                "supplier_evidence": name.get("source_text") if isinstance(name, dict) else None,
                "ico_evidence": ico.get("source_text") if isinstance(ico, dict) else None,
                "ocr_has_supplier_label": "dodavatel" in (invoice.paperless_ocr_text or "").casefold(),
                "ocr_has_customer_label": "odběratel" in (invoice.paperless_ocr_text or "").casefold(),
                "applied": run.applied,
            })
    print(json.dumps({"target_ico": target, "self_supplier_candidates": matches}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
