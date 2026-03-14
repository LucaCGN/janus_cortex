from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from psycopg2.extras import RealDictCursor

from app.data.databases.postgres import managed_connection


TIMESTAMP_CANDIDATES = (
    "updated_at",
    "captured_at",
    "generated_at",
    "trade_time",
    "event_time",
    "placed_at",
    "fetched_at",
    "scored_at",
    "started_at",
    "created_at",
    "open_time",
    "ts",
)


@dataclass
class TableAuditRow:
    schema_name: str
    table_name: str
    row_count: int
    freshness_column: str | None
    last_value: str | None


def _iter_managed_tables(connection: object) -> Iterable[tuple[str, str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema IN ('core', 'catalog', 'market_data', 'portfolio', 'nba', 'ops')
              AND table_type = 'BASE TABLE'
            ORDER BY table_schema, table_name;
            """
        )
        for schema_name, table_name in cursor.fetchall():
            yield str(schema_name), str(table_name)


def _resolve_freshness_column(connection: object, *, schema_name: str, table_name: str) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s;
            """,
            (schema_name, table_name),
        )
        columns = {str(row[0]) for row in cursor.fetchall()}
    for candidate in TIMESTAMP_CANDIDATES:
        if candidate in columns:
            return candidate
    return None


def audit_db_state() -> list[TableAuditRow]:
    rows: list[TableAuditRow] = []
    with managed_connection() as connection:
        for schema_name, table_name in _iter_managed_tables(connection):
            freshness_column = _resolve_freshness_column(
                connection,
                schema_name=schema_name,
                table_name=table_name,
            )
            select_sql = f"SELECT count(*) AS n FROM {schema_name}.{table_name}"
            if freshness_column:
                select_sql = (
                    f"SELECT count(*) AS n, max({freshness_column})::text AS last_value "
                    f"FROM {schema_name}.{table_name}"
                )
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(select_sql)
                record = dict(cursor.fetchone() or {})
            rows.append(
                TableAuditRow(
                    schema_name=schema_name,
                    table_name=table_name,
                    row_count=int(record.get("n") or 0),
                    freshness_column=freshness_column,
                    last_value=str(record.get("last_value")) if record.get("last_value") is not None else None,
                )
            )
    return rows


def main() -> int:
    for row in audit_db_state():
        freshness = f"{row.freshness_column}={row.last_value}" if row.freshness_column else "freshness=n/a"
        print(f"{row.schema_name}.{row.table_name}\trows={row.row_count}\t{freshness}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
