from __future__ import annotations

import sqlite3
import subprocess
from typing import Any

from crypto_options_app.db.errors import is_database_full_error, is_transient_database_error
from crypto_options_app.reports import runtime_audit
from crypto_options_app.scripts import run_crypto_options_runtime_audit


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


def test_runtime_audit_allows_explicit_db_compat_sqlite_connect(monkeypatch, tmp_path) -> None:
    fake_root = tmp_path / "crypto_options_app"
    db_dir = fake_root / "db"
    db_dir.mkdir(parents=True)
    (db_dir / "connection.py").write_text("import sqlite3\nsqlite3.connect('fallback')\n", encoding="utf-8")
    monkeypatch.setattr(runtime_audit, "APP_ROOT", fake_root)

    audit = runtime_audit._runtime_code_audit()

    assert audit["status"] == "ok"
    assert audit["blockers"] == []
    assert audit["runtime_offenders"] == []
    assert audit["review_required"] == []
    assert len(audit["allowed_sqlite_usage"]) == 1
    assert audit["allowed_sqlite_usage"][0]["path"].replace("\\", "/") == "db/connection.py"
    assert audit["allowed_sqlite_usage"][0]["category"] == "allowed_db_adapter_migration_compat"
    assert audit["allowed_sqlite_usage"][0]["direct_connect"] is True


def test_runtime_audit_allows_explicit_legacy_sqlite_side_stores(monkeypatch, tmp_path) -> None:
    fake_root = tmp_path / "crypto_options_app"
    store_dir = fake_root / "pipelines" / "options"
    store_dir.mkdir(parents=True)
    (store_dir / "profile_store.py").write_text(
        "import sqlite3\nsqlite3.connect('profile-store.sqlite')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime_audit, "APP_ROOT", fake_root)

    audit = runtime_audit._runtime_code_audit()

    assert audit["status"] == "ok"
    assert audit["blockers"] == []
    assert audit["runtime_offenders"] == []
    assert audit["review_required"] == []
    assert len(audit["allowed_sqlite_usage"]) == 1
    assert audit["allowed_sqlite_usage"][0]["category"] == "allowed_legacy_sqlite_research_compat"


def test_parse_memory_size_bytes() -> None:
    assert runtime_audit._parse_memory_size_bytes("512.3MiB") == int(512.3 * 1024**2)
    assert runtime_audit._parse_memory_size_bytes("3.5GiB") == int(3.5 * 1024**3)
    assert runtime_audit._parse_memory_size_bytes("bad") is None


def test_process_audit_recognizes_factory_backend(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_audit,
        "_powershell_process_rows",
        lambda: [
            {
                "pid": 41416,
                "command_line": "python -m uvicorn crypto_options_app.api.app:create_app --factory --port 8011",
            },
            {
                "pid": 1,
                "command_line": "python -m crypto_options_app.scripts.run_crypto_options_underlying_market_price_capture",
            },
            {
                "pid": 2,
                "command_line": "python -m crypto_options_app.scripts.run_crypto_options_underlying_technical_observers",
            },
            {
                "pid": 3,
                "command_line": "python -m crypto_options_app.scripts.run_crypto_options_profile_distribution_service",
            },
            {
                "pid": 4,
                "command_line": "python -m crypto_options_app.scripts.run_crypto_options_option_price_capture",
            },
        ],
    )

    audit = runtime_audit._process_audit()

    assert audit["required"]["backend"]["status"] == "ok"
    assert audit["required"]["backend"]["pids"] == [41416]
    assert "missing_process:backend" not in audit["blockers"]


def test_endpoint_audit_treats_frontend_as_warning(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_audit,
        "_http_json",
        lambda _url: {"status": "ok", "payload": {"db": {"read_status": "ok"}}},
    )
    monkeypatch.setattr(
        runtime_audit,
        "_http_text",
        lambda _url: {"status": "blocked", "error": "ConnectionRefusedError"},
    )

    audit = runtime_audit._endpoint_audit(runtime_audit.RuntimeAuditOptions())

    assert audit["status"] == "degraded"
    assert audit["blockers"] == []
    assert "frontend_unavailable" in audit["warnings"]


def test_legacy_namespace_audit_passes_when_canonical_import_stays_clean(monkeypatch) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["python", "-c", "..."],
            returncode=0,
            stdout='{"loaded_forbidden_modules": []}',
            stderr="",
        )

    monkeypatch.setattr(runtime_audit.subprocess, "run", fake_run)

    audit = runtime_audit._legacy_namespace_audit()

    assert audit["status"] == "ok"
    assert audit["blockers"] == []
    assert audit["loaded_forbidden_modules"] == []


def test_legacy_namespace_audit_blocks_legacy_app_wrappers(monkeypatch) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["python", "-c", "..."],
            returncode=0,
            stdout='{"loaded_forbidden_modules": ["app.data.pipelines.crypto.options.live_review"]}',
            stderr="",
        )

    monkeypatch.setattr(runtime_audit.subprocess, "run", fake_run)

    audit = runtime_audit._legacy_namespace_audit()

    assert audit["status"] == "blocked"
    assert audit["blockers"] == [
        "legacy_runtime_namespace_loaded:app.data.pipelines.crypto.options.live_review"
    ]


def test_database_error_helpers_cover_sqlite_and_postgres_lock_text() -> None:
    assert is_transient_database_error(sqlite3.OperationalError("database is locked")) is True
    assert is_transient_database_error(RuntimeError("DeadlockDetected: deadlock detected")) is True
    assert is_transient_database_error(RuntimeError("canceling statement due to statement timeout")) is True
    assert is_transient_database_error(sqlite3.OperationalError("no such table: x")) is False
    assert is_database_full_error(sqlite3.OperationalError("database or disk is full")) is True


def test_runtime_audit_cli_allows_degraded_reports(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        run_crypto_options_runtime_audit,
        "build_runtime_audit",
        lambda _options: {
            "status": "degraded",
            "generated_at_utc": "2026-06-07T00:00:00+00:00",
            "blockers": [],
            "warnings": ["frontend_unavailable"],
            "manual_orders_avoided": True,
        },
    )

    exit_code = run_crypto_options_runtime_audit.main(["--markdown", "--skip-docker", "--skip-endpoints"])

    assert exit_code == 0
    assert "Status: `degraded`" in capsys.readouterr().out
