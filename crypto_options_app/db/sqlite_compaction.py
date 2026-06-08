from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from crypto_options_app.db.connection import connect_read_only
from crypto_options_app.db.schema import initialize_schema


RAW_LEVEL_TABLE = "polymarket_order_book_levels"
RAW_BOOK_TABLE = "polymarket_order_books"
RAW_JSON_SANITIZED_TABLES = {
    "polymarket_price_ticks",
    "polymarket_updown_pair_snapshots",
    RAW_BOOK_TABLE,
}


@dataclass(frozen=True)
class SQLiteCompactionConfig:
    source_path: Path
    target_path: Path
    recent_raw_book_hours: int = 6
    batch_size: int = 5000
    skip_order_book_levels: bool = True
    sanitize_raw_json: bool = True


@dataclass
class TableCompactionSummary:
    table: str
    copied_rows: int = 0
    skipped: bool = False
    where_clause: str | None = None
    sanitized_source_json: bool = False
    error: str | None = None


@dataclass
class SQLiteCompactionResult:
    status: str
    source_path: str
    target_path: str
    started_at_utc: str
    completed_at_utc: str
    source_bytes: int
    target_bytes: int
    integrity_check: str
    tables: list[TableCompactionSummary] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "source_bytes": self.source_bytes,
            "target_bytes": self.target_bytes,
            "integrity_check": self.integrity_check,
            "tables": [summary.__dict__ for summary in self.tables],
            "blockers": list(self.blockers),
        }


def compact_sqlite_for_local_services(config: SQLiteCompactionConfig) -> SQLiteCompactionResult:
    """Create a compact SQLite fallback DB without raw CLOB book bloat.

    The canonical replay and strategy economics use price tick columns, paired
    Up/Down snapshots, and event path stats. Raw book levels are valuable for
    deep forensic analysis, but they are the local SQLite storage hazard. This
    compactor keeps replay-safe columns and strips bulky raw JSON payloads so
    local read paths and data services can recover while Postgres becomes the
    durable high-volume store.
    """

    source_path = Path(config.source_path)
    target_path = Path(config.target_path)
    started = datetime.now(UTC)
    blockers: list[str] = []
    table_summaries: list[TableCompactionSummary] = []
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    if target_path.exists():
        target_path.unlink()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    initialize_schema(target_path)
    source = connect_read_only(source_path)
    target = sqlite3.connect(str(target_path), timeout=30.0)
    target.row_factory = sqlite3.Row
    try:
        target.execute("PRAGMA foreign_keys = OFF")
        target.execute("PRAGMA synchronous = OFF")
        tables = [
            str(row["name"])
            for row in source.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        ]
        cutoff = datetime.now(UTC) - timedelta(hours=max(0, config.recent_raw_book_hours))
        for table in tables:
            summary = TableCompactionSummary(table=table)
            table_summaries.append(summary)
            if config.skip_order_book_levels and table == RAW_LEVEL_TABLE:
                summary.skipped = True
                summary.where_clause = "skipped_raw_order_book_levels"
                continue
            if not _target_table_exists(target, table):
                summary.skipped = True
                summary.where_clause = "missing_target_table"
                continue
            try:
                summary.copied_rows = _copy_table(
                    source,
                    target,
                    table=table,
                    batch_size=max(1, config.batch_size),
                    cutoff_iso=cutoff.isoformat(),
                    summary=summary,
                    sanitize_raw_json=config.sanitize_raw_json,
                )
            except Exception as exc:  # noqa: BLE001 - return structured compaction diagnostics.
                summary.error = f"{type(exc).__name__}: {exc}"
                blockers.append(f"{table}:{summary.error}")
                raise
        target.commit()
        integrity = str(target.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        source.close()
        target.close()

    completed = datetime.now(UTC)
    target_bytes = target_path.stat().st_size if target_path.exists() else 0
    status = "ok" if not blockers and integrity == "ok" else "degraded"
    return SQLiteCompactionResult(
        status=status,
        source_path=str(source_path),
        target_path=str(target_path),
        started_at_utc=started.isoformat(),
        completed_at_utc=completed.isoformat(),
        source_bytes=source_path.stat().st_size,
        target_bytes=target_bytes,
        integrity_check=integrity,
        tables=table_summaries,
        blockers=blockers,
    )


def replace_sqlite_with_compacted(*, source_path: Path, compacted_path: Path) -> dict[str, Any]:
    """Replace the source DB with a verified compacted DB.

    Callers must stop writers first. On Windows an open SQLite handle will make
    the replace fail, which is preferable to silently racing active services.
    """

    source_path = Path(source_path)
    compacted_path = Path(compacted_path)
    if not compacted_path.exists():
        raise FileNotFoundError(compacted_path)
    check = sqlite3.connect(str(compacted_path), timeout=30.0)
    try:
        integrity = str(check.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        check.close()
    if integrity != "ok":
        raise RuntimeError(f"compacted sqlite integrity check failed: {integrity}")

    removed: list[str] = []
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{source_path}{suffix}")
        if sidecar.exists():
            sidecar.unlink()
            removed.append(str(sidecar))
    original_bytes = source_path.stat().st_size if source_path.exists() else 0
    if source_path.exists():
        source_path.unlink()
        removed.append(str(source_path))
    shutil.move(str(compacted_path), str(source_path))
    return {
        "status": "ok",
        "source_path": str(source_path),
        "original_bytes_removed": original_bytes,
        "new_bytes": source_path.stat().st_size,
        "removed_paths": removed,
        "integrity_check": integrity,
    }


def _copy_table(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    *,
    table: str,
    batch_size: int,
    cutoff_iso: str,
    summary: TableCompactionSummary,
    sanitize_raw_json: bool,
) -> int:
    source_cols = _table_columns(source, table)
    target_cols = _table_columns(target, table)
    columns = [column for column in source_cols if column in target_cols]
    if not columns:
        summary.skipped = True
        summary.where_clause = "no_shared_columns"
        return 0
    where_clause = ""
    params: tuple[Any, ...] = ()
    if table == RAW_BOOK_TABLE and "observed_at_utc" in columns:
        where_clause = " WHERE observed_at_utc >= ?"
        params = (cutoff_iso,)
        summary.where_clause = f"observed_at_utc >= {cutoff_iso}"
    sanitize_index = columns.index("source_json") if sanitize_raw_json and "source_json" in columns else None
    summary.sanitized_source_json = sanitize_index is not None and table in RAW_JSON_SANITIZED_TABLES
    select_sql = f"SELECT {', '.join(_quote(column) for column in columns)} FROM {_quote(table)}{where_clause}"
    insert_sql = (
        f"INSERT OR IGNORE INTO {_quote(table)}({', '.join(_quote(column) for column in columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})"
    )
    cursor = source.execute(select_sql, params)
    copied = 0
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        prepared = []
        for row in rows:
            values = list(tuple(row))
            if summary.sanitized_source_json and sanitize_index is not None:
                values[sanitize_index] = json.dumps(
                    {"retained": "columns_only", "compacted_at_utc": datetime.now(UTC).isoformat()},
                    sort_keys=True,
                )
            prepared.append(tuple(values))
        target.executemany(insert_sql, prepared)
        target.commit()
        copied += len(prepared)
    return copied


def _target_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall()]


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
