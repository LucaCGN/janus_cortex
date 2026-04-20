from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from app.data.pipelines.daily.nba.analysis.backtests import engine
from app.data.pipelines.daily.nba.analysis.backtests.portfolio import (
    build_combined_portfolio_benchmark_frames,
    simulate_trade_portfolio,
)
from app.data.pipelines.daily.nba.analysis.backtests.specs import BacktestResult
from app.data.pipelines.daily.nba.analysis.contracts import ANALYSIS_VERSION, BacktestRunRequest


def _build_state_row(
    *,
    game_id: str,
    team_slug: str,
    opponent_team_slug: str,
    opening_price: float,
    state_index: int,
    team_price: float,
    event_at: datetime,
    opening_band: str,
    period: int = 1,
    period_label: str = "Q1",
    score_for: int | None = None,
    score_against: int | None = None,
    score_diff: int | None = None,
    score_diff_bucket: str = "lead_1_4",
    context_bucket: str = "Q1|lead_1_4",
    net_points_last_5_events: float | None = None,
) -> dict[str, object]:
    resolved_score_for = score_for if score_for is not None else 10 + state_index
    resolved_score_against = score_against if score_against is not None else 8 + state_index
    resolved_score_diff = score_diff if score_diff is not None else resolved_score_for - resolved_score_against
    resolved_net_points = net_points_last_5_events if net_points_last_5_events is not None else 2 * state_index
    return {
        "game_id": game_id,
        "team_side": "home",
        "state_index": state_index,
        "team_id": 1,
        "team_slug": team_slug,
        "opponent_team_id": 2,
        "opponent_team_slug": opponent_team_slug,
        "event_id": f"event-{game_id}",
        "market_id": f"market-{game_id}",
        "outcome_id": f"outcome-{game_id}",
        "season": "2025-26",
        "season_phase": "regular_season",
        "analysis_version": ANALYSIS_VERSION,
        "computed_at": datetime(2026, 2, 22, 23, 0, tzinfo=timezone.utc),
        "game_date": event_at.date(),
        "event_index": state_index,
        "action_id": str(state_index),
        "event_at": event_at,
        "period": period,
        "period_label": period_label,
        "clock": "PT11M00.00S",
        "clock_elapsed_seconds": float(state_index * 60),
        "seconds_to_game_end": float(720 - (state_index * 60)),
        "score_for": resolved_score_for,
        "score_against": resolved_score_against,
        "score_diff": resolved_score_diff,
        "score_diff_bucket": score_diff_bucket,
        "context_bucket": context_bucket,
        "team_led_flag": resolved_score_diff > 0,
        "team_trailed_flag": resolved_score_diff < 0,
        "tied_flag": resolved_score_diff == 0,
        "market_favorite_flag": opening_price >= 0.5,
        "scoreboard_control_mismatch_flag": False,
        "final_winner_flag": True,
        "scoring_side": "home",
        "points_scored": 2,
        "delta_for": 2,
        "delta_against": 0,
        "lead_changes_so_far": 0,
        "team_points_last_5_events": max(0.0, resolved_net_points),
        "opponent_points_last_5_events": 0,
        "net_points_last_5_events": resolved_net_points,
        "opening_price": opening_price,
        "opening_band": opening_band,
        "team_price": team_price,
        "price_delta_from_open": team_price - opening_price,
        "abs_price_delta_from_open": abs(team_price - opening_price),
        "price_mode": "tick",
        "gap_before_seconds": 5.0,
        "gap_after_seconds": 5.0,
        "mfe_from_state": max(0.0, team_price - opening_price),
        "mae_from_state": max(0.0, opening_price - team_price),
        "large_swing_next_12_states_flag": False,
        "crossed_50c_next_12_states_flag": False,
        "winner_stable_70_after_state_flag": False,
        "winner_stable_80_after_state_flag": False,
        "winner_stable_90_after_state_flag": False,
        "winner_stable_95_after_state_flag": False,
    }


def _build_state_frame() -> pd.DataFrame:
    base = datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc)
    rows = [
        _build_state_row(
            game_id="002A5REV",
            team_slug="BOS",
            opponent_team_slug="LAL",
            opening_price=0.75,
            state_index=2,
            team_price=0.66,
            event_at=base + timedelta(minutes=2),
            opening_band="70-80",
            score_diff_bucket="lead_1_4",
            context_bucket="Q1|lead_1_4",
        ),
        _build_state_row(
            game_id="002A5REV",
            team_slug="BOS",
            opponent_team_slug="LAL",
            opening_price=0.75,
            state_index=0,
            team_price=0.75,
            event_at=base,
            opening_band="70-80",
            score_diff_bucket="tied",
            context_bucket="Q1|tied",
        ),
        _build_state_row(
            game_id="002A5REV",
            team_slug="BOS",
            opponent_team_slug="LAL",
            opening_price=0.75,
            state_index=1,
            team_price=0.63,
            event_at=base + timedelta(minutes=1),
            opening_band="70-80",
            score_diff_bucket="trail_1_4",
            context_bucket="Q1|trail_1_4",
        ),
        _build_state_row(
            game_id="002A5REV",
            team_slug="BOS",
            opponent_team_slug="LAL",
            opening_price=0.75,
            state_index=3,
            team_price=0.73,
            event_at=base + timedelta(minutes=3),
            opening_band="70-80",
            score_diff_bucket="lead_1_4",
            context_bucket="Q1|lead_1_4",
        ),
        _build_state_row(
            game_id="002A5INV",
            team_slug="UTA",
            opponent_team_slug="DEN",
            opening_price=0.42,
            state_index=3,
            team_price=0.44,
            event_at=base + timedelta(minutes=13),
            opening_band="40-50",
            score_diff_bucket="trail_1_4",
            context_bucket="Q1|trail_1_4",
        ),
        _build_state_row(
            game_id="002A5INV",
            team_slug="UTA",
            opponent_team_slug="DEN",
            opening_price=0.42,
            state_index=0,
            team_price=0.42,
            event_at=base + timedelta(minutes=10),
            opening_band="40-50",
            score_diff_bucket="trail_1_4",
            context_bucket="Q1|trail_1_4",
        ),
        _build_state_row(
            game_id="002A5INV",
            team_slug="UTA",
            opponent_team_slug="DEN",
            opening_price=0.42,
            state_index=1,
            team_price=0.47,
            event_at=base + timedelta(minutes=11),
            opening_band="40-50",
            score_diff_bucket="trail_1_4",
            context_bucket="Q1|trail_1_4",
        ),
        _build_state_row(
            game_id="002A5INV",
            team_slug="UTA",
            opponent_team_slug="DEN",
            opening_price=0.42,
            state_index=2,
            team_price=0.52,
            event_at=base + timedelta(minutes=12),
            opening_band="40-50",
            score_diff_bucket="lead_1_4",
            context_bucket="Q1|lead_1_4",
        ),
        _build_state_row(
            game_id="002A5WIN",
            team_slug="NYK",
            opponent_team_slug="MIA",
            opening_price=0.79,
            state_index=2,
            team_price=0.83,
            event_at=base + timedelta(minutes=22),
            opening_band="70-80",
            score_diff_bucket="lead_5_9",
            context_bucket="Q2|lead_5_9",
        ),
        _build_state_row(
            game_id="002A5WIN",
            team_slug="NYK",
            opponent_team_slug="MIA",
            opening_price=0.79,
            state_index=0,
            team_price=0.79,
            event_at=base + timedelta(minutes=20),
            opening_band="70-80",
            score_diff_bucket="lead_1_4",
            context_bucket="Q2|lead_1_4",
        ),
        _build_state_row(
            game_id="002A5WIN",
            team_slug="NYK",
            opponent_team_slug="MIA",
            opening_price=0.79,
            state_index=1,
            team_price=0.81,
            event_at=base + timedelta(minutes=21),
            opening_band="70-80",
            score_diff_bucket="lead_5_9",
            context_bucket="Q2|lead_5_9",
        ),
        _build_state_row(
            game_id="002A5WIN",
            team_slug="NYK",
            opponent_team_slug="MIA",
            opening_price=0.79,
            state_index=3,
            team_price=0.74,
            event_at=base + timedelta(minutes=23),
            opening_band="70-80",
            score_diff_bucket="lead_1_4",
            context_bucket="Q2|lead_1_4",
        ),
        _build_state_row(
            game_id="002A5CBK",
            team_slug="ATL",
            opponent_team_slug="CLE",
            opening_price=0.35,
            state_index=0,
            team_price=0.35,
            event_at=base + timedelta(minutes=30),
            opening_band="30-40",
            period=1,
            period_label="Q1",
            score_for=20,
            score_against=20,
            score_diff=0,
            score_diff_bucket="tied",
            context_bucket="Q1|tied",
            net_points_last_5_events=0,
        ),
        _build_state_row(
            game_id="002A5CBK",
            team_slug="ATL",
            opponent_team_slug="CLE",
            opening_price=0.35,
            state_index=1,
            team_price=0.24,
            event_at=base + timedelta(minutes=31),
            opening_band="30-40",
            period=2,
            period_label="Q2",
            score_for=24,
            score_against=30,
            score_diff=-6,
            score_diff_bucket="trail_5_9",
            context_bucket="Q2|trail_5_9",
            net_points_last_5_events=4,
        ),
        _build_state_row(
            game_id="002A5CBK",
            team_slug="ATL",
            opponent_team_slug="CLE",
            opening_price=0.35,
            state_index=2,
            team_price=0.33,
            event_at=base + timedelta(minutes=32),
            opening_band="30-40",
            period=2,
            period_label="Q2",
            score_for=28,
            score_against=31,
            score_diff=-3,
            score_diff_bucket="trail_1_4",
            context_bucket="Q2|trail_1_4",
            net_points_last_5_events=5,
        ),
        _build_state_row(
            game_id="002A5SCALP",
            team_slug="SAC",
            opponent_team_slug="PHX",
            opening_price=0.58,
            state_index=0,
            team_price=0.58,
            event_at=base + timedelta(minutes=40),
            opening_band="50-60",
            period=1,
            period_label="Q1",
            score_for=14,
            score_against=14,
            score_diff=0,
            score_diff_bucket="tied",
            context_bucket="Q1|tied",
            net_points_last_5_events=0,
        ),
        _build_state_row(
            game_id="002A5SCALP",
            team_slug="SAC",
            opponent_team_slug="PHX",
            opening_price=0.58,
            state_index=1,
            team_price=0.44,
            event_at=base + timedelta(minutes=41),
            opening_band="50-60",
            period=1,
            period_label="Q1",
            score_for=18,
            score_against=20,
            score_diff=-2,
            score_diff_bucket="trail_1_4",
            context_bucket="Q1|trail_1_4",
            net_points_last_5_events=-2,
        ),
        _build_state_row(
            game_id="002A5SCALP",
            team_slug="SAC",
            opponent_team_slug="PHX",
            opening_price=0.58,
            state_index=2,
            team_price=0.54,
            event_at=base + timedelta(minutes=42),
            opening_band="50-60",
            period=1,
            period_label="Q1",
            score_for=22,
            score_against=22,
            score_diff=0,
            score_diff_bucket="tied",
            context_bucket="Q1|tied",
            net_points_last_5_events=2,
        ),
    ]
    return pd.DataFrame(rows)


def _build_benchmark_state_frame() -> pd.DataFrame:
    frame = _build_state_frame().copy()
    game_bases = {
        "002A5REV": datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc),
        "002A5INV": datetime(2026, 2, 25, 20, 0, tzinfo=timezone.utc),
        "002A5WIN": datetime(2026, 2, 28, 20, 0, tzinfo=timezone.utc),
        "002A5CBK": datetime(2026, 3, 3, 20, 0, tzinfo=timezone.utc),
        "002A5SCALP": datetime(2026, 3, 6, 20, 0, tzinfo=timezone.utc),
    }
    for game_id, base in game_bases.items():
        mask = frame["game_id"] == game_id
        frame.loc[mask, "event_at"] = frame.loc[mask, "state_index"].apply(lambda idx: base + timedelta(minutes=int(idx)))
        frame.loc[mask, "game_date"] = base.date()
    return frame


def test_backtests_trade_loop_no_lookahead_and_artifacts(tmp_path: Path) -> None:
    frame = _build_state_frame()
    request = BacktestRunRequest(
        season="2025-26",
        season_phase="regular_season",
        strategy_family="all",
        slippage_cents=0,
        output_root=str(tmp_path),
    )

    result = engine.build_backtest_result(frame, request)

    assert result.payload["state_rows_considered"] == len(frame)
    assert result.payload["games_considered"] == 5
    assert set(result.payload["registry"].keys()) == {
        "reversion",
        "inversion",
        "winner_definition",
        "comeback_reversion",
        "volatility_scalp",
    }
    assert result.payload["families"]["reversion"]["trade_count"] == 1
    assert result.payload["families"]["inversion"]["trade_count"] == 1
    assert result.payload["families"]["winner_definition"]["trade_count"] == 1
    assert result.payload["families"]["comeback_reversion"]["trade_count"] == 1
    assert result.payload["families"]["volatility_scalp"]["trade_count"] == 1

    for family, trades_df in result.trade_frames.items():
        assert not trades_df.empty
        trade = trades_df.iloc[0]
        assert trade["exit_state_index"] > trade["entry_state_index"]
        assert trade["entry_at"] < trade["exit_at"]
        assert trade["hold_time_seconds"] > 0
        assert trade["context_tags_json"]["context_bucket"] == trade["context_bucket"]
        assert trade["context_tags_json"]["opening_band"] == trade["opening_band"]

    payload = engine.write_backtest_artifacts(result, tmp_path / "backtests")
    assert Path(payload["artifacts"]["json"]).exists()
    assert Path(payload["artifacts"]["markdown"]).exists()
    assert Path(payload["artifacts"]["family_summary_csv"]).exists()
    assert Path(payload["artifacts"]["reversion_csv"]).exists()
    assert Path(payload["artifacts"]["inversion_csv"]).exists()
    assert Path(payload["artifacts"]["winner_definition_csv"]).exists()
    assert Path(payload["artifacts"]["comeback_reversion_csv"]).exists()
    assert Path(payload["artifacts"]["volatility_scalp_csv"]).exists()
    assert Path(payload["artifacts"]["reversion_best_trades_csv"]).exists()
    assert Path(payload["artifacts"]["reversion_worst_trades_csv"]).exists()
    assert Path(payload["artifacts"]["reversion_context_summary_csv"]).exists()
    assert Path(payload["artifacts"]["reversion_trade_traces_json"]).exists()


def test_backtests_slippage_monotonicity(tmp_path: Path) -> None:
    frame = _build_state_frame()
    zero = engine.build_backtest_result(
        frame,
        BacktestRunRequest(
            season="2025-26",
            season_phase="regular_season",
            strategy_family="all",
            slippage_cents=0,
            output_root=str(tmp_path / "zero"),
        ),
    )
    one = engine.build_backtest_result(
        frame,
        BacktestRunRequest(
            season="2025-26",
            season_phase="regular_season",
            strategy_family="all",
            slippage_cents=1,
            output_root=str(tmp_path / "one"),
        ),
    )

    for family in ("reversion", "inversion", "winner_definition", "comeback_reversion", "volatility_scalp"):
        zero_df = zero.trade_frames[family].sort_values(["game_id", "entry_state_index"]).reset_index(drop=True)
        one_df = one.trade_frames[family].sort_values(["game_id", "entry_state_index"]).reset_index(drop=True)
        assert len(zero_df) == len(one_df) == 1
        assert (one_df["gross_return_with_slippage"] <= zero_df["gross_return_with_slippage"]).all()
        assert float(zero_df.iloc[0]["gross_return_with_slippage"]) > float(one_df.iloc[0]["gross_return_with_slippage"])


def test_trade_portfolio_respects_overlap_game_limit_and_compounding() -> None:
    trades_df = pd.DataFrame(
        [
            {
                "game_id": "G1",
                "team_side": "home",
                "team_slug": "BOS",
                "opponent_team_slug": "LAL",
                "entry_state_index": 1,
                "exit_state_index": 2,
                "entry_at": datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc),
                "exit_at": datetime(2026, 2, 22, 20, 10, tzinfo=timezone.utc),
                "gross_return_with_slippage": 0.50,
            },
            {
                "game_id": "G2",
                "team_side": "home",
                "team_slug": "NYK",
                "opponent_team_slug": "MIA",
                "entry_state_index": 1,
                "exit_state_index": 2,
                "entry_at": datetime(2026, 2, 22, 20, 5, tzinfo=timezone.utc),
                "exit_at": datetime(2026, 2, 22, 20, 15, tzinfo=timezone.utc),
                "gross_return_with_slippage": 1.00,
            },
            {
                "game_id": "G3",
                "team_side": "away",
                "team_slug": "DEN",
                "opponent_team_slug": "UTA",
                "entry_state_index": 1,
                "exit_state_index": 2,
                "entry_at": datetime(2026, 2, 22, 20, 30, tzinfo=timezone.utc),
                "exit_at": datetime(2026, 2, 22, 20, 45, tzinfo=timezone.utc),
                "gross_return_with_slippage": -0.25,
            },
            {
                "game_id": "G4",
                "team_side": "away",
                "team_slug": "ATL",
                "opponent_team_slug": "CLE",
                "entry_state_index": 1,
                "exit_state_index": 2,
                "entry_at": datetime(2026, 2, 22, 21, 0, tzinfo=timezone.utc),
                "exit_at": datetime(2026, 2, 22, 21, 10, tzinfo=timezone.utc),
                "gross_return_with_slippage": 0.20,
            },
        ]
    )

    summary, steps_df = simulate_trade_portfolio(
        trades_df,
        sample_name="full_sample",
        strategy_family="inversion",
        initial_bankroll=10.0,
        position_size_fraction=1.0,
        game_limit=3,
    )

    assert summary["games_considered"] == 3
    assert summary["trade_count_considered"] == 3
    assert summary["executed_trade_count"] == 2
    assert summary["skipped_overlap_count"] == 1
    assert summary["skipped_bankroll_count"] == 0
    assert summary["ending_bankroll"] == 11.25
    assert summary["compounded_return"] == 0.125
    assert summary["max_drawdown_pct"] == 0.25
    assert list(steps_df["portfolio_action"]) == ["executed", "skipped", "executed"]
    assert list(steps_df["skip_reason"]) == [None, "overlap", None]


def test_combined_portfolio_lane_merges_keep_families_with_source_tracking() -> None:
    inversion_trades_df = pd.DataFrame(
        [
            {
                "game_id": "G1",
                "team_side": "home",
                "team_slug": "BOS",
                "opponent_team_slug": "LAL",
                "entry_state_index": 1,
                "exit_state_index": 2,
                "entry_at": datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc),
                "exit_at": datetime(2026, 2, 22, 20, 10, tzinfo=timezone.utc),
                "gross_return_with_slippage": 0.50,
            },
            {
                "game_id": "G3",
                "team_side": "home",
                "team_slug": "DEN",
                "opponent_team_slug": "UTA",
                "entry_state_index": 1,
                "exit_state_index": 2,
                "entry_at": datetime(2026, 2, 22, 20, 30, tzinfo=timezone.utc),
                "exit_at": datetime(2026, 2, 22, 20, 40, tzinfo=timezone.utc),
                "gross_return_with_slippage": 0.20,
            },
        ]
    )
    winner_definition_trades_df = pd.DataFrame(
        [
            {
                "game_id": "G2",
                "team_side": "away",
                "team_slug": "NYK",
                "opponent_team_slug": "MIA",
                "entry_state_index": 1,
                "exit_state_index": 2,
                "entry_at": datetime(2026, 2, 22, 20, 5, tzinfo=timezone.utc),
                "exit_at": datetime(2026, 2, 22, 20, 15, tzinfo=timezone.utc),
                "gross_return_with_slippage": 1.00,
            },
            {
                "game_id": "G4",
                "team_side": "away",
                "team_slug": "ATL",
                "opponent_team_slug": "CLE",
                "entry_state_index": 1,
                "exit_state_index": 2,
                "entry_at": datetime(2026, 2, 22, 20, 50, tzinfo=timezone.utc),
                "exit_at": datetime(2026, 2, 22, 21, 0, tzinfo=timezone.utc),
                "gross_return_with_slippage": -0.10,
            },
        ]
    )
    split_results = {
        "full_sample": BacktestResult(
            payload={},
            trade_frames={
                "inversion": inversion_trades_df,
                "winner_definition": winner_definition_trades_df,
            },
            state_df=pd.DataFrame(),
            strategy_registry={},
        )
    }

    summary_df, steps_df = build_combined_portfolio_benchmark_frames(
        split_results,
        strategy_families=("inversion", "winner_definition"),
        initial_bankroll=10.0,
        position_size_fraction=1.0,
        game_limit=4,
        split_order=("full_sample",),
    )

    assert len(summary_df) == 1
    summary = summary_df.iloc[0]
    assert summary["strategy_family"] == "combined_keep_families"
    assert summary["portfolio_scope"] == "combined_family_set"
    assert summary["strategy_family_members"] == "inversion,winner_definition"
    assert summary["strategy_family_count"] == 2
    assert summary["executed_trade_count"] == 3
    assert summary["skipped_overlap_count"] == 1
    assert summary["ending_bankroll"] == pytest.approx(16.2)
    assert list(steps_df["source_strategy_family"]) == [
        "inversion",
        "winner_definition",
        "inversion",
        "winner_definition",
    ]
    assert list(steps_df["portfolio_action"]) == ["executed", "skipped", "executed", "executed"]


def test_backtests_benchmarking_outputs_are_reproducible(tmp_path: Path) -> None:
    frame = _build_benchmark_state_frame()
    request = BacktestRunRequest(
        season="2025-26",
        season_phase="regular_season",
        strategy_family="all",
        slippage_cents=1,
        holdout_ratio=0.4,
        holdout_seed=7,
        robustness_seeds=(7, 11, 13),
        min_trade_count=1,
        portfolio_initial_bankroll=10.0,
        portfolio_position_size_fraction=1.0,
        portfolio_game_limit=100,
        output_root=str(tmp_path),
    )

    first = engine.build_benchmark_run_result(frame, request)
    second = engine.build_benchmark_run_result(frame, request)

    assert first.payload["benchmark"]["contract_version"] == "v4"
    assert first.payload["benchmark"]["time_validation_cutoff"] is not None
    assert set(first.split_results.keys()) == {"full_sample", "time_train", "time_validation", "random_train", "random_holdout"}
    assert first.split_results["random_holdout"].payload["games_considered"] > 0
    assert first.benchmark_frames["split_summary"].equals(second.benchmark_frames["split_summary"])
    assert first.benchmark_frames["portfolio_summary"].equals(second.benchmark_frames["portfolio_summary"])
    assert first.benchmark_frames["portfolio_robustness_detail"].equals(second.benchmark_frames["portfolio_robustness_detail"])
    assert first.benchmark_frames["portfolio_robustness_summary"].equals(second.benchmark_frames["portfolio_robustness_summary"])
    assert set(first.benchmark_frames["comparator_summary"]["comparator_name"]) == {
        "no_trade",
        "winner_prediction_hold_to_end",
    }
    assert set(first.benchmark_frames["candidate_freeze"]["candidate_label"]).issubset({"keep", "drop", "experimental"})
    assert set(first.benchmark_frames["portfolio_candidate_freeze"]["candidate_label"]).issubset({"keep", "drop", "experimental"})
    registry_families = tuple(first.payload["experiment"]["strategy_families"])
    keep_families = tuple(first.payload["benchmark"]["portfolio_keep_families"])
    robustness_detail_df = first.benchmark_frames["portfolio_robustness_detail"]
    robustness_summary_df = first.benchmark_frames["portfolio_robustness_summary"]
    assert set(robustness_detail_df["strategy_family"]) == set(registry_families)
    assert set(robustness_summary_df["strategy_family"]) == set(registry_families)
    assert set(robustness_detail_df["holdout_seed"]) == {7, 11, 13}
    assert len(robustness_detail_df) == len(registry_families) * 3
    assert set(robustness_summary_df["robustness_label"]).issubset(
        {"stable_positive", "stable_negative", "mixed", "not_run"}
    )
    combined_rows = first.benchmark_frames["portfolio_summary"][
        first.benchmark_frames["portfolio_summary"]["strategy_family"] == "combined_keep_families"
    ]
    if len(keep_families) >= 2:
        assert not combined_rows.empty
        assert set(combined_rows["strategy_family_members"]) == {",".join(keep_families)}
    else:
        assert combined_rows.empty

    payload = engine.write_benchmark_artifacts(first, tmp_path / "benchmark")
    assert Path(payload["artifacts"]["benchmark_split_summary_csv"]).exists()
    assert Path(payload["artifacts"]["benchmark_family_summary_csv"]).exists()
    assert Path(payload["artifacts"]["benchmark_comparator_summary_csv"]).exists()
    assert Path(payload["artifacts"]["benchmark_sample_vs_full_csv"]).exists()
    assert Path(payload["artifacts"]["benchmark_context_rankings_csv"]).exists()
    assert Path(payload["artifacts"]["benchmark_candidate_freeze_csv"]).exists()
    assert Path(payload["artifacts"]["benchmark_portfolio_summary_csv"]).exists()
    assert Path(payload["artifacts"]["benchmark_portfolio_steps_csv"]).exists()
    assert Path(payload["artifacts"]["benchmark_portfolio_candidate_freeze_csv"]).exists()
    assert Path(payload["artifacts"]["benchmark_portfolio_robustness_detail_csv"]).exists()
    assert Path(payload["artifacts"]["benchmark_portfolio_robustness_summary_csv"]).exists()
    assert Path(payload["artifacts"]["experiment_registry_json"]).exists()
