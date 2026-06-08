from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from crypto_options_app.config import (
    CENTRAL_POSTGRES_DB,
    CENTRAL_POSTGRES_HOST,
    CENTRAL_POSTGRES_PASSWORD,
    CENTRAL_POSTGRES_PORT,
    CENTRAL_POSTGRES_URL,
    CENTRAL_POSTGRES_USER,
)
from crypto_options_app.db.schema import EXPECTED_TABLES, EVENT_PATH_STATS_EXTRA_COLUMNS, SCHEMA_VERSION, _DDL


@dataclass(frozen=True)
class CryptoOptionsPostgresSettings:
    host: str = CENTRAL_POSTGRES_HOST
    port: int = CENTRAL_POSTGRES_PORT
    database: str = CENTRAL_POSTGRES_DB
    user: str = CENTRAL_POSTGRES_USER
    password: str = CENTRAL_POSTGRES_PASSWORD
    connect_timeout: int = 10

    @classmethod
    def from_url(cls, database_url: str = CENTRAL_POSTGRES_URL) -> "CryptoOptionsPostgresSettings":
        parsed = urlparse(database_url)
        if parsed.scheme not in {"postgresql", "postgres"}:
            raise ValueError("Postgres URL must use postgresql:// or postgres://")
        if not parsed.hostname or not parsed.path.strip("/"):
            raise ValueError("Postgres URL must include host and database")
        return cls(
            host=parsed.hostname,
            port=int(parsed.port or 5432),
            database=parsed.path.strip("/"),
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
        )

    def as_database_url(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"

    def as_psycopg2_kwargs(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": self.user,
            "password": self.password,
            "connect_timeout": self.connect_timeout,
        }


def postgres_driver_status() -> dict[str, Any]:
    return {
        "psycopg2_available": importlib.util.find_spec("psycopg2") is not None,
        "psycopg3_available": importlib.util.find_spec("psycopg") is not None,
        "preferred_driver": "psycopg2",
    }


def render_postgres_schema_sql(*, strip_foreign_keys: bool = True) -> str:
    """Render the current canonical SQLite DDL into Postgres-compatible bootstrap SQL."""

    ddl = _DDL
    ddl = re.sub(
        r"CREATE\s+VIEW\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)\s+AS",
        r"CREATE OR REPLACE VIEW \1 AS",
        ddl,
        flags=re.IGNORECASE,
    )
    ddl = ddl.replace("datetime('now')", "CURRENT_TIMESTAMP")
    if strip_foreign_keys:
        ddl = "\n".join(
            line
            for line in ddl.splitlines()
            if not line.strip().upper().startswith("FOREIGN KEY(")
        )
        ddl = re.sub(r",\s*\n\)", "\n)", ddl)
    return ddl


def initialize_postgres_schema(
    settings: CryptoOptionsPostgresSettings | None = None,
    *,
    schema_sql: str | None = None,
) -> dict[str, Any]:
    """Initialize the crypto-options schema in an already-running Postgres database."""

    driver = postgres_driver_status()
    if not driver["psycopg2_available"]:
        raise RuntimeError("psycopg2 is required for Postgres bootstrap")

    import psycopg2

    resolved = settings or CryptoOptionsPostgresSettings()
    started_at = datetime.now(UTC).isoformat()
    with psycopg2.connect(**resolved.as_psycopg2_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '10s'")
            cur.execute("SET LOCAL statement_timeout = '120s'")
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("crypto_options_app_schema_bootstrap",))
            cur.execute(schema_sql or render_postgres_schema_sql())
            for column, ddl in EVENT_PATH_STATS_EXTRA_COLUMNS.items():
                cur.execute(f"ALTER TABLE polymarket_event_path_stats ADD COLUMN IF NOT EXISTS {column} {ddl}")
            cur.execute(
                """
                INSERT INTO app_settings(setting_key, setting_value, updated_at_utc)
                VALUES(%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = EXCLUDED.setting_value,
                    updated_at_utc = EXCLUDED.updated_at_utc
                """,
                ("schema_version", SCHEMA_VERSION),
            )
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                """
            )
            tables = sorted(str(row[0]) for row in cur.fetchall())
    return {
        "status": "ok",
        "started_at_utc": started_at,
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "table_count": len(tables),
        "expected_table_count": len(EXPECTED_TABLES),
        "missing_expected_tables": sorted(EXPECTED_TABLES - set(tables)),
        "database": resolved.database,
        "host": resolved.host,
        "port": resolved.port,
    }


def check_postgres_connection(settings: CryptoOptionsPostgresSettings | None = None) -> dict[str, Any]:
    driver = postgres_driver_status()
    if not driver["psycopg2_available"]:
        return {"status": "blocked", "blockers": ["psycopg2_unavailable"], **driver}

    import psycopg2

    resolved = settings or CryptoOptionsPostgresSettings()
    try:
        with psycopg2.connect(**resolved.as_psycopg2_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version();")
                version = str(cur.fetchone()[0])
        return {
            "status": "ok",
            "database": resolved.database,
            "host": resolved.host,
            "port": resolved.port,
            "server_version": version,
            **driver,
        }
    except Exception as exc:  # pragma: no cover - depends on local Docker state
        return {
            "status": "blocked",
            "blockers": [f"{type(exc).__name__}:{exc}"],
            "database": resolved.database,
            "host": resolved.host,
            "port": resolved.port,
            **driver,
        }


def sqlite_inventory(
    sqlite_path: str | Path,
    *,
    include_counts: bool = True,
    max_count_tables: int | None = None,
) -> dict[str, Any]:
    path = Path(sqlite_path)
    if not path.exists():
        return {"status": "missing", "path": str(path), "tables": []}

    tables: list[dict[str, Any]] = []
    with sqlite3.connect(str(path), timeout=30.0) as conn:
        conn.row_factory = sqlite3.Row
        table_names = [
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        for index, table_name in enumerate(table_names):
            row_count: int | None = None
            if include_counts and (max_count_tables is None or index < max_count_tables):
                row_count = int(conn.execute(f'SELECT COUNT(*) AS c FROM "{table_name}"').fetchone()["c"])
            columns = [
                {"name": str(column["name"]), "type": str(column["type"])}
                for column in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            ]
            tables.append({"name": table_name, "row_count": row_count, "columns": columns})

    return {
        "status": "ok",
        "path": str(path),
        "table_count": len(tables),
        "expected_table_count": len(EXPECTED_TABLES),
        "missing_expected_tables": sorted(EXPECTED_TABLES - {table["name"] for table in tables}),
        "tables": tables,
    }


def build_sqlite_to_postgres_plan(
    sqlite_path: str | Path,
    *,
    database_url: str = CENTRAL_POSTGRES_URL,
    include_counts: bool = True,
) -> dict[str, Any]:
    inventory = sqlite_inventory(sqlite_path, include_counts=include_counts)
    settings = CryptoOptionsPostgresSettings.from_url(database_url)
    generated_at = datetime.now(UTC).isoformat()
    total_rows = sum(
        int(table.get("row_count") or 0)
        for table in inventory.get("tables", [])
        if table.get("row_count") is not None
    )
    largest_tables = sorted(
        [
            {"name": table["name"], "row_count": table.get("row_count")}
            for table in inventory.get("tables", [])
            if table.get("row_count") is not None
        ],
        key=lambda row: int(row.get("row_count") or 0),
        reverse=True,
    )[:12]
    return {
        "schema_version": "crypto_options_postgres_migration_plan_v1",
        "generated_at_utc": generated_at,
        "source_sqlite": inventory,
        "target_postgres": {
            "host": settings.host,
            "port": settings.port,
            "database": settings.database,
            "user": settings.user,
        },
        "total_counted_rows": total_rows,
        "largest_tables": largest_tables,
        "migration_phases": [
            "bootstrap_postgres_schema",
            "copy_low_volume_state_tables_with_bounded_importer",
            "copy_append_only_data_in_controlled_groups",
            "copy_strategy_signal_state",
            "run_row_count_and_latest_timestamp_parity",
            "run_read_api_against_postgres_shadow",
            "cut_over_workers_one_group_at_a_time",
        ],
        "runtime_cutover_blockers": [
            "workers_currently_use_sqlite3_connections_and_qmark_parameters",
            "some_queries_use_sqlite_julianday_or_json_extract",
            "health_dashboard_and_worker_reads_need_postgres_shadow_adapter",
            "high_volume_order_book_tables_need_scheduled_group_copy_and_parity_checks",
        ],
    }


def write_migration_plan_markdown(plan: dict[str, Any], path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    largest_lines = "\n".join(
        f"- `{row['name']}`: {row.get('row_count')}"
        for row in plan.get("largest_tables", [])
    )
    text = f"""# Crypto Options SQLite To Postgres Migration Plan

Generated: {plan['generated_at_utc']}

## Source

- SQLite: `{plan['source_sqlite']['path']}`
- Tables: {plan['source_sqlite'].get('table_count')}
- Counted rows: {plan['total_counted_rows']}

## Target

- Host: `{plan['target_postgres']['host']}`
- Port: `{plan['target_postgres']['port']}`
- Database: `{plan['target_postgres']['database']}`
- User: `{plan['target_postgres']['user']}`

## Largest Tables

{largest_lines or '- none'}

## Migration Phases

{chr(10).join(f'- {phase}' for phase in plan['migration_phases'])}

## Runtime Cutover Blockers

{chr(10).join(f'- {blocker}' for blocker in plan['runtime_cutover_blockers'])}
"""
    output_path.write_text(text, encoding="utf-8")
    return output_path


def plan_as_json(plan: dict[str, Any]) -> str:
    return json.dumps(plan, indent=2, sort_keys=True)
