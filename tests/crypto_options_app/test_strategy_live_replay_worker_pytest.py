from __future__ import annotations

import json
from pathlib import Path

import pytest

from crypto_options_app.db.connection import connect, count_rows
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.strategies.registry import get_strategy
from crypto_options_app.trading.executor_boundary import ExecutorBoundaryConfig
from crypto_options_app.workers.runtime_adapter import RuntimeScenario, SupervisedRuntimeConfig, validate_all_strategy_scenarios
from crypto_options_app.workers.strategy_backtest_replay import StrategyBacktestReplayConfig, run_strategy_backtest_replay
from crypto_options_app.workers.strategy_live_replay import (
    StrategyLiveReplayConfig,
    run_strategy_live_replay,
    scout_strategy_live_replay_candidates,
)


def _insert_profile_context(
    conn,
    *,
    event_key: str,
    event_slug: str,
    up_ratio: float = 0.7,
    up_reconstructed_price: float = 0.5,
    down_reconstructed_price: float = 0.5,
    shares_up_ratio: float | None = None,
    count_up_ratio: float | None = None,
    coverage_warnings: list[str] | None = None,
    source_age_seconds: float = 3.0,
) -> None:
    down_ratio = 1.0 - up_ratio
    shares_up_ratio = up_ratio if shares_up_ratio is None else shares_up_ratio
    shares_down_ratio = 1.0 - shares_up_ratio
    count_up_ratio = up_ratio if count_up_ratio is None else count_up_ratio
    count_down_ratio = 1.0 - count_up_ratio
    coverage_warnings = coverage_warnings or []
    conn.execute(
        """
        INSERT INTO profile_distribution_snapshots(
            distribution_snapshot_key, event_key, event_slug, symbol, phase, computed_at_utc,
            event_start_time_utc, event_end_time_utc, source_mode, canonical_method,
            profile_count, component_count, up_weight, down_weight, up_share_weight,
            down_share_weight, up_cost_weight, down_cost_weight, up_count_weight,
            down_count_weight, distribution_json, source_json, blockers_json, inserted_at_utc
        )
        VALUES(?, ?, ?, 'BTC', 'live', '2026-06-05T20:00:30+00:00',
               '2026-06-05T20:00:00+00:00', '2026-06-05T20:05:00+00:00',
               'raw_activity', 'cost_weighted', 2, 2, ?, ?, 100, 100, ?, ?,
               1, 1, ?, '{"raw_activity_rows": 120, "position_rows": 0, "event_order_rows": 0}',
               '[]', '2026-06-05T20:00:30+00:00')
        """,
        (
            f"{event_key}-profile-snapshot",
            event_key,
            event_slug,
            up_ratio,
            down_ratio,
            up_ratio,
            down_ratio,
            json.dumps(
                {
                    "variants": {
                        "cost_weighted": {"up": up_ratio, "down": down_ratio},
                        "shares_weighted": {"up": shares_up_ratio, "down": shares_down_ratio},
                        "profile_count_weighted": {"up": count_up_ratio, "down": count_down_ratio},
                    },
                    "top_profiles_distribution": {"up": up_ratio, "down": down_ratio},
                    "pressure_delta": up_ratio - down_ratio,
                    "source_age_seconds": source_age_seconds,
                    "target_refresh_seconds": 30.0,
                    "reconstructed_profile_prices": {
                        "up": up_reconstructed_price,
                        "down": down_reconstructed_price,
                        "pair_sum": up_reconstructed_price + down_reconstructed_price,
                    },
                    "coverage_warnings": coverage_warnings,
                    "latest_source_at_utc": "2026-06-05T20:00:27+00:00",
                }
            ),
        ),
    )


def _insert_crypto_context(conn, *, symbol: str = "BTC", score: float = 0.6, label: str = "Buy") -> None:
    conn.execute(
        """
        INSERT INTO external_technical_observer_snapshots(
            observer_snapshot_key, provider, symbol, interval, source_url,
            request_started_at_utc, observed_at_utc, completed_at_utc,
            latency_ms, summary_label, summary_score, buy_count, sell_count,
            neutral_count, error_count, component_count, components_json,
            source_json, inserted_at_utc
        )
        VALUES(?, 'ifcm', ?, '1m', 'https://example.test',
               '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:01+00:00',
               '2026-06-05T20:00:02+00:00', 100, ?, ?, 8, 2, 1, 0,
               11, '{}', '{}', '2026-06-05T20:00:02+00:00')
        """,
        (f"{symbol}-technical", symbol, label, score),
    )


def _insert_pair_snapshot(
    conn,
    *,
    event_key: str,
    event_slug: str,
    timestamp: str,
    up_bid: float,
    up_ask: float,
    down_bid: float,
    down_ask: float,
    key_suffix: str,
) -> None:
    conn.execute(
        """
        INSERT INTO polymarket_updown_pair_snapshots(
            pair_snapshot_key, event_key, event_slug, symbol, bucket_timestamp_utc,
            up_event_token_key, down_event_token_key, up_token_id, down_token_id,
            up_best_bid, up_best_ask, up_mid_price, down_best_bid, down_best_ask,
            down_mid_price, up_depth_top3_bid_size, up_depth_top3_ask_size,
            down_depth_top3_bid_size, down_depth_top3_ask_size, source_latency_ms,
            source_json, inserted_at_utc
        )
        VALUES(
            ?, ?, ?, 'BTC', ?, 'pytest-event:up', 'pytest-event:down',
            'pytest-token-up', 'pytest-token-down', ?, ?, ?, ?, ?, ?,
            100, 100, 100, 100, 25, '{}', ?
        )
        """,
        (
            f"{event_key}:pair:{key_suffix}",
            event_key,
            event_slug,
            timestamp,
            up_bid,
            up_ask,
            (up_bid + up_ask) / 2.0,
            down_bid,
            down_ask,
            (down_bid + down_ask) / 2.0,
            timestamp,
        ),
    )


def _insert_splus_hedger_components(conn, *, event_key: str, up_cost: float = 70.0, down_cost: float = 30.0) -> None:
    snapshot_key = f"{event_key}-profile-snapshot"
    for outcome, cost, shares in (("Up", up_cost, 100.0), ("Down", down_cost, 100.0)):
        conn.execute(
            """
            INSERT INTO profile_distribution_components(
                distribution_component_key, distribution_snapshot_key, profile_key,
                handle, grade, trading_style, source_mode, outcome, net_shares,
                net_notional_usd, cost_basis_usd, grade_weight, style_weight,
                final_weight, contribution_json, inserted_at_utc
            )
            VALUES(?, ?, ?, '@pytest-splus-hedger', 'S+', 'hedger',
                   'raw_activity', ?, ?, ?, ?, 1, 1, 1, '{}',
                   '2026-06-05T20:00:30+00:00')
            """,
            (
                f"{snapshot_key}:{outcome.lower()}",
                snapshot_key,
                f"pytest-splus-hedger-{outcome.lower()}",
                outcome,
                shares,
                cost,
                cost,
            ),
        )


def _insert_distributed_splus_hedger_components(
    conn,
    *,
    event_key: str,
    up_cost: float = 184.0,
    down_cost: float = 16.0,
    profile_count: int = 2,
) -> None:
    snapshot_key = f"{event_key}-profile-snapshot"
    per_profile_up = up_cost / profile_count
    per_profile_down = down_cost / profile_count
    for index in range(profile_count):
        profile_key = f"pytest-splus-hedger-{index + 1}"
        for outcome, cost, shares in (("Up", per_profile_up, 100.0), ("Down", per_profile_down, 100.0)):
            conn.execute(
                """
                INSERT INTO profile_distribution_components(
                    distribution_component_key, distribution_snapshot_key, profile_key,
                    handle, grade, trading_style, source_mode, outcome, net_shares,
                    net_notional_usd, cost_basis_usd, grade_weight, style_weight,
                    final_weight, contribution_json, inserted_at_utc
                )
                VALUES(?, ?, ?, ?, 'S+', 'hedger',
                       'raw_activity', ?, ?, ?, ?, 1, 1, 1, '{}',
                       '2026-06-05T20:00:30+00:00')
                """,
                (
                    f"{snapshot_key}:{profile_key}:{outcome.lower()}",
                    snapshot_key,
                    profile_key,
                    f"@pytest-splus-hedger-{index + 1}",
                    outcome,
                    shares,
                    cost,
                    cost,
                ),
            )


def _insert_single_dominant_profile_components(
    conn,
    *,
    event_key: str,
    dominant_up_cost: float = 70.0,
    supporting_down_cost: float = 30.0,
) -> None:
    snapshot_key = f"{event_key}-profile-snapshot"
    rows = (
        ("dominant-up", "Up", dominant_up_cost, 120.0),
        ("support-down", "Down", supporting_down_cost, 80.0),
    )
    for profile_suffix, outcome, cost, shares in rows:
        conn.execute(
            """
            INSERT INTO profile_distribution_components(
                distribution_component_key, distribution_snapshot_key, profile_key,
                handle, grade, trading_style, source_mode, outcome, net_shares,
                net_notional_usd, cost_basis_usd, grade_weight, style_weight,
                final_weight, contribution_json, inserted_at_utc
            )
            VALUES(?, ?, ?, ?, 'S+', 'hedger',
                   'raw_activity', ?, ?, ?, ?, 1, 1, 1, '{}',
                   '2026-06-05T20:00:30+00:00')
            """,
            (
                f"{snapshot_key}:{profile_suffix}",
                snapshot_key,
                f"pytest-{profile_suffix}",
                f"@pytest-{profile_suffix}",
                outcome,
                shares,
                cost,
                cost,
            ),
        )


def _insert_option_path_stats(
    conn,
    *,
    event_key: str,
    event_slug: str,
    avg_rolling_60s_range: float,
    pair_sum_range: float = 0.05,
    snapshot_count: int = 8,
    max_rolling_60s_range: float = 0.08,
    level_crossing_count: int = 2,
    near_50c_sample_count: int = 3,
    rebound_direction_flip_count: int = 1,
    strong_rebound_touch_count: int = 1,
    avg_pair_depth_pressure: float = 0.12,
    avg_source_latency_ms: float = 120.0,
    max_source_latency_ms: float = 180.0,
) -> None:
    conn.execute(
        """
        INSERT INTO polymarket_event_path_stats(
            event_path_stats_key, event_key, event_slug, symbol,
            event_start_time_utc, event_end_time_utc, computed_at_utc,
            first_snapshot_at_utc, last_snapshot_at_utc, snapshot_count,
            up_first_price, up_last_price, up_min_price, up_max_price,
            up_range, up_abs_move_sum, up_abs_move_per_minute, up_stddev,
            path_direction, path_efficiency, avg_rolling_60s_range,
            max_rolling_60s_range, level_crossing_count,
            near_50c_sample_count, rebound_direction_flip_count,
            strong_rebound_touch_count, pair_sum_range, avg_pair_depth_pressure, avg_source_latency_ms,
            max_source_latency_ms, source_json, inserted_at_utc, updated_at_utc
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"event_path_stats:{event_key}",
            event_key,
            event_slug,
            "BTC",
            "2026-06-05T20:00:00+00:00",
            "2026-06-05T20:05:00+00:00",
            "2026-06-05T20:04:50+00:00",
            "2026-06-05T20:00:00+00:00",
            "2026-06-05T20:04:50+00:00",
            snapshot_count,
            0.50,
            0.58,
            0.45,
            0.62,
            0.17,
            0.24,
            0.048,
            0.04,
            "up",
            0.5,
            avg_rolling_60s_range,
            max_rolling_60s_range,
            level_crossing_count,
            near_50c_sample_count,
            rebound_direction_flip_count,
            strong_rebound_touch_count,
            pair_sum_range,
            avg_pair_depth_pressure,
            avg_source_latency_ms,
            max_source_latency_ms,
            "{}",
            "2026-06-05T20:04:50+00:00",
            "2026-06-05T20:04:50+00:00",
        ),
    )


def test_strategy_backtest_replay_worker_persists_historical_lifecycle_rows_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-backtest-replay.sqlite")

    payload = run_strategy_backtest_replay(
        StrategyBacktestReplayConfig(
            db_path=db_path,
            run_id="pytest-strategy-backtest-replay",
            strategy_ids=("profile_hedge_scalping_v1", "hedger_ratio_replication_v4"),
            max_trades_per_strategy=1,
        )
    )

    assert payload["schema_version"] == "crypto_options_strategy_backtest_replay_run_v1"
    assert payload["runtime_mode"] == "dry_run"
    assert payload["orders_allowed"] is False
    assert payload["live_trading_authorized"] is False
    assert payload["manual_orders_avoided"] is True
    assert payload["result_count"] == 2
    with connect(Path(db_path)) as conn:
        assert count_rows(conn, "strategy_validation_runs") == 2
        assert count_rows(conn, "validation_budget_ledger") == 0
        phases = {
            row["run_phase"]
            for row in conn.execute("SELECT DISTINCT run_phase FROM strategy_validation_runs").fetchall()
        }
        run_types = {
            row["run_type"]
            for row in conn.execute("SELECT DISTINCT run_type FROM strategy_validation_runs").fetchall()
        }
    assert phases == {"historical_replay"}
    assert run_types == {"dry_run"}


def test_strategy_live_replay_candidate_scout_does_not_write_validation_rows_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-candidate-scout.sqlite")
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES('scout-event', 'btc-updown-5m-scout', 'BTC', 300,
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:05:00+00:00', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES('scout-event:up', 'scout-event', 'scout-token', 'Up',
                   'btc-updown-5m-scout', 'BTC', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """
        )
        for key, minute, bid, ask in (
            ("entry", "01", 0.49, 0.51),
            ("forward", "02", 0.56, 0.58),
        ):
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, 'scout-event:up', 'scout-event', 'scout-token',
                       'btc-updown-5m-scout', 'Up', ?, ?, ?, ?, ?, ?, ?, 100, '{}')
                """,
                (
                    f"scout-{key}",
                    f"2026-06-05T20:{minute}:00+00:00",
                    f"2026-06-05T20:{minute}:00+00:00",
                    f"2026-06-05T20:{minute}:00+00:00",
                    (bid + ask) / 2.0,
                    bid,
                    ask,
                    ask - bid,
                ),
            )
        assert count_rows(conn, "strategy_validation_runs") == 0

    payload = scout_strategy_live_replay_candidates(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-candidate-scout",
            strategy_ids=("profile_splus_hedger_follow_hold_60s_v8",),
            forward_mark_horizon_seconds=60.0,
            max_scenarios=1,
            scenario_selector="latest",
        )
    )

    assert payload["schema_version"] == "crypto_options_strategy_live_replay_candidate_scout_v1"
    assert payload["mode"] == "live_replay_candidate_scout"
    assert payload["scenario_count"] == 1
    assert payload["distinct_event_count"] == 1
    assert payload["distinct_event_keys"] == ["scout-event"]
    assert payload["orders_allowed"] is False
    assert payload["live_trading_authorized"] is False
    assert payload["manual_orders_avoided"] is True
    with connect(db_path) as conn:
        assert count_rows(conn, "strategy_validation_runs") == 0
        assert count_rows(conn, "strategy_candidates") == 0


def test_strategy_live_replay_worker_persists_shadow_lifecycle_rows_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay.sqlite")

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-strategy-live-replay",
            strategy_ids=("profile_hedge_scalping_v1", "hedger_ratio_replication_v4"),
            max_trades_per_strategy=1,
        )
    )

    assert payload["schema_version"] == "crypto_options_strategy_live_replay_run_v1"
    assert payload["runtime_mode"] == "shadow"
    assert payload["orders_allowed"] is False
    assert payload["live_trading_authorized"] is False
    assert payload["manual_orders_avoided"] is True
    assert payload["result_count"] == 2
    assert payload["db_counts"]["strategy_validation_runs"] == 2
    with connect(Path(db_path)) as conn:
        assert count_rows(conn, "strategy_validation_runs") == 2
        assert count_rows(conn, "validation_budget_ledger") == 0
        assert count_rows(conn, "strategy_candidates") == 2
        assert count_rows(conn, "execution_intents") == 2
        assert count_rows(conn, "orders") == 2
        assert count_rows(conn, "fills") == 2
        assert count_rows(conn, "positions") == 2
        phases = {
            row["run_phase"]
            for row in conn.execute("SELECT DISTINCT run_phase FROM strategy_validation_runs").fetchall()
        }
        run_types = {
            row["run_type"]
            for row in conn.execute("SELECT DISTINCT run_type FROM strategy_validation_runs").fetchall()
        }
        evidence_rows = [
            json.loads(row["evidence_json"])
            for row in conn.execute("SELECT evidence_json FROM strategy_validation_runs").fetchall()
        ]
    assert phases == {"live_replay"}
    assert run_types == {"shadow"}
    for evidence in evidence_rows:
        economics = evidence["result"]["economics"]
        assert economics["source"] == "runtime_shadow_entry_fill_needs_forward_mark"
        assert economics["sample_count"] == 0
        assert "simulated_pnl_usd" in economics
        assert economics["promotion_economics_ready"] is False
        assert "forward_price_path_required_for_promotion_pnl" in economics["blockers"]
        assert "liquidation_pnl_usd" in economics
        assert evidence["result"]["shadow_economics"] == economics


def test_strategy_live_replay_worker_uses_forward_price_path_for_shadow_economics_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-forward.sqlite")

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-event', 'btc-updown-5m-pytest', 'BTC', 300,
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:05:00+00:00',
                '{}', '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-event:up', 'pytest-event', 'pytest-token', 'Up',
                'btc-updown-5m-pytest', 'BTC', '{}',
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO polymarket_price_ticks(
                price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
            )
            VALUES(
                'pytest-entry-tick', 'pytest-event:up', 'pytest-event', 'pytest-token',
                'btc-updown-5m-pytest', 'Up', '2026-06-05T20:00:00+00:00',
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00',
                    0.505, 0.50, 0.51, 0.01, 100, '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO polymarket_price_ticks(
                price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
            )
            VALUES(
                'pytest-forward-tick', 'pytest-event:up', 'pytest-event', 'pytest-token',
                'btc-updown-5m-pytest', 'Up', '2026-06-05T20:01:00+00:00',
                '2026-06-05T20:01:00+00:00', '2026-06-05T20:01:00+00:00',
                0.66, 0.65, 0.67, 0.02, 80, '{}'
            )
            """
        )
        _insert_profile_context(conn, event_key="pytest-event", event_slug="btc-updown-5m-pytest", up_ratio=0.7)

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-strategy-live-replay-forward",
            strategy_ids=("profile_hedge_scalping_v1",),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
        )
    )

    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark"
    assert payload["scenario"]["forward_mark_price"] == 0.65
    assert payload["passed_count"] == 1
    with connect(Path(db_path)) as conn:
        evidence = json.loads(
            conn.execute("SELECT evidence_json FROM strategy_validation_runs").fetchone()["evidence_json"]
        )
    economics = evidence["result"]["economics"]
    assert economics["source"] == "runtime_shadow_forward_mark"
    assert economics["sample_count"] == 1
    assert economics["promotion_economics_ready"] is True
    assert economics["simulated_pnl_usd"] > 0
    assert economics["win_rate"] == 1.0


def test_profile_follow_v9_blocks_forward_win_while_v10_allows_bounded_spread_drag_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-profile-v9-liquidation-gate.sqlite")

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-event', 'btc-updown-5m-profile-v9', 'BTC', 300,
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:05:00+00:00',
                '{}', '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-event:up', 'pytest-event', 'pytest-token', 'Up',
                'btc-updown-5m-profile-v9', 'BTC', '{}',
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        for tick_key, timestamp, mid, bid, ask in (
            ("pytest-entry-tick", "2026-06-05T20:00:00+00:00", 0.505, 0.50, 0.51),
            ("pytest-forward-tick", "2026-06-05T20:01:00+00:00", 0.66, 0.65, 0.67),
        ):
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(
                    ?, 'pytest-event:up', 'pytest-event', 'pytest-token',
                    'btc-updown-5m-profile-v9', 'Up', ?, ?, ?, ?, ?, ?, ?, 100, '{}'
                )
                """,
                (tick_key, timestamp, timestamp, timestamp, mid, bid, ask, ask - bid),
            )
        _insert_profile_context(
            conn,
            event_key="pytest-event",
            event_slug="btc-updown-5m-profile-v9",
            up_ratio=0.92,
            shares_up_ratio=0.92,
            count_up_ratio=0.92,
        )
        _insert_distributed_splus_hedger_components(conn, event_key="pytest-event", up_cost=184.0, down_cost=16.0)
        _insert_option_path_stats(
            conn,
            event_key="pytest-event",
            event_slug="btc-updown-5m-profile-v9",
            avg_rolling_60s_range=0.02,
            pair_sum_range=0.04,
            snapshot_count=12,
        )

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-profile-v9-liquidation-gate",
            strategy_ids=(
                "profile_splus_hedger_follow_hold_60s_v9",
                "profile_splus_hedger_follow_hold_60s_v10",
            ),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
        )
    )

    assert payload["passed_count"] == 2
    with connect(Path(db_path)) as conn:
        evidence_by_strategy = {
            json.loads(row["evidence_json"])["result"]["strategy_id"]: json.loads(row["evidence_json"])
            for row in conn.execute("SELECT evidence_json FROM strategy_validation_runs").fetchall()
        }
    v9_economics = evidence_by_strategy["profile_splus_hedger_follow_hold_60s_v9"]["result"]["economics"]
    v9_decision = evidence_by_strategy["profile_splus_hedger_follow_hold_60s_v9"]["result"]["attribution"][
        "strategy_decision"
    ]
    v10_economics = evidence_by_strategy["profile_splus_hedger_follow_hold_60s_v10"]["result"]["economics"]
    v10_decision = evidence_by_strategy["profile_splus_hedger_follow_hold_60s_v10"]["result"]["attribution"][
        "strategy_decision"
    ]
    assert v9_economics["source"] == "runtime_shadow_forward_mark"
    assert v9_economics["simulated_pnl_usd"] > 0
    assert v9_economics["liquidation_pnl_usd"] < 0
    assert v9_economics["promotion_economics_ready"] is False
    assert v9_economics["blockers"] == ["liquidation_pnl_below_shadow_gate"]
    assert v9_decision["shadow_economics_require_liquidation_non_negative"] is True
    assert v10_economics["source"] == "runtime_shadow_forward_mark"
    assert v10_economics["simulated_pnl_usd"] > 0
    assert v10_economics["liquidation_pnl_usd"] == pytest.approx(-0.014)
    assert v10_economics["spread_drag_usd"] == pytest.approx(0.014)
    assert v10_economics["promotion_economics_ready"] is True
    assert v10_economics["blockers"] == []
    assert v10_decision["shadow_economics_min_liquidation_pnl_usd"] == -0.02


def test_strategy_live_replay_worker_enriches_scenarios_with_abc_context_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-abc-context.sqlite")

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-event', 'btc-updown-5m-pytest', 'BTC', 300,
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:05:00+00:00',
                '{}', '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-event:up', 'pytest-event', 'pytest-token', 'Up',
                'btc-updown-5m-pytest', 'BTC', '{}',
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        for key, timestamp, bid, ask, mid in (
            ("entry", "2026-06-05T20:00:00+00:00", 0.49, 0.51, 0.50),
            ("forward", "2026-06-05T20:01:00+00:00", 0.61, 0.63, 0.62),
        ):
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, 'pytest-event:up', 'pytest-event', 'pytest-token',
                       'btc-updown-5m-pytest', 'Up', ?, ?, ?, ?, ?, ?, 0.02, 100, '{}')
                """,
                (f"pytest-{key}-tick", timestamp, timestamp, timestamp, mid, bid, ask),
            )
        conn.execute(
            """
            INSERT INTO profile_distribution_snapshots(
                distribution_snapshot_key, event_key, event_slug, symbol, phase, computed_at_utc,
                event_start_time_utc, event_end_time_utc, source_mode, canonical_method,
                profile_count, component_count, up_weight, down_weight, up_share_weight,
                down_share_weight, up_cost_weight, down_cost_weight, up_count_weight,
                down_count_weight, distribution_json, source_json, blockers_json, inserted_at_utc
            )
            VALUES(
                'pytest-profile-snapshot', 'pytest-event', 'btc-updown-5m-pytest', 'BTC',
                'live', '2026-06-05T20:00:30+00:00',
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:05:00+00:00',
                'raw_activity', 'cost_weighted', 2, 2, 20, 80, 40, 100,
                20, 80, 1, 1,
                ?, '{"raw_activity_rows": 120, "position_rows": 0, "event_order_rows": 0}',
                '[]', '2026-06-05T20:00:30+00:00'
            )
            """,
            (
                json.dumps(
                    {
                        "variants": {
                            "cost_weighted": {"up": 0.2, "down": 0.8},
                        },
                        "top_profiles_distribution": {"up": 0.2, "down": 0.8},
                        "pressure_delta": -0.6,
                        "source_age_seconds": 3.0,
                        "target_refresh_seconds": 30.0,
                        "reconstructed_profile_prices": {"up": 0.5, "down": 0.8, "pair_sum": 1.3},
                    }
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO external_technical_observer_snapshots(
                observer_snapshot_key, provider, symbol, interval, source_url,
                request_started_at_utc, observed_at_utc, completed_at_utc,
                latency_ms, summary_label, summary_score, buy_count, sell_count,
                neutral_count, error_count, component_count, components_json,
                source_json, inserted_at_utc
            )
            VALUES(
                'pytest-technical', 'ifcm', 'BTC', '1m', 'https://example.test',
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:01+00:00',
                '2026-06-05T20:00:02+00:00', 100, 'Buy', 0.6, 8, 2, 1, 0,
                11, '{}', '{}', '2026-06-05T20:00:02+00:00'
            )
            """
        )

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-strategy-live-replay-abc-context",
            strategy_ids=("profile_hedge_scalping_v1",),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
        )
    )

    assert payload["scenario"]["target_up_ratio"] == 0.2
    assert payload["scenario"]["profile_distribution_ready"] is True
    assert payload["scenario"]["crypto_observer_ready"] is True
    with connect(Path(db_path)) as conn:
        evidence = json.loads(
            conn.execute("SELECT evidence_json FROM strategy_validation_runs").fetchone()["evidence_json"]
        )
    context = evidence["result"]["attribution"]["signal_context"]
    assert context["target_up_ratio"] == 0.2
    assert context["profile_distribution"]["profile_distribution_down_ratio"] == 0.8
    assert context["profile_distribution"]["raw_activity_rows"] == 120
    assert context["crypto_observer"]["intervals"] == ["1m"]
    assert context["option_path_ready"] is False


def test_strategy_live_replay_worker_enriches_profile_context_with_consensus_diagnostics_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-profile-diagnostics.sqlite")

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-event', 'btc-updown-5m-pytest', 'BTC', 300,
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:05:00+00:00',
                '{}', '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-event:up', 'pytest-event', 'pytest-token', 'Up',
                'btc-updown-5m-pytest', 'BTC', '{}',
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        for key, timestamp, bid, ask, mid in (
            ("entry", "2026-06-05T20:00:00+00:00", 0.49, 0.51, 0.50),
            ("forward", "2026-06-05T20:01:00+00:00", 0.61, 0.63, 0.62),
        ):
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, 'pytest-event:up', 'pytest-event', 'pytest-token',
                       'btc-updown-5m-pytest', 'Up', ?, ?, ?, ?, ?, ?, 0.02, 100, '{}')
                """,
                (f"pytest-{key}-tick", timestamp, timestamp, timestamp, mid, bid, ask),
            )
        _insert_profile_context(
            conn,
            event_key="pytest-event",
            event_slug="btc-updown-5m-pytest",
            up_ratio=0.7,
            shares_up_ratio=0.56,
            count_up_ratio=0.6,
            coverage_warnings=["profile_distribution_source_stale"],
            source_age_seconds=41.0,
        )
        _insert_single_dominant_profile_components(conn, event_key="pytest-event", dominant_up_cost=70.0, supporting_down_cost=30.0)

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-strategy-live-replay-profile-diagnostics",
            strategy_ids=("profile_hedge_scalping_v1",),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
        )
    )

    with connect(Path(db_path)) as conn:
        evidence = json.loads(conn.execute("SELECT evidence_json FROM strategy_validation_runs").fetchone()["evidence_json"])
    context = evidence["result"]["attribution"]["signal_context"]["profile_distribution"]
    assert context["coverage_warnings"] == ["profile_distribution_source_stale"]
    assert context["cost_weighted_up_ratio"] == 0.7
    assert context["shares_weighted_up_ratio"] == 0.56
    assert context["profile_count_weighted_up_ratio"] == 0.6
    assert context["cost_vs_shares_up_gap_abs"] == 0.14
    assert context["cost_vs_profile_count_up_gap_abs"] == 0.1
    assert context["top_profile_cost_share"] == 0.7
    assert context["latest_source_at_utc"] == "2026-06-05T20:00:27+00:00"


def test_path_aware_profile_strategy_requires_tradable_option_path_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-path-aware.sqlite")

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-event', 'btc-updown-5m-pytest', 'BTC', 300,
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:05:00+00:00',
                '{}', '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-event:up', 'pytest-event', 'pytest-token', 'Up',
                'btc-updown-5m-pytest', 'BTC', '{}',
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        for key, timestamp, bid, ask, mid in (
            ("entry", "2026-06-05T20:00:00+00:00", 0.49, 0.51, 0.50),
            ("forward", "2026-06-05T20:01:00+00:00", 0.65, 0.67, 0.66),
        ):
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, 'pytest-event:up', 'pytest-event', 'pytest-token',
                       'btc-updown-5m-pytest', 'Up', ?, ?, ?, ?, ?, ?, 0.02, 100, '{}')
                """,
                (f"pytest-{key}-tick", timestamp, timestamp, timestamp, mid, bid, ask),
            )
        _insert_profile_context(conn, event_key="pytest-event", event_slug="btc-updown-5m-pytest", up_ratio=0.78)
        _insert_splus_hedger_components(conn, event_key="pytest-event", up_cost=78.0, down_cost=22.0)
        _insert_option_path_stats(
            conn,
            event_key="pytest-event",
            event_slug="btc-updown-5m-pytest",
            avg_rolling_60s_range=0.01,
        )

    quiet_payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-path-aware-quiet",
            strategy_ids=("profile_splus_hedger_follow_path_active_hold_60s_v1",),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
        )
    )

    assert quiet_payload["passed_count"] == 0
    assert "option_path_avg_rolling_60s_range_low" in quiet_payload["blockers"][
        "profile_splus_hedger_follow_path_active_hold_60s_v1"
    ]

    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE polymarket_event_path_stats
               SET avg_rolling_60s_range = 0.05,
                   updated_at_utc = '2026-06-05T20:04:55+00:00'
             WHERE event_path_stats_key = 'event_path_stats:pytest-event'
            """
        )

    active_payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-path-aware-active",
            strategy_ids=("profile_splus_hedger_follow_path_active_hold_60s_v1",),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
        )
    )

    assert active_payload["passed_count"] == 1
    with connect(Path(db_path)) as conn:
        evidence = json.loads(
            conn.execute(
                """
                SELECT evidence_json
                  FROM strategy_validation_runs
                 WHERE run_id = 'pytest-path-aware-active'
                """
            ).fetchone()["evidence_json"]
        )
    decision = evidence["result"]["attribution"]["strategy_decision"]
    assert decision["option_path_ready"] is True
    assert decision["avg_rolling_60s_range"] == 0.05

    strict_payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-splus-v4-clean",
            strategy_ids=("profile_splus_hedger_follow_hold_60s_v4",),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
        )
    )

    assert strict_payload["passed_count"] == 1

    strict_v5_payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-splus-v5-clean",
            strategy_ids=("profile_splus_hedger_follow_hold_60s_v5",),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
        )
    )

    assert strict_v5_payload["passed_count"] == 1

    strict_v6_payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-splus-v6-clean",
            strategy_ids=("profile_splus_hedger_follow_hold_60s_v6",),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
        )
    )

    assert strict_v6_payload["passed_count"] == 1

    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE polymarket_price_ticks
               SET best_bid = 0.71,
                   best_ask = 0.72,
                   mid_price = 0.715
             WHERE price_tick_key = 'pytest-entry-tick'
            """
        )

    high_entry_payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-splus-v4-high-entry",
            strategy_ids=("profile_splus_hedger_follow_hold_60s_v4",),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
        )
    )

    assert high_entry_payload["passed_count"] == 0
    assert "option_entry_price_above_band" in high_entry_payload["blockers"][
        "profile_splus_hedger_follow_hold_60s_v4"
    ]

    high_entry_v5_payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-splus-v5-high-entry",
            strategy_ids=("profile_splus_hedger_follow_hold_60s_v5",),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
        )
    )

    assert high_entry_v5_payload["passed_count"] == 0
    assert "option_entry_price_above_band" in high_entry_v5_payload["blockers"][
        "profile_splus_hedger_follow_hold_60s_v5"
    ]

    high_entry_v6_payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-splus-v6-high-entry",
            strategy_ids=("profile_splus_hedger_follow_hold_60s_v6",),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
        )
    )

    assert high_entry_v6_payload["passed_count"] == 0
    assert "option_entry_price_above_band" in high_entry_v6_payload["blockers"][
        "profile_splus_hedger_follow_hold_60s_v6"
    ]


def test_coherent_profile_strategy_blocks_stale_coverage_warning_then_passes_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-coherent-profile.sqlite")

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-event', 'btc-updown-5m-pytest', 'BTC', 300,
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:05:00+00:00',
                '{}', '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-event:up', 'pytest-event', 'pytest-token', 'Up',
                'btc-updown-5m-pytest', 'BTC', '{}',
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        for key, timestamp, bid, ask, mid in (
            ("entry", "2026-06-05T20:00:00+00:00", 0.49, 0.51, 0.50),
            ("forward", "2026-06-05T20:01:00+00:00", 0.64, 0.66, 0.65),
        ):
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, 'pytest-event:up', 'pytest-event', 'pytest-token',
                       'btc-updown-5m-pytest', 'Up', ?, ?, ?, ?, ?, ?, 0.02, 100, '{}')
                """,
                (f"pytest-{key}-tick", timestamp, timestamp, timestamp, mid, bid, ask),
            )
        _insert_profile_context(conn, event_key="pytest-event", event_slug="btc-updown-5m-pytest", up_ratio=0.72)
        conn.execute("DELETE FROM profile_distribution_components WHERE distribution_snapshot_key = 'pytest-event-profile-snapshot'")
        for suffix, outcome, cost, shares in (
            ("up-a", "Up", 36.0, 60.0),
            ("up-b", "Up", 36.0, 60.0),
            ("down-a", "Down", 14.0, 40.0),
            ("down-b", "Down", 14.0, 40.0),
        ):
            conn.execute(
                """
                INSERT INTO profile_distribution_components(
                    distribution_component_key, distribution_snapshot_key, profile_key,
                    handle, grade, trading_style, source_mode, outcome, net_shares,
                    net_notional_usd, cost_basis_usd, grade_weight, style_weight,
                    final_weight, contribution_json, inserted_at_utc
                )
                VALUES(?, 'pytest-event-profile-snapshot', ?, ?, 'S+', 'hedger',
                       'raw_activity', ?, ?, ?, ?, 1, 1, 1, '{}',
                       '2026-06-05T20:00:30+00:00')
                """,
                (
                    f"pytest-event-profile-snapshot:{suffix}",
                    f"pytest-{suffix}",
                    f"@pytest-{suffix}",
                    outcome,
                    shares,
                    cost,
                    cost,
                ),
            )
        conn.execute(
            """
            UPDATE profile_distribution_snapshots
               SET profile_count = 4,
                   component_count = 4,
                   distribution_json = ?
             WHERE distribution_snapshot_key = 'pytest-event-profile-snapshot'
            """,
            (
                json.dumps(
                    {
                        "variants": {
                            "cost_weighted": {"up": 0.72, "down": 0.28},
                            "shares_weighted": {"up": 0.70, "down": 0.30},
                            "profile_count_weighted": {"up": 0.68, "down": 0.32},
                        },
                        "top_profiles_distribution": {"up": 0.72, "down": 0.28},
                        "pressure_delta": 0.44,
                        "source_age_seconds": 12.0,
                        "target_refresh_seconds": 30.0,
                        "latest_source_at_utc": "2026-06-05T20:00:18+00:00",
                        "coverage_warnings": ["profile_distribution_source_stale"],
                        "reconstructed_profile_prices": {"up": 0.54, "down": 0.46, "pair_sum": 1.0},
                    }
                ),
            ),
        )

    blocked_payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-coherent-profile-blocked",
            strategy_ids=("profile_splus_hedger_follow_coherent_hold_60s_v1",),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
        )
    )

    assert blocked_payload["passed_count"] == 0
    assert "profile_distribution_coverage_warning:profile_distribution_source_stale" in blocked_payload["blockers"][
        "profile_splus_hedger_follow_coherent_hold_60s_v1"
    ]

    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE profile_distribution_snapshots
               SET profile_count = 4,
                   component_count = 4,
                   distribution_json = ?
             WHERE distribution_snapshot_key = 'pytest-event-profile-snapshot'
            """,
            (
                json.dumps(
                    {
                        "variants": {
                            "cost_weighted": {"up": 0.72, "down": 0.28},
                            "shares_weighted": {"up": 0.70, "down": 0.30},
                            "profile_count_weighted": {"up": 0.68, "down": 0.32},
                        },
                        "top_profiles_distribution": {"up": 0.72, "down": 0.28},
                        "pressure_delta": 0.44,
                        "source_age_seconds": 12.0,
                        "target_refresh_seconds": 30.0,
                        "latest_source_at_utc": "2026-06-05T20:00:18+00:00",
                        "coverage_warnings": [],
                        "reconstructed_profile_prices": {"up": 0.54, "down": 0.46, "pair_sum": 1.0},
                    }
                ),
            ),
        )
        conn.execute("DELETE FROM profile_distribution_components WHERE distribution_snapshot_key = 'pytest-event-profile-snapshot'")
        for suffix, outcome, cost, shares in (
            ("up-a", "Up", 36.0, 60.0),
            ("up-b", "Up", 36.0, 60.0),
            ("down-a", "Down", 14.0, 40.0),
            ("down-b", "Down", 14.0, 40.0),
        ):
            conn.execute(
                """
                INSERT INTO profile_distribution_components(
                    distribution_component_key, distribution_snapshot_key, profile_key,
                    handle, grade, trading_style, source_mode, outcome, net_shares,
                    net_notional_usd, cost_basis_usd, grade_weight, style_weight,
                    final_weight, contribution_json, inserted_at_utc
                )
                VALUES(?, 'pytest-event-profile-snapshot', ?, ?, 'S+', 'hedger',
                       'raw_activity', ?, ?, ?, ?, 1, 1, 1, '{}',
                       '2026-06-05T20:00:22+00:00')
                """,
                (
                    f"pytest-event-profile-snapshot:{suffix}",
                    f"pytest-{suffix}",
                    f"@pytest-{suffix}",
                    outcome,
                    shares,
                    cost,
                    cost,
                ),
            )

    passed_payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-coherent-profile-passed",
            strategy_ids=("profile_splus_hedger_follow_coherent_hold_60s_v1",),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
        )
    )

    assert passed_payload["passed_count"] == 1
    with connect(Path(db_path)) as conn:
        evidence = json.loads(
            conn.execute(
                """
                SELECT evidence_json
                  FROM strategy_validation_runs
                 WHERE run_id = 'pytest-coherent-profile-passed'
                """
            ).fetchone()["evidence_json"]
        )
    decision = evidence["result"]["attribution"]["strategy_decision"]
    assert decision["profile_source_age_seconds"] == 12.0
    assert decision["cost_vs_shares_up_gap_abs"] == 0.02
    assert decision["top_profile_cost_share"] == 0.36


def test_master_hedge_grid_floor_profile_follow_runs_in_hedge_only_mode_until_surplus_exists_pytest(
    tmp_path: Path,
) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-hedge-floor.sqlite")

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-event', 'btc-updown-5m-hedge-floor', 'BTC', 300,
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:05:00+00:00',
                '{}', '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-event:up', 'pytest-event', 'pytest-token', 'Up',
                'btc-updown-5m-hedge-floor', 'BTC', '{}',
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        for key, timestamp, bid, ask, mid in (
            ("entry", "2026-06-05T20:00:00+00:00", 0.53, 0.55, 0.54),
            ("forward", "2026-06-05T20:01:00+00:00", 0.61, 0.63, 0.62),
        ):
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, 'pytest-event:up', 'pytest-event', 'pytest-token',
                       'btc-updown-5m-hedge-floor', 'Up', ?, ?, ?, ?, ?, ?, 0.02, 100, '{}')
                """,
                (f"pytest-{key}-tick", timestamp, timestamp, timestamp, mid, bid, ask),
            )
        _insert_pair_snapshot(
            conn,
            event_key="pytest-event",
            event_slug="btc-updown-5m-hedge-floor",
            timestamp="2026-06-05T20:00:00+00:00",
            up_bid=0.53,
            up_ask=0.55,
            down_bid=0.35,
            down_ask=0.37,
            key_suffix="entry",
        )
        _insert_pair_snapshot(
            conn,
            event_key="pytest-event",
            event_slug="btc-updown-5m-hedge-floor",
            timestamp="2026-06-05T20:00:20+00:00",
            up_bid=0.66,
            up_ask=0.68,
            down_bid=0.27,
            down_ask=0.30,
            key_suffix="dip",
        )
        _insert_pair_snapshot(
            conn,
            event_key="pytest-event",
            event_slug="btc-updown-5m-hedge-floor",
            timestamp="2026-06-05T20:00:40+00:00",
            up_bid=0.58,
            up_ask=0.60,
            down_bid=0.36,
            down_ask=0.38,
            key_suffix="rebound",
        )
        _insert_pair_snapshot(
            conn,
            event_key="pytest-event",
            event_slug="btc-updown-5m-hedge-floor",
            timestamp="2026-06-05T20:01:00+00:00",
            up_bid=0.61,
            up_ask=0.63,
            down_bid=0.38,
            down_ask=0.40,
            key_suffix="forward",
        )
        _insert_profile_context(
            conn,
            event_key="pytest-event",
            event_slug="btc-updown-5m-hedge-floor",
            up_ratio=0.68,
            up_reconstructed_price=0.55,
            down_reconstructed_price=0.37,
        )
        _insert_splus_hedger_components(conn, event_key="pytest-event", up_cost=68.0, down_cost=32.0)
        _insert_option_path_stats(
            conn,
            event_key="pytest-event",
            event_slug="btc-updown-5m-hedge-floor",
            avg_rolling_60s_range=0.06,
            max_rolling_60s_range=0.12,
            snapshot_count=16,
            level_crossing_count=8,
            near_50c_sample_count=8,
            rebound_direction_flip_count=5,
            strong_rebound_touch_count=4,
            pair_sum_range=0.03,
        )

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-master-hedge-grid-floor",
            strategy_ids=(
                "master_hedge_grid_floor_profile_follow_v1",
                "master_hedge_grid_floor_profile_follow_v2",
                "master_hedge_grid_floor_profile_follow_v3",
                "master_hedge_grid_floor_seed_builder_v1",
                "master_hedge_grid_floor_seed_builder_neutral_v1",
                "master_hedge_grid_floor_paired_seed_builder_v1",
                "master_hedge_grid_floor_paired_seed_builder_v2",
                "master_hedge_grid_floor_paired_seed_builder_v3",
                "master_hedge_grid_floor_paired_seed_builder_v4",
                "master_hedge_grid_floor_paired_seed_builder_v5",
                "master_hedge_grid_floor_paired_seed_builder_v6",
                "master_hedge_grid_floor_paired_seed_builder_v12",
            ),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
        )
    )

    assert payload["passed_count"] == 11
    assert payload["blocked_count"] == 1
    assert "hedge_floor_no_protected_tail_budget" not in payload["blockers"]["master_hedge_grid_floor_profile_follow_v1"]
    assert "hedge_floor_no_protected_tail_budget" not in payload["blockers"]["master_hedge_grid_floor_profile_follow_v2"]
    assert payload["blockers"]["master_hedge_grid_floor_profile_follow_v3"] == ["hedge_floor_current_floor_too_low"]
    with connect(Path(db_path)) as conn:
        evidence_rows = [
            json.loads(row["evidence_json"])
            for row in conn.execute(
                """
                SELECT evidence_json
                  FROM strategy_validation_runs
                 WHERE run_id = 'pytest-master-hedge-grid-floor'
                 ORDER BY strategy_id
                """
            ).fetchall()
        ]
    evidence_by_strategy = {
        row["result"]["strategy_id"]: row
        for row in evidence_rows
    }
    decision = evidence_by_strategy["master_hedge_grid_floor_profile_follow_v1"]["result"]["attribution"]["strategy_decision"]
    state = decision["hedge_floor_state"]
    assert decision["hedge_floor_mode"] == "seed_both_then_preserve_floor"
    assert decision["tail_orders_allowed"] is False
    assert decision["inversion_intensity"] > 0.8
    assert decision["grid_viability"] > 0.7
    assert state["down_shares"] == round(1.0 / 0.37, 8)
    assert state["surplus_above_floor"] == 0.0
    v2_decision = evidence_by_strategy["master_hedge_grid_floor_profile_follow_v2"]["result"]["attribution"]["strategy_decision"]
    assert v2_decision["profile_ratio_source"] == "by_grade_style:S+ / hedger"
    assert v2_decision["profile_target_side"] == "Up"
    assert v2_decision["tail_orders_allowed"] is False
    assert v2_decision["grid_viability"] > 0.7
    v3_decision = evidence_by_strategy["master_hedge_grid_floor_profile_follow_v3"]["result"]["attribution"]["strategy_decision"]
    assert v3_decision["hedge_floor_mode"] == "protected_floor_follow"
    assert v3_decision["hedge_floor_state"]["guaranteed_floor"] < 0.03
    seed_decision = evidence_by_strategy["master_hedge_grid_floor_seed_builder_v1"]["result"]["attribution"]["strategy_decision"]
    assert seed_decision["hedge_floor_mode"] == "seed_floor_builder"
    assert seed_decision["hedge_floor_state"]["guaranteed_floor"] < 0.0
    assert seed_decision["hedge_floor_state"]["guaranteed_floor"] >= -0.20
    assert seed_decision["grid_viability"] > 0.7
    neutral_seed_decision = evidence_by_strategy["master_hedge_grid_floor_seed_builder_neutral_v1"]["result"]["attribution"]["strategy_decision"]
    assert neutral_seed_decision["shadow_decision_mode"] == "seed_floor_builder_option_churn_no_lookahead"
    assert neutral_seed_decision["hedge_floor_mode"] == "seed_floor_builder"
    assert neutral_seed_decision["hedge_floor_state"]["guaranteed_floor"] < 0.0
    assert neutral_seed_decision["hedge_floor_state"]["guaranteed_floor"] >= -0.20
    paired_seed_decision = evidence_by_strategy["master_hedge_grid_floor_paired_seed_builder_v1"]["result"]["attribution"]["strategy_decision"]
    paired_seed_entry = paired_seed_decision["paired_seed_entry"]
    paired_seed_state = paired_seed_decision["hedge_floor_state"]
    assert paired_seed_decision["shadow_decision_mode"] == "paired_seed_equal_share_floor_no_lookahead"
    assert paired_seed_decision["hedge_floor_mode"] == "paired_seed_equal_share_floor"
    assert paired_seed_decision["hedge_floor_seed_allocation_mode"] == "equal_shares"
    assert paired_seed_decision["floor_preserving_order_gate"] is True
    assert paired_seed_entry["pair_sum"] == pytest.approx(0.92)
    assert paired_seed_entry["equal_shares"] == pytest.approx(2.0 / 0.92)
    assert paired_seed_state["up_shares"] == pytest.approx(paired_seed_state["down_shares"])
    assert paired_seed_state["guaranteed_floor"] == pytest.approx((2.0 / 0.92) - 2.0)
    assert paired_seed_decision["protected_floor_improvement"]["preserves_floor"] is True
    paired_seed_context = evidence_by_strategy["master_hedge_grid_floor_paired_seed_builder_v1"]["result"]["attribution"]["signal_context"]
    assert paired_seed_context["paired_market_snapshot_ready"] is True
    assert paired_seed_context["paired_forward_snapshot_ready"] is True
    assert paired_seed_context["paired_entry_up_ask"] == pytest.approx(0.55)
    assert paired_seed_context["paired_entry_down_ask"] == pytest.approx(0.37)
    paired_seed_economics = evidence_by_strategy["master_hedge_grid_floor_paired_seed_builder_v1"]["result"]["economics"]
    assert paired_seed_economics["source"] == "runtime_shadow_paired_seed_floor_with_forward_pair_mark"
    assert paired_seed_economics["sample_count"] == 1
    assert paired_seed_economics["promotion_economics_ready"] is True
    assert paired_seed_economics["paired_entry_pair_sum"] == pytest.approx(0.92)
    assert paired_seed_economics["paired_equal_shares"] == pytest.approx(2.0 / 0.92)
    assert paired_seed_economics["guaranteed_floor_pnl_usd"] == pytest.approx((2.0 / 0.92) - 2.0)
    assert paired_seed_economics["paired_forward_mark_available"] is True
    assert paired_seed_economics["paired_forward_mark_pnl_usd"] == pytest.approx((2.0 / 0.92 * 0.99) - 2.0)
    paired_seed_v2_decision = evidence_by_strategy["master_hedge_grid_floor_paired_seed_builder_v2"]["result"]["attribution"]["strategy_decision"]
    assert paired_seed_v2_decision["shadow_decision_mode"] == "paired_seed_small_negative_floor_harvest_no_lookahead"
    assert paired_seed_v2_decision["paired_seed_entry"]["pair_sum"] == pytest.approx(0.92)
    paired_seed_v3_decision = evidence_by_strategy["master_hedge_grid_floor_paired_seed_builder_v3"]["result"]["attribution"]["strategy_decision"]
    assert paired_seed_v3_decision["shadow_decision_mode"] == "paired_seed_inversion_scalp_path_no_lookahead_economics"
    assert paired_seed_v3_decision["paired_seed_scalp_simulation_required"] is True
    paired_seed_v3_context = evidence_by_strategy["master_hedge_grid_floor_paired_seed_builder_v3"]["result"]["attribution"]["signal_context"]
    assert paired_seed_v3_context["paired_scalp_path_ready"] is True
    assert paired_seed_v3_context["paired_path_snapshot_count"] == 3
    paired_seed_v3_economics = evidence_by_strategy["master_hedge_grid_floor_paired_seed_builder_v3"]["result"]["economics"]
    assert paired_seed_v3_economics["source"] == "runtime_shadow_paired_seed_scalp_path"
    assert paired_seed_v3_economics["completed_scalp_cycle_count"] == 1
    assert paired_seed_v3_economics["completed_scalp_profit_usd"] > 0.0
    assert paired_seed_v3_economics["guaranteed_floor_pnl_usd"] > paired_seed_v3_economics["initial_guaranteed_floor_pnl_usd"]
    assert paired_seed_v3_economics["promotion_economics_ready"] is True
    paired_seed_v4_decision = evidence_by_strategy["master_hedge_grid_floor_paired_seed_builder_v4"]["result"]["attribution"]["strategy_decision"]
    assert paired_seed_v4_decision["shadow_decision_mode"] == "paired_seed_deep_dip_quick_scalp_path_no_lookahead_economics"
    paired_seed_v4_economics = evidence_by_strategy["master_hedge_grid_floor_paired_seed_builder_v4"]["result"]["economics"]
    assert paired_seed_v4_economics["source"] == "runtime_shadow_paired_seed_scalp_path"
    assert paired_seed_v4_economics["completed_scalp_cycle_count"] == 1
    assert paired_seed_v4_economics["completed_scalp_profit_usd"] > 0.0
    assert paired_seed_v4_economics["promotion_economics_ready"] is True
    paired_seed_v5_decision = evidence_by_strategy["master_hedge_grid_floor_paired_seed_builder_v5"]["result"]["attribution"]["strategy_decision"]
    assert paired_seed_v5_decision["shadow_decision_mode"] == "paired_seed_closed_cycle_scalp_path_no_lookahead_economics"
    assert paired_seed_v5_decision["paired_seed_scalp_min_path_snapshots"] == 2
    assert paired_seed_v5_decision["paired_seed_scalp_entry_window_fraction"] == pytest.approx(0.67)
    paired_seed_v5_economics = evidence_by_strategy["master_hedge_grid_floor_paired_seed_builder_v5"]["result"]["economics"]
    assert paired_seed_v5_economics["source"] == "runtime_shadow_paired_seed_scalp_path"
    assert paired_seed_v5_economics["paired_seed_scalp_min_path_snapshots"] == 2
    assert paired_seed_v5_economics["paired_seed_scalp_buy_cutoff_index"] == 2
    assert paired_seed_v5_economics["completed_scalp_cycle_count"] == 1
    assert paired_seed_v5_economics["open_scalp_position_count"] == 0
    assert paired_seed_v5_economics["promotion_economics_ready"] is True
    paired_seed_v6_decision = evidence_by_strategy["master_hedge_grid_floor_paired_seed_builder_v6"]["result"]["attribution"]["strategy_decision"]
    assert paired_seed_v6_decision["shadow_decision_mode"] == "paired_seed_closed_cycle_entry_gap_scalp_no_lookahead_economics"
    assert paired_seed_v6_decision["paired_seed_entry"]["entry_ask_gap"] == pytest.approx(0.18)
    assert paired_seed_v6_decision["paired_seed_entry"]["pair_sum"] == pytest.approx(0.92)
    paired_seed_v6_economics = evidence_by_strategy["master_hedge_grid_floor_paired_seed_builder_v6"]["result"]["economics"]
    assert paired_seed_v6_economics["source"] == "runtime_shadow_paired_seed_scalp_path"
    assert paired_seed_v6_economics["completed_scalp_cycle_count"] == 1
    assert paired_seed_v6_economics["open_scalp_position_count"] == 0
    assert paired_seed_v6_economics["promotion_economics_ready"] is True
    paired_seed_v12_decision = evidence_by_strategy["master_hedge_grid_floor_paired_seed_builder_v12"]["result"]["attribution"]["strategy_decision"]
    assert paired_seed_v12_decision["shadow_decision_mode"] == "paired_seed_closed_cycle_positive_floor_preflight_no_lookahead_economics"
    assert paired_seed_v12_decision["paired_seed_scalp_preflight_required"] is True
    assert paired_seed_v12_decision["paired_seed_scalp_preflight"]["completed_scalp_cycle_count"] == 1
    assert paired_seed_v12_decision["paired_seed_scalp_preflight"]["open_scalp_position_count"] == 0
    assert paired_seed_v12_decision["paired_seed_scalp_preflight"]["guaranteed_floor_pnl_usd"] > 0.0
    paired_seed_v12_economics = evidence_by_strategy["master_hedge_grid_floor_paired_seed_builder_v12"]["result"]["economics"]
    assert paired_seed_v12_economics["source"] == "runtime_shadow_paired_seed_scalp_path"
    assert paired_seed_v12_economics["promotion_economics_ready"] is True


def test_coherent_profile_strategy_blocks_on_method_divergence_and_stale_coverage_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-profile-coherent.sqlite")

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-event', 'btc-updown-5m-pytest', 'BTC', 300,
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:05:00+00:00',
                '{}', '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-event:up', 'pytest-event', 'pytest-token', 'Up',
                'btc-updown-5m-pytest', 'BTC', '{}',
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        for key, timestamp, bid, ask, mid in (
            ("entry", "2026-06-05T20:00:00+00:00", 0.49, 0.51, 0.50),
            ("forward", "2026-06-05T20:01:00+00:00", 0.65, 0.67, 0.66),
        ):
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, 'pytest-event:up', 'pytest-event', 'pytest-token',
                       'btc-updown-5m-pytest', 'Up', ?, ?, ?, ?, ?, ?, 0.02, 100, '{}')
                """,
                (f"pytest-{key}-tick", timestamp, timestamp, timestamp, mid, bid, ask),
            )
        _insert_profile_context(
            conn,
            event_key="pytest-event",
            event_slug="btc-updown-5m-pytest",
            up_ratio=0.7,
            shares_up_ratio=0.4,
            count_up_ratio=0.44,
            coverage_warnings=["profile_distribution_source_stale"],
            source_age_seconds=75.0,
        )
        _insert_single_dominant_profile_components(conn, event_key="pytest-event", dominant_up_cost=70.0, supporting_down_cost=30.0)
        _insert_option_path_stats(
            conn,
            event_key="pytest-event",
            event_slug="btc-updown-5m-pytest",
            avg_rolling_60s_range=0.05,
        )

    blocked_payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-profile-coherent-blocked",
            strategy_ids=("profile_splus_hedger_follow_coherent_scalp_v1",),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
        )
    )

    blockers = blocked_payload["blockers"]["profile_splus_hedger_follow_coherent_scalp_v1"]
    assert "profile_distribution_source_age_high" in blockers
    assert "profile_distribution_cost_share_divergence_high" in blockers
    assert "profile_distribution_coverage_warning:profile_distribution_source_stale" in blockers
    assert "profile_distribution_top_profile_concentration_high" in blockers

    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE profile_distribution_snapshots
               SET profile_count = 4,
                   component_count = 4,
                   distribution_json = ?
             WHERE distribution_snapshot_key = 'pytest-event-profile-snapshot'
            """,
            (
                json.dumps(
                    {
                        "variants": {
                            "cost_weighted": {"up": 0.7, "down": 0.3},
                            "shares_weighted": {"up": 0.58, "down": 0.42},
                            "profile_count_weighted": {"up": 0.59, "down": 0.41},
                        },
                        "top_profiles_distribution": {"up": 0.7, "down": 0.3},
                        "pressure_delta": 0.4,
                        "source_age_seconds": 18.0,
                        "target_refresh_seconds": 30.0,
                        "reconstructed_profile_prices": {"up": 0.5, "down": 0.5, "pair_sum": 1.0},
                        "coverage_warnings": [],
                        "latest_source_at_utc": "2026-06-05T20:01:12+00:00",
                    }
                ),
            ),
        )
        conn.execute("DELETE FROM profile_distribution_components WHERE distribution_snapshot_key = 'pytest-event-profile-snapshot'")
        for suffix, outcome, cost, shares in (
            ("up-a", "Up", 36.0, 60.0),
            ("up-b", "Up", 36.0, 60.0),
            ("down-a", "Down", 14.0, 40.0),
            ("down-b", "Down", 14.0, 40.0),
        ):
            conn.execute(
                """
                INSERT INTO profile_distribution_components(
                    distribution_component_key, distribution_snapshot_key, profile_key,
                    handle, grade, trading_style, source_mode, outcome, net_shares,
                    net_notional_usd, cost_basis_usd, grade_weight, style_weight,
                    final_weight, contribution_json, inserted_at_utc
                )
                VALUES(?, 'pytest-event-profile-snapshot', ?, ?, 'S+', 'hedger',
                       'raw_activity', ?, ?, ?, ?, 1, 1, 1, '{}',
                       '2026-06-05T20:01:12+00:00')
                """,
                (
                    f"pytest-event-profile-snapshot:{suffix}",
                    f"pytest-{suffix}",
                    f"@pytest-{suffix}",
                    outcome,
                    shares,
                    cost,
                    cost,
                ),
            )

    passed_payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-profile-coherent-passed",
            strategy_ids=("profile_splus_hedger_follow_coherent_scalp_v1",),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
        )
    )

    assert passed_payload["passed_count"] == 1
    with connect(Path(db_path)) as conn:
        evidence = json.loads(
            conn.execute(
                """
                SELECT evidence_json
                  FROM strategy_validation_runs
                 WHERE run_id = 'pytest-profile-coherent-passed'
                """
            ).fetchone()["evidence_json"]
        )
    decision = evidence["result"]["attribution"]["strategy_decision"]
    assert decision["profile_source_age_seconds"] == 18.0
    assert decision["cost_vs_shares_up_gap_abs"] == 0.12
    assert decision["top_profile_cost_share"] == 0.36


def test_crypto_direction_option_context_strategy_is_profile_free_forward_mark_lane_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-crypto-option-context.sqlite")

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-event', 'btc-updown-5m-pytest', 'BTC', 300,
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:05:00+00:00',
                '{}', '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-event:up', 'pytest-event', 'pytest-token', 'Up',
                'btc-updown-5m-pytest', 'BTC', '{}',
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        for key, timestamp, bid, ask, mid in (
            ("entry", "2026-06-05T20:00:00+00:00", 0.49, 0.51, 0.50),
            ("forward", "2026-06-05T20:01:00+00:00", 0.62, 0.64, 0.63),
        ):
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, 'pytest-event:up', 'pytest-event', 'pytest-token',
                       'btc-updown-5m-pytest', 'Up', ?, ?, ?, ?, ?, ?, 0.02, 100, '{}')
                """,
                (f"pytest-{key}-tick", timestamp, timestamp, timestamp, mid, bid, ask),
            )
        _insert_crypto_context(conn, symbol="BTC", score=0.6, label="Buy")

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-crypto-direction-option-context",
            strategy_ids=("crypto_direction_option_context_hold_60s_v1",),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
        )
    )

    assert payload["strategy_ids"] == ["crypto_direction_option_context_hold_60s_v1"]
    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark"
    assert payload["passed_count"] == 1
    assert payload["blocked_count"] == 0
    with connect(Path(db_path)) as conn:
        evidence = json.loads(
            conn.execute("SELECT evidence_json FROM strategy_validation_runs").fetchone()["evidence_json"]
        )
    result = evidence["result"]
    economics = result["economics"]
    assert "profile_sources" in result["attribution"]
    assert result["attribution"]["profile_sources"] == []
    assert set(result["attribution"]["indicator_sources"]) >= {
        "target_relative_ema_momentum_v1",
        "support_resistance_band_confluence_v1",
        "option_updown_pair_divergence_v1",
        "option_orderbook_depth_pressure_v1",
    }
    assert economics["source"] == "runtime_shadow_forward_mark"
    assert economics["sample_count"] == 1
    assert economics["promotion_economics_ready"] is True
    assert economics["simulated_pnl_usd"] == 0.11


def test_strategy_live_replay_worker_samples_multiple_forward_price_paths_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-forward-batch.sqlite")

    with connect(db_path) as conn:
        for index, symbol in enumerate(("BTC", "ETH"), start=1):
            conn.execute(
                """
                INSERT INTO events(
                    event_key, event_slug, symbol, cadence_seconds,
                    event_start_time_utc, event_end_time_utc, source_json,
                    inserted_at_utc, updated_at_utc
                )
                VALUES(?, ?, ?, 300, ?, ?, '{}', ?, ?)
                """,
                (
                    f"pytest-event-{index}",
                    f"{symbol.lower()}-updown-5m-pytest-{index}",
                    symbol,
                    f"2026-06-05T20:0{index}:00+00:00",
                    f"2026-06-05T20:0{index + 5}:00+00:00",
                    f"2026-06-05T20:0{index}:00+00:00",
                    f"2026-06-05T20:0{index}:00+00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO event_tokens(
                    event_token_key, event_key, token_id, outcome, event_slug, symbol,
                    source_json, inserted_at_utc, updated_at_utc
                )
                VALUES(?, ?, ?, 'Up', ?, ?, '{}', ?, ?)
                """,
                (
                    f"pytest-event-{index}:up",
                    f"pytest-event-{index}",
                    f"pytest-token-{index}",
                    f"{symbol.lower()}-updown-5m-pytest-{index}",
                    symbol,
                    f"2026-06-05T20:0{index}:00+00:00",
                    f"2026-06-05T20:0{index}:00+00:00",
                ),
            )
            entry_minute = 10 + index
            forward_minute = 11 + index
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, ?, ?, ?, ?, 'Up', ?, ?, ?, 0.50, 0.49, 0.51, 0.02, 100, '{}')
                """,
                (
                    f"pytest-entry-tick-{index}",
                    f"pytest-event-{index}:up",
                    f"pytest-event-{index}",
                    f"pytest-token-{index}",
                    f"{symbol.lower()}-updown-5m-pytest-{index}",
                    f"2026-06-05T20:{entry_minute}:00+00:00",
                    f"2026-06-05T20:{entry_minute}:00+00:00",
                    f"2026-06-05T20:{entry_minute}:00+00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, ?, ?, ?, ?, 'Up', ?, ?, ?, 0.64, 0.63, 0.65, 0.02, 80, '{}')
                """,
                (
                    f"pytest-forward-tick-{index}",
                    f"pytest-event-{index}:up",
                    f"pytest-event-{index}",
                    f"pytest-token-{index}",
                    f"{symbol.lower()}-updown-5m-pytest-{index}",
                    f"2026-06-05T20:{forward_minute}:00+00:00",
                    f"2026-06-05T20:{forward_minute}:00+00:00",
                    f"2026-06-05T20:{forward_minute}:00+00:00",
                ),
            )
            _insert_profile_context(
                conn,
                event_key=f"pytest-event-{index}",
                event_slug=f"{symbol.lower()}-updown-5m-pytest-{index}",
                up_ratio=0.7,
            )

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-strategy-live-replay-forward-batch",
            strategy_ids=("profile_hedge_scalping_v1",),
            max_trades_per_strategy=1,
            forward_mark_horizon_seconds=60.0,
            max_scenarios=2,
        )
    )

    assert payload["scenario_count"] == 2
    assert payload["result_count"] == 2
    assert payload["passed_count"] == 2
    assert payload["db_counts"]["strategy_validation_runs"] == 2
    with connect(Path(db_path)) as conn:
        rows = conn.execute(
            "SELECT run_id, evidence_json FROM strategy_validation_runs ORDER BY run_id"
        ).fetchall()
    assert [row["run_id"] for row in rows] == [
        "pytest-strategy-live-replay-forward-batch-scenario-1",
        "pytest-strategy-live-replay-forward-batch-scenario-2",
    ]
    for row in rows:
        evidence = json.loads(row["evidence_json"])
        economics = evidence["result"]["economics"]
        assert economics["source"] == "runtime_shadow_forward_mark"
        assert economics["sample_count"] == 1
        assert economics["promotion_economics_ready"] is True


def test_strategy_live_replay_profile_preferred_selector_skips_wrong_side_tokens_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-profile-preferred.sqlite")

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-profile-event', 'btc-updown-5m-profile-selector', 'BTC', 300,
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:05:00+00:00',
                '{}', '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        for outcome, token_id in (("Up", "pytest-up-token"), ("Down", "pytest-down-token")):
            conn.execute(
                """
                INSERT INTO event_tokens(
                    event_token_key, event_key, token_id, outcome, event_slug, symbol,
                    source_json, inserted_at_utc, updated_at_utc
                )
                VALUES(?, 'pytest-profile-event', ?, ?, 'btc-updown-5m-profile-selector',
                       'BTC', '{}', '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
                """,
                (f"pytest-profile-event:{outcome.lower()}", token_id, outcome),
            )
        ticks = (
            ("down-entry", "pytest-profile-event:down", "pytest-down-token", "Down", "2026-06-05T20:02:00+00:00", 0.49, 0.51, 0.50),
            ("down-forward", "pytest-profile-event:down", "pytest-down-token", "Down", "2026-06-05T20:03:00+00:00", 0.42, 0.44, 0.43),
            ("up-entry", "pytest-profile-event:up", "pytest-up-token", "Up", "2026-06-05T20:00:00+00:00", 0.49, 0.51, 0.50),
            ("up-forward", "pytest-profile-event:up", "pytest-up-token", "Up", "2026-06-05T20:01:00+00:00", 0.63, 0.65, 0.64),
        )
        for key, event_token_key, token_id, outcome, timestamp, bid, ask, mid in ticks:
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, ?, 'pytest-profile-event', ?, 'btc-updown-5m-profile-selector',
                       ?, ?, ?, ?, ?, ?, ?, 0.02, 100, '{}')
                """,
                (f"pytest-{key}", event_token_key, token_id, outcome, timestamp, timestamp, timestamp, mid, bid, ask),
            )
        _insert_profile_context(
            conn,
            event_key="pytest-profile-event",
            event_slug="btc-updown-5m-profile-selector",
            up_ratio=0.7,
        )

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-profile-preferred-selector",
            strategy_ids=("profile_distribution_consensus_hold_60s_v1",),
            forward_mark_horizon_seconds=60.0,
            scenario_selector="profile_preferred",
        )
    )

    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark_profile_preferred"
    assert payload["scenario"]["outcome"] == "Up"
    assert payload["passed_count"] == 1
    assert "profile_distribution_side_mismatch" not in payload["blockers"]["profile_distribution_consensus_hold_60s_v1"]


def test_strategy_live_replay_profile_opposed_selector_targets_contrarian_side_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-profile-opposed.sqlite")

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES(
                'pytest-profile-event', 'btc-updown-5m-profile-opposed', 'BTC', 300,
                '2026-06-05T20:00:00+00:00', '2026-06-05T20:05:00+00:00',
                '{}', '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00'
            )
            """
        )
        for outcome, token_id in (("Up", "pytest-up-token"), ("Down", "pytest-down-token")):
            conn.execute(
                """
                INSERT INTO event_tokens(
                    event_token_key, event_key, token_id, outcome, event_slug, symbol,
                    source_json, inserted_at_utc, updated_at_utc
                )
                VALUES(?, 'pytest-profile-event', ?, ?, 'btc-updown-5m-profile-opposed',
                       'BTC', '{}', '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
                """,
                (f"pytest-profile-event:{outcome.lower()}", token_id, outcome),
            )
        ticks = (
            ("up-entry", "pytest-profile-event:up", "pytest-up-token", "Up", "2026-06-05T20:02:00+00:00", 0.49, 0.51, 0.50),
            ("up-forward", "pytest-profile-event:up", "pytest-up-token", "Up", "2026-06-05T20:03:00+00:00", 0.62, 0.64, 0.63),
            ("down-entry", "pytest-profile-event:down", "pytest-down-token", "Down", "2026-06-05T20:00:00+00:00", 0.49, 0.51, 0.50),
            ("down-forward", "pytest-profile-event:down", "pytest-down-token", "Down", "2026-06-05T20:01:00+00:00", 0.57, 0.59, 0.58),
        )
        for key, event_token_key, token_id, outcome, timestamp, bid, ask, mid in ticks:
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, ?, 'pytest-profile-event', ?, 'btc-updown-5m-profile-opposed',
                       ?, ?, ?, ?, ?, ?, ?, 0.02, 100, '{}')
                """,
                (f"pytest-{key}", event_token_key, token_id, outcome, timestamp, timestamp, timestamp, mid, bid, ask),
            )
        _insert_profile_context(
            conn,
            event_key="pytest-profile-event",
            event_slug="btc-updown-5m-profile-opposed",
            up_ratio=0.7,
        )

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-profile-opposed-selector",
            strategy_ids=("profile_distribution_contrarian_hold_60s_v1",),
            forward_mark_horizon_seconds=60.0,
            scenario_selector="profile_opposed",
        )
    )

    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark_profile_opposed"
    assert payload["scenario"]["outcome"] == "Down"
    assert payload["passed_count"] == 1
    assert "profile_distribution_side_mismatch" not in payload["blockers"]["profile_distribution_contrarian_hold_60s_v1"]


def test_strategy_live_replay_profile_group_selector_requires_matching_subgroup_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-profile-group.sqlite")

    with connect(db_path) as conn:
        for event_key, event_slug, minute, up_forward_bid in (
            ("aggregate-only-event", "btc-updown-5m-aggregate-only", "04", 0.43),
            ("splus-event", "btc-updown-5m-splus-group", "00", 0.63),
        ):
            conn.execute(
                """
                INSERT INTO events(
                    event_key, event_slug, symbol, cadence_seconds,
                    event_start_time_utc, event_end_time_utc, source_json,
                    inserted_at_utc, updated_at_utc
                )
                VALUES(?, ?, 'BTC', 300, '2026-06-05T20:00:00+00:00',
                       '2026-06-05T20:05:00+00:00', '{}',
                       '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
                """,
                (event_key, event_slug),
            )
            conn.execute(
                """
                INSERT INTO event_tokens(
                    event_token_key, event_key, token_id, outcome, event_slug, symbol,
                    source_json, inserted_at_utc, updated_at_utc
                )
                VALUES(?, ?, ?, 'Up', ?, 'BTC', '{}',
                       '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
                """,
                (f"{event_key}:up", event_key, f"{event_key}-up-token", event_slug),
            )
            for suffix, entry_minute, bid, ask, mid in (
                ("entry", minute, 0.49, 0.51, 0.50),
                ("forward", f"{int(minute) + 1:02d}", up_forward_bid, up_forward_bid + 0.02, up_forward_bid + 0.01),
            ):
                timestamp = f"2026-06-05T20:{entry_minute}:00+00:00"
                conn.execute(
                    """
                    INSERT INTO polymarket_price_ticks(
                        price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                        chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                        mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                    )
                    VALUES(?, ?, ?, ?, ?, 'Up', ?, ?, ?, ?, ?, ?, 0.02, 100, '{}')
                    """,
                    (
                        f"{event_key}-{suffix}",
                        f"{event_key}:up",
                        event_key,
                        f"{event_key}-up-token",
                        event_slug,
                        timestamp,
                        timestamp,
                        timestamp,
                        mid,
                        bid,
                        ask,
                    ),
                )
            _insert_profile_context(
                conn,
                event_key=event_key,
                event_slug=event_slug,
                up_ratio=0.7,
            )
        _insert_splus_hedger_components(conn, event_key="splus-event", up_cost=70.0, down_cost=30.0)

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-profile-group-selector",
            strategy_ids=("profile_splus_hedger_follow_hold_60s_v1",),
            forward_mark_horizon_seconds=60.0,
            scenario_selector="profile_group",
        )
    )

    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark_profile_group"
    assert payload["scenario"]["event_key"] == "splus-event"
    assert payload["scenario"]["outcome"] == "Up"
    assert payload["passed_count"] == 1
    assert "profile_distribution_group_missing:by_grade_style:S+ / hedger" not in payload["blockers"][
        "profile_splus_hedger_follow_hold_60s_v1"
    ]


def test_strategy_live_replay_profile_group_quality_selector_prefilters_v10_blockers_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-profile-group-quality.sqlite")

    def insert_event_with_up_ticks(
        conn,
        *,
        event_key: str,
        event_slug: str,
        entry_minute: str,
        entry_bid: float,
        entry_ask: float,
        forward_bid: float,
        forward_ask: float,
    ) -> None:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES(?, ?, 'BTC', 300, '2026-06-05T20:00:00+00:00',
                   '2026-06-05T20:05:00+00:00', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """,
            (event_key, event_slug),
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES(?, ?, ?, 'Up', ?, 'BTC', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """,
            (f"{event_key}:up", event_key, f"{event_key}-up-token", event_slug),
        )
        forward_minute = f"{int(entry_minute) + 1:02d}"
        for suffix, minute, bid, ask in (
            ("entry", entry_minute, entry_bid, entry_ask),
            ("forward", forward_minute, forward_bid, forward_ask),
        ):
            timestamp = f"2026-06-05T20:{minute}:00+00:00"
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, ?, ?, ?, ?, 'Up', ?, ?, ?, ?, ?, ?, ?, 100, '{}')
                """,
                (
                    f"{event_key}-{suffix}",
                    f"{event_key}:up",
                    event_key,
                    f"{event_key}-up-token",
                    event_slug,
                    timestamp,
                    timestamp,
                    timestamp,
                    (bid + ask) / 2.0,
                    bid,
                    ask,
                    ask - bid,
                ),
            )

    def insert_low_concentration_splus_components(conn, *, event_key: str, profile_count: int = 4) -> None:
        snapshot_key = f"{event_key}-profile-snapshot"
        for index in range(profile_count):
            profile_key = f"pytest-low-concentration-splus-{index + 1}"
            for outcome, total_cost, price in (("Up", 90.0, 0.90), ("Down", 10.0, 0.10)):
                per_profile_cost = total_cost / profile_count
                conn.execute(
                    """
                    INSERT INTO profile_distribution_components(
                        distribution_component_key, distribution_snapshot_key, profile_key,
                        handle, grade, trading_style, source_mode, outcome, net_shares,
                        net_notional_usd, cost_basis_usd, grade_weight, style_weight,
                        final_weight, contribution_json, inserted_at_utc
                    )
                    VALUES(?, ?, ?, ?, 'S+', 'hedger',
                           'raw_activity', ?, ?, ?, ?, 1, 1, 1, '{}',
                           '2026-06-05T20:00:30+00:00')
                    """,
                    (
                        f"{snapshot_key}:{profile_key}:{outcome.lower()}",
                        snapshot_key,
                        profile_key,
                        f"@pytest-low-concentration-splus-{index + 1}",
                        outcome,
                        per_profile_cost / price,
                        per_profile_cost,
                        per_profile_cost,
                    ),
                )

    with connect(db_path) as conn:
        for event_key, event_slug, minute, bid, ask in (
            ("quality-event", "btc-updown-5m-profile-quality", "00", 0.59, 0.60),
            ("high-entry-event", "btc-updown-5m-profile-high-entry", "01", 0.83, 0.84),
            ("concentrated-event", "btc-updown-5m-profile-concentrated", "02", 0.59, 0.60),
        ):
            insert_event_with_up_ticks(
                conn,
                event_key=event_key,
                event_slug=event_slug,
                entry_minute=minute,
                entry_bid=bid,
                entry_ask=ask,
                forward_bid=0.76,
                forward_ask=0.78,
            )
            _insert_profile_context(conn, event_key=event_key, event_slug=event_slug, up_ratio=0.9)
            _insert_option_path_stats(
                conn,
                event_key=event_key,
                event_slug=event_slug,
                avg_rolling_60s_range=0.03,
                snapshot_count=8,
                pair_sum_range=0.05,
            )
        insert_low_concentration_splus_components(conn, event_key="quality-event")
        insert_low_concentration_splus_components(conn, event_key="high-entry-event")
        _insert_single_dominant_profile_components(
            conn,
            event_key="concentrated-event",
            dominant_up_cost=90.0,
            supporting_down_cost=10.0,
        )

    payload = scout_strategy_live_replay_candidates(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-profile-group-quality-selector",
            strategy_ids=("profile_splus_hedger_follow_hold_60s_v10",),
            forward_mark_horizon_seconds=60.0,
            max_scenarios=3,
            scenario_selector="profile_group_quality",
        )
    )

    assert payload["scenario_selector"] == "profile_group_quality"
    assert payload["scenario_sources"] == ["polymarket_price_ticks_forward_mark_profile_group_quality"]
    assert payload["scenario_count"] == 1
    assert payload["scenarios"][0]["event_key"] == "quality-event"


def test_strategy_live_replay_high_inversion_selector_prefers_rebound_heavy_event_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-high-inversion-selector.sqlite")

    with connect(db_path) as conn:
        for event_key, event_slug, entry_minute, forward_minute, avg_range, max_range, crossings, flips, rebounds in (
            ("quiet-event", "btc-updown-5m-quiet", "02", "03", 0.005, 0.008, 0, 0, 0),
            ("active-event", "btc-updown-5m-active", "00", "01", 0.065, 0.12, 8, 5, 4),
        ):
            conn.execute(
                """
                INSERT INTO events(
                    event_key, event_slug, symbol, cadence_seconds,
                    event_start_time_utc, event_end_time_utc, source_json,
                    inserted_at_utc, updated_at_utc
                )
                VALUES(?, ?, 'BTC', 300, '2026-06-05T20:00:00+00:00',
                       '2026-06-05T20:05:00+00:00', '{}',
                       '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
                """,
                (event_key, event_slug),
            )
            conn.execute(
                """
                INSERT INTO event_tokens(
                    event_token_key, event_key, token_id, outcome, event_slug, symbol,
                    source_json, inserted_at_utc, updated_at_utc
                )
                VALUES(?, ?, ?, 'Up', ?, 'BTC', '{}',
                       '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
                """,
                (f"{event_key}:up", event_key, f"{event_key}-token", event_slug),
            )
            for key, minute, bid, ask, mid in (
                ("entry", entry_minute, 0.49, 0.51, 0.50),
                ("forward", forward_minute, 0.62, 0.64, 0.63),
            ):
                conn.execute(
                    """
                    INSERT INTO polymarket_price_ticks(
                        price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                        chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                        mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                    )
                    VALUES(?, ?, ?, ?, ?, 'Up', ?, ?, ?, ?, ?, ?, 0.02, 100, '{}')
                    """,
                    (
                        f"{event_key}-{key}",
                        f"{event_key}:up",
                        event_key,
                        f"{event_key}-token",
                        event_slug,
                        f"2026-06-05T20:{minute}:00+00:00",
                        f"2026-06-05T20:{minute}:00+00:00",
                        f"2026-06-05T20:{minute}:00+00:00",
                        mid,
                        bid,
                        ask,
                    ),
                )
            _insert_option_path_stats(
                conn,
                event_key=event_key,
                event_slug=event_slug,
                avg_rolling_60s_range=avg_range,
                max_rolling_60s_range=max_range,
                level_crossing_count=crossings,
                rebound_direction_flip_count=flips,
                strong_rebound_touch_count=rebounds,
            )

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-high-inversion-selector",
            strategy_ids=("master_hedge_grid_floor_neutral_rebound_v1",),
            forward_mark_horizon_seconds=60.0,
            scenario_selector="high_inversion",
        )
    )

    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark_high_inversion"
    assert payload["scenario"]["event_key"] == "active-event"


def test_strategy_live_replay_high_inversion_disjoint_caps_per_event_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-high-inversion-disjoint.sqlite")

    def insert_event(conn, *, event_key: str, score_seed: int, entry_offsets: tuple[int, ...]) -> None:
        event_slug = f"btc-updown-5m-{event_key}"
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES(?, ?, 'BTC', 300, '2026-06-05T20:00:00+00:00',
                   '2026-06-05T20:05:00+00:00', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """,
            (event_key, event_slug),
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES(?, ?, ?, 'Up', ?, 'BTC', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """,
            (f"{event_key}:up", event_key, f"{event_key}-token", event_slug),
        )
        for offset in entry_offsets:
            for suffix, seconds, bid, ask, mid in (
                ("entry", offset, 0.49, 0.51, 0.50),
                ("forward", offset + 60, 0.62, 0.64, 0.63),
            ):
                minute, second = divmod(seconds, 60)
                timestamp = f"2026-06-05T20:{minute:02d}:{second:02d}+00:00"
                conn.execute(
                    """
                    INSERT INTO polymarket_price_ticks(
                        price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                        chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                        mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                    )
                    VALUES(?, ?, ?, ?, ?, 'Up', ?, ?, ?, ?, ?, ?, 0.02, 100, '{}')
                    """,
                    (
                        f"{event_key}-{offset}-{suffix}",
                        f"{event_key}:up",
                        event_key,
                        f"{event_key}-token",
                        event_slug,
                        timestamp,
                        timestamp,
                        timestamp,
                        mid,
                        bid,
                        ask,
                    ),
                )
        _insert_option_path_stats(
            conn,
            event_key=event_key,
            event_slug=event_slug,
            avg_rolling_60s_range=0.03 + score_seed * 0.001,
            max_rolling_60s_range=0.07 + score_seed * 0.001,
            level_crossing_count=score_seed,
            rebound_direction_flip_count=max(1, score_seed // 2),
            strong_rebound_touch_count=max(1, score_seed // 3),
        )

    with connect(db_path) as conn:
        insert_event(conn, event_key="top-event", score_seed=12, entry_offsets=(0, 10, 20, 30))
        insert_event(conn, event_key="second-event", score_seed=10, entry_offsets=(0, 10, 20, 30))
        insert_event(conn, event_key="third-event", score_seed=8, entry_offsets=(0, 10))

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-high-inversion-disjoint",
            strategy_ids=("master_hedge_grid_floor_neutral_rebound_v1",),
            max_scenarios=5,
            max_trades_per_strategy=5,
            forward_mark_horizon_seconds=60.0,
            scenario_selector="high_inversion_disjoint",
        )
    )

    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark_high_inversion"
    assert payload["scenario_count"] == 5
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT evidence_json
              FROM strategy_validation_runs
             WHERE run_id LIKE 'pytest-high-inversion-disjoint%'
             ORDER BY run_id
            """
        ).fetchall()
    event_counts: dict[str, int] = {}
    for row in rows:
        result = json.loads(row["evidence_json"])["result"]
        event_key = str(result["event_key"])
        event_counts[event_key] = event_counts.get(event_key, 0) + 1
    assert event_counts["top-event"] == 2
    assert event_counts["second-event"] == 2
    assert event_counts["third-event"] == 1


def test_strategy_live_replay_recent_high_inversion_disjoint_prefers_recent_window_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-recent-high-inversion-disjoint.sqlite")

    def insert_event(
        conn,
        *,
        event_key: str,
        score_seed: int,
        entry_minute: int,
        forward_minute: int,
    ) -> None:
        event_slug = f"btc-updown-5m-{event_key}"
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES(?, ?, 'BTC', 300, '2026-06-05T20:00:00+00:00',
                   '2026-06-05T20:05:00+00:00', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """,
            (event_key, event_slug),
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES(?, ?, ?, 'Up', ?, 'BTC', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """,
            (f"{event_key}:up", event_key, f"{event_key}-token", event_slug),
        )
        for suffix, minute, bid, ask, mid in (
            ("entry", entry_minute, 0.49, 0.51, 0.50),
            ("forward", forward_minute, 0.62, 0.64, 0.63),
        ):
            timestamp = f"2026-06-05T20:{minute:02d}:00+00:00"
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, ?, ?, ?, ?, 'Up', ?, ?, ?, ?, ?, ?, 0.02, 100, '{}')
                """,
                (
                    f"{event_key}-{suffix}",
                    f"{event_key}:up",
                    event_key,
                    f"{event_key}-token",
                    event_slug,
                    timestamp,
                    timestamp,
                    timestamp,
                    mid,
                    bid,
                    ask,
                ),
            )
        _insert_option_path_stats(
            conn,
            event_key=event_key,
            event_slug=event_slug,
            avg_rolling_60s_range=0.03 + score_seed * 0.001,
            max_rolling_60s_range=0.07 + score_seed * 0.001,
            level_crossing_count=score_seed,
            rebound_direction_flip_count=max(1, score_seed // 2),
            strong_rebound_touch_count=max(1, score_seed // 3),
        )

    with connect(db_path) as conn:
        insert_event(conn, event_key="old-best-event", score_seed=20, entry_minute=0, forward_minute=1)
        insert_event(conn, event_key="recent-event", score_seed=8, entry_minute=3, forward_minute=4)

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-recent-high-inversion-disjoint",
            strategy_ids=("master_hedge_grid_floor_neutral_rebound_v1",),
            max_scenarios=1,
            forward_mark_horizon_seconds=60.0,
            scenario_selector="recent_high_inversion_disjoint",
        )
    )

    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark_recent_high_inversion"
    assert payload["scenario"]["event_key"] == "recent-event"


def test_strategy_live_replay_hedge_grid_ready_selector_filters_weak_recent_rows_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-hedge-grid-ready-selector.sqlite")

    def insert_event(
        conn,
        *,
        event_key: str,
        entry_minute: int,
        forward_minute: int,
        best_ask: float,
        avg_range: float,
        crossings: int,
        flips: int,
        near_50c: int,
        pair_pressure: float,
        pair_sum_range: float = 0.05,
    ) -> None:
        event_slug = f"btc-updown-5m-{event_key}"
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES(?, ?, 'BTC', 300, '2026-06-05T20:00:00+00:00',
                   '2026-06-05T20:05:00+00:00', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """,
            (event_key, event_slug),
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES(?, ?, ?, 'Up', ?, 'BTC', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """,
            (f"{event_key}:up", event_key, f"{event_key}-token", event_slug),
        )
        for suffix, minute, bid, ask, mid in (
            ("entry", entry_minute, best_ask - 0.01, best_ask, best_ask - 0.005),
            ("forward", forward_minute, min(0.99, best_ask + 0.05), min(0.99, best_ask + 0.06), best_ask + 0.055),
        ):
            timestamp = f"2026-06-05T20:{minute:02d}:00+00:00"
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, ?, ?, ?, ?, 'Up', ?, ?, ?, ?, ?, ?, 0.01, 100, '{}')
                """,
                (
                    f"{event_key}-{suffix}",
                    f"{event_key}:up",
                    event_key,
                    f"{event_key}-token",
                    event_slug,
                    timestamp,
                    timestamp,
                    timestamp,
                    mid,
                    bid,
                    ask,
                ),
            )
        _insert_option_path_stats(
            conn,
            event_key=event_key,
            event_slug=event_slug,
            avg_rolling_60s_range=avg_range,
            max_rolling_60s_range=max(0.07, avg_range * 2.0),
            level_crossing_count=crossings,
            rebound_direction_flip_count=flips,
            near_50c_sample_count=near_50c,
            avg_pair_depth_pressure=pair_pressure,
            pair_sum_range=pair_sum_range,
        )

    with connect(db_path) as conn:
        insert_event(
            conn,
            event_key="ready-older-event",
            entry_minute=0,
            forward_minute=1,
            best_ask=0.52,
            avg_range=0.04,
            crossings=8,
            flips=4,
            near_50c=10,
            pair_pressure=0.16,
        )
        insert_event(
            conn,
            event_key="weak-recent-event",
            entry_minute=3,
            forward_minute=4,
            best_ask=0.52,
            avg_range=0.01,
            crossings=2,
            flips=1,
            near_50c=3,
            pair_pressure=0.04,
        )

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-hedge-grid-ready-selector",
            strategy_ids=("master_hedge_grid_floor_neutral_rebound_v1",),
            max_scenarios=1,
            forward_mark_horizon_seconds=60.0,
            scenario_selector="hedge_grid_ready",
        )
    )

    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark_hedge_grid_ready"
    assert payload["scenario"]["event_key"] == "ready-older-event"
    closed_cycle_payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-hedge-grid-closed-cycle-no-fallback",
            strategy_ids=("master_hedge_grid_floor_paired_seed_builder_v12",),
            max_scenarios=1,
            forward_mark_horizon_seconds=60.0,
            scenario_selector="hedge_grid_closed_cycle_ready",
        )
    )
    assert closed_cycle_payload["scenario_count"] == 0
    assert closed_cycle_payload["blockers"]["scenario_selector"] == ["no_matching_scenarios"]


def test_strategy_live_replay_hedge_grid_closed_cycle_ready_selector_requires_closed_floor_path_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-hedge-grid-closed-cycle-selector.sqlite")

    def insert_event(
        conn,
        *,
        event_key: str,
        entry_minute: int,
        forward_minute: int,
        best_ask: float,
        closed_cycle: bool,
    ) -> None:
        event_slug = f"btc-updown-5m-{event_key}"
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES(?, ?, 'BTC', 300, '2026-06-05T20:00:00+00:00',
                   '2026-06-05T20:05:00+00:00', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """,
            (event_key, event_slug),
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES(?, ?, ?, 'Up', ?, 'BTC', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """,
            (f"{event_key}:up", event_key, f"{event_key}-token", event_slug),
        )
        for suffix, minute, bid, ask, mid in (
            ("entry", entry_minute, best_ask - 0.01, best_ask, best_ask - 0.005),
            ("forward", forward_minute, min(0.99, best_ask + 0.05), min(0.99, best_ask + 0.06), best_ask + 0.055),
        ):
            timestamp = f"2026-06-05T20:{minute:02d}:00+00:00"
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, ?, ?, ?, ?, 'Up', ?, ?, ?, ?, ?, ?, 0.01, 100, '{}')
                """,
                (
                    f"{event_key}-{suffix}",
                    f"{event_key}:up",
                    event_key,
                    f"{event_key}-token",
                    event_slug,
                    timestamp,
                    timestamp,
                    timestamp,
                    mid,
                    bid,
                    ask,
                ),
            )
        _insert_option_path_stats(
            conn,
            event_key=event_key,
            event_slug=event_slug,
            avg_rolling_60s_range=0.05,
            max_rolling_60s_range=0.12,
            level_crossing_count=8,
            rebound_direction_flip_count=4,
            near_50c_sample_count=10,
            avg_pair_depth_pressure=0.16,
            pair_sum_range=0.04,
        )
        _insert_pair_snapshot(
            conn,
            event_key=event_key,
            event_slug=event_slug,
            timestamp=f"2026-06-05T20:{entry_minute:02d}:00+00:00",
            up_bid=0.54,
            up_ask=0.55,
            down_bid=0.36,
            down_ask=0.37,
            key_suffix="entry",
        )
        if closed_cycle:
            path = (
                ("dip", "20", 0.48, 0.50, 0.30, 0.31),
                ("rebound", "40", 0.54, 0.56, 0.34, 0.35),
                ("forward", "01:00", 0.61, 0.63, 0.38, 0.40),
            )
        else:
            path = (
                ("dip", "20", 0.48, 0.50, 0.30, 0.31),
                ("stall", "40", 0.50, 0.52, 0.31, 0.32),
                ("forward", "03:00", 0.51, 0.53, 0.31, 0.32),
            )
        for suffix, seconds, up_bid, up_ask, down_bid, down_ask in path:
            timestamp = (
                f"2026-06-05T20:{seconds}+00:00"
                if ":" in seconds
                else f"2026-06-05T20:{entry_minute:02d}:{seconds}+00:00"
            )
            _insert_pair_snapshot(
                conn,
                event_key=event_key,
                event_slug=event_slug,
                timestamp=timestamp,
                up_bid=up_bid,
                up_ask=up_ask,
                down_bid=down_bid,
                down_ask=down_ask,
                key_suffix=suffix,
            )

    with connect(db_path) as conn:
        insert_event(conn, event_key="closed-cycle-older-event", entry_minute=0, forward_minute=1, best_ask=0.55, closed_cycle=True)
        insert_event(conn, event_key="weak-recent-event", entry_minute=2, forward_minute=3, best_ask=0.55, closed_cycle=False)

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-hedge-grid-closed-cycle-ready-selector",
            strategy_ids=("master_hedge_grid_floor_paired_seed_builder_v12",),
            max_scenarios=1,
            forward_mark_horizon_seconds=60.0,
            scenario_selector="hedge_grid_closed_cycle_ready",
        )
    )

    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark_hedge_grid_closed_cycle_ready"
    assert payload["scenario"]["event_key"] == "closed-cycle-older-event"
    assert payload["passed_count"] == 1


def test_strategy_live_replay_low_range_no_edge_selector_prefers_dead_window_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-low-range-no-edge-selector.sqlite")

    with connect(db_path) as conn:
        for event_key, event_slug, entry_minute, forward_minute, avg_range, max_range, forward_bid in (
            ("active-event", "btc-updown-5m-active", "00", "01", 0.07, 0.12, 0.62),
            ("quiet-no-edge-event", "btc-updown-5m-quiet-no-edge", "02", "03", 0.012, 0.02, 0.515),
        ):
            conn.execute(
                """
                INSERT INTO events(
                    event_key, event_slug, symbol, cadence_seconds,
                    event_start_time_utc, event_end_time_utc, source_json,
                    inserted_at_utc, updated_at_utc
                )
                VALUES(?, ?, 'BTC', 300, '2026-06-05T20:00:00+00:00',
                       '2026-06-05T20:05:00+00:00', '{}',
                       '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
                """,
                (event_key, event_slug),
            )
            conn.execute(
                """
                INSERT INTO event_tokens(
                    event_token_key, event_key, token_id, outcome, event_slug, symbol,
                    source_json, inserted_at_utc, updated_at_utc
                )
                VALUES(?, ?, ?, 'Up', ?, 'BTC', '{}',
                       '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
                """,
                (f"{event_key}:up", event_key, f"{event_key}-token", event_slug),
            )
            for key, minute, bid, ask, mid in (
                ("entry", entry_minute, 0.49, 0.51, 0.50),
                ("forward", forward_minute, forward_bid, min(0.99, forward_bid + 0.02), forward_bid + 0.01),
            ):
                conn.execute(
                    """
                    INSERT INTO polymarket_price_ticks(
                        price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                        chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                        mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                    )
                    VALUES(?, ?, ?, ?, ?, 'Up', ?, ?, ?, ?, ?, ?, 0.02, 100, '{}')
                    """,
                    (
                        f"{event_key}-{key}",
                        f"{event_key}:up",
                        event_key,
                        f"{event_key}-token",
                        event_slug,
                        f"2026-06-05T20:{minute}:00+00:00",
                        f"2026-06-05T20:{minute}:00+00:00",
                        f"2026-06-05T20:{minute}:00+00:00",
                        mid,
                        bid,
                        ask,
                    ),
                )
            _insert_option_path_stats(
                conn,
                event_key=event_key,
                event_slug=event_slug,
                avg_rolling_60s_range=avg_range,
                max_rolling_60s_range=max_range,
                level_crossing_count=0 if event_key.startswith("quiet") else 8,
                rebound_direction_flip_count=0 if event_key.startswith("quiet") else 5,
                strong_rebound_touch_count=0 if event_key.startswith("quiet") else 4,
            )

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-low-range-no-edge-selector",
            strategy_ids=("master_hedge_grid_floor_low_range_no_edge_control_v1",),
            forward_mark_horizon_seconds=60.0,
            scenario_selector="low_range_no_edge",
        )
    )

    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark_low_range_no_edge"
    assert payload["scenario"]["event_key"] == "quiet-no-edge-event"
    assert payload["blocked_count"] == 1
    assert "diagnostic_no_live_promotion_control_lane" in payload["blockers"][
        "master_hedge_grid_floor_low_range_no_edge_control_v1"
    ]
    with connect(db_path) as conn:
        assert count_rows(conn, "strategy_replay_candidate_cache_runs") == 1
        assert count_rows(conn, "strategy_replay_candidate_scenarios") == 1
        cache_row = conn.execute(
            """
            SELECT selector, candidate_count, status
            FROM strategy_replay_candidate_cache_runs
            """
        ).fetchone()
        scenario_row = conn.execute(
            """
            SELECT selector, source_json
            FROM strategy_replay_candidate_scenarios
            """
        ).fetchone()

    assert dict(cache_row)["selector"] == "low_range_no_edge"
    assert dict(cache_row)["candidate_count"] == 1
    assert dict(cache_row)["status"] == "ok"
    assert dict(scenario_row)["selector"] == "low_range_no_edge"
    assert json.loads(dict(scenario_row)["source_json"])["source"] == "low_range_no_edge"


def test_strategy_live_replay_tail_touch_selector_prefers_empirical_tail_event_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-tail-touch-selector.sqlite")

    tail_table = {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "bucket_probabilities": {"touched_5c": {"gt180": 1.0}},
        "sides": {
            "up": {"touched_5c": {"touched": False}},
            "down": {
                "touched_5c": {
                    "touched": True,
                    "first_touch_seconds": 60.0,
                    "time_remaining_seconds": 240.0,
                    "time_remaining_bucket": "gt180",
                    "max_after_touch": 0.24,
                    "first_target_key": "reached_10c",
                    "reached_10c": True,
                    "time_to_10c_seconds": 40.0,
                }
            },
        },
    }
    with connect(db_path) as conn:
        for event_key, event_slug, outcome, entry_minute, forward_minute, entry_bid, entry_ask, forward_bid in (
            ("plain-event", "btc-updown-5m-plain", "Up", "00", "01", 0.49, 0.51, 0.52),
            ("tail-event", "btc-updown-5m-tail", "Down", "01", "02", 0.04, 0.05, 0.12),
        ):
            conn.execute(
                """
                INSERT INTO events(
                    event_key, event_slug, symbol, cadence_seconds,
                    event_start_time_utc, event_end_time_utc, source_json,
                    inserted_at_utc, updated_at_utc
                )
                VALUES(?, ?, 'BTC', 300, '2026-06-05T20:00:00+00:00',
                       '2026-06-05T20:05:00+00:00', '{}',
                       '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
                """,
                (event_key, event_slug),
            )
            conn.execute(
                """
                INSERT INTO event_tokens(
                    event_token_key, event_key, token_id, outcome, event_slug, symbol,
                    source_json, inserted_at_utc, updated_at_utc
                )
                VALUES(?, ?, ?, ?, ?, 'BTC', '{}',
                       '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
                """,
                (f"{event_key}:{outcome.lower()}", event_key, f"{event_key}-token", outcome, event_slug),
            )
            for key, minute, bid, ask, mid in (
                ("entry", entry_minute, entry_bid, entry_ask, (entry_bid + entry_ask) / 2.0),
                ("forward", forward_minute, forward_bid, min(0.99, forward_bid + 0.02), forward_bid + 0.01),
            ):
                conn.execute(
                    """
                    INSERT INTO polymarket_price_ticks(
                        price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                        chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                        mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 100, '{}')
                    """,
                    (
                        f"{event_key}-{key}",
                        f"{event_key}:{outcome.lower()}",
                        event_key,
                        f"{event_key}-token",
                        event_slug,
                        outcome,
                        f"2026-06-05T20:{minute}:00+00:00",
                        f"2026-06-05T20:{minute}:00+00:00",
                        f"2026-06-05T20:{minute}:00+00:00",
                        mid,
                        bid,
                        ask,
                        ask - bid,
                    ),
                )
            _insert_option_path_stats(
                conn,
                event_key=event_key,
                event_slug=event_slug,
                avg_rolling_60s_range=0.06,
                max_rolling_60s_range=0.12,
                level_crossing_count=6,
                rebound_direction_flip_count=4,
                strong_rebound_touch_count=3,
                near_50c_sample_count=1,
                snapshot_count=12,
            )
        conn.execute(
            """
            UPDATE polymarket_event_path_stats
               SET tail_comeback_table_json = ?
             WHERE event_key = 'tail-event'
            """,
            (json.dumps(tail_table, sort_keys=True),),
        )

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-tail-touch-selector",
            strategy_ids=("option_tail_reversal_hold_60s_v2",),
            forward_mark_horizon_seconds=60.0,
            scenario_selector="tail_touch",
        )
    )

    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark_tail_touch"
    assert payload["scenario"]["event_key"] == "tail-event"
    assert payload["scenario"]["outcome"] == "Down"
    assert abs(payload["scenario"]["time_remaining_seconds"] - 240.0) < 0.01


def test_strategy_live_replay_tail_touch_forward_edge_clean_selector_filters_weak_edge_rows_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-tail-touch-forward-edge-clean-selector.sqlite")

    tail_table = {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "bucket_probabilities": {"touched_5c": {"gt180": 1.0}},
        "sides": {
            "down": {
                "touched_5c": {
                    "touched": True,
                    "first_touch_seconds": 60.0,
                    "time_remaining_seconds": 240.0,
                    "time_remaining_bucket": "gt180",
                    "max_after_touch": 0.24,
                    "first_target_key": "reached_10c",
                    "reached_10c": True,
                    "time_to_10c_seconds": 40.0,
                }
            }
        },
    }
    with connect(db_path) as conn:
        for event_key, event_slug, entry_bid, entry_ask, forward_bid, strong_rebound_touch_count in (
            ("tail-weak-edge", "btc-updown-5m-tail-weak-edge", 0.08, 0.09, 0.10, 3),
            ("tail-weak-rebounds", "eth-updown-5m-tail-weak-rebounds", 0.03, 0.04, 0.13, 0),
            ("tail-strong-edge", "eth-updown-5m-tail-strong-edge", 0.04, 0.05, 0.12, 3),
        ):
            conn.execute(
                """
                INSERT INTO events(
                    event_key, event_slug, symbol, cadence_seconds,
                    event_start_time_utc, event_end_time_utc, source_json,
                    inserted_at_utc, updated_at_utc
                )
                VALUES(?, ?, 'BTC', 300, '2026-06-05T20:00:00+00:00',
                       '2026-06-05T20:05:00+00:00', '{}',
                       '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
                """,
                (event_key, event_slug),
            )
            conn.execute(
                """
                INSERT INTO event_tokens(
                    event_token_key, event_key, token_id, outcome, event_slug, symbol,
                    source_json, inserted_at_utc, updated_at_utc
                )
                VALUES(?, ?, ?, 'Down', ?, 'BTC', '{}',
                       '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
                """,
                (f"{event_key}:down", event_key, f"{event_key}-token", event_slug),
            )
            for key, minute, bid, ask, mid in (
                ("entry", "01", entry_bid, entry_ask, (entry_bid + entry_ask) / 2.0),
                ("forward", "02", forward_bid, min(0.99, forward_bid + 0.02), forward_bid + 0.01),
            ):
                conn.execute(
                    """
                    INSERT INTO polymarket_price_ticks(
                        price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                        chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                        mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                    )
                    VALUES(?, ?, ?, ?, ?, 'Down', ?, ?, ?, ?, ?, ?, ?, 120, '{}')
                    """,
                    (
                        f"{event_key}-{key}",
                        f"{event_key}:down",
                        event_key,
                        f"{event_key}-token",
                        event_slug,
                        f"2026-06-05T20:{minute}:00+00:00",
                        f"2026-06-05T20:{minute}:00+00:00",
                        f"2026-06-05T20:{minute}:00+00:00",
                        mid,
                        bid,
                        ask,
                        ask - bid,
                    ),
                )
            _insert_option_path_stats(
                conn,
                event_key=event_key,
                event_slug=event_slug,
                avg_rolling_60s_range=0.06,
                max_rolling_60s_range=0.12,
                level_crossing_count=6,
                rebound_direction_flip_count=4,
                strong_rebound_touch_count=strong_rebound_touch_count,
                near_50c_sample_count=1,
                snapshot_count=12,
            )
            conn.execute(
                """
                UPDATE polymarket_event_path_stats
                   SET tail_comeback_table_json = ?
                 WHERE event_key = ?
                """,
                (json.dumps(tail_table, sort_keys=True), event_key),
            )
            _insert_profile_context(
                conn,
                event_key=event_key,
                event_slug=event_slug,
                up_ratio=0.61,
                up_reconstructed_price=0.58,
                down_reconstructed_price=0.42,
            )

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-tail-touch-forward-edge-clean-selector",
            strategy_ids=("master_hedge_grid_floor_tail_reversal_probe_v5",),
            forward_mark_horizon_seconds=60.0,
            scenario_selector="tail_touch_forward_edge_clean",
        )
    )

    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark_tail_touch_forward_edge_clean"
    assert payload["scenario"]["event_key"] == "tail-strong-edge"
    assert payload["scenario"]["event_slug"] == "eth-updown-5m-tail-strong-edge"
    with connect(db_path) as conn:
        assert count_rows(conn, "strategy_replay_candidate_cache_runs") == 1
        assert count_rows(conn, "strategy_replay_candidate_scenarios") == 1
        conn.execute("DELETE FROM polymarket_price_ticks")

    cached_payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-tail-touch-forward-edge-clean-selector-cached",
            strategy_ids=("master_hedge_grid_floor_tail_reversal_probe_v5",),
            forward_mark_horizon_seconds=60.0,
            scenario_selector="tail_touch_forward_edge_clean",
        )
    )

    assert cached_payload["scenario_source"] == "polymarket_price_ticks_forward_mark_tail_touch_forward_edge_clean_cached"
    assert cached_payload["scenario"]["event_key"] == "tail-strong-edge"


def test_master_tail_v3_treats_profile_pressure_as_confidence_not_blocker_pytest() -> None:
    scenario = RuntimeScenario(
        event_key="tail-confidence-event",
        event_token_key="tail-confidence-event:up",
        token_id="tail-token",
        event_slug="btc-updown-5m-tail-confidence",
        outcome="Up",
        shares=1.0,
        limit_price=0.05,
        spread=0.01,
        liquidity_depth=100.0,
        time_remaining_seconds=120.0,
        live_market_verified=True,
        signal_context={
            "best_bid": 0.04,
            "best_ask": 0.05,
            "paired_down_ask": 0.95,
            "forward_best_bid": 0.12,
            "target_up_ratio": 0.20,
            "profile_distribution_ready": True,
            "profile_distribution": {
                "profile_count": 6,
                "source_age_seconds": 10.0,
                "coverage_warnings": [],
            },
            "option_path_ready": True,
            "option_path": {
                "snapshot_count": 12,
                "level_crossing_count": 6,
                "rebound_direction_flip_count": 4,
                "strong_rebound_touch_count": 3,
                "extreme_sample_count": 2,
                "near_50c_sample_count": 8,
                "avg_rolling_60s_range": 0.08,
                "max_rolling_60s_range": 0.16,
                "avg_pair_depth_pressure": 0.20,
                "pair_sum_range": 0.02,
                "tail_comeback_table": {
                    "bucket_probabilities": {"touched_5c": {"60_180": 0.75}},
                },
            },
        },
    )

    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="pytest-master-tail-v3-confidence",
            mode="shadow",
            executor_boundary=ExecutorBoundaryConfig(
                supervised_runtime_gate=True,
                ledger_gate=True,
                risk_gate=True,
                reconciliation_gate=True,
            ),
            allow_live_submission=False,
            live_environment_approved=False,
            credentials_ready=False,
        ),
        scenario=scenario,
        specs=(get_strategy("master_hedge_grid_floor_tail_reversal_probe_v3"),),
    )

    result = report.results[0]
    assert "profile_distribution_side_mismatch" not in result.blockers
    assert "profile_distribution_pressure_too_balanced" not in result.blockers
    decision = result.attribution["strategy_decision"]
    assert decision["profile_dependency_mode"] == "confidence_modifier"
    assert decision["profile_confidence_status"] == "opposed"


def test_strategy_live_replay_tail_floor_probe_v3_accepts_tail_touch_window_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-tail-floor-probe-v3.sqlite")

    tail_table = {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "bucket_probabilities": {"touched_10c": {"gt180": 0.76}},
        "sides": {
            "down": {
                "touched_10c": {
                    "touched": True,
                    "first_touch_seconds": 60.0,
                    "time_remaining_seconds": 240.0,
                    "time_remaining_bucket": "gt180",
                    "max_after_touch": 0.26,
                    "first_target_key": "reached_20c",
                    "reached_20c": True,
                    "time_to_20c_seconds": 40.0,
                }
            }
        },
    }
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES('tail-event', 'btc-updown-5m-tail', 'BTC', 300, '2026-06-05T20:00:00+00:00',
                   '2026-06-05T20:05:00+00:00', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES('tail-event:down', 'tail-event', 'tail-token', 'Down', 'btc-updown-5m-tail', 'BTC', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """
        )
        for key, minute, bid, ask, mid in (
            ("entry", "01", 0.09, 0.10, 0.095),
            ("forward", "02", 0.21, 0.23, 0.22),
        ):
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, 'tail-event:down', 'tail-event', 'tail-token', 'btc-updown-5m-tail', 'Down',
                       ?, ?, ?, ?, ?, ?, 0.01, 100, '{}')
                """,
                (f"tail-event-{key}", f"2026-06-05T20:{minute}:00+00:00", f"2026-06-05T20:{minute}:00+00:00", f"2026-06-05T20:{minute}:00+00:00", mid, bid, ask),
            )
        _insert_option_path_stats(
            conn,
            event_key="tail-event",
            event_slug="btc-updown-5m-tail",
            avg_rolling_60s_range=0.06,
            max_rolling_60s_range=0.12,
            level_crossing_count=6,
            rebound_direction_flip_count=4,
            strong_rebound_touch_count=3,
            near_50c_sample_count=1,
            snapshot_count=12,
        )
        conn.execute(
            """
            UPDATE polymarket_event_path_stats
               SET tail_comeback_table_json = ?
             WHERE event_key = 'tail-event'
            """,
            (json.dumps(tail_table, sort_keys=True),),
        )

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-tail-floor-probe-v3",
            strategy_ids=("master_hedge_grid_floor_tail_reversal_probe_v3",),
            forward_mark_horizon_seconds=60.0,
            scenario_selector="tail_touch",
        )
    )

    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark_tail_touch"
    assert payload["passed_count"] == 1
    assert payload["blocked_count"] == 0
    assert "grid_price_band_not_reached" not in payload["blockers"]["master_hedge_grid_floor_tail_reversal_probe_v3"]


def test_strategy_live_replay_tail_floor_probe_v4_accepts_profile_price_tail_touch_window_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-tail-floor-probe-v4.sqlite")

    tail_table = {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "bucket_probabilities": {"touched_10c": {"gt180": 0.76}},
        "sides": {
            "down": {
                "touched_10c": {
                    "touched": True,
                    "first_touch_seconds": 75.0,
                    "time_remaining_seconds": 240.0,
                    "time_remaining_bucket": "gt180",
                    "max_after_touch": 0.24,
                    "first_target_key": "reached_20c",
                    "reached_20c": True,
                    "time_to_20c_seconds": 45.0,
                }
            }
        },
    }
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES('tail-event-v4', 'eth-updown-5m-tail', 'ETH', 300, '2026-06-05T20:00:00+00:00',
                   '2026-06-05T20:05:00+00:00', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES('tail-event-v4:down', 'tail-event-v4', 'tail-token-v4', 'Down', 'eth-updown-5m-tail', 'ETH', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """
        )
        for key, minute, bid, ask, mid in (
            ("entry", "01", 0.09, 0.10, 0.095),
            ("forward", "02", 0.12, 0.13, 0.125),
        ):
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, 'tail-event-v4:down', 'tail-event-v4', 'tail-token-v4', 'eth-updown-5m-tail', 'Down',
                       ?, ?, ?, ?, ?, ?, 0.01, 120, '{}')
                """,
                (
                    f"tail-event-v4-{key}",
                    f"2026-06-05T20:{minute}:00+00:00",
                    f"2026-06-05T20:{minute}:00+00:00",
                    f"2026-06-05T20:{minute}:00+00:00",
                    mid,
                    bid,
                    ask,
                ),
            )
        _insert_option_path_stats(
            conn,
            event_key="tail-event-v4",
            event_slug="eth-updown-5m-tail",
            avg_rolling_60s_range=0.06,
            max_rolling_60s_range=0.12,
            level_crossing_count=6,
            rebound_direction_flip_count=4,
            strong_rebound_touch_count=3,
            near_50c_sample_count=1,
            snapshot_count=12,
        )
        conn.execute(
            """
            UPDATE polymarket_event_path_stats
               SET tail_comeback_table_json = ?
             WHERE event_key = 'tail-event-v4'
            """,
            (json.dumps(tail_table, sort_keys=True),),
        )
        _insert_profile_context(
            conn,
            event_key="tail-event-v4",
            event_slug="eth-updown-5m-tail",
            up_ratio=0.61,
            up_reconstructed_price=0.58,
            down_reconstructed_price=0.42,
        )

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-tail-floor-probe-v4",
            strategy_ids=("master_hedge_grid_floor_tail_reversal_probe_v4",),
            forward_mark_horizon_seconds=60.0,
            scenario_selector="tail_touch",
        )
    )

    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark_tail_touch"
    assert payload["passed_count"] == 1
    assert payload["blocked_count"] == 0
    assert payload["blockers"]["master_hedge_grid_floor_tail_reversal_probe_v4"] == []


def test_strategy_live_replay_tail_floor_probe_v5_requires_stronger_forward_edge_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-tail-floor-probe-v5.sqlite")

    tail_table = {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "bucket_probabilities": {"touched_10c": {"gt180": 0.76}},
        "sides": {
            "down": {
                "touched_10c": {
                    "touched": True,
                    "first_touch_seconds": 75.0,
                    "time_remaining_seconds": 240.0,
                    "time_remaining_bucket": "gt180",
                    "max_after_touch": 0.24,
                    "first_target_key": "reached_20c",
                    "reached_20c": True,
                    "time_to_20c_seconds": 45.0,
                }
            }
        },
    }
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES('tail-event-v5', 'eth-updown-5m-tail-v5', 'ETH', 300, '2026-06-05T20:00:00+00:00',
                   '2026-06-05T20:05:00+00:00', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES('tail-event-v5:down', 'tail-event-v5', 'tail-token-v5', 'Down', 'eth-updown-5m-tail-v5', 'ETH', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """
        )
        for key, minute, bid, ask, mid in (
            ("entry", "01", 0.09, 0.10, 0.095),
            ("forward", "02", 0.14, 0.15, 0.145),
        ):
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, 'tail-event-v5:down', 'tail-event-v5', 'tail-token-v5', 'eth-updown-5m-tail-v5', 'Down',
                       ?, ?, ?, ?, ?, ?, 0.01, 120, '{}')
                """,
                (
                    f"tail-event-v5-{key}",
                    f"2026-06-05T20:{minute}:00+00:00",
                    f"2026-06-05T20:{minute}:00+00:00",
                    f"2026-06-05T20:{minute}:00+00:00",
                    mid,
                    bid,
                    ask,
                ),
            )
        _insert_option_path_stats(
            conn,
            event_key="tail-event-v5",
            event_slug="eth-updown-5m-tail-v5",
            avg_rolling_60s_range=0.06,
            max_rolling_60s_range=0.12,
            level_crossing_count=6,
            rebound_direction_flip_count=4,
            strong_rebound_touch_count=3,
            near_50c_sample_count=1,
            snapshot_count=12,
        )
        conn.execute(
            """
            UPDATE polymarket_event_path_stats
               SET tail_comeback_table_json = ?
             WHERE event_key = 'tail-event-v5'
            """,
            (json.dumps(tail_table, sort_keys=True),),
        )
        _insert_profile_context(
            conn,
            event_key="tail-event-v5",
            event_slug="eth-updown-5m-tail-v5",
            up_ratio=0.61,
            up_reconstructed_price=0.58,
            down_reconstructed_price=0.42,
        )

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-tail-floor-probe-v5",
            strategy_ids=("master_hedge_grid_floor_tail_reversal_probe_v5",),
            forward_mark_horizon_seconds=60.0,
            scenario_selector="tail_touch",
        )
    )

    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark_tail_touch"
    assert payload["passed_count"] == 1
    assert payload["blocked_count"] == 0
    assert payload["blockers"]["master_hedge_grid_floor_tail_reversal_probe_v5"] == []


def test_strategy_live_replay_tail_floor_probe_v6_requires_protected_surplus_for_tails_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-tail-floor-probe-v6.sqlite")

    tail_table = {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "bucket_probabilities": {"touched_10c": {"gt180": 0.76}},
        "sides": {
            "down": {
                "touched_10c": {
                    "touched": True,
                    "first_touch_seconds": 75.0,
                    "time_remaining_seconds": 240.0,
                    "time_remaining_bucket": "gt180",
                    "max_after_touch": 0.24,
                    "first_target_key": "reached_20c",
                    "reached_20c": True,
                    "time_to_20c_seconds": 45.0,
                }
            }
        },
    }
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES('tail-event-v6', 'eth-updown-5m-tail-v6', 'ETH', 300, '2026-06-05T20:00:00+00:00',
                   '2026-06-05T20:05:00+00:00', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES('tail-event-v6:down', 'tail-event-v6', 'tail-token-v6', 'Down', 'eth-updown-5m-tail-v6', 'ETH', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """
        )
        for key, minute, bid, ask, mid in (
            ("entry", "01", 0.09, 0.10, 0.095),
            ("forward", "02", 0.14, 0.15, 0.145),
        ):
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, 'tail-event-v6:down', 'tail-event-v6', 'tail-token-v6', 'eth-updown-5m-tail-v6', 'Down',
                       ?, ?, ?, ?, ?, ?, 0.01, 120, '{}')
                """,
                (
                    f"tail-event-v6-{key}",
                    f"2026-06-05T20:{minute}:00+00:00",
                    f"2026-06-05T20:{minute}:00+00:00",
                    f"2026-06-05T20:{minute}:00+00:00",
                    mid,
                    bid,
                    ask,
                ),
            )
        _insert_option_path_stats(
            conn,
            event_key="tail-event-v6",
            event_slug="eth-updown-5m-tail-v6",
            avg_rolling_60s_range=0.06,
            max_rolling_60s_range=0.12,
            level_crossing_count=6,
            rebound_direction_flip_count=4,
            strong_rebound_touch_count=3,
            near_50c_sample_count=1,
            snapshot_count=12,
        )
        conn.execute(
            """
            UPDATE polymarket_event_path_stats
               SET tail_comeback_table_json = ?
             WHERE event_key = 'tail-event-v6'
            """,
            (json.dumps(tail_table, sort_keys=True),),
        )
        _insert_profile_context(
            conn,
            event_key="tail-event-v6",
            event_slug="eth-updown-5m-tail-v6",
            up_ratio=0.61,
            up_reconstructed_price=0.58,
            down_reconstructed_price=0.42,
        )

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-tail-floor-probe-v6",
            strategy_ids=("master_hedge_grid_floor_tail_reversal_probe_v6",),
            forward_mark_horizon_seconds=60.0,
            scenario_selector="tail_touch",
        )
    )

    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark_tail_touch"
    assert payload["passed_count"] == 1
    assert payload["blocked_count"] == 0
    assert payload["blockers"]["master_hedge_grid_floor_tail_reversal_probe_v6"] == []
    with connect(Path(db_path)) as conn:
        evidence = json.loads(
            conn.execute(
                """
                SELECT evidence_json
                  FROM strategy_validation_runs
                 WHERE run_id = 'pytest-tail-floor-probe-v6'
                """
            ).fetchone()["evidence_json"]
        )
    decision = evidence["result"]["attribution"]["strategy_decision"]
    assert decision["surplus_tail_budget"] == 0.0
    assert decision["tail_orders_allowed"] is False


def test_strategy_live_replay_tail_floor_probe_v7_requires_splus_subgroup_context_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-live-replay-tail-floor-probe-v7.sqlite")

    tail_table = {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "bucket_probabilities": {"touched_10c": {"gt180": 0.76}},
        "sides": {
            "down": {
                "touched_10c": {
                    "touched": True,
                    "first_touch_seconds": 75.0,
                    "time_remaining_seconds": 240.0,
                    "time_remaining_bucket": "gt180",
                    "max_after_touch": 0.24,
                    "first_target_key": "reached_20c",
                    "reached_20c": True,
                    "time_to_20c_seconds": 45.0,
                }
            }
        },
    }
    with connect(db_path) as conn:
        for event_key, event_slug, entry_minute, forward_minute, forward_bid in (
            ("tail-event-v7-aggregate", "eth-updown-5m-tail-v7-aggregate", "01", "02", 0.14),
            ("tail-event-v7-subgroup", "eth-updown-5m-tail-v7-subgroup", "03", "04", 0.15),
        ):
            conn.execute(
                """
                INSERT INTO events(
                    event_key, event_slug, symbol, cadence_seconds,
                    event_start_time_utc, event_end_time_utc, source_json,
                    inserted_at_utc, updated_at_utc
                )
                VALUES(?, ?, 'ETH', 300, '2026-06-05T20:00:00+00:00',
                       '2026-06-05T20:05:00+00:00', '{}',
                       '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
                """,
                (event_key, event_slug),
            )
            conn.execute(
                """
                INSERT INTO event_tokens(
                    event_token_key, event_key, token_id, outcome, event_slug, symbol,
                    source_json, inserted_at_utc, updated_at_utc
                )
                VALUES(?, ?, ?, 'Down', ?, 'ETH', '{}',
                       '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
                """,
                (f"{event_key}:down", event_key, f"{event_key}-down-token", event_slug),
            )
            for suffix, minute, bid, ask, mid in (
                ("entry", entry_minute, 0.09, 0.10, 0.095),
                ("forward", forward_minute, forward_bid, forward_bid + 0.01, forward_bid + 0.005),
            ):
                timestamp = f"2026-06-05T20:{minute}:00+00:00"
                conn.execute(
                    """
                    INSERT INTO polymarket_price_ticks(
                        price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                        chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                        mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                    )
                    VALUES(?, ?, ?, ?, ?, 'Down', ?, ?, ?, ?, ?, ?, 0.01, 120, '{}')
                    """,
                    (
                        f"{event_key}-{suffix}",
                        f"{event_key}:down",
                        event_key,
                        f"{event_key}-down-token",
                        event_slug,
                        timestamp,
                        timestamp,
                        timestamp,
                        mid,
                        bid,
                        ask,
                    ),
                )
            _insert_option_path_stats(
                conn,
                event_key=event_key,
                event_slug=event_slug,
                avg_rolling_60s_range=0.06,
                max_rolling_60s_range=0.12,
                level_crossing_count=6,
                rebound_direction_flip_count=4,
                strong_rebound_touch_count=3,
                near_50c_sample_count=1,
                snapshot_count=12,
            )
            conn.execute(
                """
                UPDATE polymarket_event_path_stats
                   SET tail_comeback_table_json = ?
                 WHERE event_key = ?
                """,
                (json.dumps(tail_table, sort_keys=True), event_key),
            )
            _insert_profile_context(
                conn,
                event_key=event_key,
                event_slug=event_slug,
                up_ratio=0.39,
                up_reconstructed_price=0.42,
                down_reconstructed_price=0.58,
            )
        _insert_splus_hedger_components(conn, event_key="tail-event-v7-subgroup", up_cost=30.0, down_cost=70.0)

    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
            db_path=db_path,
            run_id="pytest-tail-floor-probe-v7",
            strategy_ids=("master_hedge_grid_floor_tail_reversal_probe_v7",),
            forward_mark_horizon_seconds=60.0,
            max_scenarios=2,
            scenario_selector="tail_touch_forward_edge_clean",
        )
    )

    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark_tail_touch_forward_edge_clean"
    assert payload["passed_count"] == 1
    assert payload["blocked_count"] == 1
    blocked = next(item for item in payload["scenario_statuses"] if item["blocked_count"] == 1)
    passed = next(item for item in payload["scenario_statuses"] if item["passed_count"] == 1)
    assert blocked["event_key"] == "tail-event-v7-aggregate"
    assert blocked["blockers"]["master_hedge_grid_floor_tail_reversal_probe_v7"] == [
        "profile_distribution_group_missing:by_grade_style:S+ / hedger"
    ]
    assert passed["event_key"] == "tail-event-v7-subgroup"
    assert passed["blockers"]["master_hedge_grid_floor_tail_reversal_probe_v7"] == []
    with connect(Path(db_path)) as conn:
        evidence_rows = [
            json.loads(row["evidence_json"])
            for row in conn.execute(
                """
                SELECT evidence_json
                  FROM strategy_validation_runs
                 WHERE run_id IN ('pytest-tail-floor-probe-v7-scenario-1', 'pytest-tail-floor-probe-v7-scenario-2')
                 ORDER BY run_id
                """
            ).fetchall()
        ]
    subgroup_evidence = next(
        evidence
        for evidence in evidence_rows
        if evidence["result"]["event_key"] == "tail-event-v7-subgroup"
    )
    subgroup_decision = subgroup_evidence["result"]["attribution"]["strategy_decision"]
    assert subgroup_decision["profile_ratio_source"] == "by_grade_style:S+ / hedger"
    assert subgroup_decision["profile_target_side"] == "Down"
    assert subgroup_decision["tail_orders_allowed"] is False


def test_strategy_backtest_replay_tail_floor_probe_v3_uses_captured_tail_touch_windows_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-backtest-replay-tail-floor-probe-v3.sqlite")

    tail_table = {
        "schema_version": "crypto_options_tail_comeback_table_v1",
        "bucket_probabilities": {"touched_10c": {"gt180": 0.76}},
        "sides": {
            "down": {
                "touched_10c": {
                    "touched": True,
                    "first_touch_seconds": 60.0,
                    "time_remaining_seconds": 240.0,
                    "time_remaining_bucket": "gt180",
                    "max_after_touch": 0.26,
                    "first_target_key": "reached_20c",
                    "reached_20c": True,
                    "time_to_20c_seconds": 40.0,
                }
            }
        },
    }
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_key, event_slug, symbol, cadence_seconds,
                event_start_time_utc, event_end_time_utc, source_json,
                inserted_at_utc, updated_at_utc
            )
            VALUES('tail-event', 'btc-updown-5m-tail', 'BTC', 300, '2026-06-05T20:00:00+00:00',
                   '2026-06-05T20:05:00+00:00', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO event_tokens(
                event_token_key, event_key, token_id, outcome, event_slug, symbol,
                source_json, inserted_at_utc, updated_at_utc
            )
            VALUES('tail-event:down', 'tail-event', 'tail-token', 'Down', 'btc-updown-5m-tail', 'BTC', '{}',
                   '2026-06-05T20:00:00+00:00', '2026-06-05T20:00:00+00:00')
            """
        )
        for key, minute, bid, ask, mid in (
            ("entry", "01", 0.09, 0.10, 0.095),
            ("forward", "02", 0.21, 0.23, 0.22),
        ):
            conn.execute(
                """
                INSERT INTO polymarket_price_ticks(
                    price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    mid_price, best_bid, best_ask, spread, depth_top3_ask_size, source_json
                )
                VALUES(?, 'tail-event:down', 'tail-event', 'tail-token', 'btc-updown-5m-tail', 'Down',
                       ?, ?, ?, ?, ?, ?, 0.01, 100, '{}')
                """,
                (
                    f"tail-event-{key}",
                    f"2026-06-05T20:{minute}:00+00:00",
                    f"2026-06-05T20:{minute}:00+00:00",
                    f"2026-06-05T20:{minute}:00+00:00",
                    mid,
                    bid,
                    ask,
                ),
            )
        _insert_option_path_stats(
            conn,
            event_key="tail-event",
            event_slug="btc-updown-5m-tail",
            avg_rolling_60s_range=0.06,
            max_rolling_60s_range=0.12,
            level_crossing_count=6,
            rebound_direction_flip_count=4,
            strong_rebound_touch_count=3,
            near_50c_sample_count=1,
            snapshot_count=12,
        )
        conn.execute(
            """
            UPDATE polymarket_event_path_stats
               SET tail_comeback_table_json = ?
             WHERE event_key = 'tail-event'
            """,
            (json.dumps(tail_table, sort_keys=True),),
        )

    payload = run_strategy_backtest_replay(
        StrategyBacktestReplayConfig(
            db_path=db_path,
            run_id="pytest-tail-floor-probe-v3-historical",
            strategy_ids=("master_hedge_grid_floor_tail_reversal_probe_v3",),
            forward_mark_horizon_seconds=60.0,
            scenario_selector="tail_touch",
        )
    )

    assert payload["scenario_source"] == "polymarket_price_ticks_forward_mark_tail_touch"
    assert payload["runtime_mode"] == "dry_run"
    assert payload["passed_count"] == 1
    assert payload["blocked_count"] == 0
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT run_type, run_phase, run_status, lifecycle_audit_status, reconciliation_status
              FROM strategy_validation_runs
             WHERE strategy_id = 'master_hedge_grid_floor_tail_reversal_probe_v3'
            """
        ).fetchone()
    assert dict(row) == {
        "run_type": "dry_run",
        "run_phase": "historical_replay",
        "run_status": "completed",
        "lifecycle_audit_status": "passed",
        "reconciliation_status": "reconciled",
    }
