from __future__ import annotations

import app.data.pipelines.daily.nba.analysis.bundle_loader as bundle_loader_mod
import app.data.pipelines.daily.nba.analysis.mart_game_profiles as game_profiles_mod


def test_outcome_side_matching_accepts_nickname_only_labels_pytest() -> None:
    game = {
        "home_team_slug": "OKC",
        "away_team_slug": "HOU",
        "home_team_name": "Oklahoma City Thunder",
        "away_team_name": "Houston Rockets",
        "home_team_city": None,
        "away_team_city": None,
    }

    assert bundle_loader_mod._match_outcome_side("Thunder", game) == "home"
    assert bundle_loader_mod._match_outcome_side("Rockets", game) == "away"


def test_outcome_side_matching_accepts_multiword_nickname_labels_pytest() -> None:
    game = {
        "home_team_slug": "LAC",
        "away_team_slug": "POR",
        "home_team_name": "LA Clippers",
        "away_team_name": "Portland Trail Blazers",
        "home_team_city": None,
        "away_team_city": None,
    }

    assert bundle_loader_mod._match_outcome_side("Clippers", game) == "home"
    assert bundle_loader_mod._match_outcome_side("Trail Blazers", game) == "away"

    assert (
        game_profiles_mod._resolve_outcome_side(
            game=game,
            outcome_label="Trail Blazers",
            fallback="home",
        )
        == "away"
    )
