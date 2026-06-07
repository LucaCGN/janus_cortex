from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.config import DEFAULT_CONFIG


AUTOMATION_STARTUP_READINESS_SCHEMA_VERSION = "crypto_options_automation_startup_readiness_v1"
APP_ROOT = Path("crypto_options_app")
TEAM_COORDINATION_ROOT = APP_ROOT / "artifacts" / "team_coordination"


@dataclass(frozen=True)
class AutomationStartupReadinessOptions:
    artifact_root: Path = DEFAULT_CONFIG.artifact_root
    team_coordination_root: Path = TEAM_COORDINATION_ROOT


PLANNED_AUTOMATION_CONTRACTS: dict[str, dict[str, Any]] = {
    "db-data-observability": {
        "cadence": "15m",
        "reasoning": "low_or_medium",
        "mode": "report_first",
        "scope": "Postgres/runtime, A/B/C/D source health, storage audit, bounded blockers.",
        "required_artifacts": [
            "crypto_options_app/artifacts/reports/storage_architecture_audit_latest.json",
            "crypto_options_app/artifacts/reports/runtime_audit_latest.json",
            "crypto_options_app/artifacts/reports/transition_readiness_latest.json",
        ],
        "forbidden_actions": [
            "manual_orders",
            "live_trading",
            "monolithic_imports",
            "unbounded_replay",
        ],
    },
    "signal-strategy-queue-worker": {
        "cadence": "5-15m",
        "reasoning": "medium_or_high",
        "mode": "one_bounded_batch",
        "scope": "One signal/strategy cleanup row or one bounded batch from the policy-aware cleanup report.",
        "required_artifacts": [
            "crypto_options_app/artifacts/reports/signal_strategy_cleanup_batch_latest.md",
            "crypto_options_app/artifacts/team_coordination/fixed_chat_signal_strategy.md",
            "crypto_options_app/artifacts/team_coordination/promotion_policy.md",
        ],
        "forbidden_actions": [
            "manual_orders",
            "live_trading",
            "db_storage_infrastructure_changes",
            "frontend_styling_changes",
        ],
    },
    "frontend-status-reporter": {
        "cadence": "30-60m",
        "reasoning": "low_or_medium",
        "mode": "report_first",
        "scope": "Frontend/control-center endpoint status, UI contract drift, and read-only reporting.",
        "required_artifacts": [
            "crypto_options_app/artifacts/team_coordination/fixed_chat_frontend.md",
            "crypto_options_app/artifacts/team_coordination/fixed_chat_prompts/frontend_control_center_developer.md",
            "crypto_options_app/artifacts/reports/transition_readiness_latest.json",
        ],
        "forbidden_actions": [
            "manual_orders",
            "live_trading",
            "promotion_logic_changes",
            "db_storage_changes",
        ],
    },
}

FUTURE_FORBIDDEN_AUTOMATIONS = (
    "autonomous-live-promotion",
    "broad-multi-lane-worker-swarm",
)


def build_automation_startup_readiness(
    options: AutomationStartupReadinessOptions | None = None,
) -> dict[str, Any]:
    options = options or AutomationStartupReadinessOptions()
    artifact_root = Path(options.artifact_root)
    team_root = Path(options.team_coordination_root)
    generated_at = datetime.now(UTC).isoformat()
    common = _common_state(artifact_root=artifact_root, team_root=team_root)
    automations = {
        automation_id: _automation_state(
            automation_id=automation_id,
            contract=contract,
            common=common,
        )
        for automation_id, contract in PLANNED_AUTOMATION_CONTRACTS.items()
    }
    ready_count = sum(1 for row in automations.values() if row.get("status") == "ready_to_schedule")
    blockers = sorted(
        {
            blocker
            for row in automations.values()
            for blocker in row.get("blockers", [])
            if blocker.startswith("shared:")
        }
    )
    return {
        "schema_version": AUTOMATION_STARTUP_READINESS_SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "status": "blocked" if blockers else "ready_to_schedule" if ready_count == len(automations) else "degraded",
        "ready_count": ready_count,
        "automation_count": len(automations),
        "shared_blockers": blockers,
        "common": common,
        "planned_automations": automations,
        "future_forbidden_automations": list(FUTURE_FORBIDDEN_AUTOMATIONS),
        "activation": {
            "requires_user_or_master_request": True,
            "create_immediately": False,
            "note": "This report only gates limited automations; it does not create or authorize them.",
        },
        "safety": {
            "manual_orders_avoided": True,
            "live_trading_authorized": False,
            "orders_allowed": False,
            "no_autonomous_live_promotion": True,
        },
    }


def render_automation_startup_readiness_markdown(review: dict[str, Any]) -> str:
    lines = [
        "# Crypto Options Automation Startup Readiness",
        "",
        f"- Generated: `{review.get('generated_at_utc')}`",
        f"- Status: `{review.get('status')}`",
        f"- Ready limited automations: `{review.get('ready_count')}/{review.get('automation_count')}`",
        "- Create immediately: `false`",
        "- Live authority: `none`",
        "",
        "## Planned Limited Automations",
    ]
    for automation_id, row in (review.get("planned_automations") or {}).items():
        lines.extend(
            [
                "",
                f"### `{automation_id}`",
                "",
                f"- Status: `{row.get('status')}`",
                f"- Cadence: `{row.get('cadence')}`",
                f"- Reasoning: `{row.get('reasoning')}`",
                f"- Mode: `{row.get('mode')}`",
                f"- Scope: {row.get('scope')}",
                "- Blockers: "
                + (
                    "`none`"
                    if not row.get("blockers")
                    else ", ".join(f"`{blocker}`" for blocker in row.get("blockers") or [])
                ),
                "- Required artifacts:",
            ]
        )
        for artifact in row.get("required_artifacts") or []:
            lines.append(f"  - `{artifact}`")
    lines.extend(["", "## Forbidden Until Supervised Live Gate Exists"])
    for name in review.get("future_forbidden_automations") or []:
        lines.append(f"- `{name}`")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- No automation may authorize live trading.",
            "- No automation may place manual orders.",
            "- No limited automation should be created unless this report remains `ready_to_schedule` and the master/user explicitly requests scheduling.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_automation_startup_readiness_artifacts(
    review: dict[str, Any],
    *,
    artifact_root: Path = DEFAULT_CONFIG.artifact_root,
) -> dict[str, str]:
    report_dir = Path(artifact_root) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = report_dir / f"automation_startup_readiness_{stamp}.json"
    md_path = report_dir / f"automation_startup_readiness_{stamp}.md"
    latest_json = report_dir / "automation_startup_readiness_latest.json"
    latest_md = report_dir / "automation_startup_readiness_latest.md"
    json_text = json.dumps(review, indent=2, sort_keys=True, default=str)
    md_text = render_automation_startup_readiness_markdown(review)
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


def _common_state(*, artifact_root: Path, team_root: Path) -> dict[str, Any]:
    required = {
        "automation_registry": team_root / "automation_registry.md",
        "handoff_queue": team_root / "handoff_queue.jsonl",
        "fixed_chat_startup_readiness": artifact_root / "reports" / "fixed_chat_startup_readiness_latest.json",
        "transition_readiness": artifact_root / "reports" / "transition_readiness_latest.json",
    }
    files = {name: {"path": str(path), "exists": path.exists()} for name, path in required.items()}
    blockers = [f"shared:missing_{name}" for name, row in files.items() if not row["exists"]]
    fixed_chat = _load_json(required["fixed_chat_startup_readiness"])
    transition = _load_json(required["transition_readiness"])
    if fixed_chat.get("status") != "ready":
        blockers.append("shared:fixed_chat_startup_not_ready")
    blockers.extend(_transition_safety_blockers(transition))
    return {
        "status": "blocked" if blockers else "ok",
        "files": files,
        "fixed_chat_startup_status": fixed_chat.get("status"),
        "transition_readiness_status": transition.get("status"),
        "blockers": sorted(set(blockers)),
    }


def _automation_state(
    *,
    automation_id: str,
    contract: dict[str, Any],
    common: dict[str, Any],
) -> dict[str, Any]:
    blockers: list[str] = list(common.get("blockers") or [])
    missing_artifacts = [path for path in contract.get("required_artifacts") or [] if not Path(path).exists()]
    blockers.extend(f"missing_artifact:{path}" for path in missing_artifacts)
    return {
        "status": "ready_to_schedule" if not blockers else "blocked",
        "cadence": contract.get("cadence"),
        "reasoning": contract.get("reasoning"),
        "mode": contract.get("mode"),
        "scope": contract.get("scope"),
        "required_artifacts": list(contract.get("required_artifacts") or []),
        "forbidden_actions": list(contract.get("forbidden_actions") or []),
        "blockers": sorted(set(blockers)),
    }


def _transition_safety_blockers(payload: dict[str, Any]) -> list[str]:
    if not isinstance(payload, dict):
        return []
    blockers: list[str] = []
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


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
