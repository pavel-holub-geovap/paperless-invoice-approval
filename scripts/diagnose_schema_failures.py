#!/usr/bin/env python3
"""Read-only inventory of Qwen schema failures without invoice contents."""

from __future__ import annotations

import json
from collections import Counter

from app.db import SessionLocal
from app.models import AIExtraction
from sqlalchemy import select
from sqlalchemy.orm import selectinload


def main() -> None:
    with SessionLocal() as db:
        rows = db.scalars(
            select(AIExtraction)
            .where(
                AIExtraction.model == "qwen3:8b",
            )
            .options(selectinload(AIExtraction.invoice))
            .order_by(AIExtraction.created_at.desc())
            .limit(50)
        ).all()
        report = [
            {
                "extraction_id": row.id,
                "extraction_revision": row.extraction_revision,
                "paperless_document_id": row.invoice.paperless_document_id,
                "invoice_id": row.invoice_id,
                "title": row.invoice.paperless_title,
                "model": row.model,
                "schema_version": row.schema_version,
                "prompt_version": row.prompt_version,
                "status": row.status,
                "error_message": row.error_message,
                "raw_response_preserved": row.raw_response is not None,
                "schema_validation_errors": row.schema_validation_errors_json,
                "corrective_retry_count": row.corrective_retry_count,
                "created_at": row.created_at,
            }
            for row in rows
            if row.error_code == "SCHEMA_VALIDATION_FAILED"
        ]
        groups = Counter(
            (error.get("path", "unknown"), error.get("type", "unknown"))
            for row in report
            for error in row["schema_validation_errors"]
        )
        print(
            json.dumps(
                {
                    "failures": report,
                    "groups": [
                        {"path": path, "type": error_type, "count": count}
                        for (path, error_type), count in groups.most_common()
                    ],
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )


if __name__ == "__main__":
    main()
