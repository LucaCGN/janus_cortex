from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.data.databases import migrate
from app.data.databases.postgres import ensure_database_exists, managed_connection
from app.data.databases.repositories import JanusUpsertRepository
import app.data.pipelines.daily.nba.analysis_module as mod


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


def test_opening_band_and_completeness_helpers_pytest() -> None:
    label, rank = mod._opening_band_for_price(0.72)
    assert label == "70-80"
    assert rank == 7

    completeness = mod._build_completeness_report(
        mod.pd.DataFrame(
            [
                {"coverage_status": "covered_pre_and_ingame", "home_team_slug": "BOS", "away_team_slug": "LAL", "game_date": "2026-02-22"},
                {"coverage_status": "no_matching_event", "home_team_slug": "NYK", "away_team_slug": "CHA", "game_date": "2026-02-23"},
            ]
        )
    )
    assert completeness["games_total"] == 2
    assert completeness["research_ready_games"] == 1
    assert completeness["descriptive_only_games"] == 1
    assert completeness["coverage_status_counts"]["no_matching_event"] == 1


def test_build_analysis_mart_and_reports_pytest(tmp_path: Path) -> None:
    with managed_connection() as connection:
        repo = JanusUpsertRepository(connection)
        _seed_catalog_and_game(repo, game_id="002TEST0001")

    mart_summary = mod.build_analysis_mart(
        mod.AnalysisMartBuildRequest(
            season="2025-26",
            season_phase="regular_season",
            rebuild=True,
            game_ids=["002TEST0001"],
            output_root=str(tmp_path),
        )
    )
    assert mart_summary["games_considered"] == 1
    assert mart_summary["research_ready_games"] == 1
    assert mart_summary["game_team_profiles_written"] == 2
    assert mart_summary["state_rows_written"] == 10
    assert Path(mart_summary["artifacts"]["universe_json"]).exists()

    with managed_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM nba.nba_analysis_game_team_profiles;")
            assert int(cursor.fetchone()[0]) == 2
            cursor.execute("SELECT count(*) FROM nba.nba_analysis_state_panel;")
            assert int(cursor.fetchone()[0]) == 10
            cursor.execute("SELECT count(*) FROM nba.nba_analysis_team_season_profiles;")
            assert int(cursor.fetchone()[0]) == 2
            cursor.execute("SELECT count(*) FROM nba.nba_analysis_opening_band_profiles;")
            assert int(cursor.fetchone()[0]) >= 1

    report_summary = mod.build_analysis_report(
        season="2025-26",
        season_phase="regular_season",
        analysis_version=mod.ANALYSIS_VERSION,
        output_root=str(tmp_path),
    )
    assert "teams_against_expectation" in report_summary
    assert Path(report_summary["artifacts"]["json"]).exists()
    assert Path(report_summary["artifacts"]["markdown"]).exists()
    report_json_payload = json.loads(Path(report_summary["artifacts"]["json"]).read_text(encoding="utf-8"))
    assert report_json_payload["artifacts"]["json"] == report_summary["artifacts"]["json"]
    assert "sections" in report_json_payload["artifacts"]

    backtest_summary = mod.run_analysis_backtests(
        mod.BacktestRunRequest(
            season="2025-26",
            season_phase="regular_season",
            strategy_family="all",
            slippage_cents=1,
            output_root=str(tmp_path),
        )
    )
    assert "families" in backtest_summary
    assert backtest_summary["families"]["reversion"]["trade_count"] >= 1
    assert Path(backtest_summary["artifacts"]["json"]).exists()

    model_summary = mod.train_analysis_baselines(
        mod.ModelRunRequest(
            season="2025-26",
            season_phase="regular_season",
            target_family="all",
            output_root=str(tmp_path),
        )
    )
    assert "tracks" in model_summary
    assert Path(model_summary["artifacts"]["json"]).exists()
