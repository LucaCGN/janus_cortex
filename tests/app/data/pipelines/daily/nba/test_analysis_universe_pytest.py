from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.data.databases import migrate
from app.data.databases.postgres import ensure_database_exists, managed_connection
from app.data.databases.repositories import JanusUpsertRepository
from app.data.pipelines.daily.nba.analysis.contracts import AnalysisUniverseRequest
from app.data.pipelines.daily.nba.analysis.universe import (
    build_analysis_universe_qa_summary,
    load_analysis_universe,
)
import app.data.pipelines.daily.nba.analysis_module as mod


pytestmark = pytest.mark.postgres_live


TEAM_FIXTURES = {
    "BOS": 1610612738,
    "LAL": 1610612747,
    "NYK": 1610612752,
    "CHA": 1610612766,
    "ORL": 1610612753,
    "SAS": 1610612759,
    "DEN": 1610612743,
    "MEM": 1610612763,
    "DAL": 1610612742,
    "MIL": 1610612749,
    "PHI": 1610612755,
    "MIA": 1610612748,
}


@pytest.fixture(scope="module", autouse=True)
def reset_and_migrate_db() -> None:
    ensure_database_exists()
    with managed_connection() as connection:
        migrate.drop_managed_schemas(connection)
        migrate.apply_migrations(connection)


def _seed_event(repo: JanusUpsertRepository, *, suffix: str) -> str:
    _ = repo.upsert_provider(
        provider_id=str(uuid4()),
        code=f"polymarket_analysis_{suffix}",
        name="Polymarket",
        category="prediction_market",
    )
    event_type_id = repo.upsert_event_type(
        event_type_id=str(uuid4()),
        code=f"sports_nba_game_analysis_{suffix}",
        name="NBA Game",
        domain="sports",
    )
    event_id = repo.upsert_event(
        event_id=str(uuid4()),
        event_type_id=event_type_id,
        information_profile_id=None,
        title=f"NBA analysis seed {suffix}",
        status="resolved",
        canonical_slug=f"nba-analysis-seed-{suffix.lower()}",
    )
    return event_id


def _seed_teams(repo: JanusUpsertRepository) -> None:
    team_meta = {
        "BOS": ("Boston", "Celtics"),
        "LAL": ("Los Angeles", "Lakers"),
        "NYK": ("New York", "Knicks"),
        "CHA": ("Charlotte", "Hornets"),
        "ORL": ("Orlando", "Magic"),
        "SAS": ("San Antonio", "Spurs"),
        "DEN": ("Denver", "Nuggets"),
        "MEM": ("Memphis", "Grizzlies"),
        "DAL": ("Dallas", "Mavericks"),
        "MIL": ("Milwaukee", "Bucks"),
        "PHI": ("Philadelphia", "76ers"),
        "MIA": ("Miami", "Heat"),
    }
    for team_slug, team_id in TEAM_FIXTURES.items():
        city, name = team_meta[team_slug]
        repo.upsert_nba_team(team_id=team_id, team_slug=team_slug, team_name=name, team_city=city)


def _seed_game(
    repo: JanusUpsertRepository,
    *,
    game_id: str,
    game_start: datetime,
    home_slug: str,
    away_slug: str,
    season_phase: str = "regular_season",
    game_status: int = 3,
    game_status_text: str | None = None,
    home_score: int = 110,
    away_score: int = 100,
) -> None:
    repo.upsert_nba_game(
        game_id=game_id,
        season="2025-26",
        game_date=game_start.date(),
        game_start_time=game_start,
        game_status=game_status,
        game_status_text=game_status_text or ("Final" if game_status == 3 else "In Progress"),
        period=4,
        game_clock="PT00M00.00S",
        home_team_id=TEAM_FIXTURES[home_slug],
        away_team_id=TEAM_FIXTURES[away_slug],
        home_team_slug=home_slug,
        away_team_slug=away_slug,
        home_score=home_score,
        away_score=away_score,
        season_phase=season_phase,
        season_phase_label=season_phase.replace("_", " ").title(),
        updated_at=game_start + timedelta(hours=3),
    )


def _seed_timed_pbp(repo: JanusUpsertRepository, *, game_id: str, game_start: datetime) -> None:
    rows = [
        {
            "event_index": 1,
            "action_id": "1",
            "period": 1,
            "clock": "PT11M00.00S",
            "description": "Opening basket",
            "home_score": 2,
            "away_score": 0,
            "payload_json": {
                "timeActual": (game_start + timedelta(minutes=1)).isoformat(),
                "teamTricode": "BOS",
                "teamId": TEAM_FIXTURES["BOS"],
                "pointsTotal": 2,
            },
        },
        {
            "event_index": 2,
            "action_id": "2",
            "period": 1,
            "clock": "PT10M00.00S",
            "description": "Answering basket",
            "home_score": 2,
            "away_score": 2,
            "payload_json": {
                "timeActual": (game_start + timedelta(minutes=2)).isoformat(),
                "teamTricode": "LAL",
                "teamId": TEAM_FIXTURES["LAL"],
                "pointsTotal": 2,
            },
        },
    ]
    for row in rows:
        repo.upsert_nba_play_by_play_event(
            game_id=game_id,
            event_index=row["event_index"],
            action_id=row["action_id"],
            period=row["period"],
            clock=row["clock"],
            description=row["description"],
            home_score=row["home_score"],
            away_score=row["away_score"],
            is_score_change=True,
            payload_json=row["payload_json"],
        )


def _seed_feature_snapshot(
    repo: JanusUpsertRepository,
    *,
    game_id: str,
    game_start: datetime,
    coverage_status: str,
    event_id: str | None,
    pbp_event_count: int,
    covered_polymarket_game_flag: bool,
    home_pre: tuple[float | None, float | None] = (None, None),
    away_pre: tuple[float | None, float | None] = (None, None),
    home_in: tuple[float | None, float | None] = (None, None),
    away_in: tuple[float | None, float | None] = (None, None),
) -> None:
    repo.upsert_nba_game_feature_snapshot(
        game_id=game_id,
        computed_at=game_start + timedelta(hours=3),
        feature_version="pytest_analysis_universe_v1",
        season="2025-26",
        event_id=event_id,
        pbp_event_count=pbp_event_count,
        lead_changes=2,
        covered_polymarket_game_flag=covered_polymarket_game_flag,
        home_pre_game_price_min=home_pre[0],
        home_pre_game_price_max=home_pre[1],
        away_pre_game_price_min=away_pre[0],
        away_pre_game_price_max=away_pre[1],
        home_in_game_price_min=home_in[0],
        home_in_game_price_max=home_in[1],
        away_in_game_price_min=away_in[0],
        away_in_game_price_max=away_in[1],
        price_window_start=game_start - timedelta(minutes=30),
        price_window_end=game_start + timedelta(hours=3),
        coverage_status=coverage_status,
        source_summary_json={"pytest": True, "coverage_status": coverage_status},
    )


def _seed_link(repo: JanusUpsertRepository, *, game_id: str, event_id: str, linked_at: datetime) -> None:
    repo.upsert_nba_game_event_link(
        nba_game_event_link_id=str(uuid4()),
        game_id=game_id,
        event_id=event_id,
        confidence=0.98,
        linked_by="pytest",
        linked_at=linked_at,
    )


@pytest.fixture(scope="module")
def seeded_universe_games() -> dict[str, str]:
    with managed_connection() as connection:
        repo = JanusUpsertRepository(connection)
        _seed_teams(repo)

        research_ready_game_id = "002A100001"
        no_history_game_id = "002A100002"
        pregame_only_game_id = "002A100003"
        no_match_game_id = "002A100004"
        excluded_game_id = "002A100005"
        unfinished_game_id = "002A100006"

        research_start = datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc)
        no_history_start = datetime(2026, 2, 23, 20, 0, tzinfo=timezone.utc)
        pregame_only_start = datetime(2026, 2, 24, 20, 0, tzinfo=timezone.utc)
        no_match_start = datetime(2026, 2, 25, 20, 0, tzinfo=timezone.utc)
        excluded_start = datetime(2026, 2, 26, 20, 0, tzinfo=timezone.utc)
        unfinished_start = datetime(2026, 2, 27, 20, 0, tzinfo=timezone.utc)

        _seed_game(repo, game_id=research_ready_game_id, game_start=research_start, home_slug="BOS", away_slug="LAL")
        research_event_id = _seed_event(repo, suffix=research_ready_game_id)
        _seed_link(repo, game_id=research_ready_game_id, event_id=research_event_id, linked_at=research_start + timedelta(minutes=1))
        _seed_timed_pbp(repo, game_id=research_ready_game_id, game_start=research_start)
        _seed_feature_snapshot(
            repo,
            game_id=research_ready_game_id,
            game_start=research_start,
            coverage_status="covered_pre_and_ingame",
            event_id=research_event_id,
            pbp_event_count=2,
            covered_polymarket_game_flag=True,
        )

        _seed_game(repo, game_id=no_history_game_id, game_start=no_history_start, home_slug="NYK", away_slug="CHA")
        no_history_event_id = _seed_event(repo, suffix=no_history_game_id)
        _seed_link(repo, game_id=no_history_game_id, event_id=no_history_event_id, linked_at=no_history_start + timedelta(minutes=1))
        _seed_feature_snapshot(
            repo,
            game_id=no_history_game_id,
            game_start=no_history_start,
            coverage_status="no_history",
            event_id=no_history_event_id,
            pbp_event_count=0,
            covered_polymarket_game_flag=True,
        )

        _seed_game(repo, game_id=pregame_only_game_id, game_start=pregame_only_start, home_slug="ORL", away_slug="SAS")
        pregame_event_id = _seed_event(repo, suffix=pregame_only_game_id)
        _seed_link(repo, game_id=pregame_only_game_id, event_id=pregame_event_id, linked_at=pregame_only_start + timedelta(minutes=1))
        _seed_feature_snapshot(
            repo,
            game_id=pregame_only_game_id,
            game_start=pregame_only_start,
            coverage_status="pregame_only",
            event_id=pregame_event_id,
            pbp_event_count=0,
            covered_polymarket_game_flag=True,
            home_pre=(0.51, 0.54),
            away_pre=(0.46, 0.49),
        )

        _seed_game(repo, game_id=no_match_game_id, game_start=no_match_start, home_slug="DEN", away_slug="MEM")
        _seed_feature_snapshot(
            repo,
            game_id=no_match_game_id,
            game_start=no_match_start,
            coverage_status="no_matching_event",
            event_id=None,
            pbp_event_count=0,
            covered_polymarket_game_flag=False,
        )

        _seed_game(repo, game_id=excluded_game_id, game_start=excluded_start, home_slug="PHI", away_slug="MIA")
        excluded_event_id = _seed_event(repo, suffix=excluded_game_id)
        _seed_link(repo, game_id=excluded_game_id, event_id=excluded_event_id, linked_at=excluded_start + timedelta(minutes=1))
        _seed_feature_snapshot(
            repo,
            game_id=excluded_game_id,
            game_start=excluded_start,
            coverage_status="covered_pre_and_ingame",
            event_id=excluded_event_id,
            pbp_event_count=0,
            covered_polymarket_game_flag=True,
            home_pre=(0.57, 0.61),
            away_pre=(0.39, 0.43),
            home_in=(0.42, 0.74),
            away_in=(0.26, 0.58),
        )

        _seed_game(
            repo,
            game_id=unfinished_game_id,
            game_start=unfinished_start,
            home_slug="DAL",
            away_slug="MIL",
            game_status=2,
            game_status_text="In Progress",
        )
        unfinished_event_id = _seed_event(repo, suffix=unfinished_game_id)
        _seed_link(repo, game_id=unfinished_game_id, event_id=unfinished_event_id, linked_at=unfinished_start + timedelta(minutes=1))
        _seed_feature_snapshot(
            repo,
            game_id=unfinished_game_id,
            game_start=unfinished_start,
            coverage_status="covered_pre_and_ingame",
            event_id=unfinished_event_id,
            pbp_event_count=0,
            covered_polymarket_game_flag=True,
            home_pre=(0.61, 0.64),
            away_pre=(0.36, 0.39),
            home_in=(0.49, 0.70),
            away_in=(0.30, 0.51),
        )

    return {
        "research_ready": research_ready_game_id,
        "no_history": no_history_game_id,
        "pregame_only": pregame_only_game_id,
        "no_matching_event": no_match_game_id,
        "excluded": excluded_game_id,
        "unfinished": unfinished_game_id,
    }


def test_load_analysis_universe_classification_pytest(seeded_universe_games: dict[str, str]) -> None:
    request = AnalysisUniverseRequest(season="2025-26", season_phase="regular_season", coverage_filter="all")
    with managed_connection() as connection:
        universe = load_analysis_universe(connection, request)
        descriptive_only_df = mod.load_analysis_universe(
            connection,
            AnalysisUniverseRequest(season="2025-26", season_phase="regular_season", coverage_filter="descriptive_only"),
        )

    assert set(universe.full_universe["game_id"].astype(str)) == {
        seeded_universe_games["research_ready"],
        seeded_universe_games["no_history"],
        seeded_universe_games["pregame_only"],
        seeded_universe_games["no_matching_event"],
        seeded_universe_games["excluded"],
    }
    assert seeded_universe_games["unfinished"] not in set(universe.full_universe["game_id"].astype(str))

    classification_by_game = (
        universe.full_universe.set_index("game_id")[["classification", "classification_reason"]].to_dict(orient="index")
    )
    assert classification_by_game[seeded_universe_games["research_ready"]]["classification"] == "research_ready"
    assert classification_by_game[seeded_universe_games["no_history"]]["classification"] == "descriptive_only"
    assert classification_by_game[seeded_universe_games["pregame_only"]]["classification"] == "descriptive_only"
    assert classification_by_game[seeded_universe_games["no_matching_event"]]["classification"] == "descriptive_only"
    assert classification_by_game[seeded_universe_games["excluded"]]["classification"] == "excluded"
    assert (
        classification_by_game[seeded_universe_games["excluded"]]["classification_reason"]
        == "covered_pre_and_ingame_without_timed_pbp"
    )

    assert set(universe.research_ready["game_id"].astype(str)) == {seeded_universe_games["research_ready"]}
    assert set(universe.descriptive_only["game_id"].astype(str)) == {
        seeded_universe_games["no_history"],
        seeded_universe_games["pregame_only"],
        seeded_universe_games["no_matching_event"],
    }
    assert set(descriptive_only_df["game_id"].astype(str)) == {
        seeded_universe_games["no_history"],
        seeded_universe_games["pregame_only"],
        seeded_universe_games["no_matching_event"],
    }
    research_ready_row = universe.full_universe.loc[
        universe.full_universe["game_id"].astype(str) == seeded_universe_games["research_ready"]
    ].iloc[0]
    assert bool(research_ready_row["has_bilateral_market_path"]) is True
    assert bool(research_ready_row["market_path_inferred_from_coverage_status"]) is True


def test_analysis_universe_qa_summary_pytest(seeded_universe_games: dict[str, str]) -> None:
    request = AnalysisUniverseRequest(season="2025-26", season_phase="regular_season", coverage_filter="all")
    with managed_connection() as connection:
        universe = load_analysis_universe(connection, request)

    qa = build_analysis_universe_qa_summary(universe.full_universe)

    assert qa["games_total"] == 5
    assert qa["research_ready_games"] == 1
    assert qa["descriptive_only_games"] == 3
    assert qa["excluded_games"] == 1
    assert qa["coverage_status_counts"] == {
        "covered_pre_and_ingame": 2,
        "no_history": 1,
        "no_matching_event": 1,
        "pregame_only": 1,
    }
    assert qa["classification_counts"] == {
        "descriptive_only": 3,
        "excluded": 1,
        "research_ready": 1,
    }

    team_counts = {item["team_slug"]: item["games"] for item in qa["coverage_by_team"]}
    assert team_counts == {
        "CHA": 1,
        "DEN": 1,
        "MEM": 1,
        "MIA": 1,
        "NYK": 1,
        "ORL": 1,
        "PHI": 1,
        "SAS": 1,
    }

    dates = [item["game_date"] for item in qa["coverage_by_date"]]
    assert dates == ["2026-02-22", "2026-02-23", "2026-02-24", "2026-02-25", "2026-02-26"]
    unresolved_samples = {item["game_id"]: item for item in qa["unresolved_game_samples"]}
    assert seeded_universe_games["no_history"] in unresolved_samples
    assert seeded_universe_games["excluded"] in unresolved_samples
    assert unresolved_samples[seeded_universe_games["excluded"]]["classification"] == "excluded"
    assert unresolved_samples[seeded_universe_games["excluded"]]["has_timed_pbp"] is False
