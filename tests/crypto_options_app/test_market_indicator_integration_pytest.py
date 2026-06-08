from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from crypto_options_app.db.connection import connect, count_rows
from crypto_options_app.db.schema import create_schema
from crypto_options_app.feeds.polymarket_events import filter_discoverable_events
from crypto_options_app.feeds.polymarket_prices import insert_polymarket_price_tick, normalize_polymarket_price_tick
from crypto_options_app.feeds.underlying_prices import (
    insert_underlying_candle,
    insert_underlying_tick,
    normalize_underlying_candle,
    normalize_underlying_tick,
)
from crypto_options_app.indicators.compute import PriceTick, compute_indicator_snapshots
from crypto_options_app.signals.event_signals import build_event_context, insert_event_context
from crypto_options_app.signals.indicator_signals import insert_indicator_snapshot, summarize_indicator_snapshots


def test_event_universe_filter_keeps_current_future_recent_and_excludes_old_pytest() -> None:
    now = datetime(2026, 6, 3, 1, 0, tzinfo=UTC)
    events = [
        {"event_slug": "current", "event_start_time_utc": now - timedelta(minutes=1), "event_end_time_utc": now + timedelta(minutes=4)},
        {"event_slug": "future", "event_start_time_utc": now + timedelta(minutes=1), "event_end_time_utc": now + timedelta(minutes=6)},
        {"event_slug": "recent", "event_start_time_utc": now - timedelta(minutes=8), "event_end_time_utc": now - timedelta(minutes=2)},
        {"event_slug": "old", "event_start_time_utc": now - timedelta(hours=2), "event_end_time_utc": now - timedelta(hours=1)},
    ]

    selected = filter_discoverable_events(events, now_utc=now, recently_ended_window_seconds=600)

    assert {event["event_slug"] for event in selected} == {"current", "future", "recent"}


def test_polymarket_price_tick_normalization_persists_latency_and_latest_view_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "market.sqlite"
    chart = datetime(2026, 6, 3, 1, 0, 0, tzinfo=UTC)
    received = chart + timedelta(milliseconds=100)
    inserted = received + timedelta(milliseconds=50)

    tick = normalize_polymarket_price_tick(
        {
            "event_token_key": "event-1:up",
            "event_key": "event-1",
            "event_slug": "btc-updown-1",
            "token_id": "token-up",
            "outcome": "Up",
            "chart_timestamp_utc": chart.isoformat(),
            "best_bid": 0.61,
            "best_ask": 0.63,
            "depth_top3_bid_size": 100,
            "depth_top3_ask_size": 120,
        },
        system_received_at_utc=received,
        system_inserted_at_utc=inserted,
    )

    assert tick["mid_price"] == pytest.approx(0.62)
    assert tick["spread"] == pytest.approx(0.02)
    assert tick["source_latency_ms"] == pytest.approx(100.0)
    assert tick["insert_latency_ms"] == pytest.approx(50.0)

    with connect(db_path) as conn:
        create_schema(conn)
        conn.execute(
            """
            INSERT INTO events(event_key, inserted_at_utc, updated_at_utc)
            VALUES('event-1', ?, ?)
            """,
            (inserted.isoformat(), inserted.isoformat()),
        )
        conn.execute(
            """
            INSERT INTO event_tokens(event_token_key, event_key, token_id, outcome, inserted_at_utc, updated_at_utc)
            VALUES('event-1:up', 'event-1', 'token-up', 'Up', ?, ?)
            """,
            (inserted.isoformat(), inserted.isoformat()),
        )
        insert_polymarket_price_tick(conn, tick)
        latest = conn.execute(
            "SELECT token_id, mid_price, source_latency_ms FROM v_crypto_options_app_latest_polymarket_prices"
        ).fetchone()
        assert latest["token_id"] == "token-up"
        assert latest["mid_price"] == pytest.approx(0.62)
        assert latest["source_latency_ms"] == pytest.approx(100.0)


def test_underlying_tick_and_candle_normalization_persist_separately_from_odds_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "underlying.sqlite"
    inserted = datetime(2026, 6, 3, 1, 0, tzinfo=UTC)
    tick = normalize_underlying_tick(
        {
            "symbol": "btc",
            "source": "fixture_exchange",
            "observed_at_utc": inserted.isoformat(),
            "price": 100100.0,
            "bid": 100099.0,
            "ask": 100101.0,
        },
        inserted_at_utc=inserted,
    )
    candle = normalize_underlying_candle(
        {
            "symbol": "btc",
            "exchange": "fixture_exchange",
            "interval": "1m",
            "opened_at_utc": inserted.isoformat(),
            "closed_at_utc": (inserted + timedelta(minutes=1)).isoformat(),
            "open": 100000,
            "high": 100200,
            "low": 99950,
            "close": 100100,
            "volume": 12.5,
        },
        inserted_at_utc=inserted,
    )

    assert tick["symbol"] == "BTC"
    assert candle["symbol"] == "BTC"
    with connect(db_path) as conn:
        create_schema(conn)
        insert_underlying_tick(conn, tick)
        insert_underlying_candle(conn, candle)
        assert count_rows(conn, "underlying_price_ticks") == 1
        assert count_rows(conn, "underlying_candles") == 1
        assert count_rows(conn, "polymarket_price_ticks") == 0


def test_indicator_snapshots_compute_and_persist_with_event_context_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "indicators.sqlite"
    start = datetime(2026, 6, 3, 1, 0, tzinfo=UTC)
    ticks = [
        PriceTick("BTC", start + timedelta(seconds=15 * index), 100000.0 + index * 10.0, volume=1.0 + index)
        for index in range(20)
    ]

    snapshots = compute_indicator_snapshots(
        symbol="BTC",
        ticks=ticks,
        event_threshold_price=100100.0,
        computed_at_utc=ticks[-1].observed_at_utc,
    )
    summary = summarize_indicator_snapshots(snapshots)
    context = build_event_context(
        event_key="event-1",
        event_token_key="event-1:up",
        symbol="BTC",
        side="Up",
        event_threshold_price=100100.0,
        underlying_price=100190.0,
        event_end_time_utc=ticks[-1].observed_at_utc + timedelta(seconds=120),
        computed_at_utc=ticks[-1].observed_at_utc,
        indicator_summary=summary,
    )

    assert {snapshot.indicator_id for snapshot in snapshots} == {
        "target_relative_ema_momentum_v1",
        "volume_weighted_pressure_v1",
        "support_resistance_band_confluence_v1",
        "volatility_per_second_5m",
        "volatility_per_second_1h",
        "volatility_per_second_1d",
    }
    assert summary["sources"]
    assert context.target_delta_abs == 90.0
    assert context.target_delta_signed_for_side == 90.0
    assert context.time_remaining_seconds == 120.0

    with connect(db_path) as conn:
        create_schema(conn)
        for snapshot in snapshots:
            insert_indicator_snapshot(conn, snapshot)
        insert_event_context(conn, context)
        assert count_rows(conn, "indicator_snapshots") == 6
        row = conn.execute(
            "SELECT target_delta_abs, target_delta_signed_for_side, blocker_summary_json FROM event_indicator_context"
        ).fetchone()
        assert row["target_delta_abs"] == 90.0
        assert row["target_delta_signed_for_side"] == 90.0
        assert "time_remaining_seconds" in row["blocker_summary_json"]
