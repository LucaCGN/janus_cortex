from __future__ import annotations

import json
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.config import DEFAULT_CONFIG


REPO_CLEANUP_INVENTORY_SCHEMA_VERSION = "crypto_options_repo_cleanup_inventory_v1"
ACTIVE_CRYPTO_ROOTS = ("crypto_options_app/", "tests/crypto_options_app/")
REFERENCE_ROOTS = ("wnba_nba_app_reference/", "global_app_reference/")
LEGACY_ROOTS = ("app/", "tests/app/", "codex_tool/", "tests/codex_tool/", "codex_tools/", "tests/codex_tools/", "tools/", "tests/tools/")
CRYPTO_COMPATIBILITY_MARKERS = (
    "crypto_options",
    "/crypto/",
    "crypto/",
    "polymarket/crypto",
)
WNBA_NBA_MARKERS = (
    "nba",
    "wnba",
    "basketball",
    "aces",
    "valkyries",
)


@dataclass(frozen=True)
class RepoCleanupInventoryOptions:
    artifact_root: Path = DEFAULT_CONFIG.artifact_root
    include_git_status: bool = True
    include_fixed_chat_readiness: bool = False
    max_status_rows: int = 2500
    markdown_sample_limit: int = 20


def build_repo_cleanup_inventory(
    options: RepoCleanupInventoryOptions | None = None,
    *,
    status_rows: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    options = options or RepoCleanupInventoryOptions()
    generated_at = datetime.now(UTC).isoformat()
    rows = status_rows if status_rows is not None else _git_status_rows(max_rows=options.max_status_rows)
    path_entries = [_build_path_entry(row) for row in rows]
    classification_counts = Counter(entry["classification"] for entry in path_entries)
    action_counts = Counter(entry["proposed_action"] for entry in path_entries)
    review_required_count = sum(1 for entry in path_entries if entry["review_required"])
    unknown_count = classification_counts.get("unknown_or_root_review", 0)
    legacy_move_candidate_count = sum(
        classification_counts.get(name, 0)
        for name in ("global_reference_candidate", "wnba_nba_reference_candidate")
    )
    compatibility_count = classification_counts.get("crypto_compatibility_wrapper_candidate", 0)
    active_crypto_count = classification_counts.get("crypto_active", 0)
    fixed_chat_readiness = (
        _load_fixed_chat_readiness(options.artifact_root)
        if options.include_fixed_chat_readiness
        else _fixed_chat_readiness_not_evaluated(options.artifact_root)
    )
    fixed_chats_ready = fixed_chat_readiness.get("status") == "ready"
    gates = {
        "path_level_inventory_ready": True,
        "automatic_moves_allowed": False,
        "repo_move_ready": review_required_count == 0 and bool(path_entries),
        "fixed_chats_start_ready": fixed_chats_ready,
        "fixed_chats_start_gate": (
            "ready_per_fixed_chat_startup_readiness"
            if fixed_chats_ready
            else "run_or_review_fixed_chat_startup_readiness"
        ),
        "fixed_chat_startup_readiness_path": fixed_chat_readiness.get("path"),
        "github_issue_creation_ready": fixed_chats_ready,
        "github_issue_gate": (
            "issues_ready_per_fixed_chat_startup_readiness"
            if fixed_chats_ready
            else "blocked_until_cleanup_inventory_reviewable_and_branch_plan_exists"
        ),
    }
    warnings = _build_warnings(
        dirty_path_count=len(path_entries),
        unknown_count=unknown_count,
        compatibility_count=compatibility_count,
        legacy_move_candidate_count=legacy_move_candidate_count,
    )
    return {
        "schema_version": REPO_CLEANUP_INVENTORY_SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "status": "degraded" if warnings else "ok",
        "warnings": warnings,
        "summary": {
            "dirty_path_count": len(path_entries),
            "review_required_count": review_required_count,
            "active_crypto_count": active_crypto_count,
            "crypto_compatibility_wrapper_candidate_count": compatibility_count,
            "legacy_move_candidate_count": legacy_move_candidate_count,
            "unknown_or_root_review_count": unknown_count,
            "classification_counts": dict(sorted(classification_counts.items())),
            "proposed_action_counts": dict(sorted(action_counts.items())),
        },
        "gates": gates,
        "fixed_chat_startup_readiness": fixed_chat_readiness,
        "move_policy": {
            "delete_allowed": False,
            "bulk_move_allowed": False,
            "move_requires_review": True,
            "active_crypto_roots": list(ACTIVE_CRYPTO_ROOTS),
            "reference_roots": list(REFERENCE_ROOTS),
        },
        "path_entries": path_entries,
        "next_actions": _next_actions(
            unknown_count=unknown_count,
            compatibility_count=compatibility_count,
            legacy_move_candidate_count=legacy_move_candidate_count,
            fixed_chats_ready=fixed_chats_ready,
        ),
        "manual_orders_avoided": True,
        "live_trading_authorized": False,
    }


def render_repo_cleanup_inventory_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    gates = report.get("gates") or {}
    lines = [
        "# Crypto Options Repo Cleanup Inventory",
        "",
        f"- Generated: `{report.get('generated_at_utc')}`",
        f"- Status: `{report.get('status')}`",
        f"- Manual orders avoided: `{report.get('manual_orders_avoided')}`",
        "",
        "## Summary",
        f"- Dirty paths: `{summary.get('dirty_path_count')}`",
        f"- Review-required paths: `{summary.get('review_required_count')}`",
        f"- Active crypto paths: `{summary.get('active_crypto_count')}`",
        f"- Crypto compatibility wrapper candidates: `{summary.get('crypto_compatibility_wrapper_candidate_count')}`",
        f"- Legacy move candidates: `{summary.get('legacy_move_candidate_count')}`",
        f"- Unknown/root review paths: `{summary.get('unknown_or_root_review_count')}`",
        "",
        "## Gates",
        f"- Automatic moves allowed: `{gates.get('automatic_moves_allowed')}`",
        f"- Repo move ready: `{gates.get('repo_move_ready')}`",
        f"- Fixed chats start ready: `{gates.get('fixed_chats_start_ready')}`",
        f"- Fixed chat gate: `{gates.get('fixed_chats_start_gate')}`",
        f"- Fixed chat readiness report: `{gates.get('fixed_chat_startup_readiness_path')}`",
        f"- GitHub issue creation ready: `{gates.get('github_issue_creation_ready')}`",
        "",
        "## Classification Counts",
    ]
    for name, count in (summary.get("classification_counts") or {}).items():
        lines.append(f"- `{name}`: `{count}`")
    lines.extend(["", "## Warnings"])
    for warning in report.get("warnings") or ["none"]:
        lines.append(f"- `{warning}`")
    lines.extend(["", "## Proposed Move Samples"])
    sample_limit = 20
    for entry in (report.get("path_entries") or [])[:sample_limit]:
        destination = entry.get("proposed_destination") or "none"
        lines.append(
            "- "
            f"`{entry.get('status')}` `{entry.get('path')}` -> "
            f"`{entry.get('classification')}` / `{entry.get('proposed_action')}` / `{destination}`"
        )
    remaining = max(0, len(report.get("path_entries") or []) - sample_limit)
    if remaining:
        lines.append(f"- ... `{remaining}` additional paths in JSON artifact")
    lines.extend(["", "## Next Actions"])
    for action in report.get("next_actions") or ["none"]:
        lines.append(f"- {action}")
    return "\n".join(lines) + "\n"


def write_repo_cleanup_inventory_artifacts(
    report: dict[str, Any],
    *,
    artifact_root: Path = DEFAULT_CONFIG.artifact_root,
) -> dict[str, str]:
    report_dir = Path(artifact_root) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = report_dir / f"repo_cleanup_inventory_{stamp}.json"
    md_path = report_dir / f"repo_cleanup_inventory_{stamp}.md"
    latest_json = report_dir / "repo_cleanup_inventory_latest.json"
    latest_md = report_dir / "repo_cleanup_inventory_latest.md"
    json_text = json.dumps(report, indent=2, sort_keys=True, default=str)
    md_text = render_repo_cleanup_inventory_markdown(report)
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


def _build_path_entry(row: dict[str, str]) -> dict[str, Any]:
    path = row.get("path", "")
    classification = classify_cleanup_path(path)
    return {
        "status": row.get("status", ""),
        "path": path,
        "classification": classification,
        "proposed_action": _proposed_action(classification),
        "proposed_destination": _proposed_destination(path, classification),
        "review_required": _review_required(classification),
        "move_allowed_now": False,
    }


def classify_cleanup_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    lowered = normalized.lower()
    if normalized.startswith(ACTIVE_CRYPTO_ROOTS):
        return "crypto_active"
    if normalized.startswith("wnba_nba_app_reference/"):
        return "wnba_nba_reference"
    if normalized.startswith("global_app_reference/"):
        return "global_reference"
    if normalized.startswith(".github/"):
        return "github_review_required"
    if normalized.startswith(".codex_automation_memory/"):
        return "local_automation_state_review"
    if _has_crypto_compatibility_marker(lowered):
        return "crypto_compatibility_wrapper_candidate"
    if _has_wnba_nba_marker(lowered):
        return "wnba_nba_reference_candidate"
    if normalized.startswith(LEGACY_ROOTS):
        return "global_reference_candidate"
    if "/" not in normalized:
        return "root_config_review"
    return "unknown_or_root_review"


def _has_crypto_compatibility_marker(lowered_path: str) -> bool:
    return any(marker in lowered_path for marker in CRYPTO_COMPATIBILITY_MARKERS)


def _has_wnba_nba_marker(lowered_path: str) -> bool:
    return any(marker in lowered_path for marker in WNBA_NBA_MARKERS)


def _proposed_action(classification: str) -> str:
    if classification == "crypto_active":
        return "keep_active_crypto"
    if classification == "crypto_compatibility_wrapper_candidate":
        return "review_keep_temporarily_or_migrate_into_crypto_options_app"
    if classification == "wnba_nba_reference_candidate":
        return "review_move_to_wnba_nba_reference"
    if classification == "global_reference_candidate":
        return "review_move_to_global_reference"
    if classification in {"wnba_nba_reference", "global_reference"}:
        return "keep_reference"
    if classification == "local_automation_state_review":
        return "review_ignore_or_archive_local_state"
    if classification == "github_review_required":
        return "review_crypto_github_scope"
    if classification == "root_config_review":
        return "manual_root_config_review"
    return "manual_review"


def _proposed_destination(path: str, classification: str) -> str | None:
    normalized = path.replace("\\", "/")
    if classification == "wnba_nba_reference_candidate":
        return f"wnba_nba_app_reference/{normalized}"
    if classification == "global_reference_candidate":
        return f"global_app_reference/{normalized}"
    if classification == "crypto_compatibility_wrapper_candidate":
        return f"crypto_options_app/compatibility/{normalized}"
    if classification == "local_automation_state_review":
        return f"global_app_reference/{normalized}"
    return None


def _review_required(classification: str) -> bool:
    return classification not in {"crypto_active", "wnba_nba_reference", "global_reference"}


def _build_warnings(
    *,
    dirty_path_count: int,
    unknown_count: int,
    compatibility_count: int,
    legacy_move_candidate_count: int,
) -> list[str]:
    warnings: list[str] = []
    if dirty_path_count:
        warnings.append("repo_dirty_requires_review_before_moves")
    if unknown_count:
        warnings.append("unknown_or_root_paths_need_manual_review")
    if compatibility_count:
        warnings.append("crypto_compatibility_wrappers_need_cutover_decision")
    if legacy_move_candidate_count:
        warnings.append("legacy_reference_candidates_need_inventory_review")
    return warnings


def _next_actions(
    *,
    unknown_count: int,
    compatibility_count: int,
    legacy_move_candidate_count: int,
    fixed_chats_ready: bool,
) -> list[str]:
    actions = [
        "Review the JSON path_entries list before moving files.",
        "Create a branch/commit plan that separates crypto active work from reference moves.",
    ]
    if unknown_count:
        actions.append("Manually classify unknown/root paths before any bulk move.")
    if compatibility_count:
        actions.append("Decide which crypto compatibility wrappers must stay until runtime routes are fully cut over.")
    if legacy_move_candidate_count:
        actions.append("Move reviewed legacy paths into reference roots in small, testable batches.")
    if fixed_chats_ready:
        actions.append("Start only the ready fixed chats from fixed_chat_bootstrap; keep future-only prompts closed.")
    else:
        actions.append("Run fixed-chat startup readiness before starting any fixed chat.")
    return actions


def _fixed_chat_readiness_not_evaluated(artifact_root: Path) -> dict[str, Any]:
    path = Path(artifact_root) / "reports" / "fixed_chat_startup_readiness_latest.json"
    return {
        "status": "not_evaluated",
        "ready_count": None,
        "chat_count": None,
        "path": str(path),
    }


def _load_fixed_chat_readiness(artifact_root: Path) -> dict[str, Any]:
    path = Path(artifact_root) / "reports" / "fixed_chat_startup_readiness_latest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "status": "missing",
            "ready_count": 0,
            "chat_count": None,
            "path": str(path),
        }
    return {
        "status": payload.get("status"),
        "ready_count": payload.get("ready_count"),
        "chat_count": payload.get("chat_count"),
        "path": str(path),
    }


def _git_status_rows(*, max_rows: int) -> list[dict[str, str]]:
    try:
        completed = subprocess.run(
            ["git", "status", "--short", "--untracked-files=all"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception:
        return []
    rows: list[dict[str, str]] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        status = line[:2].strip()
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        rows.append({"status": status, "path": path})
        if len(rows) >= max_rows:
            break
    return rows
