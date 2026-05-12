from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from app.data.nodes.wnba.cdn_client import WNBA_SCHEDULE_URL, fetch_wnba_cdn_json

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def _safe_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%m/%d/%Y %H:%M:%S").date()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(raw[:10]).date()
    except ValueError:
        return None


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _season_phase_from_game(game: dict[str, Any]) -> str:
    label = str(game.get("gameLabel") or "").strip().lower()
    subtype = str(game.get("gameSubtype") or "").strip().lower()
    game_id_prefix = str(game.get("gameId") or "")[:3]
    if "preseason" in label or game_id_prefix == "101":
        return "preseason"
    if "playoff" in label or game_id_prefix == "104":
        return "playoffs"
    if "commissioner" in label or "cup" in label or "cup" in subtype:
        return "commissioners_cup"
    return "regular_season"


def normalize_schedule_payload(
    payload: dict[str, Any],
    *,
    season: str = "2026",
    fetched_at: datetime | None = None,
    source: str = "wnba_cdn_schedule",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Normalize WNBA CDN schedule payload into game and team rows."""
    fetched_at = fetched_at or _utc_now()
    league_schedule = payload.get("leagueSchedule") if isinstance(payload, dict) else {}
    game_dates = league_schedule.get("gameDates") if isinstance(league_schedule, dict) else []
    if not isinstance(game_dates, list):
        return pd.DataFrame(), pd.DataFrame()

    game_rows: list[dict[str, Any]] = []
    teams_by_id: dict[int, dict[str, Any]] = {}

    for game_date_block in game_dates:
        if not isinstance(game_date_block, dict):
            continue
        block_date = _safe_date(game_date_block.get("gameDate"))
        games = game_date_block.get("games")
        if not isinstance(games, list):
            continue
        for game in games:
            if not isinstance(game, dict):
                continue
            home = game.get("homeTeam") if isinstance(game.get("homeTeam"), dict) else {}
            away = game.get("awayTeam") if isinstance(game.get("awayTeam"), dict) else {}
            home_team_id = _safe_int(home.get("teamId"))
            away_team_id = _safe_int(away.get("teamId"))
            for team in (home, away):
                team_id = _safe_int(team.get("teamId"))
                if team_id is None:
                    continue
                teams_by_id[team_id] = {
                    "team_id": team_id,
                    "team_slug": team.get("teamSlug"),
                    "team_tricode": str(team.get("teamTricode") or "").upper() or None,
                    "team_name": team.get("teamName") or str(team.get("teamTricode") or team_id),
                    "team_city": team.get("teamCity"),
                    "source": source,
                    "fetched_at": fetched_at,
                    "raw": team,
                }

            game_start_time = _safe_dt(game.get("gameDateTimeUTC"))
            game_rows.append(
                {
                    "game_id": str(game.get("gameId") or ""),
                    "game_code": game.get("gameCode"),
                    "season": season,
                    "league_id": "10",
                    "game_date": block_date or _safe_date(game.get("gameDateUTC")),
                    "game_start_time": game_start_time,
                    "game_status": _safe_int(game.get("gameStatus")),
                    "game_status_text": game.get("gameStatusText"),
                    "period": _safe_int(game.get("period")),
                    "game_clock": game.get("gameClock"),
                    "season_phase": _season_phase_from_game(game),
                    "season_phase_label": game.get("gameLabel"),
                    "season_phase_sub_label": game.get("gameSubLabel"),
                    "season_phase_subtype": game.get("gameSubtype"),
                    "series_text": game.get("seriesText"),
                    "series_game_number": game.get("seriesGameNumber"),
                    "home_team_id": home_team_id,
                    "away_team_id": away_team_id,
                    "home_team_tricode": str(home.get("teamTricode") or "").upper() or None,
                    "away_team_tricode": str(away.get("teamTricode") or "").upper() or None,
                    "home_team_slug": home.get("teamSlug"),
                    "away_team_slug": away.get("teamSlug"),
                    "home_team_name": home.get("teamName"),
                    "away_team_name": away.get("teamName"),
                    "home_team_city": home.get("teamCity"),
                    "away_team_city": away.get("teamCity"),
                    "home_score": _safe_int(home.get("score")),
                    "away_score": _safe_int(away.get("score")),
                    "arena_name": game.get("arenaName"),
                    "arena_city": game.get("arenaCity"),
                    "arena_state": game.get("arenaState"),
                    "is_neutral": bool(game.get("isNeutral")) if game.get("isNeutral") is not None else None,
                    "postponed_status": game.get("postponedStatus"),
                    "source": source,
                    "fetched_at": fetched_at,
                    "raw": game,
                }
            )

    games_df = pd.DataFrame([row for row in game_rows if row.get("game_id")])
    teams_df = pd.DataFrame(teams_by_id.values())
    return games_df, teams_df


def fetch_season_schedule_payload() -> dict[str, Any]:
    return fetch_wnba_cdn_json(WNBA_SCHEDULE_URL)


def fetch_season_schedule_df(season: str = "2026") -> pd.DataFrame:
    payload = fetch_season_schedule_payload()
    games_df, _teams_df = normalize_schedule_payload(payload, season=season)
    logger.info("fetch_season_schedule_df: WNBA games=%d season=%s", len(games_df), season)
    return games_df


def fetch_season_teams_df(season: str = "2026") -> pd.DataFrame:
    payload = fetch_season_schedule_payload()
    _games_df, teams_df = normalize_schedule_payload(payload, season=season)
    logger.info("fetch_season_teams_df: WNBA teams=%d season=%s", len(teams_df), season)
    return teams_df


def parse_polymarket_wnba_slug(slug: str) -> tuple[str | None, str | None, str | None]:
    parts = str(slug or "").split("-")
    if len(parts) < 6 or parts[0].lower() != "wnba":
        return None, None, None
    away = parts[1].upper()
    home = parts[2].upper()
    year, month, day = parts[-3:]
    if len(year) != 4 or len(month) != 2 or len(day) != 2:
        return None, None, None
    return away, home, f"{year}-{month}-{day}"


def match_polymarket_slug_to_game(slug: str, schedule_df: pd.DataFrame) -> str | None:
    away, home, game_date = parse_polymarket_wnba_slug(slug)
    if not away or not home or not game_date or schedule_df.empty:
        return None
    day_games = schedule_df[schedule_df["game_date"].astype(str) == game_date]
    if day_games.empty:
        return None
    match = day_games[
        (day_games["home_team_tricode"].astype(str).str.upper() == home)
        & (day_games["away_team_tricode"].astype(str).str.upper() == away)
    ]
    if match.empty:
        return None
    return str(match.iloc[0]["game_id"])
