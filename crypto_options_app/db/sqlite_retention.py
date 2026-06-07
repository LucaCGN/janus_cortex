from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


RAW_LEVEL_TABLE = "polymarket_order_book_levels"
RAW_BOOK_TABLE = "polymarket_order_books"
RAW_JSON_TABLES = (
    "polymarket_price_ticks",
    "polymarket_updown_pair_snapshots",
    RAW_BOOK_TABLE,
)


@dataclass(frozen=True)
class SQLiteRetentionConfig:
    db_path: Path
    recent_raw_book_hours: int = 2
    batch_size: int = 25_000
    delete_order_book_levels: bool = True
    sanitize_raw_json: bool = True
    checkpoint_each_batch: bool = True


@dataclass
class RetentionStepSummary:
    step: str
    affected_rows: int = 0
    status: str = "ok"
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SQLiteRetentionResult:
    status: str
    db_path: str
    started_at_utc: str
    completed_at_utc: str
    before: dict[str, Any]
    after: dict[str, Any]
    steps: list[RetentionStepSummary]
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "db_path": self.db_path,
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "before": self.before,
            "after": self.after,
            "steps": [step.__dict__ for step in self.steps],
            "blockers": list(self.blockers),
        }


def trim_sqlite_raw_option_storage(config: SQLiteRetentionConfig) -> SQLiteRetentionResult:
    """Trim raw option capture bloat in place without requiring a second DB copy.

    This is intentionally conservative about semantic data: it deletes raw book
    level rows, keeps price ticks/pair snapshots/event stats, and strips bulky
    raw JSON payloads after their replay-safe columns have already been stored.
    It commits and checkpoints in bounded batches so it can run when disk space
    is already tight.
    """

    db_path = Path(config.db_path)
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    started = datetime.now(UTC)
    conn = sqlite3.connect(str(db_path), timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 60000")
    steps: list[RetentionStepSummary] = []
    blockers: list[str] = []
    try:
        before = _storage_state(conn, db_path)
        _checkpoint(conn, truncate=True)
        if config.delete_order_book_levels and _table_exists(conn, RAW_LEVEL_TABLE):
            steps.append(
                _delete_all_by_rowid_batches(
                    conn,
                    table=RAW_LEVEL_TABLE,
                    batch_size=max(1, config.batch_size),
                    checkpoint_each_batch=config.checkpoint_each_batch,
                )
            )
        if _table_exists(conn, RAW_BOOK_TABLE):
            cutoff = datetime.now(UTC) - timedelta(hours=max(0, config.recent_raw_book_hours))
            steps.append(
                _delete_where_by_rowid_batches(
                    conn,
                    table=RAW_BOOK_TABLE,
                    where_sql="observed_at_utc < ?",
                    params=(cutoff.isoformat(),),
                    batch_size=max(1, config.batch_size),
                    checkpoint_each_batch=config.checkpoint_each_batch,
                )
            )
        if config.sanitize_raw_json:
            compact_payload = json.dumps(
                {"retained": "columns_only", "compacted_at_utc": datetime.now(UTC).isoformat()},
                sort_keys=True,
            )
            for table in RAW_JSON_TABLES:
                if _table_exists(conn, table) and "source_json" in _table_columns(conn, table):
                    steps.append(
                        _update_source_json_by_rowid_batches(
                            conn,
                            table=table,
                            source_json=compact_payload,
                            batch_size=max(1, config.batch_size),
                            checkpoint_each_batch=config.checkpoint_each_batch,
                        )
                    )
        _checkpoint(conn, truncate=True)
        after = _storage_state(conn, db_path)
    except Exception as exc:  # noqa: BLE001 - callers need structured retention diagnostics.
        blockers.append(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        conn.close()
    completed = datetime.now(UTC)
    return SQLiteRetentionResult(
        status="ok" if not blockers else "degraded",
        db_path=str(db_path),
        started_at_utc=started.isoformat(),
        completed_at_utc=completed.isoformat(),
        before=before,
        after=after,
        steps=steps,
        blockers=blockers,
    )


def _delete_all_by_rowid_batches(
    conn: sqlite3.Connection,
    *,
    table: str,
    batch_size: int,
    checkpoint_each_batch: bool,
) -> RetentionStepSummary:
    max_rowid = conn.execute(f"SELECT MAX(rowid) FROM {_quote(table)}").fetchone()[0]
    if max_rowid is None:
        return RetentionStepSummary(step=f"delete_all:{table}", affected_rows=0)
    affected = 0
    start = 0
    while start < int(max_rowid):
        end = start + batch_size
        before = conn.total_changes
        conn.execute(f"DELETE FROM {_quote(table)} WHERE rowid > ? AND rowid <= ?", (start, end))
        affected += conn.total_changes - before
        conn.commit()
        if checkpoint_each_batch:
            _checkpoint(conn, truncate=True)
        start = end
    return RetentionStepSummary(step=f"delete_all:{table}", affected_rows=affected)


def _delete_where_by_rowid_batches(
    conn: sqlite3.Connection,
    *,
    table: str,
    where_sql: str,
    params: tuple[Any, ...],
    batch_size: int,
    checkpoint_each_batch: bool,
) -> RetentionStepSummary:
    affected = 0
    while True:
        before = conn.total_changes
        conn.execute(
            f"""
            DELETE FROM {_quote(table)}
            WHERE rowid IN (
                SELECT rowid FROM {_quote(table)}
                WHERE {where_sql}
                LIMIT ?
            )
            """,
            (*params, batch_size),
        )
        changed = conn.total_changes - before
        conn.commit()
        affected += changed
        if checkpoint_each_batch:
            _checkpoint(conn, truncate=True)
        if changed == 0:
            break
    return RetentionStepSummary(
        step=f"delete_where:{table}",
        affected_rows=affected,
        details={"where": where_sql, "params": list(params)},
    )


def _update_source_json_by_rowid_batches(
    conn: sqlite3.Connection,
    *,
    table: str,
    source_json: str,
    batch_size: int,
    checkpoint_each_batch: bool,
) -> RetentionStepSummary:
    max_rowid = conn.execute(f"SELECT MAX(rowid) FROM {_quote(table)}").fetchone()[0]
    if max_rowid is None:
        return RetentionStepSummary(step=f"sanitize_source_json:{table}", affected_rows=0)
    affected = 0
    start = 0
    while start < int(max_rowid):
        end = start + batch_size
        before = conn.total_changes
        conn.execute(
            f"""
            UPDATE {_quote(table)}
            SET source_json=?
            WHERE rowid > ? AND rowid <= ?
            """,
            (source_json, start, end),
        )
        changed = conn.total_changes - before
        affected += changed
        conn.commit()
        if checkpoint_each_batch:
            _checkpoint(conn, truncate=True)
        start = end
    return RetentionStepSummary(step=f"sanitize_source_json:{table}", affected_rows=affected)


def _storage_state(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
    freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    return {
        "db_bytes": db_path.stat().st_size,
        "wal_bytes": Path(f"{db_path}-wal").stat().st_size if Path(f"{db_path}-wal").exists() else 0,
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "free_pages_bytes": freelist_count * page_size,
        "used_pages_bytes": (page_count - freelist_count) * page_size,
    }


def _checkpoint(conn: sqlite3.Connection, *, truncate: bool = False) -> None:
    mode = "TRUNCATE" if truncate else "PASSIVE"
    conn.execute(f"PRAGMA wal_checkpoint({mode})")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall()]


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
