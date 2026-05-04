from __future__ import annotations

from app.modules.nba.execution.contracts import LiveRunConfig
from app.modules.nba.execution.runner import _evaluate_exit_reason, _run_game_entry_budget_state


def test_live_run_config_defaults_to_two_minimum_share_orders_pytest() -> None:
    config = LiveRunConfig(run_id="live-budget-demo")

    assert config.entry_target_notional_usd == 0.0
    assert config.max_entry_orders_per_game == 2
    assert config.max_entry_notional_per_game_usd == 10.0


def test_run_game_entry_budget_state_counts_only_matching_entry_orders_pytest() -> None:
    rows = [
        {
            "side": "buy",
            "status": "open",
            "limit_price": 0.4,
            "size": 2.5,
            "metadata_json": {
                "game_id": "0042500115",
                "execution_profile_version": "v1",
                "required_notional_usd": 1.0,
            },
        },
        {
            "side": "buy",
            "status": "submit_error",
            "limit_price": 0.4,
            "size": 2.5,
            "metadata_json": {
                "game_id": "0042500115",
                "execution_profile_version": "v1",
                "required_notional_usd": 1.0,
            },
        },
        {
            "side": "sell",
            "status": "open",
            "limit_price": 0.5,
            "size": 2.0,
            "metadata_json": {
                "game_id": "0042500115",
                "execution_profile_version": "v1",
                "required_notional_usd": 1.0,
            },
        },
        {
            "side": "buy",
            "status": "open",
            "limit_price": 0.5,
            "size": 2.0,
            "metadata_json": {
                "game_id": "0042500125",
                "execution_profile_version": "v1",
                "required_notional_usd": 1.0,
            },
        },
    ]

    state = _run_game_entry_budget_state(
        rows,
        game_id="0042500115",
        execution_profile_version="v1",
    )

    assert state.entry_order_count == 1
    assert state.entry_requested_notional_usd == 1.0


def test_evaluate_exit_reason_takes_profit_at_95_for_any_family_pytest() -> None:
    reason, stop_triggered = _evaluate_exit_reason(
        {
            "strategy_family": "inversion",
            "entry_metadata": {"exit_threshold": 0.49},
        },
        {
            "team_price": 0.95,
            "period_label": "Q4",
            "seconds_to_game_end": 42.0,
        },
    )

    assert reason == "take_profit_95"
    assert stop_triggered is False
