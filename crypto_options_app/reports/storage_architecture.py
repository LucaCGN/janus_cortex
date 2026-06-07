from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from crypto_options_app.cache.redis_hot_plane import check_redis_hot_plane
from crypto_options_app.config import DEFAULT_CONFIG
from crypto_options_app.db.connection import connect_read_only
from crypto_options_app.reports.runtime_audit import (
    RuntimeAuditOptions,
    build_runtime_audit,
)


STORAGE_AUDIT_SCHEMA_VERSION = "crypto_options_storage_architecture_audit_v1"


@dataclass(frozen=True)
class StorageArchitectureAuditOptions:
    artifact_root: Path = DEFAULT_CONFIG.artifact_root
    backend_base_url: str = "http://127.0.0.1:8011/v1/crypto-options-app"
    include_runtime_audit: bool = True
    include_endpoint_timings: bool = True
    include_postgres_diagnostics: bool = True
    endpoint_timeout_seconds: float = 8.0


def build_storage_architecture_audit(
    options: StorageArchitectureAuditOptions | None = None,
) -> dict[str, Any]:
    options = options or StorageArchitectureAuditOptions()
    runtime = (
        build_runtime_audit(
            RuntimeAuditOptions(
                backend_url=f"{options.backend_base_url}/health",
                artifact_root=options.artifact_root,
                check_docker=True,
                check_endpoints=False,
                include_processes=False,
            )
        )
        if options.include_runtime_audit
        else {"skipped": True}
    )
    endpoints = (
        _endpoint_timing_audit(options)
        if options.include_endpoint_timings
        else {"skipped": True, "endpoints": {}}
    )
    postgres_diagnostics = (
        _postgres_diagnostics()
        if options.include_postgres_diagnostics
        else {"skipped": True}
    )
    redis = check_redis_hot_plane()
    signals = _decision_signals(
        runtime=runtime,
        endpoints=endpoints,
        postgres_diagnostics=postgres_diagnostics,
    )
    decision = decide_storage_architecture(signals)
    blockers = list(signals.get("blockers") or [])
    warnings = list(signals.get("warnings") or [])
    return {
        "schema_version": STORAGE_AUDIT_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "status": "blocked" if blockers else "degraded" if warnings else "ok",
        "decision": decision,
        "signals": signals,
        "runtime": _compact_runtime(runtime),
        "endpoint_timings": endpoints,
        "postgres_diagnostics": postgres_diagnostics,
        "redis_hot_plane": redis,
        "postgres_role": "durable_source_of_truth",
        "redis_role": "gated_hot_plane_candidate",
        "redis_must_not_store_authoritative_trading_truth": True,
        "manual_orders_avoided": True,
        "live_trading_authorized": False,
    }


def decide_storage_architecture(signals: dict[str, Any]) -> dict[str, Any]:
    blockers = list(signals.get("blockers") or [])
    hot_plane_reasons = list(signals.get("hot_plane_reasons") or [])
    query_reasons = list(signals.get("query_layer_reasons") or [])
    if blockers:
        return {
            "decision": "defer_storage_change_until_runtime_stable",
            "postgres": "required_durable_source_of_truth",
            "redis": "do_not_enable_while_blocked",
            "reasons": blockers,
        }
    if hot_plane_reasons:
        return {
            "decision": "postgres_plus_redis_hot_plane_candidate",
            "postgres": "required_durable_source_of_truth",
            "redis": "enable_only_for_cache_queue_ttl_and_pubsub_after_adapter_tests",
            "reasons": hot_plane_reasons,
            "non_redis_fixes_still_required": query_reasons,
        }
    return {
        "decision": "postgres_only_for_now",
        "postgres": "required_durable_source_of_truth",
        "redis": "not_needed_until_hot_latest_state_or_queue_pressure_is_measured",
        "reasons": query_reasons or ["no_hot_plane_pressure_detected"],
    }


def render_storage_architecture_markdown(audit: dict[str, Any]) -> str:
    decision = audit.get("decision") or {}
    lines = [
        "# Crypto Options Storage Architecture Audit",
        "",
        f"- Generated: `{audit.get('generated_at_utc')}`",
        f"- Status: `{audit.get('status')}`",
        f"- Decision: `{decision.get('decision')}`",
        f"- Postgres role: `{audit.get('postgres_role')}`",
        f"- Redis role: `{audit.get('redis_role')}`",
        f"- Manual orders avoided: `{audit.get('manual_orders_avoided')}`",
        "",
        "## Reasons",
    ]
    for reason in decision.get("reasons") or ["none"]:
        lines.append(f"- `{reason}`")
    if decision.get("non_redis_fixes_still_required"):
        lines.extend(["", "## Query-Layer Fixes Still Required"])
        for reason in decision["non_redis_fixes_still_required"]:
            lines.append(f"- `{reason}`")
    lines.extend(["", "## Endpoint Timings"])
    for name, payload in (audit.get("endpoint_timings", {}).get("endpoints") or {}).items():
        lines.append(
            f"- `{name}`: `{payload.get('status')}`, "
            f"{payload.get('elapsed_ms')} ms, {payload.get('payload_bytes')} bytes"
        )
    lines.extend(["", "## Runtime Summary"])
    runtime = audit.get("runtime") or {}
    lines.append(f"- Runtime status: `{runtime.get('status')}`")
    lines.append(f"- DB backend: `{runtime.get('db_connection_backend')}`")
    docker = runtime.get("postgres_container") or {}
    lines.append(f"- Postgres CPU: `{docker.get('cpu_percent')}`")
    lines.append(f"- Postgres memory: `{docker.get('mem_usage')}`")
    redis = audit.get("redis_hot_plane") or {}
    lines.append(f"- Redis hot plane: `{redis.get('status')}`")
    diagnostics = audit.get("postgres_diagnostics") or {}
    if diagnostics and not diagnostics.get("skipped"):
        lines.extend(["", "## Postgres Diagnostics"])
        lines.append(f"- Status: `{diagnostics.get('status')}`")
        lines.append(f"- Database size: `{diagnostics.get('database_size_pretty')}`")
        lines.append(f"- Active connection count: `{diagnostics.get('active_connection_count')}`")
        lines.append(f"- Total connection count: `{diagnostics.get('total_connection_count')}`")
        lines.append(f"- Long active query count: `{diagnostics.get('long_active_query_count')}`")
        if diagnostics.get("warnings"):
            lines.append(f"- Warnings: `{', '.join(diagnostics.get('warnings') or [])}`")
    return "\n".join(lines) + "\n"


def write_storage_architecture_artifacts(
    audit: dict[str, Any],
    *,
    artifact_root: Path = DEFAULT_CONFIG.artifact_root,
) -> dict[str, str]:
    report_dir = Path(artifact_root) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = report_dir / f"storage_architecture_audit_{stamp}.json"
    md_path = report_dir / f"storage_architecture_audit_{stamp}.md"
    latest_json = report_dir / "storage_architecture_audit_latest.json"
    latest_md = report_dir / "storage_architecture_audit_latest.md"
    json_text = json.dumps(audit, indent=2, sort_keys=True, default=str)
    md_text = render_storage_architecture_markdown(audit)
    json_path.write_text(json_text + "\n", encoding="utf-8")
    md_path.write_text(md_text, encoding="utf-8")
    latest_json.write_text(json_text + "\n", encoding="utf-8")
    latest_md.write_text(md_text, encoding="utf-8")
    return {
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "latest_json_path": str(latest_json),
        "latest_markdown_path": str(latest_md),
    }


def _endpoint_timing_audit(options: StorageArchitectureAuditOptions) -> dict[str, Any]:
    base = options.backend_base_url.rstrip("/")
    endpoints = {
        "health": f"{base}/health",
        "dashboard_control_center_state": f"{base}/dashboard/control-center-state?include_details=false",
        "signals_validation_status": f"{base}/signals/validation/status?include_signals=false",
        "strategies_promotion": f"{base}/strategies/promotion",
    }
    rows = {
        name: _timed_json_get(url, timeout_seconds=options.endpoint_timeout_seconds)
        for name, url in endpoints.items()
    }
    warnings = [
        f"{name}:slow_endpoint_over_2000ms"
        for name, row in rows.items()
        if row.get("status") == "ok" and float(row.get("elapsed_ms") or 0.0) > 2000.0
    ]
    warnings.extend(
        f"{name}:large_payload_over_2mb"
        for name, row in rows.items()
        if int(row.get("payload_bytes") or 0) > 2_000_000
    )
    blockers = [
        f"{name}:endpoint_unavailable"
        for name, row in rows.items()
        if row.get("status") != "ok"
    ]
    return {
        "status": "blocked" if blockers else "degraded" if warnings else "ok",
        "blockers": blockers,
        "warnings": warnings,
        "endpoints": rows,
    }


def _postgres_diagnostics() -> dict[str, Any]:
    try:
        with connect_read_only(DEFAULT_CONFIG.db_path) as conn:
            if not bool(getattr(conn, "is_postgres", False)):
                return {"status": "skipped", "reason": "runtime_connection_not_postgres"}
            settings = _fetch_rows(
                conn,
                """
                SELECT name, setting, unit
                  FROM pg_settings
                 WHERE name IN (
                    'max_connections',
                    'shared_buffers',
                    'work_mem',
                    'maintenance_work_mem',
                    'effective_cache_size',
                    'temp_buffers'
                 )
                 ORDER BY name
                """,
            )
            connection_states = _fetch_rows(
                conn,
                """
                SELECT COALESCE(state, 'unknown') AS state, COUNT(*)::int AS count
                  FROM pg_stat_activity
                 GROUP BY COALESCE(state, 'unknown')
                 ORDER BY state
                """,
            )
            long_active_queries = _fetch_rows(
                conn,
                """
                SELECT pid,
                       usename,
                       state,
                       wait_event_type,
                       wait_event,
                       EXTRACT(EPOCH FROM (now() - query_start))::float AS query_age_seconds,
                       LEFT(query, 160) AS query_sample
                  FROM pg_stat_activity
                 WHERE pid <> pg_backend_pid()
                   AND state = 'active'
                   AND query_start IS NOT NULL
                 ORDER BY query_start ASC
                 LIMIT 8
                """,
            )
            database_size = _fetch_one(
                conn,
                """
                SELECT pg_database_size(current_database())::bigint AS bytes,
                       pg_size_pretty(pg_database_size(current_database())) AS pretty
                """,
            )
            database_stats = _fetch_one(
                conn,
                """
                SELECT numbackends::int,
                       temp_files::bigint,
                       temp_bytes::bigint,
                       xact_commit::bigint,
                       xact_rollback::bigint,
                       blks_read::bigint,
                       blks_hit::bigint
                  FROM pg_stat_database
                 WHERE datname = current_database()
                """,
            )
            largest_tables = _fetch_rows(
                conn,
                """
                SELECT relname,
                       pg_total_relation_size(relid)::bigint AS total_bytes,
                       pg_size_pretty(pg_total_relation_size(relid)) AS total_pretty,
                       n_live_tup::bigint AS estimated_live_rows
                  FROM pg_stat_user_tables
                 ORDER BY pg_total_relation_size(relid) DESC
                 LIMIT 8
                """,
            )
    except Exception as exc:  # noqa: BLE001 - storage audit should report diagnostics failures.
        return {
            "status": "degraded",
            "warnings": [f"postgres_diagnostics_unavailable:{type(exc).__name__}:{exc}"],
        }
    state_counts = {str(row.get("state") or "unknown"): int(row.get("count") or 0) for row in connection_states}
    active_count = int(state_counts.get("active", 0))
    total_count = sum(state_counts.values())
    long_query_count = sum(1 for row in long_active_queries if float(row.get("query_age_seconds") or 0.0) >= 30.0)
    warnings: list[str] = []
    if total_count >= 40:
        warnings.append("postgres_connection_count_over_40")
    if active_count >= 8:
        warnings.append("postgres_active_connection_count_over_8")
    if long_query_count:
        warnings.append("postgres_long_active_queries_present")
    temp_bytes = int((database_stats or {}).get("temp_bytes") or 0)
    if temp_bytes >= 1_000_000_000:
        warnings.append("postgres_temp_bytes_over_1gb")
    return {
        "status": "degraded" if warnings else "ok",
        "warnings": warnings,
        "connection_states": connection_states,
        "active_connection_count": active_count,
        "total_connection_count": total_count,
        "long_active_query_count": long_query_count,
        "long_active_queries": long_active_queries,
        "settings": settings,
        "database_size_bytes": int((database_size or {}).get("bytes") or 0),
        "database_size_pretty": (database_size or {}).get("pretty"),
        "database_stats": database_stats or {},
        "largest_tables": largest_tables,
    }


def _timed_json_get(url: str, *, timeout_seconds: float) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read()
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "status": "ok",
            "status_code": response.status,
            "elapsed_ms": elapsed_ms,
            "payload_bytes": len(body),
        }
    except Exception as exc:  # noqa: BLE001 - audit reports local runtime state.
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "status": "blocked",
            "elapsed_ms": elapsed_ms,
            "payload_bytes": 0,
            "error": f"{type(exc).__name__}:{exc}",
        }


def _decision_signals(
    *,
    runtime: dict[str, Any],
    endpoints: dict[str, Any],
    postgres_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    hot_plane_reasons: list[str] = []
    query_layer_reasons: list[str] = []
    runtime_status = runtime.get("status")
    if runtime_status == "blocked":
        blockers.append("runtime_audit_blocked")
    db = runtime.get("database") or {}
    if db.get("runtime_connection_is_postgres") is False:
        blockers.append("runtime_connection_not_postgres")
    docker_resources = ((runtime.get("docker") or {}).get("resources") or {})
    if docker_resources.get("warnings"):
        warnings.extend(docker_resources["warnings"])
        query_layer_reasons.extend(
            f"{warning}:requires_postgres_resource_review"
            for warning in docker_resources["warnings"]
        )
    if docker_resources.get("blockers"):
        blockers.extend(docker_resources["blockers"])
    if endpoints.get("warnings"):
        warnings.extend(endpoints["warnings"])
        for warning in endpoints["warnings"]:
            if "slow_endpoint" in warning or "large_payload" in warning:
                hot_plane_reasons.append(warning)
    if endpoints.get("blockers"):
        warnings.extend(endpoints["blockers"])
        query_layer_reasons.extend(endpoints["blockers"])
    diagnostics = postgres_diagnostics or {}
    if diagnostics.get("warnings"):
        warnings.extend(diagnostics["warnings"])
        query_layer_reasons.extend(diagnostics["warnings"])
        for warning in diagnostics["warnings"]:
            if "connection_count" in warning:
                hot_plane_reasons.append(warning)
    if (db.get("runtime_code") or {}).get("status") == "blocked":
        query_layer_reasons.append("runtime_sqlite_connect_offenders_exist")
    if not hot_plane_reasons and not query_layer_reasons:
        query_layer_reasons.append("keep_bounded_postgres_queries_and_indexes")
    return {
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "hot_plane_reasons": sorted(set(hot_plane_reasons)),
        "query_layer_reasons": sorted(set(query_layer_reasons)),
    }


def _fetch_rows(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _fetch_one(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else dict(row)


def _compact_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    db = runtime.get("database") or {}
    docker = runtime.get("docker") or {}
    return {
        "status": runtime.get("status"),
        "blockers": runtime.get("blockers") or [],
        "warnings": runtime.get("warnings") or [],
        "db_connection_backend": "postgres" if db.get("runtime_connection_is_postgres") else "unknown",
        "runtime_code_status": (db.get("runtime_code") or {}).get("status"),
        "postgres_container": docker.get("resources") or {},
    }
