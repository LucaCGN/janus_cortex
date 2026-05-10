from __future__ import annotations

import threading
from collections import deque

from app.modules.nba.execution import runner as runner_mod
from app.modules.nba.execution.contracts import LiveRunConfig
from app.modules.nba.execution.runner import (
    LiveRunWorker,
    _evaluate_exit_reason,
    _resolve_resting_target_exit_price,
    _run_game_entry_budget_state,
    _should_confirm_contextual_scalp_stop,
)


def test_live_run_config_defaults_to_bounded_second_round_budget_pytest() -> None:
    config = LiveRunConfig(run_id="live-budget-demo")

    assert config.entry_target_notional_usd == 1.0
    assert config.max_entry_orders_per_game == 2
    assert config.max_entry_notional_per_game_usd == 2.0


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


def test_evaluate_exit_reason_manages_underdog_range_scalp_target_and_stop_pytest() -> None:
    position = {
        "strategy_family": "underdog_range_scalp",
        "entry_metadata": {"target_price": 0.28, "stop_price": 0.18},
        "stop_price": 0.18,
    }

    target_reason, target_stop_triggered = _evaluate_exit_reason(
        position,
        {
            "team_price": 0.29,
            "period_label": "Q3",
            "seconds_to_game_end": 900.0,
        },
    )
    stop_reason, stop_triggered = _evaluate_exit_reason(
        position,
        {
            "team_price": 0.17,
            "period_label": "Q3",
            "seconds_to_game_end": 900.0,
        },
    )

    assert target_reason == "target_hit"
    assert target_stop_triggered is False
    assert stop_reason == "stop_hit"
    assert stop_triggered is True


def test_evaluate_exit_reason_manages_favorite_floor_rebound_target_and_stop_pytest() -> None:
    position = {
        "strategy_family": "favorite_floor_rebound",
        "entry_metadata": {"target_price": 0.18, "stop_price": 0.08},
        "stop_price": 0.08,
    }

    target_reason, target_stop_triggered = _evaluate_exit_reason(
        position,
        {
            "team_price": 0.19,
            "period_label": "Q4",
            "seconds_to_game_end": 240.0,
        },
    )
    stop_reason, stop_triggered = _evaluate_exit_reason(
        position,
        {
            "team_price": 0.07,
            "period_label": "Q4",
            "seconds_to_game_end": 240.0,
        },
    )

    assert target_reason == "target_hit"
    assert target_stop_triggered is False
    assert stop_reason == "stop_hit"
    assert stop_triggered is True


def test_contextual_scalp_stop_requires_confirmation_when_score_context_survives_pytest() -> None:
    position = {
        "strategy_family": "underdog_range_scalp",
        "entry_metadata": {"max_close_score_gap": 10.0, "min_seconds_left": 120.0},
    }

    should_confirm = _should_confirm_contextual_scalp_stop(
        position,
        {
            "score_diff": -5,
            "seconds_to_game_end": 900.0,
        },
    )
    should_exit_immediately = _should_confirm_contextual_scalp_stop(
        position,
        {
            "score_diff": -13,
            "seconds_to_game_end": 900.0,
        },
    )

    assert should_confirm is True
    assert should_exit_immediately is False


def test_resolve_resting_target_exit_price_uses_strategy_target_or_minimum_gain_pytest() -> None:
    target_price = _resolve_resting_target_exit_price(
        {
            "entry_price": 0.20,
            "entry_metadata": {"target_price": 0.40},
        }
    )
    fallback_price = _resolve_resting_target_exit_price(
        {
            "entry_price": 0.20,
            "entry_metadata": {},
        }
    )

    assert target_price == 0.4
    assert fallback_price == 0.26


def test_handle_trade_fill_submits_resting_target_sell_pytest(tmp_path, monkeypatch) -> None:
    worker = LiveRunWorker.__new__(LiveRunWorker)
    worker.config = LiveRunConfig(run_id="live-bracket-test", dry_run=False)
    worker.run_root = tmp_path
    worker.account = {"account_id": "acct-1"}
    worker.events = deque(maxlen=20)
    worker.fill_metrics = []
    worker.active_orders = {}
    worker.active_positions = {}
    worker._lock = threading.RLock()

    submitted: list[dict] = []

    def fake_create_live_order(_connection, **kwargs):
        submitted.append(kwargs)
        return {
            "order_id": "target-order-1",
            "external_order_id": "external-target-1",
            "status": "submitted",
            "event_type": "live_place_submitted",
            "metadata_json": kwargs["metadata_json"],
        }

    monkeypatch.setattr(runner_mod, "create_live_order", fake_create_live_order)

    worker._handle_trade_fill(
        object(),
        {
            "signal_id": "underdog_range_scalp|004|away|42",
            "order_id": "entry-order-1",
            "side": "buy",
            "submitted_at": "2026-05-06T00:00:00+00:00",
            "game_id": "004",
            "matchup": "LAL at OKC",
            "market_id": "market-1",
            "outcome_id": "outcome-away",
            "token_id": "token-away",
            "team_side": "away",
            "strategy_family": "underdog_range_scalp",
            "size": 5.0,
            "signal_price": 0.20,
            "best_ask": 0.20,
            "stop_price": 0.16,
            "entry_metadata": {"target_price": 0.40, "stop_price": 0.16},
        },
        {
            "trade_id": "trade-entry-1",
            "price": 0.20,
            "size": 5.0,
            "trade_time": "2026-05-06T00:00:02+00:00",
        },
    )

    assert submitted
    assert submitted[0]["side"] == "sell"
    assert submitted[0]["price"] == 0.40
    assert submitted[0]["size"] == 5.0
    assert submitted[0]["metadata_json"]["order_policy"] == "resting_limit_target"
    assert "underdog_range_scalp|004|away|42" in worker.active_positions
    target_key = "underdog_range_scalp|004|away|42|target_exit"
    assert target_key in worker.active_orders
    assert worker.active_orders[target_key]["pending_action"] == "target_exit"


def test_reconcile_runtime_state_allows_active_order_mutation_during_fill_pytest(monkeypatch, tmp_path) -> None:
    worker = LiveRunWorker.__new__(LiveRunWorker)
    worker.config = LiveRunConfig(run_id="live-mutation-test", dry_run=False)
    worker.run_root = tmp_path
    worker.account = {"account_id": "acct-1"}
    worker.events = deque(maxlen=20)
    worker.active_orders = {
        "signal-1": {
            "order_id": "order-1",
            "signal_id": "signal-1",
            "submitted_at": "2026-05-09T00:00:00+00:00",
            "status": "open",
        }
    }
    worker.active_positions = {}
    worker._seen_trade_ids = set()
    worker._lock = threading.RLock()

    monkeypatch.setattr(runner_mod, "reconcile_live_order_fills", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runner_mod,
        "list_run_orders",
        lambda *args, **kwargs: [{"order_id": "order-1", "status": "filled"}],
    )
    monkeypatch.setattr(
        runner_mod,
        "list_run_trades",
        lambda *args, **kwargs: [{"order_id": "order-1", "trade_id": "trade-1"}],
    )
    monkeypatch.setattr(runner_mod, "list_latest_positions", lambda *args, **kwargs: [])

    def fake_handle_trade_fill(_connection, _order_state, _trade):
        worker.active_orders["signal-1|target"] = {"order_id": "target-1", "status": "open"}

    monkeypatch.setattr(worker, "_handle_trade_fill", fake_handle_trade_fill)

    worker._reconcile_runtime_state(connection=object())

    assert "signal-1" not in worker.active_orders
    assert worker.active_orders["signal-1|target"]["order_id"] == "target-1"
