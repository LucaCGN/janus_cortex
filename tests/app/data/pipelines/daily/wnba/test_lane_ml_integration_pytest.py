from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app.data.nodes.wnba.live.play_by_play import normalize_play_by_play_payload
from app.data.nodes.wnba.schedule.season_schedule import normalize_schedule_payload
from app.data.pipelines.daily.wnba.analysis.contracts import (
    default_shadow_lane_families,
    default_shadow_lane_specs,
)
from app.data.pipelines.daily.wnba.analysis.deterministic_lanes import (
    build_wnba_lane_registry,
    build_wnba_lane_signal_rows,
)
from app.data.pipelines.daily.wnba.analysis.ml_model import train_wnba_short_horizon_reprice_model
from app.data.pipelines.daily.wnba.analysis.state_panel import build_wnba_state_panel
from tools.run_wnba_analysis_bundle import build_wnba_analysis_bundle


FIXTURE_PATH = (
    Path(__file__).parents[3]
    / "nodes"
    / "wnba"
    / "fixtures"
    / "wnba_cdn_samples.json"
)


def _samples() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _fixture_state_panel() -> pd.DataFrame:
    samples = _samples()
    games_df, _teams_df = normalize_schedule_payload(samples["schedule"], season="2026")
    pbp_df = normalize_play_by_play_payload(samples["play_by_play"])
    return build_wnba_state_panel(pbp_df, game=games_df.iloc[0].to_dict())


def test_wnba_lane_registry_covers_nba_derived_shadow_families_pytest() -> None:
    expected = {
        "underdog_range_scalp",
        "favorite_floor_rebound",
        "micro_grid_reprice",
        "lead_fragility",
        "panic_fade_fast",
        "quarter_open_reprice",
        "halftime_gap_fill",
        "q4_clutch",
        "winner_definition",
    }

    registry = build_wnba_lane_registry()

    assert expected == set(registry)
    assert set(default_shadow_lane_families()) == expected
    assert all(lane.shadow_only and not lane.orders_allowed for lane in registry.values())


def test_wnba_lane_signals_are_shadow_only_and_block_missing_clob_pytest() -> None:
    state_df = _fixture_state_panel()

    signal_df = build_wnba_lane_signal_rows(state_df, include_no_signal=True)

    assert set(default_shadow_lane_families()) == set(signal_df["family"].unique())
    assert signal_df["orders_allowed"].eq(False).all()
    assert signal_df["shadow_only"].eq(True).all()
    assert "missing_wnba_clob_price_path" in {
        blocker
        for blockers in signal_df["blockers_json"]
        for blocker in blockers
    }


def test_wnba_lane_signals_emit_entry_candidates_with_synthetic_prices_pytest() -> None:
    rows = []
    for index, lane in enumerate(default_shadow_lane_specs()):
        entry_min = lane.entry_price_min if lane.entry_price_min is not None else 0.35
        entry_max = lane.entry_price_max if lane.entry_price_max is not None else 0.75
        price = round((entry_min + entry_max) / 2.0, 4)
        score_floor = lane.min_score_diff if lane.min_score_diff is not None else -4
        score_ceiling = lane.max_score_diff if lane.max_score_diff is not None else 8
        score_diff = min(max(2, score_floor), score_ceiling)
        period = lane.min_period if lane.min_period is not None else 2
        if lane.max_period is not None:
            period = min(period, lane.max_period)
        clock_elapsed = lane.min_clock_elapsed_seconds if lane.min_clock_elapsed_seconds is not None else 300
        if lane.max_clock_elapsed_seconds is not None:
            clock_elapsed = min(clock_elapsed, lane.max_clock_elapsed_seconds)
        seconds_to_game_end = lane.max_seconds_to_game_end if lane.max_seconds_to_game_end is not None else 1600
        if lane.min_seconds_to_game_end is not None:
            seconds_to_game_end = max(seconds_to_game_end, lane.min_seconds_to_game_end)
        price_delta = (lane.min_price_delta_from_open or 0.0) + 0.01
        rows.append(
            {
                "game_id": f"G{index}",
                "team_side": "home",
                "state_index": index,
                "period": period,
                "clock": "PT05M00.00S",
                "clock_elapsed_seconds": clock_elapsed,
                "seconds_to_game_end": seconds_to_game_end,
                "score_diff": score_diff,
                "score_diff_bucket": "synthetic",
                "context_bucket": "synthetic",
                "recent_net_points_5_events": lane.min_recent_net_points or 2,
                "team_price": price,
                "spread": min(lane.max_spread or 0.01, 0.01),
                "opening_price": max(0.01, price - price_delta),
                "price_delta_from_open": price_delta,
            }
        )

    signal_df = build_wnba_lane_signal_rows(pd.DataFrame(rows))
    candidates = signal_df[signal_df["signal_status"] == "entry_candidate"]

    assert set(default_shadow_lane_families()).issubset(set(candidates["family"]))
    assert candidates["target_price"].notna().all()


def test_wnba_short_horizon_model_blocks_without_labels_and_trains_on_synthetic_rows_pytest() -> None:
    blocked = train_wnba_short_horizon_reprice_model(pd.DataFrame())
    assert blocked["status"] == "blocked"
    assert "missing_labeled_wnba_clob_price_windows" in blocked["blockers"]

    rows = []
    for game_index in range(10):
        for state_index in range(12):
            score_diff = state_index - 5
            team_price = 0.25 + (state_index * 0.04)
            rows.append(
                {
                    "game_id": f"G{game_index}",
                    "label_status": "labeled",
                    "label_up_2c": score_diff >= 1,
                    "period": 2 + (state_index % 3),
                    "seconds_to_game_end": 1800 - state_index * 30,
                    "score_diff": score_diff,
                    "recent_net_points": score_diff,
                    "team_price": team_price,
                    "spread": 0.02,
                }
            )

    trained = train_wnba_short_horizon_reprice_model(
        pd.DataFrame(rows),
        min_rows=50,
        min_distinct_games=5,
        max_iter=80,
    )

    assert trained["status"] == "trained_baseline"
    assert trained["validation_rows"] > 0
    assert trained["metrics_json"]["validation_accuracy"] >= 0.5


def test_wnba_analysis_bundle_is_passive_shadow_ready_but_calibration_blocked_pytest() -> None:
    payload = build_wnba_analysis_bundle(season="2026", use_fixture=True)
    readiness = payload["integration_readiness"]

    assert readiness["passive_shadow_ready"] is True
    assert readiness["calibrated_backtesting_ready"] is False
    assert readiness["orders_allowed"] is False
    assert "missing_wnba_clob_price_path" in readiness["calibration_blockers"]
    assert payload["ml_training"]["status"] == "blocked"
