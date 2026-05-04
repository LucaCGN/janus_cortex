#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
play_by_play.py
---------------

NBA live play-by-play node, normalized for deterministic ingestion and tests.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from nba_api.live.nba.endpoints import playbyplay
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
_PROVIDER_ERROR_LOG_COOLDOWN_SEC = 300.0
_PROVIDER_ERROR_LOG_STATE: dict[str, float] = {}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "1", "yes", "y"}:
            return True
        if v in {"false", "0", "no", "n"}:
            return False
    return default


def _extract_actions(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    game = payload.get("game")
    if not isinstance(game, dict):
        return []
    actions = game.get("actions")
    if not isinstance(actions, list):
        return []
    return [row for row in actions if isinstance(row, dict)]


def _is_transient_provider_decode_error(exc: Exception) -> bool:
    message = str(exc or "")
    name = exc.__class__.__name__
    return "JSONDecodeError" in name or "Expecting value" in message


def _log_provider_fetch_error(*, game_id: str, exc: Exception) -> None:
    if not _is_transient_provider_decode_error(exc):
        logger.error("fetch_play_by_play_df: provider error game_id=%s error=%r", game_id, exc)
        return
    now = time.monotonic()
    last_logged_at = _PROVIDER_ERROR_LOG_STATE.get(game_id)
    if last_logged_at is None or (now - last_logged_at) >= _PROVIDER_ERROR_LOG_COOLDOWN_SEC:
        _PROVIDER_ERROR_LOG_STATE[game_id] = now
        logger.warning(
            "fetch_play_by_play_df: transient decode failure game_id=%s; keeping poll loop alive and retrying: %s",
            game_id,
            exc,
        )
        return
    logger.debug("fetch_play_by_play_df: transient decode failure suppressed game_id=%s error=%r", game_id, exc)


class PlayByPlayRequest(BaseModel):
    """Request params for live PbP."""

    game_id: str = Field(..., description="NBA Game ID.")
    cursor: Optional[int] = Field(
        default=None,
        description="Optional cursor; keeps rows after this action/event index.",
    )
    window_last_n_actions: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional rolling window over the most recent N actions.",
    )


def fetch_play_by_play_df(request: PlayByPlayRequest) -> pd.DataFrame:
    """
    Fetches play-by-play actions as normalized rows.

    Output columns:
    - game_id, event_index, action_id, clock, period, team_id, team_tricode
    - person_id, player_name, action_type, sub_type, description
    - score_home, score_away, points_home, points_away, is_score_change
    - updated_at, raw
    """
    logger.debug(
        "fetch_play_by_play_df: start game_id=%s cursor=%s window=%s",
        request.game_id,
        request.cursor,
        request.window_last_n_actions,
    )

    try:
        payload = playbyplay.PlayByPlay(request.game_id).get_dict()
    except Exception as exc:  # noqa: BLE001
        _log_provider_fetch_error(game_id=request.game_id, exc=exc)
        return pd.DataFrame()

    actions = _extract_actions(payload)
    if not actions:
        logger.info("fetch_play_by_play_df: empty actions game_id=%s", request.game_id)
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    prev_home = 0
    prev_away = 0
    for idx, action in enumerate(actions, start=1):
        score_home = _to_int(action.get("scoreHome"), default=prev_home)
        score_away = _to_int(action.get("scoreAway"), default=prev_away)
        points_home = max(0, score_home - prev_home)
        points_away = max(0, score_away - prev_away)
        prev_home = score_home
        prev_away = score_away

        rows.append(
            {
                "game_id": request.game_id,
                "event_index": idx,
                "action_id": _to_int(action.get("actionId"), default=idx),
                "clock": action.get("clock"),
                "period": _to_int(action.get("period")),
                "team_id": _to_int(action.get("teamId")),
                "team_tricode": action.get("teamTricode"),
                "person_id": _to_int(action.get("personId")),
                "player_name": action.get("playerName") or action.get("nameI"),
                "action_type": action.get("actionType"),
                "sub_type": action.get("subType"),
                "description": action.get("description"),
                "score_home": score_home,
                "score_away": score_away,
                "points_home": points_home,
                "points_away": points_away,
                "is_score_change": _to_bool(action.get("isScoreChange"), default=(points_home + points_away) > 0),
                "updated_at": action.get("timeActual"),
                "raw": action,
            }
        )

    df = pd.DataFrame(rows)
    df = df.sort_values(by=["event_index", "action_id"]).reset_index(drop=True)

    if request.cursor is not None:
        cursor = int(request.cursor)
        # Keep rows strictly after the cursor across both common cursor semantics.
        df = df[(df["event_index"] > cursor) | (df["action_id"] > cursor)].reset_index(drop=True)

    if request.window_last_n_actions is not None and request.window_last_n_actions > 0:
        df = df.tail(request.window_last_n_actions).reset_index(drop=True)

    logger.info(
        "fetch_play_by_play_df: ok game_id=%s rows=%d",
        request.game_id,
        len(df),
    )
    return df


def compute_runs(df: pd.DataFrame, lookback_actions: int = 20) -> pd.DataFrame:
    """
    Computes scoring runs over the last `lookback_actions` actions.
    """
    if df.empty:
        return pd.DataFrame()

    work = df.copy()
    if lookback_actions > 0:
        work = work.tail(lookback_actions)
    work = work.reset_index(drop=True)

    if "points_home" not in work.columns or "points_away" not in work.columns:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    run: Optional[dict[str, Any]] = None

    for _, row in work.iterrows():
        ph = _to_int(row.get("points_home"))
        pa = _to_int(row.get("points_away"))
        if ph <= 0 and pa <= 0:
            continue

        if ph > pa:
            scoring_side = "home"
            points = ph
        elif pa > ph:
            scoring_side = "away"
            points = pa
        else:
            # Rare tie scoring deltas, split and terminate current run.
            if run is not None:
                rows.append(run)
                run = None
            continue

        if run is None or run["scoring_side"] != scoring_side:
            if run is not None:
                rows.append(run)
            run = {
                "game_id": row.get("game_id"),
                "scoring_side": scoring_side,
                "scoring_team": row.get("team_tricode"),
                "start_event_index": _to_int(row.get("event_index")),
                "end_event_index": _to_int(row.get("event_index")),
                "start_action_id": _to_int(row.get("action_id")),
                "end_action_id": _to_int(row.get("action_id")),
                "start_period": _to_int(row.get("period")),
                "end_period": _to_int(row.get("period")),
                "start_clock": row.get("clock"),
                "end_clock": row.get("clock"),
                "points_for": points,
                "events_count": 1,
            }
        else:
            run["end_event_index"] = _to_int(row.get("event_index"))
            run["end_action_id"] = _to_int(row.get("action_id"))
            run["end_period"] = _to_int(row.get("period"))
            run["end_clock"] = row.get("clock")
            run["points_for"] += points
            run["events_count"] += 1

    if run is not None:
        rows.append(run)

    if not rows:
        return pd.DataFrame()

    runs_df = pd.DataFrame(rows)
    runs_df = runs_df.sort_values(
        by=["points_for", "events_count", "end_event_index"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    return runs_df


def fetch_lead_tracker_df(request: PlayByPlayRequest) -> pd.DataFrame:
    """
    Builds lead progression from normalized play-by-play rows.
    """
    df = fetch_play_by_play_df(request)
    if df.empty:
        return pd.DataFrame()

    lead = df["score_home"].fillna(0).astype(int) - df["score_away"].fillna(0).astype(int)
    out = pd.DataFrame(
        {
            "game_id": df["game_id"],
            "event_index": df["event_index"],
            "action_id": df["action_id"],
            "period": df["period"],
            "clock": df["clock"],
            "description": df["description"],
            "score_home": df["score_home"],
            "score_away": df["score_away"],
            "home_lead": lead,
            "LEAD": lead,
        }
    )

    out["leader_side"] = out["home_lead"].apply(
        lambda x: "home" if x > 0 else ("away" if x < 0 else "tied")
    )
    out["max_abs_lead_so_far"] = out["home_lead"].abs().cummax()
    return out.reset_index(drop=True)


def compute_lead_change_summary(df: pd.DataFrame) -> dict[str, Any]:
    """
    Summarize lead progression for one game.

    Lead changes count transitions between non-tied leaders. Tied rows do not
    count as independent lead changes, but they are tracked separately.
    """
    if df.empty:
        return {
            "lead_changes": 0,
            "times_tied": 0,
            "home_largest_lead": 0,
            "away_largest_lead": 0,
            "home_lead_events": 0,
            "away_lead_events": 0,
            "tied_events": 0,
            "home_lead_segments": 0,
            "away_lead_segments": 0,
            "last_leader_side": "tied",
        }

    work = df.copy()
    if "home_lead" not in work.columns:
        if {"score_home", "score_away"}.issubset(work.columns):
            work["home_lead"] = (
                pd.to_numeric(work["score_home"], errors="coerce").fillna(0).astype(int)
                - pd.to_numeric(work["score_away"], errors="coerce").fillna(0).astype(int)
            )
        else:
            raise ValueError("compute_lead_change_summary requires home_lead or score_home/score_away columns")

    if "leader_side" not in work.columns:
        work["leader_side"] = work["home_lead"].apply(
            lambda x: "home" if int(x) > 0 else ("away" if int(x) < 0 else "tied")
        )

    leaders = work["leader_side"].astype(str).tolist()
    lead_changes = 0
    home_segments = 0
    away_segments = 0
    last_non_tied: str | None = None

    for side in leaders:
        if side == "tied":
            continue
        if last_non_tied is None:
            if side == "home":
                home_segments += 1
            elif side == "away":
                away_segments += 1
            last_non_tied = side
            continue
        if side != last_non_tied:
            lead_changes += 1
            if side == "home":
                home_segments += 1
            elif side == "away":
                away_segments += 1
            last_non_tied = side

    home_lead = pd.to_numeric(work["home_lead"], errors="coerce").fillna(0).astype(int)
    last_leader_side = str(work["leader_side"].iloc[-1]) if not work.empty else "tied"
    return {
        "lead_changes": int(lead_changes),
        "times_tied": int((home_lead == 0).sum()),
        "home_largest_lead": int(max(home_lead.max(), 0)),
        "away_largest_lead": int(abs(min(home_lead.min(), 0))),
        "home_lead_events": int((home_lead > 0).sum()),
        "away_lead_events": int((home_lead < 0).sum()),
        "tied_events": int((home_lead == 0).sum()),
        "home_lead_segments": int(home_segments),
        "away_lead_segments": int(away_segments),
        "last_leader_side": last_leader_side,
    }


def upsert_nba_play_by_play_to_sqlite(
    df: pd.DataFrame,
    sqlite_path: str | Path,
    table_name: str = "nba_play_by_play",
) -> None:
    """
    Idempotent upsert for normalized PbP rows, keyed by (game_id, event_index).
    """
    if df.empty:
        logger.warning("upsert_nba_play_by_play_to_sqlite: empty dataframe, skipping.")
        return

    required_cols = {"game_id", "event_index"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"upsert_nba_play_by_play_to_sqlite: missing required columns {sorted(missing)}")

    db_path = Path(sqlite_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    cols = [
        "game_id",
        "event_index",
        "action_id",
        "clock",
        "period",
        "team_id",
        "team_tricode",
        "person_id",
        "player_name",
        "action_type",
        "sub_type",
        "description",
        "score_home",
        "score_away",
        "points_home",
        "points_away",
        "is_score_change",
        "updated_at",
        "raw",
        "ingested_at",
    ]

    now_iso = datetime.now(timezone.utc).isoformat()
    work = df.copy()
    if "ingested_at" not in work.columns:
        work["ingested_at"] = now_iso
    if "raw" in work.columns:
        work["raw"] = work["raw"].apply(lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else None)
    work = work.where(pd.notnull(work), None)

    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                game_id TEXT NOT NULL,
                event_index INTEGER NOT NULL,
                action_id INTEGER,
                clock TEXT,
                period INTEGER,
                team_id INTEGER,
                team_tricode TEXT,
                person_id INTEGER,
                player_name TEXT,
                action_type TEXT,
                sub_type TEXT,
                description TEXT,
                score_home INTEGER,
                score_away INTEGER,
                points_home INTEGER,
                points_away INTEGER,
                is_score_change INTEGER,
                updated_at TEXT,
                raw TEXT,
                ingested_at TEXT,
                PRIMARY KEY (game_id, event_index)
            )
            """
        )

        insert_cols = [c for c in cols if c in work.columns]
        placeholders = ",".join("?" for _ in insert_cols)
        sql = (
            f"INSERT OR REPLACE INTO {table_name} "
            f"({','.join(insert_cols)}) VALUES ({placeholders})"
        )
        values = [tuple(row[c] for c in insert_cols) for _, row in work.iterrows()]
        cur.executemany(sql, values)
        conn.commit()

    logger.info(
        "upsert_nba_play_by_play_to_sqlite: upserted rows=%d table=%s",
        len(work),
        table_name,
    )
