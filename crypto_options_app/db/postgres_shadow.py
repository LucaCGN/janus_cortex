from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.config import CENTRAL_ARTIFACT_ROOT, CENTRAL_DB_PATH, CENTRAL_POSTGRES_URL
from crypto_options_app.db.postgres import CryptoOptionsPostgresSettings, postgres_driver_status
from crypto_options_app.db.postgres_import import _validate_identifier


DEFAULT_SHADOW_PARITY_TABLES: tuple[str, ...] = (
    "app_settings",
    "data_service_watermarks",
    "signal_specs",
    "signal_versions",
    "signal_queue_items",
    "signal_validation_runs",
    "signal_validation_results",
    "strategy_specs",
    "strategy_versions",
    "strategy_promotion_state",
    "strategy_validation_runs",
    "polymarket_event_path_stats",
    "external_technical_observer_snapshots",
    "profile_distribution_snapshots",
)

LATEST_TIMESTAMP_COLUMNS: dict[str, tuple[str, ...]] = {
    "app_settings": ("updated_at_utc",),
    "data_service_watermarks": ("updated_at_utc", "last_run_at_utc"),
    "signal_specs": ("updated_at_utc", "created_at_utc", "inserted_at_utc"),
    "signal_versions": ("updated_at_utc", "created_at_utc", "inserted_at_utc"),
    "signal_queue_items": ("updated_at_utc", "owned_at_utc", "created_at_utc", "inserted_at_utc"),
    "signal_validation_runs": ("completed_at_utc", "started_at_utc", "created_at_utc", "inserted_at_utc"),
    "signal_validation_results": ("computed_at_utc", "created_at_utc", "inserted_at_utc"),
    "strategy_specs": ("updated_at_utc", "created_at_utc", "inserted_at_utc"),
    "strategy_versions": ("updated_at_utc", "created_at_utc", "inserted_at_utc"),
    "strategy_promotion_state": ("updated_at_utc", "promoted_at_utc", "created_at_utc", "inserted_at_utc"),
    "strategy_validation_runs": ("completed_at_utc", "started_at_utc", "created_at_utc", "inserted_at_utc"),
    "polymarket_event_path_stats": ("computed_at_utc", "updated_at_utc", "inserted_at_utc"),
    "external_technical_observer_snapshots": ("completed_at_utc", "started_at_utc", "inserted_at_utc"),
    "profile_distribution_snapshots": ("computed_at_utc", "inserted_at_utc"),
}

LIVE_TABLE_LAG_TOLERANCE: dict[str, dict[str, float]] = {
    # These tables are written by 30-second services while the hot-copy and parity
    # commands are running. Exact parity is still reported, but bounded lag should
    # not block read-only API fallback when the registry/validation tables match.
    "app_settings": {"max_row_delta": 0.0, "max_latest_lag_seconds": 300.0},
    "data_service_watermarks": {"max_row_delta": 0.0, "max_latest_lag_seconds": 300.0},
    "polymarket_event_path_stats": {"max_row_delta": 10.0, "max_latest_lag_seconds": 180.0},
    "external_technical_observer_snapshots": {"max_row_delta": 500.0, "max_latest_lag_seconds": 180.0},
    "profile_distribution_snapshots": {"max_row_delta": 200.0, "max_latest_lag_seconds": 180.0},
}


@dataclass(frozen=True)
class PostgresShadowParityConfig:
    sqlite_path: Path = CENTRAL_DB_PATH
    database_url: str = CENTRAL_POSTGRES_URL
    artifact_root: Path = CENTRAL_ARTIFACT_ROOT
    tables: tuple[str, ...] = DEFAULT_SHADOW_PARITY_TABLES
    sqlite_attempts: int = 4
    sqlite_retry_delay_seconds: float = 1.0


def build_postgres_shadow_parity_report(
    config: PostgresShadowParityConfig | None = None,
) -> dict[str, Any]:
    config = config or PostgresShadowParityConfig()
    started_at = datetime.now(UTC).isoformat()
    source_path = Path(config.sqlite_path)
    if not source_path.exists():
        return _parity_result(
            status="blocked",
            started_at=started_at,
            table_results=[],
            blockers=[f"sqlite_source_missing:{source_path}"],
            database_url=config.database_url,
        )

    driver = postgres_driver_status()
    if not driver["psycopg2_available"]:
        return _parity_result(
            status="blocked",
            started_at=started_at,
            table_results=[],
            blockers=["psycopg2_unavailable"],
            database_url=config.database_url,
        )

    try:
        sqlite_conn = _connect_sqlite_with_retry(
            source_path,
            attempts=config.sqlite_attempts,
            delay_seconds=config.sqlite_retry_delay_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - parity command should report structured failures.
        return _parity_result(
            status="blocked",
            started_at=started_at,
            table_results=[],
            blockers=[f"sqlite_open_error:{type(exc).__name__}:{exc}"],
            database_url=config.database_url,
        )

    try:
        import psycopg2

        settings = CryptoOptionsPostgresSettings.from_url(config.database_url)
        with psycopg2.connect(**settings.as_psycopg2_kwargs()) as pg_conn:
            table_results = [
                _table_parity(sqlite_conn, pg_conn, table_name)
                for table_name in config.tables
            ]
    except Exception as exc:  # noqa: BLE001 - parity command should report structured failures.
        return _parity_result(
            status="blocked",
            started_at=started_at,
            table_results=[],
            blockers=[f"postgres_parity_error:{type(exc).__name__}:{exc}"],
            database_url=config.database_url,
        )
    finally:
        sqlite_conn.close()

    blockers = [blocker for result in table_results for blocker in result.get("blockers", [])]
    if blockers:
        status = "degraded"
    elif all(bool(result.get("parity_acceptable")) for result in table_results):
        status = "ok"
    else:
        status = "degraded"
    return _parity_result(
        status=status,
        started_at=started_at,
        table_results=table_results,
        blockers=blockers,
        database_url=config.database_url,
    )


def write_postgres_shadow_parity_report(
    report: dict[str, Any],
    *,
    artifact_root: Path = CENTRAL_ARTIFACT_ROOT,
) -> Path:
    output_path = Path(artifact_root) / "reports" / "postgres_shadow_parity_latest.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, sort_keys=True, default=str) + "\n"
    temp_path = output_path.with_name(f"{output_path.name}.{time.time_ns()}.tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(output_path)
    return output_path


def load_postgres_shadow_parity_report(
    *,
    artifact_root: Path = CENTRAL_ARTIFACT_ROOT,
) -> dict[str, Any] | None:
    path = Path(artifact_root) / "reports" / "postgres_shadow_parity_latest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    payload["artifact_path"] = str(path)
    generated_at = _parse_utc(payload.get("completed_at_utc") or payload.get("generated_at_utc"))
    if generated_at is not None:
        payload["artifact_age_seconds"] = max(0.0, (datetime.now(UTC) - generated_at).total_seconds())
    return payload


def _table_parity(sqlite_conn: sqlite3.Connection, pg_conn: Any, table_name: str) -> dict[str, Any]:
    _validate_identifier(table_name)
    blockers: list[str] = []
    sqlite_exists = _sqlite_table_exists(sqlite_conn, table_name)
    postgres_exists = _postgres_table_exists(pg_conn, table_name)
    if not sqlite_exists:
        blockers.append("sqlite_table_missing")
    if not postgres_exists:
        blockers.append("postgres_table_missing")
    if blockers:
        return {
            "table": table_name,
            "status": "blocked",
            "sqlite_exists": sqlite_exists,
            "postgres_exists": postgres_exists,
            "source_count": None,
            "target_count": None,
            "row_count_match": False,
            "latest_timestamp_column": None,
            "source_latest_timestamp": None,
            "target_latest_timestamp": None,
            "latest_timestamp_match": False,
            "blockers": blockers,
        }

    source_columns = _sqlite_columns(sqlite_conn, table_name)
    target_columns = _postgres_columns(pg_conn, table_name)
    latest_column = _latest_column_for_table(table_name, source_columns, target_columns)
    source_count = _sqlite_count(sqlite_conn, table_name)
    target_count = _postgres_count(pg_conn, table_name)
    source_latest = _sqlite_latest(sqlite_conn, table_name, latest_column) if latest_column else None
    target_latest = _postgres_latest(pg_conn, table_name, latest_column) if latest_column else None
    row_count_delta = source_count - target_count
    row_count_match = row_count_delta == 0
    latest_timestamp_match = source_latest == target_latest if latest_column else True
    latest_lag_seconds = _timestamp_lag_seconds(source_latest, target_latest) if latest_column else 0.0
    row_count_within_tolerance, latest_timestamp_within_tolerance = _within_live_lag_tolerance(
        table_name,
        row_count_delta=row_count_delta,
        latest_lag_seconds=latest_lag_seconds,
        latest_timestamp_match=latest_timestamp_match,
    )
    parity_acceptable = row_count_within_tolerance and latest_timestamp_within_tolerance
    if not row_count_within_tolerance:
        blockers.append("row_count_mismatch")
    if not latest_timestamp_within_tolerance:
        blockers.append("latest_timestamp_mismatch")
    return {
        "table": table_name,
        "status": "ok" if not blockers else "degraded",
        "sqlite_exists": True,
        "postgres_exists": True,
        "source_count": source_count,
        "target_count": target_count,
        "row_count_delta": row_count_delta,
        "row_count_match": row_count_match,
        "row_count_within_tolerance": row_count_within_tolerance,
        "latest_timestamp_column": latest_column,
        "source_latest_timestamp": source_latest,
        "target_latest_timestamp": target_latest,
        "latest_timestamp_match": latest_timestamp_match,
        "latest_timestamp_within_tolerance": latest_timestamp_within_tolerance,
        "latest_lag_seconds": latest_lag_seconds,
        "parity_acceptable": parity_acceptable,
        "blockers": blockers,
    }


def _parity_result(
    *,
    status: str,
    started_at: str,
    table_results: list[dict[str, Any]],
    blockers: list[str],
    database_url: str,
) -> dict[str, Any]:
    settings = CryptoOptionsPostgresSettings.from_url(database_url)
    return {
        "schema_version": "crypto_options_postgres_shadow_parity_v1",
        "status": status,
        "started_at_utc": started_at,
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "target": {
            "host": settings.host,
            "port": settings.port,
            "database": settings.database,
            "user": settings.user,
        },
        "table_count": len(table_results),
        "matched_table_count": sum(
            1
            for result in table_results
            if result.get("row_count_match") and result.get("latest_timestamp_match")
        ),
        "acceptable_table_count": sum(1 for result in table_results if bool(result.get("parity_acceptable"))),
        "blockers": sorted(set(str(blocker) for blocker in blockers)),
        "runtime_read_cutover_allowed": status == "ok" and bool(table_results),
        "tables": table_results,
    }


def _connect_sqlite_with_retry(path: Path, *, attempts: int, delay_seconds: float) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    last_error: sqlite3.OperationalError | None = None
    for attempt in range(max(1, int(attempts))):
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 10000")
            return conn
        except sqlite3.OperationalError as exc:
            last_error = exc
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            if attempt == max(1, int(attempts)) - 1:
                raise
            time.sleep(max(0.1, float(delay_seconds)))
    raise last_error or sqlite3.OperationalError("sqlite_open_failed")


def _sqlite_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _postgres_table_exists(pg_conn: Any, table_name: str) -> bool:
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE table_schema = 'public'
               AND table_name = %s
             LIMIT 1
            """,
            (table_name,),
        )
        return cur.fetchone() is not None


def _sqlite_columns(conn: sqlite3.Connection, table_name: str) -> tuple[str, ...]:
    rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return tuple(str(row["name"]) for row in rows)


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


def _latest_column_for_table(table_name: str, source_columns: tuple[str, ...], target_columns: tuple[str, ...]) -> str | None:
    source_set = set(source_columns)
    target_set = set(target_columns)
    for column in LATEST_TIMESTAMP_COLUMNS.get(table_name, ()):
        if column in source_set and column in target_set:
            return column
    for column in ("updated_at_utc", "computed_at_utc", "completed_at_utc", "inserted_at_utc", "created_at_utc"):
        if column in source_set and column in target_set:
            return column
    return None


def _sqlite_count(conn: sqlite3.Connection, table_name: str) -> int:
    row = conn.execute(f'SELECT COUNT(*) AS c FROM "{table_name}"').fetchone()
    return int(row["c"])


def _postgres_count(pg_conn: Any, table_name: str) -> int:
    from psycopg2 import sql

    with pg_conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table_name)))
        return int(cur.fetchone()[0])


def _sqlite_latest(conn: sqlite3.Connection, table_name: str, column: str) -> str | None:
    row = conn.execute(f'SELECT MAX("{column}") AS latest FROM "{table_name}"').fetchone()
    return _normalize_timestamp(row["latest"] if row else None)


def _postgres_latest(pg_conn: Any, table_name: str, column: str) -> str | None:
    from psycopg2 import sql

    with pg_conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT MAX({}) FROM {}").format(sql.Identifier(column), sql.Identifier(table_name)))
        row = cur.fetchone()
    return _normalize_timestamp(row[0] if row else None)


def _normalize_timestamp(value: Any) -> str | None:
    parsed = _parse_utc(value)
    if parsed is None:
        return None if value in {None, ""} else str(value)
    return parsed.isoformat()


def _timestamp_lag_seconds(source_latest: str | None, target_latest: str | None) -> float | None:
    source_dt = _parse_utc(source_latest)
    target_dt = _parse_utc(target_latest)
    if source_dt is None or target_dt is None:
        return None
    return max(0.0, (source_dt - target_dt).total_seconds())


def _within_live_lag_tolerance(
    table_name: str,
    *,
    row_count_delta: int,
    latest_lag_seconds: float | None,
    latest_timestamp_match: bool,
) -> tuple[bool, bool]:
    if row_count_delta == 0 and latest_timestamp_match:
        return True, True
    tolerance = LIVE_TABLE_LAG_TOLERANCE.get(table_name)
    if tolerance is None:
        return row_count_delta == 0, latest_timestamp_match

    max_row_delta = int(tolerance.get("max_row_delta", 0.0))
    max_latest_lag_seconds = float(tolerance.get("max_latest_lag_seconds", 0.0))
    row_ok = 0 <= row_count_delta <= max_row_delta
    latest_ok = latest_timestamp_match or (
        latest_lag_seconds is not None
        and 0.0 <= latest_lag_seconds <= max_latest_lag_seconds
    )
    return row_ok, latest_ok


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
