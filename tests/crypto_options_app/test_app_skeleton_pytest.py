from __future__ import annotations

import importlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from crypto_options_app import CryptoOptionsAppConfig, create_app
from crypto_options_app.api.routers import dashboard as dashboard_router
from crypto_options_app.api.routers.dashboard import (
    _merge_source_module_rows,
    _module_block_summaries,
    _overlay_status_file_modules,
    _profile_distribution_rows,
    _service_status_file_row_is_fresh,
)
from crypto_options_app.api.routers import health as health_router
from crypto_options_app.api.routers.signals import _curate_signal_selection
from crypto_options_app.db.connection import connect
from crypto_options_app.db.postgres_shadow import write_postgres_shadow_parity_report
from crypto_options_app.db.schema import initialize_schema


EXPECTED_MODULES = [
    "crypto_options_app.api.app",
    "crypto_options_app.api.routers.dashboard",
    "crypto_options_app.api.routers.health",
    "crypto_options_app.api.routers.signals",
    "crypto_options_app.api.routers.strategies",
    "crypto_options_app.db.connection",
    "crypto_options_app.db.schema",
    "crypto_options_app.db.runtime_persistence",
    "crypto_options_app.feeds.profile_activity",
    "crypto_options_app.feeds.polymarket_events",
    "crypto_options_app.feeds.polymarket_prices",
    "crypto_options_app.feeds.polymarket_status",
    "crypto_options_app.feeds.underlying_prices",
    "crypto_options_app.profiles.grading",
    "crypto_options_app.profiles.reconstruction",
    "crypto_options_app.profiles.classifier",
    "crypto_options_app.profiles.generator_scores",
    "crypto_options_app.signals.contracts",
    "crypto_options_app.signals.aggregation",
    "crypto_options_app.signals.profile_signals",
    "crypto_options_app.signals.event_signals",
    "crypto_options_app.signals.indicator_signals",
    "crypto_options_app.signals.validation.models",
    "crypto_options_app.signals.validation.registry",
    "crypto_options_app.signals.validation.result_store",
    "crypto_options_app.signals.validation.runner",
    "crypto_options_app.indicators.compute",
    "crypto_options_app.indicators.definitions",
    "crypto_options_app.replay.frames",
    "crypto_options_app.replay.fill_simulation",
    "crypto_options_app.replay.exit_simulation",
    "crypto_options_app.replay.reports",
    "crypto_options_app.strategies.schema",
    "crypto_options_app.strategies.registry",
    "crypto_options_app.strategies.manager",
    "crypto_options_app.strategies.candidates",
    "crypto_options_app.strategies.readiness",
    "crypto_options_app.trading.candidates",
    "crypto_options_app.trading.intents",
    "crypto_options_app.trading.orders",
    "crypto_options_app.trading.fills",
    "crypto_options_app.trading.positions",
    "crypto_options_app.trading.exits",
    "crypto_options_app.trading.reconciliation",
    "crypto_options_app.trading.executor_boundary",
    "crypto_options_app.risk.gates",
    "crypto_options_app.risk.stop_gates",
    "crypto_options_app.risk.exposure",
    "crypto_options_app.workers.feed_worker",
    "crypto_options_app.workers.signal_worker",
    "crypto_options_app.workers.signal_design_reviewer",
    "crypto_options_app.workers.signal_validation_worker",
    "crypto_options_app.workers.replay_worker",
    "crypto_options_app.workers.strategy_worker",
    "crypto_options_app.workers.strategy_backtest_replay",
    "crypto_options_app.workers.strategy_live_replay",
    "crypto_options_app.workers.supervisor",
    "crypto_options_app.reports.run_report",
    "crypto_options_app.reports.strategy_report",
    "crypto_options_app.reports.system_status",
    "crypto_options_app.reports.system_integrity",
    "crypto_options_app.reports.live_order_audit",
    "crypto_options_app.reports.live_dashboard",
]


def test_crypto_options_app_imports_without_side_effects_pytest() -> None:
    for module_name in EXPECTED_MODULES:
        assert importlib.import_module(module_name)


def test_crypto_options_app_builder_mounts_health_route_pytest() -> None:
    app = create_app(CryptoOptionsAppConfig())
    client = TestClient(app)

    response = client.get("/v1/crypto-options-app/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "crypto_options_app_health_v2"
    assert payload["service"] == "crypto-options-app"
    assert payload["status"] in {"ok", "degraded"}
    assert payload["orders_allowed"] is False
    assert payload["live_trading_authorized"] is False
    assert payload["manual_orders_avoided"] is True
    assert "db" in payload
    assert "external_services" in payload
    assert "strategies" in payload
    assert "live_validation" in payload
    assert "integrity" in payload


def test_crypto_options_app_health_ping_is_process_liveness_only_pytest() -> None:
    app = create_app(CryptoOptionsAppConfig())
    client = TestClient(app)

    response = client.get("/v1/crypto-options-app/health/ping")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "crypto_options_app_health_ping_v1"
    assert payload["service"] == "crypto-options-app"
    assert payload["status"] == "ok"


def test_crypto_options_app_health_uses_short_cached_polymarket_status_pytest(monkeypatch) -> None:
    calls: list[float] = []

    def fake_fetch_polymarket_status(*, timeout_seconds: float) -> dict:
        calls.append(timeout_seconds)
        return {
            "schema_version": "polymarket_status_v1",
            "status": "operational",
            "clob_api": {"status": "operational", "trading_available": True},
            "blockers": [],
        }

    monkeypatch.setattr(health_router, "fetch_polymarket_status", fake_fetch_polymarket_status)
    app = create_app(CryptoOptionsAppConfig())
    client = TestClient(app)

    first = client.get("/v1/crypto-options-app/health")
    second = client.get("/v1/crypto-options-app/health")

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == [0.4]


def test_crypto_options_app_health_serves_expired_cache_without_inline_full_db_refresh_pytest(tmp_path, monkeypatch) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    artifact_root = tmp_path / "artifacts"
    app = create_app(CryptoOptionsAppConfig(db_path=db_path, artifact_root=artifact_root, database_backend="sqlite"))
    client = TestClient(app)

    cache_path = artifact_root / "reports" / "system_integrity_db_report_cache.json"
    initial = client.get("/v1/crypto-options-app/health")
    assert initial.status_code == 200
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached["read_status"] == "ok"
    assert cached["db_read_status"] == "ok"
    assert cached["generated_at_utc"] == cached["cached_at_utc"]
    cached["cached_at_utc"] = "2026-01-01T00:00:00+00:00"
    cache_path.write_text(json.dumps(cached), encoding="utf-8")

    original_builder = health_router.build_system_integrity_health
    prefer_flags: list[bool] = []

    def tracked_builder(config, *, options=None):
        prefer_flags.append(bool(options and options.prefer_cached_db_report))
        return original_builder(config, options=options)

    monkeypatch.setattr(health_router, "build_system_integrity_health", tracked_builder)
    app.state.crypto_options_health_cache = None

    response = client.get("/v1/crypto-options-app/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["db"]["read_status"] == "expired_fallback"
    assert payload["db"]["db_read_status"] == "expired_fallback"
    assert payload["db"]["cache_status"] == "expired_fallback"
    assert "canonical_db_report_cache_expired" in set(payload["integrity"]["readiness_blockers"])
    assert prefer_flags == [True]


def test_crypto_options_app_builder_mounts_dashboard_routes_pytest() -> None:
    app = create_app(CryptoOptionsAppConfig())
    client = TestClient(app)

    command_center = client.get("/v1/crypto-options-app")
    page = client.get("/v1/crypto-options-app/dashboard")
    state = client.get("/v1/crypto-options-app/dashboard/state")
    control_center = client.get("/v1/crypto-options-app/dashboard/control-center-state")

    assert command_center.status_code == 200
    assert "Crypto Options Control Surface" in command_center.text
    assert "strategyPolicyPanel" in command_center.text
    assert page.status_code == 200
    assert "Crypto Options Live Console" in page.text
    assert state.status_code == 200
    assert control_center.status_code == 200
    payload = state.json()
    assert payload["schema_version"] == "crypto_options_live_dashboard_state_v1"
    assert payload["manual_orders_avoided"] is True
    control_payload = control_center.json()
    assert control_payload["schema_version"] == "crypto_options_control_center_state_v1"
    assert control_payload["orders_allowed"] is False
    assert control_payload["manual_orders_avoided"] is True


def test_control_center_state_stays_online_when_dashboard_db_read_is_locked_pytest(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    artifact_root = tmp_path / "artifacts"

    def locked_read(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(dashboard_router, "_connect_dashboard_read_only", locked_read)

    app = create_app(CryptoOptionsAppConfig(db_path=db_path, artifact_root=artifact_root))
    client = TestClient(app)

    response = client.get("/v1/crypto-options-app/dashboard/control-center-state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "crypto_options_control_center_state_v1"
    assert payload["db_read_status"] == "blocked"
    assert "database is locked" in payload["db_read_error"]
    assert payload["orders_allowed"] is False
    assert payload["manual_orders_avoided"] is True


def test_control_center_uses_expired_cache_without_inline_db_refresh_pytest(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    artifact_root = tmp_path / "artifacts"
    reports_root = artifact_root / "reports"
    automation_root = artifact_root / "automation"
    reports_root.mkdir(parents=True)
    automation_root.mkdir(parents=True)
    (reports_root / "control_center_state_cache.json").write_text(
        json.dumps(
            {
                "schema_version": "crypto_options_control_center_state_v1",
                "cached_at_utc": "2026-01-01T00:00:00+00:00",
                "generated_at_utc": "2026-01-01T00:00:00+00:00",
                "modules": [],
                "module_blocks": {},
                "positions": [{"position_key": "cached-position"}],
                "orders": [],
                "history": [],
                "events": [],
                "profile_distributions": [],
                "crypto_indicators": {"latest_prices": [], "technicals": [], "indicator_snapshots": []},
            }
        ),
        encoding="utf-8",
    )
    (automation_root / "option_price_capture_status.json").write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "status": "healthy",
                "orders_allowed": False,
                "live_trading_authorized": False,
                "manual_orders_avoided": True,
                "blockers": [],
                "summary": {"status": "healthy", "blockers": [], "event_path_stats_rows_upserted": 8},
            }
        ),
        encoding="utf-8",
    )

    def unexpected_db_refresh(*_args, **_kwargs):
        raise AssertionError("control center should not refresh DB on default expired-cache path")

    monkeypatch.setattr(dashboard_router, "_connect_dashboard_read_only", unexpected_db_refresh)

    app = create_app(CryptoOptionsAppConfig(db_path=db_path, artifact_root=artifact_root))
    client = TestClient(app)

    response = client.get("/v1/crypto-options-app/dashboard/control-center-state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["db_read_status"] == "expired_fallback"
    assert payload["cache_status"] == "expired_fallback"
    assert payload["positions"] == [{"position_key": "cached-position"}]
    assert payload["module_blocks"]["C"]["status"] == "ready"
    assert "canonical_db_report_cache_expired" in payload["readiness_blockers"]
    assert payload["orders_allowed"] is False
    assert payload["manual_orders_avoided"] is True


def test_control_center_expired_cache_rebuilds_degraded_profile_module_before_status_overlay_pytest(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    artifact_root = tmp_path / "artifacts"
    reports_root = artifact_root / "reports"
    automation_root = artifact_root / "automation"
    reports_root.mkdir(parents=True)
    automation_root.mkdir(parents=True)
    stale = "2026-01-01T00:00:00+00:00"
    (reports_root / "control_center_state_cache.json").write_text(
        json.dumps(
            {
                "schema_version": "crypto_options_control_center_state_v1",
                "cached_at_utc": stale,
                "generated_at_utc": stale,
                "modules": [],
                "module_blocks": {},
                "positions": [],
                "orders": [],
                "history": [],
                "events": [],
                "profile_distributions": [
                    {
                        "distribution_snapshot_key": "stale-profile",
                        "symbol": "BTC",
                        "phase": "live",
                        "computed_at_utc": stale,
                        "blockers": [],
                        "coverage_warnings": [],
                        "source_age_seconds": 900.0,
                        "target_refresh_seconds": 30.0,
                    }
                ],
                "crypto_indicators": {"latest_prices": [], "technicals": [], "indicator_snapshots": []},
            }
        ),
        encoding="utf-8",
    )
    (automation_root / "profile_distribution_status.json").write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "status": "healthy",
                "orders_allowed": False,
                "live_trading_authorized": False,
                "manual_orders_avoided": True,
                "blockers": [],
                "summary": {"status": "healthy", "blockers": [], "component_rows_inserted": 20},
            }
        ),
        encoding="utf-8",
    )

    def unexpected_db_refresh(*_args, **_kwargs):
        raise AssertionError("control center should not refresh DB on default expired-cache path")

    monkeypatch.setattr(dashboard_router, "_connect_dashboard_read_only", unexpected_db_refresh)

    app = create_app(CryptoOptionsAppConfig(db_path=db_path, artifact_root=artifact_root))
    client = TestClient(app)

    response = client.get("/v1/crypto-options-app/dashboard/control-center-state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["db_read_status"] == "expired_fallback"
    assert payload["module_blocks"]["B"]["status"] == "degraded"
    assert payload["module_blocks"]["B"]["blockers"] == ["profile_distribution_rows_stale"]
    assert "BTC" in payload["module_blocks"]["B"]["detail"]
    assert len([row for row in payload["modules"] if row["data_block"] == "B"]) == 2
    assert "canonical_db_report_cache_expired" in payload["readiness_blockers"]


def test_control_center_uses_postgres_shadow_when_cache_is_expired_pytest(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    artifact_root = tmp_path / "artifacts"
    reports_root = artifact_root / "reports"
    reports_root.mkdir(parents=True)
    (reports_root / "control_center_state_cache.json").write_text(
        json.dumps(
            {
                "schema_version": "crypto_options_control_center_state_v1",
                "cached_at_utc": "2026-01-01T00:00:00+00:00",
                "generated_at_utc": "2026-01-01T00:00:00+00:00",
                "readiness_blockers": ["canonical_db_report_cache_expired"],
                "modules": [],
                "module_blocks": {},
                "positions": [{"position_key": "cached-position"}],
                "orders": [],
                "history": [],
                "events": [],
                "profile_distributions": [],
                "crypto_indicators": {"latest_prices": [], "technicals": [], "indicator_snapshots": []},
            }
        ),
        encoding="utf-8",
    )
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

    def unexpected_db_refresh(*_args, **_kwargs):
        raise AssertionError("control center should use shadow fallback instead of SQLite refresh")

    monkeypatch.setattr(dashboard_router, "_connect_dashboard_read_only", unexpected_db_refresh)

    app = create_app(CryptoOptionsAppConfig(db_path=db_path, artifact_root=artifact_root))
    client = TestClient(app)

    response = client.get("/v1/crypto-options-app/dashboard/control-center-state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["sqlite_read_status"] == "expired_fallback"
    assert payload["db_read_status"] == "postgres_shadow_fallback"
    assert payload["cache_status"] == "postgres_shadow_fallback"
    assert payload["postgres_shadow_read_available"] is True
    assert "canonical_db_report_cache_expired" not in payload["readiness_blockers"]
    assert payload["orders_allowed"] is False
    assert payload["manual_orders_avoided"] is True


def test_control_center_surfaces_postgres_shadow_degradation_when_cache_is_expired_pytest(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = initialize_schema(tmp_path / "crypto_options.sqlite")
    artifact_root = tmp_path / "artifacts"
    reports_root = artifact_root / "reports"
    reports_root.mkdir(parents=True)
    (reports_root / "control_center_state_cache.json").write_text(
        json.dumps(
            {
                "schema_version": "crypto_options_control_center_state_v1",
                "cached_at_utc": "2026-01-01T00:00:00+00:00",
                "generated_at_utc": "2026-01-01T00:00:00+00:00",
                "readiness_blockers": ["canonical_db_report_cache_expired"],
                "modules": [],
                "module_blocks": {},
                "positions": [{"position_key": "cached-position"}],
                "orders": [],
                "history": [],
                "events": [],
                "profile_distributions": [],
                "crypto_indicators": {"latest_prices": [], "technicals": [], "indicator_snapshots": []},
            }
        ),
        encoding="utf-8",
    )
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
                    "table": "external_technical_observer_snapshots",
                    "status": "degraded",
                    "blockers": ["row_count_mismatch", "latest_timestamp_mismatch"],
                    "row_count_delta": 18,
                    "source_latest_timestamp": "2026-06-06T10:44:13+00:00",
                    "target_latest_timestamp": "2026-06-06T10:43:19+00:00",
                }
            ],
        },
        artifact_root=artifact_root,
    )

    def unexpected_db_refresh(*_args, **_kwargs):
        raise AssertionError("control center should keep expired cache fallback when parity is degraded")

    monkeypatch.setattr(dashboard_router, "_connect_dashboard_read_only", unexpected_db_refresh)

    app = create_app(CryptoOptionsAppConfig(db_path=db_path, artifact_root=artifact_root))
    client = TestClient(app)

    response = client.get("/v1/crypto-options-app/dashboard/control-center-state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["db_read_status"] == "expired_fallback"
    assert payload["cache_status"] == "expired_fallback"
    assert payload["postgres_shadow_read_available"] is False
    assert payload["postgres_shadow_parity_status"] == "degraded"
    assert payload["postgres_shadow_runtime_cutover_allowed"] is False
    assert payload["postgres_shadow_blockers"] == ["row_count_mismatch", "latest_timestamp_mismatch"]
    assert payload["postgres_shadow_lagging_tables"] == [
        {
            "table": "external_technical_observer_snapshots",
            "blockers": ["row_count_mismatch", "latest_timestamp_mismatch"],
            "row_count_delta": 18,
            "source_latest_timestamp": "2026-06-06T10:44:13+00:00",
            "target_latest_timestamp": "2026-06-06T10:43:19+00:00",
        }
    ]
    assert "canonical_db_report_cache_expired" in payload["readiness_blockers"]


def test_control_center_synthesizes_source_modules_from_visible_data_pytest() -> None:
    now = datetime.now(UTC)
    modules = _merge_source_module_rows(
        [],
        crypto={
            "latest_prices": [{"symbol": "BTC", "observed_at_utc": now.isoformat()}],
            "technicals": [{"symbol": "BTC", "completed_at_utc": now.isoformat()}],
            "indicator_snapshots": [{"symbol": "BTC", "computed_at_utc": now.isoformat()}],
        },
        profiles=[
            {
                "distribution_snapshot_key": "profile-row",
                "blockers": [],
                "computed_at_utc": now.isoformat(),
                "source_age_seconds": 4.0,
                "target_refresh_seconds": 30.0,
            }
        ],
        events=[{"event_path_stats_key": "event-row", "snapshot_count": 8, "last_snapshot_at_utc": now.isoformat()}],
    )

    by_block = {row["data_block"]: row for row in modules}
    assert by_block["A"]["status"] == "ready"
    assert by_block["A"]["metrics"]["latest_price_rows"] == 1
    assert by_block["B"]["status"] == "ready"
    assert by_block["B"]["metrics"]["distribution_snapshot_rows"] == 1
    assert by_block["C"]["status"] == "ready"
    assert by_block["C"]["metrics"]["event_path_rows"] == 1


def test_control_center_overlays_service_status_files_on_source_modules_pytest(tmp_path) -> None:
    app = create_app(
        CryptoOptionsAppConfig(
            db_path=tmp_path / "control.sqlite",
            artifact_root=tmp_path / "artifacts",
        )
    )
    automation_root = tmp_path / "artifacts" / "automation"
    automation_root.mkdir(parents=True)
    for file_name, metrics in {
        "underlying_technical_observers_status.json": {"component_rows_inserted": 100},
        "profile_distribution_status.json": {"component_rows_inserted": 20},
        "option_price_capture_status.json": {"event_path_stats_rows_upserted": 8},
    }.items():
        (automation_root / file_name).write_text(
            json.dumps(
                {
                    "generated_at_utc": datetime.now(UTC).isoformat(),
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
    client = TestClient(app)

    response = client.get("/v1/crypto-options-app/dashboard/control-center-state")

    assert response.status_code == 200
    payload = response.json()
    by_block = payload["module_blocks"]
    assert by_block["A"]["status"] == "ready"
    assert by_block["B"]["status"] == "ready"
    assert by_block["C"]["status"] == "ready"
    assert {row["source"] for row in payload["modules"]} == {"service_status_file"}


def test_service_status_file_freshness_accepts_generated_at_without_source_age_pytest() -> None:
    assert _service_status_file_row_is_fresh(
        {
            "source": "service_status_file",
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "target_refresh_seconds": 30.0,
        }
    )


def test_control_center_status_file_overlay_does_not_hide_degraded_db_rows_pytest(tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    automation_root = artifact_root / "automation"
    automation_root.mkdir(parents=True)
    (automation_root / "profile_distribution_status.json").write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "status": "healthy",
                "orders_allowed": False,
                "live_trading_authorized": False,
                "manual_orders_avoided": True,
                "blockers": [],
                "summary": {"status": "healthy", "blockers": [], "component_rows_inserted": 20},
            }
        ),
        encoding="utf-8",
    )
    modules = [
        {
            "data_block": "B",
            "module_id": "top_profiles_distribution",
            "symbol": "BTC",
            "status": "degraded",
            "blockers": ["no_ready_profile_distribution"],
            "source": "db_readiness",
        }
    ]

    merged = _overlay_status_file_modules(modules, artifact_root=artifact_root)
    summaries = _module_block_summaries(merged)

    assert len([row for row in merged if row["data_block"] == "B"]) == 2
    assert summaries["B"]["status"] == "ready"
    assert summaries["B"]["blockers"] == []
    assert summaries["B"]["detail"] == "2 modules"


def test_control_center_stale_service_status_file_does_not_hide_db_blockers_pytest(tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    status_path = artifact_root / "automation" / "profile_distribution_service_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(
        json.dumps(
            {
                "generated_at_utc": (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
                "status": "healthy",
                "orders_allowed": False,
                "live_trading_authorized": False,
                "manual_orders_avoided": True,
                "blockers": [],
                "summary": {"status": "healthy", "blockers": [], "component_rows_inserted": 20},
            }
        ),
        encoding="utf-8",
    )
    modules = [
        {
            "data_block": "B",
            "module_id": "top_profiles_distribution",
            "symbol": "BTC",
            "status": "degraded",
            "blockers": ["no_ready_profile_distribution"],
            "source": "db_readiness",
        }
    ]

    merged = _overlay_status_file_modules(modules, artifact_root=artifact_root)
    summaries = _module_block_summaries(merged)

    assert summaries["B"]["status"] == "degraded"
    assert summaries["B"]["blockers"] == ["no_ready_profile_distribution"]
    assert "BTC" in summaries["B"]["detail"]


def test_control_center_profile_module_treats_sparse_profile_coverage_as_warning_pytest() -> None:
    now = datetime.now(UTC)
    modules = _merge_source_module_rows(
        [],
        crypto={"latest_prices": [], "technicals": [], "indicator_snapshots": []},
        profiles=[
            {
                "distribution_snapshot_key": "live-profile-row",
                "phase": "live",
                "computed_at_utc": now.isoformat(),
                "component_count": 12,
                "blockers": [],
                "coverage_warnings": [],
                "source_age_seconds": 4.0,
                "target_refresh_seconds": 30.0,
            },
            {
                "distribution_snapshot_key": "future-sparse-row",
                "phase": "pre",
                "computed_at_utc": now.isoformat(),
                "component_count": 0,
                "blockers": [],
                "coverage_warnings": ["no_profile_distribution_components"],
                "source_age_seconds": 4.0,
                "target_refresh_seconds": 30.0,
            },
            {
                "distribution_snapshot_key": "old-stale-row",
                "phase": "live",
                "computed_at_utc": (now - timedelta(minutes=12)).isoformat(),
                "component_count": 7,
                "blockers": ["profile_distribution_source_stale"],
                "coverage_warnings": [],
                "source_age_seconds": 1200.0,
                "target_refresh_seconds": 30.0,
            },
        ],
        events=[],
    )

    profile_module = next(row for row in modules if row["data_block"] == "B")
    assert profile_module["status"] == "ready"
    assert profile_module["blockers"] == []
    assert profile_module["metrics"]["distribution_snapshot_rows"] == 3
    assert profile_module["metrics"]["rows_with_blockers"] == 0
    assert profile_module["metrics"]["rows_stale"] == 0
    assert profile_module["metrics"]["rows_with_coverage_warnings"] == 1


def test_control_center_profile_module_ignores_stale_post_rows_when_live_rows_are_fresh_pytest() -> None:
    now = datetime.now(UTC)
    modules = _merge_source_module_rows(
        [],
        crypto={"latest_prices": [], "technicals": [], "indicator_snapshots": []},
        profiles=[
            {
                "distribution_snapshot_key": "fresh-live-row",
                "phase": "live",
                "symbol": "BTC",
                "computed_at_utc": now.isoformat(),
                "component_count": 29,
                "blockers": [],
                "coverage_warnings": [],
                "source_age_seconds": 0.0,
                "target_refresh_seconds": 30.0,
            },
            {
                "distribution_snapshot_key": "fresh-pre-row",
                "phase": "pre",
                "symbol": "BTC",
                "computed_at_utc": now.isoformat(),
                "component_count": 12,
                "blockers": [],
                "coverage_warnings": [],
                "source_age_seconds": 0.0,
                "target_refresh_seconds": 30.0,
            },
            {
                "distribution_snapshot_key": "stale-post-row",
                "phase": "post",
                "symbol": "BTC",
                "computed_at_utc": now.isoformat(),
                "component_count": 39,
                "blockers": [],
                "coverage_warnings": ["profile_distribution_source_stale"],
                "source_age_seconds": 120.0,
                "target_refresh_seconds": 30.0,
            },
        ],
        events=[],
    )

    profile_module = next(row for row in modules if row["data_block"] == "B")
    assert profile_module["status"] == "ready"
    assert profile_module["blockers"] == []
    assert profile_module["metrics"]["distribution_snapshot_rows"] == 3
    assert profile_module["metrics"]["current_distribution_snapshot_rows"] == 3
    assert profile_module["metrics"]["actionable_distribution_snapshot_rows"] == 2
    assert profile_module["metrics"]["rows_stale"] == 0


def test_control_center_source_modules_degrade_when_fallback_rows_are_stale_pytest() -> None:
    stale = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    modules = _merge_source_module_rows(
        [],
        crypto={
            "latest_prices": [{"symbol": "BTC", "observed_at_utc": stale}],
            "technicals": [{"symbol": "BTC", "completed_at_utc": stale}],
            "indicator_snapshots": [{"symbol": "BTC", "computed_at_utc": stale}],
        },
        profiles=[
            {
                "distribution_snapshot_key": "profile-row",
                "blockers": [],
                "computed_at_utc": stale,
                "source_age_seconds": 900.0,
                "target_refresh_seconds": 30.0,
            }
        ],
        events=[{"event_path_stats_key": "event-row", "snapshot_count": 0, "last_snapshot_at_utc": stale}],
    )

    by_block = {row["data_block"]: row for row in modules}
    assert by_block["A"]["status"] == "degraded"
    assert "stale_underlying_price_rows" in by_block["A"]["blockers"]
    assert "stale_technical_observer_rows" in by_block["A"]["blockers"]
    assert "stale_indicator_snapshot_rows" in by_block["A"]["blockers"]
    assert by_block["B"]["status"] == "degraded"
    assert by_block["B"]["blockers"] == ["profile_distribution_rows_stale"]
    assert by_block["B"]["metrics"]["rows_stale"] == 1
    assert by_block["C"]["status"] == "degraded"
    assert "stale_event_path_rows" in by_block["C"]["blockers"]
    assert "event_path_rows_missing_snapshot_count" in by_block["C"]["blockers"]


def test_control_center_source_modules_degrade_when_fallback_rows_mix_fresh_and_stale_pytest() -> None:
    now = datetime.now(UTC)
    stale = (now - timedelta(minutes=10)).isoformat()
    fresh = now.isoformat()
    modules = _merge_source_module_rows(
        [],
        crypto={
            "latest_prices": [
                {"symbol": "BTC", "observed_at_utc": fresh},
                {"symbol": "ETH", "observed_at_utc": stale},
            ],
            "technicals": [
                {"symbol": "BTC", "completed_at_utc": fresh},
                {"symbol": "ETH", "completed_at_utc": stale},
            ],
            "indicator_snapshots": [
                {"symbol": "BTC", "computed_at_utc": fresh},
                {"symbol": "ETH", "computed_at_utc": stale},
            ],
        },
        profiles=[],
        events=[
            {"event_path_stats_key": "fresh-event", "snapshot_count": 8, "last_snapshot_at_utc": fresh},
            {"event_path_stats_key": "stale-event", "snapshot_count": 6, "last_snapshot_at_utc": stale},
        ],
    )

    by_block = {row["data_block"]: row for row in modules}
    assert by_block["A"]["status"] == "degraded"
    assert "stale_underlying_price_rows" in by_block["A"]["blockers"]
    assert "stale_technical_observer_rows" in by_block["A"]["blockers"]
    assert "stale_indicator_snapshot_rows" in by_block["A"]["blockers"]
    assert by_block["A"]["metrics"]["stale_latest_price_rows"] == 1
    assert by_block["A"]["metrics"]["stale_technical_observer_rows"] == 1
    assert by_block["A"]["metrics"]["stale_indicator_snapshot_rows"] == 1
    assert by_block["C"]["status"] == "degraded"
    assert "stale_event_path_rows" in by_block["C"]["blockers"]
    assert by_block["C"]["metrics"]["rows_stale"] == 1


def test_control_center_module_block_summaries_normalize_a_b_c_status_pytest() -> None:
    summaries = _module_block_summaries(
        [
            {"data_block": "A", "status": "ready", "symbol": "BTC", "blockers": []},
            {"data_block": "A", "status": "healthy", "symbol": "ETH", "blockers": []},
            {"data_block": "B", "status": "degraded", "symbol": None, "blockers": ["profile_distribution_rows_stale"]},
        ]
    )

    assert summaries["A"]["status"] == "ready"
    assert summaries["A"]["module_count"] == 2
    assert summaries["A"]["detail"] == "2 modules (BTC/ETH)"
    assert summaries["B"]["status"] == "degraded"
    assert summaries["B"]["blockers"] == ["profile_distribution_rows_stale"]
    assert "blockers: profile_distribution_rows_stale" in summaries["B"]["detail"]
    assert summaries["C"]["status"] == "missing"
    assert summaries["C"]["detail"] == "no readiness row"


def test_control_center_state_exposes_module_block_summaries_for_frontend_cards_pytest(tmp_path) -> None:
    app = create_app(
        CryptoOptionsAppConfig(
            db_path=tmp_path / "control.sqlite",
            artifact_root=tmp_path / "artifacts",
        )
    )
    client = TestClient(app)

    response = client.get("/v1/crypto-options-app/dashboard/control-center-state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["module_blocks"]["A"]["status"] == "missing"
    assert payload["module_blocks"]["B"]["status"] == "missing"
    assert payload["module_blocks"]["C"]["status"] == "missing"


def test_control_center_event_rows_clamp_negative_latency_pytest(tmp_path) -> None:
    db_path = initialize_schema(tmp_path / "control-center-events.sqlite")
    now = "2026-06-06T09:05:32.496901+00:00"
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, event_start_time_utc, event_end_time_utc,
                source_table, source_json, inserted_at_utc, updated_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, '{}', ?, ?)
            """,
            (
                "event-1",
                "eth-updown-5m-1",
                "ETH",
                "2026-06-06T09:00:00+00:00",
                "2026-06-06T09:05:00+00:00",
                "polymarket_option_price_capture",
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO polymarket_event_path_stats(
                event_path_stats_key, event_key, event_slug, symbol,
                event_start_time_utc, event_end_time_utc, computed_at_utc,
                first_snapshot_at_utc, last_snapshot_at_utc, snapshot_count,
                avg_source_latency_ms, max_source_latency_ms,
                event_price_points_json, pre_event_price_points_json,
                level_first_touch_seconds_json, level_crossings_json, price_bucket_counts_json,
                trade_print_count, source_json, inserted_at_utc, updated_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', '{}', '{}', '{}', '{}', ?, '{}', ?, ?)
            """,
            (
                "event-path-1",
                "event-1",
                "eth-updown-5m-1",
                "ETH",
                "2026-06-06T09:00:00+00:00",
                "2026-06-06T09:05:00+00:00",
                now,
                "2026-06-06T09:00:00+00:00",
                "2026-06-06T09:05:00+00:00",
                25,
                -308.05176,
                1791.882,
                0,
                now,
                now,
            ),
        )

    app = create_app(CryptoOptionsAppConfig(db_path=db_path, artifact_root=tmp_path / "artifacts"))
    client = TestClient(app)

    response = client.get("/v1/crypto-options-app/dashboard/control-center-state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["events"][0]["avg_source_latency_ms"] == 0.0
    assert payload["events"][0]["max_source_latency_ms"] == 1791.882


def test_control_center_exposes_replay_candidate_cache_summary_pytest(tmp_path) -> None:
    db_path = initialize_schema(tmp_path / "control-center-replay-cache.sqlite")
    older = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    latest = datetime.now(UTC).isoformat()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO strategy_replay_candidate_cache_runs(
                candidate_cache_run_key, selector, strategy_signature,
                forward_mark_horizon_seconds, generated_at_utc, candidate_count,
                status, source_json
            )
            VALUES(
                'cache-run-empty', 'tail_touch_forward_edge_clean',
                'strategy-a,strategy-b', 60, ?,
                0, 'empty', '{}'
            )
            """,
            (older,),
        )
        conn.execute(
            """
            INSERT INTO strategy_replay_candidate_cache_runs(
                candidate_cache_run_key, selector, strategy_signature,
                forward_mark_horizon_seconds, generated_at_utc, candidate_count,
                status, source_json
            )
            VALUES(
                'cache-run-filled', 'tail_touch_forward_edge_clean',
                'strategy-a,strategy-b', 60, ?,
                2, 'ok', '{}'
            )
            """,
            (latest,),
        )
        conn.execute(
            """
            INSERT INTO strategy_replay_candidate_cache_runs(
                candidate_cache_run_key, selector, strategy_signature,
                forward_mark_horizon_seconds, generated_at_utc, candidate_count,
                status, source_json
            )
            VALUES(
                'cache-run-low-range', 'low_range_no_edge',
                'strategy-control', 60, ?,
                1, 'ok', '{}'
            )
            """,
            (latest,),
        )
        conn.execute(
            """
            INSERT INTO strategy_replay_candidate_scenarios(
                candidate_key, candidate_cache_run_key, selector, strategy_signature,
                event_key, event_token_key, token_id, event_slug, outcome,
                system_received_at_utc, best_bid, best_ask, spread,
                forward_best_bid, forward_mark_at_utc, forward_horizon_seconds,
                replay_tail_touch_count, replay_strong_rebounds, score,
                source_json, inserted_at_utc
            )
            VALUES(
                'candidate-1', 'cache-run-filled', 'tail_touch_forward_edge_clean',
                'strategy-a,strategy-b', 'event-1', 'event-1:up', 'token-1',
                'btc-updown-5m-cache', 'Up', '2026-06-06T16:49:00+00:00',
                0.04, 0.05, 0.01, 0.12, ?,
                60, 2, 1, 27, '{}', '2026-06-06T16:50:00+00:00'
            )
            """,
            (latest,),
        )

    app = create_app(CryptoOptionsAppConfig(db_path=db_path, artifact_root=tmp_path / "artifacts"))
    client = TestClient(app)

    response = client.get("/v1/crypto-options-app/dashboard/control-center-state")

    assert response.status_code == 200
    cache = response.json()["replay_candidate_cache"]
    assert cache["status"] == "ready"
    assert cache["run_count"] == 3
    assert cache["candidate_count"] == 3
    assert cache["selectors"]["tail_touch_forward_edge_clean"]["run_count"] == 2
    assert cache["selectors"]["tail_touch_forward_edge_clean"]["candidate_count"] == 2
    assert cache["selectors"]["low_range_no_edge"]["run_count"] == 1
    assert cache["selectors"]["low_range_no_edge"]["candidate_count"] == 1


def test_control_center_exposes_latest_replay_candidate_scout_artifact_pytest(tmp_path) -> None:
    db_path = initialize_schema(tmp_path / "control-center-replay-scout.sqlite")
    artifact_root = tmp_path / "artifacts"
    reports_root = artifact_root / "reports"
    reports_root.mkdir(parents=True)
    generated_at = datetime.now(UTC).isoformat()
    (reports_root / "strategy_replay_candidate_scout_latest.json").write_text(
        json.dumps(
            {
                "schema_version": "crypto_options_strategy_live_replay_candidate_scout_v1",
                "run_id": "strategy-live-replay-scout-pytest",
                "generated_at_utc": generated_at,
                "scenario_selector": "tail_touch_forward_edge_clean",
                "scenario_count": 2,
                "distinct_event_count": 2,
                "distinct_event_token_count": 2,
                "blockers": [],
                "orders_allowed": False,
                "live_trading_authorized": False,
                "manual_orders_avoided": True,
                "scenarios": [
                    {
                        "event_key": "event-1",
                        "event_slug": "btc-updown-5m-scout",
                        "event_token_key": "event-1:down",
                        "outcome": "Down",
                        "source": "polymarket_price_ticks_forward_mark_tail_touch_forward_edge_clean_cached",
                        "limit_price": 0.1,
                        "forward_mark_price": 0.15,
                        "time_remaining_seconds": 153.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    app = create_app(CryptoOptionsAppConfig(db_path=db_path, artifact_root=artifact_root))
    client = TestClient(app)

    response = client.get("/v1/crypto-options-app/dashboard/control-center-state")

    assert response.status_code == 200
    cache = response.json()["replay_candidate_cache"]
    assert cache["status"] == "ready"
    assert cache["candidate_count"] == 2
    assert cache["latest_scout"]["status"] == "ready"
    assert cache["latest_scout"]["candidate_count"] == 2
    assert cache["latest_scout"]["distinct_event_count"] == 2
    assert cache["latest_scout"]["scenario_selector"] == "tail_touch_forward_edge_clean"
    assert cache["selectors"]["tail_touch_forward_edge_clean"]["source"] == "latest_scout_artifact"


def test_control_center_exposes_selector_specific_replay_candidate_scouts_pytest(tmp_path) -> None:
    db_path = initialize_schema(tmp_path / "control-center-selector-scouts.sqlite")
    artifact_root = tmp_path / "artifacts"
    reports_root = artifact_root / "reports"
    reports_root.mkdir(parents=True)
    for selector, count in (("tail_touch_forward_edge_clean", 2), ("profile_group", 3)):
        (reports_root / f"strategy_replay_candidate_scout_{selector}_latest.json").write_text(
            json.dumps(
                {
                    "schema_version": "crypto_options_strategy_live_replay_candidate_scout_v1",
                    "run_id": f"scout-{selector}",
                    "generated_at_utc": datetime.now(UTC).isoformat(),
                    "scenario_selector": selector,
                    "scenario_count": count,
                    "distinct_event_count": count,
                    "distinct_event_token_count": count,
                    "blockers": [],
                    "orders_allowed": False,
                    "live_trading_authorized": False,
                    "manual_orders_avoided": True,
                    "scenarios": [],
                }
            ),
            encoding="utf-8",
        )

    app = create_app(CryptoOptionsAppConfig(db_path=db_path, artifact_root=artifact_root))
    client = TestClient(app)

    response = client.get("/v1/crypto-options-app/dashboard/control-center-state")

    assert response.status_code == 200
    cache = response.json()["replay_candidate_cache"]
    assert cache["status"] == "ready"
    assert cache["candidate_count"] == 5
    assert cache["selector_scouts"]["tail_touch_forward_edge_clean"]["candidate_count"] == 2
    assert cache["selector_scouts"]["profile_group"]["candidate_count"] == 3
    assert cache["selectors"]["tail_touch_forward_edge_clean"]["source"] == "latest_scout_artifact"
    assert cache["selectors"]["profile_group"]["source"] == "latest_scout_artifact"


def test_control_center_profile_distribution_rows_include_component_breakdown_pytest(tmp_path) -> None:
    db_path = initialize_schema(tmp_path / "dashboard-profile-breakdown.sqlite")
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO profile_distribution_snapshots(
                distribution_snapshot_key, event_key, event_slug, symbol, phase, computed_at_utc,
                event_start_time_utc, event_end_time_utc, source_mode, canonical_method,
                profile_count, component_count, up_weight, down_weight, up_share_weight,
                down_share_weight, up_cost_weight, down_cost_weight, up_count_weight,
                down_count_weight, distribution_json, source_json, blockers_json, inserted_at_utc
            )
            VALUES(
                'snapshot-1', 'event-1', 'btc-updown-5m-test', 'BTC', 'live',
                '2026-06-05T20:00:30+00:00', '2026-06-05T20:00:00+00:00',
                '2026-06-05T20:05:00+00:00', 'raw_activity', 'cost_weighted',
                2, 2, 20, 80, 40, 100, 20, 80, 1, 1, ?, ?,
                '[]', '2026-06-05T20:00:30+00:00'
            )
            """,
            (
                json.dumps(
                    {
                        "variants": {"cost_weighted": {"up": 0.2, "down": 0.8}},
                        "source_age_seconds": 4.0,
                        "target_refresh_seconds": 30.0,
                        "reconstructed_profile_prices": {"up": 0.5, "down": 0.8, "pair_sum": 1.3},
                    }
                ),
                json.dumps({"raw_activity_rows": 90, "position_rows": 0, "event_order_rows": 0}),
            ),
        )
        for key, grade, style, outcome, shares, cost in (
            ("component-up", "S++", "oneside", "Up", 40, 20),
            ("component-down", "S", "hedger", "Down", 100, 80),
        ):
            conn.execute(
                """
                INSERT INTO profile_distribution_components(
                    distribution_component_key, distribution_snapshot_key, profile_key,
                    handle, grade, trading_style, source_mode, outcome, net_shares,
                    net_notional_usd, cost_basis_usd, grade_weight, style_weight,
                    final_weight, contribution_json, inserted_at_utc
                )
                VALUES(?, 'snapshot-1', ?, ?, ?, ?, 'raw_activity', ?, ?, ?, ?, 1, 1, 1, '{}',
                       '2026-06-05T20:00:30+00:00')
                """,
                (key, key, key, grade, style, outcome, shares, cost, cost),
            )

        rows = _profile_distribution_rows(conn, limit=1)

    assert rows[0]["source"]["raw_activity_rows"] == 90
    assert rows[0]["source_age_seconds"] == 4.0
    assert rows[0]["target_refresh_seconds"] == 30.0
    breakdown = rows[0]["component_breakdown"]
    assert breakdown["schema_version"] == "crypto_options_profile_distribution_component_breakdown_v1"
    assert {row["label"] for row in breakdown["by_grade"]} == {"S++", "S"}
    assert {row["label"] for row in breakdown["by_style"]} == {"oneside", "hedger"}


def test_signal_selection_revision_candidates_are_not_pending_review_pytest() -> None:
    curated = _curate_signal_selection(
        [
            {
                "signal_id": "selected-profile-signal",
                "signal_type": "hedge_ratio",
                "promotion_state": "STRUCTURAL_PASS",
                "source_blocks": ["B"],
                "required_data_blocks": ["B"],
                "latest_hit_rate": 0.8,
                "impact_if_degraded": "critical",
                "distinct_event_count": 100,
            },
            {
                "signal_id": "reviewed-weak-crypto-signal",
                "signal_type": "outcome_prediction",
                "promotion_state": "NEEDS_V2_REVIEW",
                "source_blocks": ["A", "C"],
                "required_data_blocks": ["A", "C"],
                "latest_hit_rate": 0.45,
                "impact_if_degraded": "high",
                "distinct_event_count": 100,
            },
        ]
    )

    by_id = {row["signal_id"]: row for row in curated["signals"]}
    assert curated["review_count"] == 0
    assert curated["revision_candidate_count"] == 1
    assert by_id["reviewed-weak-crypto-signal"]["selection_tier"] == "revision_candidate"
    assert by_id["reviewed-weak-crypto-signal"]["action_state"] == "REVISION_REQUIRED"


def test_signal_selection_keeps_selected_passed_rows_in_strict_replay_pytest() -> None:
    curated = _curate_signal_selection(
        [
            {
                "signal_id": "selected-crypto-signal",
                "signal_type": "trend_regime",
                "promotion_state": "STRUCTURAL_PASS",
                "status": "PASSED",
                "queue_status": "PASSED",
                "source_blocks": ["A"],
                "required_data_blocks": ["A"],
                "latest_hit_rate": 0.82,
                "impact_if_degraded": "critical",
                "distinct_event_count": 90,
            }
        ]
    )

    row = curated["signals"][0]
    assert row["selection_tier"] == "selected"
    assert row["action_state"] == "STRICT_REPLAY_REQUIRED"
    assert "strict crypto-indicator replay" in row["next_action"]


def test_signal_selection_retires_superseded_profile_rows_from_primary_set_pytest() -> None:
    curated = _curate_signal_selection(
        [
            {
                "signal_id": "master_hedge_grid_scalping_outcome_prediction_profiles_splus_hedger_coherent_consensus_v4",
                "signal_type": "outcome_prediction",
                "variant": "splus_hedger_coherent_consensus",
                "promotion_state": "STRUCTURAL_PASS",
                "status": "PASSED",
                "queue_status": "PASSED",
                "source_blocks": ["B"],
                "required_data_blocks": ["B"],
                "live_shadow_hit_rate": 0.74,
                "impact_if_degraded": "critical",
                "distinct_event_count": 96,
            },
            {
                "signal_id": "master_hedge_grid_scalping_outcome_prediction_profiles_splus_hedger_subgroup_coherent_consensus_v5",
                "signal_type": "outcome_prediction",
                "variant": "splus_hedger_subgroup_coherent_consensus",
                "promotion_state": "STRUCTURAL_PASS",
                "status": "PASSED",
                "queue_status": "PASSED",
                "source_blocks": ["B"],
                "required_data_blocks": ["B"],
                "live_shadow_hit_rate": 0.76,
                "impact_if_degraded": "critical",
                "distinct_event_count": 102,
                "supersedes_signal_id": "master_hedge_grid_scalping_outcome_prediction_profiles_splus_hedger_coherent_consensus_v4",
            },
            {
                "signal_id": "master_hedge_grid_scalping_tail_reversal_probability_optionprice_profiles_tail_touch_subgroup_alignment_context_v3",
                "signal_type": "tail_reversal_probability",
                "variant": "tail_touch_subgroup_alignment_context",
                "promotion_state": "STRUCTURAL_PASS",
                "status": "PASSED",
                "queue_status": "PASSED",
                "source_blocks": ["B", "C"],
                "required_data_blocks": ["B", "C"],
                "live_shadow_hit_rate": 0.71,
                "impact_if_degraded": "critical",
                "distinct_event_count": 88,
            },
            {
                "signal_id": "master_hedge_grid_scalping_tail_reversal_probability_optionprice_profiles_tail_touch_profile_price_context_v4",
                "signal_type": "tail_reversal_probability",
                "variant": "tail_touch_profile_price_context",
                "promotion_state": "STRUCTURAL_PASS",
                "status": "PASSED",
                "queue_status": "PASSED",
                "source_blocks": ["B", "C"],
                "required_data_blocks": ["B", "C"],
                "live_shadow_hit_rate": 0.75,
                "impact_if_degraded": "critical",
                "distinct_event_count": 93,
                "supersedes_signal_id": "master_hedge_grid_scalping_tail_reversal_probability_optionprice_profiles_tail_touch_subgroup_alignment_context_v3",
            },
            {
                "signal_id": "master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_method_agreement_concentration_reference_v2",
                "signal_type": "profile_subgroup_distribution",
                "variant": "method_agreement_concentration_reference",
                "promotion_state": "STRUCTURAL_PASS",
                "status": "PASSED",
                "queue_status": "PASSED",
                "source_blocks": ["B", "C"],
                "required_data_blocks": ["B", "C"],
                "live_shadow_hit_rate": 0.69,
                "impact_if_degraded": "critical",
                "distinct_event_count": 84,
            },
            {
                "signal_id": "master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_profile_price_tail_context_reference_v3",
                "signal_type": "profile_subgroup_distribution",
                "variant": "profile_price_tail_context_reference",
                "promotion_state": "STRUCTURAL_PASS",
                "status": "PASSED",
                "queue_status": "PASSED",
                "source_blocks": ["B", "C"],
                "required_data_blocks": ["B", "C"],
                "live_shadow_hit_rate": 0.74,
                "impact_if_degraded": "critical",
                "distinct_event_count": 91,
                "supersedes_signal_id": "master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_method_agreement_concentration_reference_v2",
            },
            {
                "signal_id": "master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_profile_price_execution_context_reference_v4",
                "signal_type": "profile_subgroup_distribution",
                "variant": "profile_price_execution_context_reference",
                "promotion_state": "STRUCTURAL_PASS",
                "status": "PASSED",
                "queue_status": "PASSED",
                "source_blocks": ["B", "C"],
                "required_data_blocks": ["B", "C"],
                "live_shadow_hit_rate": 0.77,
                "impact_if_degraded": "critical",
                "distinct_event_count": 94,
                "supersedes_signal_id": "master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_profile_price_tail_context_reference_v3",
            },
        ]
    )

    by_id = {row["signal_id"]: row for row in curated["signals"]}
    superseded = by_id["master_hedge_grid_scalping_outcome_prediction_profiles_splus_hedger_coherent_consensus_v4"]
    superseding = by_id[
        "master_hedge_grid_scalping_outcome_prediction_profiles_splus_hedger_subgroup_coherent_consensus_v5"
    ]
    tail_superseded = by_id[
        "master_hedge_grid_scalping_tail_reversal_probability_optionprice_profiles_tail_touch_subgroup_alignment_context_v3"
    ]
    tail_superseding = by_id[
        "master_hedge_grid_scalping_tail_reversal_probability_optionprice_profiles_tail_touch_profile_price_context_v4"
    ]
    subgroup_distribution_superseded = by_id[
        "master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_method_agreement_concentration_reference_v2"
    ]
    subgroup_distribution_mid = by_id[
        "master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_profile_price_tail_context_reference_v3"
    ]
    subgroup_distribution_superseding = by_id[
        "master_hedge_grid_scalping_profile_subgroup_distribution_profiles_optionprice_profile_price_execution_context_reference_v4"
    ]

    assert curated["discarded_count"] == 4
    assert superseded["selection_tier"] == "discarded"
    assert superseded["action_state"] == "RETIRED_FROM_PRIMARY"
    assert superseding["selection_tier"] == "selected"
    assert "superseded by a stronger revision" in superseded["selection_reason"]
    assert superseded["superseded_by_signal_id"] == superseding["signal_id"]
    assert tail_superseded["selection_tier"] == "discarded"
    assert tail_superseded["action_state"] == "RETIRED_FROM_PRIMARY"
    assert tail_superseding["selection_tier"] == "selected"
    assert "superseded by a stronger revision" in tail_superseded["selection_reason"]
    assert tail_superseded["superseded_by_signal_id"] == tail_superseding["signal_id"]
    assert subgroup_distribution_superseded["selection_tier"] == "discarded"
    assert subgroup_distribution_superseded["action_state"] == "RETIRED_FROM_PRIMARY"
    assert subgroup_distribution_mid["selection_tier"] == "discarded"
    assert subgroup_distribution_mid["action_state"] == "RETIRED_FROM_PRIMARY"
    assert subgroup_distribution_superseding["selection_tier"] == "selected"
    assert "superseded by a stronger revision" in subgroup_distribution_superseded["selection_reason"]
    assert subgroup_distribution_superseded["superseded_by_signal_id"] == subgroup_distribution_superseding["signal_id"]
    assert "superseded by a stronger revision" in subgroup_distribution_mid["selection_reason"]
    assert subgroup_distribution_mid["superseded_by_signal_id"] == subgroup_distribution_superseding["signal_id"]


def test_crypto_options_app_builder_mounts_signal_catalog_routes_pytest(tmp_path) -> None:
    app = create_app(CryptoOptionsAppConfig(db_path=tmp_path / "signals.sqlite"))
    client = TestClient(app)

    response = client.get("/v1/crypto-options-app/signals/catalog")
    backtests = client.get("/v1/crypto-options-app/signals/backtests")
    validation_status = client.get("/v1/crypto-options-app/signals/validation/status")
    historical_replay = client.get("/v1/crypto-options-app/signals/replay/backtests")
    live_replay = client.get("/v1/crypto-options-app/signals/replay/live")

    assert response.status_code == 200
    assert backtests.status_code == 200
    assert validation_status.status_code == 200
    assert historical_replay.status_code == 200
    assert live_replay.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "crypto_options_signal_catalog_v1"
    assert payload["family"] == "master_hedge_grid_scalping"
    assert payload["signal_count"] >= 20
    assert payload["orders_allowed"] is False
    assert payload["live_trading_authorized"] is False
    assert payload["by_required_data_block"]["A"] > 0
    assert payload["by_required_data_block"]["B"] > 0
    assert payload["by_required_data_block"]["C"] > 0


def test_crypto_options_app_builder_mounts_strategy_lab_routes_pytest(tmp_path) -> None:
    app = create_app(CryptoOptionsAppConfig(db_path=tmp_path / "strategies.sqlite"))
    client = TestClient(app)

    catalog = client.get("/v1/crypto-options-app/strategies/catalog")
    readiness = client.get("/v1/crypto-options-app/strategies/readiness")
    validation_lab = client.get("/v1/crypto-options-app/strategies/validation-lab")
    promotion = client.get("/v1/crypto-options-app/strategies/promotion")
    historical_replay = client.get("/v1/crypto-options-app/strategies/replay/backtests")
    live_replay = client.get("/v1/crypto-options-app/strategies/replay/live")
    page = client.get("/v1/crypto-options-app/strategies/lab")

    assert catalog.status_code == 200
    assert readiness.status_code == 200
    assert validation_lab.status_code == 200
    assert promotion.status_code == 200
    assert historical_replay.status_code == 200
    assert live_replay.status_code == 200
    assert page.status_code == 200

    catalog_payload = catalog.json()
    readiness_payload = readiness.json()
    validation_lab_payload = validation_lab.json()
    promotion_payload = promotion.json()
    assert catalog_payload["schema_version"] == "crypto_options_strategy_catalog_v1"
    assert catalog_payload["strategy_count"] >= 20
    assert catalog_payload["orders_allowed"] is False
    assert catalog_payload["live_trading_authorized"] is False
    assert readiness_payload["schema_version"] == "crypto_options_strategy_readiness_v1"
    assert readiness_payload["strategy_count"] == catalog_payload["strategy_count"]
    assert "replay" in readiness_payload["by_readiness_type"]
    assert "pulse" in readiness_payload["by_readiness_type"]
    assert validation_lab_payload["schema_version"] == "crypto_options_strategy_validation_lab_v1"
    assert validation_lab_payload["strategy_validation_run_count"] == 0
    assert validation_lab_payload["validation_budget_ledger_count"] == 0
    assert validation_lab_payload["orders_allowed"] is False
    assert validation_lab_payload["live_trading_authorized"] is False
    assert promotion_payload["schema_version"] == "crypto_options_strategy_promotion_summary_v1"
    assert promotion_payload["strategy_count"] == catalog_payload["strategy_count"]
    assert promotion_payload["policy_contract"]["schema_version"] == "crypto_options_promotion_policy_contract_v1"
    assert promotion_payload["policy_contract"]["signals"]["promotable_state"] == "PROMOTION_READY"
    assert "PASSED" in promotion_payload["policy_contract"]["signals"]["not_promotable_labels"]
    live_requirements = promotion_payload["policy_contract"]["strategies"]["live_candidate_requirements"]
    assert live_requirements["recent_distinct_economic_samples"] == 12
    assert live_requirements["recent_shadow_live_win_rate_gt"] == 0.7
    assert promotion_payload["orders_allowed"] is False
    assert promotion_payload["live_trading_authorized"] is False
    assert historical_replay.json()["schema_version"] == "crypto_options_strategy_replay_summary_v1"
    assert live_replay.json()["schema_version"] == "crypto_options_strategy_replay_summary_v1"
    assert "Strategy Lab" in page.text
    assert "Budget Guardrail" in page.text
