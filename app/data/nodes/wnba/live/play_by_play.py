from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from app.data.nodes.wnba.cdn_client import fetch_wnba_cdn_json, wnba_play_by_play_url

logger = logging.getLogger(__name__)


class WnbaPlayByPlayRequest(BaseModel):
    game_id: str = Field(..., description="Official WNBA game id.")
    cursor: int | None = None
    window_last_n_actions: int | None = Field(default=None, ge=1)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


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


def _extract_actions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    game = payload.get("game") if isinstance(payload.get("game"), dict) else {}
    actions = game.get("actions") if isinstance(game, dict) else []
    if not isinstance(actions, list):
        return []
    return [item for item in actions if isinstance(item, dict)]


def _score_change_hint(action: dict[str, Any], points_home: int, points_away: int) -> bool:
    explicit = action.get("isScoreChange")
    if isinstance(explicit, bool):
        return explicit
    if explicit is not None:
        parsed = str(explicit).strip().lower()
        if parsed in {"1", "true", "yes"}:
            return True
        if parsed in {"0", "false", "no"}:
            return False
    return (points_home + points_away) > 0


def normalize_play_by_play_payload(
    payload: dict[str, Any],
    *,
    game_id: str | None = None,
    fetched_at: datetime | None = None,
    source: str = "wnba_cdn_play_by_play",
) -> pd.DataFrame:
    fetched_at = fetched_at or _utc_now()
    game = payload.get("game") if isinstance(payload.get("game"), dict) else {}
    normalized_game_id = str(game_id or game.get("gameId") or "")
    actions = _extract_actions(payload)
    if not actions:
        return pd.DataFrame()

    prev_home = 0
    prev_away = 0
    rows: list[dict[str, Any]] = []
    for index, action in enumerate(actions, start=1):
        home_score = _safe_int(action.get("scoreHome"), prev_home) or 0
        away_score = _safe_int(action.get("scoreAway"), prev_away) or 0
        points_home = max(0, home_score - prev_home)
        points_away = max(0, away_score - prev_away)
        prev_home = home_score
        prev_away = away_score
        team_id = _safe_int(action.get("teamId"))
        team_tricode = str(action.get("teamTricode") or "").upper() or None
        is_score_change = _score_change_hint(action, points_home, points_away)
        scoring_team_id = team_id if is_score_change and (points_home or points_away) else None
        scoring_team_tricode = team_tricode if scoring_team_id else None
        action_type = str(action.get("actionType") or "")
        sub_type = str(action.get("subType") or "")

        rows.append(
            {
                "game_id": normalized_game_id,
                "event_index": index,
                "action_id": str(action.get("actionId") or action.get("actionNumber") or index),
                "action_number": _safe_int(action.get("actionNumber")),
                "order_number": _safe_int(action.get("orderNumber")),
                "period": _safe_int(action.get("period")),
                "period_type": action.get("periodType"),
                "clock": action.get("clock"),
                "time_actual": _safe_dt(action.get("timeActual")),
                "team_id": team_id,
                "team_tricode": team_tricode,
                "person_id": _safe_int(action.get("personId")),
                "player_name": action.get("playerName") or action.get("playerNameI"),
                "action_type": action_type or None,
                "sub_type": sub_type or None,
                "description": action.get("description"),
                "home_score": home_score,
                "away_score": away_score,
                "points_home": points_home,
                "points_away": points_away,
                "is_score_change": is_score_change,
                "scoring_team_id": scoring_team_id,
                "scoring_team_tricode": scoring_team_tricode,
                "substitution_direction": sub_type if action_type == "substitution" else None,
                "substitution_person_id": _safe_int(action.get("personId")) if action_type == "substitution" else None,
                "substitution_player_name": (
                    action.get("playerName") or action.get("playerNameI")
                    if action_type == "substitution"
                    else None
                ),
                "qualifiers": action.get("qualifiers") if isinstance(action.get("qualifiers"), list) else [],
                "source": source,
                "fetched_at": fetched_at,
                "raw": action,
            }
        )

    df = pd.DataFrame(rows).sort_values(by=["event_index"]).reset_index(drop=True)
    return df


def fetch_play_by_play_payload(game_id: str) -> dict[str, Any]:
    return fetch_wnba_cdn_json(wnba_play_by_play_url(game_id))


def fetch_play_by_play_df(request: WnbaPlayByPlayRequest) -> pd.DataFrame:
    payload = fetch_play_by_play_payload(request.game_id)
    df = normalize_play_by_play_payload(payload, game_id=request.game_id)
    if request.cursor is not None and not df.empty:
        cursor = int(request.cursor)
        df = df[
            (pd.to_numeric(df["event_index"], errors="coerce") > cursor)
            | (pd.to_numeric(df["action_number"], errors="coerce") > cursor)
        ].reset_index(drop=True)
    if request.window_last_n_actions is not None and request.window_last_n_actions > 0:
        df = df.tail(request.window_last_n_actions).reset_index(drop=True)
    logger.info("fetch_play_by_play_df: WNBA game_id=%s rows=%d", request.game_id, len(df))
    return df


def compute_wnba_seconds_to_game_end(period: int | None, clock: str | None) -> int | None:
    """Compute game seconds remaining for 40-minute WNBA regulation games."""
    if period is None or not clock:
        return None
    if period <= 0:
        return None
    raw = str(clock)
    if not raw.startswith("PT"):
        return None
    try:
        minutes_part = raw.split("M")[0].replace("PT", "")
        seconds_part = raw.split("M")[1].replace("S", "")
        seconds_left_in_period = int(float(minutes_part) * 60 + float(seconds_part))
    except (IndexError, ValueError):
        return None
    if period <= 4:
        future_periods = max(0, 4 - period)
        return seconds_left_in_period + future_periods * 10 * 60
    return seconds_left_in_period
