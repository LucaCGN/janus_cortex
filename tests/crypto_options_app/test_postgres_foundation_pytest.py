from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from crypto_options_app.cache.redis_hot_plane import (
    RedisHotPlaneClient,
    RedisHotPlaneSettings,
    _encode_resp_command,
    check_redis_hot_plane,
)
from crypto_options_app.config import CENTRAL_POSTGRES_URL, CENTRAL_REDIS_URL, CryptoOptionsAppConfig
from crypto_options_app.db.postgres import (
    CryptoOptionsPostgresSettings,
    build_sqlite_to_postgres_plan,
    postgres_driver_status,
    render_postgres_schema_sql,
)
from crypto_options_app.db.postgres_connection import translate_sqlite_runtime_sql
from crypto_options_app.db.postgres_shadow import (
    PostgresShadowParityConfig,
    _within_live_lag_tolerance,
    build_postgres_shadow_parity_report,
    load_postgres_shadow_parity_report,
    write_postgres_shadow_parity_report,
)
from crypto_options_app.db.postgres_import import PostgresBulkCopyConfig, _upsert_update_columns
from crypto_options_app.scripts.run_crypto_options_sqlite_to_postgres_copy import main as postgres_copy_main


def test_crypto_options_config_has_postgres_defaults() -> None:
    config = CryptoOptionsAppConfig()
    assert config.database_backend == "postgres"
    assert config.postgres_enabled is True
    assert config.postgres_database_url == CENTRAL_POSTGRES_URL
    assert "127.0.0.1:55433" in config.postgres_database_url
    assert config.redis_enabled is False
    assert config.redis_url == CENTRAL_REDIS_URL


def test_postgres_and_optional_redis_compose_and_env_are_app_owned() -> None:
    compose = Path("crypto_options_app/docker-compose.postgres.yml")
    env = Path("crypto_options_app/.env.postgres.example")
    assert compose.exists()
    assert env.exists()
    compose_text = compose.read_text(encoding="utf-8")
    env_text = env.read_text(encoding="utf-8")
    assert "janus-cortex-crypto-options-postgres" in compose_text
    assert "postgres:16-alpine" in compose_text
    assert "55433:5432" in compose_text
    assert "janus-cortex-crypto-options-redis" in compose_text
    assert "redis:7-alpine" in compose_text
    assert "127.0.0.1:56379:6379" in compose_text
    assert "--maxmemory-policy" in compose_text
    assert "JANUS_CRYPTO_OPTIONS_POSTGRES_URL" in env_text
    assert "JANUS_CRYPTO_OPTIONS_REDIS_ENABLED=0" in env_text
    assert "JANUS_CRYPTO_OPTIONS_REDIS_URL=redis://127.0.0.1:56379/0" in env_text
    assert "janus_disposable" not in compose_text


def test_redis_hot_plane_defaults_disabled() -> None:
    settings = RedisHotPlaneSettings.from_url(CENTRAL_REDIS_URL, enabled=False)
    assert settings.host == "127.0.0.1"
    assert settings.port == 56379
    assert settings.database == 0
    status = check_redis_hot_plane(settings)
    assert status["status"] == "disabled"
    assert status["role"] == "gated_hot_plane_candidate"


def test_redis_hot_plane_cache_uses_json_and_ttl() -> None:
    commands: list[tuple[str, ...]] = []

    def fake_execute(command: tuple[str, ...]) -> object:
        commands.append(command)
        if command[0] == "GET":
            return '{"fresh":true,"source":"A"}'
        return "OK"

    client = RedisHotPlaneClient(
        RedisHotPlaneSettings(enabled=True),
        command_executor=fake_execute,
    )

    set_result = client.set_json_cache("source/A/latest", {"source": "A", "fresh": True}, ttl_seconds=30)
    get_result = client.get_json_cache("source/A/latest")

    assert set_result["status"] == "ok"
    assert get_result == {"status": "ok", "payload": {"fresh": True, "source": "A"}}
    assert commands[0] == (
        "SET",
        "crypto_options:cache:source/A/latest",
        '{"fresh":true,"source":"A"}',
        "EX",
        "30",
    )
    assert commands[1] == ("GET", "crypto_options:cache:source/A/latest")


def test_redis_hot_plane_lock_uses_nx_ttl_and_owner_checked_release() -> None:
    commands: list[tuple[str, ...]] = []

    def fake_execute(command: tuple[str, ...]) -> object:
        commands.append(command)
        if command[0] == "EVAL":
            return 1
        return "OK"

    client = RedisHotPlaneClient(
        RedisHotPlaneSettings(enabled=True),
        command_executor=fake_execute,
    )

    acquire_result = client.acquire_ttl_lock("queue/signal/123", "worker-a", ttl_seconds=45)
    release_result = client.release_ttl_lock("queue/signal/123", "worker-a")

    assert acquire_result == {"status": "ok", "acquired": True, "owner": "worker-a"}
    assert release_result == {"status": "ok", "released": True, "owner": "worker-a"}
    assert commands[0] == (
        "SET",
        "crypto_options:lock:queue/signal/123",
        "worker-a",
        "NX",
        "EX",
        "45",
    )
    assert commands[1][0] == "EVAL"
    assert commands[1][2:] == ("1", "crypto_options:lock:queue/signal/123", "worker-a")


def test_redis_resp_encoder_builds_command_frames() -> None:
    assert _encode_resp_command(("PING",)) == b"*1\r\n$4\r\nPING\r\n"
    assert _encode_resp_command(("SET", "k", "v", "EX", "5")).startswith(b"*5\r\n$3\r\nSET")


def test_postgres_settings_parse_default_url() -> None:
    settings = CryptoOptionsPostgresSettings.from_url(CENTRAL_POSTGRES_URL)
    assert settings.host == "127.0.0.1"
    assert settings.port == 55433
    assert settings.database == "crypto_options"
    assert settings.user == "crypto_options"


def test_postgres_schema_renderer_removes_sqlite_view_syntax() -> None:
    sql = render_postgres_schema_sql()
    assert "CREATE VIEW IF NOT EXISTS" not in sql
    assert "CREATE OR REPLACE VIEW v_crypto_options_app_signal_validation_status AS" in sql
    assert "CREATE TABLE IF NOT EXISTS app_settings" in sql
    assert "FOREIGN KEY(" not in sql


def test_initialize_schema_skips_postgres_bootstrap_when_runtime_schema_ready_pytest(
    monkeypatch, tmp_path: Path
) -> None:
    from crypto_options_app.db import schema

    db_path = tmp_path / "crypto_options.sqlite"
    monkeypatch.setattr(schema, "should_use_postgres_runtime", lambda path: True)
    monkeypatch.setattr(schema, "_postgres_runtime_schema_ready", lambda path: True)

    assert schema.initialize_schema(db_path) == db_path


def test_initialize_schema_bootstraps_postgres_only_when_schema_not_ready_pytest(
    monkeypatch, tmp_path: Path
) -> None:
    from crypto_options_app.db import postgres, schema

    calls: list[object] = []
    db_path = tmp_path / "crypto_options.sqlite"
    marker = object()
    monkeypatch.setattr(schema, "should_use_postgres_runtime", lambda path: True)
    monkeypatch.setattr(schema, "_postgres_runtime_schema_ready", lambda path: False)
    monkeypatch.setattr(postgres.CryptoOptionsPostgresSettings, "from_url", classmethod(lambda cls: marker))
    monkeypatch.setattr(postgres, "initialize_postgres_schema", lambda settings: calls.append(settings))

    assert schema.initialize_schema(db_path) == db_path
    assert calls == [marker]


def test_driver_status_is_structured() -> None:
    status = postgres_driver_status()
    assert "psycopg2_available" in status
    assert status["preferred_driver"] == "psycopg2"


def test_sqlite_to_postgres_plan_summarizes_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "mini.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE example (id TEXT PRIMARY KEY, value INTEGER)")
        conn.execute("INSERT INTO example(id, value) VALUES('a', 1)")
    plan = build_sqlite_to_postgres_plan(db_path)
    assert plan["schema_version"] == "crypto_options_postgres_migration_plan_v1"
    assert plan["source_sqlite"]["table_count"] == 1
    assert plan["total_counted_rows"] == 1
    assert plan["largest_tables"][0]["name"] == "example"
    assert "bootstrap_postgres_schema" in plan["migration_phases"]


def test_postgres_shadow_parity_blocks_missing_sqlite_source(tmp_path: Path) -> None:
    report = build_postgres_shadow_parity_report(
        PostgresShadowParityConfig(sqlite_path=tmp_path / "missing.sqlite")
    )

    assert report["schema_version"] == "crypto_options_postgres_shadow_parity_v1"
    assert report["status"] == "blocked"
    assert report["runtime_read_cutover_allowed"] is False
    assert report["blockers"][0].startswith("sqlite_source_missing")


def test_postgres_shadow_parity_artifact_round_trip(tmp_path: Path) -> None:
    report = {
        "schema_version": "crypto_options_postgres_shadow_parity_v1",
        "status": "degraded",
        "completed_at_utc": "2026-06-06T00:00:00+00:00",
        "runtime_read_cutover_allowed": False,
        "blockers": ["row_count_mismatch"],
        "tables": [],
    }

    path = write_postgres_shadow_parity_report(report, artifact_root=tmp_path)
    loaded = load_postgres_shadow_parity_report(artifact_root=tmp_path)

    assert path == tmp_path / "reports" / "postgres_shadow_parity_latest.json"
    assert loaded is not None
    assert loaded["status"] == "degraded"
    assert loaded["artifact_path"] == str(path)
    assert "artifact_age_seconds" in loaded
    assert json.loads(path.read_text(encoding="utf-8"))["blockers"] == ["row_count_mismatch"]


def test_postgres_shadow_parity_tolerates_bounded_live_writer_lag_only() -> None:
    assert _within_live_lag_tolerance(
        "external_technical_observer_snapshots",
        row_count_delta=18,
        latest_lag_seconds=55.0,
        latest_timestamp_match=False,
    ) == (True, True)
    assert _within_live_lag_tolerance(
        "external_technical_observer_snapshots",
        row_count_delta=18,
        latest_lag_seconds=250.0,
        latest_timestamp_match=False,
    ) == (True, False)
    assert _within_live_lag_tolerance(
        "signal_specs",
        row_count_delta=1,
        latest_lag_seconds=15.0,
        latest_timestamp_match=False,
    ) == (False, False)


def test_postgres_import_upsert_update_columns_exclude_primary_key() -> None:
    assert _upsert_update_columns(
        ("signal_id", "status", "updated_at_utc"),
        ("signal_id",),
    ) == ("status", "updated_at_utc")
    assert _upsert_update_columns(("id",), ("id",)) == ()


def test_postgres_bulk_copy_config_supports_upsert_update() -> None:
    config = PostgresBulkCopyConfig(upsert_update=True)
    assert config.upsert_update is True


def test_postgres_runtime_sql_translation_escapes_literal_like_percent_pytest() -> None:
    translated = translate_sqlite_runtime_sql(
        "SELECT * FROM strategy_validation_runs WHERE run_id LIKE ? AND strategy_id LIKE 'profile_splus_hedger_follow%'",
    )

    assert "run_id LIKE %s" in translated
    assert "strategy_id LIKE 'profile_splus_hedger_follow%%'" in translated


def test_postgres_runtime_sql_translation_keeps_named_params_when_escaping_percent_pytest() -> None:
    translated = translate_sqlite_runtime_sql(
        "SELECT * FROM signal_specs WHERE family = :family AND variant LIKE 'tail_%'",
        named_params=True,
    )

    assert "family = %(family)s" in translated
    assert "variant LIKE 'tail_%%'" in translated


def test_postgres_copy_cli_accepts_upsert_update_in_dry_run(tmp_path: Path) -> None:
    db_path = tmp_path / "mini.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE example (id TEXT PRIMARY KEY, value INTEGER)")
        conn.execute("INSERT INTO example(id, value) VALUES('a', 1)")

    output_dir = tmp_path / "reports"
    rc = postgres_copy_main(
        [
            "--sqlite-path",
            str(db_path),
            "--tables",
            "example",
            "--upsert-update",
            "--dry-run",
            "--output-dir",
            str(output_dir),
        ]
    )

    assert rc == 0
    report = json.loads((output_dir / "postgres_bulk_copy_latest.json").read_text(encoding="utf-8"))
    assert report["status"] == "dry_run"
    assert report["tables"][0]["table"] == "example"
