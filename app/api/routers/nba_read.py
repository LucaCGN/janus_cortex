from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from psycopg2.extensions import connection as PsycopgConnection

from app.api.db import cursor_dict, fetchall_dicts, to_jsonable
from app.api.dependencies import get_db_connection


router = APIRouter(prefix="/v1/nba", tags=["nba"])


@router.get("/games")
def list_nba_games(
    game_date: date | None = Query(default=None),
    status: int | None = Query(default=None),
    live_only: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    conditions: list[str] = []
    params: list[Any] = []

    if game_date is not None:
        conditions.append("g.game_date = %s")
        params.append(game_date)
    if status is not None:
        conditions.append("g.game_status = %s")
        params.append(status)
    if live_only:
        conditions.append("g.game_status = 2")

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
                g.updated_at
            FROM nba.nba_games g
            {where_sql}
            ORDER BY g.game_date DESC, g.game_start_time DESC NULLS LAST
            LIMIT %s OFFSET %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)

    return {"items": to_jsonable(rows), "count": len(rows)}
