from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd
from psycopg2.extensions import connection as PsycopgConnection
from psycopg2.extras import Json, RealDictCursor

from app.data.nodes.nba.live.play_by_play import compute_runs


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _fetchone_dict(cursor: RealDictCursor) -> dict[str, Any] | None:
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(row)


def _fetchall_dicts(cursor: RealDictCursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


def get_latest_cached_context(
    connection: PsycopgConnection,
    *,
    game_id: str,
    context_type: str,
) -> dict[str, Any] | None:
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            """
            SELECT payload_json
            FROM nba.nba_context_cache
            WHERE game_id = %s
              AND context_type = %s
            ORDER BY generated_at DESC
            LIMIT 1;
            """,
            (game_id, context_type),
        )
        row = _fetchone_dict(cursor)
    if row is None:
        return None
    payload = row.get("payload_json")
    return payload if isinstance(payload, dict) else None


def persist_context_cache(
    connection: PsycopgConnection,
    *,
    game_id: str,
    context_type: str,
    payload: dict[str, Any],
) -> None:
    payload_jsonable = _to_jsonable(payload)
    generated_at = payload.get("generated_at")
    if isinstance(generated_at, str):
        try:
            generated_at_dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            generated_at_dt = datetime.now(timezone.utc)
    elif isinstance(generated_at, datetime):
        generated_at_dt = generated_at.astimezone(timezone.utc)
    else:
        generated_at_dt = datetime.now(timezone.utc)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO nba.nba_context_cache (
                game_id,
                context_type,
                generated_at,
                payload_json
            ) VALUES (%s, %s, %s, %s);
            """,
            (game_id, context_type, generated_at_dt, Json(payload_jsonable)),
        )


def _fetch_game(connection: PsycopgConnection, *, game_id: str) -> dict[str, Any] | None:
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
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
                ht.team_city AS home_team_city,
                at.team_name AS away_team_name,
                at.team_city AS away_team_city
            FROM nba.nba_games g
            LEFT JOIN nba.nba_teams ht ON ht.team_id = g.home_team_id
            LEFT JOIN nba.nba_teams at ON at.team_id = g.away_team_id
            WHERE g.game_id = %s
            LIMIT 1;
            """,
            (game_id,),
        )
        return _fetchone_dict(cursor)


def _fetch_latest_team_stats(
    connection: PsycopgConnection,
    *,
    team_id: int | None,
) -> list[dict[str, Any]]:
    if team_id is None:
        return []
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            """
            SELECT
                x.season,
                x.metric_set,
                x.captured_at,
                x.stats_json,
                x.source
            FROM (
                SELECT DISTINCT ON (s.metric_set)
                    s.season,
                    s.metric_set,
                    s.captured_at,
                    s.stats_json,
                    s.source
                FROM nba.nba_team_stats_snapshots s
                WHERE s.team_id = %s
                ORDER BY s.metric_set, s.captured_at DESC
            ) x
            ORDER BY x.metric_set ASC;
            """,
            (team_id,),
        )
        return _fetchall_dicts(cursor)


def _fetch_recent_team_insights(
    connection: PsycopgConnection,
    *,
    team_id: int | None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    if team_id is None:
        return []
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            """
            SELECT
                insight_id,
                insight_type,
                category,
                text,
                condition,
                value,
                source,
                captured_at
            FROM nba.nba_team_insights
            WHERE team_id = %s
            ORDER BY captured_at DESC
            LIMIT %s;
            """,
            (team_id, limit),
        )
        return _fetchall_dicts(cursor)


def _fetch_live_snapshots(
    connection: PsycopgConnection,
    *,
    game_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
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
            (game_id, limit),
        )
        return _fetchall_dicts(cursor)


def _fetch_play_by_play(
    connection: PsycopgConnection,
    *,
    game_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
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
            (game_id, limit),
        )
        rows = _fetchall_dicts(cursor)
    rows.reverse()
    return rows


def _fetch_linked_events(
    connection: PsycopgConnection,
    *,
    game_id: str,
    outcome_limit: int = 12,
) -> list[dict[str, Any]]:
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(
            """
            SELECT
                l.nba_game_event_link_id,
                l.confidence,
                l.linked_by,
                l.linked_at,
                e.event_id,
                e.title,
                e.canonical_slug,
                e.status,
                e.start_time,
                e.end_time,
                (
                    SELECT count(*)
                    FROM catalog.markets m
                    WHERE m.event_id = e.event_id
                ) AS market_count,
                (
                    SELECT count(*)
                    FROM catalog.outcomes o
                    JOIN catalog.markets m ON m.market_id = o.market_id
                    WHERE m.event_id = e.event_id
                ) AS outcome_count
            FROM nba.nba_game_event_links l
            JOIN catalog.events e ON e.event_id = l.event_id
            WHERE l.game_id = %s
            ORDER BY l.linked_at DESC;
            """,
            (game_id,),
        )
        events = _fetchall_dicts(cursor)

        for event in events:
            cursor.execute(
                """
                SELECT
                    m.market_id,
                    m.question,
                    m.market_slug,
                    m.market_type,
                    m.settlement_status,
                    o.outcome_id,
                    o.outcome_label,
                    o.outcome_index,
                    latest_tick.ts AS latest_ts,
                    latest_tick.price AS latest_price
                FROM catalog.markets m
                JOIN catalog.outcomes o ON o.market_id = m.market_id
                LEFT JOIN LATERAL (
                    SELECT ts, price
                    FROM market_data.outcome_price_ticks t
                    WHERE t.outcome_id = o.outcome_id
                    ORDER BY ts DESC
                    LIMIT 1
                ) latest_tick ON TRUE
                WHERE m.event_id = %s
                ORDER BY m.question ASC, o.outcome_index ASC
                LIMIT %s;
                """,
                (event["event_id"], outcome_limit),
            )
            event["outcomes_preview"] = _fetchall_dicts(cursor)
    return events


def _summarize_play_by_play_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "events_count": 0,
            "score_change_events": 0,
            "latest_event_index": None,
            "latest_score": None,
            "max_home_lead": None,
            "max_away_lead": None,
            "largest_runs": [],
        }

    df = pd.DataFrame(rows).sort_values(by="event_index").reset_index(drop=True)
    df["home_score"] = pd.to_numeric(df["home_score"], errors="coerce").ffill().fillna(0)
    df["away_score"] = pd.to_numeric(df["away_score"], errors="coerce").ffill().fillna(0)
    df["points_home"] = df["home_score"].diff().fillna(df["home_score"]).clip(lower=0)
    df["points_away"] = df["away_score"].diff().fillna(df["away_score"]).clip(lower=0)
    lead_series = df["home_score"] - df["away_score"]
    runs_df = compute_runs(df, lookback_actions=min(30, len(df)))

    latest_row = df.iloc[-1]
    return {
        "events_count": int(len(df)),
        "score_change_events": int(df["is_score_change"].fillna(False).astype(bool).sum()),
        "latest_event_index": int(latest_row["event_index"]),
        "latest_score": {
            "home": int(latest_row["home_score"]),
            "away": int(latest_row["away_score"]),
        },
        "max_home_lead": int(max(0, lead_series.max())),
        "max_away_lead": int(max(0, (-lead_series).max())),
        "largest_runs": runs_df.head(3).to_dict(orient="records") if not runs_df.empty else [],
    }


def _team_payload(
    connection: PsycopgConnection,
    *,
    team_id: int | None,
    team_slug: str | None,
    team_name: str | None,
    team_city: str | None,
) -> dict[str, Any]:
    return {
        "team_id": team_id,
        "team_slug": team_slug,
        "team_name": team_name,
        "team_city": team_city,
        "latest_stats": _fetch_latest_team_stats(connection, team_id=team_id),
        "recent_insights": _fetch_recent_team_insights(connection, team_id=team_id),
    }


def build_nba_game_context(
    connection: PsycopgConnection,
    *,
    game_id: str,
    context_type: str,
    snapshot_limit: int = 20,
    pbp_limit: int = 200,
) -> dict[str, Any]:
    game = _fetch_game(connection, game_id=game_id)
    if game is None:
        raise ValueError("game_id not found")

    live_snapshots = _fetch_live_snapshots(connection, game_id=game_id, limit=snapshot_limit)
    play_by_play = _fetch_play_by_play(connection, game_id=game_id, limit=pbp_limit)
    linked_events = _fetch_linked_events(connection, game_id=game_id)

    payload = {
        "context_type": context_type,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "game": game,
        "teams": {
            "home": _team_payload(
                connection,
                team_id=game.get("home_team_id"),
                team_slug=game.get("home_team_slug"),
                team_name=game.get("home_team_name"),
                team_city=game.get("home_team_city"),
            ),
            "away": _team_payload(
                connection,
                team_id=game.get("away_team_id"),
                team_slug=game.get("away_team_slug"),
                team_name=game.get("away_team_name"),
                team_city=game.get("away_team_city"),
            ),
        },
        "linked_events": linked_events,
        "live_window": {
            "snapshot_count": len(live_snapshots),
            "latest_snapshot": live_snapshots[0] if live_snapshots else None,
            "snapshots": live_snapshots,
        },
        "play_by_play_window": {
            "event_count": len(play_by_play),
            "recent_events": play_by_play[-25:],
            "summary": _summarize_play_by_play_rows(play_by_play),
        },
    }

    if context_type == "pre":
        payload["live_window"]["snapshots"] = live_snapshots[:5]
        payload["play_by_play_window"]["recent_events"] = []
    return payload


def resolve_nba_game_context(
    connection: PsycopgConnection,
    *,
    game_id: str,
    context_type: str,
    refresh: bool = False,
    persist: bool = True,
    snapshot_limit: int = 20,
    pbp_limit: int = 200,
) -> dict[str, Any]:
    if not refresh:
        cached = get_latest_cached_context(
            connection,
            game_id=game_id,
            context_type=context_type,
        )
        if cached is not None:
            cached["cache_source"] = "cache"
            return cached

    payload = build_nba_game_context(
        connection,
        game_id=game_id,
        context_type=context_type,
        snapshot_limit=snapshot_limit,
        pbp_limit=pbp_limit,
    )
    payload = _to_jsonable(payload)
    payload["cache_source"] = "fresh"
    if persist:
        persist_context_cache(
            connection,
            game_id=game_id,
            context_type=context_type,
            payload=payload,
        )
    return payload
