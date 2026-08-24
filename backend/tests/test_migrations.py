from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_empty_database_upgrades_through_all_revisions(tmp_path: Path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "migration-test.db"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    with sqlite3.connect(database_path) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        invoice_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(invoices)").fetchall()
        }
        validation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(validation_results)").fetchall()
        }
        ai_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(ai_extractions)").fetchall()
        }
        allocation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(allocations)").fetchall()
        }
        assignment_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(approval_assignments)").fetchall()
        }

    assert revision == ("0006",)
    assert {
        "paperless_title",
        "paperless_ocr_text",
        "sync_status",
        "ai_status",
        "original_review_confirmed",
        "original_reviewed_at",
        "original_reviewed_by",
        "disposition",
        "disposition_reason",
        "disposition_comment",
        "disposition_actor",
        "disposition_changed_at",
        "duplicate_of_invoice_id",
        "source_status",
        "source_missing_at",
    } <= invoice_columns
    assert {"expected", "actual"} <= validation_columns
    assert {"parsed_result", "raw_response", "extraction_revision", "duration_ms"} <= ai_columns
    assert {"note", "vat_breakdown", "created_by", "created_at", "updated_at"} <= allocation_columns
    assert {
        "status",
        "assigned_by",
        "assigned_at",
        "decided_at",
        "invalidated_at",
        "invalidation_reason",
    } <= assignment_columns
