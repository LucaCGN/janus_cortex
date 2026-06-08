from __future__ import annotations

import sqlite3
from pathlib import Path

from crypto_options_app.config import DEFAULT_CONFIG
from crypto_options_app.db.postgres_connection import (
    PostgresCompatConnection,
    should_use_postgres_runtime,
)


def default_db_path() -> Path:
    return DEFAULT_CONFIG.db_path


def connect(db_path: str | Path | None = None):
    if should_use_postgres_runtime(db_path):
        return PostgresCompatConnection()
    path = Path(db_path) if db_path is not None else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    _configure_write_connection(conn)
    return conn


def connect_read_only(db_path: str | Path):
    if should_use_postgres_runtime(db_path):
        return PostgresCompatConnection(readonly=True)
    path = Path(db_path).resolve()
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    return connect_read_only(db_path)


def _configure_write_connection(conn: sqlite3.Connection) -> None:
    """Prefer WAL for concurrent service writes and report reads.

    SQLite remains the local fallback until Postgres cutover is complete. WAL
    materially reduces read/write contention for the app's data services, but
    it can fail on special filesystems or already read-only handles; connection
    setup should still succeed in those cases.
    """

    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.OperationalError:
        return


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    if getattr(conn, "is_postgres", False):
        row = conn.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = %s
            LIMIT 1
            """,
            (table_name,),
        ).fetchone()
        return row is not None
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def count_rows(conn: sqlite3.Connection, table_name: str) -> int:
    if not table_exists(conn, table_name):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) AS c FROM {table_name}").fetchone()["c"])
