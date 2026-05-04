from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.data.databases import migrate
from app.data.databases.postgres import ensure_database_exists, managed_connection
from app.data.databases.repositories import JanusUpsertRepository
import app.data.pipelines.daily.nba.analysis.mart_aggregates as aggregates_mod
import app.data.pipelines.daily.nba.analysis.mart_game_profiles as game_profiles_mod
import app.data.pipelines.daily.nba.analysis_module as mod
from app.data.pipelines.daily.nba.analysis.stakes import evaluate_game_stakes


pytestmark = pytest.mark.postgres_live


@pytest.fixture(scope="module", autouse=True)
def reset_and_migrate_db() -> None:
    ensure_database_exists()
    with managed_connection() as connection:
        migrate.drop_managed_schemas(connection)
        migrate.apply_migrations(connection)


def _seed_catalog_and_game(repo: JanusUpsertRepository, *, game_id: str) -> dict[str, str]:
    provider_id = repo.upsert_provider(
        provider_id=str(uuid4()),
        code=f"polymarket_analysis_{game_id}",
        name="Polymarket",
        category="prediction_market",
    )
    event_type_id = repo.upsert_event_type(
        event_type_id=str(uuid4()),
        code=f"sports_nba_game_analysis_{game_id}",
        name="NBA Game",
        domain="sports",
    )
    event_id = repo.upsert_event(
        event_id=str(uuid4()),
        event_type_id=event_type_id,
        information_profile_id=None,
        title="Lakers at Celtics",
        status="resolved",
        canonical_slug=f"nba-lal-bos-{game_id.lower()}",
    )
    market_id = repo.upsert_market(
        market_id=str(uuid4()),
        event_id=event_id,
        question="Who wins the game?",
        market_type="moneyline",
        market_slug=f"moneyline-{game_id.lower()}",
        settlement_status="resolved",
    )
    _ = repo.upsert_market_external_ref(
        market_ref_id=str(uuid4()),
        market_id=market_id,
        provider_id=provider_id,
        external_market_id=f"ext-{game_id.lower()}",
    )
    home_outcome_id = repo.upsert_outcome(
        outcome_id=str(uuid4()),
        market_id=market_id,
        outcome_index=0,
        outcome_label="BOS",
        token_id=f"token-bos-{game_id.lower()}",
        is_winner=True,
    )
    away_outcome_id = repo.upsert_outcome(
        outcome_id=str(uuid4()),
        market_id=market_id,
        outcome_index=1,
        outcome_label="LAL",
        token_id=f"token-lal-{game_id.lower()}",
        is_winner=False,
    )

    repo.upsert_nba_team(team_id=1610612738, team_slug="BOS", team_name="Celtics", team_city="Boston")
    repo.upsert_nba_team(team_id=1610612747, team_slug="LAL", team_name="Lakers", team_city="Los Angeles")

    game_start = datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc)
    repo.upsert_nba_game(
        game_id=game_id,
        season="2025-26",
        game_date=game_start.date(),
        game_start_time=game_start,
        game_status=3,
        game_status_text="Final",
        period=4,
        game_clock="PT00M00.00S",
        home_team_id=1610612738,
        away_team_id=1610612747,
        home_team_slug="BOS",
        away_team_slug="LAL",
        home_score=6,
        away_score=4,
        season_phase="regular_season",
        season_phase_label="Regular Season",
        updated_at=game_start + timedelta(hours=3),
    )
    repo.upsert_nba_game_event_link(
        nba_game_event_link_id=str(uuid4()),
        game_id=game_id,
        event_id=event_id,
        confidence=0.98,
        linked_by="pytest",
        linked_at=game_start + timedelta(minutes=1),
    )

    pbp_events = [
        {
            "event_index": 1,
            "action_id": "1",
            "period": 1,
            "clock": "PT11M30.00S",
            "description": "LAL makes layup",
            "home_score": 0,
            "away_score": 2,
            "payload_json": {
                "timeActual": (game_start + timedelta(minutes=1)).isoformat(),
                "teamTricode": "LAL",
                "playerNameI": "L James",
                "pointsTotal": 2,
                "teamId": 1610612747,
            },
        },
        {
            "event_index": 2,
            "action_id": "2",
            "period": 1,
            "clock": "PT10M50.00S",
            "description": "BOS makes three",
            "home_score": 3,
            "away_score": 2,
            "payload_json": {
                "timeActual": (game_start + timedelta(minutes=2)).isoformat(),
                "teamTricode": "BOS",
                "playerNameI": "J Tatum",
                "pointsTotal": 3,
                "teamId": 1610612738,
            },
        },
        {
            "event_index": 3,
            "action_id": "3",
            "period": 2,
            "clock": "PT09M00.00S",
            "description": "BOS scores inside",
            "home_score": 5,
            "away_score": 2,
            "payload_json": {
                "timeActual": (game_start + timedelta(minutes=3)).isoformat(),
                "teamTricode": "BOS",
                "playerNameI": "J Brown",
                "pointsTotal": 2,
                "teamId": 1610612738,
            },
        },
        {
            "event_index": 4,
            "action_id": "4",
            "period": 4,
            "clock": "PT00M20.00S",
            "description": "LAL free throws",
            "home_score": 5,
            "away_score": 4,
            "payload_json": {
                "timeActual": (game_start + timedelta(minutes=4)).isoformat(),
                "teamTricode": "LAL",
                "playerNameI": "A Reaves",
                "pointsTotal": 2,
                "teamId": 1610612747,
            },
        },
        {
            "event_index": 5,
            "action_id": "5",
            "period": 4,
            "clock": "PT00M01.00S",
            "description": "BOS free throw",
            "home_score": 6,
            "away_score": 4,
            "payload_json": {
                "timeActual": (game_start + timedelta(minutes=5)).isoformat(),
                "teamTricode": "BOS",
                "playerNameI": "D White",
                "pointsTotal": 1,
                "teamId": 1610612738,
            },
        },
    ]
    for row in pbp_events:
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

    repo.upsert_nba_game_feature_snapshot(
        game_id=game_id,
        computed_at=game_start + timedelta(hours=3),
        feature_version="pytest_analysis_feature_v1",
        season="2025-26",
        event_id=event_id,
        pbp_event_count=len(pbp_events),
        lead_changes=2,
        covered_polymarket_game_flag=True,
        home_pre_game_price_min=0.70,
        home_pre_game_price_max=0.74,
        away_pre_game_price_min=0.26,
        away_pre_game_price_max=0.30,
        home_in_game_price_min=0.58,
        home_in_game_price_max=0.96,
        away_in_game_price_min=0.04,
        away_in_game_price_max=0.42,
        price_window_start=game_start - timedelta(minutes=30),
        price_window_end=game_start + timedelta(minutes=5),
        coverage_status="covered_pre_and_ingame",
        source_summary_json={"pytest": True},
    )

    bos_ticks = [
        (game_start - timedelta(minutes=30), 0.72),
        (game_start + timedelta(minutes=1), 0.58),
        (game_start + timedelta(minutes=2), 0.64),
        (game_start + timedelta(minutes=3), 0.80),
        (game_start + timedelta(minutes=4), 0.70),
        (game_start + timedelta(minutes=5), 0.96),
    ]
    lal_ticks = [
        (game_start - timedelta(minutes=30), 0.28),
        (game_start + timedelta(minutes=1), 0.42),
        (game_start + timedelta(minutes=2), 0.36),
        (game_start + timedelta(minutes=3), 0.20),
        (game_start + timedelta(minutes=4), 0.30),
        (game_start + timedelta(minutes=5), 0.04),
    ]
    for ts, price in bos_ticks:
        repo.insert_outcome_price_tick(
            outcome_id=home_outcome_id,
            ts=ts,
            source="clob_prices_history",
            price=price,
            ignore_duplicates=True,
        )
    for ts, price in lal_ticks:
        repo.insert_outcome_price_tick(
            outcome_id=away_outcome_id,
            ts=ts,
            source="clob_prices_history",
            price=price,
            ignore_duplicates=True,
        )

    return {
        "event_id": event_id,
        "market_id": market_id,
        "home_outcome_id": home_outcome_id,
        "away_outcome_id": away_outcome_id,
    }


@pytest.fixture(scope="module")
def seeded_game_id() -> str:
    game_id = "002TESTA2001"
    with managed_connection() as connection:
        repo = JanusUpsertRepository(connection)
        _seed_catalog_and_game(repo, game_id=game_id)
    return game_id


def test_extracted_game_profile_rows_pytest(seeded_game_id: str) -> None:
    with managed_connection() as connection:
        universe_df = mod.load_analysis_universe(
            connection,
            mod.AnalysisUniverseRequest(
                season="2025-26",
                season_phase="regular_season",
                coverage_filter="all",
            ),
        )
        universe_row = universe_df.loc[universe_df["game_id"] == seeded_game_id].iloc[0]
        bundle = game_profiles_mod.load_analysis_bundle(connection, game_id=seeded_game_id)

    assert bundle is not None
    computed_at = datetime(2026, 2, 23, tzinfo=timezone.utc)
    game_rows, state_rows, qa = game_profiles_mod.derive_game_rows(
        universe_row=universe_row,
        bundle=bundle,
        analysis_version=mod.ANALYSIS_VERSION,
        computed_at=computed_at,
        build_state_rows_for_side=mod._build_state_rows_for_side,
    )

    assert len(game_rows) == 2
    assert len(state_rows) == 10
    assert qa["research_ready_flag"] is True
    assert qa["available_outcome_sides"] == ["away", "home"]

    home_row = next(row for row in game_rows if row["team_side"] == "home")
    away_row = next(row for row in game_rows if row["team_side"] == "away")

    assert game_profiles_mod.opening_band_for_price(0.72) == ("70-80", 7)
    assert home_row["team_slug"] == "BOS"
    assert home_row["research_ready_flag"] is True
    assert home_row["price_path_reconciled_flag"] is True
    assert home_row["opening_price"] == pytest.approx(0.72)
    assert home_row["closing_price"] == pytest.approx(0.96)
    assert home_row["opening_band"] == "70-80"
    assert home_row["ingame_price_range"] == pytest.approx(0.38)
    assert home_row["total_swing"] == pytest.approx(0.38)
    assert home_row["max_favorable_excursion"] == pytest.approx(0.24)
    assert home_row["max_adverse_excursion"] == pytest.approx(0.14)
    assert home_row["inversion_count"] == 0
    assert home_row["winner_stable_70_clock_elapsed_seconds"] == pytest.approx(900.0)
    assert home_row["stakes_score"] == pytest.approx(45.0)
    assert home_row["stakes_bucket"] == "medium"
    assert "stakes" in home_row["notes_json"]

    assert away_row["team_slug"] == "LAL"
    assert away_row["research_ready_flag"] is True
    assert away_row["price_path_reconciled_flag"] is True
    assert away_row["opening_band"] == "20-30"
    assert away_row["max_favorable_excursion"] == pytest.approx(0.14)
    assert away_row["max_adverse_excursion"] == pytest.approx(0.24)
    assert away_row["winner_stable_70_at"] is None


def test_opening_band_helper_edges_pytest() -> None:
    assert game_profiles_mod.opening_band_for_price(None) == (None, None)
    assert game_profiles_mod.opening_band_for_price(0.0) == ("0-10", 0)
    assert game_profiles_mod.opening_band_for_price(0.10) == ("10-20", 1)
    assert game_profiles_mod.opening_band_for_price(0.72) == ("70-80", 7)
    assert game_profiles_mod.opening_band_for_price(0.999999) == ("90-100", 9)


def test_evaluate_game_stakes_scores_playoff_balance_and_playin_pytest() -> None:
    playoff = evaluate_game_stakes(
        season_phase="playoffs",
        game_date="2026-04-20",
        home_opening_price=0.52,
        away_opening_price=0.48,
    )
    play_in = evaluate_game_stakes(season_phase="play_in", game_date="2026-04-15")
    locked = evaluate_game_stakes(
        season_phase="regular_season",
        game_date="2026-04-10",
        context={"position_mathematically_locked_flag": True},
    )

    assert playoff.stakes_score == pytest.approx(94.2)
    assert playoff.stakes_bucket == "critical"
    assert play_in.stakes_score == pytest.approx(100.0)
    assert locked.stakes_score == pytest.approx(0.0)


def test_derive_game_rows_preserves_non_research_ready_semantics_pytest() -> None:
    game_start = datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc)
    universe_row = mod.pd.Series(
        {
            "game_id": "002TESTA2NR",
            "season": "2025-26",
            "season_phase": "regular_season",
            "coverage_status": "pregame_only",
        }
    )
    bundle = {
        "game": {
            "game_id": "002TESTA2NR",
            "game_date": game_start.date().isoformat(),
            "game_start_time": game_start.isoformat(),
            "home_team_id": 1610612738,
            "away_team_id": 1610612747,
            "home_team_slug": "BOS",
            "away_team_slug": "LAL",
            "home_score": 110,
            "away_score": 100,
        },
        "feature_snapshot": {
            "event_id": "event-nr",
            "home_pre_game_price_min": 0.70,
            "home_pre_game_price_max": 0.74,
            "away_pre_game_price_min": 0.26,
            "away_pre_game_price_max": 0.30,
        },
        "play_by_play": {
            "items": [
                {
                    "time_actual": (game_start + timedelta(minutes=1)).isoformat(),
                    "home_score": 2,
                    "away_score": 0,
                }
            ],
            "summary": {
                "first_event_at": (game_start + timedelta(minutes=1)).isoformat(),
                "last_event_at": (game_start + timedelta(minutes=1)).isoformat(),
            },
        },
        "selected_market": {
            "event_id": "event-nr",
            "market_id": "market-nr",
            "market_type": "moneyline",
            "question": "Who wins the game?",
            "series": [
                {"side": "home", "outcome_id": "home-outcome", "ticks": []},
                {"side": "away", "outcome_id": "away-outcome", "ticks": []},
            ],
        },
    }
    callback_invocations = 0

    def _unexpected_state_builder(**_: object) -> list[dict[str, object]]:
        nonlocal callback_invocations
        callback_invocations += 1
        return []

    game_rows, state_rows, qa = game_profiles_mod.derive_game_rows(
        universe_row=universe_row,
        bundle=bundle,
        analysis_version=mod.ANALYSIS_VERSION,
        computed_at=datetime(2026, 2, 23, tzinfo=timezone.utc),
        build_state_rows_for_side=_unexpected_state_builder,
    )

    assert callback_invocations == 0
    assert state_rows == []
    assert qa["research_ready_flag"] is False
    assert qa["price_path_reconciled_flag"] is False
    assert len(game_rows) == 2

    home_row = next(row for row in game_rows if row["team_side"] == "home")
    away_row = next(row for row in game_rows if row["team_side"] == "away")

    assert home_row["research_ready_flag"] is False
    assert home_row["price_path_reconciled_flag"] is False
    assert home_row["opening_price"] == pytest.approx(0.72)
    assert home_row["opening_band"] == "70-80"
    assert home_row["notes_json"]["state_row_count"] == 0
    assert away_row["opening_price"] == pytest.approx(0.28)
    assert away_row["opening_band"] == "20-30"
    assert away_row["notes_json"]["state_row_count"] == 0


def test_derive_game_rows_repairs_outcome_sides_and_keeps_canonical_readiness_pytest() -> None:
    game_start = datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc)
    timed_items = [
        {
            "time_actual": (game_start + timedelta(minutes=1)).isoformat(),
            "home_score": 2,
            "away_score": 0,
        }
    ]
    universe_row = mod.pd.Series(
        {
            "game_id": "002TESTA2FIX",
            "season": "2025-26",
            "season_phase": "regular_season",
            "coverage_status": "covered_pre_and_ingame",
            "classification": "research_ready",
            "classification_reason": "covered_pre_and_ingame",
            "research_ready_flag": True,
        }
    )
    bundle = {
        "game": {
            "game_id": "002TESTA2FIX",
            "game_date": game_start.date().isoformat(),
            "game_start_time": game_start.isoformat(),
            "home_team_id": 1610612746,
            "away_team_id": 1610612757,
            "home_team_slug": "LAC",
            "away_team_slug": "POR",
            "home_team_name": "Clippers",
            "away_team_name": "Trail Blazers",
            "home_team_city": "LA",
            "away_team_city": "Portland",
            "home_score": 110,
            "away_score": 100,
        },
        "feature_snapshot": {
            "event_id": "event-fix",
        },
        "play_by_play": {
            "items": timed_items,
            "summary": {
                "first_event_at": timed_items[0]["time_actual"],
                "last_event_at": timed_items[0]["time_actual"],
            },
        },
        "selected_market": {
            "event_id": "event-fix",
            "market_id": "market-fix",
            "market_type": "moneyline",
            "question": "Trail Blazers vs. Clippers",
            "series": [
                {
                    "side": "home",
                    "outcome_id": "away-outcome",
                    "outcome_label": "Trail Blazers",
                    "ticks": [
                        {"ts": (game_start - timedelta(minutes=30)).isoformat(), "price": 0.35},
                        {"ts": (game_start + timedelta(minutes=1)).isoformat(), "price": 0.30},
                    ],
                },
                {
                    "side": "home",
                    "outcome_id": "home-outcome",
                    "outcome_label": "Clippers",
                    "ticks": [
                        {"ts": (game_start - timedelta(minutes=30)).isoformat(), "price": 0.65},
                        {"ts": (game_start + timedelta(minutes=1)).isoformat(), "price": 0.70},
                    ],
                },
            ],
        },
    }
    builder_calls: list[tuple[str, str]] = []

    def _state_builder(**kwargs: object) -> list[dict[str, object]]:
        team_side = str(kwargs["team_side"])
        outcome_id = str(kwargs["outcome_id"])
        builder_calls.append((team_side, outcome_id))
        prices = [0.70] if team_side == "home" else [0.30]
        rows = []
        for index, item in enumerate(kwargs["timed_items"]):
            rows.append(
                {
                    "team_side": team_side,
                    "event_at": game_profiles_mod._safe_datetime(item["time_actual"]),
                    "clock_elapsed_seconds": float((index + 1) * 60),
                    "team_price": prices[index],
                }
            )
        return rows

    game_rows, state_rows, qa = game_profiles_mod.derive_game_rows(
        universe_row=universe_row,
        bundle=bundle,
        analysis_version=mod.ANALYSIS_VERSION,
        computed_at=datetime(2026, 2, 23, tzinfo=timezone.utc),
        build_state_rows_for_side=_state_builder,
    )

    assert sorted(builder_calls) == [("away", "away-outcome"), ("home", "home-outcome")]
    assert len(state_rows) == 2
    assert qa["research_ready_flag"] is True
    assert qa["price_path_reconciled_flag"] is True
    assert qa["available_outcome_sides"] == ["away", "home"]
    assert qa["state_rows_complete_flag"] is True
    assert qa["research_ready_contract_drift_flag"] is False

    home_row = next(row for row in game_rows if row["team_side"] == "home")
    away_row = next(row for row in game_rows if row["team_side"] == "away")
    assert home_row["outcome_id"] == "home-outcome"
    assert away_row["outcome_id"] == "away-outcome"
    assert away_row["research_ready_flag"] is True
    assert away_row["notes_json"]["state_rows_complete_flag"] is True


def test_derive_game_rows_surfaces_local_alignment_drift_without_reclassifying_pytest() -> None:
    game_start = datetime(2026, 2, 22, 20, 0, tzinfo=timezone.utc)
    timed_items = [
        {
            "time_actual": (game_start + timedelta(minutes=1)).isoformat(),
            "home_score": 2,
            "away_score": 0,
        }
    ]
    universe_row = mod.pd.Series(
        {
            "game_id": "002TESTA2DRIFT",
            "season": "2025-26",
            "season_phase": "regular_season",
            "coverage_status": "covered_pre_and_ingame",
            "classification": "research_ready",
            "classification_reason": "covered_pre_and_ingame",
            "research_ready_flag": True,
        }
    )
    bundle = {
        "game": {
            "game_id": "002TESTA2DRIFT",
            "game_date": game_start.date().isoformat(),
            "game_start_time": game_start.isoformat(),
            "home_team_id": 1610612738,
            "away_team_id": 1610612747,
            "home_team_slug": "BOS",
            "away_team_slug": "LAL",
            "home_team_name": "Celtics",
            "away_team_name": "Lakers",
            "home_team_city": "Boston",
            "away_team_city": "Los Angeles",
            "home_score": 110,
            "away_score": 100,
        },
        "feature_snapshot": {
            "event_id": "event-drift",
            "home_pre_game_price_min": 0.68,
            "home_pre_game_price_max": 0.72,
            "away_pre_game_price_min": 0.28,
            "away_pre_game_price_max": 0.32,
        },
        "play_by_play": {
            "items": timed_items,
            "summary": {
                "first_event_at": timed_items[0]["time_actual"],
                "last_event_at": timed_items[0]["time_actual"],
            },
        },
        "selected_market": {
            "event_id": "event-drift",
            "market_id": "market-drift",
            "market_type": "moneyline",
            "question": "Who wins the game?",
            "series": [
                {
                    "side": "home",
                    "outcome_id": "home-outcome",
                    "outcome_label": "BOS",
                    "ticks": [
                        {"ts": (game_start - timedelta(minutes=30)).isoformat(), "price": 0.70},
                        {"ts": (game_start + timedelta(minutes=1)).isoformat(), "price": 0.90},
                    ],
                },
                {
                    "side": "away",
                    "outcome_id": "away-outcome",
                    "outcome_label": "LAL",
                    "ticks": [],
                },
            ],
        },
    }

    def _state_builder(**kwargs: object) -> list[dict[str, object]]:
        team_side = str(kwargs["team_side"])
        return [
            {
                "team_side": team_side,
                "event_at": game_profiles_mod._safe_datetime(kwargs["timed_items"][0]["time_actual"]),
                "clock_elapsed_seconds": 60.0,
                "team_price": 0.90 if team_side == "home" else 0.10,
            }
        ]

    game_rows, state_rows, qa = game_profiles_mod.derive_game_rows(
        universe_row=universe_row,
        bundle=bundle,
        analysis_version=mod.ANALYSIS_VERSION,
        computed_at=datetime(2026, 2, 23, tzinfo=timezone.utc),
        build_state_rows_for_side=_state_builder,
    )

    assert len(state_rows) == 1
    assert qa["research_ready_flag"] is True
    assert qa["price_path_reconciled_flag"] is False
    assert qa["state_rows_complete_flag"] is False
    assert qa["research_ready_contract_drift_flag"] is True

    away_row = next(row for row in game_rows if row["team_side"] == "away")
    assert away_row["research_ready_flag"] is True
    assert away_row["price_path_reconciled_flag"] is False
    assert away_row["notes_json"]["research_ready_contract_drift_flag"] is True


def test_build_analysis_mart_game_and_aggregate_profiles_pytest(tmp_path: Path, seeded_game_id: str) -> None:
    request = mod.AnalysisMartBuildRequest(
        season="2025-26",
        season_phase="regular_season",
        rebuild=True,
        game_ids=[seeded_game_id],
        output_root=str(tmp_path),
    )
    first_summary = mod.build_analysis_mart(request)
    second_summary = mod.build_analysis_mart(
        mod.AnalysisMartBuildRequest(
            season="2025-26",
            season_phase="regular_season",
            rebuild=False,
            game_ids=[seeded_game_id],
            output_root=str(tmp_path),
        )
    )

    assert first_summary["games_considered"] == 1
    assert first_summary["research_ready_games"] == 1
    assert first_summary["game_team_profiles_written"] == 2
    assert first_summary["state_rows_written"] == 10
    assert second_summary["game_team_profiles_written"] == 2
    assert second_summary["team_season_profiles_written"] == 2
    assert second_summary["opening_band_profiles_written"] == 2
    assert Path(first_summary["artifacts"]["universe_json"]).exists()
    assert Path(first_summary["artifacts"]["game_profiles_csv"]).exists()

    with managed_connection() as connection:
        profiles_df = aggregates_mod.load_game_profiles_df(
            connection,
            season="2025-26",
            season_phase="regular_season",
            analysis_version=mod.ANALYSIS_VERSION,
        )
        state_df = mod._load_state_panel_df(
            connection,
            season="2025-26",
            season_phase="regular_season",
            analysis_version=mod.ANALYSIS_VERSION,
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM nba.nba_analysis_game_team_profiles;")
            assert int(cursor.fetchone()[0]) == 2
            cursor.execute(
                """
                SELECT count(*)
                FROM (
                    SELECT game_id, team_side, analysis_version, count(*) AS row_count
                    FROM nba.nba_analysis_game_team_profiles
                    GROUP BY 1, 2, 3
                    HAVING count(*) > 1
                ) duplicates;
                """
            )
            assert int(cursor.fetchone()[0]) == 0
            cursor.execute("SELECT count(*) FROM nba.nba_analysis_team_season_profiles;")
            assert int(cursor.fetchone()[0]) == 2
            cursor.execute("SELECT count(*) FROM nba.nba_analysis_opening_band_profiles;")
            assert int(cursor.fetchone()[0]) == 2

    computed_at = datetime(2026, 2, 24, tzinfo=timezone.utc)
    team_rows = aggregates_mod.build_team_season_profile_rows(
        profiles_df,
        state_df,
        season="2025-26",
        season_phase="regular_season",
        analysis_version=mod.ANALYSIS_VERSION,
        computed_at=computed_at,
    )
    opening_band_rows = aggregates_mod.build_opening_band_profile_rows(
        profiles_df,
        season="2025-26",
        season_phase="regular_season",
        analysis_version=mod.ANALYSIS_VERSION,
        computed_at=computed_at,
    )
    repeated_team_rows = aggregates_mod.build_team_season_profile_rows(
        profiles_df,
        state_df,
        season="2025-26",
        season_phase="regular_season",
        analysis_version=mod.ANALYSIS_VERSION,
        computed_at=computed_at,
    )
    repeated_opening_band_rows = aggregates_mod.build_opening_band_profile_rows(
        profiles_df,
        season="2025-26",
        season_phase="regular_season",
        analysis_version=mod.ANALYSIS_VERSION,
        computed_at=computed_at,
    )

    assert [row["opening_band"] for row in opening_band_rows] == ["20-30", "70-80"]
    assert [row["team_slug"] for row in team_rows] == ["BOS", "LAL"]
    assert team_rows == repeated_team_rows
    assert opening_band_rows == repeated_opening_band_rows

    home_team_row = next(row for row in team_rows if row["team_slug"] == "BOS")
    away_team_row = next(row for row in team_rows if row["team_slug"] == "LAL")

    assert home_team_row["sample_games"] == 1
    assert home_team_row["wins"] == 1
    assert home_team_row["avg_total_swing"] == pytest.approx(0.38)
    assert home_team_row["opening_price_trend_slope"] is None
    assert home_team_row["rolling_10_json"]["latest"]["window_sample_games"] == 1
    assert away_team_row["avg_underdog_spike"] == pytest.approx(0.14)
    assert away_team_row["avg_favorite_drawdown"] is None


def test_aggregate_profile_builders_ignore_non_research_ready_rows_pytest() -> None:
    computed_at = datetime(2026, 2, 24, tzinfo=timezone.utc)
    profiles_df = mod.pd.DataFrame(
        [
            {
                "game_id": "game-ready-home",
                "team_side": "home",
                "team_id": 1,
                "team_slug": "BOS",
                "game_date": datetime(2026, 2, 22).date(),
                "research_ready_flag": True,
                "final_winner_flag": True,
                "opening_price": 0.72,
                "closing_price": 0.80,
                "opening_band": "70-80",
                "pregame_price_range": 0.04,
                "ingame_price_range": 0.20,
                "total_swing": 0.20,
                "max_favorable_excursion": 0.08,
                "max_adverse_excursion": 0.02,
                "inversion_count": 0,
                "seconds_above_50c": 100.0,
                "seconds_below_50c": 0.0,
                "winner_stable_70_clock_elapsed_seconds": 120.0,
                "winner_stable_80_clock_elapsed_seconds": None,
                "winner_stable_90_clock_elapsed_seconds": None,
                "winner_stable_95_clock_elapsed_seconds": None,
            },
            {
                "game_id": "game-descriptive-home",
                "team_side": "home",
                "team_id": 1,
                "team_slug": "BOS",
                "game_date": datetime(2026, 2, 23).date(),
                "research_ready_flag": False,
                "final_winner_flag": False,
                "opening_price": 0.42,
                "closing_price": None,
                "opening_band": "40-50",
                "pregame_price_range": 0.02,
                "ingame_price_range": None,
                "total_swing": 0.10,
                "max_favorable_excursion": 0.01,
                "max_adverse_excursion": 0.09,
                "inversion_count": 1,
                "seconds_above_50c": None,
                "seconds_below_50c": None,
                "winner_stable_70_clock_elapsed_seconds": None,
                "winner_stable_80_clock_elapsed_seconds": None,
                "winner_stable_90_clock_elapsed_seconds": None,
                "winner_stable_95_clock_elapsed_seconds": None,
            },
            {
                "game_id": "game-ready-away",
                "team_side": "away",
                "team_id": 2,
                "team_slug": "LAL",
                "game_date": datetime(2026, 2, 22).date(),
                "research_ready_flag": True,
                "final_winner_flag": False,
                "opening_price": 0.28,
                "closing_price": 0.20,
                "opening_band": "20-30",
                "pregame_price_range": 0.04,
                "ingame_price_range": 0.20,
                "total_swing": 0.20,
                "max_favorable_excursion": 0.02,
                "max_adverse_excursion": 0.08,
                "inversion_count": 0,
                "seconds_above_50c": 0.0,
                "seconds_below_50c": 100.0,
                "winner_stable_70_clock_elapsed_seconds": None,
                "winner_stable_80_clock_elapsed_seconds": None,
                "winner_stable_90_clock_elapsed_seconds": None,
                "winner_stable_95_clock_elapsed_seconds": None,
            },
        ]
    )
    state_df = mod.pd.DataFrame(
        [
            {"team_id": 1, "scoreboard_control_mismatch_flag": 0.0},
            {"team_id": 2, "scoreboard_control_mismatch_flag": 1.0},
        ]
    )

    team_rows = aggregates_mod.build_team_season_profile_rows(
        profiles_df,
        state_df,
        season="2025-26",
        season_phase="regular_season",
        analysis_version=mod.ANALYSIS_VERSION,
        computed_at=computed_at,
    )
    opening_band_rows = aggregates_mod.build_opening_band_profile_rows(
        profiles_df,
        season="2025-26",
        season_phase="regular_season",
        analysis_version=mod.ANALYSIS_VERSION,
        computed_at=computed_at,
    )

    bos_row = next(row for row in team_rows if row["team_slug"] == "BOS")
    assert bos_row["sample_games"] == 1
    assert bos_row["research_ready_games"] == 1
    assert bos_row["favorite_games"] == 1
    assert bos_row["underdog_games"] == 0
    assert bos_row["notes_json"]["research_ready_games"] == 1
    assert [row["opening_band"] for row in opening_band_rows] == ["20-30", "70-80"]
