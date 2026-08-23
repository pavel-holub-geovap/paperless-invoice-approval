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

    assert revision == ("0002",)
    assert {"paperless_title", "paperless_ocr_text", "sync_status"} <= invoice_columns
