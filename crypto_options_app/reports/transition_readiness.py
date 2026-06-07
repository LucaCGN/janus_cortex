from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from crypto_options_app.config import DEFAULT_CONFIG


TRANSITION_READINESS_SCHEMA_VERSION = "crypto_options_transition_readiness_v1"
APP_ROOT = Path("crypto_options_app")
TEAM_COORDINATION_ROOT = APP_ROOT / "artifacts" / "team_coordination"
REQUIRED_COORDINATION_FILES = (
    "master_status.md",
    "fixed_chat_signal_strategy.md",
    "fixed_chat_frontend.md",
    "automation_registry.md",
    "promotion_policy.md",
    "repo_cleanup_plan.md",
    "repo_cleanup_inventory.md",
    "github_issue_milestone_plan.md",
    "fixed_chat_bootstrap.md",
    "handoff_queue.jsonl",
    "storage_architecture_decision.md",
)


@dataclass(frozen=True)
class TransitionReadinessOptions:
    artifact_root: Path = DEFAULT_CONFIG.artifact_root
    backend_base_url: str = "http://127.0.0.1:8011/v1/crypto-options-app"
    include_git_status: bool = True
    endpoint_timeout_seconds: float = 8.0


def build_transition_readiness_review(
    options: TransitionReadinessOptions | None = None,
) -> dict[str, Any]:
    options = options or TransitionReadinessOptions()
    generated_at = datetime.now(UTC).isoformat()
    coordination = _coordination_state()
    storage = _load_latest_storage_audit(options.artifact_root)
    endpoints = _endpoint_state(options)
    repo = _repo_state() if options.include_git_status else {"skipped": True}
    promotion = _promotion_state(endpoints.get("strategies_promotion", {}).get("payload") or {})
    signals = _signal_state(endpoints.get("signals_validation_status", {}).get("payload") or {})
    blockers, warnings = _transition_blockers(
        coordination=coordination,
        storage=storage,
        endpoints=endpoints,
        repo=repo,
        promotion=promotion,
    )
    readiness = _readiness_estimates(
        blockers=blockers,
        warnings=warnings,
        coordination=coordination,
        storage=storage,
        repo=repo,
        promotion=promotion,
    )
    return {
        "schema_version": TRANSITION_READINESS_SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "status": "blocked" if blockers else "degraded" if warnings else "ok",
        "blockers": blockers,
        "warnings": warnings,
        "readiness": readiness,
        "coordination": coordination,
        "storage": storage,
        "endpoints": _compact_endpoints(endpoints),
        "signals": signals,
        "promotion": promotion,
        "repo": repo,
        "next_actions": _next_actions(blockers=blockers, warnings=warnings, promotion=promotion),
        "manual_orders_avoided": True,
        "live_trading_authorized": False,
    }


def render_transition_readiness_markdown(review: dict[str, Any]) -> str:
    readiness = review.get("readiness") or {}
    lines = [
        "# Crypto Options Transition Readiness Review",
        "",
        f"- Generated: `{review.get('generated_at_utc')}`",
        f"- Status: `{review.get('status')}`",
        f"- Manual orders avoided: `{review.get('manual_orders_avoided')}`",
        "",
        "## Readiness",
    ]
    for key, value in readiness.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Blockers"])
    for blocker in review.get("blockers") or ["none"]:
        lines.append(f"- `{blocker}`")
    lines.extend(["", "## Warnings"])
    for warning in review.get("warnings") or ["none"]:
        lines.append(f"- `{warning}`")
    lines.extend(["", "## Promotion Summary"])
    promotion = review.get("promotion") or {}
    lines.append(f"- Strategy count: `{promotion.get('strategy_count')}`")
    lines.append(f"- Live candidates: `{promotion.get('live_candidate_count')}`")
    lines.append(f"- Shadow ready: `{promotion.get('shadow_ready_count')}`")
    lines.append(f"- Strict signal blockers: `{promotion.get('strict_signal_blocker_count')}`")
    lines.append(f"- Policy contract: `{promotion.get('policy_contract_schema_version') or 'missing'}`")
    lines.extend(["", "## Repo Summary"])
    repo = review.get("repo") or {}
    lines.append(f"- Dirty paths: `{repo.get('dirty_path_count')}`")
    lines.append(f"- Crypto active dirty paths: `{repo.get('classification_counts', {}).get('crypto_active', 0)}`")
    lines.append(f"- Reference candidate dirty paths: `{repo.get('reference_candidate_count')}`")
    lines.extend(["", "## Next Actions"])
    for action in review.get("next_actions") or ["none"]:
        lines.append(f"- {action}")
    return "\n".join(lines) + "\n"


def write_transition_readiness_artifacts(
    review: dict[str, Any],
    *,
    artifact_root: Path = DEFAULT_CONFIG.artifact_root,
) -> dict[str, str]:
    report_dir = Path(artifact_root) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = report_dir / f"transition_readiness_{stamp}.json"
    md_path = report_dir / f"transition_readiness_{stamp}.md"
    latest_json = report_dir / "transition_readiness_latest.json"
    latest_md = report_dir / "transition_readiness_latest.md"
    json_text = json.dumps(review, indent=2, sort_keys=True, default=str)
    md_text = render_transition_readiness_markdown(review)
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


def _coordination_state() -> dict[str, Any]:
    files = {}
    missing = []
    for name in REQUIRED_COORDINATION_FILES:
        path = TEAM_COORDINATION_ROOT / name
        files[name] = {"path": str(path), "exists": path.exists()}
        if not path.exists():
            missing.append(name)
    return {
        "status": "blocked" if missing else "ok",
        "root": str(TEAM_COORDINATION_ROOT),
        "missing_files": missing,
        "files": files,
    }


def _load_latest_storage_audit(artifact_root: Path) -> dict[str, Any]:
    path = Path(artifact_root) / "reports" / "storage_architecture_audit_latest.json"
    if not path.exists():
        return {"status": "missing", "path": str(path), "decision": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"status": "blocked", "path": str(path), "error": f"json_decode:{exc}"}
    return {
        "status": payload.get("status"),
        "path": str(path),
        "decision": (payload.get("decision") or {}).get("decision"),
        "redis_status": (payload.get("redis_hot_plane") or {}).get("status"),
        "postgres_memory": ((payload.get("runtime") or {}).get("postgres_container") or {}).get("mem_usage"),
        "blockers": payload.get("signals", {}).get("blockers") or [],
        "warnings": payload.get("signals", {}).get("warnings") or [],
    }


def _endpoint_state(options: TransitionReadinessOptions) -> dict[str, Any]:
    base = options.backend_base_url.rstrip("/")
    return {
        "health": _timed_json_get(f"{base}/health", timeout_seconds=options.endpoint_timeout_seconds),
        "signals_validation_status": _timed_json_get(
            f"{base}/signals/validation/status?include_signals=false",
            timeout_seconds=options.endpoint_timeout_seconds,
        ),
        "strategies_promotion": _timed_json_get(
            f"{base}/strategies/promotion",
            timeout_seconds=options.endpoint_timeout_seconds,
        ),
        "dashboard_control_center_state": _timed_json_get(
            f"{base}/dashboard/control-center-state",
            timeout_seconds=options.endpoint_timeout_seconds,
        ),
    }


def _timed_json_get(url: str, *, timeout_seconds: float) -> dict[str, Any]:
    started = datetime.now(UTC)
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read()
        elapsed_ms = (datetime.now(UTC) - started).total_seconds() * 1000.0
        return {
            "status": "ok",
            "status_code": response.status,
            "elapsed_ms": round(elapsed_ms, 2),
            "payload_bytes": len(raw),
            "payload": json.loads(raw.decode("utf-8", errors="replace")),
        }
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (datetime.now(UTC) - started).total_seconds() * 1000.0
        return {
            "status": "blocked",
            "elapsed_ms": round(elapsed_ms, 2),
            "payload_bytes": 0,
            "error": f"{type(exc).__name__}:{exc}",
        }


def _promotion_state(payload: dict[str, Any]) -> dict[str, Any]:
    strategies = list(payload.get("strategies") or [])
    by_state = dict(payload.get("by_promotion_state") or {})
    policy_contract = payload.get("policy_contract") if isinstance(payload.get("policy_contract"), dict) else {}
    live_candidates = [row for row in strategies if row.get("promotion_state") == "LIVE_CANDIDATE"]
    strict_blockers = [
        row
        for row in strategies
        if (row.get("signal_gate") or {}).get("strict_replay_required_count", 0)
    ]
    accidental_live = [
        row
        for row in strategies
        if row.get("live_trading_authorized") or row.get("orders_allowed")
    ]
    return {
        "strategy_count": int(payload.get("strategy_count") or len(strategies)),
        "by_promotion_state": by_state,
        "live_candidate_count": len(live_candidates),
        "shadow_ready_count": int(by_state.get("SHADOW_READY") or 0),
        "review_count": int(by_state.get("SHADOW_REVIEW") or 0) + int(by_state.get("REVIEW_BLOCKED") or 0),
        "strict_signal_blocker_count": len(strict_blockers),
        "accidental_live_authorized_count": len(accidental_live),
        "orders_allowed": bool(payload.get("orders_allowed")),
        "live_trading_authorized": bool(payload.get("live_trading_authorized")),
        "manual_orders_avoided": bool(payload.get("manual_orders_avoided", True)),
        "policy_contract_schema_version": policy_contract.get("schema_version"),
        "policy_contract_present": policy_contract.get("schema_version") == "crypto_options_promotion_policy_contract_v1",
    }


def _signal_state(payload: dict[str, Any]) -> dict[str, Any]:
    review_queue = payload.get("review_queue") or {}
    return {
        "schema_version": payload.get("schema_version"),
        "live_trading_authorized": bool(payload.get("live_trading_authorized")),
        "pending_review_request_count": int(review_queue.get("pending_review_request_count") or 0),
    }


def _repo_state() -> dict[str, Any]:
    latest_inventory = _latest_repo_cleanup_inventory_state()
    if latest_inventory:
        return latest_inventory
    rows = _git_status_rows()
    classifications = [_classify_dirty_path(row["path"]) for row in rows]
    counts: dict[str, int] = {}
    for classification in classifications:
        counts[classification] = counts.get(classification, 0) + 1
    return {
        "status": "degraded" if rows else "ok",
        "dirty_path_count": len(rows),
        "dirty_paths_sample": rows[:40],
        "classification_counts": counts,
        "reference_candidate_count": counts.get("wnba_nba_reference_candidate", 0)
        + counts.get("global_reference_candidate", 0),
        "cleanup_reference_roots": ["wnba_nba_app_reference", "global_app_reference"],
    }


def _latest_repo_cleanup_inventory_state(artifact_root: Path | None = None) -> dict[str, Any] | None:
    path = Path(artifact_root or DEFAULT_CONFIG.artifact_root) / "reports" / "repo_cleanup_inventory_latest.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    summary = payload.get("summary") or {}
    entries = list(payload.get("path_entries") or [])
    classification_counts = dict(summary.get("classification_counts") or {})
    return {
        "status": payload.get("status", "degraded"),
        "source": "repo_cleanup_inventory_latest",
        "path_level_inventory_path": str(path),
        "dirty_path_count": int(summary.get("dirty_path_count") or len(entries)),
        "dirty_paths_sample": [
            {"status": entry.get("status", ""), "path": entry.get("path", "")}
            for entry in entries[:40]
        ],
        "classification_counts": classification_counts,
        "reference_candidate_count": int(summary.get("legacy_move_candidate_count") or 0),
        "review_required_count": int(summary.get("review_required_count") or 0),
        "cleanup_reference_roots": ["wnba_nba_app_reference", "global_app_reference"],
        "gates": payload.get("gates") or {},
    }


def _git_status_rows() -> list[dict[str, str]]:
    try:
        completed = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception:
        return []
    rows = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        status = line[:2].strip()
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        rows.append({"status": status, "path": path})
    return rows


def _classify_dirty_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.startswith("crypto_options_app/") or normalized.startswith("tests/crypto_options_app/"):
        return "crypto_active"
    if normalized.startswith("wnba_nba_app_reference/"):
        return "wnba_nba_reference"
    if normalized.startswith("global_app_reference/"):
        return "global_reference"
    if normalized.startswith("app/") or normalized.startswith("tests/app/"):
        if "nba" in normalized.lower() or "wnba" in normalized.lower() or "basketball" in normalized.lower():
            return "wnba_nba_reference_candidate"
        return "global_reference_candidate"
    if normalized.startswith("codex_tool/") or normalized.startswith("codex_tools/") or normalized.startswith("tools/"):
        return "global_reference_candidate"
    if normalized.startswith(".github/"):
        return "github_review_required"
    return "unknown_or_root"


def _transition_blockers(
    *,
    coordination: dict[str, Any],
    storage: dict[str, Any],
    endpoints: dict[str, Any],
    repo: dict[str, Any],
    promotion: dict[str, Any],
) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    if coordination.get("status") != "ok":
        blockers.append("team_coordination_missing_required_files")
    if storage.get("status") in {None, "missing", "blocked"}:
        blockers.append("storage_audit_missing_or_blocked")
    elif storage.get("status") == "degraded":
        warnings.append("storage_audit_degraded")
    health = endpoints.get("health") or {}
    if health.get("status") != "ok":
        blockers.append("health_endpoint_unavailable")
    else:
        health_payload = health.get("payload") or {}
        if not ((health_payload.get("db") or {}).get("connection_is_postgres")):
            blockers.append("health_not_postgres_runtime")
        if health_payload.get("orders_allowed") or health_payload.get("live_trading_authorized"):
            blockers.append("live_or_orders_enabled_during_transition")
        if health_payload.get("status") == "degraded":
            warnings.append("health_degraded")
    if promotion.get("accidental_live_authorized_count"):
        blockers.append("strategy_promotion_contains_accidental_live_authorized_rows")
    if not promotion.get("policy_contract_present"):
        blockers.append("strategy_promotion_policy_contract_missing")
    if promotion.get("live_candidate_count"):
        warnings.append("live_candidates_exist_review_before_live")
    if repo.get("dirty_path_count"):
        warnings.append("repo_dirty_requires_inventory_cleanup")
    return sorted(set(blockers)), sorted(set(warnings))


def _readiness_estimates(
    *,
    blockers: list[str],
    warnings: list[str],
    coordination: dict[str, Any],
    storage: dict[str, Any],
    repo: dict[str, Any],
    promotion: dict[str, Any],
) -> dict[str, str]:
    return {
        "runtime_data_stability": "70%" if not blockers else "55-65%",
        "replay_integrity": "75%",
        "promotion_demotion_trust": "50%" if promotion.get("live_candidate_count") == 0 else "45%",
        "signal_strategy_queue_readiness": "55-60%",
        "frontend_control_center_readiness": "45%",
        "repo_github_workflow_readiness": "40%" if repo.get("dirty_path_count") else "55%",
        "coordination_readiness": "70%" if coordination.get("status") == "ok" else "35%",
        "storage_architecture_readiness": "60%" if storage.get("decision") else "35%",
        "broad_automation_readiness": "not_ready",
    }


def _next_actions(*, blockers: list[str], warnings: list[str], promotion: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    if blockers:
        actions.append("Fix transition blockers before adding automations or fixed-chat execution.")
    if "repo_dirty_requires_inventory_cleanup" in warnings:
        actions.append("Continue Batch 4 compatibility-wrapper review and keep generated/runtime artifacts unstaged.")
    if "storage_audit_degraded" in warnings:
        actions.append("Reduce measured Postgres memory/query pressure before enabling Redis or widening replay/data-service workers.")
    if promotion.get("shadow_ready_count"):
        actions.append("Review SHADOW_READY rows for recent one-hour economic proof before any live promotion.")
    actions.append("Keep fixed chats and limited automations on their GitHub issue and team_coordination handoff contracts.")
    return actions


def _compact_endpoints(endpoints: dict[str, Any]) -> dict[str, Any]:
    return {
        name: {
            "status": row.get("status"),
            "elapsed_ms": row.get("elapsed_ms"),
            "payload_bytes": row.get("payload_bytes"),
            "status_code": row.get("status_code"),
            "error": row.get("error"),
        }
        for name, row in endpoints.items()
    }
