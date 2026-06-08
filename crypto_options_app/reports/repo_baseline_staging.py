from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.config import DEFAULT_CONFIG


REPO_BASELINE_STAGING_SCHEMA_VERSION = "crypto_options_repo_baseline_staging_v1"
STAGE_CATEGORIES = {
    "stage_source",
    "stage_tests",
    "stage_docs_specs",
    "stage_coordination_artifacts",
    "stage_transition_reports",
}
SELECTED_REPORT_NAMES = {
    "repo_cleanup_batches_latest.json",
    "repo_cleanup_batches_latest.md",
    "repo_cleanup_inventory_latest.json",
    "repo_cleanup_inventory_latest.md",
    "repo_baseline_staging_plan_latest.json",
    "repo_baseline_staging_plan_latest.md",
    "storage_architecture_audit_latest.json",
    "storage_architecture_audit_latest.md",
    "transition_readiness_latest.json",
    "transition_readiness_latest.md",
}


def build_repo_baseline_staging_plan(
    cleanup_batches: dict[str, Any] | None = None,
    *,
    artifact_root: Path = DEFAULT_CONFIG.artifact_root,
) -> dict[str, Any]:
    cleanup_batches = cleanup_batches or _load_latest_batches(artifact_root)
    batch_0 = (cleanup_batches.get("batches") or {}).get("batch_0_active_crypto_baseline") or {}
    entries = list(batch_0.get("entries") or [])
    plan_entries = [_build_plan_entry(entry) for entry in entries]
    category_counts = Counter(entry["stage_category"] for entry in plan_entries)
    git_action_counts = Counter(entry["recommended_git_action"] for entry in plan_entries)
    stage_candidate_count = sum(1 for entry in plan_entries if entry["recommended_git_action"] == "stage")
    hold_count = sum(1 for entry in plan_entries if entry["recommended_git_action"] == "hold")
    manual_review_count = sum(1 for entry in plan_entries if entry["recommended_git_action"] == "manual_review")
    gates = {
        "batch_0_branch_required": "codex/crypto-transition-control-plane",
        "automatic_stage_allowed": False,
        "batch_0_stage_plan_ready": True,
        "generated_artifacts_excluded_from_stage": hold_count > 0,
        "manual_review_required_before_stage": manual_review_count > 0,
        "fixed_chats_start_ready": False,
    }
    return {
        "schema_version": REPO_BASELINE_STAGING_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source_batch_generated_at_utc": cleanup_batches.get("generated_at_utc"),
        "status": "degraded" if manual_review_count else "ok",
        "summary": {
            "batch_0_entry_count": len(plan_entries),
            "stage_candidate_count": stage_candidate_count,
            "hold_count": hold_count,
            "manual_review_count": manual_review_count,
            "stage_category_counts": dict(sorted(category_counts.items())),
            "recommended_git_action_counts": dict(sorted(git_action_counts.items())),
        },
        "gates": gates,
        "entries": plan_entries,
        "next_actions": _next_actions(
            stage_candidate_count=stage_candidate_count,
            hold_count=hold_count,
            manual_review_count=manual_review_count,
        ),
        "manual_orders_avoided": True,
        "live_trading_authorized": False,
    }


def render_repo_baseline_staging_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Crypto Options Batch 0 Baseline Staging Plan",
        "",
        f"- Generated: `{report.get('generated_at_utc')}`",
        f"- Status: `{report.get('status')}`",
        f"- Manual orders avoided: `{report.get('manual_orders_avoided')}`",
        "",
        "## Summary",
        f"- Batch 0 paths: `{summary.get('batch_0_entry_count')}`",
        f"- Stage candidates: `{summary.get('stage_candidate_count')}`",
        f"- Hold paths: `{summary.get('hold_count')}`",
        f"- Manual review paths: `{summary.get('manual_review_count')}`",
        "",
        "## Stage Categories",
    ]
    for category, count in (summary.get("stage_category_counts") or {}).items():
        lines.append(f"- `{category}`: `{count}`")
    lines.extend(["", "## Git Actions"])
    for action, count in (summary.get("recommended_git_action_counts") or {}).items():
        lines.append(f"- `{action}`: `{count}`")
    lines.extend(["", "## Stage Candidate Samples"])
    _append_samples(lines, report.get("entries") or [], wanted_action="stage")
    lines.extend(["", "## Hold Samples"])
    _append_samples(lines, report.get("entries") or [], wanted_action="hold")
    lines.extend(["", "## Next Actions"])
    for action in report.get("next_actions") or ["none"]:
        lines.append(f"- {action}")
    return "\n".join(lines) + "\n"


def write_repo_baseline_staging_artifacts(
    report: dict[str, Any],
    *,
    artifact_root: Path = DEFAULT_CONFIG.artifact_root,
) -> dict[str, str]:
    report_dir = Path(artifact_root) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = report_dir / f"repo_baseline_staging_plan_{stamp}.json"
    md_path = report_dir / f"repo_baseline_staging_plan_{stamp}.md"
    latest_json = report_dir / "repo_baseline_staging_plan_latest.json"
    latest_md = report_dir / "repo_baseline_staging_plan_latest.md"
    json_text = json.dumps(report, indent=2, sort_keys=True, default=str)
    md_text = render_repo_baseline_staging_markdown(report)
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


def classify_baseline_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    if "__pycache__/" in normalized or name.endswith((".pyc", ".pyo")):
        return "hold_generated_cache"
    if normalized.endswith((".sqlite", ".sqlite3", ".db", ".db-wal", ".db-shm")):
        return "hold_data_file"
    if normalized.startswith("crypto_options_app/data/"):
        return "hold_data_file"
    if normalized.startswith("crypto_options_app/artifacts/team_coordination/"):
        return "stage_coordination_artifacts"
    if normalized.startswith("crypto_options_app/artifacts/reports/"):
        return "stage_transition_reports" if name in SELECTED_REPORT_NAMES else "hold_generated_report"
    if normalized.startswith("crypto_options_app/artifacts/"):
        return "hold_generated_artifact"
    if normalized in {
        "crypto_options_app/tmp_health_snapshot.json",
        "crypto_options_app/crypto_options.sqlite3",
    }:
        return "hold_runtime_state"
    if normalized.startswith("tests/crypto_options_app/") or normalized.startswith("crypto_options_app/tests/"):
        return "stage_tests"
    if normalized.startswith("crypto_options_app/docs/"):
        return "stage_docs_specs"
    if normalized.startswith("crypto_options_app/"):
        return "stage_source"
    return "manual_review"


def recommended_git_action_for_category(category: str) -> str:
    if category in STAGE_CATEGORIES:
        return "stage"
    if category.startswith("hold_"):
        return "hold"
    return "manual_review"


def _build_plan_entry(entry: dict[str, Any]) -> dict[str, Any]:
    path = str(entry.get("path") or "")
    category = classify_baseline_path(path)
    action = recommended_git_action_for_category(category)
    return {
        "status": entry.get("status", ""),
        "path": path,
        "stage_category": category,
        "recommended_git_action": action,
        "reason": _reason_for_category(category),
    }


def _reason_for_category(category: str) -> str:
    reasons = {
        "stage_source": "active crypto source/config/runtime code",
        "stage_tests": "active crypto tests",
        "stage_docs_specs": "active crypto docs/specs",
        "stage_coordination_artifacts": "shared source-of-truth coordination artifact",
        "stage_transition_reports": "selected latest transition/report artifact",
        "hold_generated_cache": "generated Python/cache artifact",
        "hold_data_file": "database or local data file",
        "hold_generated_report": "generated historical report; keep out of baseline commit unless reviewed",
        "hold_generated_artifact": "runtime/historical artifact; keep out of baseline commit unless reviewed",
        "hold_runtime_state": "local runtime state snapshot",
        "manual_review": "path does not match a safe Batch 0 rule",
    }
    return reasons.get(category, "unclassified")


def _next_actions(*, stage_candidate_count: int, hold_count: int, manual_review_count: int) -> list[str]:
    actions = [
        "Review stage candidates before staging Batch 0.",
        "Keep held runtime/data/generated artifacts unstaged unless explicitly promoted to source-of-truth.",
    ]
    if manual_review_count:
        actions.append("Resolve manual-review paths before staging Batch 0.")
    if hold_count:
        actions.append("Add ignore/archive rules for recurring runtime outputs after confirming none are needed for source-of-truth.")
    if stage_candidate_count:
        actions.append("Stage Batch 0 candidates on codex/crypto-transition-control-plane only after this plan is accepted.")
    return actions


def _append_samples(lines: list[str], entries: list[dict[str, Any]], *, wanted_action: str, limit: int = 14) -> None:
    matches = [entry for entry in entries if entry.get("recommended_git_action") == wanted_action]
    for entry in matches[:limit]:
        lines.append(f"- `{entry.get('stage_category')}` `{entry.get('path')}`")
    remaining = max(0, len(matches) - limit)
    if remaining:
        lines.append(f"- ... `{remaining}` additional `{wanted_action}` paths in JSON artifact")
    if not matches:
        lines.append("- `none`")


def _load_latest_batches(artifact_root: Path) -> dict[str, Any]:
    path = Path(artifact_root) / "reports" / "repo_cleanup_batches_latest.json"
    if not path.exists():
        raise FileNotFoundError(f"repo cleanup batches not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))
