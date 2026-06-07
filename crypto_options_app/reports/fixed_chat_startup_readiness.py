from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.config import DEFAULT_CONFIG


FIXED_CHAT_STARTUP_READINESS_SCHEMA_VERSION = "crypto_options_fixed_chat_startup_readiness_v1"
APP_ROOT = Path("crypto_options_app")
TEAM_COORDINATION_ROOT = APP_ROOT / "artifacts" / "team_coordination"


@dataclass(frozen=True)
class FixedChatStartupReadinessOptions:
    artifact_root: Path = DEFAULT_CONFIG.artifact_root
    team_coordination_root: Path = TEAM_COORDINATION_ROOT


FIXED_CHAT_CONTRACTS: dict[str, dict[str, Any]] = {
    "frontend_control_center_developer": {
        "prompt": "fixed_chat_prompts/frontend_control_center_developer.md",
        "lane_file": "fixed_chat_frontend.md",
        "github_issues": ["#160", "#161", "#162", "#163", "#164"],
        "required_phrases": [
            "Do not work on",
            "DB infrastructure",
            "promotion logic",
            "trading runtime",
        ],
        "first_artifacts": [
            "crypto_options_app/artifacts/team_coordination/fixed_chat_frontend.md",
            "crypto_options_app/artifacts/team_coordination/github_source_of_truth_sync.md",
            "crypto_options_app/artifacts/reports/transition_readiness_latest.json",
        ],
    },
    "signal_strategy_management_cleanup": {
        "prompt": "fixed_chat_prompts/signal_strategy_management_cleanup.md",
        "lane_file": "fixed_chat_signal_strategy.md",
        "github_issues": ["#155", "#156", "#157", "#158", "#159"],
        "required_phrases": [
            "crypto_options_promotion_policy_contract_v1",
            "cleanup batch classifications",
            "No live trading",
            "manual orders",
        ],
        "first_artifacts": [
            "crypto_options_app/artifacts/team_coordination/fixed_chat_signal_strategy.md",
            "crypto_options_app/artifacts/reports/signal_strategy_cleanup_batch_latest.md",
            "crypto_options_app/artifacts/team_coordination/promotion_policy.md",
        ],
        "requires_cleanup_batch": True,
    },
}

FUTURE_ONLY_PROMPTS = (
    "db_data_observability.md",
    "indicator_dev.md",
    "indicator_qa.md",
    "signal_dev.md",
    "signal_qa.md",
    "strategy_dev.md",
    "strategy_qa.md",
)


def build_fixed_chat_startup_readiness(
    options: FixedChatStartupReadinessOptions | None = None,
) -> dict[str, Any]:
    options = options or FixedChatStartupReadinessOptions()
    team_root = Path(options.team_coordination_root)
    artifact_root = Path(options.artifact_root)
    generated_at = datetime.now(UTC).isoformat()
    common = _common_state(team_root=team_root, artifact_root=artifact_root)
    chats = {
        chat_id: _chat_state(
            chat_id=chat_id,
            contract=contract,
            team_root=team_root,
            artifact_root=artifact_root,
            common=common,
        )
        for chat_id, contract in FIXED_CHAT_CONTRACTS.items()
    }
    future = _future_prompt_state(team_root)
    blockers = sorted(
        {
            blocker
            for chat in chats.values()
            for blocker in chat.get("blockers", [])
            if blocker.startswith("shared:")
        }
    )
    ready_count = sum(1 for chat in chats.values() if chat.get("status") == "ready")
    return {
        "schema_version": FIXED_CHAT_STARTUP_READINESS_SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "status": "blocked" if blockers else "ready" if ready_count == len(chats) else "degraded",
        "ready_count": ready_count,
        "chat_count": len(chats),
        "shared_blockers": blockers,
        "common": common,
        "fixed_chats": chats,
        "future_only_prompts": future,
        "safety": {
            "manual_orders_avoided": True,
            "live_trading_authorized": False,
            "orders_allowed": False,
            "note": "Fixed-chat readiness does not authorize live trading or manual orders.",
        },
    }


def render_fixed_chat_startup_readiness_markdown(review: dict[str, Any]) -> str:
    lines = [
        "# Crypto Options Fixed Chat Startup Readiness",
        "",
        f"- Generated: `{review.get('generated_at_utc')}`",
        f"- Status: `{review.get('status')}`",
        f"- Ready chats: `{review.get('ready_count')}/{review.get('chat_count')}`",
        "- Live authority: `none`",
        "",
        "## Fixed Chats",
    ]
    for chat_id, chat in (review.get("fixed_chats") or {}).items():
        lines.extend(
            [
                "",
                f"### `{chat_id}`",
                "",
                f"- Status: `{chat.get('status')}`",
                f"- Prompt: `{chat.get('prompt_path')}`",
                f"- GitHub issues: `{', '.join(chat.get('github_issues') or [])}`",
                "- Blockers: "
                + (
                    "`none`"
                    if not chat.get("blockers")
                    else ", ".join(f"`{blocker}`" for blocker in chat.get("blockers") or [])
                ),
                "- Warnings: "
                + (
                    "`none`"
                    if not chat.get("warnings")
                    else ", ".join(f"`{warning}`" for warning in chat.get("warnings") or [])
                ),
                "- First artifacts:",
            ]
        )
        for artifact in chat.get("first_artifacts") or []:
            lines.append(f"  - `{artifact}`")
    lines.extend(["", "## Future Only Prompts"])
    for prompt in review.get("future_only_prompts") or []:
        lines.append(f"- `{prompt.get('path')}`: `{prompt.get('status')}`")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- No fixed chat may authorize live trading.",
            "- No fixed chat may place manual orders.",
            "- Supervised live remains gated by promotion policy, reconciliation, lifecycle coverage, and explicit runtime authorization.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_fixed_chat_startup_readiness_artifacts(
    review: dict[str, Any],
    *,
    artifact_root: Path = DEFAULT_CONFIG.artifact_root,
) -> dict[str, str]:
    report_dir = Path(artifact_root) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = report_dir / f"fixed_chat_startup_readiness_{stamp}.json"
    md_path = report_dir / f"fixed_chat_startup_readiness_{stamp}.md"
    latest_json = report_dir / "fixed_chat_startup_readiness_latest.json"
    latest_md = report_dir / "fixed_chat_startup_readiness_latest.md"
    json_text = json.dumps(review, indent=2, sort_keys=True, default=str)
    md_text = render_fixed_chat_startup_readiness_markdown(review)
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


def _common_state(*, team_root: Path, artifact_root: Path) -> dict[str, Any]:
    required = {
        "master_status": team_root / "master_status.md",
        "promotion_policy": team_root / "promotion_policy.md",
        "handoff_queue": team_root / "handoff_queue.jsonl",
        "fixed_chat_bootstrap": team_root / "fixed_chat_bootstrap.md",
        "github_source_of_truth_sync": team_root / "github_source_of_truth_sync.md",
        "transition_readiness_latest": artifact_root / "reports" / "transition_readiness_latest.json",
    }
    files = {name: {"path": str(path), "exists": path.exists()} for name, path in required.items()}
    blockers = [f"shared:missing_{name}" for name, row in files.items() if not row["exists"]]
    github_text = _read_text(required["github_source_of_truth_sync"])
    transition = _load_json(required["transition_readiness_latest"])
    safety_blockers = _transition_safety_blockers(transition)
    return {
        "status": "blocked" if blockers or safety_blockers else "ok",
        "files": files,
        "github_source_mentions_ready_fixed_chats": "Signal/strategy cleanup fixed chat: ready" in github_text
        and "Frontend fixed chat: ready" in github_text,
        "transition_readiness_status": transition.get("status") if isinstance(transition, dict) else None,
        "transition_readiness_path": str(required["transition_readiness_latest"]),
        "blockers": blockers + safety_blockers,
    }


def _chat_state(
    *,
    chat_id: str,
    contract: dict[str, Any],
    team_root: Path,
    artifact_root: Path,
    common: dict[str, Any],
) -> dict[str, Any]:
    prompt_path = team_root / contract["prompt"]
    lane_path = team_root / contract["lane_file"]
    github_path = team_root / "github_source_of_truth_sync.md"
    cleanup_path = artifact_root / "reports" / "signal_strategy_cleanup_batch_latest.md"
    prompt_text = _read_text(prompt_path)
    github_text = _read_text(github_path)
    blockers: list[str] = list(common.get("blockers") or [])
    warnings: list[str] = []
    if not prompt_path.exists():
        blockers.append("missing_prompt")
    if not lane_path.exists():
        blockers.append("missing_lane_file")
    for phrase in contract.get("required_phrases") or []:
        if phrase not in prompt_text:
            blockers.append(f"prompt_missing_phrase:{phrase}")
    missing_issues = [issue for issue in contract.get("github_issues") or [] if issue not in github_text]
    if missing_issues:
        blockers.append("github_sync_missing_issues:" + ",".join(missing_issues))
    if contract.get("requires_cleanup_batch") and not cleanup_path.exists():
        blockers.append("missing_signal_strategy_cleanup_batch")
    if common.get("transition_readiness_status") in {"blocked", None}:
        warnings.append("transition_readiness_not_ok")
    return {
        "status": "ready" if not blockers else "blocked",
        "prompt_path": str(prompt_path),
        "lane_file": str(lane_path),
        "github_issues": list(contract.get("github_issues") or []),
        "first_artifacts": list(contract.get("first_artifacts") or []),
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }


def _future_prompt_state(team_root: Path) -> list[dict[str, Any]]:
    rows = []
    prompt_root = team_root / "fixed_chat_prompts"
    for name in FUTURE_ONLY_PROMPTS:
        path = prompt_root / name
        rows.append(
            {
                "path": str(path),
                "exists": path.exists(),
                "status": "future_only" if path.exists() else "missing",
            }
        )
    return rows


def _transition_safety_blockers(payload: dict[str, Any]) -> list[str]:
    if not isinstance(payload, dict):
        return []
    blockers = []
    if payload.get("live_trading_authorized"):
        blockers.append("shared:transition_live_trading_authorized")
    if payload.get("manual_orders_avoided") is False:
        blockers.append("shared:transition_manual_orders_not_avoided")
    promotion = payload.get("promotion") if isinstance(payload.get("promotion"), dict) else {}
    if promotion.get("orders_allowed") or promotion.get("live_trading_authorized"):
        blockers.append("shared:promotion_orders_or_live_enabled")
    if promotion.get("accidental_live_authorized_count"):
        blockers.append("shared:promotion_accidental_live_authorized_rows")
    return blockers


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
