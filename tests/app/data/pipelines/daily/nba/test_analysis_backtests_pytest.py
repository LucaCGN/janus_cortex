from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from app.data.pipelines.daily.nba.analysis.backtests import engine
from app.data.pipelines.daily.nba.analysis.backtests.benchmarking import (
    BACKTEST_BENCHMARK_CONTRACT_VERSION,
    _normalize_robustness_seeds,
)
from app.data.pipelines.daily.nba.analysis.backtests.llm_experiment import (
    _build_game_candidates,
    _build_llm_prompt_payload,
    _build_context_lookup,
    _clean_trades_df,
    build_team_profile_context_lookup,
    _safe_float,
    _serialise_scalar,
    _LLM_MODEL_CACHED_INPUT_PRICE_PER_1M,
    _LLM_MODEL_INPUT_PRICE_PER_1M,
    _LLM_MODEL_OUTPUT_PRICE_PER_1M,
    build_llm_iteration_plan,
    estimate_llm_usage_cost,
    normalize_llm_selected_candidate_ids,
)
from app.data.pipelines.daily.nba.analysis.backtests.master_router import (
    build_master_router_selection_priors,
    build_master_router_trade_frame,
)
from app.data.pipelines.daily.nba.analysis.backtests.portfolio import (
    _normalize_family_members,
    build_combined_portfolio_benchmark_frames,
    build_master_router_portfolio_benchmark_frames,
    simulate_trade_portfolio,
    build_routed_portfolio_benchmark_frames,
)
from app.data.pipelines.daily.nba.analysis.backtests.specs import BacktestResult
from app.data.pipelines.daily.nba.analysis.backtests.unified_router import (
    resolve_unified_router_game_selection,
)
from app.data.pipelines.daily.nba.analysis.contracts import (
    ANALYSIS_VERSION,
    BacktestRunRequest,
    DEFAULT_BACKTEST_ROBUSTNESS_SEEDS,
)


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
    seconds_to_game_end: float | None = None,
    lead_changes_so_far: int = 0,
) -> dict[str, object]:
    resolved_score_for = score_for if score_for is not None else 10 + state_index
    resolved_score_against = score_against if score_against is not None else 8 + state_index
    resolved_score_diff = score_diff if score_diff is not None else resolved_score_for - resolved_score_against
    resolved_net_points = net_points_last_5_events if net_points_last_5_events is not None else 2 * state_index
    resolved_seconds_to_game_end = seconds_to_game_end if seconds_to_game_end is not None else float(720 - (state_index * 60))
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
        "seconds_to_game_end": resolved_seconds_to_game_end,
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
        "lead_changes_so_far": lead_changes_so_far,
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
        _build_state_row(
            game_id="002A5LIFT",
            team_slug="ATL",
            opponent_team_slug="BOS",
            opening_price=0.30,
            state_index=0,
            team_price=0.30,
            event_at=base + timedelta(minutes=50),
            opening_band="30-40",
            period=1,
            period_label="Q1",
            score_for=18,
            score_against=18,
            score_diff=0,
            score_diff_bucket="tied",
            context_bucket="Q1|tied",
            net_points_last_5_events=0,
            seconds_to_game_end=1800.0,
        ),
        _build_state_row(
            game_id="002A5LIFT",
            team_slug="ATL",
            opponent_team_slug="BOS",
            opening_price=0.30,
            state_index=1,
            team_price=0.34,
            event_at=base + timedelta(minutes=51),
            opening_band="30-40",
            period=2,
            period_label="Q2",
            score_for=22,
            score_against=26,
            score_diff=-4,
            score_diff_bucket="trail_1_4",
            context_bucket="Q2|trail_1_4",
            net_points_last_5_events=1,
            seconds_to_game_end=1740.0,
        ),
        _build_state_row(
            game_id="002A5LIFT",
            team_slug="ATL",
            opponent_team_slug="BOS",
            opening_price=0.30,
            state_index=2,
            team_price=0.39,
            event_at=base + timedelta(minutes=52),
            opening_band="30-40",
            period=2,
            period_label="Q2",
            score_for=27,
            score_against=29,
            score_diff=-2,
            score_diff_bucket="trail_1_4",
            context_bucket="Q2|trail_1_4",
            net_points_last_5_events=3,
            seconds_to_game_end=1680.0,
        ),
        _build_state_row(
            game_id="002A5LIFT",
            team_slug="ATL",
            opponent_team_slug="BOS",
            opening_price=0.30,
            state_index=3,
            team_price=0.50,
            event_at=base + timedelta(minutes=53),
            opening_band="30-40",
            period=2,
            period_label="Q2",
            score_for=31,
            score_against=31,
            score_diff=0,
            score_diff_bucket="tied",
            context_bucket="Q2|tied",
            net_points_last_5_events=6,
            seconds_to_game_end=1620.0,
        ),
        _build_state_row(
            game_id="002A5Q1RUN",
            team_slug="DAL",
            opponent_team_slug="HOU",
            opening_price=0.46,
            state_index=0,
            team_price=0.46,
            event_at=base + timedelta(minutes=60),
            opening_band="40-50",
            period=1,
            period_label="Q1",
            score_for=10,
            score_against=10,
            score_diff=0,
            score_diff_bucket="tied",
            context_bucket="Q1|tied",
            net_points_last_5_events=0,
        ),
        _build_state_row(
            game_id="002A5Q1RUN",
            team_slug="DAL",
            opponent_team_slug="HOU",
            opening_price=0.46,
            state_index=1,
            team_price=0.52,
            event_at=base + timedelta(minutes=61),
            opening_band="40-50",
            period=1,
            period_label="Q1",
            score_for=14,
            score_against=13,
            score_diff=1,
            score_diff_bucket="lead_1_4",
            context_bucket="Q1|lead_1_4",
            net_points_last_5_events=2,
        ),
        _build_state_row(
            game_id="002A5Q1RUN",
            team_slug="DAL",
            opponent_team_slug="HOU",
            opening_price=0.46,
            state_index=2,
            team_price=0.56,
            event_at=base + timedelta(minutes=62),
            opening_band="40-50",
            period=1,
            period_label="Q1",
            score_for=18,
            score_against=14,
            score_diff=4,
            score_diff_bucket="lead_1_4",
            context_bucket="Q1|lead_1_4",
            net_points_last_5_events=5,
        ),
        _build_state_row(
            game_id="002A5Q1RUN",
            team_slug="DAL",
            opponent_team_slug="HOU",
            opening_price=0.46,
            state_index=3,
            team_price=0.64,
            event_at=base + timedelta(minutes=63),
            opening_band="40-50",
            period=1,
            period_label="Q1",
            score_for=22,
            score_against=16,
            score_diff=6,
            score_diff_bucket="lead_5_9",
            context_bucket="Q1|lead_5_9",
            net_points_last_5_events=7,
        ),
        _build_state_row(
            game_id="002A5CLUTCH",
            team_slug="IND",
            opponent_team_slug="MIL",
            opening_price=0.48,
            state_index=0,
            team_price=0.49,
            event_at=base + timedelta(minutes=70),
            opening_band="40-50",
            period=4,
            period_label="Q4",
            score_for=92,
            score_against=92,
            score_diff=0,
            score_diff_bucket="tied",
            context_bucket="Q4|tied",
            net_points_last_5_events=1,
            seconds_to_game_end=300.0,
            lead_changes_so_far=2,
        ),
        _build_state_row(
            game_id="002A5CLUTCH",
            team_slug="IND",
            opponent_team_slug="MIL",
            opening_price=0.48,
            state_index=1,
            team_price=0.54,
            event_at=base + timedelta(minutes=71),
            opening_band="40-50",
            period=4,
            period_label="Q4",
            score_for=96,
            score_against=95,
            score_diff=1,
            score_diff_bucket="lead_1_4",
            context_bucket="Q4|lead_1_4",
            net_points_last_5_events=2,
            seconds_to_game_end=260.0,
            lead_changes_so_far=3,
        ),
        _build_state_row(
            game_id="002A5CLUTCH",
            team_slug="IND",
            opponent_team_slug="MIL",
            opening_price=0.48,
            state_index=2,
            team_price=0.58,
            event_at=base + timedelta(minutes=72),
            opening_band="40-50",
            period=4,
            period_label="Q4",
            score_for=100,
            score_against=98,
            score_diff=2,
            score_diff_bucket="lead_1_4",
            context_bucket="Q4|lead_1_4",
            net_points_last_5_events=4,
            seconds_to_game_end=220.0,
            lead_changes_so_far=4,
        ),
        _build_state_row(
            game_id="002A5CLUTCH",
            team_slug="IND",
            opponent_team_slug="MIL",
            opening_price=0.48,
            state_index=3,
            team_price=0.73,
            event_at=base + timedelta(minutes=73),
            opening_band="40-50",
            period=4,
            period_label="Q4",
            score_for=106,
            score_against=101,
            score_diff=5,
            score_diff_bucket="lead_5_9",
            context_bucket="Q4|lead_5_9",
            net_points_last_5_events=6,
            seconds_to_game_end=140.0,
            lead_changes_so_far=4,
        ),
        _build_state_row(
            game_id="002A5PANIC",
            team_slug="BOS",
            opponent_team_slug="CHI",
            opening_price=0.69,
            state_index=0,
            team_price=0.69,
            event_at=base + timedelta(minutes=80),
            opening_band="60-70",
            period=1,
            period_label="Q1",
            score_for=18,
            score_against=16,
            score_diff=2,
            score_diff_bucket="lead_1_4",
            context_bucket="Q1|lead_1_4",
            net_points_last_5_events=1,
            seconds_to_game_end=2400.0,
        ),
        _build_state_row(
            game_id="002A5PANIC",
            team_slug="BOS",
            opponent_team_slug="CHI",
            opening_price=0.69,
            state_index=1,
            team_price=0.48,
            event_at=base + timedelta(minutes=81),
            opening_band="60-70",
            period=2,
            period_label="Q2",
            score_for=32,
            score_against=34,
            score_diff=-2,
            score_diff_bucket="trail_1_4",
            context_bucket="Q2|trail_1_4",
            net_points_last_5_events=-3,
            seconds_to_game_end=1680.0,
        ),
        _build_state_row(
            game_id="002A5PANIC",
            team_slug="BOS",
            opponent_team_slug="CHI",
            opening_price=0.69,
            state_index=2,
            team_price=0.53,
            event_at=base + timedelta(minutes=82),
            opening_band="60-70",
            period=3,
            period_label="Q3",
            score_for=48,
            score_against=50,
            score_diff=-2,
            score_diff_bucket="trail_1_4",
            context_bucket="Q3|trail_1_4",
            net_points_last_5_events=3,
            seconds_to_game_end=1320.0,
        ),
        _build_state_row(
            game_id="002A5PANIC",
            team_slug="BOS",
            opponent_team_slug="CHI",
            opening_price=0.69,
            state_index=3,
            team_price=0.64,
            event_at=base + timedelta(minutes=83),
            opening_band="60-70",
            period=3,
            period_label="Q3",
            score_for=56,
            score_against=54,
            score_diff=2,
            score_diff_bucket="lead_1_4",
            context_bucket="Q3|lead_1_4",
            net_points_last_5_events=6,
            seconds_to_game_end=1260.0,
        ),
        _build_state_row(
            game_id="002A5HALF",
            team_slug="PHI",
            opponent_team_slug="TOR",
            opening_price=0.44,
            state_index=0,
            team_price=0.44,
            event_at=base + timedelta(minutes=90),
            opening_band="40-50",
            period=2,
            period_label="Q2",
            score_for=42,
            score_against=42,
            score_diff=0,
            score_diff_bucket="tied",
            context_bucket="Q2|tied",
            net_points_last_5_events=0,
            seconds_to_game_end=1500.0,
        ),
        _build_state_row(
            game_id="002A5HALF",
            team_slug="PHI",
            opponent_team_slug="TOR",
            opening_price=0.44,
            state_index=1,
            team_price=0.54,
            event_at=base + timedelta(minutes=91),
            opening_band="40-50",
            period=3,
            period_label="Q3",
            score_for=48,
            score_against=47,
            score_diff=1,
            score_diff_bucket="lead_1_4",
            context_bucket="Q3|lead_1_4",
            net_points_last_5_events=2,
            seconds_to_game_end=1380.0,
        ),
        _build_state_row(
            game_id="002A5HALF",
            team_slug="PHI",
            opponent_team_slug="TOR",
            opening_price=0.44,
            state_index=2,
            team_price=0.57,
            event_at=base + timedelta(minutes=92),
            opening_band="40-50",
            period=3,
            period_label="Q3",
            score_for=54,
            score_against=51,
            score_diff=3,
            score_diff_bucket="lead_1_4",
            context_bucket="Q3|lead_1_4",
            net_points_last_5_events=4,
            seconds_to_game_end=1320.0,
        ),
        _build_state_row(
            game_id="002A5HALF",
            team_slug="PHI",
            opponent_team_slug="TOR",
            opening_price=0.44,
            state_index=3,
            team_price=0.66,
            event_at=base + timedelta(minutes=93),
            opening_band="40-50",
            period=3,
            period_label="Q3",
            score_for=60,
            score_against=54,
            score_diff=6,
            score_diff_bucket="lead_5_9",
            context_bucket="Q3|lead_5_9",
            net_points_last_5_events=6,
            seconds_to_game_end=1200.0,
        ),
        _build_state_row(
            game_id="002A5CBK2",
            team_slug="BKN",
            opponent_team_slug="MIA",
            opening_price=0.32,
            state_index=0,
            team_price=0.20,
            event_at=base + timedelta(minutes=100),
            opening_band="30-40",
            period=2,
            period_label="Q2",
            score_for=38,
            score_against=48,
            score_diff=-10,
            score_diff_bucket="trail_10_14",
            context_bucket="Q2|trail_10_14",
            net_points_last_5_events=1,
            seconds_to_game_end=1620.0,
        ),
        _build_state_row(
            game_id="002A5CBK2",
            team_slug="BKN",
            opponent_team_slug="MIA",
            opening_price=0.32,
            state_index=1,
            team_price=0.22,
            event_at=base + timedelta(minutes=101),
            opening_band="30-40",
            period=3,
            period_label="Q3",
            score_for=46,
            score_against=56,
            score_diff=-10,
            score_diff_bucket="trail_10_14",
            context_bucket="Q3|trail_10_14",
            net_points_last_5_events=5,
            seconds_to_game_end=1320.0,
        ),
        _build_state_row(
            game_id="002A5CBK2",
            team_slug="BKN",
            opponent_team_slug="MIA",
            opening_price=0.32,
            state_index=2,
            team_price=0.27,
            event_at=base + timedelta(minutes=102),
            opening_band="30-40",
            period=3,
            period_label="Q3",
            score_for=52,
            score_against=59,
            score_diff=-7,
            score_diff_bucket="trail_5_9",
            context_bucket="Q3|trail_5_9",
            net_points_last_5_events=7,
            seconds_to_game_end=1260.0,
        ),
        _build_state_row(
            game_id="002A5CBK2",
            team_slug="BKN",
            opponent_team_slug="MIA",
            opening_price=0.32,
            state_index=3,
            team_price=0.38,
            event_at=base + timedelta(minutes=103),
            opening_band="30-40",
            period=3,
            period_label="Q3",
            score_for=58,
            score_against=62,
            score_diff=-4,
            score_diff_bucket="trail_1_4",
            context_bucket="Q3|trail_1_4",
            net_points_last_5_events=9,
            seconds_to_game_end=1200.0,
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
        "002A5LIFT": datetime(2026, 3, 9, 20, 0, tzinfo=timezone.utc),
        "002A5Q1RUN": datetime(2026, 3, 12, 20, 0, tzinfo=timezone.utc),
        "002A5CLUTCH": datetime(2026, 3, 15, 20, 0, tzinfo=timezone.utc),
        "002A5PANIC": datetime(2026, 3, 18, 20, 0, tzinfo=timezone.utc),
        "002A5HALF": datetime(2026, 3, 21, 20, 0, tzinfo=timezone.utc),
        "002A5CBK2": datetime(2026, 3, 24, 20, 0, tzinfo=timezone.utc),
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
    assert result.payload["games_considered"] == 11
    assert set(result.payload["registry"].keys()) == {
        "inversion",
        "winner_definition",
        "underdog_liftoff",
        "q1_repricing",
        "q4_clutch",
    }
    assert result.payload["families"]["inversion"]["trade_count"] == 4
    assert result.payload["families"]["winner_definition"]["trade_count"] == 1
    assert result.payload["families"]["underdog_liftoff"]["trade_count"] == 1
    assert result.payload["families"]["q1_repricing"]["trade_count"] == 2
    assert result.payload["families"]["q4_clutch"]["trade_count"] == 1

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
    assert Path(payload["artifacts"]["inversion_csv"]).exists()
    assert Path(payload["artifacts"]["winner_definition_csv"]).exists()
    assert Path(payload["artifacts"]["underdog_liftoff_csv"]).exists()
    assert Path(payload["artifacts"]["q1_repricing_csv"]).exists()
    assert Path(payload["artifacts"]["q4_clutch_csv"]).exists()
    assert Path(payload["artifacts"]["inversion_best_trades_csv"]).exists()
    assert Path(payload["artifacts"]["inversion_worst_trades_csv"]).exists()
    assert Path(payload["artifacts"]["inversion_context_summary_csv"]).exists()
    assert Path(payload["artifacts"]["inversion_trade_traces_json"]).exists()


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

    for family in (
        "inversion",
        "winner_definition",
        "underdog_liftoff",
        "q1_repricing",
        "q4_clutch",
    ):
        zero_df = zero.trade_frames[family].sort_values(["game_id", "entry_state_index"]).reset_index(drop=True)
        one_df = one.trade_frames[family].sort_values(["game_id", "entry_state_index"]).reset_index(drop=True)
        assert len(zero_df) == len(one_df) == int(zero.payload["families"][family]["trade_count"])
        assert len(zero_df) >= 1
        assert (one_df["gross_return_with_slippage"] <= zero_df["gross_return_with_slippage"]).all()
        assert (zero_df["gross_return_with_slippage"] > one_df["gross_return_with_slippage"]).all()


def test_trade_portfolio_respects_concurrency_game_limit_and_compounding() -> None:
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
                "entry_price": 0.40,
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
                "entry_price": 0.35,
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
                "entry_price": 0.25,
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
                "entry_price": 0.10,
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
        min_order_dollars=1.0,
        min_shares=5.0,
        max_concurrent_positions=1,
        concurrency_mode="shared_cash_equal_split",
        random_slippage_max_cents=0,
        random_slippage_seed=20260422,
    )

    assert summary["games_considered"] == 3
    assert summary["trade_count_considered"] == 3
    assert summary["executed_trade_count"] == 2
    assert summary["skipped_overlap_count"] == 0
    assert summary["skipped_bankroll_count"] == 0
    assert summary["skipped_concurrency_count"] == 1
    assert summary["skipped_min_order_count"] == 0
    assert summary["ending_bankroll"] == 11.25
    assert summary["compounded_return"] == 0.125
    assert summary["max_drawdown_pct"] == 0.25
    assert list(steps_df["portfolio_action"]) == ["executed", "skipped", "executed"]
    assert list(steps_df["skip_reason"]) == [None, "concurrency", None]


def test_trade_portfolio_respects_polymarket_minimum_order_floor() -> None:
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
                "entry_price": 0.30,
                "gross_return_with_slippage": 0.50,
            }
        ]
    )

    summary, steps_df = simulate_trade_portfolio(
        trades_df,
        sample_name="full_sample",
        strategy_family="winner_definition",
        initial_bankroll=1.20,
        position_size_fraction=1.0,
        game_limit=1,
        min_order_dollars=1.0,
        min_shares=5.0,
        max_concurrent_positions=1,
        concurrency_mode="shared_cash_equal_split",
    )

    assert summary["executed_trade_count"] == 0
    assert summary["skipped_min_order_count"] == 1
    assert summary["ending_bankroll"] == pytest.approx(1.2)
    assert list(steps_df["skip_reason"]) == ["min_order"]
    assert float(steps_df.iloc[0]["minimum_required_stake"]) == pytest.approx(1.5)


def test_trade_portfolio_risk_controls_throttle_after_runup_and_stop_on_drawdown() -> None:
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
                "exit_at": datetime(2026, 2, 22, 20, 5, tzinfo=timezone.utc),
                "entry_price": 0.40,
                "gross_return_with_slippage": 2.0,
            },
            {
                "game_id": "G2",
                "team_side": "home",
                "team_slug": "DEN",
                "opponent_team_slug": "UTA",
                "entry_state_index": 1,
                "exit_state_index": 2,
                "entry_at": datetime(2026, 2, 22, 20, 6, tzinfo=timezone.utc),
                "exit_at": datetime(2026, 2, 22, 20, 6, 30, tzinfo=timezone.utc),
                "entry_price": 0.40,
                "gross_return_with_slippage": -0.50,
            },
            {
                "game_id": "G3",
                "team_side": "home",
                "team_slug": "MIL",
                "opponent_team_slug": "NYK",
                "entry_state_index": 1,
                "exit_state_index": 2,
                "entry_at": datetime(2026, 2, 22, 20, 7, tzinfo=timezone.utc),
                "exit_at": datetime(2026, 2, 22, 20, 12, tzinfo=timezone.utc),
                "entry_price": 0.40,
                "gross_return_with_slippage": 0.20,
            },
        ]
    )

    summary, steps_df = simulate_trade_portfolio(
        trades_df,
        sample_name="full_sample",
        strategy_family="master_strategy_router_same_side_top1_conf60_v1",
        initial_bankroll=10.0,
        position_size_fraction=1.0,
        game_limit=3,
        min_order_dollars=1.0,
        min_shares=5.0,
        max_concurrent_positions=1,
        concurrency_mode="shared_cash_equal_split",
        runup_throttle_peak_multiple=2.0,
        runup_throttle_fraction_scale=0.50,
        drawdown_new_entry_stop_pct=0.20,
        random_slippage_max_cents=0,
        random_slippage_seed=20260422,
    )

    assert summary["executed_trade_count"] == 2
    assert summary["skipped_risk_guard_count"] == 1
    assert list(steps_df["portfolio_action"]) == ["executed", "executed", "skipped"]
    assert list(steps_df["skip_reason"]) == [None, None, "risk_guard"]
    assert float(steps_df.iloc[1]["effective_position_fraction"]) == pytest.approx(0.5)
    assert bool(steps_df.iloc[1]["runup_throttle_active"]) is True
    assert bool(steps_df.iloc[2]["risk_guard_active"]) is True
    assert float(steps_df.iloc[2]["drawdown_pct_before_batch"]) == pytest.approx(0.25)


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
        min_order_dollars=1.0,
        min_shares=5.0,
        max_concurrent_positions=1,
        concurrency_mode="shared_cash_equal_split",
        random_slippage_max_cents=0,
        random_slippage_seed=20260422,
        split_order=("full_sample",),
    )

    assert len(summary_df) == 1
    summary = summary_df.iloc[0]
    assert summary["strategy_family"] == "combined_keep_families"
    assert summary["portfolio_scope"] == "combined_family_set"
    assert summary["strategy_family_members"] == "inversion,winner_definition"
    assert summary["strategy_family_count"] == 2
    assert summary["executed_trade_count"] == 3
    assert summary["skipped_concurrency_count"] == 1
    assert summary["ending_bankroll"] == pytest.approx(16.2)
    assert list(steps_df["source_strategy_family"]) == [
        "inversion",
        "winner_definition",
        "inversion",
        "winner_definition",
    ]
    assert list(steps_df["portfolio_action"]) == ["executed", "skipped", "executed", "executed"]


def test_routed_portfolio_lane_selects_family_by_opening_band() -> None:
    inversion_trades_df = pd.DataFrame(
        [
            {
                "game_id": "G1",
                "team_side": "home",
                "team_slug": "UTA",
                "opponent_team_slug": "DEN",
                "opening_band": "40-50",
                "entry_state_index": 1,
                "exit_state_index": 2,
                "entry_at": datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc),
                "exit_at": datetime(2026, 2, 22, 20, 10, tzinfo=timezone.utc),
                "gross_return_with_slippage": 0.50,
            }
        ]
    )
    winner_definition_trades_df = pd.DataFrame(
        [
            {
                "game_id": "G2",
                "team_side": "away",
                "team_slug": "NYK",
                "opponent_team_slug": "MIA",
                "opening_band": "70-80",
                "entry_state_index": 1,
                "exit_state_index": 2,
                "entry_at": datetime(2026, 2, 22, 20, 20, tzinfo=timezone.utc),
                "exit_at": datetime(2026, 2, 22, 20, 30, tzinfo=timezone.utc),
                "gross_return_with_slippage": 0.20,
            }
        ]
    )
    underdog_liftoff_trades_df = pd.DataFrame(
        [
            {
                "game_id": "G3",
                "team_side": "home",
                "team_slug": "ATL",
                "opponent_team_slug": "BOS",
                "opening_band": "20-30",
                "entry_state_index": 1,
                "exit_state_index": 2,
                "entry_at": datetime(2026, 2, 22, 20, 40, tzinfo=timezone.utc),
                "exit_at": datetime(2026, 2, 22, 20, 50, tzinfo=timezone.utc),
                "gross_return_with_slippage": 0.30,
            }
        ]
    )
    split_results = {
        "full_sample": BacktestResult(
            payload={},
            trade_frames={
                "inversion": inversion_trades_df,
                "winner_definition": winner_definition_trades_df,
                "underdog_liftoff": underdog_liftoff_trades_df,
            },
            state_df=pd.DataFrame(),
            strategy_registry={},
        )
    }

    summary_df, steps_df = build_routed_portfolio_benchmark_frames(
        split_results,
        opening_band_route_map={
            "20-30": "underdog_liftoff",
            "40-50": "inversion",
            "70-80": "winner_definition",
        },
        strategy_families=("inversion", "winner_definition", "underdog_liftoff"),
        initial_bankroll=10.0,
        position_size_fraction=1.0,
        game_limit=3,
        min_order_dollars=1.0,
        min_shares=5.0,
        max_concurrent_positions=1,
        concurrency_mode="shared_cash_equal_split",
        random_slippage_max_cents=0,
        random_slippage_seed=20260422,
        split_order=("full_sample",),
    )

    assert len(summary_df) == 1
    summary = summary_df.iloc[0]
    assert summary["strategy_family"] == "statistical_routing_v1"
    assert summary["portfolio_scope"] == "routed_family_set"
    assert summary["executed_trade_count"] == 3
    assert summary["ending_bankroll"] == pytest.approx(23.4)
    assert list(steps_df["source_strategy_family"]) == [
        "inversion",
        "winner_definition",
        "underdog_liftoff",
    ]


def test_master_router_lane_selects_highest_confidence_core_family_and_keeps_extra_sleeves() -> None:
    time_train_trade_frames = {
        "winner_definition": pd.DataFrame(
            [
                {
                    "game_id": "T-WIN",
                    "team_side": "home",
                    "team_slug": "OKC",
                    "opponent_team_slug": "HOU",
                    "opening_band": "60-70",
                    "period_label": "Q4",
                    "context_bucket": "Q4|lead_5_9",
                    "signal_strength": 4.0,
                    "entry_price": 0.80,
                    "entry_state_index": 1,
                    "exit_state_index": 2,
                    "entry_at": datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc),
                    "exit_at": datetime(2026, 2, 22, 20, 10, tzinfo=timezone.utc),
                    "gross_return_with_slippage": 0.10,
                }
            ]
        ),
        "inversion": pd.DataFrame(
            [
                {
                    "game_id": "T-INV",
                    "team_side": "away",
                    "team_slug": "UTA",
                    "opponent_team_slug": "DEN",
                    "opening_band": "40-50",
                    "period_label": "Q2",
                    "context_bucket": "Q2|lead_1_4",
                    "signal_strength": 6.0,
                    "entry_price": 0.50,
                    "entry_state_index": 1,
                    "exit_state_index": 2,
                    "entry_at": datetime(2026, 2, 22, 20, 20, tzinfo=timezone.utc),
                    "exit_at": datetime(2026, 2, 22, 20, 30, tzinfo=timezone.utc),
                    "gross_return_with_slippage": 0.20,
                }
            ]
        ),
        "underdog_liftoff": pd.DataFrame(
            [
                {
                    "game_id": "T-LIFT",
                    "team_side": "home",
                    "team_slug": "ATL",
                    "opponent_team_slug": "BOS",
                    "opening_band": "20-30",
                    "period_label": "Q2",
                    "context_bucket": "Q2|lead_1_4",
                    "signal_strength": 3.0,
                    "entry_price": 0.36,
                    "entry_state_index": 1,
                    "exit_state_index": 2,
                    "entry_at": datetime(2026, 2, 22, 20, 40, tzinfo=timezone.utc),
                    "exit_at": datetime(2026, 2, 22, 20, 50, tzinfo=timezone.utc),
                    "gross_return_with_slippage": 0.12,
                }
            ]
        ),
        "q1_repricing": pd.DataFrame(
            [
                {
                    "game_id": "G1",
                    "team_side": "home",
                    "team_slug": "DAL",
                    "opponent_team_slug": "HOU",
                    "opening_band": "40-50",
                    "period_label": "Q1",
                    "context_bucket": "Q1|lead_1_4",
                    "signal_strength": 5.0,
                    "entry_price": 0.55,
                    "entry_state_index": 1,
                    "exit_state_index": 2,
                    "entry_at": datetime(2026, 2, 22, 19, 50, tzinfo=timezone.utc),
                    "exit_at": datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc),
                    "gross_return_with_slippage": 0.08,
                }
            ]
        ),
    }
    full_sample_trade_frames = {
        "winner_definition": pd.DataFrame(
            [
                {
                    "game_id": "G1",
                    "team_side": "away",
                    "team_slug": "NYK",
                    "opponent_team_slug": "MIA",
                    "opening_band": "60-70",
                    "period_label": "Q4",
                    "context_bucket": "Q4|lead_5_9",
                    "signal_strength": 4.0,
                    "entry_price": 0.80,
                    "entry_state_index": 1,
                    "exit_state_index": 2,
                    "entry_at": datetime(2026, 2, 22, 20, 30, tzinfo=timezone.utc),
                    "exit_at": datetime(2026, 2, 22, 20, 40, tzinfo=timezone.utc),
                    "gross_return_with_slippage": 0.09,
                }
            ]
        ),
        "inversion": pd.DataFrame(
            [
                {
                    "game_id": "G1",
                    "team_side": "home",
                    "team_slug": "UTA",
                    "opponent_team_slug": "DEN",
                    "opening_band": "40-50",
                    "period_label": "Q2",
                    "context_bucket": "Q2|lead_1_4",
                    "signal_strength": 5.0,
                    "entry_price": 0.50,
                    "entry_state_index": 1,
                    "exit_state_index": 2,
                    "entry_at": datetime(2026, 2, 22, 20, 15, tzinfo=timezone.utc),
                    "exit_at": datetime(2026, 2, 22, 20, 25, tzinfo=timezone.utc),
                    "gross_return_with_slippage": 0.18,
                }
            ]
        ),
        "underdog_liftoff": pd.DataFrame(
            [
                {
                    "game_id": "G1",
                    "team_side": "home",
                    "team_slug": "ATL",
                    "opponent_team_slug": "BOS",
                    "opening_band": "20-30",
                    "period_label": "Q2",
                    "context_bucket": "Q2|lead_1_4",
                    "signal_strength": 3.0,
                    "entry_price": 0.36,
                    "entry_state_index": 1,
                    "exit_state_index": 2,
                    "entry_at": datetime(2026, 2, 22, 20, 10, tzinfo=timezone.utc),
                    "exit_at": datetime(2026, 2, 22, 20, 20, tzinfo=timezone.utc),
                    "gross_return_with_slippage": 0.11,
                }
            ]
        ),
        "q1_repricing": pd.DataFrame(
            [
                {
                    "game_id": "G1",
                    "team_side": "home",
                    "team_slug": "DAL",
                    "opponent_team_slug": "HOU",
                    "opening_band": "40-50",
                    "period_label": "Q1",
                    "context_bucket": "Q1|lead_1_4",
                    "signal_strength": 5.0,
                    "entry_price": 0.55,
                    "entry_state_index": 1,
                    "exit_state_index": 2,
                    "entry_at": datetime(2026, 2, 22, 19, 50, tzinfo=timezone.utc),
                    "exit_at": datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc),
                    "gross_return_with_slippage": 0.08,
                }
            ]
        ),
    }
    split_results = {
        "time_train": BacktestResult(
            payload={},
            trade_frames=time_train_trade_frames,
            state_df=pd.DataFrame(),
            strategy_registry={},
        ),
        "full_sample": BacktestResult(
            payload={},
            trade_frames=full_sample_trade_frames,
            state_df=pd.DataFrame(),
            strategy_registry={},
        ),
    }

    summary_df, steps_df, decisions_df = build_master_router_portfolio_benchmark_frames(
        split_results,
        initial_bankroll=10.0,
        position_size_fraction=1.0,
        game_limit=3,
        min_order_dollars=1.0,
        min_shares=5.0,
        max_concurrent_positions=2,
        concurrency_mode="shared_cash_equal_split",
        random_slippage_max_cents=0,
        random_slippage_seed=20260422,
        split_order=("full_sample",),
        selection_sample_name="time_train",
        core_strategy_families=("winner_definition", "inversion", "underdog_liftoff"),
        extra_strategy_families=("q1_repricing",),
    )

    assert len(summary_df) == 1
    summary = summary_df.iloc[0]
    assert summary["strategy_family"] == "master_strategy_router_v1"
    assert summary["portfolio_scope"] == "routed_family_set"
    assert summary["strategy_family_members"] == "winner_definition,inversion,underdog_liftoff,q1_repricing"
    assert summary["executed_trade_count"] == 2
    assert list(steps_df["source_strategy_family"]) == [
        "q1_repricing",
        "underdog_liftoff",
    ]
    assert list(steps_df["portfolio_action"]) == ["executed", "executed"]
    assert len(decisions_df) == 1
    decision = decisions_df.iloc[0]
    assert decision["selected_core_family"] == "underdog_liftoff"
    assert decision["triggered_core_family_count"] == 3
    assert decision["triggered_extra_family_count"] == 1


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
        portfolio_min_order_dollars=1.0,
        portfolio_min_shares=5.0,
        portfolio_max_concurrent_positions=3,
        portfolio_concurrency_mode="shared_cash_equal_split",
        output_root=str(tmp_path),
    )

    first = engine.build_benchmark_run_result(frame, request)
    second = engine.build_benchmark_run_result(frame, request)

    assert first.payload["benchmark"]["contract_version"] == BACKTEST_BENCHMARK_CONTRACT_VERSION
    assert first.payload["benchmark"]["time_validation_cutoff"] is not None
    assert set(first.split_results.keys()) == {"full_sample", "time_train", "time_validation", "random_train", "random_holdout"}
    assert first.split_results["random_holdout"].payload["games_considered"] > 0
    assert first.benchmark_frames["split_summary"].equals(second.benchmark_frames["split_summary"])
    assert first.benchmark_frames["portfolio_summary"].equals(second.benchmark_frames["portfolio_summary"])
    assert first.benchmark_frames["portfolio_daily_paths"].equals(second.benchmark_frames["portfolio_daily_paths"])
    assert first.benchmark_frames["portfolio_robustness_detail"].equals(second.benchmark_frames["portfolio_robustness_detail"])
    assert first.benchmark_frames["portfolio_robustness_summary"].equals(second.benchmark_frames["portfolio_robustness_summary"])
    assert set(first.benchmark_frames["comparator_summary"]["comparator_name"]) == {
        "no_trade",
        "winner_prediction_hold_to_end",
    }
    assert set(first.benchmark_frames["candidate_freeze"]["candidate_label"]).issubset({"keep", "drop", "experimental"})
    assert set(first.benchmark_frames["portfolio_candidate_freeze"]["candidate_label"]).issubset({"keep", "drop", "experimental"})
    assert set(first.benchmark_frames["route_summary"]["selected_family"]).issubset(set(first.payload["experiment"]["strategy_families"]))
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
    assert "mean_ending_bankroll" in robustness_summary_df.columns
    assert "mean_compounded_return" in robustness_summary_df.columns
    assert not first.benchmark_frames["game_strategy_classification"].empty
    assert not first.benchmark_frames["master_router_decisions"].empty
    assert not first.benchmark_frames["portfolio_daily_paths"].empty
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
    assert Path(payload["artifacts"]["benchmark_route_summary_csv"]).exists()
    assert Path(payload["artifacts"]["benchmark_game_strategy_classification_csv"]).exists()
    assert Path(payload["artifacts"]["benchmark_master_router_decisions_csv"]).exists()
    assert Path(payload["artifacts"]["benchmark_portfolio_summary_csv"]).exists()
    assert Path(payload["artifacts"]["benchmark_portfolio_steps_csv"]).exists()
    assert Path(payload["artifacts"]["benchmark_portfolio_daily_paths_csv"]).exists()
    assert Path(payload["artifacts"]["benchmark_portfolio_candidate_freeze_csv"]).exists()
    assert Path(payload["artifacts"]["benchmark_portfolio_robustness_detail_csv"]).exists()
    assert Path(payload["artifacts"]["benchmark_portfolio_robustness_summary_csv"]).exists()
    assert Path(payload["artifacts"]["experiment_registry_json"]).exists()
    assert any(key.startswith("benchmark_portfolio_chart_") for key in payload["artifacts"])


def test_llm_experiment_scalar_and_cost_helper_contracts() -> None:
    assert _safe_float(None) is None
    assert _safe_float("") is None
    assert _safe_float("bad") is None
    assert _safe_float("2.5") == pytest.approx(2.5)
    assert _safe_float(3) == pytest.approx(3.0)

    ts = pd.Timestamp("2026-04-21T12:34:56Z")
    assert _serialise_scalar(ts) == "2026-04-21T12:34:56+00:00"
    assert _serialise_scalar(7) == 7
    assert _serialise_scalar(float("nan")) is None

    estimated_cost = (
        (1_200 - 100) * _LLM_MODEL_INPUT_PRICE_PER_1M
        + 100 * _LLM_MODEL_CACHED_INPUT_PRICE_PER_1M
        + 250 * _LLM_MODEL_OUTPUT_PRICE_PER_1M
    ) / 1_000_000
    assert estimated_cost == pytest.approx(0.0019575)


def test_llm_experiment_trade_frame_and_context_lookup_are_deterministic() -> None:
    frame = pd.DataFrame(
        [
            {
                "game_id": "g1",
                "opening_band": "40-50",
                "period_label": "Q1",
                "context_bucket": "Q1|trail_1_4",
                "gross_return_with_slippage": -0.10,
                "entry_at": "2026-04-21T12:00:00Z",
                "exit_at": "2026-04-21T12:05:00Z",
                "signal_strength": "0.4",
                "entry_price": "0.42",
                "exit_price": "0.38",
                "entry_state_index": "2",
            },
            {
                "game_id": "g2",
                "opening_band": "40-50",
                "period_label": "Q1",
                "context_bucket": "Q1|trail_1_4",
                "gross_return_with_slippage": 0.20,
                "entry_at": "2026-04-21T12:02:00Z",
                "exit_at": "2026-04-21T12:06:00Z",
                "signal_strength": "0.7",
                "entry_price": "0.43",
                "exit_price": "0.51",
                "entry_state_index": "1",
            },
            {
                "game_id": "g3",
                "opening_band": "70-80",
                "period_label": "Q2",
                "context_bucket": "Q2|lead_1_4",
                "gross_return_with_slippage": 0.05,
                "entry_at": "2026-04-21T12:10:00Z",
                "exit_at": "2026-04-21T12:18:00Z",
                "signal_strength": "0.5",
                "entry_price": "0.78",
                "exit_price": "0.81",
                "entry_state_index": "3",
            },
        ]
    )

    cleaned = _clean_trades_df(frame)
    assert cleaned["entry_at"].dtype.kind == "M"
    assert cleaned["exit_at"].dtype.kind == "M"
    assert cleaned["signal_strength"].dtype.kind in {"f", "i"}
    assert cleaned["entry_price"].dtype.kind in {"f", "i"}
    assert cleaned["exit_price"].dtype.kind in {"f", "i"}
    assert cleaned["entry_state_index"].dtype.kind in {"f", "i"}

    exact_lookup, band_period_lookup, overall, context_rows = _build_context_lookup(cleaned)
    assert exact_lookup[("40-50", "Q1", "Q1|trail_1_4")]["trade_count"] == 2
    assert band_period_lookup[("40-50", "Q1")]["trade_count"] == 2
    assert overall["trade_count"] == 3
    assert overall["win_rate"] == pytest.approx(2 / 3)
    assert [row["context_bucket"] for row in context_rows] == ["Q1|trail_1_4", "Q2|lead_1_4"]
    assert context_rows[0]["avg_return"] == pytest.approx(0.05)


def test_llm_prompt_payload_can_include_postseason_historical_team_context() -> None:
    state_df = pd.DataFrame(
        [
            _build_state_row(
                game_id="GCTX",
                team_slug="BOS",
                opponent_team_slug="NYK",
                opening_price=0.62,
                state_index=1,
                team_price=0.66,
                event_at=datetime(2026, 4, 21, 20, 0, tzinfo=timezone.utc),
                opening_band="60-70",
                period_label="Q4",
                context_bucket="Q4|lead_5_9",
                score_diff=7,
                net_points_last_5_events=4.0,
                seconds_to_game_end=180.0,
                lead_changes_so_far=3,
            )
        ]
    )
    trade_frames = {
        "winner_definition": pd.DataFrame(
            [
                {
                    "game_id": "GCTX",
                    "team_side": "home",
                    "team_slug": "BOS",
                    "opponent_team_slug": "NYK",
                    "opening_band": "60-70",
                    "period_label": "Q4",
                    "context_bucket": "Q4|lead_5_9",
                    "entry_state_index": 1,
                    "entry_at": datetime(2026, 4, 21, 20, 0, tzinfo=timezone.utc),
                    "entry_price": 0.66,
                    "signal_strength": 5.0,
                    "entry_metadata_json": "{\"entry_threshold\": 0.8}",
                }
            ]
        )
    }
    result = BacktestResult(payload={}, trade_frames=trade_frames, state_df=state_df, strategy_registry={})
    family_profiles = {
        "winner_definition": {
            "candidate_role": "core",
            "trade_count": 20,
            "win_rate": 0.65,
            "avg_return": 0.08,
            "tags": ["favorite", "late"],
        }
    }
    priors = {
        "winner_definition": {
            "overall": {"trade_count": 20, "win_rate": 0.65, "avg_return": 0.08},
            "exact_context_lookup": {("60-70", "Q4", "Q4|lead_5_9"): {"trade_count": 8, "win_rate": 0.75, "avg_return": 0.11}},
            "band_period_lookup": {},
        }
    }
    team_profiles_df = pd.DataFrame(
        [
            {
                "team_slug": "BOS",
                "sample_games": 82,
                "wins": 56,
                "favorite_games": 54,
                "underdog_games": 28,
                "avg_opening_price": 0.63,
                "avg_total_swing": 0.21,
                "avg_max_favorable_excursion": 0.17,
                "avg_max_adverse_excursion": 0.09,
                "inversion_rate": 0.18,
                "avg_favorite_drawdown": 0.11,
                "avg_underdog_spike": 0.07,
                "control_confidence_mismatch_rate": 0.08,
                "winner_stable_80_rate": 0.44,
                "winner_stable_90_rate": 0.27,
                "opening_price_trend_slope": 0.01,
                "rolling_10_json": "{\"latest\":{\"window_sample_games\":10,\"avg_total_swing\":0.24,\"avg_inversion_count\":0.8,\"avg_opening_price\":0.65}}",
                "rolling_20_json": "{\"latest\":{\"window_sample_games\":20,\"avg_total_swing\":0.22,\"avg_inversion_count\":0.6,\"avg_opening_price\":0.64}}",
            },
            {
                "team_slug": "NYK",
                "sample_games": 82,
                "wins": 48,
                "favorite_games": 41,
                "underdog_games": 41,
                "avg_opening_price": 0.54,
                "avg_total_swing": 0.18,
                "avg_max_favorable_excursion": 0.14,
                "avg_max_adverse_excursion": 0.10,
                "inversion_rate": 0.12,
                "avg_favorite_drawdown": 0.09,
                "avg_underdog_spike": 0.05,
                "control_confidence_mismatch_rate": 0.05,
                "winner_stable_80_rate": 0.31,
                "winner_stable_90_rate": 0.18,
                "opening_price_trend_slope": -0.02,
                "rolling_10_json": "{\"latest\":{\"window_sample_games\":10,\"avg_total_swing\":0.17,\"avg_inversion_count\":0.4,\"avg_opening_price\":0.53}}",
                "rolling_20_json": "{\"latest\":{\"window_sample_games\":20,\"avg_total_swing\":0.19,\"avg_inversion_count\":0.5,\"avg_opening_price\":0.55}}",
            },
        ]
    )
    team_lookup = build_team_profile_context_lookup(team_profiles_df)
    game_candidates = _build_game_candidates(
        result,
        family_profiles=family_profiles,
        priors=priors,
        core_strategy_families=("winner_definition",),
        extra_strategy_families=(),
        historical_team_context_lookup=team_lookup,
    )
    payload = _build_llm_prompt_payload(
        lane_name="llm_hybrid_freedom_compact_v1",
        lane_mode="llm_freedom",
        llm_component_scope="bc_freedom",
        prompt_profile="compact",
        use_confidence_gate=False,
        game_id="GCTX",
        opening_band="60-70",
        fallback_ids=[game_candidates["GCTX"][0]["candidate_id"]],
        available_candidates=game_candidates["GCTX"],
        family_profiles=family_profiles,
    )

    assert payload["historical_game_context"]["team"]["tm"] == "BOS"
    assert payload["historical_game_context"]["opp"]["tm"] == "NYK"
    assert payload["historical_game_context"]["delta"]["stable80"] == pytest.approx(0.13)


def test_llm_experiment_iteration_seed_and_family_normalization() -> None:
    assert _normalize_robustness_seeds(None, fallback_seed=19) == DEFAULT_BACKTEST_ROBUSTNESS_SEEDS
    assert _normalize_robustness_seeds("13, 7, 7, bad, 11", fallback_seed=19) == (13, 7, 11)
    assert _normalize_robustness_seeds([], fallback_seed=19) == (19,)

    assert _normalize_family_members("llm_hybrid_restrained_v1", ["  b_only  ", "c_only", "b_only", ""]) == (
        "b_only",
        "c_only",
    )
    assert _normalize_family_members("llm_hybrid_freedom_v1", None) == ("llm_hybrid_freedom_v1",)


def test_llm_experiment_public_cost_and_sampling_helpers() -> None:
    assert estimate_llm_usage_cost(input_tokens=1_200, cached_input_tokens=100, output_tokens=250) == pytest.approx(0.0019575)

    state_df = _build_benchmark_state_frame()
    trade_frames = {
        "winner_definition": pd.DataFrame(
            [
                {
                    "game_id": "002A5REV",
                    "entry_at": datetime(2026, 2, 22, 20, 5, tzinfo=timezone.utc),
                    "exit_at": datetime(2026, 2, 22, 21, 0, tzinfo=timezone.utc),
                },
                {
                    "game_id": "002A5WIN",
                    "entry_at": datetime(2026, 2, 23, 20, 5, tzinfo=timezone.utc),
                    "exit_at": datetime(2026, 2, 23, 21, 0, tzinfo=timezone.utc),
                },
                {
                    "game_id": "002A5DOG",
                    "entry_at": datetime(2026, 2, 24, 20, 5, tzinfo=timezone.utc),
                    "exit_at": datetime(2026, 2, 24, 21, 0, tzinfo=timezone.utc),
                },
            ]
        )
    }
    result = BacktestResult(payload={}, trade_frames=trade_frames, state_df=state_df, strategy_registry={})
    plan = build_llm_iteration_plan(
        result,
        strategy_families=("winner_definition",),
        iteration_count=2,
        games_per_iteration=2,
        seeds=(7, 11),
        fallback_seed=19,
    )
    assert len(plan) == 2
    assert plan[0]["iteration_seed"] == 7
    assert plan[0]["sample_game_count"] == 2
    assert len(plan[0]["sampled_game_ids"]) == 2
    rerun = build_llm_iteration_plan(
        result,
        strategy_families=("winner_definition",),
        iteration_count=2,
        games_per_iteration=2,
        seeds=(7, 11),
        fallback_seed=19,
    )
    assert plan == rerun
    assert set(plan[0]["sampled_game_ids"]).issubset({"002A5REV", "002A5WIN", "002A5DOG"})


def test_llm_selected_candidate_normalization_respects_role_and_side_rules() -> None:
    candidates = [
        {"candidate_id": "core-home-1", "candidate_role": "core", "team_side": "home"},
        {"candidate_id": "extra-home-1", "candidate_role": "extra", "team_side": "home"},
        {"candidate_id": "extra-away-1", "candidate_role": "extra", "team_side": "away"},
        {"candidate_id": "core-home-2", "candidate_role": "core", "team_side": "home"},
    ]
    restrained = normalize_llm_selected_candidate_ids(
        ["core-home-2", "extra-away-1", "extra-home-1", "core-home-1"],
        candidates,
        lane_mode="llm_restrained",
        allowed_roles=("core", "extra"),
    )
    assert restrained == ["core-home-2", "extra-home-1"]

    freedom = normalize_llm_selected_candidate_ids(
        ["core-home-2", "extra-away-1", "extra-home-1", "core-home-1"],
        candidates,
        lane_mode="llm_freedom",
        allowed_roles=("core", "extra"),
    )
    assert freedom == ["core-home-2", "extra-home-1", "core-home-1"]

    capped = normalize_llm_selected_candidate_ids(
        ["core-home-2", "extra-home-1", "core-home-1"],
        candidates,
        lane_mode="llm_freedom",
        allowed_roles=("core", "extra"),
        max_selected_candidates=2,
        max_core_candidates=1,
        max_extra_candidates=1,
        require_core_for_extra=True,
    )
    assert capped == ["core-home-2", "extra-home-1"]


def test_master_router_trade_frame_can_guard_extras_and_skip_low_confidence_cores() -> None:
    selection_result = BacktestResult(
        payload={},
        trade_frames={
            "winner_definition": pd.DataFrame(
                [
                    {
                        "game_id": "G1",
                        "team_side": "home",
                        "team_slug": "OKC",
                        "opponent_team_slug": "HOU",
                        "opening_band": "60-70",
                        "period_label": "Q4",
                        "context_bucket": "Q4|lead_5_9",
                        "signal_strength": 6.0,
                        "entry_price": 0.80,
                        "entry_state_index": 1,
                        "exit_state_index": 2,
                        "entry_at": datetime(2026, 2, 22, 20, 10, tzinfo=timezone.utc),
                        "exit_at": datetime(2026, 2, 22, 20, 20, tzinfo=timezone.utc),
                        "gross_return_with_slippage": 0.12,
                    }
                ]
            ),
            "inversion": pd.DataFrame(
                [
                    {
                        "game_id": "G2",
                        "team_side": "away",
                        "team_slug": "DEN",
                        "opponent_team_slug": "UTA",
                        "opening_band": "40-50",
                        "period_label": "Q2",
                        "context_bucket": "Q2|lead_1_4",
                        "signal_strength": 4.0,
                        "entry_price": 0.52,
                        "entry_state_index": 1,
                        "exit_state_index": 2,
                        "entry_at": datetime(2026, 2, 22, 20, 30, tzinfo=timezone.utc),
                        "exit_at": datetime(2026, 2, 22, 20, 40, tzinfo=timezone.utc),
                        "gross_return_with_slippage": 0.18,
                    }
                ]
            ),
            "underdog_liftoff": pd.DataFrame(columns=engine.BACKTEST_TRADE_COLUMNS),
        },
        state_df=pd.DataFrame(),
        strategy_registry={},
    )
    sample_result = BacktestResult(
        payload={},
        trade_frames={
            "winner_definition": pd.DataFrame(
                [
                    {
                        "game_id": "G1",
                        "team_side": "home",
                        "team_slug": "OKC",
                        "opponent_team_slug": "HOU",
                        "opening_band": "60-70",
                        "period_label": "Q4",
                        "context_bucket": "Q4|lead_5_9",
                        "signal_strength": 7.0,
                        "entry_price": 0.82,
                        "entry_state_index": 1,
                        "exit_state_index": 2,
                        "entry_at": datetime(2026, 2, 22, 20, 50, tzinfo=timezone.utc),
                        "exit_at": datetime(2026, 2, 22, 21, 0, tzinfo=timezone.utc),
                        "gross_return_with_slippage": 0.08,
                    }
                ]
            ),
            "inversion": pd.DataFrame(
                [
                    {
                        "game_id": "G2",
                        "team_side": "away",
                        "team_slug": "DEN",
                        "opponent_team_slug": "UTA",
                        "opening_band": "40-50",
                        "period_label": "Q2",
                        "context_bucket": "Q2|lead_1_4",
                        "signal_strength": 1.0,
                        "entry_price": 0.48,
                        "entry_state_index": 1,
                        "exit_state_index": 2,
                        "entry_at": datetime(2026, 2, 22, 21, 10, tzinfo=timezone.utc),
                        "exit_at": datetime(2026, 2, 22, 21, 20, tzinfo=timezone.utc),
                        "gross_return_with_slippage": 0.05,
                    }
                ]
            ),
            "underdog_liftoff": pd.DataFrame(columns=engine.BACKTEST_TRADE_COLUMNS),
            "q1_repricing": pd.DataFrame(
                [
                    {
                        "game_id": "G1",
                        "team_side": "home",
                        "team_slug": "OKC",
                        "opponent_team_slug": "HOU",
                        "opening_band": "60-70",
                        "period_label": "Q1",
                        "context_bucket": "Q1|lead_1_4",
                        "signal_strength": 5.0,
                        "entry_price": 0.55,
                        "entry_state_index": 1,
                        "exit_state_index": 2,
                        "entry_at": datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc),
                        "exit_at": datetime(2026, 2, 22, 20, 5, tzinfo=timezone.utc),
                        "gross_return_with_slippage": 0.09,
                    },
                    {
                        "game_id": "G1",
                        "team_side": "away",
                        "team_slug": "HOU",
                        "opponent_team_slug": "OKC",
                        "opening_band": "60-70",
                        "period_label": "Q1",
                        "context_bucket": "Q1|lead_1_4",
                        "signal_strength": 6.0,
                        "entry_price": 0.45,
                        "entry_state_index": 1,
                        "exit_state_index": 2,
                        "entry_at": datetime(2026, 2, 22, 20, 1, tzinfo=timezone.utc),
                        "exit_at": datetime(2026, 2, 22, 20, 6, tzinfo=timezone.utc),
                        "gross_return_with_slippage": -0.04,
                    },
                ]
            ),
            "q4_clutch": pd.DataFrame(columns=engine.BACKTEST_TRADE_COLUMNS),
        },
        state_df=pd.DataFrame(),
        strategy_registry={},
    )
    priors = build_master_router_selection_priors(
        selection_result,
        core_strategy_families=("winner_definition", "inversion", "underdog_liftoff"),
    )

    trade_frame, decisions_df = build_master_router_trade_frame(
        sample_result,
        sample_name="sample",
        selection_sample_name="selection",
        priors=priors,
        core_strategy_families=("winner_definition", "inversion", "underdog_liftoff"),
        extra_strategy_families=("q1_repricing", "q4_clutch"),
        extra_selection_mode="same_side",
        min_selected_core_confidence=0.55,
        min_core_confidence_for_extras=0.55,
    )

    assert list(trade_frame["source_strategy_family"]) == ["winner_definition", "q1_repricing"]
    assert list(trade_frame["team_side"]) == ["home", "home"]
    assert decisions_df.loc[decisions_df["game_id"] == "G2", "selected_core_family"].iloc[0] is None


def test_unified_router_selection_prefers_default_then_llm_then_skip() -> None:
    strong_default = resolve_unified_router_game_selection(
        deterministic_decision={"selected_core_family": "winner_definition", "selected_confidence": 0.74},
        llm_decision={"selected_candidate_count": 1, "llm_confidence": 0.91, "decision_status": "ok"},
        weak_confidence_threshold=0.60,
        llm_accept_confidence=0.60,
    )
    assert strong_default["final_source"] == "deterministic_default"
    assert strong_default["default_is_weak_flag"] is False

    llm_override = resolve_unified_router_game_selection(
        deterministic_decision={"selected_core_family": "winner_definition", "selected_confidence": 0.42},
        llm_decision={"selected_candidate_count": 1, "llm_confidence": 0.71, "decision_status": "ok"},
        weak_confidence_threshold=0.60,
        llm_accept_confidence=0.60,
    )
    assert llm_override["final_source"] == "llm_override"
    assert llm_override["default_is_weak_flag"] is True

    weak_skip = resolve_unified_router_game_selection(
        deterministic_decision={"selected_core_family": "winner_definition", "selected_confidence": 0.42},
        llm_decision={"selected_candidate_count": 0, "llm_confidence": 0.0, "decision_status": "ok"},
        weak_confidence_threshold=0.60,
        llm_accept_confidence=0.60,
        skip_weak_when_llm_empty=True,
    )
    assert weak_skip["final_source"] == "skip_weak_game"
    assert weak_skip["final_selection_reason"] == "weak_default_llm_skip"
