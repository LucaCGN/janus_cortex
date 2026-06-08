from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from crypto_options_app.config import DEFAULT_CONFIG
from crypto_options_app.db.dialect import convert_qmark_to_format


class PostgresCompatConnection:
    """Small DB-API compatibility wrapper for runtime Postgres cutover.

    The app still contains a large number of qmark/SQLite-era queries. This
    wrapper keeps the runtime on Postgres while focused modules are converted to
    explicit dialect queries over time. SQLite remains available only for
    explicit fixture/migration paths.
    """

    is_postgres = True

    def __init__(self, *, database_url: str | None = None, readonly: bool = False) -> None:
        import psycopg2
        import psycopg2.extras

        from crypto_options_app.db.postgres import CryptoOptionsPostgresSettings

        self._settings = CryptoOptionsPostgresSettings.from_url(
            database_url or DEFAULT_CONFIG.postgres_database_url
        )
        self._conn = psycopg2.connect(
            **self._settings.as_psycopg2_kwargs(),
            cursor_factory=psycopg2.extras.DictCursor,
        )
        self._readonly = bool(readonly)
        self.total_changes = 0
        self._last_changes = 0
        if self._readonly:
            self._conn.set_session(readonly=True, autocommit=False)

    def __enter__(self) -> "PostgresCompatConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()

    def execute(self, sql: str, params: Iterable[Any] | dict[str, Any] | None = None):
        translated = translate_sqlite_runtime_sql(sql, named_params=isinstance(params, dict))
        if translated is None:
            return _MemoryCursor([])
        if translated == "__JANUS_SQLITE_CHANGES__":
            return _MemoryCursor([{"c": self._last_changes, "changes()": self._last_changes}])
        cursor = self._conn.cursor()
        cursor.execute(translated, params if isinstance(params, dict) else tuple(params or ()))
        self._last_changes = max(0, int(cursor.rowcount or 0))
        if self._last_changes > 0:
            self.total_changes += self._last_changes
        return cursor

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]):
        params_list = list(seq_of_params)
        translated = translate_sqlite_runtime_sql(
            sql,
            named_params=bool(params_list and isinstance(params_list[0], dict)),
        )
        if translated is None:
            return _MemoryCursor([])
        cursor = self._conn.cursor()
        if params_list and isinstance(params_list[0], dict):
            cursor.executemany(translated, params_list)
        else:
            cursor.executemany(translated, [tuple(params) for params in params_list])
        self._last_changes = max(0, int(cursor.rowcount or 0))
        if self._last_changes > 0:
            self.total_changes += self._last_changes
        return cursor

    def executescript(self, sql: str) -> None:
        for statement in _split_sql_script(sql):
            translated = translate_sqlite_runtime_sql(statement)
            if translated:
                with self._conn.cursor() as cursor:
                    cursor.execute(translated)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


class _MemoryCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.rowcount = len(rows)

    def fetchone(self):
        if not self._rows:
            return None
        return self._rows[0]

    def fetchall(self):
        return list(self._rows)


def should_use_postgres_runtime(db_path: str | Path | None = None) -> bool:
    backend = DEFAULT_CONFIG.database_backend.strip().lower()
    if backend not in {"postgres", "postgresql"}:
        return False
    if db_path is None:
        return True
    try:
        requested = Path(db_path).resolve()
        canonical = Path(DEFAULT_CONFIG.db_path).resolve()
    except OSError:
        return False
    return requested == canonical


def translate_sqlite_runtime_sql(sql: str, *, named_params: bool = False) -> str | None:
    stripped = sql.strip()
    if not stripped:
        return None
    if stripped.upper().startswith("PRAGMA "):
        return None
    if re.fullmatch(r"SELECT\s+changes\(\)\s+AS\s+[A-Za-z_][A-Za-z0-9_]*", stripped, flags=re.IGNORECASE):
        return "__JANUS_SQLITE_CHANGES__"

    translated = _convert_named_params(sql) if named_params else convert_qmark_to_format(sql)
    translated = _translate_sqlite_master(translated)
    translated = _translate_json_extract(translated)
    translated = _translate_strftime_intervals(translated)
    translated = translated.replace("datetime('now')", "CURRENT_TIMESTAMP")
    translated = translated.replace('datetime("now")', "CURRENT_TIMESTAMP")
    translated = re.sub(
        r"CREATE\s+VIEW\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)\s+AS",
        r"CREATE OR REPLACE VIEW \1 AS",
        translated,
        flags=re.IGNORECASE,
    )
    translated = _strip_sqlite_foreign_key_lines(translated)
    translated = _escape_psycopg_literal_percents(translated)
    return translated


def _convert_named_params(sql: str) -> str:
    return re.sub(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)", r"%(\1)s", sql)


def _escape_psycopg_literal_percents(sql: str) -> str:
    """Escape percent signs that are SQL literals, not psycopg placeholders."""

    output: list[str] = []
    index = 0
    while index < len(sql):
        char = sql[index]
        if char != "%":
            output.append(char)
            index += 1
            continue
        next_char = sql[index + 1] if index + 1 < len(sql) else ""
        if next_char == "%":
            output.append("%%")
            index += 2
            continue
        if next_char == "s":
            output.append("%s")
            index += 2
            continue
        named_match = re.match(r"%\([A-Za-z_][A-Za-z0-9_]*\)s", sql[index:])
        if named_match:
            output.append(named_match.group(0))
            index += len(named_match.group(0))
            continue
        output.append("%%")
        index += 1
    return "".join(output)


def _translate_sqlite_master(sql: str) -> str:
    replacement = (
        "(SELECT table_name AS name, 'table' AS type "
        "FROM information_schema.tables WHERE table_schema = 'public') AS sqlite_master"
    )
    return re.sub(r"\bsqlite_master\b", replacement, sql, flags=re.IGNORECASE)


def _translate_json_extract(sql: str) -> str:
    pattern = re.compile(
        r"json_extract\(\s*([A-Za-z_][A-Za-z0-9_\.]*)\s*,\s*'\$\.([^']+)'\s*\)",
        flags=re.IGNORECASE,
    )

    def replace(match: re.Match[str]) -> str:
        expression = match.group(1)
        path = ",".join(part for part in match.group(2).split(".") if part)
        return f"({expression}::jsonb #>> '{{{path}}}')"

    return pattern.sub(replace, sql)


def _translate_strftime_intervals(sql: str) -> str:
    pattern = re.compile(
        r"([A-Za-z_][A-Za-z0-9_\.]*)\s*([<>]=)\s*strftime\("
        r"'%Y-%m-%dT%H:%M:%f\+00:00'\s*,\s*([^,]+?)\s*,\s*(%s)\s*\)",
        flags=re.IGNORECASE,
    )

    def replace(match: re.Match[str]) -> str:
        left = match.group(1)
        operator = match.group(2)
        base_expr = match.group(3)
        interval_param = match.group(4)
        return f"{left}::timestamp {operator} ({base_expr}::timestamp + {interval_param}::interval)"

    return pattern.sub(replace, sql)


def _strip_sqlite_foreign_key_lines(sql: str) -> str:
    lines = [
        line
        for line in sql.splitlines()
        if not line.strip().upper().startswith("FOREIGN KEY(")
    ]
    return re.sub(r",\s*\n\)", "\n)", "\n".join(lines))


def _split_sql_script(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_single_quote = False
    in_double_quote = False
    for char in sql:
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
        if char == ";" and not in_single_quote and not in_double_quote:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements
