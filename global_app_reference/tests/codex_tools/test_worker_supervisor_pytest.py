from __future__ import annotations

import json

from codex_tools.polymarket import (
    STRATEGY_WORKER_LEG_PLAN_SCHEMA_VERSION,
    STRATEGY_WORKER_STATUS_SCHEMA_VERSION,
    STRATEGY_WORKER_TICK_SCHEMA_VERSION,
    build_strategy_worker_leg_plan,
    build_strategy_worker_status,
    build_strategy_worker_tick,
)
from codex_tools.polymarket.cli import main as polymarket_cli_main


def test_strategy_worker_status_summarizes_dry_run_workers_pytest() -> None:
    status = build_strategy_worker_status(
        {
            "workers": [
                {
                    "worker_id": "worker-1",
                    "enabled": True,
                    "dry_run": True,
                    "strategy_mode": "sideways_grid",
                    "token_id": "token-1",
                }
            ]
        },
        now_utc="2026-05-31T00:00:00Z",
    )

    assert status["schema_version"] == STRATEGY_WORKER_STATUS_SCHEMA_VERSION
    assert status["status"] == "ok"
    assert status["active_dry_run_worker_count"] == 1
    assert status["order_preparation_attempted"] is False
    assert status["order_submission_attempted"] is False


def test_strategy_worker_leg_plan_points_to_gated_order_path_without_calling_it_pytest() -> None:
    plan = build_strategy_worker_leg_plan(
        {
            "worker_id": "worker-1",
            "enabled": True,
            "dry_run": True,
            "strategy_mode": "sideways_grid",
            "token_id": "token-1",
            "review_after_hours": 24,
        },
        action_plan={"status": "ready_for_approved_order_management_call"},
        requested_order={"side": "buy", "price": "0.10", "size": "5"},
        now_utc="2026-05-31T00:00:00Z",
    )

    assert plan["schema_version"] == STRATEGY_WORKER_LEG_PLAN_SCHEMA_VERSION
    assert plan["status"] == "ready_for_portfolio_manager_order_review"
    assert plan["portfolio_manager_order_command"][:3] == ["python", "-m", "codex_tools.polymarket.cli"]
    assert plan["order_preparation_attempted"] is False
    assert plan["order_submission_attempted"] is False


def test_strategy_worker_tick_records_dry_run_heartbeat_and_ledger_pytest() -> None:
    tick = build_strategy_worker_tick(
        {
            "worker_id": "worker-1",
            "enabled": True,
            "dry_run": True,
            "state": "dry_run_active",
            "strategy_mode": "sideways_grid",
            "token_id": "token-1",
        },
        now_utc="2026-05-31T00:00:00Z",
    )

    assert tick["schema_version"] == STRATEGY_WORKER_TICK_SCHEMA_VERSION
    assert tick["status"] == "dry_run_tick_recorded"
    assert tick["next_state"] == "dry_run_active"
    assert tick["heartbeat_json"]["worker_id"] == "worker-1"
    assert tick["ledger_event"]["event_type"] == "dry_run_tick"
    assert tick["order_preparation_attempted"] is False
    assert tick["order_submission_attempted"] is False


def test_strategy_worker_tick_expires_after_review_window_pytest() -> None:
    tick = build_strategy_worker_tick(
        {
            "worker_id": "worker-expired",
            "enabled": True,
            "dry_run": True,
            "state": "dry_run_active",
            "token_id": "token-1",
            "review_after_at": "2026-05-30T00:00:00Z",
        },
        now_utc="2026-05-31T00:00:00Z",
    )

    assert tick["status"] == "blocked"
    assert tick["next_state"] == "expired"
    assert "review_window_expired" in tick["blockers"]


def test_strategy_worker_tick_rejects_non_dry_run_worker_pytest() -> None:
    tick = build_strategy_worker_tick(
        {
            "worker_id": "worker-live",
            "enabled": True,
            "dry_run": False,
            "state": "dry_run_active",
            "token_id": "token-1",
        },
        now_utc="2026-05-31T00:00:00Z",
    )

    assert tick["status"] == "blocked"
    assert "non_dry_run_worker_not_supported" in tick["blockers"]
    assert tick["order_submission_attempted"] is False


def test_polymarket_cli_strategy_worker_status_outputs_inert_summary_pytest(tmp_path, capsys) -> None:
    config_path = tmp_path / "workers.json"
    config_path.write_text(
        json.dumps(
            {
                "workers": [
                    {
                        "worker_id": "worker-cli",
                        "enabled": True,
                        "dry_run": True,
                        "strategy_mode": "loss_recovery_scalp",
                        "token_id": "token-cli",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    exit_code = polymarket_cli_main(
        [
            "strategy-worker-status",
            "--worker-config-json",
            str(config_path),
            "--now-utc",
            "2026-05-31T00:00:00Z",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["schema_version"] == STRATEGY_WORKER_STATUS_SCHEMA_VERSION
    assert payload["order_preparation_attempted"] is False
