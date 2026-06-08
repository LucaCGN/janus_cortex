from __future__ import annotations

import json

from crypto_options_app.reports.automation_startup_readiness import (
    AUTOMATION_STARTUP_READINESS_SCHEMA_VERSION,
    AutomationStartupReadinessOptions,
    build_automation_startup_readiness,
    render_automation_startup_readiness_markdown,
)


def _write_minimum_ready_files(tmp_path) -> tuple:
    artifact_root = tmp_path / "artifacts"
    team_root = artifact_root / "team_coordination"
    report_root = artifact_root / "reports"
    team_root.mkdir(parents=True)
    report_root.mkdir(parents=True)
    (team_root / "automation_registry.md").write_text("registry\n", encoding="utf-8")
    (team_root / "handoff_queue.jsonl").write_text("{}\n", encoding="utf-8")
    (team_root / "fixed_chat_frontend.md").write_text("frontend\n", encoding="utf-8")
    prompt_root = team_root / "fixed_chat_prompts"
    prompt_root.mkdir()
    (prompt_root / "frontend_control_center_developer.md").write_text("frontend prompt\n", encoding="utf-8")
    (team_root / "fixed_chat_signal_strategy.md").write_text("signals\n", encoding="utf-8")
    (team_root / "promotion_policy.md").write_text("policy\n", encoding="utf-8")
    for name in [
        "storage_architecture_audit_latest.json",
        "runtime_audit_latest.json",
        "transition_readiness_latest.json",
    ]:
        (report_root / name).write_text(
            json.dumps(
                {
                    "status": "degraded",
                    "manual_orders_avoided": True,
                    "live_trading_authorized": False,
                    "promotion": {
                        "orders_allowed": False,
                        "live_trading_authorized": False,
                        "accidental_live_authorized_count": 0,
                    },
                }
            ),
            encoding="utf-8",
        )
    (report_root / "fixed_chat_startup_readiness_latest.json").write_text(
        json.dumps({"status": "ready"}),
        encoding="utf-8",
    )
    (report_root / "signal_strategy_cleanup_batch_latest.md").write_text("batch\n", encoding="utf-8")
    return artifact_root, team_root


def test_automation_startup_readiness_marks_limited_automations_ready(tmp_path) -> None:
    artifact_root, team_root = _write_minimum_ready_files(tmp_path)

    review = build_automation_startup_readiness(
        AutomationStartupReadinessOptions(
            artifact_root=artifact_root,
            team_coordination_root=team_root,
        )
    )

    assert review["schema_version"] == AUTOMATION_STARTUP_READINESS_SCHEMA_VERSION
    assert review["status"] == "ready_to_schedule"
    assert review["ready_count"] == 3
    assert review["activation"]["create_immediately"] is False
    assert "autonomous-live-promotion" in review["future_forbidden_automations"]


def test_automation_startup_readiness_blocks_when_fixed_chats_not_ready(tmp_path) -> None:
    artifact_root, team_root = _write_minimum_ready_files(tmp_path)
    (artifact_root / "reports" / "fixed_chat_startup_readiness_latest.json").write_text(
        json.dumps({"status": "blocked"}),
        encoding="utf-8",
    )

    review = build_automation_startup_readiness(
        AutomationStartupReadinessOptions(
            artifact_root=artifact_root,
            team_coordination_root=team_root,
        )
    )

    assert review["status"] == "blocked"
    assert "shared:fixed_chat_startup_not_ready" in review["shared_blockers"]
    assert review["planned_automations"]["signal-strategy-queue-worker"]["status"] == "blocked"


def test_automation_startup_readiness_blocks_live_enabled_transition(tmp_path) -> None:
    artifact_root, team_root = _write_minimum_ready_files(tmp_path)
    (artifact_root / "reports" / "transition_readiness_latest.json").write_text(
        json.dumps(
            {
                "status": "blocked",
                "manual_orders_avoided": True,
                "live_trading_authorized": True,
                "promotion": {"orders_allowed": False, "live_trading_authorized": False},
            }
        ),
        encoding="utf-8",
    )

    review = build_automation_startup_readiness(
        AutomationStartupReadinessOptions(
            artifact_root=artifact_root,
            team_coordination_root=team_root,
        )
    )

    assert review["status"] == "blocked"
    assert "shared:transition_live_trading_authorized" in review["shared_blockers"]


def test_render_automation_startup_readiness_markdown(tmp_path) -> None:
    artifact_root, team_root = _write_minimum_ready_files(tmp_path)
    review = build_automation_startup_readiness(
        AutomationStartupReadinessOptions(
            artifact_root=artifact_root,
            team_coordination_root=team_root,
        )
    )

    markdown = render_automation_startup_readiness_markdown(review)

    assert "Crypto Options Automation Startup Readiness" in markdown
    assert "db-data-observability" in markdown
    assert "signal-strategy-queue-worker" in markdown
    assert "Create immediately: `false`" in markdown
