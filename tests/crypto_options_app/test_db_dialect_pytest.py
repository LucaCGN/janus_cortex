from __future__ import annotations

from crypto_options_app.db.dialect import POSTGRES_DIALECT, SQLITE_DIALECT, convert_qmark_to_format, dialect_for_backend


def test_dialect_placeholders_and_seconds_between_pytest() -> None:
    assert SQLITE_DIALECT.placeholder() == "?"
    assert SQLITE_DIALECT.placeholders(3) == "?, ?, ?"
    assert "julianday(later_col)" in SQLITE_DIALECT.seconds_between("later_col", "earlier_col")

    assert POSTGRES_DIALECT.placeholder() == "%s"
    assert POSTGRES_DIALECT.placeholders(2) == "%s, %s"
    assert "EXTRACT(EPOCH FROM" in POSTGRES_DIALECT.seconds_between("later_col", "earlier_col")
    assert dialect_for_backend("postgresql") == POSTGRES_DIALECT


def test_convert_qmark_to_format_skips_string_literals_pytest() -> None:
    sql = "SELECT * FROM t WHERE a = ? AND b = '?' AND c = \"?\" AND d = ?"
    assert convert_qmark_to_format(sql) == "SELECT * FROM t WHERE a = %s AND b = '?' AND c = \"?\" AND d = %s"


def test_convert_qmark_to_format_handles_escaped_quotes_pytest() -> None:
    sql = "SELECT 'it''s ?' AS txt, col FROM t WHERE id = ?"
    assert convert_qmark_to_format(sql) == "SELECT 'it''s ?' AS txt, col FROM t WHERE id = %s"
