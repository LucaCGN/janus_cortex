from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from crypto_options_app.config import CENTRAL_ARTIFACT_ROOT, CryptoOptionsAppConfig
from crypto_options_app.db.connection import connect_read_only, count_rows, table_exists
from crypto_options_app.db.postgres_connection import should_use_postgres_runtime
from crypto_options_app.db.postgres import CryptoOptionsPostgresSettings, check_postgres_connection
from crypto_options_app.db.postgres_shadow import load_postgres_shadow_parity_report
from crypto_options_app.db.schema import EXPECTED_TABLES
from crypto_options_app.feeds.polymarket_status import fetch_polymarket_status
from crypto_options_app.strategies.registry import all_strategy_specs
from crypto_options_app.workers.comparison_runner import build_core_flow_test_plan


HEALTH_SCHEMA_VERSION = "crypto_options_app_health_v2"
DEFAULT_ARTIFACT_ROOT = CENTRAL_ARTIFACT_ROOT
TRADING_LIFECYCLE_TABLES = (
    "strategy_candidates",
    "execution_intents",
    "orders",
    "fills",
    "positions",
    "exit_plans",
    "exit_orders",
    "settlements",
    "pnl_snapshots",
    "risk_gate_evaluations",
    "stop_gate_events",
    "system_status_snapshots",
    "run_reports",
)
STRATEGY_VALIDATION_TABLES = (
    "strategy_specs",
    "strategy_versions",
    "strategy_readiness",
    "strategy_promotion_state",
    "strategy_run_configs",
    "strategy_validation_runs",
    "validation_budget_ledger",
)
SIGNAL_VALIDATION_TABLES = (
    "signal_specs",
    "signal_versions",
    "signal_queue_items",
    "signal_validation_runs",
    "signal_validation_results",
    "signal_observations",
    "signal_artifacts",
)
DATA_SERVICE_TABLES = (
    "events",
    "event_tokens",
    "polymarket_price_ticks",
    "polymarket_order_books",
    "polymarket_order_book_levels",
    "polymarket_trade_prints",
    "polymarket_updown_pair_snapshots",
    "polymarket_event_path_stats",
    "underlying_price_ticks",
    "indicator_snapshots",
    "external_technical_observer_snapshots",
    "external_technical_observer_components",
    "profile_distribution_snapshots",
    "profile_distribution_components",
    "data_signal_readiness_snapshots",
)
DATA_SERVICE_WATERMARK_FRESHNESS: dict[str, dict[str, Any]] = {
    "underlying_market_prices": {"data_block": "A", "stale_after_seconds": 180, "critical_for_live_readiness": True},
    "underlying_technical_observers": {"data_block": "A", "stale_after_seconds": 180, "critical_for_live_readiness": True},
    "top_profiles_distribution": {"data_block": "B", "stale_after_seconds": 180, "critical_for_live_readiness": True},
    "polymarket_option_price_capture": {"data_block": "C", "stale_after_seconds": 180, "critical_for_live_readiness": True},
    "polymarket_live_activity_capture": {"data_block": "D", "stale_after_seconds": 300, "critical_for_live_readiness": True},
}
VALIDATION_ROW_AUDIT_FIELDS = (
    "strategy_id",
    "status",
    "event_key",
    "event_token_key",
    "token_id",
    "event_slug",
    "outcome",
    "order_key",
    "exchange_order_id",
    "order_status",
    "reconciliation_status",
    "live_submission_attempted",
)
MAX_HEALTH_ARTIFACT_SUMMARIES = 8
STATUS_FILE_READINESS_MODULES = (
    {
        "data_block": "A",
        "module_id": "underlying_market_prices",
        "watermark_module_id": "underlying_market_prices",
        "watermark_source": "service_status_file",
        "file_name": "underlying_market_price_capture_status.json",
        "payload_metrics": (
            "tick_rows_inserted",
            "candle_rows_inserted",
            "readiness_rows_inserted",
        ),
    },
    {
        "data_block": "A",
        "module_id": "underlying_technical_observers",
        "watermark_module_id": "underlying_technical_observers",
        "watermark_source": "service_status_file",
        "file_name": "underlying_technical_observers_status.json",
        "payload_metrics": (
            "snapshot_rows_inserted",
            "component_rows_inserted",
            "readiness_rows_inserted",
        ),
    },
    {
        "data_block": "B",
        "module_id": "profile_distribution_service",
        "watermark_module_id": "top_profiles_distribution",
        "watermark_source": "service_status_file",
        "file_name": "profile_distribution_status.json",
        "file_names": (
            "profile_distribution_service_status.json",
            "profile_distribution_status.json",
        ),
        "payload_metrics": (
            "snapshot_count",
            "snapshot_rows_inserted",
            "component_rows_inserted",
            "profile_count",
            "events_processed",
            "event_count",
        ),
    },
    {
        "data_block": "C",
        "module_id": "polymarket_option_price_capture",
        "watermark_module_id": "polymarket_option_price_capture",
        "watermark_source": "service_status_file",
        "file_name": "option_price_capture_status.json",
        "payload_metrics": (
            "target_count",
            "tick_rows_inserted",
            "book_level_rows_inserted",
            "pair_snapshot_rows_inserted",
            "event_path_stats_rows_upserted",
            "readiness_rows_inserted",
        ),
    },
    {
        "data_block": "D",
        "module_id": "polymarket_live_activity_capture",
        "watermark_module_id": "polymarket_live_activity_capture",
        "watermark_source": "service_status_file",
        "file_name": "market_activity_capture_status.json",
        "payload_metrics": (
            "condition_count",
            "trade_rows_inserted",
        ),
    },
)
STATUS_FILE_READINESS_DIRECTORIES = ("automation", "reports")


@dataclass(frozen=True)
class HealthBuildOptions:
    artifact_root: Path = DEFAULT_ARTIFACT_ROOT
    include_validation_artifacts: bool = True
    include_validation_rows: bool = False
    polymarket_status_provider: Callable[[], dict[str, Any]] | None = None
    external_status_timeout_seconds: float = 2.0
    db_report_cache_max_age_seconds: float = 120.0
    prefer_cached_db_report: bool = False


def build_system_integrity_health(
    config: CryptoOptionsAppConfig,
    *,
    options: HealthBuildOptions | None = None,
) -> dict[str, Any]:
    options = options or HealthBuildOptions()
    generated_at = datetime.now(UTC).isoformat()
    db_report = _db_report(
        config.db_path,
        artifact_root=options.artifact_root,
        cache_max_age_seconds=options.db_report_cache_max_age_seconds,
        prefer_cache=options.prefer_cached_db_report,
    )
    db_report = _overlay_status_file_readiness(db_report, artifact_root=options.artifact_root)
    postgres_report = _postgres_report(config, artifact_root=options.artifact_root)
    db_report = _apply_postgres_shadow_read_fallback(db_report, postgres_report=postgres_report)
    validation_budget_report = (
        db_report.get("validation_budget")
        if isinstance(db_report.get("validation_budget"), dict)
        else _default_validation_budget_report()
    )
    strategy_report = _strategy_registry_report()
    external_services_report = _external_services_report(
        options.polymarket_status_provider,
        timeout_seconds=options.external_status_timeout_seconds,
    )
    artifact_report = (
        _validation_artifact_report(
            options.artifact_root,
            include_validation_rows=options.include_validation_rows,
        )
        if options.include_validation_artifacts
        else {"enabled": False}
    )
    readiness_blockers = _readiness_blockers(
        db_report=db_report,
        artifact_report=artifact_report,
        external_services_report=external_services_report,
    )
    core_flow_plan = _core_flow_plan_report(db_report=db_report)
    status = "ok" if not readiness_blockers else "degraded"
    return {
        "schema_version": HEALTH_SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "service": config.app_name,
        "api_version": config.api_version,
        "status": status,
        "orders_allowed": False,
        "live_trading_authorized": False,
        "manual_orders_avoided": True,
        "env_live_flags": _live_env_flags(),
        "db": db_report,
        "postgres": postgres_report,
        "validation_budget": validation_budget_report,
        "centralized_paths": _centralized_path_report(config, options.artifact_root),
        "external_services": external_services_report,
        "strategies": strategy_report,
        "live_validation": artifact_report,
        "prepared_tests": {
            "core_flow": core_flow_plan,
        },
        "integrity": {
            "readiness_blockers": readiness_blockers,
            "strategy_bundle_comparison_may_begin": False,
            "core_flow_test_may_begin_after_operator_live_gate": core_flow_plan["may_begin_after_operator_live_gate"],
            "longer_live_tests_blocked_until_review": bool(readiness_blockers),
        },
    }


def _centralized_path_report(config: CryptoOptionsAppConfig, artifact_root: Path) -> dict[str, Any]:
    return {
        "app_root": "crypto_options_app",
        "db_path": str(config.db_path),
        "artifact_root": str(artifact_root),
        "active_profile_pool_path": str(config.active_profile_pool_path),
        "docs_root": str(config.docs_root),
        "source_of_truth": "crypto_options_app",
        "legacy_paths_compatibility_only": True,
    }


def _overlay_status_file_readiness(db_report: dict[str, Any], *, artifact_root: Path) -> dict[str, Any]:
    rows = _status_file_readiness_rows(artifact_root=artifact_root)
    if not rows:
        return db_report

    updated = dict(db_report)
    updated["status_file_readiness"] = rows
    updated["status_file_source_summary"] = _status_file_source_summary(rows)

    db_rows = updated.get("latest_data_signal_readiness")
    if not isinstance(db_rows, list):
        db_rows = []
    if updated.get("read_status") in {"blocked", "expired_fallback"} or not db_rows:
        updated["latest_data_signal_readiness"] = rows
        updated["latest_data_signal_readiness_source"] = "service_status_files"
    if updated.get("read_status") in {"blocked", "expired_fallback", "stale_fallback"}:
        updated["watermarks"] = _overlay_status_file_watermarks(
            updated.get("watermarks"),
            rows=rows,
        )
    return updated


def _status_file_readiness_rows(*, artifact_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    for module in STATUS_FILE_READINESS_MODULES:
        file_names = module.get("file_names") or (module["file_name"],)
        status_file = _resolve_status_file_payload(
            artifact_root=artifact_root,
            file_names=tuple(str(file_name) for file_name in file_names),
        )
        if status_file is None:
            continue
        status_path, payload = status_file
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        generated_at = payload.get("generated_at_utc") or summary.get("generated_at_utc")
        parsed_generated = _parse_utc(generated_at)
        source_age_seconds = (
            max(0.0, (now - parsed_generated).total_seconds())
            if parsed_generated is not None
            else None
        )
        blockers = payload.get("blockers")
        if blockers is None:
            blockers = summary.get("blockers")
        if not isinstance(blockers, list):
            blockers = []
        status = str(payload.get("status") or summary.get("status") or "unknown")
        metrics: dict[str, Any] = {}
        for key in module.get("payload_metrics", ()):
            if key in payload and payload.get(key) is not None:
                metrics[str(key)] = payload.get(key)
            elif key in summary and summary.get(key) is not None:
                metrics[str(key)] = summary.get(key)
        rows.append(
            {
                "data_block": module["data_block"],
                "module_id": module["module_id"],
                "symbol": None,
                "generated_at_utc": generated_at,
                "target_refresh_seconds": 30.0,
                "status": "ready" if status in {"healthy", "ready", "complete"} and not blockers else status,
                "latest_source_at_utc": generated_at,
                "source_age_seconds": source_age_seconds,
                "payload": {
                    "status_file": str(status_path),
                    "status_file_status": status,
                    "watermark_module_id": module.get("watermark_module_id"),
                    "watermark_source": module.get("watermark_source"),
                    "metrics": metrics,
                    "orders_allowed": bool(payload.get("orders_allowed", False)),
                    "live_trading_authorized": bool(payload.get("live_trading_authorized", False)),
                    "manual_orders_avoided": payload.get("manual_orders_avoided"),
                },
                "blockers": [str(blocker) for blocker in blockers],
                "source": "service_status_file",
            }
        )
    return rows


def _resolve_status_file_payload(*, artifact_root: Path, file_names: tuple[str, ...]) -> tuple[Path, dict[str, Any]] | None:
    candidates: list[tuple[Path, dict[str, Any]]] = []
    root = Path(artifact_root)
    for directory_name in STATUS_FILE_READINESS_DIRECTORIES:
        for file_name in file_names:
            candidate = root / directory_name / file_name
            payload = _load_json_if_exists(candidate)
            if payload:
                candidates.append((candidate, payload))
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            _parse_utc(item[1].get("generated_at_utc") or (item[1].get("summary") or {}).get("generated_at_utc"))
            or datetime.fromtimestamp(item[0].stat().st_mtime, tz=UTC),
            item[0].stat().st_mtime,
        )
    )
    return candidates[-1]


def _status_file_source_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_block: dict[str, dict[str, Any]] = {}
    for row in rows:
        block = str(row.get("data_block") or "?")
        entry = by_block.setdefault(block, {"module_count": 0, "ready_count": 0, "blockers": []})
        entry["module_count"] += 1
        if row.get("status") in {"ready", "healthy", "complete"}:
            entry["ready_count"] += 1
        entry["blockers"].extend(str(blocker) for blocker in row.get("blockers") or [])
    for entry in by_block.values():
        entry["blockers"] = sorted(set(entry["blockers"]))
        entry["status"] = (
            "ready"
            if entry["module_count"] > 0 and entry["ready_count"] == entry["module_count"] and not entry["blockers"]
            else "degraded"
        )
    return by_block


def _overlay_status_file_watermarks(
    current_watermarks: Any,
    *,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    watermarks = [dict(row) for row in current_watermarks] if isinstance(current_watermarks, list) else []
    by_module_id: dict[str, dict[str, Any]] = {}
    for watermark in watermarks:
        module_id = str(watermark.get("module_id") or "").strip()
        if module_id:
            by_module_id[module_id] = watermark

    for readiness_row in rows:
        module_id = _watermark_module_id_from_status_row(readiness_row)
        if not module_id:
            continue
        by_module_id[module_id] = _status_row_to_watermark(readiness_row, existing=by_module_id.get(module_id))

    merged = list(by_module_id.values())
    merged.sort(key=lambda item: _parse_utc(item.get("updated_at_utc")) or datetime.min.replace(tzinfo=UTC), reverse=True)
    return merged


def _watermark_module_id_from_status_row(row: dict[str, Any]) -> str:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return str(
        payload.get("watermark_module_id")
        or row.get("module_id")
        or ""
    ).strip()


def _status_row_to_watermark(
    row: dict[str, Any],
    *,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    summary = dict(existing or {})
    module_id = _watermark_module_id_from_status_row(row)
    generated_at = row.get("generated_at_utc") or summary.get("updated_at_utc")
    status = str(row.get("status") or summary.get("status") or "unknown")
    blockers = [str(blocker) for blocker in row.get("blockers") or []]
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    rows_observed = (
        metrics.get("snapshot_rows_inserted")
        if metrics.get("snapshot_rows_inserted") is not None
        else metrics.get("snapshot_count")
    )
    rows_inserted = (
        metrics.get("readiness_rows_inserted")
        if metrics.get("readiness_rows_inserted") is not None
        else metrics.get("tick_rows_inserted")
    )
    error_count = len(blockers)
    summary.update(
        {
            "service_name": "crypto_options_app",
            "module_id": module_id,
            "status": status,
            "rows_observed": rows_observed if rows_observed is not None else summary.get("rows_observed", 0),
            "rows_inserted": rows_inserted if rows_inserted is not None else summary.get("rows_inserted", 0),
            "error_count": error_count,
            "updated_at_utc": generated_at,
            "last_run_at_utc": generated_at,
            "source": payload.get("watermark_source") or summary.get("source") or "service_status_file",
        }
    )
    return summary


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _non_negative_latency_ms(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _postgres_report(
    config: CryptoOptionsAppConfig,
    *,
    artifact_root: Path = DEFAULT_ARTIFACT_ROOT,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "enabled": config.postgres_enabled,
        "active_backend": config.database_backend,
        "database_url_configured": bool(config.postgres_database_url),
        "role": "migration_target",
        "runtime_cutover_complete": False,
    }
    shadow_parity = load_postgres_shadow_parity_report(artifact_root=artifact_root)
    if shadow_parity is not None:
        report["shadow_parity"] = _compact_postgres_shadow_parity(shadow_parity)
    try:
        settings = CryptoOptionsPostgresSettings.from_url(config.postgres_database_url)
        settings = replace(settings, connect_timeout=1)
    except Exception as exc:  # noqa: BLE001 - health should report config errors.
        return {**report, "status": "blocked", "blockers": [f"postgres_config_error:{type(exc).__name__}:{exc}"]}

    connection = check_postgres_connection(settings)
    return {
        **report,
        "status": connection.get("status"),
        "host": settings.host,
        "port": settings.port,
        "database": settings.database,
        "connection": connection,
    }


def _compact_postgres_shadow_parity(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": report.get("schema_version"),
        "status": report.get("status"),
        "completed_at_utc": report.get("completed_at_utc"),
        "artifact_age_seconds": report.get("artifact_age_seconds"),
        "table_count": report.get("table_count"),
        "matched_table_count": report.get("matched_table_count"),
        "runtime_read_cutover_allowed": bool(report.get("runtime_read_cutover_allowed")),
        "blockers": list(report.get("blockers") or [])[:12],
        "lagging_tables": _compact_postgres_shadow_lagging_tables(report),
        "artifact_path": report.get("artifact_path"),
    }


def _compact_postgres_shadow_lagging_tables(report: dict[str, Any]) -> list[dict[str, Any]]:
    lagging: list[dict[str, Any]] = []
    for table in report.get("tables") or []:
        if table.get("status") == "ok":
            continue
        lagging.append(
            {
                "table": table.get("table"),
                "blockers": list(table.get("blockers") or [])[:6],
                "row_count_delta": table.get("row_count_delta"),
                "source_latest_timestamp": table.get("source_latest_timestamp"),
                "target_latest_timestamp": table.get("target_latest_timestamp"),
            }
        )
        if len(lagging) >= 6:
            break
    return lagging


def _apply_postgres_shadow_read_fallback(
    db_report: dict[str, Any],
    *,
    postgres_report: dict[str, Any],
) -> dict[str, Any]:
    if db_report.get("read_status") != "expired_fallback":
        return db_report
    shadow_parity = (
        postgres_report.get("shadow_parity")
        if isinstance(postgres_report.get("shadow_parity"), dict)
        else {}
    )
    if (
        postgres_report.get("status") == "ok"
        and shadow_parity.get("status") == "ok"
        and shadow_parity.get("runtime_read_cutover_allowed") is True
    ):
        updated = dict(db_report)
        updated["sqlite_read_status"] = "expired_fallback"
        updated["read_status"] = "postgres_shadow_fallback"
        updated["db_read_status"] = "postgres_shadow_fallback"
        updated["cache_status"] = "postgres_shadow_fallback"
        updated["postgres_shadow_read_available"] = True
        updated["postgres_shadow_parity_completed_at_utc"] = shadow_parity.get("completed_at_utc")
        updated.pop("stale_for_live_readiness", None)
        return updated
    if shadow_parity:
        updated = dict(db_report)
        updated["postgres_shadow_read_available"] = False
        updated["postgres_shadow_parity_status"] = shadow_parity.get("status")
        updated["postgres_shadow_runtime_cutover_allowed"] = bool(
            shadow_parity.get("runtime_read_cutover_allowed")
        )
        updated["postgres_shadow_parity_completed_at_utc"] = shadow_parity.get("completed_at_utc")
        updated["postgres_shadow_blockers"] = list(shadow_parity.get("blockers") or [])[:12]
        updated["postgres_shadow_lagging_tables"] = list(shadow_parity.get("lagging_tables") or [])[:6]
        return updated
    return db_report


def _db_report(
    db_path: Path,
    *,
    artifact_root: Path = DEFAULT_ARTIFACT_ROOT,
    cache_max_age_seconds: float = 120.0,
    prefer_cache: bool = False,
) -> dict[str, Any]:
    path = Path(db_path)
    postgres_runtime_expected = should_use_postgres_runtime(path)
    report: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists() or postgres_runtime_expected,
        "configured_sqlite_path": str(path),
        "configured_sqlite_path_exists": path.exists(),
        "postgres_runtime_expected": postgres_runtime_expected,
        "connection_backend": "unknown",
        "connection_is_postgres": False,
        "expected_table_count": len(EXPECTED_TABLES),
        "schema_status": "missing",
        "read_status": "missing",
        "missing_tables": sorted(EXPECTED_TABLES),
        "table_counts": {},
        "watermarks": [],
    }
    if not path.exists() and not postgres_runtime_expected:
        return report

    if prefer_cache:
        cached = _fresh_db_report_cache(
            artifact_root=artifact_root,
            cache_max_age_seconds=cache_max_age_seconds,
        )
        if cached is not None:
            return cached
        expired_cached = _expired_db_report_cache(
            artifact_root=artifact_root,
            cache_max_age_seconds=cache_max_age_seconds,
        )
        if expired_cached is not None:
            return expired_cached

    report["schema_status"] = "unverified"
    report["read_status"] = "unverified"
    report["missing_tables"] = []
    try:
        conn_context = connect_read_only(path)
    except Exception as exc:  # noqa: BLE001 - health should report DB monitor failures.
        return _db_report_with_cache_fallback(
            report,
            artifact_root=artifact_root,
            cache_max_age_seconds=cache_max_age_seconds,
            exc=exc,
        )

    try:
        with conn_context as conn:
            connection_is_postgres = bool(getattr(conn, "is_postgres", False))
            report["connection_is_postgres"] = connection_is_postgres
            report["connection_backend"] = "postgres" if connection_is_postgres else "sqlite"
            if connection_is_postgres:
                report["path_role"] = "sqlite_compatibility_path"
                report["runtime_source_of_truth"] = "postgres"
            else:
                report["path_role"] = "runtime_sqlite_path"
                report["runtime_source_of_truth"] = "sqlite"
            existing_tables = _existing_table_names(conn)
            report["missing_tables"] = sorted(EXPECTED_TABLES - existing_tables)
            report["schema_status"] = "incomplete" if report["missing_tables"] else "complete"
            report["read_status"] = "ok"
            report["unexpected_tables"] = sorted(existing_tables - EXPECTED_TABLES)
            report["table_counts"] = {
                table_name: _health_row_count(conn, table_name)
                for table_name in sorted(
                    set(TRADING_LIFECYCLE_TABLES + DATA_SERVICE_TABLES + STRATEGY_VALIDATION_TABLES + SIGNAL_VALIDATION_TABLES)
                )
                if table_exists(conn, table_name)
            }
            if table_exists(conn, "data_service_watermarks"):
                report["watermarks"] = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT service_name, module_id, status, rows_observed, rows_inserted,
                               error_count, updated_at_utc, last_run_at_utc, source
                        FROM data_service_watermarks
                        ORDER BY updated_at_utc DESC
                        LIMIT 50
                        """
                    ).fetchall()
                ]
                report["watermark_freshness"] = _watermark_freshness_report(report["watermarks"])
            if table_exists(conn, "polymarket_event_path_stats"):
                report["latest_completed_event_path_stats"] = _latest_completed_event_path_stats(conn)
            if table_exists(conn, "external_technical_observer_snapshots"):
                report["latest_external_technical_observers"] = _latest_external_technical_observers(conn)
            if table_exists(conn, "profile_distribution_snapshots"):
                report["latest_profile_distributions"] = _latest_profile_distributions(conn)
            if table_exists(conn, "data_signal_readiness_snapshots"):
                report["latest_data_signal_readiness"] = _latest_data_signal_readiness(conn)
            if table_exists(conn, "strategy_validation_runs") or table_exists(conn, "validation_budget_ledger"):
                report["validation_budget"] = _validation_budget_report(conn)
            if table_exists(conn, "signal_validation_runs") or table_exists(conn, "signal_validation_results"):
                report["signal_validation"] = _signal_validation_report(conn)
    except Exception as exc:  # noqa: BLE001 - health should report partial DB monitor failures.
        return _db_report_with_cache_fallback(
            report,
            artifact_root=artifact_root,
            cache_max_age_seconds=cache_max_age_seconds,
            exc=exc,
        )
    _write_db_report_cache(report, artifact_root=artifact_root)
    return report


def _existing_table_names(conn: Any) -> set[str]:
    if getattr(conn, "is_postgres", False):
        return {
            str(row["name"])
            for row in conn.execute(
                """
                SELECT table_name AS name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                """
            ).fetchall()
        }
    return {
        str(row["name"])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def _health_row_count(conn: Any, table_name: str) -> int:
    if getattr(conn, "is_postgres", False):
        exists_row = conn.execute(f"SELECT EXISTS(SELECT 1 FROM {table_name} LIMIT 1) AS has_rows").fetchone()
        has_rows = bool(exists_row["has_rows"] if exists_row is not None else False)
        estimate_row = conn.execute(
            "SELECT COALESCE((SELECT reltuples::bigint FROM pg_class WHERE oid = to_regclass(?)), -1) AS c",
            (table_name,),
        ).fetchone()
        estimate = int(estimate_row["c"] if estimate_row is not None else -1)
        if has_rows and estimate <= 0:
            return 1
        return estimate
    return count_rows(conn, table_name)


def _watermark_freshness_report(
    watermarks: list[dict[str, Any]],
    *,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    now = now_utc or datetime.now(UTC)
    by_module = {
        str(row.get("module_id") or ""): row
        for row in watermarks
        if str(row.get("module_id") or "")
    }
    if not by_module:
        return {
            "generated_at_utc": now.isoformat(),
            "status": "unobserved",
            "modules": [],
            "blockers": [],
        }

    modules: list[dict[str, Any]] = []
    blockers: list[str] = []
    for module_id, config in DATA_SERVICE_WATERMARK_FRESHNESS.items():
        watermark = by_module.get(module_id)
        stale_after_seconds = float(config["stale_after_seconds"])
        row_blockers: list[str] = []
        if watermark is None:
            status = "missing"
            age_seconds = None
            last_run_at_utc = None
            raw_status = "missing"
            if config.get("critical_for_live_readiness"):
                row_blockers.append(f"missing_data_service_watermark:{module_id}")
        else:
            raw_status = str(watermark.get("status") or "unknown")
            last_run_at_utc = watermark.get("last_run_at_utc") or watermark.get("updated_at_utc")
            parsed_last_run = _parse_utc(last_run_at_utc)
            age_seconds = None if parsed_last_run is None else max(0.0, (now - parsed_last_run).total_seconds())
            status = raw_status
            if age_seconds is None:
                status = "unknown"
                row_blockers.append(f"unknown_data_service_watermark_age:{module_id}")
            elif age_seconds > stale_after_seconds:
                status = "stale"
                row_blockers.append(f"stale_data_service_watermark:{module_id}")
            if raw_status in {"degraded", "failed", "stale"} and not row_blockers:
                row_blockers.append(f"{raw_status}_data_service_watermark:{module_id}")
        blockers.extend(row_blockers)
        modules.append(
            {
                "module_id": module_id,
                "data_block": config.get("data_block"),
                "status": status,
                "raw_status": raw_status,
                "age_seconds": age_seconds,
                "stale_after_seconds": stale_after_seconds,
                "last_run_at_utc": last_run_at_utc,
                "critical_for_live_readiness": bool(config.get("critical_for_live_readiness")),
                "blockers": row_blockers,
            }
        )

    return {
        "generated_at_utc": now.isoformat(),
        "status": "degraded" if blockers else "ok",
        "modules": modules,
        "blockers": sorted(set(blockers)),
    }


def _db_report_cache_path(*, artifact_root: Path) -> Path:
    return Path(artifact_root) / "reports" / "system_integrity_db_report_cache.json"


def _normalize_db_report_cache_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    generated_at = normalized.get("generated_at_utc") or normalized.get("cached_at_utc")
    if generated_at is not None:
        normalized["generated_at_utc"] = generated_at
    read_status = normalized.get("read_status") or normalized.get("db_read_status")
    if read_status is not None:
        normalized["read_status"] = read_status
        normalized["db_read_status"] = read_status
    return normalized


def _write_db_report_cache(report: dict[str, Any], *, artifact_root: Path) -> None:
    if report.get("read_status") != "ok":
        return
    cache_path = _db_report_cache_path(artifact_root=artifact_root)
    payload = _normalize_db_report_cache_payload(report | {
        "cache_status": "fresh",
        "cached_at_utc": datetime.now(UTC).isoformat(),
    })
    try:
        _write_json_atomically(cache_path, payload)
    except OSError:
        return


def _write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, default=str)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


def _fresh_db_report_cache(
    *,
    artifact_root: Path,
    cache_max_age_seconds: float,
) -> dict[str, Any] | None:
    cache_path = _db_report_cache_path(artifact_root=artifact_root)
    try:
        cached = _normalize_db_report_cache_payload(json.loads(cache_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    cached_at_raw = cached.get("cached_at_utc")
    if not cached_at_raw:
        return None
    try:
        cached_at = datetime.fromisoformat(str(cached_at_raw).replace("Z", "+00:00"))
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=UTC)
        cache_age_seconds = max(0.0, (datetime.now(UTC) - cached_at.astimezone(UTC)).total_seconds())
    except ValueError:
        return None
    if cache_age_seconds > cache_max_age_seconds:
        return None
    cached["cache_status"] = "fresh_cache"
    cached["cache_age_seconds"] = cache_age_seconds
    cached["cache_max_age_seconds"] = cache_max_age_seconds
    cached["db_read_status"] = cached.get("read_status")
    return cached


def _expired_db_report_cache(
    *,
    artifact_root: Path,
    cache_max_age_seconds: float,
) -> dict[str, Any] | None:
    cache_path = _db_report_cache_path(artifact_root=artifact_root)
    try:
        cached = _normalize_db_report_cache_payload(json.loads(cache_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    cached_at_raw = cached.get("cached_at_utc")
    if not cached_at_raw:
        return None
    try:
        cached_at = datetime.fromisoformat(str(cached_at_raw).replace("Z", "+00:00"))
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=UTC)
        cache_age_seconds = max(0.0, (datetime.now(UTC) - cached_at.astimezone(UTC)).total_seconds())
    except ValueError:
        return None
    if cache_age_seconds <= cache_max_age_seconds:
        return None
    cached["read_status"] = "expired_fallback"
    cached["db_read_status"] = "expired_fallback"
    cached["cache_status"] = "expired_fallback"
    cached["cache_age_seconds"] = cache_age_seconds
    cached["cache_max_age_seconds"] = cache_max_age_seconds
    cached["stale_for_live_readiness"] = True
    return cached


def _db_report_with_cache_fallback(
    report: dict[str, Any],
    *,
    artifact_root: Path,
    cache_max_age_seconds: float,
    exc: Exception,
) -> dict[str, Any]:
    read_error = f"{type(exc).__name__}:{exc}"
    cache_path = _db_report_cache_path(artifact_root=artifact_root)
    try:
        cached = _normalize_db_report_cache_payload(json.loads(cache_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        report["db_read_error"] = read_error
        report["schema_status"] = "read_unavailable"
        report["read_status"] = "blocked"
        report["cache_status"] = "empty"
        return report

    cached_at_raw = cached.get("cached_at_utc")
    cache_age_seconds: float | None = None
    if cached_at_raw:
        try:
            cached_at = datetime.fromisoformat(str(cached_at_raw).replace("Z", "+00:00"))
            if cached_at.tzinfo is None:
                cached_at = cached_at.replace(tzinfo=UTC)
            cache_age_seconds = max(0.0, (datetime.now(UTC) - cached_at.astimezone(UTC)).total_seconds())
        except ValueError:
            cache_age_seconds = None
    if cache_age_seconds is not None and cache_age_seconds <= cache_max_age_seconds:
        cached["db_read_error"] = read_error
        cached["read_status"] = "stale_fallback"
        cached["db_read_status"] = "stale_fallback"
        cached["cache_status"] = "stale_fallback"
        cached["cache_age_seconds"] = cache_age_seconds
        cached["cache_max_age_seconds"] = cache_max_age_seconds
        cached["orders_allowed"] = False
        return cached

    if cache_age_seconds is not None:
        cached["db_read_error"] = read_error
        cached["read_status"] = "expired_fallback"
        cached["db_read_status"] = "expired_fallback"
        cached["cache_status"] = "expired_fallback"
        cached["cache_age_seconds"] = cache_age_seconds
        cached["cache_max_age_seconds"] = cache_max_age_seconds
        cached["orders_allowed"] = False
        cached["live_trading_authorized"] = False
        cached["stale_for_live_readiness"] = True
        return cached

    report["db_read_error"] = read_error
    report["schema_status"] = "read_unavailable"
    report["read_status"] = "blocked"
    report["cache_status"] = "invalid"
    return report


def _latest_external_technical_observers(conn: Any, *, limit: int = 30) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT provider, symbol, interval, source_url, completed_at_utc,
               latency_ms, summary_label, summary_score, buy_count, sell_count,
               neutral_count, error_count, component_count
        FROM v_crypto_options_app_latest_external_technical_observers
        ORDER BY completed_at_utc DESC, provider, symbol, interval
        LIMIT ?
        """,
        (max(0, limit),),
    ).fetchall()
    decoded: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    for row in rows:
        item = dict(row)
        completed = item.get("completed_at_utc")
        if completed:
            try:
                parsed = datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                item["age_seconds"] = max(0.0, (now - parsed.astimezone(UTC)).total_seconds())
            except ValueError:
                item["age_seconds"] = None
        decoded.append(item)
    return decoded


def _latest_data_signal_readiness(conn: Any, *, limit: int = 30) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.data_block, r.module_id, r.symbol, r.generated_at_utc,
               r.target_refresh_seconds, r.status, r.latest_source_at_utc,
               r.source_age_seconds, r.payload_json, r.blockers_json
        FROM data_signal_readiness_snapshots r
        JOIN (
            SELECT data_block, module_id, MAX(generated_at_utc) AS latest_generated_at_utc
            FROM data_signal_readiness_snapshots
            GROUP BY data_block, module_id
        ) latest
          ON latest.data_block = r.data_block
         AND latest.module_id = r.module_id
         AND latest.latest_generated_at_utc = r.generated_at_utc
        ORDER BY r.data_block, r.module_id, COALESCE(r.symbol, '')
        LIMIT ?
        """,
        (max(0, limit),),
    ).fetchall()
    decoded: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for key, default in (("payload_json", {}), ("blockers_json", [])):
            target = key.removesuffix("_json")
            try:
                item[target] = json.loads(str(item.pop(key) or json.dumps(default)))
            except json.JSONDecodeError:
                item[target] = default
        item["source_age_seconds"] = _normalize_readiness_source_age_seconds(item)
        decoded.append(item)
    return decoded


def _normalize_readiness_source_age_seconds(row: dict[str, Any]) -> float | None:
    reported = row.get("source_age_seconds")
    try:
        reported_age = None if reported is None else float(reported)
    except (TypeError, ValueError):
        reported_age = None
    latest_source = _parse_utc(row.get("latest_source_at_utc"))
    if latest_source is None:
        return reported_age
    observed_age = max(0.0, (datetime.now(UTC) - latest_source).total_seconds())
    if reported_age is None:
        return observed_age
    return max(reported_age, observed_age)


def _latest_profile_distributions(conn: Any, *, limit: int = 12) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT event_key, event_slug, symbol, phase, computed_at_utc,
               event_start_time_utc, event_end_time_utc, source_mode,
               canonical_method, profile_count, component_count,
               up_weight, down_weight, distribution_json, blockers_json
        FROM profile_distribution_snapshots
        WHERE computed_at_utc = (
            SELECT MAX(computed_at_utc)
            FROM profile_distribution_snapshots
        )
        ORDER BY symbol, event_start_time_utc, event_slug
        LIMIT ?
        """,
        (max(0, limit),),
    ).fetchall()
    decoded: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    for row in rows:
        item = dict(row)
        for key, default in (("distribution_json", {}), ("blockers_json", [])):
            target = key.removesuffix("_json")
            try:
                item[target] = json.loads(str(item.pop(key) or json.dumps(default)))
            except json.JSONDecodeError:
                item[target] = default
        computed = item.get("computed_at_utc")
        if computed:
            try:
                parsed = datetime.fromisoformat(str(computed).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                item["age_seconds"] = max(0.0, (now - parsed.astimezone(UTC)).total_seconds())
            except ValueError:
                item["age_seconds"] = None
        decoded.append(item)
    return decoded


def _latest_completed_event_path_stats(conn: Any, *, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT event_slug, symbol, event_start_time_utc, event_end_time_utc,
               snapshot_count, up_first_price, up_last_price, up_min_price,
               up_max_price, up_range, up_abs_move_per_minute, up_stddev,
               event_price_points_json, pre_event_price_points_json,
               level_first_touch_seconds_json, path_direction, path_efficiency,
               time_to_first_extreme_seconds, avg_swing_distance, max_swing_distance,
               avg_rolling_30s_range, max_rolling_30s_range,
               avg_rolling_60s_range, max_rolling_60s_range,
               level_crossing_count, near_50c_sample_count, extreme_sample_count,
               rebound_direction_flip_count, strong_rebound_touch_count,
               pair_sum_range, avg_pair_depth_pressure, avg_source_latency_ms,
               max_source_latency_ms, trade_print_count, computed_at_utc
        FROM polymarket_event_path_stats
        WHERE event_end_time_utc IS NOT NULL
          AND event_end_time_utc <= ?
        ORDER BY event_end_time_utc DESC, symbol
        LIMIT ?
        """,
        (datetime.now(UTC).isoformat(), max(0, limit)),
    ).fetchall()
    decoded: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for key in ("event_price_points_json", "pre_event_price_points_json", "level_first_touch_seconds_json"):
            target = key.removesuffix("_json")
            try:
                item[target] = json.loads(str(item.pop(key) or "{}"))
            except json.JSONDecodeError:
                item[target] = {}
        item["avg_source_latency_ms"] = _non_negative_latency_ms(item.get("avg_source_latency_ms"))
        item["max_source_latency_ms"] = _non_negative_latency_ms(item.get("max_source_latency_ms"))
        decoded.append(item)
    return decoded


def _strategy_registry_report() -> dict[str, Any]:
    specs = all_strategy_specs()
    families = Counter(spec.strategy_family for spec in specs)
    return {
        "registered_count": len(specs),
        "strategy_ids": [spec.strategy_id for spec in specs],
        "families": dict(sorted(families.items())),
    }


def _validation_budget_report(conn: Any, *, ledger_limit: int = 10, run_limit: int = 10) -> dict[str, Any]:
    ledger_entries: list[dict[str, Any]] = []
    strategy_runs: list[dict[str, Any]] = []
    ledger_row_count = 0
    strategy_validation_run_count = 0
    supervised_live_run_count = 0
    ledger_required_run_without_entry_count = 0

    if table_exists(conn, "validation_budget_ledger"):
        ledger_row_count = count_rows(conn, "validation_budget_ledger")
        ledger_entries = [
            dict(row)
            for row in conn.execute(
                """
                SELECT validation_run_id, strategy_or_component_id, started_at_utc,
                       completed_at_utc, budget_cap_usd, notional_submitted_usd,
                       notional_filled_usd, realized_pnl_usd, open_cost_usd,
                       remaining_validation_budget_usd, cash_balance_before_usd,
                       cash_balance_after_usd, cash_balance_status,
                       hard_stop_triggered, stop_reason, lifecycle_audit_status,
                       reconciliation_status, updated_at_utc
                FROM validation_budget_ledger
                ORDER BY updated_at_utc DESC, inserted_at_utc DESC
                LIMIT ?
                """,
                (max(0, ledger_limit),),
            ).fetchall()
        ]

    if table_exists(conn, "strategy_validation_runs"):
        strategy_validation_run_count = count_rows(conn, "strategy_validation_runs")
        supervised_live_run_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM strategy_validation_runs WHERE lower(run_type) = 'supervised_live'"
            ).fetchone()[0]
        )
        strategy_runs = [
            dict(row)
            for row in conn.execute(
                """
                SELECT strategy_id, strategy_version, run_type, run_phase, run_status,
                       run_id, validation_run_id, max_notional_usd, max_events,
                       max_trades, max_wall_time_seconds, lifecycle_audit_status,
                       reconciliation_status, budget_ledger_required,
                       cash_balance_status, started_at_utc, completed_at_utc
                FROM strategy_validation_runs
                ORDER BY started_at_utc DESC, inserted_at_utc DESC
                LIMIT ?
                """,
                (max(0, run_limit),),
            ).fetchall()
        ]
        if table_exists(conn, "validation_budget_ledger"):
            ledger_required_run_without_entry_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM strategy_validation_runs r
                    LEFT JOIN validation_budget_ledger l
                      ON l.validation_run_id = r.validation_run_id
                    WHERE r.budget_ledger_required = 1
                      AND (
                        r.validation_run_id IS NULL
                        OR trim(r.validation_run_id) = ''
                        OR l.validation_run_id IS NULL
                      )
                    """
                ).fetchone()[0]
            )

    latest_ledger_entry = ledger_entries[0] if ledger_entries else None
    latest_strategy_validation_run = strategy_runs[0] if strategy_runs else None
    return {
        "schema_version": "crypto_options_validation_budget_v1",
        "validation_budget_cap_usd": 50.0,
        "cash_balance_hard_stop_usd": 100.0,
        "ledger_row_count": ledger_row_count,
        "strategy_validation_run_count": strategy_validation_run_count,
        "supervised_live_run_count": supervised_live_run_count,
        "ledger_required_run_without_entry_count": ledger_required_run_without_entry_count,
        "latest_ledger_entry": latest_ledger_entry,
        "latest_strategy_validation_run": latest_strategy_validation_run,
        "cash_balance_status": (
            latest_ledger_entry.get("cash_balance_status")
            if isinstance(latest_ledger_entry, dict)
            else "cash_balance_unavailable"
        ),
        "ledger_entries": ledger_entries,
        "strategy_validation_runs": strategy_runs,
    }


def _signal_validation_report(conn: Any, *, orphan_limit: int = 25) -> dict[str, Any]:
    signal_validation_run_count = count_rows(conn, "signal_validation_runs")
    signal_validation_result_count = count_rows(conn, "signal_validation_results")
    latest_orphaned_runs: list[dict[str, Any]] = []
    orphaned_run_count = 0
    if table_exists(conn, "signal_validation_runs") and table_exists(conn, "signal_validation_results"):
        orphaned_run_count = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM signal_validation_runs r
                LEFT JOIN signal_validation_results vr
                  ON vr.validation_run_key = r.validation_run_key
                WHERE vr.validation_run_key IS NULL
                """
            ).fetchone()[0]
        )
        latest_orphaned_runs = [
            dict(row)
            for row in conn.execute(
                """
                SELECT r.validation_run_key, r.signal_id, r.version, r.phase, r.status, r.started_at_utc
                FROM signal_validation_runs r
                LEFT JOIN signal_validation_results vr
                  ON vr.validation_run_key = r.validation_run_key
                WHERE vr.validation_run_key IS NULL
                ORDER BY r.started_at_utc ASC, r.validation_run_key ASC
                LIMIT ?
                """,
                (max(0, orphan_limit),),
            ).fetchall()
        ]
    return {
        "schema_version": "crypto_options_signal_validation_integrity_v1",
        "signal_validation_run_count": signal_validation_run_count,
        "signal_validation_result_count": signal_validation_result_count,
        "orphaned_run_count": orphaned_run_count,
        "latest_orphaned_runs": latest_orphaned_runs,
    }


def _default_validation_budget_report() -> dict[str, Any]:
    return {
        "schema_version": "crypto_options_validation_budget_v1",
        "validation_budget_cap_usd": 50.0,
        "cash_balance_hard_stop_usd": 100.0,
        "ledger_row_count": 0,
        "strategy_validation_run_count": 0,
        "supervised_live_run_count": 0,
        "ledger_required_run_without_entry_count": 0,
        "latest_ledger_entry": None,
        "latest_strategy_validation_run": None,
        "cash_balance_status": "cash_balance_unavailable",
        "ledger_entries": [],
        "strategy_validation_runs": [],
    }


def _external_services_report(provider: Callable[[], dict[str, Any]] | None, *, timeout_seconds: float) -> dict[str, Any]:
    actual_provider = provider or fetch_polymarket_status
    executor: ThreadPoolExecutor | None = None
    try:
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(actual_provider)
        polymarket = future.result(timeout=max(0.0, timeout_seconds))
    except FutureTimeoutError:
        polymarket = {
            "schema_version": "polymarket_status_v1",
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "source": "polymarket_status_api",
            "status": "unknown",
            "clob_api": {"status": "unknown", "trading_available": False},
            "blockers": ["polymarket_status_timeout"],
            "warnings": ["polymarket_status_unavailable_requires_per_order_verified_market"],
            "error": f"TimeoutError:external status exceeded {timeout_seconds:.2f}s",
        }
    except Exception as exc:  # noqa: BLE001 - health must surface provider failure.
        polymarket = {
            "schema_version": "polymarket_status_v1",
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "source": "polymarket_status_api",
            "status": "unknown",
            "clob_api": {"status": "unknown", "trading_available": False},
            "blockers": ["polymarket_status_unavailable"],
            "error": f"{type(exc).__name__}:{exc}",
        }
    finally:
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
    return {
        "polymarket": polymarket,
    }


def _validation_artifact_report(artifact_root: Path, *, include_validation_rows: bool = False) -> dict[str, Any]:
    root = Path(artifact_root)
    validation_dir = root / "live-validation"
    report_dir = root / "reports"
    automation_state_path = root / "automation" / "run_state.json"
    latest_order_audit = _summarize_order_audit(_load_json_if_exists(report_dir / "live_order_integrity_audit_latest.json"))
    all_artifacts = sorted(validation_dir.glob("*.json"), key=lambda path: path.stat().st_mtime) if validation_dir.exists() else []
    artifacts = all_artifacts[-MAX_HEALTH_ARTIFACT_SUMMARIES:]
    summaries = [_summarize_validation_artifact(path, include_rows=include_validation_rows) for path in artifacts]
    successful_strategy_ids = sorted(
        {
            strategy_id
            for summary in summaries
            for strategy_id in summary.get("successful_strategy_ids", [])
            if strategy_id
        }
    )
    missing_audit_rows = [
        row
        for summary in summaries
        for row in summary.get("audit_missing_fields_by_row", [])
        if row.get("missing_fields")
    ]
    latest_summary = summaries[-1] if summaries else {}
    latest_missing_audit_rows = [
        row
        for row in latest_summary.get("audit_missing_fields_by_row", [])
        if row.get("missing_fields")
    ]
    latest_report = _latest_file(report_dir, "*.md")
    latest_settlement_performance = _summarize_settlement_performance(
        _load_json_if_exists(report_dir / "settlement_performance_latest.json")
    )
    return {
        "artifact_root": str(root),
        "validation_artifact_count": len(all_artifacts),
        "health_artifact_summary_count": len(artifacts),
        "health_artifact_summary_limit": MAX_HEALTH_ARTIFACT_SUMMARIES,
        "latest_validation_artifact": None if not all_artifacts else str(all_artifacts[-1]),
        "latest_report": None if latest_report is None else str(latest_report),
        "latest_order_integrity_audit": latest_order_audit,
        "latest_settlement_performance": latest_settlement_performance,
        "automation_state": _load_json_if_exists(automation_state_path),
        "successful_strategy_count": len(successful_strategy_ids),
        "successful_strategy_ids": successful_strategy_ids,
        "audit_missing_field_row_count": len(missing_audit_rows),
        "audit_missing_fields_by_row": missing_audit_rows[:50],
        "latest_audit_missing_field_row_count": len(latest_missing_audit_rows),
        "latest_audit_missing_fields_by_row": latest_missing_audit_rows[:50],
        "artifacts": summaries,
    }


def _summarize_validation_artifact(path: Path, *, include_rows: bool = False) -> dict[str, Any]:
    payload = _load_json_if_exists(path) or {}
    rows = payload.get("strategy_rows") if isinstance(payload.get("strategy_rows"), list) else []
    row_summaries: list[dict[str, Any]] = []
    audit_missing: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    blocker_counts: Counter[str] = Counter()
    successful_strategy_ids: set[str] = set()
    has_filled_lifecycle_rows = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "unknown")
        status_counts[status] += 1
        blockers = [str(blocker) for blocker in row.get("blockers") or []]
        blocker_counts.update(blockers)
        strategy_id = row.get("strategy_id")
        if strategy_id and status in {"executed", "live_structural_executed"} and not blockers:
            successful_strategy_ids.add(str(strategy_id))
        if (
            status in {"executed", "live_structural_executed"}
            and row.get("order_status") == "filled"
            and row.get("lifecycle_covered") is True
        ):
            has_filled_lifecycle_rows = True
        row_summary = {
            "strategy_id": row.get("strategy_id"),
            "status": status,
            "blockers": blockers,
            "event_key": row.get("event_key"),
            "event_token_key": row.get("event_token_key"),
            "token_id": row.get("token_id"),
            "event_slug": row.get("event_slug"),
            "outcome": row.get("outcome"),
            "order_key": row.get("order_key"),
            "exchange_order_id": row.get("exchange_order_id"),
            "order_status": row.get("order_status"),
            "filled_shares": row.get("filled_shares"),
            "fill_price": row.get("fill_price"),
            "reconciliation_status": row.get("reconciliation_status"),
            "lifecycle_covered": row.get("lifecycle_covered"),
            "live_submission_attempted": row.get("live_submission_attempted"),
        }
        if include_rows:
            row_summaries.append(row_summary)
        should_have_exchange_audit_fields = (
            status in {"executed", "live_structural_executed"}
            and not blockers
            and bool(row.get("live_submission_attempted"))
        )
        missing = [field for field in VALIDATION_ROW_AUDIT_FIELDS if row.get(field) in {None, ""}] if should_have_exchange_audit_fields else []
        if missing:
            audit_missing.append({"strategy_id": row.get("strategy_id"), "status": status, "missing_fields": missing})
    return {
        "path": str(path),
        "generated_at_utc": payload.get("generated_at_utc"),
        "run_id": payload.get("run_id"),
        "strategy_row_count": len([row for row in rows if isinstance(row, dict)]),
        "status_counts": dict(sorted(status_counts.items())),
        "blocker_counts": dict(sorted(blocker_counts.items())),
        "manual_orders_avoided": payload.get("manual_orders_avoided"),
        "live_submission_attempted": payload.get("live_submission_attempted"),
        "checked_target_count": payload.get("checked_target_count"),
        "verified_candidate_count": payload.get("verified_candidate_count"),
        "successful_strategy_ids": sorted(successful_strategy_ids),
        "has_filled_lifecycle_rows": has_filled_lifecycle_rows,
        "strategy_rows": row_summaries,
        "audit_missing_fields_by_row": audit_missing,
    }


def _summarize_order_audit(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    audit = payload.get("audit") if isinstance(payload.get("audit"), dict) else {}
    summary = audit.get("summary") if isinstance(audit.get("summary"), dict) else {}
    recorded_status_counts = summary.get("recorded_status_counts") if isinstance(summary.get("recorded_status_counts"), dict) else {}
    scoped_exchange_side_counts = (
        summary.get("scoped_exchange_side_counts") if isinstance(summary.get("scoped_exchange_side_counts"), dict) else {}
    )
    return {
        "schema_version": payload.get("schema_version"),
        "generated_at_utc": payload.get("generated_at_utc"),
        "status": payload.get("status") or audit.get("status"),
        "audit_attempt_count": payload.get("audit_attempt_count"),
        "audit": {
            "schema_version": audit.get("schema_version"),
            "status": audit.get("status"),
            "blockers": audit.get("blockers") or [],
            "recorded_successful_buy_count": audit.get("recorded_successful_buy_count"),
            "exchange_buy_count": audit.get("exchange_buy_count"),
            "strong_match_count": audit.get("strong_match_count"),
            "weak_match_count": audit.get("weak_match_count"),
            "unmatched_recorded_count": audit.get("unmatched_recorded_count"),
            "unmatched_exchange_buy_count": audit.get("unmatched_exchange_buy_count"),
            "unexpected_exchange_sell_count": audit.get("unexpected_exchange_sell_count"),
            "summary": {
                "recorded_status_counts": recorded_status_counts,
                "recorded_unfilled_buy_count": summary.get("recorded_unfilled_buy_count"),
                "scoped_exchange_side_counts": scoped_exchange_side_counts,
                "fill_mismatch_count": len(summary.get("fill_mismatches") or []),
                "external_exchange_sell_count": len(summary.get("external_exchange_sells_in_validation_scope") or []),
            },
        },
    }


def _summarize_settlement_performance(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    return {
        "schema_version": payload.get("schema_version"),
        "generated_at_utc": payload.get("generated_at_utc"),
        "run_id": payload.get("run_id"),
        "summary": payload.get("summary") if isinstance(payload.get("summary"), dict) else {},
        "blockers": payload.get("blockers") or [],
        "db_counts": payload.get("db_counts") if isinstance(payload.get("db_counts"), dict) else {},
        "row_count": len(rows),
        "manual_orders_avoided": payload.get("manual_orders_avoided"),
    }


def _readiness_blockers(
    *,
    db_report: dict[str, Any],
    artifact_report: dict[str, Any],
    external_services_report: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    polymarket = external_services_report.get("polymarket") if isinstance(external_services_report.get("polymarket"), dict) else {}
    clob = polymarket.get("clob_api") if isinstance(polymarket.get("clob_api"), dict) else {}
    status_blockers = {str(blocker) for blocker in polymarket.get("blockers") or []}
    status_unavailable_only = bool(status_blockers) and status_blockers <= {
        "polymarket_status_timeout",
        "polymarket_status_unavailable",
    }
    if not clob.get("trading_available") and not status_unavailable_only:
        blockers.append("polymarket_clob_trading_unavailable")
    for blocker in polymarket.get("blockers") or []:
        if str(blocker) in {"polymarket_status_timeout", "polymarket_status_unavailable"}:
            continue
        blockers.append(f"polymarket_status:{blocker}")
    if not db_report.get("exists"):
        blockers.append("canonical_db_missing")
    if db_report.get("read_status") == "blocked":
        blockers.append("canonical_db_read_unavailable")
    if db_report.get("read_status") == "expired_fallback":
        blockers.append("canonical_db_report_cache_expired")
    if db_report.get("schema_status") == "incomplete" or db_report.get("missing_tables"):
        blockers.append("canonical_db_schema_incomplete")
    table_counts = db_report.get("table_counts") if isinstance(db_report.get("table_counts"), dict) else {}
    validation_budget = db_report.get("validation_budget") if isinstance(db_report.get("validation_budget"), dict) else {}
    signal_validation = db_report.get("signal_validation") if isinstance(db_report.get("signal_validation"), dict) else {}
    watermark_freshness = db_report.get("watermark_freshness") if isinstance(db_report.get("watermark_freshness"), dict) else {}
    for blocker in watermark_freshness.get("blockers") or []:
        blockers.append(f"data_service:{blocker}")
    if artifact_report.get("successful_strategy_count", 0) > 0 and not any(
        int(table_counts.get(table_name, 0)) > 0
        for table_name in ("strategy_candidates", "execution_intents", "orders", "fills", "positions", "run_reports")
    ):
        blockers.append("live_validation_not_persisted_to_canonical_db")
    if artifact_report.get("validation_artifact_count", 0) <= 0:
        blockers.append("missing_live_validation_artifact")
    if artifact_report.get("validation_artifact_count", 0) > 0 and int(validation_budget.get("ledger_row_count") or 0) <= 0:
        blockers.append("historical_live_validation_without_budget_ledger")
    if artifact_report.get("latest_audit_missing_field_row_count", 0) > 0:
        blockers.append("validation_artifact_missing_exchange_audit_fields")
    if int(validation_budget.get("ledger_required_run_without_entry_count") or 0) > 0:
        blockers.append("strategy_validation_run_missing_budget_ledger")
    if int(signal_validation.get("orphaned_run_count") or 0) > 0:
        blockers.append("signal_validation_run_missing_result")
    latest_order_audit = artifact_report.get("latest_order_integrity_audit")
    if isinstance(latest_order_audit, dict):
        audit = latest_order_audit.get("audit") if isinstance(latest_order_audit.get("audit"), dict) else {}
        for blocker in audit.get("blockers") or []:
            blockers.append(f"order_integrity:{blocker}")
    latest_summary = {}
    artifacts = artifact_report.get("artifacts")
    if isinstance(artifacts, list) and artifacts:
        latest_summary = artifacts[-1] if isinstance(artifacts[-1], dict) else {}
    latest_run_id = latest_summary.get("run_id") if isinstance(latest_summary, dict) else None
    latest_has_fills = bool(latest_summary.get("has_filled_lifecycle_rows"))
    settlement_report = artifact_report.get("latest_settlement_performance")
    settlement_run_id = settlement_report.get("run_id") if isinstance(settlement_report, dict) else None
    if latest_has_fills and latest_run_id and settlement_run_id != latest_run_id:
        blockers.append("latest_live_run_missing_settlement_performance")
    if int(artifact_report.get("successful_strategy_count") or 0) < 10:
        blockers.append("fewer_than_10_successful_live_structural_strategy_artifacts")
    return blockers


def _core_flow_plan_report(*, db_report: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    if not db_report.get("exists"):
        blockers.append("canonical_db_missing")
    if db_report.get("read_status") == "blocked":
        blockers.append("canonical_db_read_unavailable")
    if db_report.get("read_status") == "expired_fallback":
        blockers.append("canonical_db_report_cache_expired")
    if db_report.get("schema_status") == "incomplete" or db_report.get("missing_tables"):
        blockers.append("canonical_db_schema_incomplete")
    try:
        plan = build_core_flow_test_plan()
        group = plan.groups[0]
        constraints = [
            {
                "strategy_id": constraint.strategy_id,
                "first_run_sizing": constraint.first_run_sizing,
                "low_volume_event_target": constraint.low_volume_event_target,
                "hard_event_cap": constraint.hard_event_cap,
                "hard_time_limit_seconds": constraint.hard_time_limit_seconds,
                "total_budget_cap_usd": constraint.total_budget_cap_usd,
            }
            for constraint in group.constraints
        ]
    except Exception as exc:  # noqa: BLE001 - health must surface readiness failure, not crash.
        blockers.append(f"core_flow_plan_error:{type(exc).__name__}")
        group = None
        constraints = []
    return {
        "plan_id": None if group is None else plan.plan_id,
        "group_id": None if group is None else group.group_id,
        "strategy_ids": [] if group is None else list(group.strategy_ids),
        "constraints": constraints,
        "max_events": 3,
        "hard_time_limit_seconds": 900,
        "total_budget_cap_usd": 10.0,
        "sizing": "minimal",
        "may_begin_after_operator_live_gate": not blockers,
        "blockers": blockers,
    }


def _live_env_flags() -> dict[str, bool]:
    return {
        "JANUS_CRYPTO_OPTIONS_LIVE_EXECUTE": os.getenv("JANUS_CRYPTO_OPTIONS_LIVE_EXECUTE") == "1",
        "JANUS_CRYPTO_OPTIONS_LIVE_APPROVED": os.getenv("JANUS_CRYPTO_OPTIONS_LIVE_APPROVED") == "1",
        "JANUS_CRYPTO_OPTIONS_ACK_LIVE_RISK": os.getenv("JANUS_CRYPTO_OPTIONS_ACK_LIVE_RISK") == "1",
    }


def _latest_file(root: Path, pattern: str) -> Path | None:
    if not root.exists():
        return None
    matches = sorted(root.glob(pattern), key=lambda path: path.stat().st_mtime)
    return matches[-1] if matches else None


def _load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
