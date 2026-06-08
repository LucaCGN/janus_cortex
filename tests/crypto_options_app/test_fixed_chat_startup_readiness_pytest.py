from __future__ import annotations

import json

from crypto_options_app.reports.fixed_chat_startup_readiness import (
    FIXED_CHAT_STARTUP_READINESS_SCHEMA_VERSION,
    FixedChatStartupReadinessOptions,
    build_fixed_chat_startup_readiness,
    render_fixed_chat_startup_readiness_markdown,
)


def _write_minimum_ready_files(tmp_path) -> tuple:
    artifact_root = tmp_path / "artifacts"
    team_root = artifact_root / "team_coordination"
    prompt_root = team_root / "fixed_chat_prompts"
    report_root = artifact_root / "reports"
    prompt_root.mkdir(parents=True)
    report_root.mkdir(parents=True)
    for name in [
        "master_status.md",
        "promotion_policy.md",
        "handoff_queue.jsonl",
        "fixed_chat_bootstrap.md",
        "fixed_chat_frontend.md",
        "fixed_chat_signal_strategy.md",
    ]:
        (team_root / name).write_text("ok\n", encoding="utf-8")
    (team_root / "github_source_of_truth_sync.md").write_text(
        "Frontend fixed chat: ready\n"
        "Signal/strategy cleanup fixed chat: ready\n"
        "#155 #156 #157 #158 #159\n"
        "#160 #161 #162 #163 #164\n",
        encoding="utf-8",
    )
    (prompt_root / "frontend_control_center_developer.md").write_text(
        "Do not work on DB infrastructure, promotion logic, or trading runtime.\n",
        encoding="utf-8",
    )
    (prompt_root / "signal_strategy_management_cleanup.md").write_text(
        "Use crypto_options_promotion_policy_contract_v1. "
        "Treat cleanup batch classifications as guidance. "
        "No live trading and no manual orders.\n",
        encoding="utf-8",
    )
    for name in [
        "db_data_observability.md",
        "indicator_dev.md",
        "indicator_qa.md",
        "signal_dev.md",
        "signal_qa.md",
        "strategy_dev.md",
        "strategy_qa.md",
    ]:
        (prompt_root / name).write_text("future only\n", encoding="utf-8")
    (report_root / "transition_readiness_latest.json").write_text(
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
    (report_root / "signal_strategy_cleanup_batch_latest.md").write_text(
        "# Signal And Strategy Cleanup Batch\n",
        encoding="utf-8",
    )
    return artifact_root, team_root


def test_fixed_chat_startup_readiness_marks_two_chats_ready(tmp_path) -> None:
    artifact_root, team_root = _write_minimum_ready_files(tmp_path)

    review = build_fixed_chat_startup_readiness(
        FixedChatStartupReadinessOptions(
            artifact_root=artifact_root,
            team_coordination_root=team_root,
        )
    )

    assert review["schema_version"] == FIXED_CHAT_STARTUP_READINESS_SCHEMA_VERSION
    assert review["status"] == "ready"
    assert review["ready_count"] == 2
    assert review["fixed_chats"]["frontend_control_center_developer"]["status"] == "ready"
    assert review["fixed_chats"]["signal_strategy_management_cleanup"]["status"] == "ready"


def test_fixed_chat_startup_readiness_blocks_missing_cleanup_batch(tmp_path) -> None:
    artifact_root, team_root = _write_minimum_ready_files(tmp_path)
    (artifact_root / "reports" / "signal_strategy_cleanup_batch_latest.md").unlink()

    review = build_fixed_chat_startup_readiness(
        FixedChatStartupReadinessOptions(
            artifact_root=artifact_root,
            team_coordination_root=team_root,
        )
    )

    signal_chat = review["fixed_chats"]["signal_strategy_management_cleanup"]
    assert signal_chat["status"] == "blocked"
    assert "missing_signal_strategy_cleanup_batch" in signal_chat["blockers"]
    assert review["fixed_chats"]["frontend_control_center_developer"]["status"] == "ready"


def test_fixed_chat_startup_readiness_blocks_live_enabled_transition(tmp_path) -> None:
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

    review = build_fixed_chat_startup_readiness(
        FixedChatStartupReadinessOptions(
            artifact_root=artifact_root,
            team_coordination_root=team_root,
        )
    )

    assert review["status"] == "blocked"
    assert "shared:transition_live_trading_authorized" in review["shared_blockers"]
    assert "shared:transition_live_trading_authorized" in review["fixed_chats"]["frontend_control_center_developer"][
        "blockers"
    ]


def test_render_fixed_chat_startup_readiness_markdown(tmp_path) -> None:
    artifact_root, team_root = _write_minimum_ready_files(tmp_path)
    review = build_fixed_chat_startup_readiness(
        FixedChatStartupReadinessOptions(
            artifact_root=artifact_root,
            team_coordination_root=team_root,
        )
    )

    markdown = render_fixed_chat_startup_readiness_markdown(review)

    assert "Crypto Options Fixed Chat Startup Readiness" in markdown
    assert "frontend_control_center_developer" in markdown
    assert "signal_strategy_management_cleanup" in markdown
    assert "Live authority: `none`" in markdown
