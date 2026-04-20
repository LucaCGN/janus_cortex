from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from psycopg2.extensions import connection as PsycopgConnection

from app.data.databases.postgres import ensure_database_exists, managed_connection
from app.data.databases.safety import describe_database_target, require_safe_db_test_target


MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
MIGRATION_ID_PATTERN = re.compile(r"^\d+_v\d+_\d+_\d+__.+\.sql$")
PHASE_PATTERN = re.compile(r"^\d+_(v\d+_\d+_\d+)__.+\.sql$")
MANAGED_SCHEMAS = (
    "core",
    "catalog",
    "market_data",
    "portfolio",
    "strategy",
    "nba",
    "research",
    "ops",
)


@dataclass(frozen=True)
class Migration:
    migration_id: str
    phase: str
    path: Path
    checksum: str


def _phase_from_filename(filename: str) -> str:
    match = PHASE_PATTERN.match(filename)
    if not match:
        return "unknown"
    return match.group(1).replace("_", ".")


def discover_migrations() -> list[Migration]:
    if not MIGRATIONS_DIR.exists():
        raise FileNotFoundError(f"Missing migrations directory: {MIGRATIONS_DIR}")

    migrations: list[Migration] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if not MIGRATION_ID_PATTERN.match(path.name):
            raise ValueError(
                f"Invalid migration filename '{path.name}'. "
                "Expected '<NNNN>_vX_Y_Z__description.sql'."
            )
        content = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
        migrations.append(
            Migration(
                migration_id=path.name,
                phase=_phase_from_filename(path.name),
                path=path,
                checksum=checksum,
            )
        )
    return migrations


def ensure_version_table(connection: PsycopgConnection) -> None:
    with connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA IF NOT EXISTS core;")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS core.schema_migrations (
                migration_id TEXT PRIMARY KEY,
                phase TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
    connection.commit()


def get_applied_migrations(connection: PsycopgConnection) -> dict[str, str]:
    ensure_version_table(connection)
    with connection.cursor() as cursor:
        cursor.execute("SELECT migration_id, checksum FROM core.schema_migrations;")
        rows = cursor.fetchall()
    return {migration_id: checksum for migration_id, checksum in rows}


def apply_migrations(connection: PsycopgConnection, *, target: str | None = None) -> list[str]:
    migrations = discover_migrations()
    applied_map = get_applied_migrations(connection)
    applied_now: list[str] = []

    for migration in migrations:
        if target and migration.migration_id > target:
            break

        existing_checksum = applied_map.get(migration.migration_id)
        if existing_checksum:
            if existing_checksum != migration.checksum:
                raise ValueError(
                    f"Checksum mismatch for previously applied migration "
                    f"{migration.migration_id}. Applied={existing_checksum}, "
                    f"Current={migration.checksum}"
                )
            continue

        sql_text = migration.path.read_text(encoding="utf-8")
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql_text)
                cursor.execute(
                    """
                    INSERT INTO core.schema_migrations (migration_id, phase, checksum)
                    VALUES (%s, %s, %s);
                    """,
                    (migration.migration_id, migration.phase, migration.checksum),
                )
            connection.commit()
            applied_now.append(migration.migration_id)
        except Exception as exc:
            connection.rollback()
            raise RuntimeError(f"Failed applying migration: {migration.migration_id}") from exc

    return applied_now


def list_migrations() -> list[str]:
    return [migration.migration_id for migration in discover_migrations()]


def drop_managed_schemas(connection: PsycopgConnection) -> None:
    with connection.cursor() as cursor:
        for schema_name in reversed(MANAGED_SCHEMAS):
            cursor.execute(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE;")
    connection.commit()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply Postgres migrations for Janus Cortex.")
    parser.add_argument(
        "--to",
        dest="target",
        default=None,
        help="Apply migrations up to and including this migration id.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List discovered migrations and exit.",
    )
    parser.add_argument(
        "--describe-target",
        action="store_true",
        help="Print the currently selected database target and exit.",
    )
    parser.add_argument(
        "--drop-managed-schemas",
        action="store_true",
        help="Drop every managed Janus schema before applying migrations.",
    )
    parser.add_argument(
        "--require-safe-target",
        action="store_true",
        help="Require JANUS_DB_TARGET to be disposable or dev_clone before continuing.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.list:
        for migration_id in list_migrations():
            print(migration_id)
        return 0
    if args.describe_target:
        print(json.dumps(describe_database_target(), indent=2, sort_keys=True))
        return 0

    if args.require_safe_target or args.drop_managed_schemas:
        require_safe_db_test_target("migration command")

    ensure_database_exists()
    with managed_connection() as connection:
        if args.drop_managed_schemas:
            drop_managed_schemas(connection)
        applied_now = apply_migrations(connection, target=args.target)

    if applied_now:
        print("Applied migrations:")
        for migration_id in applied_now:
            print(f"- {migration_id}")
    else:
        print("No pending migrations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
