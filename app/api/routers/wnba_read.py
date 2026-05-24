from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from psycopg2.extensions import connection as PsycopgConnection

from app.api.db import cursor_dict, fetchall_dicts, to_jsonable
from app.api.dependencies import get_db_connection
from app.data.pipelines.daily.wnba.sync_postgres import run_wnba_live_game_sync


router = APIRouter(prefix="/v1/wnba", tags=["wnba-read"])


@router.get("/games/{game_id}/live")
def get_wnba_game_live(
    game_id: str,
    snapshot_limit: int = Query(default=20, ge=1, le=200),
    pbp_limit: int = Query(default=80, ge=1, le=1000),
    refresh_source: bool = Query(default=False),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    sync_summary: dict[str, Any] | None = None
    if refresh_source:
        sync_summary = to_jsonable(
            run_wnba_live_game_sync(
                game_id=game_id,
                include_live_snapshots=True,
                include_boxscore=True,
                include_play_by_play=True,
            ).__dict__
        )

    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                game_id,
                captured_at,
                game_status,
                game_status_text,
                period,
                clock,
                home_team_tricode,
                away_team_tricode,
                home_score,
                away_score,
                normalized_json,
                raw_payload_json
            FROM wnba.wnba_live_game_snapshots
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
                action_number,
                time_actual,
                period,
                clock,
                action_type,
                sub_type,
                description,
                home_score,
                away_score,
                is_score_change,
                raw_payload_json
            FROM wnba.wnba_play_by_play
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


__all__ = ["router"]
