from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.config import DEFAULT_CONFIG


REPO_CLEANUP_BATCHES_SCHEMA_VERSION = "crypto_options_repo_cleanup_batches_v1"

BATCH_DEFINITIONS: dict[str, dict[str, str]] = {
    "batch_0_active_crypto_baseline": {
        "title": "No-Move Active Crypto Baseline",
        "branch": "codex/crypto-transition-control-plane",
        "purpose": "Establish active crypto baseline and focused tests before reference moves.",
    },
    "batch_1_local_state_root_config_review": {
        "title": "Local State And Root Config Review",
        "branch": "codex/crypto-repo-local-state-cleanup",
        "purpose": "Decide ignore/archive behavior for local automation state and root config churn.",
    },
    "batch_2_wnba_nba_reference_move": {
        "title": "WNBA/NBA Reference Move",
        "branch": "codex/crypto-repo-wnba-nba-reference",
        "purpose": "Move reviewed sports-bot references into wnba_nba_app_reference without deleting work.",
    },
    "batch_3_global_reference_move": {
        "title": "Global Legacy Reference Move",
        "branch": "codex/crypto-repo-global-reference",
        "purpose": "Move reviewed non-crypto Janus references into global_app_reference.",
    },
    "batch_4_crypto_compatibility_wrapper_cutover": {
        "title": "Crypto Compatibility Wrapper Decision",
        "branch": "codex/crypto-compatibility-wrapper-cutover",
        "purpose": "Keep or migrate crypto compatibility wrappers after import/runtime checks.",
    },
    "batch_5_github_source_of_truth_setup": {
        "title": "GitHub Source-Of-Truth Setup",
        "branch": "codex/crypto-github-workflow-setup",
        "purpose": "Prepare crypto milestones/issues after cleanup branches are reviewable.",
    },
    "reference_already_placed": {
        "title": "Reference Already Placed",
        "branch": "",
        "purpose": "Existing reference-root files require no move.",
    },
    "manual_review": {
        "title": "Manual Review",
        "branch": "codex/crypto-repo-manual-review",
        "purpose": "Paths that do not fit a safe automatic batch.",
    },
}

CLASSIFICATION_TO_BATCH = {
    "crypto_active": "batch_0_active_crypto_baseline",
    "local_automation_state_review": "batch_1_local_state_root_config_review",
    "root_config_review": "batch_1_local_state_root_config_review",
    "wnba_nba_reference_candidate": "batch_2_wnba_nba_reference_move",
    "global_reference_candidate": "batch_3_global_reference_move",
    "crypto_compatibility_wrapper_candidate": "batch_4_crypto_compatibility_wrapper_cutover",
    "github_review_required": "batch_5_github_source_of_truth_setup",
    "wnba_nba_reference": "reference_already_placed",
    "global_reference": "reference_already_placed",
}


def build_repo_cleanup_batches(
    inventory: dict[str, Any] | None = None,
    *,
    artifact_root: Path = DEFAULT_CONFIG.artifact_root,
) -> dict[str, Any]:
    inventory = inventory or _load_latest_inventory(artifact_root)
    entries = list(inventory.get("path_entries") or [])
    batches: dict[str, dict[str, Any]] = {}
    for batch_id, definition in BATCH_DEFINITIONS.items():
        batches[batch_id] = {
            "batch_id": batch_id,
            **definition,
            "entry_count": 0,
            "review_required_count": 0,
            "move_candidate_count": 0,
            "entries": [],
            "move_allowed_now": False,
        }
    for entry in entries:
        batch_id = batch_for_classification(str(entry.get("classification") or ""))
        batch = batches[batch_id]
        batch_entry = {
            "status": entry.get("status", ""),
            "path": entry.get("path", ""),
            "classification": entry.get("classification", ""),
            "proposed_action": entry.get("proposed_action", ""),
            "proposed_destination": entry.get("proposed_destination"),
            "review_required": bool(entry.get("review_required")),
        }
        batch["entries"].append(batch_entry)
        batch["entry_count"] += 1
        if batch_entry["review_required"]:
            batch["review_required_count"] += 1
        if batch_entry["proposed_destination"]:
            batch["move_candidate_count"] += 1
    batch_counts = Counter(batch_for_classification(str(entry.get("classification") or "")) for entry in entries)
    gates = {
        "automatic_moves_allowed": False,
        "batch_0_ready_for_branch": batches["batch_0_active_crypto_baseline"]["entry_count"] > 0,
        "reference_move_ready": False,
        "fixed_chats_start_ready": False,
        "github_issue_creation_ready": False,
    }
    return {
        "schema_version": REPO_CLEANUP_BATCHES_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source_inventory_generated_at_utc": inventory.get("generated_at_utc"),
        "source_inventory_status": inventory.get("status"),
        "source_inventory_summary": inventory.get("summary") or {},
        "status": "degraded",
        "gates": gates,
        "batch_counts": dict(sorted(batch_counts.items())),
        "batches": batches,
        "next_actions": [
            "Review and commit the current batch branch before opening reference-move branches.",
            "Keep reference moves separate from active crypto baseline and local-state cleanup.",
            "Hold root dependency changes until their owning app/tooling branch is clear.",
            "Review batch_4 compatibility wrappers before moving any wrapper paths.",
            "Create GitHub milestones/issues only after cleanup branches are reviewable.",
        ],
        "manual_orders_avoided": True,
        "live_trading_authorized": False,
    }


def batch_for_classification(classification: str) -> str:
    return CLASSIFICATION_TO_BATCH.get(classification, "manual_review")


def render_repo_cleanup_batches_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Crypto Options Repo Cleanup Batches",
        "",
        f"- Generated: `{report.get('generated_at_utc')}`",
        f"- Status: `{report.get('status')}`",
        f"- Source inventory: `{report.get('source_inventory_generated_at_utc')}`",
        f"- Manual orders avoided: `{report.get('manual_orders_avoided')}`",
        "",
        "## Gates",
    ]
    for key, value in (report.get("gates") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Batches"])
    for batch_id, batch in (report.get("batches") or {}).items():
        lines.extend(
            [
                "",
                f"### {batch.get('title')}",
                f"- ID: `{batch_id}`",
                f"- Branch: `{batch.get('branch') or 'none'}`",
                f"- Entries: `{batch.get('entry_count')}`",
                f"- Review required: `{batch.get('review_required_count')}`",
                f"- Move candidates: `{batch.get('move_candidate_count')}`",
                f"- Move allowed now: `{batch.get('move_allowed_now')}`",
                f"- Purpose: {batch.get('purpose')}",
            ]
        )
        for entry in list(batch.get("entries") or [])[:8]:
            destination = entry.get("proposed_destination") or "none"
            lines.append(f"  - `{entry.get('path')}` -> `{destination}`")
        remaining = max(0, int(batch.get("entry_count") or 0) - 8)
        if remaining:
            lines.append(f"  - ... `{remaining}` additional paths in JSON artifact")
    lines.extend(["", "## Next Actions"])
    for action in report.get("next_actions") or ["none"]:
        lines.append(f"- {action}")
    return "\n".join(lines) + "\n"


def write_repo_cleanup_batch_artifacts(
    report: dict[str, Any],
    *,
    artifact_root: Path = DEFAULT_CONFIG.artifact_root,
) -> dict[str, str]:
    report_dir = Path(artifact_root) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = report_dir / f"repo_cleanup_batches_{stamp}.json"
    md_path = report_dir / f"repo_cleanup_batches_{stamp}.md"
    latest_json = report_dir / "repo_cleanup_batches_latest.json"
    latest_md = report_dir / "repo_cleanup_batches_latest.md"
    json_text = json.dumps(report, indent=2, sort_keys=True, default=str)
    md_text = render_repo_cleanup_batches_markdown(report)
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


def _load_latest_inventory(artifact_root: Path) -> dict[str, Any]:
    path = Path(artifact_root) / "reports" / "repo_cleanup_inventory_latest.json"
    if not path.exists():
        raise FileNotFoundError(f"repo cleanup inventory not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))
