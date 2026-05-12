from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from app.data.nodes.wnba.cdn_client import (
    WNBA_TODAYS_SCOREBOARD_URL,
    fetch_wnba_cdn_json,
    wnba_boxscore_url,
)

logger = logging.getLogger(__name__)


class WnbaLiveStatsRequest(BaseModel):
    game_id: str = Field(..., description="Official WNBA game id.")
    include_players: bool = True
    as_of: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class WnbaBoxscoreFrames:
    snapshot: dict[str, Any]
    teams: pd.DataFrame
    players: pd.DataFrame


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "y"}:
        return True
    if lowered in {"0", "false", "no", "n"}:
        return False
    return None


def _safe_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _team_stats_row(
    *,
    game_id: str,
    team_side: str,
    team: dict[str, Any],
    snapshot: dict[str, Any],
    fetched_at: datetime,
    source: str,
) -> dict[str, Any]:
    stats = team.get("statistics") if isinstance(team.get("statistics"), dict) else {}
    return {
        "game_id": game_id,
        "team_id": _safe_int(team.get("teamId")),
        "team_side": team_side,
        "team_tricode": str(team.get("teamTricode") or "").upper() or None,
        "team_name": team.get("teamName"),
        "team_city": team.get("teamCity"),
        "period": snapshot.get("period"),
        "clock": snapshot.get("clock"),
        "minutes": stats.get("minutes"),
        "points": _safe_int(stats.get("points") if stats else team.get("score")),
        "rebounds": _safe_int(stats.get("reboundsTotal")),
        "assists": _safe_int(stats.get("assists")),
        "steals": _safe_int(stats.get("steals")),
        "blocks": _safe_int(stats.get("blocks")),
        "turnovers": _safe_int(stats.get("turnoversTotal") if "turnoversTotal" in stats else stats.get("turnovers")),
        "fgm": _safe_int(stats.get("fieldGoalsMade")),
        "fga": _safe_int(stats.get("fieldGoalsAttempted")),
        "fg3m": _safe_int(stats.get("threePointersMade")),
        "fg3a": _safe_int(stats.get("threePointersAttempted")),
        "ftm": _safe_int(stats.get("freeThrowsMade")),
        "fta": _safe_int(stats.get("freeThrowsAttempted")),
        "plus_minus": _safe_float(stats.get("plusMinusPoints")),
        "source": source,
        "fetched_at": fetched_at,
        "stats_json": stats,
        "raw": team,
    }


def _player_rows(
    *,
    game_id: str,
    team_side: str,
    team: dict[str, Any],
    fetched_at: datetime,
    source: str,
) -> list[dict[str, Any]]:
    team_id = _safe_int(team.get("teamId"))
    team_tricode = str(team.get("teamTricode") or "").upper() or None
    players = team.get("players") if isinstance(team.get("players"), list) else []
    rows: list[dict[str, Any]] = []
    for player in players:
        if not isinstance(player, dict):
            continue
        stats = player.get("statistics") if isinstance(player.get("statistics"), dict) else {}
        player_id = _safe_int(player.get("personId"))
        if player_id is None:
            continue
        rows.append(
            {
                "game_id": game_id,
                "player_id": player_id,
                "team_id": team_id,
                "team_side": team_side,
                "team_tricode": team_tricode,
                "player_name": player.get("name") or player.get("nameI"),
                "first_name": player.get("firstName"),
                "family_name": player.get("familyName"),
                "jersey_num": player.get("jerseyNum"),
                "position": player.get("position"),
                "status": player.get("status"),
                "starter": _safe_bool(player.get("starter")),
                "oncourt": _safe_bool(player.get("oncourt")),
                "played": _safe_bool(player.get("played")),
                "order_no": _safe_int(player.get("order")),
                "minutes": stats.get("minutes"),
                "minutes_calculated": stats.get("minutesCalculated"),
                "points": _safe_int(stats.get("points")),
                "rebounds": _safe_int(stats.get("reboundsTotal")),
                "assists": _safe_int(stats.get("assists")),
                "steals": _safe_int(stats.get("steals")),
                "blocks": _safe_int(stats.get("blocks")),
                "turnovers": _safe_int(stats.get("turnovers")),
                "fgm": _safe_int(stats.get("fieldGoalsMade")),
                "fga": _safe_int(stats.get("fieldGoalsAttempted")),
                "fg3m": _safe_int(stats.get("threePointersMade")),
                "fg3a": _safe_int(stats.get("threePointersAttempted")),
                "ftm": _safe_int(stats.get("freeThrowsMade")),
                "fta": _safe_int(stats.get("freeThrowsAttempted")),
                "fouls_personal": _safe_int(stats.get("foulsPersonal")),
                "plus_minus": _safe_float(stats.get("plusMinusPoints")),
                "source": source,
                "fetched_at": fetched_at,
                "stats_json": stats,
                "raw": player,
            }
        )
    return rows


def normalize_scoreboard_payload(
    payload: dict[str, Any],
    *,
    fetched_at: datetime | None = None,
    source: str = "wnba_cdn_scoreboard",
) -> pd.DataFrame:
    fetched_at = fetched_at or _utc_now()
    scoreboard = payload.get("scoreboard") if isinstance(payload, dict) else {}
    games = scoreboard.get("games") if isinstance(scoreboard, dict) else []
    if not isinstance(games, list):
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for game in games:
        if not isinstance(game, dict):
            continue
        home = game.get("homeTeam") if isinstance(game.get("homeTeam"), dict) else {}
        away = game.get("awayTeam") if isinstance(game.get("awayTeam"), dict) else {}
        rows.append(
            {
                "game_id": str(game.get("gameId") or ""),
                "league_id": str(scoreboard.get("leagueId") or "10"),
                "game_status": _safe_int(game.get("gameStatus")),
                "game_status_text": game.get("gameStatusText"),
                "period": _safe_int(game.get("period")),
                "game_clock": game.get("gameClock"),
                "game_date": scoreboard.get("gameDate"),
                "game_start_time": _safe_dt(game.get("gameTimeUTC") or game.get("gameDateTimeUTC")),
                "home_team_id": _safe_int(home.get("teamId")),
                "away_team_id": _safe_int(away.get("teamId")),
                "home_team_tricode": str(home.get("teamTricode") or "").upper() or None,
                "away_team_tricode": str(away.get("teamTricode") or "").upper() or None,
                "home_score": _safe_int(home.get("score")),
                "away_score": _safe_int(away.get("score")),
                "source": source,
                "fetched_at": fetched_at,
                "raw": game,
            }
        )
    return pd.DataFrame([row for row in rows if row.get("game_id")])


def normalize_boxscore_payload(
    payload: dict[str, Any],
    *,
    fetched_at: datetime | None = None,
    source: str = "wnba_cdn_boxscore",
) -> WnbaBoxscoreFrames:
    fetched_at = fetched_at or _utc_now()
    game = payload.get("game") if isinstance(payload.get("game"), dict) else {}
    if not game:
        return WnbaBoxscoreFrames(snapshot={}, teams=pd.DataFrame(), players=pd.DataFrame())

    home = game.get("homeTeam") if isinstance(game.get("homeTeam"), dict) else {}
    away = game.get("awayTeam") if isinstance(game.get("awayTeam"), dict) else {}
    game_id = str(game.get("gameId") or "")
    snapshot = {
        "game_id": game_id,
        "game_status": _safe_int(game.get("gameStatus")),
        "game_status_text": game.get("gameStatusText"),
        "period": _safe_int(game.get("period")),
        "clock": game.get("gameClock"),
        "home_team_id": _safe_int(home.get("teamId")),
        "away_team_id": _safe_int(away.get("teamId")),
        "home_team_tricode": str(home.get("teamTricode") or "").upper() or None,
        "away_team_tricode": str(away.get("teamTricode") or "").upper() or None,
        "home_score": _safe_int(home.get("score")),
        "away_score": _safe_int(away.get("score")),
        "regulation_minutes": 40,
        "period_length_minutes": 10,
        "source": source,
        "fetched_at": fetched_at,
        "raw": game,
    }
    teams = pd.DataFrame(
        [
            _team_stats_row(
                game_id=game_id,
                team_side="home",
                team=home,
                snapshot=snapshot,
                fetched_at=fetched_at,
                source=source,
            ),
            _team_stats_row(
                game_id=game_id,
                team_side="away",
                team=away,
                snapshot=snapshot,
                fetched_at=fetched_at,
                source=source,
            ),
        ]
    )
    players = pd.DataFrame(
        [
            *_player_rows(
                game_id=game_id,
                team_side="home",
                team=home,
                fetched_at=fetched_at,
                source=source,
            ),
            *_player_rows(
                game_id=game_id,
                team_side="away",
                team=away,
                fetched_at=fetched_at,
                source=source,
            ),
        ]
    )
    return WnbaBoxscoreFrames(snapshot=snapshot, teams=teams, players=players)


def fetch_todays_scoreboard_payload() -> dict[str, Any]:
    return fetch_wnba_cdn_json(WNBA_TODAYS_SCOREBOARD_URL)


def fetch_todays_scoreboard_df() -> pd.DataFrame:
    payload = fetch_todays_scoreboard_payload()
    df = normalize_scoreboard_payload(payload)
    logger.info("fetch_todays_scoreboard_df: WNBA games=%d", len(df))
    return df


def fetch_boxscore_payload(game_id: str) -> dict[str, Any]:
    return fetch_wnba_cdn_json(wnba_boxscore_url(game_id))


def fetch_boxscore_frames(request: WnbaLiveStatsRequest) -> WnbaBoxscoreFrames:
    payload = fetch_boxscore_payload(request.game_id)
    frames = normalize_boxscore_payload(payload, fetched_at=request.as_of)
    logger.info(
        "fetch_boxscore_frames: WNBA game_id=%s teams=%d players=%d",
        request.game_id,
        len(frames.teams),
        len(frames.players),
    )
    return frames


def fetch_live_scoreboard(game_id: str) -> dict[str, Any]:
    frames = fetch_boxscore_frames(WnbaLiveStatsRequest(game_id=game_id, include_players=False))
    return frames.snapshot


def fetch_live_team_boxscore_df(request: WnbaLiveStatsRequest) -> pd.DataFrame:
    return fetch_boxscore_frames(request).teams


def fetch_live_player_boxscore_df(request: WnbaLiveStatsRequest) -> pd.DataFrame:
    return fetch_boxscore_frames(request).players
