from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

import app.data.pipelines.daily.nba.analysis.reports as reports_mod


def _sample_profiles_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "game_id": "game-1",
                "team_side": "home",
                "team_slug": "BOS",
                "opponent_team_slug": "LAL",
                "game_date": date(2026, 2, 22),
                "game_start_time": "2026-02-22T20:00:00+00:00",
                "coverage_status": "covered_pre_and_ingame",
                "research_ready_flag": True,
                "final_winner_flag": True,
                "opening_price": 0.90,
                "closing_price": 0.95,
                "total_swing": 0.12,
                "inversion_count": 0,
                "notes_json": {"universe_classification": "research_ready"},
            },
            {
                "game_id": "game-1",
                "team_side": "away",
                "team_slug": "LAL",
                "opponent_team_slug": "BOS",
                "game_date": date(2026, 2, 22),
                "game_start_time": "2026-02-22T20:00:00+00:00",
                "coverage_status": "covered_pre_and_ingame",
                "research_ready_flag": True,
                "final_winner_flag": False,
                "opening_price": 0.10,
                "closing_price": 0.05,
                "total_swing": 0.12,
                "inversion_count": 0,
                "notes_json": {"universe_classification": "research_ready"},
            },
            {
                "game_id": "game-2",
                "team_side": "home",
                "team_slug": "NYK",
                "opponent_team_slug": "CHA",
                "game_date": date(2026, 2, 23),
                "game_start_time": "2026-02-23T20:00:00+00:00",
                "coverage_status": "covered_pre_and_ingame",
                "research_ready_flag": True,
                "final_winner_flag": False,
                "opening_price": 0.80,
                "closing_price": 0.40,
                "total_swing": 0.45,
                "inversion_count": 2,
                "notes_json": {"universe_classification": "research_ready"},
            },
            {
                "game_id": "game-2",
                "team_side": "away",
                "team_slug": "CHA",
                "opponent_team_slug": "NYK",
                "game_date": date(2026, 2, 23),
                "game_start_time": "2026-02-23T20:00:00+00:00",
                "coverage_status": "covered_pre_and_ingame",
                "research_ready_flag": True,
                "final_winner_flag": True,
                "opening_price": 0.20,
                "closing_price": 0.60,
                "total_swing": 0.45,
                "inversion_count": 2,
                "notes_json": {"universe_classification": "research_ready"},
            },
            {
                "game_id": "game-3",
                "team_side": "home",
                "team_slug": "PHI",
                "opponent_team_slug": "BKN",
                "game_date": date(2026, 2, 24),
                "game_start_time": "2026-02-24T20:00:00+00:00",
                "coverage_status": "no_history",
                "research_ready_flag": False,
                "final_winner_flag": True,
                "opening_price": 0.55,
                "closing_price": None,
                "total_swing": None,
                "inversion_count": None,
                "notes_json": {"universe_classification": "descriptive_only"},
            },
            {
                "game_id": "game-3",
                "team_side": "away",
                "team_slug": "BKN",
                "opponent_team_slug": "PHI",
                "game_date": date(2026, 2, 24),
                "game_start_time": "2026-02-24T20:00:00+00:00",
                "coverage_status": "no_history",
                "research_ready_flag": False,
                "final_winner_flag": False,
                "opening_price": 0.45,
                "closing_price": None,
                "total_swing": None,
                "inversion_count": None,
                "notes_json": {"universe_classification": "descriptive_only"},
            },
        ]
    )


def test_build_report_universe_summary_preserves_descriptive_visibility_pytest() -> None:
    summary = reports_mod.build_report_universe_summary(_sample_profiles_df())

    assert summary["games_total"] == 3
    assert summary["research_ready_games"] == 2
    assert summary["descriptive_only_games"] == 1
    assert summary["coverage_status_counts"]["no_history"] == 1
    assert summary["coverage_by_team"][0]["team_slug"] in {"BKN", "PHI"}
    assert summary["undercovered_dates"][0]["game_date"] == "2026-02-24"


def test_build_descriptive_report_payload_and_artifacts_pytest(tmp_path: Path) -> None:
    profiles_df = _sample_profiles_df()
    completeness_report = reports_mod.build_report_universe_summary(profiles_df)
    state_df = pd.DataFrame(
        [
            {
                "game_id": "game-1",
                "context_bucket": "Q4 | trailing_1_5",
                "large_swing_next_12_states_flag": 1.0,
                "crossed_50c_next_12_states_flag": 1.0,
            },
            {
                "game_id": "game-2",
                "context_bucket": "Q4 | trailing_1_5",
                "large_swing_next_12_states_flag": 1.0,
                "crossed_50c_next_12_states_flag": 0.0,
            },
            {
                "game_id": "game-2",
                "context_bucket": "Q1 | leading_6_10",
                "large_swing_next_12_states_flag": 0.0,
                "crossed_50c_next_12_states_flag": 0.0,
            },
        ]
    )
    team_profiles_df = pd.DataFrame(
        [
            {
                "team_slug": "BOS",
                "sample_games": 1,
                "avg_ingame_range": 0.10,
                "avg_total_swing": 0.12,
                "avg_inversion_count": 0.0,
                "inversion_rate": 0.0,
                "avg_favorite_drawdown": 0.03,
                "control_confidence_mismatch_rate": 0.05,
                "opening_price_trend_slope": 0.02,
            },
            {
                "team_slug": "LAL",
                "sample_games": 1,
                "avg_ingame_range": 0.11,
                "avg_total_swing": 0.12,
                "avg_inversion_count": 0.0,
                "inversion_rate": 0.0,
                "avg_favorite_drawdown": None,
                "control_confidence_mismatch_rate": 0.10,
                "opening_price_trend_slope": -0.01,
            },
            {
                "team_slug": "NYK",
                "sample_games": 1,
                "avg_ingame_range": 0.40,
                "avg_total_swing": 0.45,
                "avg_inversion_count": 2.0,
                "inversion_rate": 1.0,
                "avg_favorite_drawdown": 0.18,
                "control_confidence_mismatch_rate": 0.25,
                "opening_price_trend_slope": -0.20,
            },
            {
                "team_slug": "CHA",
                "sample_games": 1,
                "avg_ingame_range": 0.41,
                "avg_total_swing": 0.45,
                "avg_inversion_count": 2.0,
                "inversion_rate": 1.0,
                "avg_favorite_drawdown": None,
                "control_confidence_mismatch_rate": 0.22,
                "opening_price_trend_slope": 0.15,
            },
        ]
    )
    opening_band_df = pd.DataFrame(
        [
            {
                "opening_band": "10-20",
                "sample_games": 2,
                "avg_total_swing": 0.28,
                "avg_inversion_count": 1.0,
                "win_rate": 0.5,
            },
            {
                "opening_band": "80-90",
                "sample_games": 2,
                "avg_total_swing": 0.29,
                "avg_inversion_count": 1.0,
                "win_rate": 0.5,
            },
        ]
    )
    winner_definition_df = pd.DataFrame(
        [
            {"threshold_cents": 80, "sample_states": 20, "stable_states": 10, "reopen_rate": 0.20},
            {"threshold_cents": 90, "sample_states": 10, "stable_states": 4, "reopen_rate": 0.35},
        ]
    )

    payload = reports_mod.build_descriptive_report_payload(
        profiles_df=profiles_df,
        state_df=state_df,
        team_profiles_df=team_profiles_df,
        opening_band_df=opening_band_df,
        winner_definition_df=winner_definition_df,
        completeness_report=completeness_report,
        season="2025-26",
        season_phase="regular_season",
        analysis_version="v1_0_1",
    )

    assert payload["section_order"][0] == "teams_against_expectation"
    assert payload["teams_against_expectation"][0]["team_slug"] == "CHA"
    assert payload["highest_volatility_teams"][0]["team_slug"] == "CHA"
    assert [row["opening_band"] for row in payload["opening_band_swing_profiles"]] == ["10-20", "80-90"]
    assert payload["opening_bands_largest_swings"] == payload["opening_band_swing_profiles"]
    assert payload["high_reversion_contexts"][0]["context_bucket"] == "Q4 | trailing_1_5"
    assert payload["winner_definition_thresholds"][0]["threshold_cents"] == 80

    markdown = reports_mod.render_analysis_report_markdown(payload)
    assert "## Opening-Band Swing Profiles" in markdown
    assert "## Scoreboard-Control Mismatch Leaders" in markdown

    artifacts = reports_mod.write_descriptive_report_artifacts(tmp_path, payload)
    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["markdown"]).exists()
    assert Path(artifacts["sections"]["teams_against_expectation"]["csv"]).exists()
    assert Path(artifacts["sections"]["opening_band_swing_profiles"]["csv"]).exists()
    assert Path(artifacts["qa"]["coverage_by_team"]["csv"]).exists()
