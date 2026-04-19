from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from app.api.db import to_jsonable
from app.data.databases.postgres import managed_connection
from app.data.pipelines.daily.nba.analysis.artifacts import ensure_output_dir, write_frame, write_json, write_markdown
from app.data.pipelines.daily.nba.analysis.contracts import ANALYSIS_VERSION, DEFAULT_SEASON, DEFAULT_SEASON_PHASE


PLAYER_NAME_KEYS = ("playerNameI", "playerName", "player_name", "scorerName", "displayName", "name")
PLAYER_ID_KEYS = ("playerId", "player_id", "personId", "person_id")
TEAM_ID_KEYS = ("teamId", "team_id")
TEAM_TRICODE_KEYS = ("teamTricode", "team_tricode", "teamAbbreviation", "team_abbreviation")
STATUS_KEYS = ("status", "availability_status", "availability", "injury_status", "game_status")


@dataclass(slots=True)
class PlayerImpactShadowResult:
    swing_state_events: pd.DataFrame
    run_segments: pd.DataFrame
    player_presence_summary: pd.DataFrame
    absence_proxy_summary: pd.DataFrame
    absence_proxy_deltas: pd.DataFrame
    summary: dict[str, Any]

    def payload(self, *, top_n: int = 10) -> dict[str, Any]:
        return {
            "shadow_mode": True,
            "experimental_label": "experimental_shadow",
            "claim_boundaries": {
                "correlational": [
                    "scorer presence on swing states",
                    "run-start and run-stop involvement",
                    "absence-proxy deltas when explicit proxies exist",
                ],
                "causal": [],
                "notes": [
                    "This lane is exploratory only.",
                    "Do not interpret absence proxies as injury causality or proof of impact.",
                ],
            },
            "summary": self.summary,
            "samples": {
                "swing_state_events": _frame_records(self.swing_state_events, top_n=top_n),
                "run_segments": _frame_records(self.run_segments, top_n=top_n),
                "player_presence_summary": _frame_records(self.player_presence_summary, top_n=top_n),
                "absence_proxy_summary": _frame_records(self.absence_proxy_summary, top_n=top_n),
                "absence_proxy_deltas": _frame_records(self.absence_proxy_deltas, top_n=top_n),
            },
        }


def _query_df(connection: Any, query: str, params: Sequence[Any] = ()) -> pd.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value).strip().replace("Z", "+00:00")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _safe_json(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _normalize_text(value: Any) -> str:
    raw = "".join(character.lower() if str(character).isalnum() else " " for character in str(value or ""))
    return " ".join(raw.split())


def _pick_first(mapping: dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _extract_player_key(payload: dict[str, Any]) -> str:
    player_id = _pick_first(payload, PLAYER_ID_KEYS)
    if player_id not in (None, ""):
        return f"id:{player_id}"
    player_name = _pick_first(payload, PLAYER_NAME_KEYS)
    if player_name not in (None, ""):
        return f"name:{_normalize_text(player_name)}"
    return "unknown_player"


def _extract_player_name(payload: dict[str, Any], description: Any = None) -> str | None:
    player_name = _pick_first(payload, PLAYER_NAME_KEYS)
    if player_name not in (None, ""):
        return str(player_name).strip()
    description_text = str(description or "").strip()
    return description_text or None


def _extract_team_slug(payload: dict[str, Any]) -> str | None:
    team_slug = _pick_first(payload, TEAM_TRICODE_KEYS)
    return str(team_slug).strip().upper() if team_slug not in (None, "") else None


def _extract_team_id(payload: dict[str, Any]) -> int | None:
    return _safe_int(_pick_first(payload, TEAM_ID_KEYS))


def _score_state_flag(row: pd.Series, *, swing_threshold: float) -> bool:
    price_delta = abs(_safe_float(row.get("price_delta_from_open")) or 0.0)
    return bool(row.get("large_swing_next_12_states_flag")) or bool(row.get("crossed_50c_next_12_states_flag")) or price_delta >= swing_threshold


def _frame_records(frame: pd.DataFrame, *, top_n: int = 10) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    trimmed = frame.head(top_n).copy()
    for column in trimmed.columns:
        series = trimmed[column]
        if pd.api.types.is_datetime64_any_dtype(series):
            trimmed[column] = pd.to_datetime(series, errors="coerce", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        elif pd.api.types.is_object_dtype(series):
            trimmed[column] = series.apply(lambda value: value.isoformat() if hasattr(value, "isoformat") else value)
    return to_jsonable(trimmed.to_dict(orient="records"))


def _load_state_panel_df(connection: Any, *, season: str, season_phase: str, analysis_version: str) -> pd.DataFrame:
    frame = _query_df(
        connection,
        """
        SELECT *
        FROM nba.nba_analysis_state_panel
        WHERE season = %s AND season_phase = %s AND analysis_version = %s
        ORDER BY game_date ASC NULLS LAST, game_id ASC, team_side ASC, state_index ASC;
        """,
        (season, season_phase, analysis_version),
    )
    if frame.empty:
        return frame
    for column in ("computed_at", "event_at"):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce", utc=True)
    if "game_date" in frame.columns:
        frame["game_date"] = pd.to_datetime(frame["game_date"], errors="coerce").dt.date
    for column in ("event_index", "state_index", "team_id", "opponent_team_id", "score_diff", "points_scored", "delta_for", "delta_against"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in ("opening_price", "team_price", "price_delta_from_open", "abs_price_delta_from_open"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _load_play_by_play_df(connection: Any, *, season: str, season_phase: str) -> pd.DataFrame:
    frame = _query_df(
        connection,
        """
        SELECT
            p.game_id,
            p.event_index,
            p.action_id,
            p.period,
            p.clock,
            p.description,
            p.home_score,
            p.away_score,
            p.is_score_change,
            p.payload_json,
            g.game_date,
            g.game_start_time,
            g.home_team_id,
            g.away_team_id,
            g.home_team_slug,
            g.away_team_slug
        FROM nba.nba_play_by_play p
        JOIN nba.nba_games g ON g.game_id = p.game_id
        WHERE g.season = %s AND g.season_phase = %s AND g.game_status = 3
        ORDER BY p.game_id ASC, p.event_index ASC;
        """,
        (season, season_phase),
    )
    if frame.empty:
        return frame
    for column in ("game_date", "game_start_time"):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce", utc=True)
    if "payload_json" in frame.columns:
        frame["payload_json"] = frame["payload_json"].apply(_safe_json)
    for column in ("event_index", "period", "home_score", "away_score"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "is_score_change" in frame.columns:
        frame["is_score_change"] = frame["is_score_change"].fillna(False).astype(bool)
    return frame

def _load_player_stats_df(connection: Any, *, season: str) -> pd.DataFrame:
    frame = _query_df(
        connection,
        """
        SELECT DISTINCT ON (p.player_id, p.metric_set)
            p.player_id,
            p.player_name,
            p.team_id,
            t.team_slug,
            p.season,
            p.captured_at,
            p.metric_set,
            p.stats_json,
            p.source
        FROM nba.nba_player_stats_snapshots p
        LEFT JOIN nba.nba_teams t ON t.team_id = p.team_id
        WHERE p.season = %s
        ORDER BY p.player_id ASC, p.metric_set ASC, p.captured_at DESC;
        """,
        (season,),
    )
    if frame.empty:
        return frame
    frame["captured_at"] = pd.to_datetime(frame["captured_at"], errors="coerce", utc=True)
    if "stats_json" in frame.columns:
        frame["stats_json"] = frame["stats_json"].apply(_safe_json)
    for column in ("player_id", "team_id"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _extract_score_delta(curr: pd.Series, prev: pd.Series | None) -> tuple[int | None, int | None]:
    home_score = _safe_int(curr.get("home_score"))
    away_score = _safe_int(curr.get("away_score"))
    if prev is None or home_score is None or away_score is None:
        return home_score, away_score
    prev_home = _safe_int(prev.get("home_score"))
    prev_away = _safe_int(prev.get("away_score"))
    if prev_home is None or prev_away is None:
        return home_score, away_score
    return home_score - prev_home, away_score - prev_away


def _infer_scoring_side(curr: pd.Series, prev: pd.Series | None) -> str | None:
    delta_home, delta_away = _extract_score_delta(curr, prev)
    if delta_home is not None and delta_away is not None:
        if delta_home > delta_away:
            return "home"
        if delta_away > delta_home:
            return "away"
    payload = _safe_json(curr.get("payload_json"))
    team_id = _extract_team_id(payload)
    if team_id is not None:
        if team_id == _safe_int(curr.get("home_team_id")):
            return "home"
        if team_id == _safe_int(curr.get("away_team_id")):
            return "away"
    team_slug = _extract_team_slug(payload)
    if team_slug is not None:
        if team_slug == str(curr.get("home_team_slug") or "").strip().upper():
            return "home"
        if team_slug == str(curr.get("away_team_slug") or "").strip().upper():
            return "away"
    return None


def _build_run_segments(pbp_df: pd.DataFrame) -> pd.DataFrame:
    if pbp_df.empty:
        return pd.DataFrame(
            columns=[
                "game_id",
                "run_index",
                "scoring_side",
                "run_points",
                "run_event_count",
                "start_event_index",
                "end_event_index",
                "start_player_key",
                "start_player_name",
                "stop_player_key",
                "stop_player_name",
                "contains_swing_state",
                "swing_event_count",
                "start_event_at",
                "end_event_at",
            ]
        )

    rows: list[dict[str, Any]] = []
    grouped = pbp_df.sort_values(["game_id", "event_index"]).groupby("game_id", sort=True)
    for game_id, group in grouped:
        current_run: dict[str, Any] | None = None
        previous_row: pd.Series | None = None
        run_index = 0
        for _, row in group.iterrows():
            if not bool(row.get("is_score_change")):
                previous_row = row
                continue
            scoring_side = _infer_scoring_side(row, previous_row)
            payload = _safe_json(row.get("payload_json"))
            player_key = _extract_player_key(payload)
            player_name = _extract_player_name(payload, row.get("description"))
            points_scored = _safe_int(_pick_first(payload, ("pointsTotal", "points_scored", "points"))) or 0
            event_at = _safe_datetime(payload.get("timeActual")) or _safe_datetime(row.get("game_start_time"))
            if current_run is None or current_run["scoring_side"] != scoring_side:
                if current_run is not None:
                    rows.append(current_run)
                current_run = {
                    "game_id": str(game_id),
                    "run_index": run_index,
                    "scoring_side": scoring_side,
                    "run_points": points_scored,
                    "run_event_count": 1,
                    "start_event_index": _safe_int(row.get("event_index")),
                    "end_event_index": _safe_int(row.get("event_index")),
                    "start_player_key": player_key,
                    "start_player_name": player_name,
                    "stop_player_key": player_key,
                    "stop_player_name": player_name,
                    "contains_swing_state": False,
                    "swing_event_count": 0,
                    "start_event_at": event_at,
                    "end_event_at": event_at,
                }
                run_index += 1
            else:
                current_run["run_points"] += points_scored
                current_run["run_event_count"] += 1
                current_run["end_event_index"] = _safe_int(row.get("event_index"))
                current_run["stop_player_key"] = player_key
                current_run["stop_player_name"] = player_name
                current_run["end_event_at"] = event_at
            previous_row = row
        if current_run is not None:
            rows.append(current_run)

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    for column in ("start_event_at", "end_event_at"):
        frame[column] = pd.to_datetime(frame[column], errors="coerce", utc=True)
    frame["contains_swing_state"] = frame["contains_swing_state"].fillna(False).astype(bool)
    return frame.sort_values(["game_id", "run_index"]).reset_index(drop=True)


def _build_swing_state_events(state_df: pd.DataFrame, pbp_df: pd.DataFrame, *, swing_threshold: float) -> pd.DataFrame:
    if state_df.empty or pbp_df.empty:
        return pd.DataFrame(
            columns=[
                "game_id",
                "event_index",
                "player_key",
                "player_name",
                "team_slug",
                "team_side",
                "score_diff",
                "price_delta_from_open",
                "swing_state_flag",
                "swing_state_sides",
                "swing_state_side_count",
                "event_description",
                "event_at",
            ]
        )

    merged = state_df.merge(
        pbp_df[["game_id", "event_index", "description", "payload_json", "game_start_time"]],
        on=["game_id", "event_index"],
        how="left",
        suffixes=("", "_pbp"),
    )
    merged["swing_state_flag"] = merged.apply(lambda row: _score_state_flag(row, swing_threshold=swing_threshold), axis=1)
    merged = merged[merged["swing_state_flag"]].copy()
    if merged.empty:
        return merged

    merged["payload_json"] = merged["payload_json"].apply(_safe_json)
    merged["player_key"] = merged["payload_json"].apply(_extract_player_key)
    merged["player_name"] = merged.apply(lambda row: _extract_player_name(row["payload_json"], row.get("description")), axis=1)
    merged["team_slug"] = merged["payload_json"].apply(_extract_team_slug)
    merged["team_id"] = merged["payload_json"].apply(_extract_team_id)
    merged["event_at"] = merged["payload_json"].apply(lambda payload: _safe_datetime(payload.get("timeActual")))
    merged["team_side_label"] = merged["team_side"].astype(str)

    grouped = (
        merged.groupby(["game_id", "event_index", "player_key", "player_name"], sort=True)
        .agg(
            team_slug=("team_slug", lambda values: next((str(value) for value in values if value not in (None, "")), None)),
            team_id=("team_id", lambda values: next((int(value) for value in values if value is not None and not pd.isna(value)), None)),
            team_side=("team_side_label", lambda values: ", ".join(sorted({str(value) for value in values if value not in (None, "")}))),
            score_diff=("score_diff", "first"),
            price_delta_from_open=("price_delta_from_open", lambda values: _safe_float(values.iloc[0])),
            swing_state_sides=("team_side_label", lambda values: sorted({str(value) for value in values if value not in (None, "")})),
            swing_state_side_count=("team_side_label", lambda values: int(len({str(value) for value in values if value not in (None, "")}))),
            event_description=("description", lambda values: next((str(value) for value in values if value not in (None, "")), None)),
            event_at=("event_at", "first"),
        )
        .reset_index()
    )
    grouped["swing_state_flag"] = True
    grouped["event_at"] = pd.to_datetime(grouped["event_at"], errors="coerce", utc=True)
    return grouped.sort_values(["game_id", "event_index", "player_key"]).reset_index(drop=True)


def _attach_run_swing_flags(run_segments: pd.DataFrame, swing_events: pd.DataFrame) -> pd.DataFrame:
    if run_segments.empty:
        return run_segments
    if swing_events.empty:
        run_segments["contains_swing_state"] = False
        run_segments["swing_event_count"] = 0
        return run_segments

    swing_index = swing_events.groupby("game_id")["event_index"].apply(list).to_dict()
    run_segments = run_segments.copy()
    contains_flags: list[bool] = []
    swing_counts: list[int] = []
    for _, row in run_segments.iterrows():
        event_indexes = swing_index.get(str(row["game_id"]), [])
        run_start = _safe_int(row.get("start_event_index")) or -1
        run_end = _safe_int(row.get("end_event_index")) or -1
        matching = [event_index for event_index in event_indexes if run_start <= event_index <= run_end]
        contains_flags.append(bool(matching))
        swing_counts.append(len(matching))
    run_segments["contains_swing_state"] = contains_flags
    run_segments["swing_event_count"] = swing_counts
    return run_segments

def _build_player_presence_summary(swing_events: pd.DataFrame, run_segments: pd.DataFrame) -> pd.DataFrame:
    if swing_events.empty:
        return pd.DataFrame(
            columns=[
                "player_key",
                "player_name",
                "team_slug",
                "team_id",
                "swing_event_count",
                "swing_game_count",
                "avg_price_delta_from_open",
                "avg_swing_state_side_count",
                "run_start_count",
                "run_stop_count",
            ]
        )

    swing_summary = (
        swing_events.groupby(["player_key", "player_name", "team_slug", "team_id"], sort=True)
        .agg(
            swing_event_count=("event_index", "count"),
            swing_game_count=("game_id", "nunique"),
            avg_price_delta_from_open=("price_delta_from_open", "mean"),
            avg_swing_state_side_count=("swing_state_side_count", "mean"),
        )
        .reset_index()
    )

    if run_segments.empty:
        swing_summary["run_start_count"] = 0
        swing_summary["run_stop_count"] = 0
        return swing_summary.sort_values(["swing_event_count", "player_name"], ascending=[False, True]).reset_index(drop=True)

    swing_runs = run_segments[run_segments["contains_swing_state"]].copy()
    run_start_rows = (
        swing_runs.groupby(["start_player_key", "start_player_name"], sort=True)
        .size()
        .reset_index(name="run_start_count")
        .rename(columns={"start_player_key": "player_key", "start_player_name": "player_name"})
    )
    run_stop_rows = (
        swing_runs.groupby(["stop_player_key", "stop_player_name"], sort=True)
        .size()
        .reset_index(name="run_stop_count")
        .rename(columns={"stop_player_key": "player_key", "stop_player_name": "player_name"})
    )
    summary = swing_summary.merge(run_start_rows, on=["player_key", "player_name"], how="left").merge(
        run_stop_rows, on=["player_key", "player_name"], how="left"
    )
    summary["run_start_count"] = summary["run_start_count"].fillna(0).astype(int)
    summary["run_stop_count"] = summary["run_stop_count"].fillna(0).astype(int)
    return summary.sort_values(["swing_event_count", "player_name"], ascending=[False, True]).reset_index(drop=True)


def _proxy_score_from_stats(stats_json: dict[str, Any]) -> tuple[float | None, str | None]:
    for key in STATUS_KEYS:
        value = stats_json.get(key)
        if value in (None, ""):
            continue
        text = _normalize_text(value)
        if not text:
            continue
        if any(token in text for token in ("out", "inactive", "dnp", "not active")):
            return 1.0, f"{key}:{text}"
        if any(token in text for token in ("questionable", "day to day")):
            return 0.5, f"{key}:{text}"
        if any(token in text for token in ("probable", "available", "healthy", "active")):
            return 0.0, f"{key}:{text}"
    if "is_active" in stats_json and stats_json.get("is_active") is not None:
        return (0.0 if bool(stats_json.get("is_active")) else 1.0), "is_active"
    if "available" in stats_json and stats_json.get("available") is not None:
        return (0.0 if bool(stats_json.get("available")) else 1.0), "available"
    return None, None


def _build_absence_proxy_summary(player_stats_df: pd.DataFrame) -> pd.DataFrame:
    if player_stats_df.empty:
        return pd.DataFrame(
            columns=[
                "player_key",
                "player_name",
                "team_slug",
                "team_id",
                "snapshot_count",
                "avg_absence_proxy_score",
                "proxy_basis",
            ]
        )

    rows: list[dict[str, Any]] = []
    for _, row in player_stats_df.iterrows():
        stats_json = _safe_json(row.get("stats_json"))
        proxy_score, basis = _proxy_score_from_stats(stats_json)
        if proxy_score is None:
            continue
        player_id = _safe_int(row.get("player_id"))
        player_name = row.get("player_name")
        player_key = f"id:{player_id}" if player_id is not None else f"name:{_normalize_text(player_name)}"
        rows.append(
            {
                "player_key": player_key,
                "player_name": player_name,
                "team_slug": row.get("team_slug"),
                "team_id": _safe_int(row.get("team_id")),
                "snapshot_count": 1,
                "absence_proxy_score": proxy_score,
                "proxy_basis": basis,
            }
        )

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    summary = (
        frame.groupby(["player_key", "player_name", "team_slug", "team_id"], sort=True)
        .agg(
            snapshot_count=("snapshot_count", "sum"),
            avg_absence_proxy_score=("absence_proxy_score", "mean"),
            proxy_basis=("proxy_basis", lambda values: ", ".join(sorted({str(value) for value in values if value not in (None, "")}))),
        )
        .reset_index()
    )
    return summary.sort_values(["avg_absence_proxy_score", "player_name"], ascending=[False, True]).reset_index(drop=True)


def _build_absence_proxy_deltas(player_presence_summary: pd.DataFrame, absence_proxy_summary: pd.DataFrame) -> pd.DataFrame:
    if player_presence_summary.empty or absence_proxy_summary.empty:
        return pd.DataFrame(
            columns=[
                "team_slug",
                "proxy_bucket",
                "sample_players",
                "avg_swing_event_count",
                "avg_run_start_count",
                "avg_run_stop_count",
            ]
        )

    joined = player_presence_summary.merge(
        absence_proxy_summary[["player_key", "avg_absence_proxy_score", "proxy_basis"]],
        on="player_key",
        how="inner",
    )
    if joined.empty:
        return pd.DataFrame(
            columns=[
                "team_slug",
                "proxy_bucket",
                "sample_players",
                "avg_swing_event_count",
                "avg_run_start_count",
                "avg_run_stop_count",
            ]
        )
    joined["proxy_bucket"] = pd.cut(
        joined["avg_absence_proxy_score"].fillna(0.0),
        bins=[-0.001, 0.25, 0.75, 1.001],
        labels=["low_proxy", "mid_proxy", "high_proxy"],
    )
    summary = (
        joined.groupby(["team_slug", "proxy_bucket"], sort=True, observed=True)
        .agg(
            sample_players=("player_key", "nunique"),
            avg_swing_event_count=("swing_event_count", "mean"),
            avg_run_start_count=("run_start_count", "mean"),
            avg_run_stop_count=("run_stop_count", "mean"),
        )
        .reset_index()
    )
    return summary.sort_values(["team_slug", "proxy_bucket"]).reset_index(drop=True)


def build_player_impact_shadow_result(
    *,
    state_df: pd.DataFrame,
    play_by_play_df: pd.DataFrame,
    player_stats_df: pd.DataFrame,
    season: str,
    season_phase: str,
    analysis_version: str,
    swing_threshold: float = 0.10,
) -> PlayerImpactShadowResult:
    swing_events = _build_swing_state_events(state_df.copy(), play_by_play_df.copy(), swing_threshold=swing_threshold)
    run_segments = _attach_run_swing_flags(_build_run_segments(play_by_play_df.copy()), swing_events)
    player_presence_summary = _build_player_presence_summary(swing_events, run_segments)
    absence_proxy_summary = _build_absence_proxy_summary(player_stats_df.copy())
    absence_proxy_deltas = _build_absence_proxy_deltas(player_presence_summary, absence_proxy_summary)

    summary = {
        "season": season,
        "season_phase": season_phase,
        "analysis_version": analysis_version,
        "shadow_mode": True,
        "experimental_label": "experimental_shadow",
        "games_total": int(state_df["game_id"].astype(str).nunique()) if "game_id" in state_df.columns else 0,
        "state_rows_total": int(len(state_df)),
        "swing_state_rows_total": int(len(swing_events)),
        "run_segments_total": int(len(run_segments)),
        "swing_run_segments_total": int(run_segments["contains_swing_state"].fillna(False).sum()) if not run_segments.empty else 0,
        "player_presence_rows_total": int(len(player_presence_summary)),
        "absence_proxy_rows_total": int(len(absence_proxy_summary)),
        "absence_proxy_delta_rows_total": int(len(absence_proxy_deltas)),
    }

    return PlayerImpactShadowResult(
        swing_state_events=swing_events,
        run_segments=run_segments,
        player_presence_summary=player_presence_summary,
        absence_proxy_summary=absence_proxy_summary,
        absence_proxy_deltas=absence_proxy_deltas,
        summary=summary,
    )

def _render_top_rows(frame: pd.DataFrame, *, limit: int = 5) -> str:
    if frame.empty:
        return "_No rows._"
    sample = frame.head(limit).copy()
    return "\n".join(
        "- " + ", ".join(f"{column}={row[column]}" for column in sample.columns[: min(len(sample.columns), 8)])
        for row in sample.to_dict(orient="records")
    )


def render_player_impact_shadow_markdown(result: PlayerImpactShadowResult) -> str:
    lines = [
        "# NBA Player Impact Shadow Artifact",
        "",
        "Experimental shadow lane. Correlational only. No causal injury claims.",
        "",
        "## Summary",
        "",
        f"- games_total: {result.summary.get('games_total', 0)}",
        f"- state_rows_total: {result.summary.get('state_rows_total', 0)}",
        f"- swing_state_rows_total: {result.summary.get('swing_state_rows_total', 0)}",
        f"- run_segments_total: {result.summary.get('run_segments_total', 0)}",
        f"- swing_run_segments_total: {result.summary.get('swing_run_segments_total', 0)}",
        f"- player_presence_rows_total: {result.summary.get('player_presence_rows_total', 0)}",
        f"- absence_proxy_rows_total: {result.summary.get('absence_proxy_rows_total', 0)}",
        "",
        "## Swing-State Presence",
        "",
        _render_top_rows(result.player_presence_summary),
        "",
        "## Run Segments",
        "",
        _render_top_rows(result.run_segments),
        "",
        "## Absence Proxies",
        "",
        _render_top_rows(result.absence_proxy_summary),
        "",
        "## Absence-Proxy Deltas",
        "",
        _render_top_rows(result.absence_proxy_deltas),
        "",
        "## Claim Boundaries",
        "",
        "- Correlational: yes",
        "- Causal: no",
        "- Injury causality claims: not supported",
    ]
    return "\n".join(lines).strip() + "\n"


def write_player_impact_shadow_artifacts(output_dir: Path, result: PlayerImpactShadowResult) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, Any] = {}
    artifacts["json"] = write_json(output_dir / "player_impact_shadow_summary.json", result.payload())
    artifacts["markdown"] = write_markdown(output_dir / "player_impact_shadow.md", render_player_impact_shadow_markdown(result))
    artifacts["swing_state_events"] = write_frame(output_dir / "player_impact_shadow_swing_state_events", result.swing_state_events)
    artifacts["run_segments"] = write_frame(output_dir / "player_impact_shadow_run_segments", result.run_segments)
    artifacts["player_presence_summary"] = write_frame(
        output_dir / "player_impact_shadow_player_presence_summary", result.player_presence_summary
    )
    artifacts["absence_proxy_summary"] = write_frame(
        output_dir / "player_impact_shadow_absence_proxy_summary", result.absence_proxy_summary
    )
    artifacts["absence_proxy_deltas"] = write_frame(
        output_dir / "player_impact_shadow_absence_proxy_deltas", result.absence_proxy_deltas
    )
    return artifacts


def build_player_impact_shadow_artifact(
    *,
    season: str = DEFAULT_SEASON,
    season_phase: str = DEFAULT_SEASON_PHASE,
    analysis_version: str = ANALYSIS_VERSION,
    output_root: str | None = None,
    swing_threshold: float = 0.10,
) -> dict[str, Any]:
    with managed_connection() as connection:
        state_df = _load_state_panel_df(connection, season=season, season_phase=season_phase, analysis_version=analysis_version)
        play_by_play_df = _load_play_by_play_df(connection, season=season, season_phase=season_phase)
        player_stats_df = _load_player_stats_df(connection, season=season)

    result = build_player_impact_shadow_result(
        state_df=state_df,
        play_by_play_df=play_by_play_df,
        player_stats_df=player_stats_df,
        season=season,
        season_phase=season_phase,
        analysis_version=analysis_version,
        swing_threshold=swing_threshold,
    )
    output_dir = ensure_output_dir(output_root, season, season_phase, analysis_version) / "player_impact_shadow"
    artifacts = write_player_impact_shadow_artifacts(output_dir, result)
    payload = result.payload()
    payload["artifacts"] = artifacts
    payload["output_dir"] = str(output_dir)
    return to_jsonable(payload)

