from __future__ import annotations

from datetime import UTC, datetime, timedelta

from crypto_options_app.replay.fill_simulation import (
    PathFillSimulationConfig,
    ReplayOrder,
    simulate_fill_path,
)
from crypto_options_app.replay.frames import ReplayFrame


def test_path_fill_limit_waits_until_post_latency_quote_crosses_pytest() -> None:
    decision_at = datetime(2026, 6, 8, 10, 0, tzinfo=UTC)
    order = ReplayOrder(
        order_type="LIMIT",
        side="BUY",
        shares=10.0,
        limit_price=0.40,
        decision_at_utc=decision_at,
    )
    frames = [
        _frame(decision_at + timedelta(milliseconds=100), best_ask=0.35),
        _frame(decision_at + timedelta(milliseconds=700), best_ask=0.42),
        _frame(decision_at + timedelta(milliseconds=900), best_ask=0.39),
    ]

    result = simulate_fill_path(
        order,
        frames,
        config=PathFillSimulationConfig(latency_ms=500, ttl_seconds=2),
    )

    assert result.fillability_status == "filled"
    assert result.fill_price == 0.39
    assert result.filled_shares == 10.0
    assert result.simulation["frames_examined"] == 2
    assert result.simulation["quote_after_submit_seconds"] == 0.4


def test_path_fill_limit_misses_when_price_moves_away_during_latency_pytest() -> None:
    decision_at = datetime(2026, 6, 8, 10, 0, tzinfo=UTC)
    order = ReplayOrder(
        order_type="LIMIT",
        side="BUY",
        shares=10.0,
        limit_price=0.40,
        decision_at_utc=decision_at,
    )
    frames = [
        _frame(decision_at + timedelta(milliseconds=100), best_ask=0.35),
        _frame(decision_at + timedelta(milliseconds=700), best_ask=0.43),
        _frame(decision_at + timedelta(milliseconds=900), best_ask=0.44),
    ]

    result = simulate_fill_path(
        order,
        frames,
        config=PathFillSimulationConfig(latency_ms=500, ttl_seconds=1),
    )

    assert result.fillability_status == "unfilled"
    assert result.filled_shares == 0.0
    assert result.blockers == ("limit_not_crossed_during_ttl",)


def test_path_fill_market_can_partial_fill_after_latency_pytest() -> None:
    decision_at = datetime(2026, 6, 8, 10, 0, tzinfo=UTC)
    order = ReplayOrder(
        order_type="MARKET",
        side="BUY",
        shares=12.0,
        decision_at_utc=decision_at,
    )
    frames = [
        _frame(decision_at + timedelta(milliseconds=100), best_ask=0.35, ask_depth=20.0),
        _frame(decision_at + timedelta(milliseconds=700), best_ask=0.48, ask_depth=5.0),
    ]

    result = simulate_fill_path(
        order,
        frames,
        config=PathFillSimulationConfig(latency_ms=500, ttl_seconds=1),
    )

    assert result.fillability_status == "partial"
    assert result.fill_price == 0.48
    assert result.filled_shares == 5.0
    assert result.slippage > 0


def _frame(
    observed_at: datetime,
    *,
    best_ask: float,
    ask_depth: float = 25.0,
    best_bid: float = 0.34,
) -> ReplayFrame:
    return ReplayFrame(
        replay_frame_key=f"frame-{observed_at.timestamp()}",
        event_key="event",
        event_token_key="token",
        replay_timestamp_utc=observed_at,
        source_observed_at_utc=observed_at,
        decision_at_utc=observed_at,
        market_state={
            "system_received_at_utc": observed_at.isoformat(),
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": round((best_bid + best_ask) / 2, 4),
            "depth_top3_bid_size": 25.0,
            "depth_top3_ask_size": ask_depth,
        },
        underlying_state={
            "observed_at_utc": observed_at.isoformat(),
            "price": 100.0,
        },
    )
