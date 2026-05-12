from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.data.nodes.wnba.balldontlie.client import (
    BalldontlieWnbaConfig,
    describe_historical_backfill_readiness,
)
from app.data.nodes.wnba.live.live_stats import normalize_boxscore_payload
from app.data.nodes.wnba.live.play_by_play import (
    compute_wnba_seconds_to_game_end,
    normalize_play_by_play_payload,
)
from app.data.nodes.wnba.polymarket.moneyline import match_wnba_moneyline_markets_to_schedule
from app.data.nodes.wnba.schedule.season_schedule import normalize_schedule_payload


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "wnba_cdn_samples.json"


def _samples() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_wnba_schedule_normalizes_games_and_teams_from_cdn_fixture_pytest() -> None:
    fetched_at = datetime(2026, 5, 12, tzinfo=timezone.utc)
    games_df, teams_df = normalize_schedule_payload(_samples()["schedule"], season="2026", fetched_at=fetched_at)

    assert len(games_df) == 1
    assert len(teams_df) == 2
    game = games_df.iloc[0]
    assert game["game_id"] == "1012600001"
    assert str(game["game_date"]) == "2026-04-25"
    assert game["season_phase"] == "preseason"
    assert game["home_team_tricode"] == "NYL"
    assert game["away_team_tricode"] == "IND"
    assert game["home_score"] == 91
    assert isinstance(game["raw"], dict)


def test_wnba_boxscore_normalizes_team_and_player_snapshot_fields_pytest() -> None:
    frames = normalize_boxscore_payload(_samples()["boxscore"])

    assert frames.snapshot["game_id"] == "1012600001"
    assert frames.snapshot["regulation_minutes"] == 40
    assert frames.snapshot["period_length_minutes"] == 10
    assert frames.snapshot["home_score"] == 91
    assert set(frames.teams["team_tricode"]) == {"NYL", "IND"}
    assert frames.teams.loc[frames.teams["team_tricode"] == "IND", "minutes"].iloc[0] == "PT200M00.00S"

    player = frames.players.loc[frames.players["player_id"] == 1642286].iloc[0]
    assert player["player_name"] == "Caitlin Clark"
    assert bool(player["starter"]) is True
    assert bool(player["oncourt"]) is False
    assert bool(player["played"]) is True
    assert player["minutes"] == "PT20M00.00S"
    assert player["plus_minus"] == 8.0


def test_wnba_play_by_play_normalizes_scoring_and_substitution_pytest() -> None:
    df = normalize_play_by_play_payload(_samples()["play_by_play"])

    assert len(df) == 3
    scoring = df.loc[df["action_number"] == 7].iloc[0]
    assert scoring["clock"] == "PT09M46.00S"
    assert scoring["points_away"] == 2
    assert scoring["points_home"] == 0
    assert bool(scoring["is_score_change"]) is True
    assert scoring["scoring_team_tricode"] == "IND"

    substitution = df.loc[df["action_type"] == "substitution"].iloc[0]
    assert substitution["substitution_direction"] == "out"
    assert substitution["substitution_person_id"] == 1627668
    assert substitution["substitution_player_name"] == "Stewart"

    assert compute_wnba_seconds_to_game_end(1, "PT09M46.00S") == 2386


def test_wnba_polymarket_moneyline_matching_builds_passive_watch_plan_pytest() -> None:
    games_df, _teams_df = normalize_schedule_payload(_samples()["schedule"], season="2026")
    markets_df = pd.DataFrame(
        [
            {
                "event_slug": "wnba-ind-nyl-2026-04-25",
                "market_id": "pm-market-1",
                "outcome": "New York Liberty",
                "token_id": "token-nyl",
            },
            {
                "event_slug": "wnba-ind-nyl-2026-04-25",
                "market_id": "pm-market-1",
                "outcome": "Indiana Fever",
                "token_id": "token-ind",
            },
        ]
    )

    targets = match_wnba_moneyline_markets_to_schedule(markets_df, games_df)

    assert len(targets) == 1
    target = targets.iloc[0]
    assert target["game_id"] == "1012600001"
    assert bool(target["passive_only"]) is True
    assert bool(target["clob_capture_required"]) is True
    assert target["home_outcome_token_id"] == "token-nyl"
    assert target["away_outcome_token_id"] == "token-ind"
    assert target["watch_plan_json"]["orders_allowed"] is False


def test_balldontlie_last_season_backfill_reports_exact_missing_config_blockers_pytest() -> None:
    readiness = describe_historical_backfill_readiness(
        season="2025",
        config=BalldontlieWnbaConfig(api_key=None, tier=None),
    )

    assert readiness["status"] == "blocked"
    blocker_codes = {row["code"] for row in readiness["blockers"]}
    assert "missing_balldontlie_wnba_api_key" in blocker_codes
    assert "missing_balldontlie_wnba_play_by_play_tier" in blocker_codes
    assert "missing_balldontlie_wnba_stats_tier" in blocker_codes
