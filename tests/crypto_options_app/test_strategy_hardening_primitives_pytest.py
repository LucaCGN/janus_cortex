from __future__ import annotations

from datetime import UTC, datetime, timedelta

from crypto_options_app.indicators.compute import OptionPairTick, compute_option_pair_indicator_snapshots
from crypto_options_app.signals.profile_signals import evaluate_profile_pressure_gate
from crypto_options_app.trading.managed_positions import (
    ManagedPositionState,
    cashout_rebuy_plan,
    hedge_rebalance_plan,
    stale_order_review_due,
)


def test_profile_pressure_gate_rejects_stale_high_conflict_rows() -> None:
    row = {
        "support_weight": 2,
        "conflict_weight": 3,
        "supporting_signals": [
            {"age_seconds": 240, "profile_grade": "S++", "action_side": "BUY"},
            {"age_seconds": 90, "profile_grade": "S", "action_side": "BUY"},
        ],
    }
    result = evaluate_profile_pressure_gate(row)
    assert result.executable is False
    assert "profile_pressure_conflict_too_high" in result.blockers
    assert "profile_pressure_stale" in result.blockers


def test_profile_pressure_gate_accepts_fresh_consensus_rows() -> None:
    row = {
        "support_weight": 4,
        "conflict_weight": 0,
        "supporting_signals": [
            {"age_seconds": 20, "profile_grade": "S++", "action_side": "BUY"},
            {"age_seconds": 35, "profile_grade": "S+", "action_side": "BUY"},
        ],
    }
    assert evaluate_profile_pressure_gate(row).executable is True


def test_option_pair_indicators_capture_divergence_depth_and_volatility() -> None:
    now = datetime(2026, 6, 4, tzinfo=UTC)
    ticks = [
        OptionPairTick("BTC", "event-1", now - timedelta(seconds=60), 0.40, 0.60, up_ask_depth=3, down_ask_depth=8),
        OptionPairTick("BTC", "event-1", now, 0.55, 0.45, up_bid_depth=20, down_bid_depth=2),
    ]
    snapshots = compute_option_pair_indicator_snapshots(symbol="BTC", ticks=ticks, computed_at_utc=now)
    by_id = {snapshot.indicator_id: snapshot for snapshot in snapshots}
    assert by_id["option_updown_pair_divergence_v1"].direction == "up"
    assert by_id["option_orderbook_depth_pressure_v1"].direction == "up"
    assert by_id["option_pair_volatility_per_second_5m_v1"].quality_flags["min_samples_met"] is True


def test_managed_position_cashout_rebuy_stale_review_and_hedge_rebalance() -> None:
    opened_at = datetime(2026, 6, 4, tzinfo=UTC)
    position = ManagedPositionState(
        strategy_id="profile_hedge_scalping_v4",
        event_token_key="event-1:up",
        outcome="Up",
        shares=10.0,
        entry_price=0.40,
        current_bid=0.53,
        current_ask=0.55,
        opened_at_utc=opened_at,
    )
    plans = cashout_rebuy_plan(position)
    assert [plan.intent_type for plan in plans] == ["cashout", "rebuy"]
    assert plans[0].side == "SELL"
    assert plans[1].side == "BUY"
    assert stale_order_review_due(created_at_utc=opened_at, now_utc=opened_at + timedelta(seconds=60))
    rebalance = hedge_rebalance_plan(
        current_up_cost=2.0,
        current_down_cost=8.0,
        target_up_ratio=0.50,
        max_additional_notional_usd=5.0,
        current_up_ask=0.50,
        current_down_ask=0.50,
    )
    assert rebalance is not None
    assert rebalance.side == "BUY"
    assert rebalance.intent_type == "hedge_rebalance"
