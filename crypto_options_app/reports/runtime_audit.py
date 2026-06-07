from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from crypto_options_app.config import DEFAULT_CONFIG
from crypto_options_app.db.connection import connect
from crypto_options_app.db.postgres import check_postgres_connection


AUDIT_SCHEMA_VERSION = "crypto_options_runtime_audit_v1"
APP_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_TABLE_GROUPS: dict[str, tuple[str, ...]] = {
    "A_crypto": (
        "external_technical_observer_snapshots",
        "external_technical_observer_components",
        "underlying_price_ticks",
        "underlying_candles",
        "indicator_snapshots",
    ),
    "B_profiles": (
        "profiles",
        "profile_grades",
        "profile_raw_activity",
        "profile_event_orders",
        "profile_event_positions",
        "profile_distribution_snapshots",
        "profile_distribution_components",
    ),
    "C_options": (
        "events",
        "event_tokens",
        "polymarket_updown_pair_snapshots",
        "polymarket_price_ticks",
        "polymarket_order_books",
        "polymarket_order_book_levels",
        "polymarket_event_path_stats",
    ),
    "signals": (
        "signal_specs",
        "signal_versions",
        "signal_queue_items",
        "signal_validation_runs",
        "signal_validation_results",
        "signal_observations",
    ),
    "strategies": (
        "strategy_specs",
        "strategy_versions",
        "strategy_promotion_state",
        "strategy_validation_runs",
        "strategy_candidates",
    ),
    "trading_lifecycle": (
        "execution_intents",
        "orders",
        "fills",
        "positions",
        "exit_plans",
        "run_reports",
    ),
}

RUNTIME_PROCESS_PATTERNS = {
    "backend": "crypto_options_app.main:app",
    "frontend": "crypto_options_app.frontend_service:app",
    "A_market": "run_crypto_options_underlying_market_price_capture",
    "A_crypto": "run_crypto_options_underlying_technical_observers",
    "B_profiles": "run_crypto_options_profile_distribution_service",
    "C_options": "run_crypto_options_option_price_capture",
}

FORBIDDEN_RUNTIME_PROCESS_PATTERNS = {
    "sqlite_hot_sync": "run_crypto_options_sqlite_to_postgres_copy",
}

RUNTIME_SQLITE_SCAN_ROOTS = ("api", "data_services", "feeds", "indicators", "reports", "signals", "strategies", "workers")
SQLITE_USAGE_SCAN_ROOTS = (
    "api",
    "data_services",
    "db",
    "feeds",
    "indicators",
    "pipelines",
    "reports",
    "scripts",
    "signals",
    "strategies",
    "workers",
)
SQLITE_IMPORT_RE = re.compile(r"(^|\n)\s*(import sqlite3|from sqlite3\b)")
SQLITE_CONNECT_RE = re.compile(r"\bsqlite3\s*\.\s*connect\s*\(")


@dataclass(frozen=True)
class RuntimeAuditOptions:
    backend_url: str = "http://127.0.0.1:8011/v1/crypto-options-app/health"
    frontend_url: str = "http://127.0.0.1:8012/"
    artifact_root: Path = DEFAULT_CONFIG.artifact_root
    check_docker: bool = True
    check_endpoints: bool = True
    include_processes: bool = True


def build_runtime_audit(options: RuntimeAuditOptions | None = None) -> dict[str, Any]:
    options = options or RuntimeAuditOptions()
    blockers: list[str] = []
    warnings: list[str] = []
    generated_at = datetime.now(UTC).isoformat()

    docker = _docker_status() if options.check_docker else {"skipped": True}
    if options.check_docker and docker.get("status") == "blocked":
        blockers.append("docker_postgres_unavailable")
    elif options.check_docker and docker.get("status") == "degraded":
        warnings.append("docker_postgres_degraded")
    docker_resources = docker.get("resources") if isinstance(docker.get("resources"), dict) else {}
    blockers.extend(str(item) for item in docker_resources.get("blockers") or [])
    warnings.extend(str(item) for item in docker_resources.get("warnings") or [])

    postgres = check_postgres_connection()
    if postgres.get("status") != "ok":
        blockers.append("postgres_connection_unavailable")

    db = _database_audit()
    blockers.extend(db.get("blockers") or [])
    warnings.extend(db.get("warnings") or [])

    processes = _process_audit() if options.include_processes else {"skipped": True}
    blockers.extend(processes.get("blockers") or [])

    endpoints = _endpoint_audit(options) if options.check_endpoints else {"skipped": True}
    blockers.extend(endpoints.get("blockers") or [])
    warnings.extend(endpoints.get("warnings") or [])

    status = _status_from_blockers(blockers, warnings)
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "status": status,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "docker": docker,
        "postgres": postgres,
        "database": db,
        "processes": processes,
        "endpoints": endpoints,
        "manual_orders_avoided": True,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def write_runtime_audit_artifacts(
    audit: dict[str, Any],
    *,
    artifact_root: Path = DEFAULT_CONFIG.artifact_root,
) -> dict[str, str]:
    report_dir = Path(artifact_root) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = report_dir / f"runtime_audit_{stamp}.json"
    md_path = report_dir / f"runtime_audit_{stamp}.md"
    latest_json = report_dir / "runtime_audit_latest.json"
    latest_md = report_dir / "runtime_audit_latest.md"
    json_text = json.dumps(audit, indent=2, sort_keys=True, default=str)
    md_text = render_runtime_audit_markdown(audit)
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


def render_runtime_audit_markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# Crypto Options Runtime Audit",
        "",
        f"- Generated: `{audit.get('generated_at_utc')}`",
        f"- Status: `{audit.get('status')}`",
        f"- Manual orders avoided: `{audit.get('manual_orders_avoided')}`",
        "",
        "## Blockers",
        *(f"- `{blocker}`" for blocker in audit.get("blockers") or ["none"]),
        "",
        "## A/B/C Data Groups",
    ]
    for group, payload in (audit.get("database", {}).get("groups") or {}).items():
        lines.append(f"- `{group}`: `{payload.get('status')}`")
        for table, count in (payload.get("counts") or {}).items():
            lines.append(f"  - `{table}`: {count}")
    runtime_code = audit.get("database", {}).get("runtime_code") or {}
    lines.extend(["", "## Runtime SQLite Usage"])
    lines.append(f"- Status: `{runtime_code.get('status', 'unknown')}`")
    lines.append(f"- Runtime offenders: `{len(runtime_code.get('runtime_offenders') or [])}`")
    lines.append(f"- Review required: `{len(runtime_code.get('review_required') or [])}`")
    lines.append(f"- Allowed migration/test/compat usage: `{len(runtime_code.get('allowed_sqlite_usage') or [])}`")
    lines.extend(["", "## Processes"])
    for name, payload in (audit.get("processes", {}).get("required") or {}).items():
        lines.append(f"- `{name}`: {payload.get('status')} {payload.get('pids') or []}")
    return "\n".join(lines) + "\n"


def _docker_status() -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                "docker",
                "inspect",
                "janus-cortex-crypto-options-postgres",
                "--format",
                "{{json .State}}",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - audit must report local platform state.
        return {"status": "blocked", "error": f"{type(exc).__name__}:{exc}"}
    if completed.returncode != 0:
        return {
            "status": "blocked",
            "returncode": completed.returncode,
            "stderr": completed.stderr.strip(),
        }
    try:
        state = json.loads(completed.stdout.strip())
    except json.JSONDecodeError:
        state = {"raw": completed.stdout.strip()}
    health = (state.get("Health") or {}).get("Status")
    running = bool(state.get("Running"))
    resource_status = _docker_resource_status()
    status = "ok" if running and health in {None, "healthy"} else "blocked"
    if resource_status.get("status") == "blocked":
        status = "blocked"
    elif status == "ok" and resource_status.get("status") == "degraded":
        status = "degraded"
    return {
        "status": status,
        "running": running,
        "health": health,
        "resources": resource_status,
        "state": state,
    }


def _docker_resource_status() -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                "docker",
                "stats",
                "janus-cortex-crypto-options-postgres",
                "--no-stream",
                "--format",
                "{{json .}}",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "warning": f"docker_stats_unavailable:{type(exc).__name__}:{exc}"}
    if completed.returncode != 0 or not completed.stdout.strip():
        return {
            "status": "degraded",
            "warning": "docker_stats_unavailable",
            "stderr": completed.stderr.strip(),
        }
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"status": "degraded", "warning": "docker_stats_unparseable", "raw": completed.stdout.strip()}
    mem_usage = str(payload.get("MemUsage") or "")
    used_text = mem_usage.split("/", 1)[0].strip()
    used_bytes = _parse_memory_size_bytes(used_text)
    status = "ok"
    warnings: list[str] = []
    blockers: list[str] = []
    if used_bytes is not None and used_bytes >= 8 * 1024**3:
        status = "blocked"
        blockers.append("postgres_container_memory_over_8gib")
    elif used_bytes is not None and used_bytes >= 6 * 1024**3:
        status = "degraded"
        warnings.append("postgres_container_memory_over_6gib")
    return {
        "status": status,
        "cpu_percent": payload.get("CPUPerc"),
        "mem_usage": mem_usage,
        "mem_used_bytes": used_bytes,
        "warnings": warnings,
        "blockers": blockers,
    }


def _parse_memory_size_bytes(value: str) -> int | None:
    match = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?i?b)\s*$", value, flags=re.IGNORECASE)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2).lower()
    multipliers = {
        "b": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000**2,
        "mib": 1024**2,
        "gb": 1000**3,
        "gib": 1024**3,
        "tb": 1000**4,
        "tib": 1024**4,
    }
    multiplier = multipliers.get(unit)
    if multiplier is None:
        return None
    return int(amount * multiplier)


def _database_audit() -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    groups: dict[str, Any] = {}
    try:
        conn = connect()
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "blocked",
            "blockers": [f"postgres_runtime_connect_failed:{type(exc).__name__}:{exc}"],
            "warnings": [],
            "groups": {},
            "runtime_connection_is_postgres": False,
        }
    with conn:
        runtime_is_postgres = bool(getattr(conn, "is_postgres", False))
        if not runtime_is_postgres:
            blockers.append("runtime_connection_not_postgres")
        for group, tables in REQUIRED_TABLE_GROUPS.items():
            counts: dict[str, int | None] = {}
            approximate: dict[str, bool] = {}
            group_blockers: list[str] = []
            for table in tables:
                try:
                    exists_row = conn.execute(f"SELECT EXISTS(SELECT 1 FROM {table} LIMIT 1) AS has_rows").fetchone()
                    has_rows = bool(exists_row["has_rows"] if exists_row is not None else False)
                    estimate_row = conn.execute(
                        "SELECT COALESCE((SELECT reltuples::bigint FROM pg_class WHERE oid = to_regclass(?)), -1) AS c",
                        (table,),
                    ).fetchone()
                    count = int(estimate_row["c"] if estimate_row is not None else -1)
                    if has_rows and count <= 0:
                        count = 1
                    approximate[table] = True
                except Exception as exc:  # noqa: BLE001
                    count = None
                    group_blockers.append(f"{table}:query_failed:{type(exc).__name__}")
                counts[table] = count
                if count == 0 or count == -1:
                    group_blockers.append(f"{table}:empty")
            groups[group] = {
                "status": "blocked" if group_blockers else "ok",
                "counts": counts,
                "counts_are_estimates": approximate,
                "blockers": group_blockers,
            }
            blockers.extend(f"{group}:{blocker}" for blocker in group_blockers)
    runtime_code = _runtime_code_audit()
    blockers.extend(runtime_code.get("blockers") or [])
    warnings.extend(runtime_code.get("warnings") or [])
    return {
        "status": _status_from_blockers(blockers, warnings),
        "blockers": blockers,
        "warnings": warnings,
        "runtime_connection_is_postgres": True,
        "groups": groups,
        "runtime_code": runtime_code,
    }


def _runtime_code_audit() -> dict[str, Any]:
    """Fail on production runtime SQLite connects and classify the remaining footprint.

    SQLite is still valid for migration, test, and explicit compatibility paths.
    The transition blocker is direct production runtime access that can reintroduce
    writer locks. Non-runtime direct SQLite users are surfaced as review work so
    the DB transition can retire them intentionally.
    """

    offenders: list[str] = []
    runtime_offenders: list[dict[str, str]] = []
    allowed: list[dict[str, str]] = []
    review_required: list[dict[str, str]] = []
    scanned_paths = 0
    sqlite_usage_count = 0
    for root_name in SQLITE_USAGE_SCAN_ROOTS:
        root = APP_ROOT / root_name
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            scanned_paths += 1
            rel = path.relative_to(APP_ROOT)
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(errors="replace")
            has_import = bool(SQLITE_IMPORT_RE.search(text))
            has_direct_connect = bool(SQLITE_CONNECT_RE.search(text))
            if not has_import and not has_direct_connect:
                continue
            sqlite_usage_count += 1
            record: dict[str, Any] = {
                "path": str(rel),
                "category": _sqlite_usage_category(rel, has_direct_connect=has_direct_connect),
                "direct_connect": has_direct_connect,
            }
            if _is_runtime_sqlite_blocker(rel, has_direct_connect=has_direct_connect):
                offenders.append(str(rel))
                runtime_offenders.append(record)
            elif record["category"].startswith("allowed_"):
                allowed.append(record)
            else:
                review_required.append(record)
    blockers = [f"runtime_sqlite_connect:{path}" for path in offenders]
    warnings = ["sqlite_usage_review_required"] if review_required and not blockers else []
    return {
        "status": _status_from_blockers(blockers, warnings),
        "offenders": offenders,
        "runtime_offenders": runtime_offenders,
        "allowed_sqlite_usage": sorted(allowed, key=lambda row: row["path"]),
        "review_required": sorted(review_required, key=lambda row: row["path"]),
        "sqlite_usage_count": sqlite_usage_count,
        "scanned_paths": scanned_paths,
        "blockers": blockers,
        "warnings": warnings,
    }


def _is_runtime_sqlite_blocker(rel: Path, *, has_direct_connect: bool) -> bool:
    if not has_direct_connect:
        return False
    if _sqlite_usage_category(rel, has_direct_connect=has_direct_connect).startswith("allowed_"):
        return False
    return bool(rel.parts and rel.parts[0] in RUNTIME_SQLITE_SCAN_ROOTS)


def _sqlite_usage_category(rel: Path, *, has_direct_connect: bool) -> str:
    rel_text = rel.as_posix()
    if rel.parts and rel.parts[0] == "db":
        if rel_text in {
            "db/connection.py",
            "db/imports.py",
            "db/postgres.py",
            "db/postgres_connection.py",
            "db/postgres_import.py",
            "db/postgres_shadow.py",
            "db/runtime_persistence.py",
            "db/schema.py",
            "db/sqlite_compaction.py",
            "db/sqlite_retention.py",
        }:
            return "allowed_db_adapter_migration_compat"
    if rel.parts and rel.parts[0] == "scripts":
        if "sqlite_to_postgres" in rel.name or "sqlite" in rel.name:
            return "allowed_migration_cli"
        return "review_script_sqlite_usage"
    if rel_text in {
        "pipelines/options/profile_store.py",
        "pipelines/options/market_data_store.py",
    }:
        return "review_legacy_sqlite_side_store"
    if has_direct_connect:
        return "review_non_runtime_direct_sqlite_connect"
    return "review_sqlite_import"


def _process_audit() -> dict[str, Any]:
    rows = _powershell_process_rows()
    required: dict[str, Any] = {}
    blockers: list[str] = []
    for name, pattern in RUNTIME_PROCESS_PATTERNS.items():
        pids = [row["pid"] for row in rows if pattern in row["command_line"]]
        required[name] = {"status": "ok" if pids else "missing", "pids": pids}
        if not pids and name != "frontend":
            blockers.append(f"missing_process:{name}")
    forbidden: dict[str, Any] = {}
    for name, pattern in FORBIDDEN_RUNTIME_PROCESS_PATTERNS.items():
        pids = [row["pid"] for row in rows if pattern in row["command_line"]]
        forbidden[name] = {"status": "blocked" if pids else "ok", "pids": pids}
        if pids:
            blockers.append(f"forbidden_process_running:{name}")
    return {
        "status": "blocked" if blockers else "ok",
        "blockers": blockers,
        "required": required,
        "forbidden": forbidden,
    }


def _endpoint_audit(options: RuntimeAuditOptions) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    backend = _http_json(options.backend_url)
    frontend = _http_text(options.frontend_url)
    if backend.get("status") != "ok":
        blockers.append("backend_health_unavailable")
    if frontend.get("status") != "ok":
        blockers.append("frontend_unavailable")
    if backend.get("payload", {}).get("db", {}).get("read_status") != "ok":
        blockers.append("backend_health_db_read_not_ok")
    if "CRYPTO_OPTIONS_API_BASE" not in str(frontend.get("body") or ""):
        warnings.append("frontend_api_base_injection_not_detected")
    return {
        "status": _status_from_blockers(blockers, warnings),
        "blockers": blockers,
        "warnings": warnings,
        "backend": backend,
        "frontend": {k: v for k, v in frontend.items() if k != "body"},
    }


def _http_json(url: str) -> dict[str, Any]:
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
            return {"status": "ok", "status_code": response.status, "payload": json.loads(body)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "blocked", "error": f"{type(exc).__name__}:{exc}"}


def _http_text(url: str) -> dict[str, Any]:
    try:
        request = Request(url, headers={"Accept": "text/html"})
        with urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
            return {"status": "ok", "status_code": response.status, "body": body[:5000]}
    except Exception as exc:  # noqa: BLE001
        return {"status": "blocked", "error": f"{type(exc).__name__}:{exc}"}


def _powershell_process_rows() -> list[dict[str, Any]]:
    command = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception:
        return []
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    payload = json.loads(completed.stdout)
    if isinstance(payload, dict):
        payload = [payload]
    return [
        {
            "pid": int(row.get("ProcessId") or 0),
            "command_line": str(row.get("CommandLine") or ""),
        }
        for row in payload
    ]


def _status_from_blockers(blockers: list[str], warnings: list[str]) -> str:
    if blockers:
        return "blocked"
    if warnings:
        return "degraded"
    return "ok"
