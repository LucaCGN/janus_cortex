from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.data.nodes.polymarket.crypto.live_capture import LiveCaptureTarget
from crypto_options_app.data_services.polymarket_option_price_capture import (
    OptionPriceCaptureConfig,
    capture_option_price_paths_once_sync,
)
from crypto_options_app.data_services.polymarket_live_activity_capture import (
    LiveActivityCaptureConfig,
    capture_live_activity_once_sync,
)
from crypto_options_app.db.connection import connect
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.indicators.event_path_stats import compute_event_path_stats


def test_option_price_capture_writes_ticks_depth_pairs_and_watermark(tmp_path) -> None:
    db_path = initialize_schema(tmp_path / "price_paths.sqlite")
    targets = [
        LiveCaptureTarget(
            event_id="event-1",
            event_slug="btc-updown-5m-1",
            market_id="market-1",
            condition_id="condition-1",
            token_id="up-token",
            outcome="Up",
            symbol="BTC",
            window_start_time="2026-06-04T00:00:00+00:00",
            window_end_time="2026-06-04T00:05:00+00:00",
            settlement_threshold=100000.0,
        ),
        LiveCaptureTarget(
            event_id="event-1",
            event_slug="btc-updown-5m-1",
            market_id="market-1",
            condition_id="condition-1",
            token_id="down-token",
            outcome="Down",
            symbol="BTC",
            window_start_time="2026-06-04T00:00:00+00:00",
            window_end_time="2026-06-04T00:05:00+00:00",
            settlement_threshold=100000.0,
        ),
    ]

    def fake_book(token_id: str) -> dict[str, object]:
        if token_id == "up-token":
            return {
                "asset_id": token_id,
                "timestamp": datetime(2026, 6, 4, tzinfo=UTC).isoformat(),
                "bids": [{"price": "0.45", "size": "20"}, {"price": "0.44", "size": "10"}],
                "asks": [{"price": "0.48", "size": "12"}, {"price": "0.49", "size": "8"}],
            }
        return {
            "asset_id": token_id,
            "timestamp": datetime(2026, 6, 4, tzinfo=UTC).isoformat(),
            "bids": [{"price": "0.52", "size": "18"}, {"price": "0.51", "size": "9"}],
            "asks": [{"price": "0.55", "size": "15"}, {"price": "0.56", "size": "7"}],
        }

    summary = capture_option_price_paths_once_sync(
        config=OptionPriceCaptureConfig(db_path=db_path, max_book_depth=2),
        targets=targets,
        order_book_fetcher=fake_book,
    )

    assert summary.status == "healthy"
    assert summary.tick_rows_inserted == 2
    assert summary.book_level_rows_inserted == 8
    assert summary.pair_snapshot_rows_inserted == 1
    assert summary.orders_allowed is False
    assert summary.live_trading_authorized is False
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM polymarket_price_ticks").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM polymarket_order_book_levels").fetchone()[0] == 8
        assert conn.execute("SELECT COUNT(*) FROM polymarket_updown_pair_snapshots").fetchone()[0] == 1
        readiness = conn.execute(
            """
            SELECT data_block, module_id, symbol, status, blockers_json, payload_json
            FROM data_signal_readiness_snapshots
            WHERE data_block='C' AND module_id='polymarket_option_price_capture'
            """
        ).fetchone()
        assert readiness is not None
        assert readiness["symbol"] == "BTC"
        assert readiness["status"] == "ready"
        stats = conn.execute("SELECT * FROM polymarket_event_path_stats").fetchone()
        assert stats is not None
        assert stats["snapshot_count"] == 1
        assert stats["trade_print_count"] == 0
        watermark = conn.execute(
            """
            SELECT status, rows_observed, rows_inserted, state_json
            FROM data_service_watermarks
            WHERE module_id='polymarket_option_price_capture'
            """
        ).fetchone()
        assert dict(watermark)["status"] == "healthy"
        assert dict(watermark)["rows_observed"] == 2


def test_event_path_stats_include_elapsed_price_path_and_swing_metrics() -> None:
    event = {
        "event_key": "event-path-1",
        "event_slug": "btc-updown-5m-path",
        "symbol": "BTC",
        "event_start_time_utc": "2026-06-04T00:00:00+00:00",
        "event_end_time_utc": "2026-06-04T00:05:00+00:00",
    }
    snapshots = [
        {
            "bucket_timestamp_utc": "2026-06-03T23:59:50+00:00",
            "up_mid_price": 0.45,
            "down_mid_price": 0.55,
            "up_best_bid": 0.42,
            "up_best_ask": 0.48,
            "down_best_bid": 0.52,
            "down_best_ask": 0.58,
            "up_depth_top3_bid_size": 10,
            "up_depth_top3_ask_size": 5,
            "down_depth_top3_bid_size": 4,
            "down_depth_top3_ask_size": 8,
        },
        {
            "bucket_timestamp_utc": "2026-06-04T00:00:00+00:00",
            "up_mid_price": 0.50,
            "down_mid_price": 0.50,
            "up_best_bid": 0.47,
            "up_best_ask": 0.53,
            "down_best_bid": 0.47,
            "down_best_ask": 0.53,
            "up_depth_top3_bid_size": 10,
            "up_depth_top3_ask_size": 5,
            "down_depth_top3_bid_size": 4,
            "down_depth_top3_ask_size": 8,
        },
        {
            "bucket_timestamp_utc": "2026-06-04T00:00:10+00:00",
            "up_mid_price": 0.60,
            "down_mid_price": 0.40,
            "up_best_bid": 0.57,
            "up_best_ask": 0.63,
            "down_best_bid": 0.37,
            "down_best_ask": 0.43,
            "up_depth_top3_bid_size": 12,
            "up_depth_top3_ask_size": 5,
            "down_depth_top3_bid_size": 5,
            "down_depth_top3_ask_size": 9,
        },
        {
            "bucket_timestamp_utc": "2026-06-04T00:00:30+00:00",
            "up_mid_price": 0.40,
            "down_mid_price": 0.60,
            "up_best_bid": 0.37,
            "up_best_ask": 0.43,
            "down_best_bid": 0.57,
            "down_best_ask": 0.63,
            "up_depth_top3_bid_size": 9,
            "up_depth_top3_ask_size": 4,
            "down_depth_top3_bid_size": 4,
            "down_depth_top3_ask_size": 9,
        },
        {
            "bucket_timestamp_utc": "2026-06-04T00:01:00+00:00",
            "up_mid_price": 0.90,
            "down_mid_price": 0.10,
            "up_best_bid": 0.87,
            "up_best_ask": 0.93,
            "down_best_bid": 0.07,
            "down_best_ask": 0.13,
            "up_depth_top3_bid_size": 11,
            "up_depth_top3_ask_size": 4,
            "down_depth_top3_bid_size": 4,
            "down_depth_top3_ask_size": 10,
        },
        {
            "bucket_timestamp_utc": "2026-06-04T00:01:10+00:00",
            "up_mid_price": 0.75,
            "down_mid_price": 0.25,
            "up_best_bid": 0.72,
            "up_best_ask": 0.78,
            "down_best_bid": 0.22,
            "down_best_ask": 0.28,
            "up_depth_top3_bid_size": 11,
            "up_depth_top3_ask_size": 5,
            "down_depth_top3_bid_size": 5,
            "down_depth_top3_ask_size": 9,
        },
    ]
    profile_context_snapshots = [
        {
            "computed_at_utc": "2026-06-04T00:00:40+00:00",
            "distribution": {
                "reconstructed_profile_prices": {
                    "up": 0.76,
                    "down": 0.24,
                }
            },
            "component_breakdown": {
                "by_grade_style": {
                    "S+ / hedger": {
                        "up_pressure_ratio": 0.7,
                        "down_pressure_ratio": 0.3,
                        "pressure_delta": 0.4,
                    }
                }
            },
        }
    ]
    crypto_context_snapshots = [
        {
            "provider": "ifcm",
            "symbol": "BTC",
            "interval": "1m",
            "completed_at_utc": "2026-06-04T00:00:35+00:00",
            "summary_label": "Strong Buy",
            "summary_score": 0.62,
        }
    ]

    stats = compute_event_path_stats(
        event=event,
        pair_snapshots=snapshots,
        profile_context_snapshots=profile_context_snapshots,
        crypto_context_snapshots=crypto_context_snapshots,
    )

    assert stats is not None
    assert stats.pre_event_price_points["-10s"] == 0.45
    assert stats.event_price_points["0s"] == 0.50
    assert stats.event_price_points["10s"] == 0.60
    assert stats.event_price_points["30s"] == 0.40
    assert stats.event_price_points["60s"] == 0.90
    assert stats.path_direction == "up"
    assert 0 < stats.path_efficiency < 1
    assert stats.time_to_first_extreme_seconds == 60
    assert stats.avg_swing_distance is not None and stats.avg_swing_distance > 0.1
    assert stats.max_rolling_30s_range == 0.50
    assert stats.level_first_touch_seconds["90c"] == 60
    assert stats.tail_comeback_table["sides"]["down"]["touched_10c"]["touched"] is True
    assert stats.tail_comeback_table["sides"]["down"]["touched_10c"]["reached_20c"] is True
    assert stats.tail_comeback_table["sides"]["down"]["touched_10c"]["volatility_bucket"] == "violent"
    assert stats.tail_comeback_table["sides"]["down"]["touched_10c"]["depth_pressure_bucket"] == "up_supportive"
    assert stats.tail_comeback_table["bucket_probabilities"]["touched_10c"]["gt180"] == 1.0
    assert stats.tail_comeback_table["context_probabilities"]["touched_10c"]["gt180"]["violent"] == 1.0
    assert (
        stats.tail_comeback_table["microstructure_context_probabilities"]["touched_10c"]["gt180"]["violent"][
            "up_supportive"
        ]
        == 1.0
    )
    assert stats.tail_comeback_table["sides"]["down"]["touched_10c"]["spread_bucket"] == "wide"
    assert stats.tail_comeback_table["sides"]["down"]["touched_10c"]["liquidity_bucket"] == "medium"
    assert stats.tail_comeback_table["sides"]["down"]["touched_10c"]["slippage_bucket"] == "moderate"
    assert (
        stats.tail_comeback_table["execution_context_probabilities"]["touched_10c"]["gt180"]["violent"][
            "wide|medium|moderate"
        ]
        == 1.0
    )
    assert stats.tail_comeback_table["sides"]["down"]["touched_10c"]["crypto_context_bucket"] == "btc|far_up"
    assert stats.tail_comeback_table["sides"]["down"]["touched_10c"]["crypto_symbol"] == "BTC"
    assert stats.tail_comeback_table["sides"]["down"]["touched_10c"]["crypto_distance_bucket"] == "far_up"
    assert (
        stats.tail_comeback_table["sides"]["down"]["touched_10c"]["crypto_distance_execution_context_bucket"]
        == "far_up||wide|medium|moderate"
    )
    assert (
        stats.tail_comeback_table["crypto_context_probabilities"]["touched_10c"]["gt180"]["violent"]["btc|far_up"]
        == 1.0
    )
    assert (
        stats.tail_comeback_table["crypto_distance_context_probabilities"]["touched_10c"]["gt180"]["violent"][
            "far_up"
        ]
        == 1.0
    )
    assert (
        stats.tail_comeback_table["crypto_distance_execution_context_probabilities"]["touched_10c"]["gt180"][
            "violent"
        ]["far_up"]["wide|medium|moderate"]
        == 1.0
    )
    assert stats.tail_comeback_table["sides"]["down"]["touched_10c"]["profile_context_bucket"] == "splus_hedger|up|up"
    assert (
        stats.tail_comeback_table["sides"]["down"]["touched_10c"]["profile_price_context_bucket"]
        == "splus_hedger|up|up_extreme"
    )
    assert (
        stats.tail_comeback_table["profile_context_probabilities"]["touched_10c"]["gt180"]["violent"][
            "splus_hedger|up|up"
        ]
        == 1.0
    )
    assert (
        stats.tail_comeback_table["profile_price_context_probabilities"]["touched_10c"]["gt180"]["violent"][
            "splus_hedger|up|up_extreme"
        ]
        == 1.0
    )
    assert (
        stats.tail_comeback_table["profile_price_crypto_context_probabilities"]["touched_10c"]["gt180"]["violent"][
            "splus_hedger|up|up_extreme"
        ]["far_up"]
        == 1.0
    )
    assert (
        stats.tail_comeback_table["profile_price_execution_context_probabilities"]["touched_10c"]["gt180"][
            "violent"
        ]["splus_hedger|up|up_extreme"]["wide|medium|moderate"]
        == 1.0
    )
    assert stats.tail_comeback_table["observability"]["context_counts"]["touched_10c"]["gt180"]["violent"]["touched"] == 1
    assert (
        stats.tail_comeback_table["observability"]["microstructure_context_counts"]["touched_10c"]["gt180"][
            "violent"
        ]["up_supportive"]["touched"]
        == 1
    )
    assert (
        stats.tail_comeback_table["observability"]["execution_context_counts"]["touched_10c"]["gt180"]["violent"][
            "wide|medium|moderate"
        ]["touched"]
        == 1
    )
    assert (
        stats.tail_comeback_table["observability"]["crypto_context_counts"]["touched_10c"]["gt180"]["violent"][
            "btc|far_up"
        ]["touched"]
        == 1
    )
    assert (
        stats.tail_comeback_table["observability"]["crypto_distance_context_counts"]["touched_10c"]["gt180"][
            "violent"
        ]["far_up"]["touched"]
        == 1
    )
    assert (
        stats.tail_comeback_table["observability"]["crypto_distance_execution_context_counts"]["touched_10c"][
            "gt180"
        ]["violent"]["far_up"]["wide|medium|moderate"]["touched"]
        == 1
    )
    assert (
        stats.tail_comeback_table["observability"]["profile_context_counts"]["touched_10c"]["gt180"]["violent"][
            "splus_hedger|up|up"
        ]["touched"]
        == 1
    )
    assert (
        stats.tail_comeback_table["observability"]["profile_price_context_counts"]["touched_10c"]["gt180"][
            "violent"
        ]["splus_hedger|up|up_extreme"]["touched"]
        == 1
    )
    assert (
        stats.tail_comeback_table["observability"]["profile_price_crypto_context_counts"]["touched_10c"]["gt180"][
            "violent"
        ]["splus_hedger|up|up_extreme"]["far_up"]["touched"]
        == 1
    )
    assert (
        stats.tail_comeback_table["observability"]["profile_price_execution_context_counts"]["touched_10c"]["gt180"][
            "violent"
        ]["splus_hedger|up|up_extreme"]["wide|medium|moderate"]["touched"]
        == 1
    )


def test_event_path_stats_clamps_negative_source_latency_pytest() -> None:
    event = {
        "event_key": "event-path-latency",
        "event_slug": "eth-updown-5m-path",
        "symbol": "ETH",
        "event_start_time_utc": "2026-06-04T00:00:00+00:00",
        "event_end_time_utc": "2026-06-04T00:05:00+00:00",
    }
    snapshots = [
        {
            "bucket_timestamp_utc": "2026-06-04T00:00:00+00:00",
            "up_mid_price": 0.52,
            "down_mid_price": 0.48,
            "source_latency_ms": -300,
        },
        {
            "bucket_timestamp_utc": "2026-06-04T00:00:10+00:00",
            "up_mid_price": 0.56,
            "down_mid_price": 0.44,
            "source_latency_ms": 150,
        },
    ]

    stats = compute_event_path_stats(event=event, pair_snapshots=snapshots)

    assert stats is not None
    assert stats.avg_source_latency_ms == 75.0
    assert stats.max_source_latency_ms == 150.0


def test_option_price_capture_rejects_live_flags(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JANUS_CRYPTO_OPTIONS_LIVE_EXECUTE", "1")
    with pytest.raises(RuntimeError, match="data_service_live_flags_rejected"):
        capture_option_price_paths_once_sync(
            config=OptionPriceCaptureConfig(db_path=tmp_path / "price_paths.sqlite"),
            targets=[],
            order_book_fetcher=lambda _token_id: {},
        )


def test_live_activity_capture_writes_trade_prints_and_watermark(tmp_path) -> None:
    db_path = initialize_schema(tmp_path / "live_activity.sqlite")
    now = datetime(2026, 6, 4, tzinfo=UTC).isoformat()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, condition_id, symbol, cadence_seconds,
                source_table, source_json, inserted_at_utc, updated_at_utc
            )
            VALUES (?, ?, ?, ?, 300, ?, '{}', ?, ?)
            """,
            ("event-1", "btc-updown-5m-1", "condition-1", "BTC", "polymarket_option_price_capture", now, now),
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, condition_id, event_slug,
                symbol, active, closed, source_table, source_json, inserted_at_utc, updated_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?, '{}', ?, ?)
            """,
            (
                "event-1:up",
                "event-1",
                "token-up",
                "Up",
                "condition-1",
                "btc-updown-5m-1",
                "BTC",
                "polymarket_option_price_capture",
                now,
                now,
            ),
        )

    def fake_activity(condition_id: str) -> dict[str, object]:
        assert condition_id == "condition-1"
        return {
            "trades": [
                {
                    "asset": "token-up",
                    "timestamp": 1780557001,
                    "price": "0.53",
                    "size": "4.0",
                    "side": "BUY",
                    "id": "trade-1",
                }
            ]
        }

    summary = capture_live_activity_once_sync(
        config=LiveActivityCaptureConfig(db_path=db_path),
        condition_ids=["condition-1"],
        activity_fetcher=fake_activity,
    )

    assert summary.status == "healthy"
    assert summary.trade_rows_inserted == 1
    assert summary.orders_allowed is False
    assert summary.live_trading_authorized is False
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM polymarket_trade_prints").fetchone()
        assert row["event_token_key"] == "event-1:up"
        assert row["token_id"] == "token-up"
        assert row["price"] == 0.53
        watermark = conn.execute(
            """
            SELECT status, rows_observed, rows_inserted
            FROM data_service_watermarks
            WHERE module_id='polymarket_live_activity_capture'
            """
        ).fetchone()
        assert watermark["status"] == "healthy"
        assert watermark["rows_inserted"] == 1
