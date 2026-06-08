from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.config import CENTRAL_DB_PATH, CENTRAL_POSTGRES_URL
from crypto_options_app.db.postgres import (
    CryptoOptionsPostgresSettings,
    initialize_postgres_schema,
    postgres_driver_status,
)


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class PostgresBulkCopyConfig:
    sqlite_path: Path = CENTRAL_DB_PATH
    database_url: str = CENTRAL_POSTGRES_URL
    tables: tuple[str, ...] = ()
    batch_size: int = 1000
    limit_per_table: int | None = None
    truncate: bool = False
    upsert_update: bool = False
    dry_run: bool = False
    bootstrap_schema: bool = False
    sqlite_attempts: int = 4
    sqlite_retry_delay_seconds: float = 1.0


def copy_sqlite_tables_to_postgres(config: PostgresBulkCopyConfig | None = None) -> dict[str, Any]:
    config = config or PostgresBulkCopyConfig()
    started_at = datetime.now(UTC).isoformat()
    source_path = Path(config.sqlite_path)
    if not source_path.exists():
        return {
            "schema_version": "crypto_options_sqlite_to_postgres_copy_v1",
            "status": "blocked",
            "blockers": [f"sqlite_source_missing:{source_path}"],
            "started_at_utc": started_at,
            "completed_at_utc": datetime.now(UTC).isoformat(),
            "tables": [],
        }

    source_conn = _connect_sqlite_with_retry(
        source_path,
        attempts=config.sqlite_attempts,
        delay_seconds=config.sqlite_retry_delay_seconds,
    )
    try:
        available_tables = _sqlite_table_names(source_conn)
        selected_tables = _selected_tables(config.tables, available_tables)
        for table_name in selected_tables:
            _validate_identifier(table_name)
        if config.dry_run:
            table_results = [
                _dry_run_table(source_conn, table_name, limit_per_table=config.limit_per_table)
                for table_name in selected_tables
            ]
            return _copy_result(
                status="dry_run",
                started_at=started_at,
                table_results=table_results,
                database_url=config.database_url,
                dry_run=True,
            )

        driver = postgres_driver_status()
        if not driver["psycopg2_available"]:
            return _copy_result(
                status="blocked",
                started_at=started_at,
                table_results=[],
                database_url=config.database_url,
                dry_run=False,
                blockers=["psycopg2_unavailable"],
            )

        import psycopg2

        settings = CryptoOptionsPostgresSettings.from_url(config.database_url)
        if config.bootstrap_schema:
            initialize_postgres_schema(settings)
        with psycopg2.connect(**settings.as_psycopg2_kwargs()) as pg_conn:
            table_results = [
                _copy_table(
                    source_conn,
                    pg_conn,
                    table_name,
                    batch_size=max(1, int(config.batch_size)),
                    limit_per_table=config.limit_per_table,
                    truncate=config.truncate,
                    upsert_update=config.upsert_update,
                )
                for table_name in selected_tables
            ]
        status = "ok" if all(result["status"] == "ok" for result in table_results) else "degraded"
        return _copy_result(
            status=status,
            started_at=started_at,
            table_results=table_results,
            database_url=config.database_url,
            dry_run=False,
        )
    except Exception as exc:  # noqa: BLE001 - copy command should report structured failures.
        return _copy_result(
            status="blocked",
            started_at=started_at,
            table_results=[],
            database_url=config.database_url,
            dry_run=config.dry_run,
            blockers=[f"{type(exc).__name__}:{exc}"],
        )
    finally:
        source_conn.close()


def _copy_result(
    *,
    status: str,
    started_at: str,
    table_results: list[dict[str, Any]],
    database_url: str,
    dry_run: bool,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "crypto_options_sqlite_to_postgres_copy_v1",
        "status": status,
        "started_at_utc": started_at,
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "dry_run": dry_run,
        "target": _safe_target(database_url),
        "table_count": len(table_results),
        "source_rows_seen": sum(int(row.get("source_rows_seen") or 0) for row in table_results),
        "inserted_rows": sum(int(row.get("inserted_rows") or 0) for row in table_results),
        "blockers": blockers or [blocker for row in table_results for blocker in row.get("blockers", [])],
        "tables": table_results,
    }


def _connect_sqlite_with_retry(path: Path, *, attempts: int, delay_seconds: float) -> sqlite3.Connection:
    last_error: sqlite3.OperationalError | None = None
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    for attempt in range(max(1, int(attempts))):
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 10000")
            return conn
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            last_error = exc
            time.sleep(max(0.1, float(delay_seconds)))
    raise last_error or sqlite3.OperationalError("sqlite_open_failed")


def _sqlite_table_names(conn: sqlite3.Connection) -> tuple[str, ...]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return tuple(str(row["name"]) for row in rows)


def _selected_tables(requested: tuple[str, ...], available: tuple[str, ...]) -> tuple[str, ...]:
    if not requested:
        return available
    missing = sorted(set(requested) - set(available))
    if missing:
        raise ValueError(f"requested SQLite tables do not exist: {missing}")
    return tuple(requested)


def _dry_run_table(
    conn: sqlite3.Connection,
    table_name: str,
    *,
    limit_per_table: int | None,
) -> dict[str, Any]:
    source_count = _sqlite_count(conn, table_name)
    source_rows_seen = _limited_count(source_count, limit_per_table)
    return {
        "table": table_name,
        "status": "dry_run",
        "source_count": source_count,
        "source_rows_seen": source_rows_seen,
        "inserted_rows": 0,
        "column_count": len(_sqlite_columns(conn, table_name)),
        "blockers": [],
    }


def _copy_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn: Any,
    table_name: str,
    *,
    batch_size: int,
    limit_per_table: int | None,
    truncate: bool,
    upsert_update: bool,
) -> dict[str, Any]:
    import psycopg2.extras
    from psycopg2 import sql

    sqlite_columns = _sqlite_columns(sqlite_conn, table_name)
    pg_columns = _postgres_columns(pg_conn, table_name)
    if not pg_columns:
        return {
            "table": table_name,
            "status": "blocked",
            "source_count": _sqlite_count(sqlite_conn, table_name),
            "source_rows_seen": 0,
            "inserted_rows": 0,
            "column_count": 0,
            "blockers": ["target_table_missing"],
        }
    columns = tuple(column for column in sqlite_columns if column in pg_columns)
    if not columns:
        return {
            "table": table_name,
            "status": "blocked",
            "source_count": _sqlite_count(sqlite_conn, table_name),
            "source_rows_seen": 0,
            "inserted_rows": 0,
            "column_count": 0,
            "blockers": ["no_shared_columns"],
        }

    with pg_conn.cursor() as cur:
        if truncate:
            cur.execute(sql.SQL("TRUNCATE TABLE {}").format(sql.Identifier(table_name)))
        before_count = _postgres_count(cur, table_name)
        source_count = _sqlite_count(sqlite_conn, table_name)
        target_count = _limited_count(source_count, limit_per_table)
        offset = 0
        rows_seen = 0
        primary_key_columns = _postgres_primary_key_columns(pg_conn, table_name)
        insert_sql = _postgres_insert_sql(
            table_name,
            columns,
            primary_key_columns=primary_key_columns,
            upsert_update=upsert_update,
        )
        while rows_seen < target_count:
            current_limit = min(batch_size, target_count - rows_seen)
            batch = _sqlite_rows(sqlite_conn, table_name, columns, limit=current_limit, offset=offset)
            if not batch:
                break
            psycopg2.extras.execute_values(
                cur,
                insert_sql.as_string(pg_conn),
                batch,
                page_size=batch_size,
            )
            rows_seen += len(batch)
            offset += len(batch)
        after_count = _postgres_count(cur, table_name)

    return {
        "table": table_name,
        "status": "ok",
        "source_count": source_count,
        "source_rows_seen": rows_seen,
        "copied_rows": rows_seen,
        "inserted_rows": max(0, after_count - before_count),
        "target_count_before": before_count,
        "target_count_after": after_count,
        "column_count": len(columns),
        "upsert_update": upsert_update,
        "primary_key_columns": primary_key_columns,
        "blockers": [],
    }


def _sqlite_columns(conn: sqlite3.Connection, table_name: str) -> tuple[str, ...]:
    _validate_identifier(table_name)
    rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return tuple(str(row["name"]) for row in rows)


def _sqlite_count(conn: sqlite3.Connection, table_name: str) -> int:
    _validate_identifier(table_name)
    row = conn.execute(f'SELECT COUNT(*) AS c FROM "{table_name}"').fetchone()
    return int(row["c"])


def _sqlite_rows(
    conn: sqlite3.Connection,
    table_name: str,
    columns: tuple[str, ...],
    *,
    limit: int,
    offset: int,
) -> list[tuple[Any, ...]]:
    _validate_identifier(table_name)
    for column in columns:
        _validate_identifier(column)
    column_sql = ", ".join(f'"{column}"' for column in columns)
    rows = conn.execute(
        f'SELECT {column_sql} FROM "{table_name}" LIMIT ? OFFSET ?',
        (int(limit), int(offset)),
    ).fetchall()
    return [tuple(row[column] for column in columns) for row in rows]


def _postgres_columns(pg_conn: Any, table_name: str) -> tuple[str, ...]:
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = %s
             ORDER BY ordinal_position
            """,
            (table_name,),
        )
        return tuple(str(row[0]) for row in cur.fetchall())


def _postgres_primary_key_columns(pg_conn: Any, table_name: str) -> tuple[str, ...]:
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT kcu.column_name
              FROM information_schema.table_constraints AS tc
              JOIN information_schema.key_column_usage AS kcu
                ON tc.constraint_name = kcu.constraint_name
               AND tc.table_schema = kcu.table_schema
             WHERE tc.table_schema = 'public'
               AND tc.table_name = %s
               AND tc.constraint_type = 'PRIMARY KEY'
             ORDER BY kcu.ordinal_position
            """,
            (table_name,),
        )
        return tuple(str(row[0]) for row in cur.fetchall())


def _postgres_insert_sql(
    table_name: str,
    columns: tuple[str, ...],
    *,
    primary_key_columns: tuple[str, ...],
    upsert_update: bool,
) -> Any:
    from psycopg2 import sql

    column_sql = sql.SQL(", ").join(sql.Identifier(column) for column in columns)
    if upsert_update and primary_key_columns and set(primary_key_columns).issubset(set(columns)):
        update_columns = _upsert_update_columns(columns, primary_key_columns)
        if update_columns:
            conflict_sql = sql.SQL(", ").join(sql.Identifier(column) for column in primary_key_columns)
            assignments = sql.SQL(", ").join(
                sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(column), sql.Identifier(column))
                for column in update_columns
            )
            return sql.SQL("INSERT INTO {} ({}) VALUES %s ON CONFLICT ({}) DO UPDATE SET {}").format(
                sql.Identifier(table_name),
                column_sql,
                conflict_sql,
                assignments,
            )
    return sql.SQL("INSERT INTO {} ({}) VALUES %s ON CONFLICT DO NOTHING").format(
        sql.Identifier(table_name),
        column_sql,
    )


def _upsert_update_columns(
    columns: tuple[str, ...],
    primary_key_columns: tuple[str, ...],
) -> tuple[str, ...]:
    primary_key_set = set(primary_key_columns)
    return tuple(column for column in columns if column not in primary_key_set)


def _postgres_count(cur: Any, table_name: str) -> int:
    from psycopg2 import sql

    cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table_name)))
    return int(cur.fetchone()[0])


def _limited_count(source_count: int, limit: int | None) -> int:
    if limit is None:
        return int(source_count)
    return min(int(source_count), max(0, int(limit)))


def _validate_identifier(identifier: str) -> None:
    if not _IDENTIFIER_PATTERN.match(identifier):
        raise ValueError(f"unsafe SQL identifier: {identifier!r}")


def _safe_target(database_url: str) -> dict[str, Any]:
    settings = CryptoOptionsPostgresSettings.from_url(database_url)
    return {
        "host": settings.host,
        "port": settings.port,
        "database": settings.database,
        "user": settings.user,
    }
