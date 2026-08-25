#!/usr/bin/env python3
"""Real Qwen3 8B GMtech DUZP re-extraction and audited apply smoke."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
from app.db import SessionLocal
from app.models import AIExtraction, Invoice
from smoke_stage_b import login, require, response_json
from sqlalchemy import select
from sqlalchemy.orm import selectinload

EXPECTED = {
    "issue_date": "2026-07-08",
    "taxable_supply_date": "2026-06-30",
    "due_date": "2026-08-07",
}


def detail(client: httpx.Client, base_url: str, invoice_id: str) -> dict[str, Any]:
    return response_json(
        client.get(f"{base_url}/api/invoices/{invoice_id}"),
        "GMtech invoice detail",
    )


def request(
    client: httpx.Client,
    method: str,
    url: str,
    csrf: str,
    *,
    json_body: dict[str, Any] | None = None,
    expected: int,
) -> httpx.Response:
    response = client.request(
        method,
        url,
        headers={"X-CSRF-Token": csrf},
        json=json_body,
    )
    require(
        response.status_code == expected,
        f"{method} {url} returned {response.status_code}: {response.text[:500]}",
    )
    return response


def values(result: dict[str, Any]) -> dict[str, Any]:
    return {
        field: (result.get(field) or {}).get("value")
        for field in EXPECTED
    }


def sources(result: dict[str, Any]) -> dict[str, Any]:
    return {
        field: (result.get(field) or {}).get("source_text")
        for field in EXPECTED
    }


def main() -> None:
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    document_id = int(os.environ.get("GMTECH_PAPERLESS_DOCUMENT_ID", "14"))
    manager = login(
        base_url,
        "queue-manager",
        os.environ["TEST_QUEUE_MANAGER_PASSWORD"],
    )
    try:
        user = response_json(manager.get(f"{base_url}/api/auth/me"), "manager /me")
        rows = response_json(
            manager.get(f"{base_url}/api/invoices?view=all"),
            "invoice inventory",
        )
        listed = next(
            row for row in rows if row["paperless_document_id"] == document_id
        )
        invoice_id = listed["id"]
        before = detail(manager, base_url, invoice_id)
        before_run = before["ai"]["latest"]
        before_revision = before_run["extraction_revision"] if before_run else 0
        require(before["data"].get("supplier_name") == "GMtech s.r.o.", "GMtech supplier is wrong")
        require(before["data"].get("invoice_number") == "20260182", "GMtech invoice number is wrong")
        require(
            {field: before["data"].get(field) for field in EXPECTED}
            == {
                "issue_date": "2026-07-08",
                "taxable_supply_date": "2026-07-08",
                "due_date": "2026-08-07",
            },
            "GMtech precondition does not contain the diagnosed historical date error",
        )
        queued = request(
            manager,
            "POST",
            f"{base_url}/api/invoices/{invoice_id}/ai-extractions",
            user["csrf_token"],
            expected=202,
        ).json()

        deadline = time.monotonic() + 1200
        completed = None
        while time.monotonic() < deadline:
            current = detail(manager, base_url, invoice_id)
            latest = current["ai"]["latest"]
            if latest and latest["extraction_revision"] > before_revision:
                if latest["status"] == "AI_COMPLETED":
                    completed = current
                    break
                if latest["status"] == "AI_FAILED":
                    raise RuntimeError(
                        f"GMtech extraction failed: {latest['error_code']} "
                        f"{latest['error_message']}"
                    )
            time.sleep(3)
        require(completed is not None, "GMtech Qwen3 8B extraction timed out")
        run = completed["ai"]["latest"]
        parsed = run["parsed_result"]
        require(run["id"] == queued["id"], "Completed extraction is not the queued run")
        require(run["model"] == "qwen3:8b", "GMtech extraction did not use Qwen3 8B")
        require(values(parsed) == EXPECTED, "Structured AI dates are wrong")
        require(
            {field: run["candidate_data"].get(field) for field in EXPECTED} == EXPECTED,
            "Normalized candidate dates are wrong",
        )
        source = sources(parsed)
        require(
            source["taxable_supply_date"] == "Datum zd. plnění: 30.06.2026",
            "DUZP evidence is wrong",
        )

        applied_response = request(
            manager,
            "POST",
            f"{base_url}/api/invoices/{invoice_id}/ai-extractions/{run['id']}/apply",
            user["csrf_token"],
            json_body={"confirm_overwrite": True},
            expected=200,
        )
        applied = applied_response.json()
        require(
            {field: applied["data"].get(field) for field in EXPECTED} == EXPECTED,
            "Applied API dates are wrong",
        )
        evidence = {
            row["field_name"]: row
            for row in applied["extracted_fields"]
            if row["field_name"] in EXPECTED
        }
        require(
            evidence["taxable_supply_date"]["source_text"]
            == "Datum zd. plnění: 30.06.2026",
            "Applied DUZP provenance is wrong",
        )
        audit = response_json(
            manager.get(f"{base_url}/api/invoices/{invoice_id}/audit"),
            "GMtech audit",
        )
        field_event = next(
            (
                row
                for row in reversed(audit)
                if row["event_type"] == "FIELD_CHANGED"
                and row["metadata"].get("field") == "taxable_supply_date"
                and row["metadata"].get("ai_extraction_id") == run["id"]
            ),
            None,
        )
        require(field_event is not None, "Extraction-linked DUZP FIELD_CHANGED audit is missing")
        require(
            field_event["old_value"] == {"taxable_supply_date": "2026-07-08"}
            and field_event["new_value"] == {"taxable_supply_date": "2026-06-30"},
            "DUZP FIELD_CHANGED audit values are wrong",
        )
        with SessionLocal() as db:
            stored = db.scalar(
                select(Invoice)
                .where(Invoice.id == invoice_id)
                .options(selectinload(Invoice.revisions))
            )
            require(stored is not None, "GMtech invoice is missing from the database")
            revision = stored.current_revision
            require(revision is not None, "GMtech current database revision is missing")
            db_current = {
                field: revision.data.get(field) for field in EXPECTED
            }
            require(db_current == EXPECTED, "Stored database dates are wrong")
            extraction = db.get(AIExtraction, run["id"])
            require(
                extraction is not None and extraction.raw_response is not None,
                "GMtech raw model response is missing",
            )
            raw_model_payload = json.loads(extraction.raw_response)
            raw_model_dates = values(raw_model_payload)
        print(
            json.dumps(
                {
                    "app_url": base_url,
                    "paperless_document_id": document_id,
                    "invoice_id": invoice_id,
                    "before_extraction_revision": before_revision,
                    "before": {field: before["data"].get(field) for field in EXPECTED},
                    "after_extraction_revision": run["extraction_revision"],
                    "model": run["model"],
                    "prompt_version": run["prompt_version"],
                    "duration_ms": run["duration_ms"],
                    "raw_model_dates": raw_model_dates,
                    "reconciled_structured_ai": values(parsed),
                    "normalized": {
                        field: run["candidate_data"].get(field) for field in EXPECTED
                    },
                    "db_current": db_current,
                    "api_current": {
                        field: applied["data"].get(field) for field in EXPECTED
                    },
                    "evidence": sources(parsed),
                    "invoice_revision": applied["current_revision_number"],
                    "audit_field_changed": field_event,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        manager.close()


if __name__ == "__main__":
    main()
