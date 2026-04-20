from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path

import pandas as pd
import pytest

from app.data.pipelines.daily.nba.analysis.contracts import ANALYSIS_VERSION, ModelRunRequest
from app.data.pipelines.daily.nba.analysis.models import train_analysis_baselines
from app.data.pipelines.daily.nba.analysis.models.features import (
    ModelInputFrames,
    naive_classification_comparison,
    naive_regression_comparison,
    resolve_train_cutoff,
    split_frame_by_cutoff,
)
from app.data.pipelines.daily.nba.analysis.models.trade_quality import run_trade_quality_baseline
from app.data.pipelines.daily.nba.analysis.models.volatility import run_volatility_inversion_baseline


def _score_diff_bucket(score_diff: int) -> str:
    if score_diff <= -15:
        return "trail_15_plus"
    if score_diff <= -10:
        return "trail_10_14"
    if score_diff <= -5:
        return "trail_5_9"
    if score_diff <= -1:
        return "trail_1_4"
    if score_diff == 0:
        return "tied"
    if score_diff <= 4:
        return "lead_1_4"
    if score_diff <= 9:
        return "lead_5_9"
    if score_diff <= 14:
        return "lead_10_14"
    return "lead_15_plus"


def _stable_index(prices: list[float], threshold: float) -> int | None:
    running_min = float("inf")
    stable = None
    for index in range(len(prices) - 1, -1, -1):
        running_min = min(running_min, prices[index])
        if prices[index] >= threshold and running_min >= threshold:
            stable = index
    return stable


def _build_synthetic_mart_frames() -> ModelInputFrames:
    games = [
        ("002MODEL0001", pd.Timestamp("2026-02-22"), [0.72, 0.76, 0.78, 0.82, 0.85, 0.90], [0.28, 0.24, 0.22, 0.18, 0.15, 0.10]),
        ("002MODEL0002", pd.Timestamp("2026-02-25"), [0.44, 0.48, 0.51, 0.55, 0.86, 0.88], [0.56, 0.52, 0.49, 0.45, 0.14, 0.12]),
        ("002MODEL0003", pd.Timestamp("2026-02-28"), [0.63, 0.61, 0.60, 0.83, 0.82, 0.84], [0.37, 0.39, 0.40, 0.17, 0.18, 0.16]),
        ("002MODEL0004", pd.Timestamp("2026-03-03"), [0.31, 0.34, 0.37, 0.41, 0.81, 0.85], [0.69, 0.66, 0.63, 0.59, 0.19, 0.15]),
        ("002MODEL0005", pd.Timestamp("2026-03-06"), [0.68, 0.66, 0.72, 0.78, 0.83, 0.87], [0.32, 0.34, 0.28, 0.22, 0.17, 0.13]),
        ("002MODEL0006", pd.Timestamp("2026-03-09"), [0.46, 0.49, 0.53, 0.57, 0.84, 0.89], [0.54, 0.51, 0.47, 0.43, 0.16, 0.11]),
        ("002MODEL0007", pd.Timestamp("2026-03-12"), [0.55, 0.58, 0.62, 0.71, 0.79, 0.86], [0.45, 0.42, 0.38, 0.29, 0.21, 0.14]),
    ]

    profile_rows: list[dict[str, object]] = []
    state_rows: list[dict[str, object]] = []
    for game_id, game_date, home_prices, away_prices in games:
        for team_side, prices, final_winner_flag, team_id, team_slug, opponent_team_id, opponent_team_slug in (
            ("home", home_prices, True, 1610612738, "BOS", 1610612747, "LAL"),
            ("away", away_prices, False, 1610612747, "LAL", 1610612738, "BOS"),
        ):
            opening_price = prices[0]
            opening_band = f"{int(opening_price * 10) * 10}-{min(100, int(opening_price * 10) * 10 + 10)}"
            stable_70 = _stable_index(prices, 0.70)
            stable_80 = _stable_index(prices, 0.80)
            stable_90 = _stable_index(prices, 0.90)
            stable_95 = _stable_index(prices, 0.95)
            profile_rows.append(
                {
                    "game_id": game_id,
                    "team_side": team_side,
                    "team_id": team_id,
                    "team_slug": team_slug,
                    "opponent_team_id": opponent_team_id,
                    "opponent_team_slug": opponent_team_slug,
                    "event_id": f"event-{game_id}",
                    "market_id": f"market-{game_id}",
                    "outcome_id": f"outcome-{game_id}-{team_side}",
                    "season": "2025-26",
                    "season_phase": "regular_season",
                    "analysis_version": ANALYSIS_VERSION,
                    "computed_at": game_date + pd.Timedelta(hours=3),
                    "game_date": game_date.date(),
                    "game_start_time": game_date.to_pydatetime().replace(tzinfo=None),
                    "coverage_status": "covered_pre_and_ingame",
                    "research_ready_flag": True,
                    "price_path_reconciled_flag": True,
                    "final_winner_flag": final_winner_flag,
                    "opening_price": opening_price,
                    "closing_price": prices[-1],
                    "opening_band": opening_band,
                    "opening_band_rank": int(opening_price * 10),
                    "pregame_price_range": max(prices[:2]) - min(prices[:2]),
                    "ingame_price_range": max(prices[2:]) - min(prices[2:]),
                    "total_swing": max(prices) - min(prices),
                    "max_favorable_excursion": max(prices) - opening_price,
                    "max_adverse_excursion": opening_price - min(prices),
                    "inversion_count": 0,
                    "first_inversion_at": None,
                    "seconds_above_50c": 120.0,
                    "seconds_below_50c": 120.0,
                    "winner_stable_70_clock_elapsed_seconds": float(stable_70 * 60) if final_winner_flag and stable_70 is not None else None,
                    "winner_stable_80_clock_elapsed_seconds": float(stable_80 * 60) if final_winner_flag and stable_80 is not None else None,
                    "winner_stable_90_clock_elapsed_seconds": float(stable_90 * 60) if final_winner_flag and stable_90 is not None else None,
                    "winner_stable_95_clock_elapsed_seconds": float(stable_95 * 60) if final_winner_flag and stable_95 is not None else None,
                    "notes_json": None,
                }
            )

            score_path_home = [(0, 2), (3, 2), (5, 2), (5, 4), (6, 4)]
            score_path_away = [(away, home) for home, away in score_path_home]
            score_path = score_path_home if team_side == "home" else score_path_away
            stable_70 = _stable_index(prices, 0.70) if final_winner_flag else None
            stable_80 = _stable_index(prices, 0.80) if final_winner_flag else None
            stable_90 = _stable_index(prices, 0.90) if final_winner_flag else None
            stable_95 = _stable_index(prices, 0.95) if final_winner_flag else None
            for state_index in range(5):
                score_for, score_against = score_path[state_index]
                score_diff = score_for - score_against
                previous_for, previous_against = score_path[state_index - 1] if state_index > 0 else score_path[0]
                points_scored = max(0, score_for - previous_for)
                future_prices = prices[state_index + 1 :]
                state_rows.append(
                    {
                        "game_id": game_id,
                        "team_side": team_side,
                        "state_index": state_index,
                        "team_id": team_id,
                        "team_slug": team_slug,
                        "opponent_team_id": opponent_team_id,
                        "opponent_team_slug": opponent_team_slug,
                        "event_id": f"event-{game_id}",
                        "market_id": f"market-{game_id}",
                        "outcome_id": f"outcome-{game_id}-{team_side}",
                        "season": "2025-26",
                        "season_phase": "regular_season",
                        "analysis_version": ANALYSIS_VERSION,
                        "computed_at": game_date + pd.Timedelta(hours=3),
                        "game_date": game_date.date(),
                        "event_index": state_index + 1,
                        "event_at": game_date.to_pydatetime() + pd.Timedelta(minutes=state_index),
                        "period": 1 + min(state_index // 2, 3),
                        "period_label": f"Q{1 + min(state_index // 2, 3)}",
                        "clock_elapsed_seconds": float(state_index * 60),
                        "seconds_to_game_end": float((4 - state_index) * 60),
                        "score_for": score_for,
                        "score_against": score_against,
                        "score_diff": score_diff,
                        "score_diff_bucket": _score_diff_bucket(score_diff),
                        "context_bucket": f"Q{1 + min(state_index // 2, 3)}|{_score_diff_bucket(score_diff)}",
                        "team_led_flag": score_diff > 0,
                        "team_trailed_flag": score_diff < 0,
                        "tied_flag": score_diff == 0,
                        "market_favorite_flag": prices[state_index] >= 0.5,
                        "scoreboard_control_mismatch_flag": (score_diff > 0 and prices[state_index] < 0.5) or (score_diff < 0 and prices[state_index] >= 0.5),
                        "final_winner_flag": final_winner_flag,
                        "points_scored": points_scored,
                        "delta_for": points_scored,
                        "delta_against": max(0, score_against - previous_against),
                        "lead_changes_so_far": state_index,
                        "team_points_last_5_events": score_for,
                        "opponent_points_last_5_events": score_against,
                        "net_points_last_5_events": score_diff,
                        "opening_price": opening_price,
                        "opening_band": opening_band,
                        "team_price": prices[state_index],
                        "price_delta_from_open": prices[state_index] - opening_price,
                        "abs_price_delta_from_open": abs(prices[state_index] - opening_price),
                        "gap_before_seconds": 0.0,
                        "gap_after_seconds": 0.0,
                        "mfe_from_state": max(prices[state_index:]) - prices[state_index],
                        "mae_from_state": prices[state_index] - min(prices[state_index:]),
                        "large_swing_next_12_states_flag": any(abs(future - prices[state_index]) >= 0.10 for future in future_prices),
                        "crossed_50c_next_12_states_flag": any((future >= 0.5) != (prices[state_index] >= 0.5) for future in future_prices),
                        "winner_stable_70_after_state_flag": final_winner_flag and stable_70 is not None and state_index >= stable_70,
                        "winner_stable_80_after_state_flag": final_winner_flag and stable_80 is not None and state_index >= stable_80,
                        "winner_stable_90_after_state_flag": final_winner_flag and stable_90 is not None and state_index >= stable_90,
                        "winner_stable_95_after_state_flag": final_winner_flag and stable_95 is not None and state_index >= stable_95,
                    }
                )

    return ModelInputFrames(
        profiles_df=pd.DataFrame(profile_rows),
        state_df=pd.DataFrame(state_rows),
    )


def test_model_helpers_time_split_and_naive_comparison_pytest() -> None:
    frame = pd.DataFrame(
        {
            "game_id": ["g1", "g2", "g3", "g4"],
            "game_date": ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"],
            "value": [1, 2, 3, 4],
        }
    )
    cutoff = resolve_train_cutoff(frame["game_date"])
    train_df, validation_df = split_frame_by_cutoff(frame, cutoff=cutoff)
    assert str(cutoff.date()) == "2026-01-03"
    assert list(train_df["game_id"]) == ["g1", "g2", "g3"]
    assert list(validation_df["game_id"]) == ["g4"]

    classification = naive_classification_comparison(
        y_true=pd.Series([0.0, 1.0, 1.0]).to_numpy(dtype=float),
        y_pred=pd.Series([0.1, 0.9, 0.8]).to_numpy(dtype=float),
        base_rate=2 / 3,
    )
    assert classification["primary_metric"] == "brier"
    assert classification["better_than_naive"] is True

    regression = naive_regression_comparison(
        y_true=pd.Series([1.0, 2.0, 3.0]).to_numpy(dtype=float),
        y_pred=pd.Series([1.1, 2.0, 2.9]).to_numpy(dtype=float),
        train_mean=2.0,
    )
    assert regression["primary_metric"] == "rmse"
    assert regression["better_than_naive"] is True


def test_thin_data_returns_insufficient_data_pytest(tmp_path: Path) -> None:
    payload, artifacts = run_volatility_inversion_baseline(
        pd.DataFrame(
            {
                "game_date": ["2026-01-01"],
                "opening_price": [0.5],
                "team_price": [0.5],
                "abs_price_delta_from_open": [0.0],
                "abs_score_diff": [0],
                "score_for": [50],
                "score_against": [50],
                "score_diff": [0],
                "seconds_to_game_end": [100.0],
                "period": [1],
                "market_favorite_flag": [1],
                "team_led_flag": [0],
                "scoreboard_control_mismatch_flag": [0],
                "net_points_last_5_events": [0.0],
                "crossed_50c_next_12_states_flag": [1],
            }
        ),
        season="2025-26",
        season_phase="regular_season",
        analysis_version=ANALYSIS_VERSION,
        train_cutoff=None,
        output_dir=tmp_path,
    )
    assert payload["status"] == "insufficient_data"
    assert artifacts == {}


def test_train_analysis_baselines_end_to_end_pytest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frames = _build_synthetic_mart_frames()

    @contextmanager
    def _fake_connection():
        yield object()

    monkeypatch.setattr("app.data.pipelines.daily.nba.analysis.models.managed_connection", _fake_connection)
    monkeypatch.setattr(
        "app.data.pipelines.daily.nba.analysis.models.load_model_input_frames",
        lambda *args, **kwargs: frames,
    )

    payload = train_analysis_baselines(
        ModelRunRequest(
            season="2025-26",
            season_phase="regular_season",
            target_family="all",
            output_root=str(tmp_path),
        )
    )
    assert payload["train_cutoff"] is not None
    assert Path(payload["artifacts"]["json"]).exists()
    assert Path(payload["artifacts"]["markdown"]).exists()
    assert payload["tracks"]["volatility_inversion"]["status"] == "success"
    assert payload["tracks"]["volatility_inversion"]["naive_comparison"]["primary_metric"] == "brier"
    assert payload["tracks"]["trade_window_quality"]["status"] == "success"
    assert payload["tracks"]["trade_window_quality"]["targets"]["mfe_from_state"]["naive_comparison"]["primary_metric"] == "rmse"
    assert payload["tracks"]["winner_definition_timing"]["status"] == "success"
    assert payload["tracks"]["winner_definition_timing"]["naive_comparison"]["primary_metric"] == "rmse"
    markdown_text = Path(payload["artifacts"]["markdown"]).read_text(encoding="utf-8")
    assert "mfe_from_state rmse" in markdown_text
    assert "mae_from_state rmse" in markdown_text
    json_payload = json.loads(Path(payload["artifacts"]["json"]).read_text(encoding="utf-8"))
    assert json_payload["artifacts"]["json"] == payload["artifacts"]["json"]
    assert "tracks" in json_payload["artifacts"]

    track_artifacts = payload["artifacts"]["tracks"]
    assert Path(track_artifacts["volatility_inversion"]["coefficients_csv"]).exists()
    assert Path(track_artifacts["volatility_inversion"]["validation_csv"]).exists()
    assert Path(track_artifacts["trade_window_quality"]["mfe_from_state_coefficients_csv"]).exists()
    assert Path(track_artifacts["trade_window_quality"]["mfe_from_state_validation_csv"]).exists()
    assert Path(track_artifacts["winner_definition_timing"]["hazard_table_csv"]).exists()
    assert Path(track_artifacts["winner_definition_timing"]["validation_csv"]).exists()
