from __future__ import annotations

from typing import Any

from crypto_options_app.reports import runtime_audit


class _FakeRow(dict):
    pass


class _FakeCursor:
    def __init__(self, row: _FakeRow) -> None:
        self._row = row

    def fetchone(self) -> _FakeRow:
        return self._row


class _FakeConn:
    is_postgres = True

    def __init__(self) -> None:
        self.sql: list[str] = []

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, sql: str, _params: Any | None = None) -> _FakeCursor:
        self.sql.append(sql)
        assert "COUNT(*)" not in sql
        if "EXISTS" in sql:
            return _FakeCursor(_FakeRow(has_rows=True))
        if "reltuples" in sql:
            return _FakeCursor(_FakeRow(c=10))
        return _FakeCursor(_FakeRow(c=1))


def test_database_audit_uses_lightweight_row_estimates(monkeypatch) -> None:
    conn = _FakeConn()
    monkeypatch.setattr(runtime_audit, "connect", lambda: conn)
    monkeypatch.setattr(runtime_audit, "_runtime_code_audit", lambda: {"status": "ok", "blockers": []})

    audit = runtime_audit._database_audit()

    assert audit["status"] == "ok"
    assert audit["runtime_connection_is_postgres"] is True
    assert conn.sql
    assert all("COUNT(*)" not in sql for sql in conn.sql)
    assert any("EXISTS" in sql for sql in conn.sql)


def test_runtime_audit_reports_runtime_sqlite_connect_offenders(monkeypatch, tmp_path) -> None:
    fake_root = tmp_path / "crypto_options_app"
    runtime_dir = fake_root / "api"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "bad.py").write_text("import sqlite3\nsqlite3.connect('x')\n", encoding="utf-8")
    monkeypatch.setattr(runtime_audit, "APP_ROOT", fake_root)

    audit = runtime_audit._runtime_code_audit()

    assert audit["status"] == "blocked"
    assert audit["offenders"] == ["api\\bad.py"] or audit["offenders"] == ["api/bad.py"]


def test_parse_memory_size_bytes() -> None:
    assert runtime_audit._parse_memory_size_bytes("512.3MiB") == int(512.3 * 1024**2)
    assert runtime_audit._parse_memory_size_bytes("3.5GiB") == int(3.5 * 1024**3)
    assert runtime_audit._parse_memory_size_bytes("bad") is None
