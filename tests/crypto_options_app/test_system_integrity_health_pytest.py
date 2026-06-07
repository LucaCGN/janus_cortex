from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import crypto_options_app.reports.system_integrity as system_integrity
from crypto_options_app.config import CryptoOptionsAppConfig
from crypto_options_app.db.connection import connect
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.db.postgres_shadow import write_postgres_shadow_parity_report
from crypto_options_app.reports.live_order_audit import audit_validation_orders_against_exchange
from crypto_options_app.reports.system_integrity import HealthBuildOptions, build_system_integrity_health
from crypto_options_app.workers.feed_worker import write_watermark


def _operational_polymarket_status() -> dict:
    return {
        "schema_version": "polymarket_status_v1",
        "status": "operational",
        "clob_api": {"status": "operational", "trading_available": True},
        "blockers": [],
    }


def _health_options(artifact_root: Path) -> HealthBuildOptions:
    return HealthBuildOptions(artifact_root=artifact_root, polymarket_status_provider=_operational_polymarket_status)


def test_system_integrity_health_surfaces_missing_db_and_old_artifact_audit_fields_pytest(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    validation_dir = artifact_root / "live-validation"
    validation_dir.mkdir(parents=True)
    (validation_dir / "run.json").write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-06-03T03:20:00+00:00",
                "run_id": "run-1",
                "manual_orders_avoided": True,
                "strategy_rows": [
                    {
                        "strategy_id": "s_tier_outcome_consensus_cashout_v1",
                        "status": "executed",
                        "blockers": [],
                        "order_key": "order-1",
                        "live_submission_attempted": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=tmp_path / "missing.sqlite"),
        options=_health_options(artifact_root),
    )

    assert health["status"] == "degraded"
    blockers = set(health["integrity"]["readiness_blockers"])
    assert "canonical_db_missing" in blockers
    assert "validation_artifact_missing_exchange_audit_fields" in blockers
    assert health["live_validation"]["audit_missing_field_row_count"] == 1
    assert health["orders_allowed"] is False
    assert health["live_trading_authorized"] is False
    assert health["prepared_tests"]["core_flow"]["max_events"] == 3
    assert health["prepared_tests"]["core_flow"]["hard_time_limit_seconds"] == 900
    assert health["prepared_tests"]["core_flow"]["total_budget_cap_usd"] == 10.0


def test_system_integrity_health_reports_canonical_db_tables_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")

    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=db_path),
        options=_health_options(tmp_path / "missing-artifacts"),
    )

    assert health["db"]["exists"] is True
    assert health["db"]["missing_tables"] == []
    assert health["db"]["table_counts"]["validation_budget_ledger"] == 0
    assert health["validation_budget"]["schema_version"] == "crypto_options_validation_budget_v1"
    assert health["validation_budget"]["ledger_row_count"] == 0
    assert health["db"]["validation_budget"]["schema_version"] == "crypto_options_validation_budget_v1"
    assert health["db"]["validation_budget"]["validation_budget_cap_usd"] == 50.0
    assert health["prepared_tests"]["core_flow"]["may_begin_after_operator_live_gate"] is True
    assert "missing_live_validation_artifact" in set(health["integrity"]["readiness_blockers"])


def test_system_integrity_health_marks_stale_live_activity_watermark_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    now = datetime.now(UTC)
    fresh = now.isoformat()
    stale = (now - timedelta(days=3)).isoformat()

    with connect(db_path) as conn:
        for module_id in (
            "underlying_market_prices",
            "underlying_technical_observers",
            "top_profiles_distribution",
            "polymarket_option_price_capture",
        ):
            write_watermark(
                conn,
                service_name="crypto_options_app",
                module_id=module_id,
                status="healthy",
                last_run_at_utc=fresh,
                rows_observed=1,
                rows_inserted=1,
                source="pytest",
            )
        write_watermark(
            conn,
            service_name="crypto_options_app",
            module_id="polymarket_live_activity_capture",
            status="healthy",
            last_run_at_utc=stale,
            rows_observed=1,
            rows_inserted=0,
            source="pytest",
        )

    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=db_path),
        options=_health_options(tmp_path / "missing-artifacts"),
    )

    freshness = health["db"]["watermark_freshness"]
    by_module = {row["module_id"]: row for row in freshness["modules"]}
    live_activity = by_module["polymarket_live_activity_capture"]
    assert live_activity["raw_status"] == "healthy"
    assert live_activity["status"] == "stale"
    assert "stale_data_service_watermark:polymarket_live_activity_capture" in live_activity["blockers"]
    assert (
        "data_service:stale_data_service_watermark:polymarket_live_activity_capture"
        in set(health["integrity"]["readiness_blockers"])
    )


def test_system_integrity_db_report_marks_postgres_runtime_without_sqlite_file_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "missing-compatibility.sqlite"

    class FakePostgresConnection:
        is_postgres = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(system_integrity, "should_use_postgres_runtime", lambda _path: True)
    monkeypatch.setattr(system_integrity, "connect_read_only", lambda _path: FakePostgresConnection())
    monkeypatch.setattr(system_integrity, "_existing_table_names", lambda _conn: set(system_integrity.EXPECTED_TABLES))
    monkeypatch.setattr(system_integrity, "table_exists", lambda _conn, _table_name: False)

    report = system_integrity._db_report(db_path, artifact_root=tmp_path / "artifacts")

    assert report["exists"] is True
    assert report["configured_sqlite_path"] == str(db_path)
    assert report["configured_sqlite_path_exists"] is False
    assert report["postgres_runtime_expected"] is True
    assert report["connection_backend"] == "postgres"
    assert report["connection_is_postgres"] is True
    assert report["path_role"] == "sqlite_compatibility_path"
    assert report["runtime_source_of_truth"] == "postgres"
    assert report["schema_status"] == "complete"
    assert report["read_status"] == "ok"


def test_system_integrity_health_reports_db_lock_without_fake_schema_gap_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")

    def locked_read(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(system_integrity, "connect_read_only", locked_read)

    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=db_path),
        options=_health_options(tmp_path / "missing-artifacts"),
    )

    blockers = set(health["integrity"]["readiness_blockers"])
    assert health["status"] == "degraded"
    assert health["db"]["exists"] is True
    assert health["db"]["schema_status"] == "read_unavailable"
    assert health["db"]["read_status"] == "blocked"
    assert health["db"]["missing_tables"] == []
    assert "canonical_db_read_unavailable" in blockers
    assert "canonical_db_schema_incomplete" not in blockers
    assert health["prepared_tests"]["core_flow"]["may_begin_after_operator_live_gate"] is False
    assert "canonical_db_read_unavailable" in set(health["prepared_tests"]["core_flow"]["blockers"])


def test_system_integrity_health_uses_recent_db_report_cache_during_lock_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    artifact_root = tmp_path / "artifacts"
    options = _health_options(artifact_root)

    initial = build_system_integrity_health(CryptoOptionsAppConfig(db_path=db_path), options=options)
    assert initial["db"]["read_status"] == "ok"
    assert initial["db"]["schema_status"] == "complete"

    def locked_read(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(system_integrity, "connect_read_only", locked_read)

    health = build_system_integrity_health(CryptoOptionsAppConfig(db_path=db_path), options=options)

    blockers = set(health["integrity"]["readiness_blockers"])
    assert health["db"]["schema_status"] == "complete"
    assert health["db"]["read_status"] == "stale_fallback"
    assert health["db"]["cache_status"] == "stale_fallback"
    assert health["db"]["missing_tables"] == []
    assert "canonical_db_read_unavailable" not in blockers
    assert "canonical_db_schema_incomplete" not in blockers
    assert health["prepared_tests"]["core_flow"]["may_begin_after_operator_live_gate"] is True


def test_system_integrity_health_uses_expired_cache_for_monitoring_but_blocks_live_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    artifact_root = tmp_path / "artifacts"
    options = _health_options(artifact_root)

    initial = build_system_integrity_health(CryptoOptionsAppConfig(db_path=db_path), options=options)
    assert initial["db"]["read_status"] == "ok"
    cache_path = artifact_root / "reports" / "system_integrity_db_report_cache.json"
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    cached["cached_at_utc"] = "2026-01-01T00:00:00+00:00"
    cache_path.write_text(json.dumps(cached), encoding="utf-8")

    def locked_read(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(system_integrity, "connect_read_only", locked_read)

    health = build_system_integrity_health(CryptoOptionsAppConfig(db_path=db_path), options=options)

    blockers = set(health["integrity"]["readiness_blockers"])
    assert health["db"]["schema_status"] == "complete"
    assert health["db"]["read_status"] == "expired_fallback"
    assert health["db"]["cache_status"] == "expired_fallback"
    assert health["db"]["stale_for_live_readiness"] is True
    assert "canonical_db_read_unavailable" not in blockers
    assert "canonical_db_report_cache_expired" in blockers
    assert health["prepared_tests"]["core_flow"]["may_begin_after_operator_live_gate"] is False
    assert "canonical_db_report_cache_expired" in set(health["prepared_tests"]["core_flow"]["blockers"])


def test_system_integrity_health_uses_postgres_shadow_when_sqlite_cache_expired_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    artifact_root = tmp_path / "artifacts"
    options = _health_options(artifact_root)

    initial = build_system_integrity_health(CryptoOptionsAppConfig(db_path=db_path), options=options)
    assert initial["db"]["read_status"] == "ok"
    cache_path = artifact_root / "reports" / "system_integrity_db_report_cache.json"
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    cached["cached_at_utc"] = "2026-01-01T00:00:00+00:00"
    cache_path.write_text(json.dumps(cached), encoding="utf-8")
    write_postgres_shadow_parity_report(
        {
            "schema_version": "crypto_options_postgres_shadow_parity_v1",
            "status": "ok",
            "completed_at_utc": "2026-06-06T10:24:12+00:00",
            "runtime_read_cutover_allowed": True,
            "blockers": [],
            "table_count": 14,
            "matched_table_count": 14,
            "tables": [],
        },
        artifact_root=artifact_root,
    )

    def locked_read(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(system_integrity, "connect_read_only", locked_read)
    monkeypatch.setattr(
        system_integrity,
        "check_postgres_connection",
        lambda *_args, **_kwargs: {"status": "ok", "database": "crypto_options"},
    )

    health = build_system_integrity_health(CryptoOptionsAppConfig(db_path=db_path), options=options)

    blockers = set(health["integrity"]["readiness_blockers"])
    assert health["db"]["sqlite_read_status"] == "expired_fallback"
    assert health["db"]["read_status"] == "postgres_shadow_fallback"
    assert health["db"]["db_read_status"] == "postgres_shadow_fallback"
    assert health["db"]["cache_status"] == "postgres_shadow_fallback"
    assert health["db"]["postgres_shadow_read_available"] is True
    assert "canonical_db_report_cache_expired" not in blockers
    assert "canonical_db_report_cache_expired" not in set(health["prepared_tests"]["core_flow"]["blockers"])


def test_system_integrity_health_surfaces_postgres_shadow_degradation_on_expired_cache_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    artifact_root = tmp_path / "artifacts"
    options = _health_options(artifact_root)

    initial = build_system_integrity_health(CryptoOptionsAppConfig(db_path=db_path), options=options)
    assert initial["db"]["read_status"] == "ok"
    cache_path = artifact_root / "reports" / "system_integrity_db_report_cache.json"
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    cached["cached_at_utc"] = "2026-01-01T00:00:00+00:00"
    cache_path.write_text(json.dumps(cached), encoding="utf-8")
    write_postgres_shadow_parity_report(
        {
            "schema_version": "crypto_options_postgres_shadow_parity_v1",
            "status": "degraded",
            "completed_at_utc": "2026-06-06T10:44:38+00:00",
            "runtime_read_cutover_allowed": False,
            "blockers": ["row_count_mismatch", "latest_timestamp_mismatch"],
            "table_count": 14,
            "matched_table_count": 6,
            "tables": [
                {
                    "table": "profile_distribution_snapshots",
                    "status": "degraded",
                    "blockers": ["row_count_mismatch", "latest_timestamp_mismatch"],
                    "row_count_delta": 10,
                    "source_latest_timestamp": "2026-06-06T10:44:15+00:00",
                    "target_latest_timestamp": "2026-06-06T10:43:23+00:00",
                }
            ],
        },
        artifact_root=artifact_root,
    )

    def locked_read(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(system_integrity, "connect_read_only", locked_read)
    monkeypatch.setattr(
        system_integrity,
        "check_postgres_connection",
        lambda *_args, **_kwargs: {"status": "ok", "database": "crypto_options"},
    )

    health = build_system_integrity_health(CryptoOptionsAppConfig(db_path=db_path), options=options)

    blockers = set(health["integrity"]["readiness_blockers"])
    assert health["db"]["read_status"] == "expired_fallback"
    assert health["db"]["cache_status"] == "expired_fallback"
    assert health["db"]["postgres_shadow_read_available"] is False
    assert health["db"]["postgres_shadow_parity_status"] == "degraded"
    assert health["db"]["postgres_shadow_runtime_cutover_allowed"] is False
    assert health["db"]["postgres_shadow_blockers"] == ["row_count_mismatch", "latest_timestamp_mismatch"]
    assert health["db"]["postgres_shadow_lagging_tables"] == [
        {
            "table": "profile_distribution_snapshots",
            "blockers": ["row_count_mismatch", "latest_timestamp_mismatch"],
            "row_count_delta": 10,
            "source_latest_timestamp": "2026-06-06T10:44:15+00:00",
            "target_latest_timestamp": "2026-06-06T10:43:23+00:00",
        }
    ]
    assert "canonical_db_report_cache_expired" in blockers


def test_system_integrity_health_prefer_cache_returns_expired_cache_without_sqlite_read_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    artifact_root = tmp_path / "artifacts"
    initial_options = _health_options(artifact_root)

    initial = build_system_integrity_health(CryptoOptionsAppConfig(db_path=db_path), options=initial_options)
    assert initial["db"]["read_status"] == "ok"
    cache_path = artifact_root / "reports" / "system_integrity_db_report_cache.json"
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    cached["cached_at_utc"] = "2026-01-01T00:00:00+00:00"
    cache_path.write_text(json.dumps(cached), encoding="utf-8")

    def unexpected_read(*_args, **_kwargs):
        raise AssertionError("prefer_cached_db_report should not touch sqlite when an expired cache exists")

    monkeypatch.setattr(system_integrity, "connect_read_only", unexpected_read)

    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=db_path),
        options=HealthBuildOptions(
            artifact_root=artifact_root,
            polymarket_status_provider=_operational_polymarket_status,
            prefer_cached_db_report=True,
        ),
    )

    blockers = set(health["integrity"]["readiness_blockers"])
    assert health["db"]["schema_status"] == "complete"
    assert health["db"]["read_status"] == "expired_fallback"
    assert health["db"]["cache_status"] == "expired_fallback"
    assert health["db"]["stale_for_live_readiness"] is True
    assert "canonical_db_report_cache_expired" in blockers
    assert health["prepared_tests"]["core_flow"]["may_begin_after_operator_live_gate"] is False


def test_system_integrity_health_overlays_status_file_readiness_on_expired_cache_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    artifact_root = tmp_path / "artifacts"
    initial = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=db_path),
        options=_health_options(artifact_root),
    )
    assert initial["db"]["read_status"] == "ok"

    cache_path = artifact_root / "reports" / "system_integrity_db_report_cache.json"
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    cached["cached_at_utc"] = "2026-01-01T00:00:00+00:00"
    cache_path.write_text(json.dumps(cached), encoding="utf-8")

    automation_root = artifact_root / "automation"
    automation_root.mkdir(parents=True)
    for file_name, metrics in {
        "underlying_technical_observers_status.json": {
            "snapshot_rows_inserted": 18,
            "component_rows_inserted": 356,
            "readiness_rows_inserted": 2,
        },
        "profile_distribution_status.json": {
            "component_rows_inserted": 195,
        },
        "option_price_capture_status.json": {
            "tick_rows_inserted": 16,
            "pair_snapshot_rows_inserted": 8,
            "event_path_stats_rows_upserted": 8,
        },
    }.items():
        (automation_root / file_name).write_text(
            json.dumps(
                {
                    "generated_at_utc": "2026-06-06T09:00:00+00:00",
                    "schema_version": "test_status_v1",
                    "status": "healthy",
                    "orders_allowed": False,
                    "live_trading_authorized": False,
                    "manual_orders_avoided": True,
                    "blockers": [],
                    "summary": metrics | {"status": "healthy", "blockers": []},
                }
            ),
            encoding="utf-8",
        )

    def unexpected_read(*_args, **_kwargs):
        raise AssertionError("prefer_cached_db_report should not touch sqlite when an expired cache exists")

    monkeypatch.setattr(system_integrity, "connect_read_only", unexpected_read)

    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=db_path),
        options=HealthBuildOptions(
            artifact_root=artifact_root,
            polymarket_status_provider=_operational_polymarket_status,
            prefer_cached_db_report=True,
        ),
    )

    assert health["db"]["read_status"] == "expired_fallback"
    assert health["db"]["latest_data_signal_readiness_source"] == "service_status_files"
    readiness = health["db"]["latest_data_signal_readiness"]
    assert {row["data_block"] for row in readiness} == {"A", "B", "C"}
    assert {row["status"] for row in readiness} == {"ready"}
    assert health["db"]["status_file_source_summary"]["A"]["status"] == "ready"
    assert health["db"]["status_file_source_summary"]["B"]["status"] == "ready"
    assert health["db"]["status_file_source_summary"]["C"]["status"] == "ready"
    watermark_by_module = {row["module_id"]: row for row in health["db"]["watermarks"]}
    assert watermark_by_module["underlying_technical_observers"]["updated_at_utc"] == "2026-06-06T09:00:00+00:00"
    assert watermark_by_module["top_profiles_distribution"]["updated_at_utc"] == "2026-06-06T09:00:00+00:00"
    assert watermark_by_module["polymarket_option_price_capture"]["updated_at_utc"] == "2026-06-06T09:00:00+00:00"
    assert watermark_by_module["top_profiles_distribution"]["source"] == "service_status_file"
    assert "canonical_db_report_cache_expired" in set(health["integrity"]["readiness_blockers"])


def test_system_integrity_health_reads_legacy_report_status_files_pytest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    artifact_root = tmp_path / "artifacts"
    initial = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=db_path),
        options=_health_options(artifact_root),
    )
    assert initial["db"]["read_status"] == "ok"

    cache_path = artifact_root / "reports" / "system_integrity_db_report_cache.json"
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    cached["cached_at_utc"] = "2026-01-01T00:00:00+00:00"
    cache_path.write_text(json.dumps(cached), encoding="utf-8")

    for file_name, metrics in {
        "underlying_technical_observers_status.json": {
            "snapshot_rows_inserted": 18,
            "component_rows_inserted": 356,
        },
        "profile_distribution_status.json": {
            "component_rows_inserted": 195,
        },
        "option_price_capture_status.json": {
            "pair_snapshot_rows_inserted": 8,
            "event_path_stats_rows_upserted": 8,
        },
    }.items():
        (artifact_root / "reports" / file_name).write_text(
            json.dumps(
                {
                    "generated_at_utc": "2026-06-06T09:05:00+00:00",
                    "schema_version": "test_status_v1",
                    "status": "healthy",
                    "orders_allowed": False,
                    "live_trading_authorized": False,
                    "manual_orders_avoided": True,
                    "blockers": [],
                    "summary": metrics | {"status": "healthy", "blockers": []},
                }
            ),
            encoding="utf-8",
        )

    def unexpected_read(*_args, **_kwargs):
        raise AssertionError("prefer_cached_db_report should not touch sqlite when an expired cache exists")

    monkeypatch.setattr(system_integrity, "connect_read_only", unexpected_read)

    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=db_path),
        options=HealthBuildOptions(
            artifact_root=artifact_root,
            polymarket_status_provider=_operational_polymarket_status,
            prefer_cached_db_report=True,
        ),
    )

    assert health["db"]["latest_data_signal_readiness_source"] == "service_status_files"
    readiness = health["db"]["latest_data_signal_readiness"]
    assert {row["data_block"] for row in readiness} == {"A", "B", "C"}
    assert {
        Path(row["payload"]["status_file"]).parent.name
        for row in readiness
    } == {"reports"}
    watermark_by_module = {row["module_id"]: row for row in health["db"]["watermarks"]}
    assert watermark_by_module["underlying_technical_observers"]["updated_at_utc"] == "2026-06-06T09:05:00+00:00"
    assert watermark_by_module["top_profiles_distribution"]["updated_at_utc"] == "2026-06-06T09:05:00+00:00"
    assert watermark_by_module["polymarket_option_price_capture"]["updated_at_utc"] == "2026-06-06T09:05:00+00:00"


def test_system_integrity_health_db_report_cache_write_remains_parseable_after_consecutive_refreshes_pytest(
    tmp_path: Path,
) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    artifact_root = tmp_path / "artifacts"
    options = _health_options(artifact_root)

    first = build_system_integrity_health(CryptoOptionsAppConfig(db_path=db_path), options=options)
    second = build_system_integrity_health(CryptoOptionsAppConfig(db_path=db_path), options=options)

    cache_path = artifact_root / "reports" / "system_integrity_db_report_cache.json"
    cached = json.loads(cache_path.read_text(encoding="utf-8"))

    assert first["db"]["read_status"] == "ok"
    assert second["db"]["read_status"] == "ok"
    assert cached["read_status"] == "ok"
    assert cached["cache_status"] == "fresh"
    assert "cached_at_utc" in cached


def test_system_integrity_health_surfaces_completed_event_path_stats_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    with db_path.open("ab"):
        pass
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO polymarket_event_path_stats(
                event_path_stats_key, event_key, event_slug, symbol,
                event_start_time_utc, event_end_time_utc, computed_at_utc,
                snapshot_count, up_abs_move_per_minute, level_crossing_count,
                strong_rebound_touch_count, trade_print_count, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
            """,
            (
                "stats:event-1",
                "event-1",
                "btc-updown-5m-1",
                "BTC",
                "2026-06-04T00:00:00+00:00",
                "2026-06-04T00:05:00+00:00",
                "2026-06-04T00:06:00+00:00",
                120,
                0.11,
                12,
                4,
                3,
                "2026-06-04T00:06:00+00:00",
                "2026-06-04T00:06:00+00:00",
            ),
        )

    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=db_path),
        options=_health_options(tmp_path / "missing-artifacts"),
    )

    latest = health["db"]["latest_completed_event_path_stats"]
    assert latest[0]["event_slug"] == "btc-updown-5m-1"
    assert latest[0]["snapshot_count"] == 120
    assert latest[0]["trade_print_count"] == 3


def test_system_integrity_health_clamps_negative_event_path_latency_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO polymarket_event_path_stats(
                event_path_stats_key, event_key, event_slug, symbol,
                event_start_time_utc, event_end_time_utc, computed_at_utc,
                snapshot_count, avg_source_latency_ms, max_source_latency_ms,
                trade_print_count, source_json, inserted_at_utc, updated_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
            """,
            (
                "stats:event-negative-latency",
                "event-negative-latency",
                "eth-updown-5m-1",
                "ETH",
                "2026-06-04T00:00:00+00:00",
                "2026-06-04T00:05:00+00:00",
                "2026-06-04T00:06:00+00:00",
                25,
                -308.05176,
                1791.882,
                0,
                "2026-06-04T00:06:00+00:00",
                "2026-06-04T00:06:00+00:00",
            ),
        )

    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=db_path),
        options=_health_options(tmp_path / "missing-artifacts"),
    )

    latest = health["db"]["latest_completed_event_path_stats"]
    assert latest[0]["avg_source_latency_ms"] == 0.0
    assert latest[0]["max_source_latency_ms"] == 1791.882


def test_system_integrity_health_surfaces_data_signal_readiness_and_external_observers_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    now = "2026-06-04T10:00:00+00:00"
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO external_technical_observer_snapshots(
                observer_snapshot_key, provider, symbol, interval, source_url,
                request_started_at_utc, observed_at_utc, completed_at_utc, latency_ms,
                summary_label, summary_score, buy_count, sell_count, neutral_count,
                error_count, component_count, components_json, source_json, inserted_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '{}', ?)
            """,
            ("observer-1", "ifcm", "BTC", "5m", "https://example.test", now, now, now, 10, "Buy", 3, 4, 1, 2, 0, 7, now),
        )
        for block, module_id in (
            ("A", "underlying_technical_observers"),
            ("C", "polymarket_option_price_capture"),
        ):
            conn.execute(
                """
                INSERT INTO data_signal_readiness_snapshots(
                    readiness_key, data_block, module_id, symbol, generated_at_utc,
                    target_refresh_seconds, status, latest_source_at_utc,
                    source_age_seconds, payload_json, blockers_json, inserted_at_utc
                )
                VALUES (?, ?, ?, 'BTC', ?, 30, 'ready', ?, 0.5, '{"sample": true}', '[]', ?)
                """,
                (f"{block}:ready", block, module_id, now, now, now),
            )

    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=db_path),
        options=_health_options(tmp_path / "missing-artifacts"),
    )

    observers = health["db"]["latest_external_technical_observers"]
    readiness = health["db"]["latest_data_signal_readiness"]
    assert observers[0]["provider"] == "ifcm"
    assert observers[0]["symbol"] == "BTC"
    assert {row["data_block"] for row in readiness} >= {"A", "C"}
    assert {row["status"] for row in readiness} == {"ready"}


def test_system_integrity_health_normalizes_stale_zero_source_age_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    stale_source = (datetime.now(UTC) - timedelta(hours=2)).isoformat()

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO data_signal_readiness_snapshots(
                readiness_key, data_block, module_id, symbol, generated_at_utc,
                target_refresh_seconds, status, latest_source_at_utc,
                source_age_seconds, payload_json, blockers_json, inserted_at_utc
            )
            VALUES (?, ?, ?, 'BTC', ?, 30, 'ready', ?, 0.0, '{"sample": true}', '[]', ?)
            """,
            ("B:stale-zero-age", "B", "top_profiles_distribution", stale_source, stale_source, stale_source),
        )

    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=db_path),
        options=_health_options(tmp_path / "missing-artifacts"),
    )

    readiness = health["db"]["latest_data_signal_readiness"]
    profile_row = next(row for row in readiness if row["data_block"] == "B")
    assert profile_row["source_age_seconds"] >= 7200.0 - 30.0


def test_system_integrity_health_does_not_require_exchange_fields_for_blocked_rows_pytest(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    validation_dir = artifact_root / "live-validation"
    validation_dir.mkdir(parents=True)
    (validation_dir / "run.json").write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-06-03T03:20:00+00:00",
                "run_id": "run-1",
                "manual_orders_avoided": True,
                "strategy_rows": [
                    {
                        "strategy_id": "buying_ahead_pre_event_v1",
                        "status": "blocked",
                        "blockers": ["no_buying_ahead_signal"],
                        "event_key": "event-1",
                        "event_slug": "event-1",
                        "live_submission_attempted": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=initialize_schema(tmp_path / "crypto_options.sqlite")),
        options=_health_options(artifact_root),
    )

    assert health["live_validation"]["latest_audit_missing_field_row_count"] == 0
    assert "validation_artifact_missing_exchange_audit_fields" not in set(health["integrity"]["readiness_blockers"])


def test_system_integrity_health_blocks_live_tests_when_polymarket_clob_under_maintenance_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")

    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=db_path),
        options=HealthBuildOptions(
            artifact_root=tmp_path / "missing-artifacts",
            polymarket_status_provider=lambda: {
                "schema_version": "polymarket_status_v1",
                "status": "maintenance",
                "clob_api": {"status": "undermaintenance", "trading_available": False},
                "active_maintenances": [{"name": "Scheduled CLOB maintenance"}],
                "blockers": ["exchange_status_not_operational", "polymarket_active_maintenance"],
            },
        ),
    )

    blockers = set(health["integrity"]["readiness_blockers"])
    assert health["external_services"]["polymarket"]["status"] == "maintenance"
    assert "polymarket_clob_trading_unavailable" in blockers
    assert "polymarket_status:exchange_status_not_operational" in blockers


def test_system_integrity_health_summarizes_heavy_audit_and_settlement_reports_pytest(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    report_dir = artifact_root / "reports"
    validation_dir = artifact_root / "live-validation"
    report_dir.mkdir(parents=True)
    validation_dir.mkdir(parents=True)
    (validation_dir / "run.json").write_text(
        json.dumps({"run_id": "run", "strategy_rows": [], "manual_orders_avoided": True}),
        encoding="utf-8",
    )
    (report_dir / "live_order_integrity_audit_latest.json").write_text(
        json.dumps(
            {
                "schema_version": "wrapper",
                "status": "matched",
                "audit_attempt_count": 1,
                "audit": {
                    "schema_version": "audit",
                    "status": "matched",
                    "blockers": [],
                    "recorded_successful_buy_count": 1,
                    "exchange_buy_count": 1,
                    "strong_match_count": 1,
                    "summary": {
                        "recorded_status_counts": {"live_structural_executed": 1},
                        "recorded_unfilled_buy_count": 2,
                        "scoped_exchange_side_counts": {"BUY": 1},
                        "strong_matches": [{"very": "large"}],
                        "fill_mismatches": [],
                        "external_exchange_sells_in_validation_scope": [],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (report_dir / "settlement_performance_latest.json").write_text(
        json.dumps(
            {
                "schema_version": "settlement",
                "run_id": "run",
                "summary": {"filled_position_count": 1},
                "blockers": ["some_events_unresolved"],
                "db_counts": {"positions": 1},
                "rows": [{"large": "row"}],
                "manual_orders_avoided": True,
            }
        ),
        encoding="utf-8",
    )

    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=initialize_schema(tmp_path / "crypto_options.sqlite")),
        options=_health_options(artifact_root),
    )

    audit_summary = health["live_validation"]["latest_order_integrity_audit"]["audit"]["summary"]
    settlement_summary = health["live_validation"]["latest_settlement_performance"]
    assert "strong_matches" not in audit_summary
    assert audit_summary["scoped_exchange_side_counts"] == {"BUY": 1}
    assert settlement_summary["row_count"] == 1
    assert "rows" not in settlement_summary


def test_system_integrity_health_omits_validation_rows_but_keeps_counts_by_default_pytest(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    validation_dir = artifact_root / "live-validation"
    validation_dir.mkdir(parents=True)
    (validation_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "run",
                "strategy_rows": [
                    {
                        "strategy_id": "profile_hedge_scalping_v3",
                        "status": "live_structural_executed",
                        "blockers": [],
                        "event_key": "event",
                        "event_slug": "event",
                        "event_token_key": "event:down",
                        "token_id": "token",
                        "outcome": "Down",
                        "order_key": "order",
                        "exchange_order_id": "0xabc",
                        "order_status": "filled",
                        "reconciliation_status": "reconciled",
                        "lifecycle_covered": True,
                        "live_submission_attempted": True,
                    }
                ],
                "manual_orders_avoided": True,
            }
        ),
        encoding="utf-8",
    )

    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=initialize_schema(tmp_path / "crypto_options.sqlite")),
        options=_health_options(artifact_root),
    )

    latest = health["live_validation"]["artifacts"][-1]
    assert latest["strategy_row_count"] == 1
    assert latest["strategy_rows"] == []
    assert latest["successful_strategy_ids"] == ["profile_hedge_scalping_v3"]
    assert latest["has_filled_lifecycle_rows"] is True


def test_system_integrity_health_flags_historical_live_validation_without_budget_ledger_pytest(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    validation_dir = artifact_root / "live-validation"
    validation_dir.mkdir(parents=True)
    (validation_dir / "run.json").write_text(
        json.dumps({"run_id": "run-1", "strategy_rows": [], "manual_orders_avoided": True}),
        encoding="utf-8",
    )

    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=initialize_schema(tmp_path / "crypto_options.sqlite")),
        options=_health_options(artifact_root),
    )

    blockers = set(health["integrity"]["readiness_blockers"])
    assert "historical_live_validation_without_budget_ledger" in blockers
    assert health["validation_budget"]["ledger_row_count"] == 0
    assert health["validation_budget"]["cash_balance_status"] == "cash_balance_unavailable"
    assert health["db"]["validation_budget"]["ledger_row_count"] == 0
    assert health["db"]["validation_budget"]["cash_balance_status"] == "cash_balance_unavailable"


def test_system_integrity_health_flags_required_strategy_validation_run_without_ledger_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO strategy_validation_runs(
                strategy_validation_run_key, strategy_id, strategy_version, run_type,
                run_phase, run_status, run_id, validation_run_id,
                signal_dependencies_json, scoped_live_flags_json,
                max_notional_usd, max_events, max_trades, max_wall_time_seconds,
                stop_rules_json, lifecycle_audit_status, reconciliation_status,
                budget_ledger_required, cash_balance_status, evidence_json,
                started_at_utc, completed_at_utc, inserted_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', '{}', ?, ?, ?, ?, '{}', ?, ?, ?, ?, '{}', ?, ?, ?)
            """,
            (
                "strategy-validation-run-1",
                "event_context_outcome_v1",
                "v1",
                "supervised_live",
                "live_shadow_test",
                "completed",
                "child-run-1",
                "validation-run-1",
                10.0,
                1,
                1,
                120,
                "passed",
                "reconciled",
                1,
                "cash_balance_unavailable",
                "2026-06-05T01:00:00+00:00",
                "2026-06-05T01:02:00+00:00",
                "2026-06-05T01:00:00+00:00",
            ),
        )

    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=db_path),
        options=_health_options(tmp_path / "missing-artifacts"),
    )

    budget = health["db"]["validation_budget"]
    assert health["validation_budget"]["strategy_validation_run_count"] == 1
    assert health["validation_budget"]["ledger_required_run_without_entry_count"] == 1
    blockers = set(health["integrity"]["readiness_blockers"])
    assert budget["strategy_validation_run_count"] == 1
    assert budget["supervised_live_run_count"] == 1
    assert budget["ledger_required_run_without_entry_count"] == 1
    assert budget["latest_strategy_validation_run"]["validation_run_id"] == "validation-run-1"
    assert "strategy_validation_run_missing_budget_ledger" in blockers


def test_system_integrity_health_flags_orphaned_signal_validation_runs_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO signal_specs(
                signal_id, family, signal_type, sources_json, variant, version,
                filename, purpose, event_phase_relevance, refresh_rate_seconds,
                time_frames_relevant_json, required_data_blocks_json,
                validation_target, win_criteria, sample_unit, impact_if_degraded,
                description, signal_payload_example_json, spec_json, inserted_at_utc, updated_at_utc
            )
            VALUES (?, ?, ?, '["A"]', ?, ?, ?, ?, ?, ?, '["5m"]', '["A"]', ?, ?, ?, ?, ?, '{}', '{}', ?, ?)
            """,
            (
                "master_hedge_grid_scalping_outcome_prediction_cryptoprice_multiframe_trend_conflict_v1",
                "master_hedge_grid_scalping",
                "outcome_prediction",
                "multiframe_trend_conflict",
                "v1",
                "master_hedge_grid_scalping_outcome_prediction_cryptoprice_multiframe_trend_conflict_v1.py",
                "logic_gate",
                "pre_event",
                30,
                "forward_direction",
                "hit_rate_gt_0_58",
                "event",
                "critical",
                "Synthetic test fixture",
                "2026-06-05T01:00:00+00:00",
                "2026-06-05T01:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO signal_validation_runs(
                validation_run_key, queue_item_key, signal_id, version, phase,
                owner_id, started_at_utc, completed_at_utc, status, summary_json, inserted_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?)
            """,
            (
                "signal-run-1",
                None,
                "master_hedge_grid_scalping_outcome_prediction_cryptoprice_multiframe_trend_conflict_v1",
                "v1",
                "last_week_backtest",
                "worker-1",
                "2026-06-05T01:00:00+00:00",
                None,
                "running",
                "2026-06-05T01:00:00+00:00",
            ),
        )

    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=db_path),
        options=_health_options(tmp_path / "missing-artifacts"),
    )

    blockers = set(health["integrity"]["readiness_blockers"])
    signal_validation = health["db"]["signal_validation"]
    assert signal_validation["signal_validation_run_count"] == 1
    assert signal_validation["signal_validation_result_count"] == 0
    assert signal_validation["orphaned_run_count"] == 1
    assert signal_validation["latest_orphaned_runs"][0]["validation_run_key"] == "signal-run-1"
    assert "signal_validation_run_missing_result" in blockers


def test_system_integrity_health_times_out_slow_external_status_provider_pytest(tmp_path: Path) -> None:
    def slow_provider() -> dict:
        time.sleep(0.2)
        return _operational_polymarket_status()

    started_at = time.perf_counter()
    health = build_system_integrity_health(
        CryptoOptionsAppConfig(db_path=initialize_schema(tmp_path / "crypto_options.sqlite")),
        options=HealthBuildOptions(
            artifact_root=tmp_path / "missing-artifacts",
            polymarket_status_provider=slow_provider,
            external_status_timeout_seconds=0.01,
        ),
    )

    assert time.perf_counter() - started_at < 1.0
    assert health["external_services"]["polymarket"]["blockers"] == ["polymarket_status_timeout"]
    blockers = set(health["integrity"]["readiness_blockers"])
    assert "polymarket_status:polymarket_status_timeout" not in blockers
    assert "polymarket_clob_trading_unavailable" not in blockers
    assert "polymarket_status_unavailable_requires_per_order_verified_market" in set(
        health["external_services"]["polymarket"]["warnings"]
    )


def test_live_order_audit_strongly_matches_future_full_identity_rows_pytest() -> None:
    result = audit_validation_orders_against_exchange(
        validation_rows=[
            {
                "strategy_id": "strategy-1",
                "status": "executed",
                "blockers": [],
                "token_id": "token-1",
                "exchange_order_id": "0x1234567890abcdef",
                "filled_shares": 2,
                "fill_price": 0.51,
            }
        ],
        exchange_trades=[
            {
                "taker_order_id": "0x1234567890abcdef",
                "asset_id": "token-1",
                "side": "BUY",
                "size": "2",
                "price": "0.51",
                "outcome": "Up",
            }
        ],
    )

    assert result.status == "matched"
    assert result.strong_match_count == 1
    assert result.blockers == ()


def test_live_order_audit_flags_weak_token_match_and_surfaces_external_sell_pytest() -> None:
    result = audit_validation_orders_against_exchange(
        validation_rows=[
            {
                "strategy_id": "strategy-1",
                "status": "executed",
                "blockers": [],
                "token_id": "token-1",
                "position_key": "position:fill:order:abc:2.0:0.51",
            }
        ],
        exchange_trades=[
            {"side": "BUY", "size": "2", "price": "0.51", "asset_id": "token-1"},
            {"side": "SELL", "size": "2", "price": "0.99", "asset_id": "token-1"},
        ],
    )

    assert result.status == "weak_match_with_blockers"
    assert result.weak_match_count == 1
    assert result.unexpected_exchange_sell_count == 1
    assert result.summary["external_exchange_sells_in_validation_scope"][0]["token_id"] == "token-1"


def test_live_order_audit_ignores_unfilled_rows_and_unrelated_exchange_history_pytest() -> None:
    result = audit_validation_orders_against_exchange(
        validation_rows=[
            {
                "strategy_id": "strategy-1",
                "status": "live_structural_executed",
                "blockers": [],
                "token_id": "token-1",
                "exchange_order_id": "0xabcdef1234567890",
                "order_status": "filled",
                "filled_shares": 3,
                "fill_price": 0.49,
            },
            {
                "strategy_id": "strategy-2",
                "status": "live_structural_executed",
                "blockers": [],
                "token_id": "token-1",
                "exchange_order_id": "order:internal-unfilled",
                "order_status": "unfilled",
                "filled_shares": None,
                "fill_price": None,
            },
        ],
        exchange_trades=[
            {
                "taker_order_id": "0xabcdef1234567890",
                "asset_id": "token-1",
                "side": "BUY",
                "size": "3",
                "price": "0.49",
            },
            {
                "taker_order_id": "0xhistorical",
                "asset_id": "unrelated-token",
                "side": "BUY",
                "size": "100",
                "price": "0.10",
            },
        ],
    )

    assert result.status == "matched"
    assert result.recorded_successful_buy_count == 1
    assert result.exchange_buy_count == 1
    assert result.strong_match_count == 1
    assert result.unmatched_exchange_buy_count == 0
    assert result.summary["recorded_unfilled_buy_count"] == 1


def test_live_order_audit_does_not_scope_same_token_history_when_order_id_is_exact_pytest() -> None:
    result = audit_validation_orders_against_exchange(
        validation_rows=[
            {
                "strategy_id": "strategy-1",
                "status": "live_structural_executed",
                "blockers": [],
                "token_id": "token-1",
                "exchange_order_id": "0xcurrent1234567890",
                "order_status": "filled",
                "filled_shares": 2,
                "fill_price": 0.50,
            },
        ],
        exchange_trades=[
            {
                "taker_order_id": "0xcurrent1234567890",
                "asset_id": "token-1",
                "side": "BUY",
                "size": "2",
                "price": "0.50",
            },
            {
                "taker_order_id": "0xolder1234567890",
                "asset_id": "token-1",
                "side": "BUY",
                "size": "2",
                "price": "0.50",
            },
        ],
    )

    assert result.status == "matched"
    assert result.exchange_buy_count == 1
    assert result.strong_match_count == 1
    assert result.unmatched_exchange_buy_count == 0


def test_live_order_audit_flags_exchange_buy_for_blocked_submit_error_row_pytest() -> None:
    result = audit_validation_orders_against_exchange(
        validation_rows=[
            {
                "strategy_id": "strategy-1",
                "status": "blocked",
                "blockers": ["submit_error", "reconciliation_mismatch"],
                "token_id": "token-1",
                "exchange_order_id": "order:internal",
                "order_status": "submit_error",
                "filled_shares": None,
                "fill_price": None,
            }
        ],
        exchange_trades=[
            {
                "taker_order_id": "0xlatefill1234567890",
                "asset_id": "token-1",
                "side": "BUY",
                "size": "4.551723",
                "price": "0.29",
            }
        ],
    )

    assert result.status == "mismatch"
    assert result.recorded_successful_buy_count == 0
    assert result.exchange_buy_count == 1
    assert result.unmatched_exchange_buy_count == 1
    assert "exchange_buy_trades_without_recorded_match" in result.blockers
