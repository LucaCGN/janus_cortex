from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.data.pipelines.daily.nba.analysis.player_impact import (
    build_player_impact_shadow_result,
    render_player_impact_shadow_markdown,
    write_player_impact_shadow_artifacts,
)


def _build_state_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "game_id": "002TESTA7",
                "team_side": "home",
                "state_index": 0,
                "team_id": 1610612738,
                "team_slug": "BOS",
                "opponent_team_id": 1610612747,
                "opponent_team_slug": "LAL",
                "season": "2025-26",
                "season_phase": "regular_season",
                "analysis_version": "v1_0_1",
                "game_date": datetime(2026, 2, 22, tzinfo=timezone.utc).date(),
                "event_index": 1,
                "event_at": datetime(2026, 2, 22, 20, 1, tzinfo=timezone.utc),
                "score_diff": 2,
                "opening_price": 0.60,
                "team_price": 0.64,
                "price_delta_from_open": 0.04,
                "large_swing_next_12_states_flag": False,
                "crossed_50c_next_12_states_flag": False,
            },
            {
                "game_id": "002TESTA7",
                "team_side": "away",
                "state_index": 0,
                "team_id": 1610612747,
                "team_slug": "LAL",
                "opponent_team_id": 1610612738,
                "opponent_team_slug": "BOS",
                "season": "2025-26",
                "season_phase": "regular_season",
                "analysis_version": "v1_0_1",
                "game_date": datetime(2026, 2, 22, tzinfo=timezone.utc).date(),
                "event_index": 1,
                "event_at": datetime(2026, 2, 22, 20, 1, tzinfo=timezone.utc),
                "score_diff": -2,
                "opening_price": 0.40,
                "team_price": 0.36,
                "price_delta_from_open": -0.04,
                "large_swing_next_12_states_flag": False,
                "crossed_50c_next_12_states_flag": False,
            },
            {
                "game_id": "002TESTA7",
                "team_side": "home",
                "state_index": 1,
                "team_id": 1610612738,
                "team_slug": "BOS",
                "opponent_team_id": 1610612747,
                "opponent_team_slug": "LAL",
                "season": "2025-26",
                "season_phase": "regular_season",
                "analysis_version": "v1_0_1",
                "game_date": datetime(2026, 2, 22, tzinfo=timezone.utc).date(),
                "event_index": 2,
                "event_at": datetime(2026, 2, 22, 20, 2, tzinfo=timezone.utc),
                "score_diff": 4,
                "opening_price": 0.60,
                "team_price": 0.72,
                "price_delta_from_open": 0.12,
                "large_swing_next_12_states_flag": True,
                "crossed_50c_next_12_states_flag": False,
            },
            {
                "game_id": "002TESTA7",
                "team_side": "away",
                "state_index": 1,
                "team_id": 1610612747,
                "team_slug": "LAL",
                "opponent_team_id": 1610612738,
                "opponent_team_slug": "BOS",
                "season": "2025-26",
                "season_phase": "regular_season",
                "analysis_version": "v1_0_1",
                "game_date": datetime(2026, 2, 22, tzinfo=timezone.utc).date(),
                "event_index": 2,
                "event_at": datetime(2026, 2, 22, 20, 2, tzinfo=timezone.utc),
                "score_diff": -4,
                "opening_price": 0.40,
                "team_price": 0.28,
                "price_delta_from_open": -0.12,
                "large_swing_next_12_states_flag": True,
                "crossed_50c_next_12_states_flag": False,
            },
            {
                "game_id": "002TESTA7",
                "team_side": "away",
                "state_index": 2,
                "team_id": 1610612747,
                "team_slug": "LAL",
                "opponent_team_id": 1610612738,
                "opponent_team_slug": "BOS",
                "season": "2025-26",
                "season_phase": "regular_season",
                "analysis_version": "v1_0_1",
                "game_date": datetime(2026, 2, 22, tzinfo=timezone.utc).date(),
                "event_index": 4,
                "event_at": datetime(2026, 2, 22, 20, 4, tzinfo=timezone.utc),
                "score_diff": 0,
                "opening_price": 0.40,
                "team_price": 0.52,
                "price_delta_from_open": 0.12,
                "large_swing_next_12_states_flag": True,
                "crossed_50c_next_12_states_flag": True,
            },
        ]
    )


def _build_pbp_df() -> pd.DataFrame:
    base = datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc)
    return pd.DataFrame(
        [
            {
                "game_id": "002TESTA7",
                "event_index": 1,
                "action_id": "1",
                "period": 1,
                "clock": "PT11M30.00S",
                "description": "J Tatum makes layup",
                "home_score": 2,
                "away_score": 0,
                "is_score_change": True,
                "payload_json": {
                    "timeActual": (base + pd.Timedelta(minutes=1)).isoformat(),
                    "playerId": 101,
                    "playerNameI": "J Tatum",
                    "teamId": 1610612738,
                    "teamTricode": "BOS",
                    "pointsTotal": 2,
                },
                "game_date": base.date(),
                "game_start_time": base,
                "home_team_id": 1610612738,
                "away_team_id": 1610612747,
                "home_team_slug": "BOS",
                "away_team_slug": "LAL",
            },
            {
                "game_id": "002TESTA7",
                "event_index": 2,
                "action_id": "2",
                "period": 1,
                "clock": "PT10M50.00S",
                "description": "J Brown makes three",
                "home_score": 4,
                "away_score": 0,
                "is_score_change": True,
                "payload_json": {
                    "timeActual": (base + pd.Timedelta(minutes=2)).isoformat(),
                    "playerId": 102,
                    "playerNameI": "J Brown",
                    "teamId": 1610612738,
                    "teamTricode": "BOS",
                    "pointsTotal": 2,
                },
                "game_date": base.date(),
                "game_start_time": base,
                "home_team_id": 1610612738,
                "away_team_id": 1610612747,
                "home_team_slug": "BOS",
                "away_team_slug": "LAL",
            },
            {
                "game_id": "002TESTA7",
                "event_index": 3,
                "action_id": "3",
                "period": 1,
                "clock": "PT10M00.00S",
                "description": "L James scores",
                "home_score": 4,
                "away_score": 2,
                "is_score_change": True,
                "payload_json": {
                    "timeActual": (base + pd.Timedelta(minutes=3)).isoformat(),
                    "playerId": 201,
                    "playerNameI": "L James",
                    "teamId": 1610612747,
                    "teamTricode": "LAL",
                    "pointsTotal": 2,
                },
                "game_date": base.date(),
                "game_start_time": base,
                "home_team_id": 1610612738,
                "away_team_id": 1610612747,
                "home_team_slug": "BOS",
                "away_team_slug": "LAL",
            },
            {
                "game_id": "002TESTA7",
                "event_index": 4,
                "action_id": "4",
                "period": 1,
                "clock": "PT09M30.00S",
                "description": "A Reaves scores",
                "home_score": 4,
                "away_score": 4,
                "is_score_change": True,
                "payload_json": {
                    "timeActual": (base + pd.Timedelta(minutes=4)).isoformat(),
                    "playerId": 202,
                    "playerNameI": "A Reaves",
                    "teamId": 1610612747,
                    "teamTricode": "LAL",
                    "pointsTotal": 2,
                },
                "game_date": base.date(),
                "game_start_time": base,
                "home_team_id": 1610612738,
                "away_team_id": 1610612747,
                "home_team_slug": "BOS",
                "away_team_slug": "LAL",
            },
        ]
    )


def _build_player_stats_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "player_id": 102,
                "player_name": "J Brown",
                "team_id": 1610612738,
                "team_slug": "BOS",
                "season": "2025-26",
                "captured_at": datetime(2026, 2, 22, 18, 0, tzinfo=timezone.utc),
                "metric_set": "availability",
                "stats_json": {"status": "out"},
                "source": "pytest",
            },
            {
                "player_id": 202,
                "player_name": "A Reaves",
                "team_id": 1610612747,
                "team_slug": "LAL",
                "season": "2025-26",
                "captured_at": datetime(2026, 2, 22, 18, 0, tzinfo=timezone.utc),
                "metric_set": "availability",
                "stats_json": {"available": True},
                "source": "pytest",
            },
        ]
    )


def test_player_impact_shadow_result_marks_experimental_and_builds_tables() -> None:
    result = build_player_impact_shadow_result(
        state_df=_build_state_df(),
        play_by_play_df=_build_pbp_df(),
        player_stats_df=_build_player_stats_df(),
        season="2025-26",
        season_phase="regular_season",
        analysis_version="v1_0_1",
    )

    assert result.summary["shadow_mode"] is True
    assert result.summary["experimental_label"] == "experimental_shadow"
    assert result.summary["swing_state_rows_total"] == 2
    assert result.summary["run_segments_total"] == 2
    assert result.summary["swing_run_segments_total"] == 2
    assert set(result.player_presence_summary["player_name"]) == {"J Brown", "A Reaves"}
    assert result.player_presence_summary.set_index("player_name").loc["J Brown", "run_stop_count"] == 1
    assert result.player_presence_summary.set_index("player_name").loc["A Reaves", "run_stop_count"] == 1
    assert set(result.absence_proxy_summary["proxy_basis"]) == {"status:out", "available"}
    assert set(result.absence_proxy_deltas["proxy_bucket"]) == {"high_proxy", "low_proxy"}

    markdown = render_player_impact_shadow_markdown(result)
    assert "Experimental shadow lane" in markdown
    assert "Correlational only" in markdown
    assert "No causal injury claims" in markdown


def test_player_impact_shadow_artifacts_write_expected_files(tmp_path: Path) -> None:
    result = build_player_impact_shadow_result(
        state_df=_build_state_df(),
        play_by_play_df=_build_pbp_df(),
        player_stats_df=_build_player_stats_df(),
        season="2025-26",
        season_phase="regular_season",
        analysis_version="v1_0_1",
    )

    artifacts = write_player_impact_shadow_artifacts(tmp_path, result)

    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["markdown"]).exists()
    assert Path(artifacts["swing_state_events"]["csv"]).exists()
    assert Path(artifacts["run_segments"]["csv"]).exists()
    assert Path(artifacts["player_presence_summary"]["csv"]).exists()
    assert Path(artifacts["absence_proxy_summary"]["csv"]).exists()
    assert Path(artifacts["absence_proxy_deltas"]["csv"]).exists()

