from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DatabaseDialect:
    name: str
    paramstyle: str

    def placeholder(self, index: int | None = None) -> str:
        if self.paramstyle == "qmark":
            return "?"
        if self.paramstyle == "format":
            return "%s"
        raise ValueError(f"unsupported paramstyle: {self.paramstyle}")

    def placeholders(self, count: int) -> str:
        return ", ".join(self.placeholder(index) for index in range(max(0, int(count))))

    def seconds_between(self, later_expr: str, earlier_expr: str) -> str:
        if self.name == "sqlite":
            return f"((julianday({later_expr}) - julianday({earlier_expr})) * 86400.0)"
        if self.name == "postgres":
            return f"EXTRACT(EPOCH FROM ({later_expr}::timestamp - {earlier_expr}::timestamp))"
        raise ValueError(f"unsupported dialect: {self.name}")


SQLITE_DIALECT = DatabaseDialect(name="sqlite", paramstyle="qmark")
POSTGRES_DIALECT = DatabaseDialect(name="postgres", paramstyle="format")


def dialect_for_backend(backend: str) -> DatabaseDialect:
    normalized = backend.strip().lower()
    if normalized == "sqlite":
        return SQLITE_DIALECT
    if normalized in {"postgres", "postgresql"}:
        return POSTGRES_DIALECT
    raise ValueError(f"unsupported database backend: {backend}")


def convert_qmark_to_format(sql: str) -> str:
    """Convert qmark placeholders to psycopg2 format placeholders outside string literals."""

    output: list[str] = []
    in_single_quote = False
    in_double_quote = False
    index = 0
    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""
        if char == "'" and not in_double_quote:
            output.append(char)
            if in_single_quote and next_char == "'":
                output.append(next_char)
                index += 2
                continue
            in_single_quote = not in_single_quote
            index += 1
            continue
        if char == '"' and not in_single_quote:
            output.append(char)
            in_double_quote = not in_double_quote
            index += 1
            continue
        if char == "?" and not in_single_quote and not in_double_quote:
            output.append("%s")
        else:
            output.append(char)
        index += 1
    return "".join(output)
