from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg2.extensions import connection as PsycopgConnection

from app.api.db import cursor_dict, fetchall_dicts, fetchone_dict, to_jsonable
from app.api.dependencies import get_db_connection
from app.api.models import NbaGameEventLinkCreateRequest
from app.data.databases.repositories import JanusUpsertRepository
from app.data.pipelines.daily.nba.sync_postgres import run_nba_live_game_sync
from app.modules.nba.context.service import resolve_nba_game_context


router = APIRouter(prefix="/v1/nba", tags=["nba"])


def _ensure_game_exists(connection: PsycopgConnection, game_id: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM nba.nba_games WHERE game_id = %s;", (game_id,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="game_id not found")


def _ensure_team_exists(connection: PsycopgConnection, team_id: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM nba.nba_teams WHERE team_id = %s;", (team_id,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="team_id not found")


@router.get("/games")
def list_nba_games(
    game_date: date | None = Query(default=None),
    game_date_from: date | None = Query(default=None),
    game_date_to: date | None = Query(default=None),
    season: str | None = Query(default=None),
    team_slug: str | None = Query(default=None),
    status: int | None = Query(default=None),
    live_only: bool = Query(default=False),
    finished_only: bool = Query(default=False),
    upcoming_only: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    conditions: list[str] = []
    params: list[Any] = []

    if game_date is not None:
        conditions.append("g.game_date = %s")
        params.append(game_date)
    if game_date_from is not None:
        conditions.append("g.game_date >= %s")
        params.append(game_date_from)
    if game_date_to is not None:
        conditions.append("g.game_date <= %s")
        params.append(game_date_to)
    if season:
        conditions.append("g.season = %s")
        params.append(season)
    if team_slug:
        team_slug_norm = team_slug.upper().strip()
        conditions.append("(g.home_team_slug = %s OR g.away_team_slug = %s)")
        params.extend([team_slug_norm, team_slug_norm])
    if status is not None:
        conditions.append("g.game_status = %s")
        params.append(status)
    if live_only:
        conditions.append("g.game_status = 2")
    if finished_only:
        conditions.append("g.game_status = 3")
    if upcoming_only:
        conditions.append("g.game_status = 1")

    where_sql = ""
    if conditions:
        where_sql = f"WHERE {' AND '.join(conditions)}"

    params.extend([limit, offset])
    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT
                g.game_id,
                g.season,
                g.game_date,
                g.game_start_time,
                g.game_status,
                g.game_status_text,
                g.period,
                g.game_clock,
                g.home_team_id,
                g.away_team_id,
                g.home_team_slug,
                g.away_team_slug,
                g.home_score,
                g.away_score,
                g.updated_at,
                ht.team_name AS home_team_name,
                at.team_name AS away_team_name
            FROM nba.nba_games g
            LEFT JOIN nba.nba_teams ht ON ht.team_id = g.home_team_id
            LEFT JOIN nba.nba_teams at ON at.team_id = g.away_team_id
            {where_sql}
            ORDER BY g.game_date DESC, g.game_start_time DESC NULLS LAST
            LIMIT %s OFFSET %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)

    return {"items": to_jsonable(rows), "count": len(rows)}


@router.get("/games/{game_id}")
def get_nba_game(
    game_id: str,
    include_live_snapshots: bool = Query(default=False),
    include_play_by_play: bool = Query(default=False),
    live_limit: int = Query(default=20, ge=1, le=500),
    pbp_limit: int = Query(default=200, ge=1, le=5000),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                g.game_id,
                g.season,
                g.game_date,
                g.game_start_time,
                g.game_status,
                g.game_status_text,
                g.period,
                g.game_clock,
                g.home_team_id,
                g.away_team_id,
                g.home_team_slug,
                g.away_team_slug,
                g.home_score,
                g.away_score,
                g.updated_at,
                ht.team_name AS home_team_name,
                at.team_name AS away_team_name
            FROM nba.nba_games g
            LEFT JOIN nba.nba_teams ht ON ht.team_id = g.home_team_id
            LEFT JOIN nba.nba_teams at ON at.team_id = g.away_team_id
            WHERE g.game_id = %s
            LIMIT 1;
            """,
            (game_id,),
        )
        game = fetchone_dict(cursor)
        if game is None:
            raise HTTPException(status_code=404, detail="game_id not found")

        snapshots: list[dict[str, Any]] = []
        if include_live_snapshots:
            cursor.execute(
                """
                SELECT game_id, captured_at, period, clock, home_score, away_score, payload_json
                FROM nba.nba_live_game_snapshots
                WHERE game_id = %s
                ORDER BY captured_at DESC
                LIMIT %s;
                """,
                (game_id, live_limit),
            )
            snapshots = fetchall_dicts(cursor)

        play_by_play: list[dict[str, Any]] = []
        if include_play_by_play:
            cursor.execute(
                """
                SELECT
                    game_id,
                    event_index,
                    action_id,
                    period,
                    clock,
                    description,
                    home_score,
                    away_score,
                    is_score_change,
                    payload_json
                FROM nba.nba_play_by_play
                WHERE game_id = %s
                ORDER BY event_index DESC
                LIMIT %s;
                """,
                (game_id, pbp_limit),
            )
            play_by_play = fetchall_dicts(cursor)

    return {
        "game": to_jsonable(game),
        "live_snapshots": to_jsonable(snapshots),
        "play_by_play": to_jsonable(play_by_play),
    }


@router.get("/games/{game_id}/live")
def get_nba_game_live(
    game_id: str,
    snapshot_limit: int = Query(default=20, ge=1, le=500),
    pbp_limit: int = Query(default=100, ge=1, le=5000),
    refresh_source: bool = Query(default=False),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    _ensure_game_exists(connection, game_id)
    sync_summary: dict[str, Any] | None = None
    if refresh_source:
        sync_summary = to_jsonable(
            run_nba_live_game_sync(
                game_id=game_id,
                include_live_snapshots=True,
                include_play_by_play=True,
            ).__dict__
        )

    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                game_id,
                captured_at,
                period,
                clock,
                home_score,
                away_score,
                payload_json
            FROM nba.nba_live_game_snapshots
            WHERE game_id = %s
            ORDER BY captured_at DESC
            LIMIT %s;
            """,
            (game_id, snapshot_limit),
        )
        snapshots = fetchall_dicts(cursor)
        cursor.execute(
            """
            SELECT
                game_id,
                event_index,
                action_id,
                period,
                clock,
                description,
                home_score,
                away_score,
                is_score_change,
                payload_json
            FROM nba.nba_play_by_play
            WHERE game_id = %s
            ORDER BY event_index DESC
            LIMIT %s;
            """,
            (game_id, pbp_limit),
        )
        pbp_rows = fetchall_dicts(cursor)
    return {
        "game_id": game_id,
        "latest_snapshot": to_jsonable(snapshots[0] if snapshots else None),
        "snapshots": to_jsonable(snapshots),
        "recent_play_by_play": to_jsonable(pbp_rows),
        "sync_summary": sync_summary,
    }


@router.get("/games/{game_id}/play-by-play")
def get_nba_game_play_by_play(
    game_id: str,
    period: int | None = Query(default=None, ge=1, le=20),
    since_event_index: int | None = Query(default=None, ge=0),
    descending: bool = Query(default=True),
    limit: int = Query(default=500, ge=1, le=5000),
    refresh_source: bool = Query(default=False),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    _ensure_game_exists(connection, game_id)
    sync_summary: dict[str, Any] | None = None
    if refresh_source:
        sync_summary = to_jsonable(
            run_nba_live_game_sync(
                game_id=game_id,
                include_live_snapshots=False,
                include_play_by_play=True,
            ).__dict__
        )

    conditions = ["game_id = %s"]
    params: list[Any] = [game_id]
    if period is not None:
        conditions.append("period = %s")
        params.append(period)
    if since_event_index is not None:
        conditions.append("event_index > %s")
        params.append(since_event_index)
    order_sql = "DESC" if descending else "ASC"
    params.append(limit)

    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT
                game_id,
                event_index,
                action_id,
                period,
                clock,
                description,
                home_score,
                away_score,
                is_score_change,
                payload_json
            FROM nba.nba_play_by_play
            WHERE {' AND '.join(conditions)}
            ORDER BY event_index {order_sql}
            LIMIT %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    return {
        "items": to_jsonable(rows),
        "count": len(rows),
        "sync_summary": sync_summary,
    }


@router.get("/games/{game_id}/features/latest")
def get_nba_game_latest_features(
    game_id: str,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    _ensure_game_exists(connection, game_id)
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                f.game_id,
                f.event_id,
                f.computed_at,
                f.feature_version,
                f.season,
                f.team_context_mode,
                f.pbp_event_count,
                f.lead_changes,
                f.home_largest_lead,
                f.away_largest_lead,
                f.home_losing_segments,
                f.away_losing_segments,
                f.home_led_and_lost,
                f.away_led_and_lost,
                f.covered_polymarket_game_flag,
                f.home_pre_game_price_min,
                f.home_pre_game_price_max,
                f.away_pre_game_price_min,
                f.away_pre_game_price_max,
                f.home_in_game_price_min,
                f.home_in_game_price_max,
                f.away_in_game_price_min,
                f.away_in_game_price_max,
                f.price_window_start,
                f.price_window_end,
                f.coverage_status,
                f.source_summary_json
            FROM nba.nba_game_feature_snapshots f
            WHERE f.game_id = %s
            ORDER BY f.computed_at DESC
            LIMIT 1;
            """,
            (game_id,),
        )
        row = fetchone_dict(cursor)
    if row is None:
        raise HTTPException(status_code=404, detail="game features not found")
    return to_jsonable(row)


@router.get("/seasons/{season}/games/features")
def list_nba_season_game_features(
    season: str,
    coverage_status: str | None = Query(default=None),
    team_slug: str | None = Query(default=None),
    game_status: int | None = Query(default=None),
    covered_only: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    conditions = ["x.season = %s"]
    params: list[Any] = [season]
    if coverage_status:
        conditions.append("x.coverage_status = %s")
        params.append(coverage_status)
    if team_slug:
        team_slug_norm = team_slug.upper().strip()
        conditions.append("(g.home_team_slug = %s OR g.away_team_slug = %s)")
        params.extend([team_slug_norm, team_slug_norm])
    if game_status is not None:
        conditions.append("g.game_status = %s")
        params.append(game_status)
    if covered_only:
        conditions.append("x.covered_polymarket_game_flag = TRUE")
    params.extend([limit, offset])

    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT
                x.game_id,
                x.event_id,
                x.computed_at,
                x.feature_version,
                x.season,
                x.team_context_mode,
                x.pbp_event_count,
                x.lead_changes,
                x.home_largest_lead,
                x.away_largest_lead,
                x.home_losing_segments,
                x.away_losing_segments,
                x.home_led_and_lost,
                x.away_led_and_lost,
                x.covered_polymarket_game_flag,
                x.home_pre_game_price_min,
                x.home_pre_game_price_max,
                x.away_pre_game_price_min,
                x.away_pre_game_price_max,
                x.home_in_game_price_min,
                x.home_in_game_price_max,
                x.away_in_game_price_min,
                x.away_in_game_price_max,
                x.price_window_start,
                x.price_window_end,
                x.coverage_status,
                x.source_summary_json,
                g.game_date,
                g.game_start_time,
                g.game_status,
                g.home_team_slug,
                g.away_team_slug,
                g.home_score,
                g.away_score
            FROM (
                SELECT DISTINCT ON (f.game_id)
                    f.*
                FROM nba.nba_game_feature_snapshots f
                WHERE f.season = %s
                ORDER BY f.game_id, f.computed_at DESC
            ) x
            JOIN nba.nba_games g ON g.game_id = x.game_id
            WHERE {' AND '.join(conditions)}
            ORDER BY g.game_date DESC NULLS LAST, g.game_start_time DESC NULLS LAST
            LIMIT %s OFFSET %s;
            """,
            tuple([season, *params]),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.get("/seasons/{season}/odds-coverage")
def list_nba_season_odds_coverage(
    season: str,
    game_id: str | None = Query(default=None),
    coverage_status: str | None = Query(default=None),
    issue_code: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    conditions = ["a.season = %s"]
    params: list[Any] = [season]
    if game_id:
        conditions.append("a.game_id = %s")
        params.append(game_id)
    if coverage_status:
        conditions.append("a.coverage_status = %s")
        params.append(coverage_status)
    if issue_code:
        conditions.append("a.issue_code = %s")
        params.append(issue_code)
    params.extend([limit, offset])

    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT
                a.odds_coverage_audit_id,
                a.season,
                a.game_id,
                a.event_id,
                a.market_id,
                a.outcome_id,
                a.audited_at,
                a.coverage_scope,
                a.coverage_status,
                a.history_points,
                a.fallback_points,
                a.window_start,
                a.window_end,
                a.issue_code,
                a.details_json
            FROM nba.nba_odds_coverage_audits a
            WHERE {' AND '.join(conditions)}
            ORDER BY a.audited_at DESC, a.game_id ASC
            LIMIT %s OFFSET %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.get("/seasons/{season}/teams/feature-rollups")
def list_nba_season_team_feature_rollups(
    season: str,
    classification_tag: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    conditions = ["x.season = %s"]
    params: list[Any] = [season]
    if classification_tag:
        conditions.append("(x.classification_tags_json -> 'tags') ? %s")
        params.append(classification_tag)
    params.extend([limit, offset])

    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT
                x.team_id,
                t.team_slug,
                t.team_name,
                x.season,
                x.computed_at,
                x.feature_version,
                x.sample_games,
                x.covered_games,
                x.wins,
                x.losses,
                x.avg_lead_changes,
                x.avg_losing_segments,
                x.avg_largest_lead_in_losses,
                x.losses_after_leading,
                x.underdog_games_with_coverage,
                x.favorite_games_with_coverage,
                x.avg_underdog_in_game_range,
                x.avg_favorite_in_game_range,
                x.classification_tags_json,
                x.notes_json
            FROM (
                SELECT DISTINCT ON (r.team_id)
                    r.*
                FROM nba.nba_team_feature_rollups r
                WHERE r.season = %s
                ORDER BY r.team_id, r.computed_at DESC
            ) x
            JOIN nba.nba_teams t ON t.team_id = x.team_id
            WHERE {' AND '.join(conditions)}
            ORDER BY x.sample_games DESC, t.team_slug ASC
            LIMIT %s OFFSET %s;
            """,
            tuple([season, *params]),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.get("/teams/{team_id}/feature-rollups")
def get_nba_team_feature_rollups(
    team_id: int,
    season: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    _ensure_team_exists(connection, team_id)
    conditions = ["r.team_id = %s"]
    params: list[Any] = [team_id]
    if season:
        conditions.append("r.season = %s")
        params.append(season)
    params.append(limit)

    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT
                r.team_id,
                t.team_slug,
                t.team_name,
                r.season,
                r.computed_at,
                r.feature_version,
                r.sample_games,
                r.covered_games,
                r.wins,
                r.losses,
                r.avg_lead_changes,
                r.avg_losing_segments,
                r.avg_largest_lead_in_losses,
                r.losses_after_leading,
                r.underdog_games_with_coverage,
                r.favorite_games_with_coverage,
                r.avg_underdog_in_game_range,
                r.avg_favorite_in_game_range,
                r.classification_tags_json,
                r.notes_json
            FROM nba.nba_team_feature_rollups r
            JOIN nba.nba_teams t ON t.team_id = r.team_id
            WHERE {' AND '.join(conditions)}
            ORDER BY r.computed_at DESC
            LIMIT %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.get("/games/{game_id}/context/pre")
def get_nba_game_pre_context(
    game_id: str,
    refresh_cache: bool = Query(default=False),
    refresh_source: bool = Query(default=False),
    persist_cache: bool = Query(default=True),
    snapshot_limit: int = Query(default=10, ge=1, le=200),
    pbp_limit: int = Query(default=100, ge=1, le=2000),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    _ensure_game_exists(connection, game_id)
    if refresh_source:
        _ = run_nba_live_game_sync(
            game_id=game_id,
            include_live_snapshots=True,
            include_play_by_play=False,
        )
    try:
        payload = resolve_nba_game_context(
            connection,
            game_id=game_id,
            context_type="pre",
            refresh=refresh_cache,
            persist=persist_cache,
            snapshot_limit=snapshot_limit,
            pbp_limit=pbp_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return to_jsonable(payload)


@router.get("/games/{game_id}/context/live")
def get_nba_game_live_context(
    game_id: str,
    refresh_cache: bool = Query(default=False),
    refresh_source: bool = Query(default=False),
    persist_cache: bool = Query(default=True),
    snapshot_limit: int = Query(default=20, ge=1, le=200),
    pbp_limit: int = Query(default=200, ge=1, le=5000),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    _ensure_game_exists(connection, game_id)
    if refresh_source:
        _ = run_nba_live_game_sync(
            game_id=game_id,
            include_live_snapshots=True,
            include_play_by_play=True,
        )
    try:
        payload = resolve_nba_game_context(
            connection,
            game_id=game_id,
            context_type="live",
            refresh=refresh_cache,
            persist=persist_cache,
            snapshot_limit=snapshot_limit,
            pbp_limit=pbp_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return to_jsonable(payload)


@router.get("/games/{game_id}/event-links")
def list_nba_game_event_links(
    game_id: str,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                l.nba_game_event_link_id,
                l.game_id,
                l.event_id,
                l.confidence,
                l.linked_by,
                l.linked_at,
                e.title AS event_title,
                e.canonical_slug,
                e.status AS event_status,
                e.start_time AS event_start_time
            FROM nba.nba_game_event_links l
            JOIN catalog.events e ON e.event_id = l.event_id
            WHERE l.game_id = %s
            ORDER BY l.linked_at DESC;
            """,
            (game_id,),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.post("/games/{game_id}/event-links")
def create_nba_game_event_link(
    game_id: str,
    payload: NbaGameEventLinkCreateRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM nba.nba_games WHERE game_id = %s;", (game_id,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="game_id not found")
        cursor.execute("SELECT 1 FROM catalog.events WHERE event_id = %s;", (str(payload.event_id),))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="event_id not found")

    repo = JanusUpsertRepository(connection)
    link_id = repo.upsert_nba_game_event_link(
        nba_game_event_link_id=str(uuid4()),
        game_id=game_id,
        event_id=str(payload.event_id),
        confidence=payload.confidence,
        linked_by=(payload.linked_by or "api_manual"),
    )
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                l.nba_game_event_link_id,
                l.game_id,
                l.event_id,
                l.confidence,
                l.linked_by,
                l.linked_at,
                e.title AS event_title,
                e.canonical_slug,
                e.status AS event_status,
                e.start_time AS event_start_time
            FROM nba.nba_game_event_links l
            JOIN catalog.events e ON e.event_id = l.event_id
            WHERE l.nba_game_event_link_id = %s
            LIMIT 1;
            """,
            (link_id,),
        )
        row = fetchone_dict(cursor)
    if row is None:
        raise HTTPException(status_code=500, detail="game event link was not persisted")
    return to_jsonable(row)


@router.get("/teams")
def list_nba_teams(
    team_slug: str | None = Query(default=None),
    conference: str | None = Query(default=None),
    division: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    conditions: list[str] = []
    params: list[Any] = []
    if team_slug:
        conditions.append("t.team_slug = %s")
        params.append(team_slug.upper().strip())
    if conference:
        conditions.append("t.conference = %s")
        params.append(conference)
    if division:
        conditions.append("t.division = %s")
        params.append(division)
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT
                t.team_id,
                t.team_slug,
                t.team_name,
                t.team_city,
                t.conference,
                t.division,
                t.metadata_json,
                t.created_at,
                t.updated_at
            FROM nba.nba_teams t
            {where_sql}
            ORDER BY t.team_slug ASC
            LIMIT %s OFFSET %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.get("/teams/{team_id}/stats")
def list_nba_team_stats(
    team_id: int,
    season: str | None = Query(default=None),
    metric_set: str | None = Query(default=None),
    latest_only: bool = Query(default=True),
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM nba.nba_teams WHERE team_id = %s;", (team_id,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="team_id not found")

    conditions: list[str] = ["s.team_id = %s"]
    params: list[Any] = [team_id]
    if season:
        conditions.append("s.season = %s")
        params.append(season)
    if metric_set:
        conditions.append("s.metric_set = %s")
        params.append(metric_set)
    where_sql = f"WHERE {' AND '.join(conditions)}"

    with cursor_dict(connection) as cursor:
        if latest_only:
            params.extend([limit, offset])
            cursor.execute(
                f"""
                SELECT
                    x.team_id,
                    x.season,
                    x.captured_at,
                    x.metric_set,
                    x.stats_json,
                    x.source
                FROM (
                    SELECT DISTINCT ON (s.season, s.metric_set)
                        s.team_id,
                        s.season,
                        s.captured_at,
                        s.metric_set,
                        s.stats_json,
                        s.source
                    FROM nba.nba_team_stats_snapshots s
                    {where_sql}
                    ORDER BY s.season DESC, s.metric_set ASC, s.captured_at DESC
                ) x
                ORDER BY x.season DESC, x.metric_set ASC, x.captured_at DESC
                LIMIT %s OFFSET %s;
                """,
                tuple(params),
            )
        else:
            params.extend([limit, offset])
            cursor.execute(
                f"""
                SELECT
                    s.team_id,
                    s.season,
                    s.captured_at,
                    s.metric_set,
                    s.stats_json,
                    s.source
                FROM nba.nba_team_stats_snapshots s
                {where_sql}
                ORDER BY s.captured_at DESC
                LIMIT %s OFFSET %s;
                """,
                tuple(params),
            )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.get("/teams/{team_id}/insights")
def list_nba_team_insights(
    team_id: int,
    insight_type: str | None = Query(default=None),
    category: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM nba.nba_teams WHERE team_id = %s;", (team_id,))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="team_id not found")

    conditions: list[str] = ["i.team_id = %s"]
    params: list[Any] = [team_id]
    if insight_type:
        conditions.append("i.insight_type = %s")
        params.append(insight_type)
    if category:
        conditions.append("i.category = %s")
        params.append(category)
    where_sql = f"WHERE {' AND '.join(conditions)}"
    params.extend([limit, offset])

    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT
                i.insight_id,
                i.team_id,
                i.insight_type,
                i.category,
                i.text,
                i.condition,
                i.value,
                i.source,
                i.captured_at
            FROM nba.nba_team_insights i
            {where_sql}
            ORDER BY i.captured_at DESC
            LIMIT %s OFFSET %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.get("/players")
def list_nba_players(
    season: str | None = Query(default=None),
    metric_set: str | None = Query(default=None),
    team_id: int | None = Query(default=None),
    latest_only: bool = Query(default=True),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    conditions: list[str] = []
    params: list[Any] = []
    if season:
        conditions.append("s.season = %s")
        params.append(season)
    if metric_set:
        conditions.append("s.metric_set = %s")
        params.append(metric_set)
    if team_id is not None:
        conditions.append("s.team_id = %s")
        params.append(team_id)
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    with cursor_dict(connection) as cursor:
        if latest_only:
            cursor.execute(
                f"""
                SELECT
                    x.player_id,
                    x.player_name,
                    x.team_id,
                    t.team_slug,
                    t.team_name,
                    x.season,
                    x.metric_set,
                    x.captured_at,
                    x.stats_json,
                    x.source
                FROM (
                    SELECT DISTINCT ON (s.player_id)
                        s.player_id,
                        s.player_name,
                        s.team_id,
                        s.season,
                        s.metric_set,
                        s.captured_at,
                        s.stats_json,
                        s.source
                    FROM nba.nba_player_stats_snapshots s
                    {where_sql}
                    ORDER BY s.player_id, s.captured_at DESC
                ) x
                LEFT JOIN nba.nba_teams t ON t.team_id = x.team_id
                ORDER BY x.captured_at DESC
                LIMIT %s OFFSET %s;
                """,
                tuple(params),
            )
        else:
            cursor.execute(
                f"""
                SELECT
                    s.player_id,
                    s.player_name,
                    s.team_id,
                    t.team_slug,
                    t.team_name,
                    s.season,
                    s.metric_set,
                    s.captured_at,
                    s.stats_json,
                    s.source
                FROM nba.nba_player_stats_snapshots s
                LEFT JOIN nba.nba_teams t ON t.team_id = s.team_id
                {where_sql}
                ORDER BY s.captured_at DESC
                LIMIT %s OFFSET %s;
                """,
                tuple(params),
            )
        rows = fetchall_dicts(cursor)

    return {"items": to_jsonable(rows), "count": len(rows)}
