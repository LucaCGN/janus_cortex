from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.config import DEFAULT_CONFIG

COMPATIBILITY_WRAPPER_DECISION_SCHEMA_VERSION = "crypto_options_compatibility_wrapper_decisions_v1"


DECISION_BUCKETS = {
    "replace_with_crypto_options_app_script_entrypoint": "replace_cli_wrapper_with_central_entrypoint",
    "migrate_or_drop_duplicate_test_after_active_suite_mapping": "review_legacy_duplicate_tests",
    "migrate_to_crypto_options_docs_or_reference": "move_legacy_docs_to_reference",
    "hold_for_runtime_import_audit": "runtime_import_audit_before_archive",
    "hold_for_strategy_signal_import_cutover": "strategy_signal_wrapper_archive_after_tests",
    "hold_for_import_audit": "manual_import_audit",
    "migrate_into_compatibility_or_remove_after_tests": "archive_or_remove_after_tests",
}


def build_compatibility_wrapper_decision_plan(
    audit: dict[str, Any] | None = None,
    *,
    artifact_root: Path = DEFAULT_CONFIG.artifact_root,
) -> dict[str, Any]:
    if audit is None:
        audit = _load_latest_audit(artifact_root)

    wrappers = list(audit.get("wrappers") or [])
    active_blockers = [wrapper for wrapper in wrappers if wrapper.get("active_reference_count", 0) > 0]
    decisions = [_decision_for_wrapper(wrapper) for wrapper in wrappers]
    bucket_counts = Counter(decision["bucket"] for decision in decisions)
    recommended_counts = Counter(decision["recommended_decision"] for decision in decisions)
    generated_at = datetime.now(UTC).isoformat()
    non_active_decisions_reviewable = not active_blockers and bool(wrappers)
    signal_strategy_fixed_chat_ready = (
        non_active_decisions_reviewable
        and _fixed_chat_ready(
            artifact_root=artifact_root,
            chat_id="signal_strategy_management_cleanup",
        )
    )
    signal_strategy_wait_reason = (
        "none"
        if signal_strategy_fixed_chat_ready
        else "GitHub milestones/issues and fixed_chat_startup_readiness_latest must be ready first."
    )
    return {
        "schema_version": COMPATIBILITY_WRAPPER_DECISION_SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "source_audit_generated_at_utc": audit.get("generated_at_utc"),
        "status": "review" if non_active_decisions_reviewable else "blocked",
        "summary": {
            "wrapper_count": len(wrappers),
            "active_blocker_count": len(active_blockers),
            "bucket_counts": dict(sorted(bucket_counts.items())),
            "recommended_decision_counts": dict(sorted(recommended_counts.items())),
        },
        "gates": {
            "active_import_blockers_cleared": not active_blockers,
            "non_active_wrapper_decisions_reviewable": non_active_decisions_reviewable,
            "github_issue_creation_can_start_after_commit": non_active_decisions_reviewable,
            "frontend_fixed_chat_ready": True,
            "signal_strategy_fixed_chat_ready": signal_strategy_fixed_chat_ready,
            "signal_strategy_fixed_chat_wait_reason": signal_strategy_wait_reason,
        },
        "decision_order": [
            "runtime_import_audit_before_archive",
            "strategy_signal_wrapper_archive_after_tests",
            "replace_cli_wrapper_with_central_entrypoint",
            "review_legacy_duplicate_tests",
            "move_legacy_docs_to_reference",
            "archive_or_remove_after_tests",
            "manual_import_audit",
        ],
        "decisions": decisions,
        "next_actions": _next_actions(
            active_blockers=active_blockers,
            signal_strategy_fixed_chat_ready=signal_strategy_fixed_chat_ready,
        ),
        "manual_orders_avoided": True,
        "live_trading_authorized": False,
    }


def render_compatibility_wrapper_decision_plan_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    gates = report.get("gates") or {}
    lines = [
        "# Crypto Options Compatibility Wrapper Decision Plan",
        "",
        f"- Generated: `{report.get('generated_at_utc')}`",
        f"- Status: `{report.get('status')}`",
        f"- Source audit: `{report.get('source_audit_generated_at_utc')}`",
        f"- Manual orders avoided: `{report.get('manual_orders_avoided')}`",
        f"- Live trading authorized: `{report.get('live_trading_authorized')}`",
        "",
        "## Summary",
        f"- Wrapper candidates: `{summary.get('wrapper_count')}`",
        f"- Active import blockers: `{summary.get('active_blocker_count')}`",
        "",
        "## Gates",
    ]
    for key, value in gates.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Decision Buckets"])
    for bucket in report.get("decision_order") or []:
        count = (summary.get("bucket_counts") or {}).get(bucket, 0)
        lines.append(f"- `{bucket}`: `{count}`")
    lines.extend(["", "## Recommended Decision Counts"])
    for decision, count in (summary.get("recommended_decision_counts") or {}).items():
        lines.append(f"- `{decision}`: `{count}`")
    lines.extend(["", "## Wrapper Samples"])
    for decision in list(report.get("decisions") or [])[:24]:
        lines.append(
            "- "
            f"`{decision.get('path')}` -> `{decision.get('bucket')}` "
            f"({decision.get('recommended_action')})"
        )
    remaining = max(0, len(report.get("decisions") or []) - 24)
    if remaining:
        lines.append(f"- ... `{remaining}` additional wrappers in JSON artifact")
    lines.extend(["", "## Next Actions"])
    for action in report.get("next_actions") or ["none"]:
        lines.append(f"- {action}")
    return "\n".join(lines) + "\n"


def write_compatibility_wrapper_decision_plan_artifacts(
    report: dict[str, Any],
    *,
    artifact_root: Path = DEFAULT_CONFIG.artifact_root,
) -> dict[str, str]:
    reports_dir = Path(artifact_root) / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = _artifact_stamp(report.get("generated_at_utc"))
    json_path = reports_dir / f"compatibility_wrapper_decisions_{stamp}.json"
    md_path = reports_dir / f"compatibility_wrapper_decisions_{stamp}.md"
    latest_json = reports_dir / "compatibility_wrapper_decisions_latest.json"
    latest_md = reports_dir / "compatibility_wrapper_decisions_latest.md"
    payload = json.dumps(report, indent=2, sort_keys=True, default=str)
    markdown = render_compatibility_wrapper_decision_plan_markdown(report)
    for path in (json_path, latest_json):
        path.write_text(payload + "\n", encoding="utf-8")
    for path in (md_path, latest_md):
        path.write_text(markdown, encoding="utf-8")
    return {
        "json": json_path.as_posix(),
        "markdown": md_path.as_posix(),
        "latest_json": latest_json.as_posix(),
        "latest_markdown": latest_md.as_posix(),
    }


def _decision_for_wrapper(wrapper: dict[str, Any]) -> dict[str, Any]:
    recommended_decision = str(wrapper.get("recommended_decision") or "hold_for_import_audit")
    bucket = DECISION_BUCKETS.get(recommended_decision, "manual_import_audit")
    active_refs = int(wrapper.get("active_reference_count") or 0)
    reference_count = int(wrapper.get("reference_count") or 0)
    if active_refs:
        bucket = "active_import_blocker"
        action = "cut_over_active_caller_before_any_archive_or_move"
    elif bucket == "replace_cli_wrapper_with_central_entrypoint":
        action = "replace_or_archive_cli_wrapper_after_entrypoint_smoke_test"
    elif bucket == "runtime_import_audit_before_archive":
        action = "prove_runtime_router_or_data_node_is_not_imported_before_archiving"
    elif bucket == "strategy_signal_wrapper_archive_after_tests":
        action = "archive_legacy_pipeline_or_service_after_signal_strategy_tests"
    elif bucket == "review_legacy_duplicate_tests":
        action = "map_or_drop_duplicate_legacy_tests_after_active_suite_coverage"
    elif bucket == "move_legacy_docs_to_reference":
        action = "move_legacy_docs_to_reference_root_if_not_already_preserved"
    elif bucket == "archive_or_remove_after_tests":
        action = "archive_or_remove_no_reference_wrapper_after_final_test_pass"
    else:
        action = "manual_review_required"
    return {
        "path": wrapper.get("path"),
        "family": wrapper.get("family"),
        "recommended_decision": recommended_decision,
        "bucket": bucket,
        "recommended_action": action,
        "active_reference_count": active_refs,
        "reference_count": reference_count,
        "documentation_reference_count": int(wrapper.get("documentation_reference_count") or 0),
        "matching_central_path": wrapper.get("matching_central_path"),
    }


def _next_actions(
    *,
    active_blockers: list[dict[str, Any]],
    signal_strategy_fixed_chat_ready: bool,
) -> list[str]:
    if active_blockers:
        return [
            f"Resolve {len(active_blockers)} active import blockers before any compatibility archive move.",
            "Regenerate compatibility_wrapper_audit_latest and this decision plan after cutover.",
        ]
    actions = [
        "Commit this decision plan so Batch 4 has a reviewable source of truth.",
        "Keep frontend fixed chat eligible now; start signal/strategy cleanup only after GitHub issues exist.",
        "Do not delete wrappers in bulk; archive or replace categories in the decision order with focused tests.",
    ]
    if signal_strategy_fixed_chat_ready:
        actions[1] = "Signal/strategy cleanup fixed chat is ready; start it from fixed_chat_bootstrap.md and GitHub issues #155-#159."
    else:
        actions.insert(1, "Create or refresh GitHub milestones/issues and fixed-chat startup readiness before starting signal/strategy cleanup.")
    return actions


def _fixed_chat_ready(*, artifact_root: Path, chat_id: str) -> bool:
    path = Path(artifact_root) / "reports" / "fixed_chat_startup_readiness_latest.json"
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if payload.get("status") != "ready":
        return False
    fixed_chats = payload.get("fixed_chats") or {}
    if isinstance(fixed_chats, dict):
        chat = fixed_chats.get(chat_id) or {}
        return chat.get("status") == "ready"
    for chat in fixed_chats:
        if chat.get("chat_id") == chat_id:
            return chat.get("status") == "ready"
    return False


def _load_latest_audit(artifact_root: Path) -> dict[str, Any]:
    path = Path(artifact_root) / "reports" / "compatibility_wrapper_audit_latest.json"
    if not path.exists():
        raise FileNotFoundError(f"compatibility wrapper audit not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _artifact_stamp(value: str | None) -> str:
    if not value:
        return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return value.replace("-", "").replace(":", "").split(".")[0]
