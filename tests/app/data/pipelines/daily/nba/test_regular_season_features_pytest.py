from __future__ import annotations

from datetime import date

import pandas as pd

from app.data.pipelines.daily.nba import regular_season_features as features


def test_collect_linked_game_moneyline_df_maps_playoff_series_outcomes_pytest(monkeypatch):
    games_df = pd.DataFrame(
        [
            {
                "game_id": "0042500105",
                "game_date": date(2026, 4, 29),
                "away_team_slug": "ORL",
                "home_team_slug": "DET",
            }
        ]
    )
    query_rows = pd.DataFrame(
        [
            {
                "game_id": "0042500105",
                "game_date": date(2026, 4, 29),
                "away_team_slug": "ORL",
                "home_team_slug": "DET",
                "away_team_name": "Magic",
                "home_team_name": "Pistons",
                "event_id": "event-1",
                "actual_event_slug": "nba-playoffs-who-will-win-series-pistons-vs-magic",
                "market_uuid": "market-1",
                "external_market_id": "external-1",
                "question": "NBA Playoffs: Who Will Win Series? - Pistons vs. Magic",
                "market_type": "moneyline",
                "outcome_id": "outcome-home",
                "outcome": "Pistons",
                "token_id": "token-home",
                "last_price": 0.61,
            },
            {
                "game_id": "0042500105",
                "game_date": date(2026, 4, 29),
                "away_team_slug": "ORL",
                "home_team_slug": "DET",
                "away_team_name": "Magic",
                "home_team_name": "Pistons",
                "event_id": "event-1",
                "actual_event_slug": "nba-playoffs-who-will-win-series-pistons-vs-magic",
                "market_uuid": "market-1",
                "external_market_id": "external-1",
                "question": "NBA Playoffs: Who Will Win Series? - Pistons vs. Magic",
                "market_type": "moneyline",
                "outcome_id": "outcome-away",
                "outcome": "Magic",
                "token_id": "token-away",
                "last_price": 0.39,
            },
        ]
    )

    monkeypatch.setattr(features, "_query_df", lambda *_args, **_kwargs: query_rows)

    out = features._collect_linked_game_moneyline_df(object(), games_df=games_df)

    assert set(out["event_slug"]) == {"nba-orl-det-2026-04-29"}
    assert set(out["actual_event_slug"]) == {"nba-playoffs-who-will-win-series-pistons-vs-magic"}
    assert set(out["team_abbr"]) == {"DET", "ORL"}
    assert set(out["outcome_id"]) == {"outcome-home", "outcome-away"}


def test_resolve_catalog_refs_falls_back_to_linked_row_ids_pytest():
    row = pd.Series(
        {
            "event_id": "event-1",
            "market_uuid": "market-1",
            "outcome_id": "outcome-1",
            "market_id": "external-1",
            "token_id": "token-1",
            "outcome": "Pistons",
        }
    )

    refs = features._resolve_catalog_refs(
        expected_slug="nba-orl-det-2026-04-29",
        moneyline_row=row,
        event_ids_by_slug={},
        outcome_lookup={},
    )

    assert refs == {
        "event_id": "event-1",
        "market_id": "market-1",
        "outcome_id": "outcome-1",
    }
