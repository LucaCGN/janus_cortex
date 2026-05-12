from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app.data.nodes.wnba.live.live_stats import normalize_boxscore_payload
from app.data.nodes.wnba.live.play_by_play import normalize_play_by_play_payload
from app.data.nodes.wnba.schedule.season_schedule import normalize_schedule_payload
from app.data.pipelines.daily.wnba.analysis.backtests import run_shadow_price_path_backtest
from app.data.pipelines.daily.wnba.analysis.contracts import WnbaLaneSpec
from app.data.pipelines.daily.wnba.analysis.data_sufficiency import (
    WnbaDataCounts,
    WnbaDataSufficiencyThresholds,
    evaluate_wnba_data_sufficiency,
)
from app.data.pipelines.daily.wnba.analysis.ml_dataset import (
    build_wnba_pbp_ml_feature_rows,
    summarize_ml_training_readiness,
)
from app.data.pipelines.daily.wnba.analysis.state_panel import (
    build_wnba_state_panel,
    wnba_seconds_to_game_end,
)


FIXTURE_PATH = (
    Path(__file__).parents[3]
    / "nodes"
    / "wnba"
    / "fixtures"
    / "wnba_cdn_samples.json"
)


def _samples() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_wnba_state_panel_builds_proxy_rows_with_40_minute_clock_pytest() -> None:
    samples = _samples()
    games_df, _teams_df = normalize_schedule_payload(samples["schedule"], season="2026")
    pbp_df = normalize_play_by_play_payload(samples["play_by_play"])

    state_df = build_wnba_state_panel(pbp_df, game=games_df.iloc[0].to_dict())

    assert len(state_df) == len(pbp_df) * 2
    first_home = state_df[(state_df["team_side"] == "home") & (state_df["state_index"] == 0)].iloc[0]
    assert first_home["seconds_to_game_end"] == 2400
    assert first_home["clock_elapsed_seconds"] == 0
    assert first_home["price_mode"] == "missing_clob"
    assert bool(first_home["backtest_eligible"]) is False
    assert wnba_seconds_to_game_end(4, "PT02M00.00S") == 120


def test_wnba_state_panel_joins_nearest_clob_ticks_and_ml_labels_pytest() -> None:
    samples = _samples()
    games_df, _teams_df = normalize_schedule_payload(samples["schedule"], season="2026")
    pbp_df = normalize_play_by_play_payload(samples["play_by_play"])
    base = datetime(2026, 4, 25, 19, 3, 23, tzinfo=timezone.utc)
    market_df = pd.DataFrame(
        [
            {
                "game_id": "1012600001",
                "team_side": "away",
                "captured_at": (base + timedelta(seconds=1)).isoformat(),
                "best_bid": 0.32,
                "best_ask": 0.34,
                "mid_price": 0.33,
                "spread": 0.02,
                "token_id": "token-ind",
            },
            {
                "game_id": "1012600001",
                "team_side": "away",
                "captured_at": (base + timedelta(minutes=6)).isoformat(),
                "best_bid": 0.38,
                "best_ask": 0.40,
                "mid_price": 0.39,
                "spread": 0.02,
                "token_id": "token-ind",
            },
            {
                "game_id": "1012600001",
                "team_side": "home",
                "captured_at": (base + timedelta(seconds=1)).isoformat(),
                "best_bid": 0.66,
                "best_ask": 0.68,
                "mid_price": 0.67,
                "spread": 0.02,
                "token_id": "token-nyl",
            },
        ]
    )

    state_df = build_wnba_state_panel(pbp_df, game=games_df.iloc[0].to_dict(), market_df=market_df)
    away_rows = state_df[state_df["team_side"] == "away"]
    feature_df = build_wnba_pbp_ml_feature_rows(away_rows, horizon_states=1)

    assert away_rows["team_price"].notna().all()
    assert away_rows.iloc[1]["price_mode"] == "nearest_clob_tick"
    assert "labeled" in set(feature_df["label_status"])
    labeled = feature_df[feature_df["label_status"] == "labeled"].iloc[0]
    assert labeled["label_price_delta"] is not None
    assert summarize_ml_training_readiness(feature_df)["status"] == "blocked"


def test_wnba_shadow_backtest_blocks_without_clob_and_runs_with_prices_pytest() -> None:
    samples = _samples()
    games_df, _teams_df = normalize_schedule_payload(samples["schedule"], season="2026")
    pbp_df = normalize_play_by_play_payload(samples["play_by_play"])
    state_df = build_wnba_state_panel(pbp_df, game=games_df.iloc[0].to_dict())
    lane = WnbaLaneSpec(
        lane_id="test_wnba_micro_grid",
        family="micro_grid_reprice",
        entry_price_min=0.20,
        entry_price_max=0.40,
        max_spread=0.03,
        max_horizon_states=2,
    )

    blocked = run_shadow_price_path_backtest(state_df, lane=lane)
    assert blocked["status"] == "blocked"
    assert blocked["blockers"] == ["missing_wnba_clob_price_path"]

    priced = state_df[state_df["team_side"] == "away"].copy().reset_index(drop=True)
    priced["team_price"] = [0.30, 0.34, 0.37]
    priced["spread"] = 0.02
    result = run_shadow_price_path_backtest(priced, lane=lane)
    assert result["status"] == "shadow_complete"
    assert result["trade_count"] == 1
    assert result["trades"][0]["exit_reason"] == "target"


def test_wnba_data_sufficiency_reports_proxy_only_until_clob_history_exists_pytest() -> None:
    counts = WnbaDataCounts(
        season="2026",
        schedule_games=349,
        games_with_boxscore=50,
        games_with_play_by_play=50,
        play_by_play_rows=20000,
        player_boxscore_rows=1500,
        market_link_count=0,
        clob_tick_count=0,
        clob_trade_count=0,
        state_panel_rows=40000,
        ml_feature_rows=40000,
        labeled_ml_feature_rows=0,
        distinct_ml_games=0,
    )

    audit = evaluate_wnba_data_sufficiency(counts)

    assert audit["status"] == "proxy_state_panel_only"
    assert "CLOB tick/trade history" in audit["verdict"]
    assert audit["ml_readiness"]["status"] == "blocked"
    assert any("insufficient_wnba_clob_tick_history" in row["blockers"] for row in audit["lane_readiness"])


def test_wnba_data_sufficiency_can_mark_shadow_backtest_ready_with_clob_history_pytest() -> None:
    counts = WnbaDataCounts(
        season="2026",
        schedule_games=80,
        games_with_boxscore=80,
        games_with_play_by_play=80,
        market_link_count=40,
        clob_tick_count=6000,
        clob_trade_count=300,
        labeled_ml_feature_rows=6000,
        distinct_ml_games=50,
    )

    audit = evaluate_wnba_data_sufficiency(
        counts,
        thresholds=WnbaDataSufficiencyThresholds(min_schedule_games_for_lane_design=40),
    )

    assert audit["status"] == "ready_for_shadow_backtest"
    assert audit["ml_readiness"]["status"] == "ready_for_experiment"
    assert all(row["status"] == "ready_for_shadow_backtest" for row in audit["lane_readiness"])


def test_wnba_data_sufficiency_marks_price_history_backtest_ready_without_live_ticks_pytest() -> None:
    counts = WnbaDataCounts(
        season="2026",
        schedule_games=80,
        games_with_boxscore=80,
        games_with_play_by_play=80,
        market_link_count=40,
        clob_tick_count=0,
        clob_trade_count=0,
        polymarket_price_history_points=6000,
        games_with_polymarket_price_history=20,
        labeled_ml_feature_rows=6000,
        distinct_ml_games=50,
    )

    audit = evaluate_wnba_data_sufficiency(
        counts,
        thresholds=WnbaDataSufficiencyThresholds(min_schedule_games_for_lane_design=40),
    )

    assert audit["status"] == "price_history_backtest_ready"
    assert audit["ml_readiness"]["status"] == "ready_for_experiment"
    assert any(row["status"] == "price_history_backtest_ready" for row in audit["lane_readiness"])
    assert "first-level historical price-path backtests" in audit["verdict"]


def test_wnba_data_sufficiency_marks_sample_price_history_backtest_ready_pytest() -> None:
    counts = WnbaDataCounts(
        season="2026",
        schedule_games=80,
        games_with_boxscore=80,
        games_with_play_by_play=80,
        market_link_count=1,
        polymarket_price_history_points=1200,
        games_with_polymarket_price_history=1,
    )

    audit = evaluate_wnba_data_sufficiency(
        counts,
        thresholds=WnbaDataSufficiencyThresholds(min_schedule_games_for_lane_design=40),
    )

    assert audit["status"] == "sample_price_history_backtest_ready"
    assert any(row["status"] == "sample_price_history_backtest_ready" for row in audit["lane_readiness"])
    assert "at least one linked finished Polymarket moneyline event" in audit["verdict"]
