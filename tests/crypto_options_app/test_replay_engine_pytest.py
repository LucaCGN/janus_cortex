from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from crypto_options_app.db.connection import connect, count_rows
from crypto_options_app.db.schema import create_schema
from crypto_options_app.replay.exit_simulation import simulate_cashout, simulate_hold_to_settlement, simulate_no_exit_loser
from crypto_options_app.replay.fill_simulation import ReplayOrder, simulate_fill
from crypto_options_app.replay.frames import build_replay_frame, build_replay_frame_from_price_path_db, data_coverage_blocker, insert_replay_frame
from crypto_options_app.replay.reports import build_component_report, insert_component_report


def test_replay_frame_generation_uses_contemporaneous_rows_and_persists_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "replay.sqlite"
    decision = datetime(2026, 6, 3, 1, 0, 5, tzinfo=UTC)
    result = build_replay_frame(
        event_key="event-1",
        event_token_key="event-1:up",
        decision_at_utc=decision,
        price_ticks=[
            _price_tick(decision - timedelta(seconds=2), mid=0.6),
            _price_tick(decision + timedelta(seconds=1), mid=0.9),
        ],
        underlying_ticks=[
            {"symbol": "BTC", "observed_at_utc": (decision - timedelta(seconds=1)).isoformat(), "price": 100100.0},
            {"symbol": "BTC", "observed_at_utc": (decision + timedelta(seconds=1)).isoformat(), "price": 100500.0},
        ],
        indicator_snapshots=[
            {"indicator_id": "target_relative_ema_momentum_v1", "computed_at_utc": (decision - timedelta(seconds=1)).isoformat(), "direction": "up"},
            {"indicator_id": "future", "computed_at_utc": (decision + timedelta(seconds=1)).isoformat(), "direction": "down"},
        ],
        profile_signals=[
            {"generator_id": "outcome_expectation", "evaluated_at_utc": (decision - timedelta(seconds=1)).isoformat(), "expected_side": "Up"}
        ],
        buying_ahead_rows=[{"activity_at_utc": (decision - timedelta(minutes=1)).isoformat()}],
    )

    assert result.blockers == ()
    assert result.frame is not None
    frame = result.frame
    assert frame.market_state["mid_price"] == 0.6
    assert frame.underlying_state["price"] == 100100.0
    assert len(frame.indicator_state["snapshots"]) == 1
    assert frame.buying_ahead is True

    with connect(db_path) as conn:
        create_schema(conn)
        insert_replay_frame(conn, replay_dataset_key=None, frame=frame)
        assert count_rows(conn, "replay_frames") == 1


def test_replay_frame_returns_structured_data_coverage_blocker_pytest() -> None:
    decision = datetime(2026, 6, 3, 1, 0, 5, tzinfo=UTC)
    result = build_replay_frame(
        event_key="event-1",
        event_token_key="event-1:up",
        decision_at_utc=decision,
        price_ticks=[],
        underlying_ticks=[],
    )
    blocker = data_coverage_blocker("candidate-1", result.blockers)

    assert result.frame is None
    assert result.blockers == ("missing_contemporaneous_polymarket_price",)
    assert blocker["status"] == "blocked"
    assert blocker["orders_allowed"] is False


def test_fillability_partial_unfilled_and_stale_quote_behavior_pytest() -> None:
    decision = datetime(2026, 6, 3, 1, 0, 5, tzinfo=UTC)
    frame = _frame(decision)

    market_buy = simulate_fill(
        ReplayOrder("market", "BUY", 10.0, decision, max_quote_age_seconds=10),
        frame,
    )
    limit_buy = simulate_fill(
        ReplayOrder("limit", "BUY", 10.0, decision, limit_price=0.55, max_quote_age_seconds=10),
        frame,
    )
    stale_buy = simulate_fill(
        ReplayOrder("market", "BUY", 1.0, decision + timedelta(seconds=30), max_quote_age_seconds=5),
        frame,
    )

    assert market_buy.fillability_status == "partial"
    assert market_buy.filled_shares == 5.0
    assert market_buy.fill_price == 0.62
    assert market_buy.slippage == pytest.approx(0.02)
    assert limit_buy.fillability_status == "unfilled"
    assert limit_buy.blockers == ("limit_not_crossed",)
    assert stale_buy.fillability_status == "blocked"
    assert stale_buy.blockers == ("stale_quote",)


def test_exit_settlement_cashout_and_component_report_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "reports.sqlite"
    decision = datetime(2026, 6, 3, 1, 0, 5, tzinfo=UTC)
    frame = _frame(decision)
    entry = simulate_fill(ReplayOrder("market", "BUY", 5.0, decision, max_quote_age_seconds=10), frame)
    exit_fill = simulate_fill(ReplayOrder("market", "SELL", 5.0, decision, max_quote_age_seconds=10), frame)

    settlement_win = simulate_hold_to_settlement(entry_fill=entry, entry_outcome="Up", resolved_outcome="Up")
    settlement_loss = simulate_no_exit_loser(entry)
    cashout = simulate_cashout(entry_fill=entry, exit_fill=exit_fill)
    report = build_component_report(component_id="outcome_expectation", outcomes=[settlement_win, settlement_loss, cashout])

    assert settlement_win.status == "settled"
    assert settlement_win.pnl_usd > 0
    assert settlement_loss.pnl_usd < 0
    assert cashout.realized is True
    assert report.sample_count == 3
    assert report.hit_rate == pytest.approx(1 / 3)
    assert report.metrics["realized_count"] == 3

    with connect(db_path) as conn:
        create_schema(conn)
        insert_component_report(conn, replay_run_key="replay-1", report=report)
        assert count_rows(conn, "replay_component_results") == 1


def test_price_path_db_replay_frame_supports_fill_and_cashout_simulation_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "price-path-replay.sqlite"
    decision = datetime(2026, 6, 4, 1, 0, 5, tzinfo=UTC)
    with connect(db_path) as conn:
        create_schema(conn)
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES (?, ?, ?, 300, '{}', ?, ?)
            """,
            (
                "event-1",
                "btc-updown-5m-1",
                "BTC",
                decision.isoformat(),
                decision.isoformat(),
            ),
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                active, closed, source_json, inserted_at_utc, updated_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, 0, '{}', ?, ?)
            """,
            (
                "event-1:up",
                "event-1",
                "token-up",
                "Up",
                "btc-updown-5m-1",
                "BTC",
                decision.isoformat(),
                decision.isoformat(),
            ),
        )
        conn.execute(
            """
            INSERT INTO polymarket_price_ticks(
                price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                mid_price, best_bid, best_ask, spread, depth_top3_bid_size, depth_top3_ask_size,
                source_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')
            """,
            (
                "entry-price",
                "event-1:up",
                "event-1",
                "token-up",
                "btc-updown-5m-1",
                "Up",
                (decision - timedelta(seconds=2)).isoformat(),
                (decision - timedelta(seconds=2)).isoformat(),
                (decision - timedelta(seconds=2)).isoformat(),
                0.50,
                0.49,
                0.51,
                0.02,
                20.0,
                30.0,
            ),
        )
        conn.execute(
            """
            INSERT INTO polymarket_price_ticks(
                price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                mid_price, best_bid, best_ask, spread, depth_top3_bid_size, depth_top3_ask_size,
                source_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')
            """,
            (
                "exit-price",
                "event-1:up",
                "event-1",
                "token-up",
                "btc-updown-5m-1",
                "Up",
                (decision + timedelta(seconds=25)).isoformat(),
                (decision + timedelta(seconds=25)).isoformat(),
                (decision + timedelta(seconds=25)).isoformat(),
                0.65,
                0.64,
                0.66,
                0.02,
                20.0,
                30.0,
            ),
        )
        entry_result = build_replay_frame_from_price_path_db(
            conn,
            event_token_key="event-1:up",
            decision_at_utc=decision,
        )
        exit_result = build_replay_frame_from_price_path_db(
            conn,
            event_token_key="event-1:up",
            decision_at_utc=decision + timedelta(seconds=30),
        )

    assert entry_result.frame is not None
    assert exit_result.frame is not None
    entry_fill = simulate_fill(ReplayOrder("market", "BUY", 2.0, decision, max_quote_age_seconds=10), entry_result.frame)
    exit_fill = simulate_fill(ReplayOrder("market", "SELL", 2.0, decision + timedelta(seconds=30), max_quote_age_seconds=10), exit_result.frame)
    cashout = simulate_cashout(entry_fill=entry_fill, exit_fill=exit_fill)
    assert cashout.realized is True
    assert cashout.pnl_usd > 0


def _price_tick(received_at: datetime, *, mid: float) -> dict[str, object]:
    return {
        "event_token_key": "event-1:up",
        "event_key": "event-1",
        "token_id": "token-up",
        "system_received_at_utc": received_at.isoformat(),
        "mid_price": mid,
        "best_bid": mid - 0.02,
        "best_ask": mid + 0.02,
        "depth_top3_bid_size": 5.0,
        "depth_top3_ask_size": 5.0,
    }


def _frame(decision: datetime):
    result = build_replay_frame(
        event_key="event-1",
        event_token_key="event-1:up",
        decision_at_utc=decision,
        price_ticks=[_price_tick(decision - timedelta(seconds=1), mid=0.6)],
        underlying_ticks=[
            {"symbol": "BTC", "observed_at_utc": (decision - timedelta(seconds=1)).isoformat(), "price": 100100.0}
        ],
    )
    assert result.frame is not None
    return result.frame
