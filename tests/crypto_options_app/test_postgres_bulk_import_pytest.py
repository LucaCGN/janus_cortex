from __future__ import annotations

import sqlite3
from pathlib import Path

from crypto_options_app.db.postgres_import import PostgresBulkCopyConfig, copy_sqlite_tables_to_postgres


def test_bulk_copy_dry_run_summarizes_selected_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "source.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE app_settings (setting_key TEXT PRIMARY KEY, setting_value TEXT, updated_at_utc TEXT)")
        conn.execute("INSERT INTO app_settings VALUES('a', 'b', '2026-06-06T00:00:00Z')")

    result = copy_sqlite_tables_to_postgres(
        PostgresBulkCopyConfig(
            sqlite_path=db_path,
            tables=("app_settings",),
            limit_per_table=1,
            dry_run=True,
        )
    )

    assert result["status"] == "dry_run"
    assert result["table_count"] == 1
    assert result["source_rows_seen"] == 1
    assert result["tables"][0]["table"] == "app_settings"
    assert result["tables"][0]["column_count"] == 3


def test_bulk_copy_blocks_missing_source(tmp_path: Path) -> None:
    result = copy_sqlite_tables_to_postgres(PostgresBulkCopyConfig(sqlite_path=tmp_path / "missing.sqlite"))
    assert result["status"] == "blocked"
    assert result["blockers"][0].startswith("sqlite_source_missing")


def test_bulk_copy_rejects_unsafe_table_names(tmp_path: Path) -> None:
    db_path = tmp_path / "source.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute('CREATE TABLE "safe_table" (id TEXT PRIMARY KEY)')
    result = copy_sqlite_tables_to_postgres(
        PostgresBulkCopyConfig(sqlite_path=db_path, tables=("safe_table;DROP",), dry_run=True)
    )
    assert result["status"] == "blocked"
    assert "requested SQLite tables do not exist" in result["blockers"][0]
