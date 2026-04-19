from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests import engine
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
    period_label: str = "Q1",
    score_diff_bucket: str = "lead_1_4",
    context_bucket: str = "Q1|lead_1_4",
) -> dict[str, object]:
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
        "period": 1,
        "period_label": period_label,
        "clock": "PT11M00.00S",
        "clock_elapsed_seconds": float(state_index * 60),
        "seconds_to_game_end": float(720 - (state_index * 60)),
        "score_for": 10 + state_index,
        "score_against": 8 + state_index,
        "score_diff": 2,
        "score_diff_bucket": score_diff_bucket,
        "context_bucket": context_bucket,
        "team_led_flag": True,
        "team_trailed_flag": False,
        "tied_flag": False,
        "market_favorite_flag": opening_price >= 0.5,
        "scoreboard_control_mismatch_flag": False,
        "final_winner_flag": True,
        "scoring_side": "home",
        "points_scored": 2,
        "delta_for": 2,
        "delta_against": 0,
        "lead_changes_so_far": 0,
        "team_points_last_5_events": 2 * state_index,
        "opponent_points_last_5_events": 0,
        "net_points_last_5_events": 2 * state_index,
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
    ]
    return pd.DataFrame(rows)


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
    assert result.payload["games_considered"] == 3
    assert result.payload["families"]["reversion"]["trade_count"] == 1
    assert result.payload["families"]["inversion"]["trade_count"] == 1
    assert result.payload["families"]["winner_definition"]["trade_count"] == 1

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
    assert Path(payload["artifacts"]["reversion_csv"]).exists()
    assert Path(payload["artifacts"]["inversion_csv"]).exists()
    assert Path(payload["artifacts"]["winner_definition_csv"]).exists()


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

    for family in ("reversion", "inversion", "winner_definition"):
        zero_df = zero.trade_frames[family].sort_values(["game_id", "entry_state_index"]).reset_index(drop=True)
        one_df = one.trade_frames[family].sort_values(["game_id", "entry_state_index"]).reset_index(drop=True)
        assert len(zero_df) == len(one_df) == 1
        assert (one_df["gross_return_with_slippage"] <= zero_df["gross_return_with_slippage"]).all()
        assert float(zero_df.iloc[0]["gross_return_with_slippage"]) > float(one_df.iloc[0]["gross_return_with_slippage"])
