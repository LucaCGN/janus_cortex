from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.data.pipelines.daily.wnba.analysis.contracts import (
    WNBA_ANALYSIS_VERSION,
    WNBA_OVERTIME_PERIOD_SECONDS,
    WNBA_REGULATION_GAME_SECONDS,
    WNBA_REGULATION_PERIOD_SECONDS,
)


def _safe_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _clock_remaining_seconds(period: int | None, clock: str | None) -> int | None:
    if period is None or not clock:
        return None
    raw = str(clock)
    if not raw.startswith("PT") or "M" not in raw:
        return None
    try:
        minute_part = raw.split("M")[0].replace("PT", "")
        second_part = raw.split("M", maxsplit=1)[1].replace("S", "")
        return int(float(minute_part) * 60 + float(second_part))
    except (IndexError, ValueError):
        return None


def wnba_seconds_to_game_end(period: int | None, clock: str | None) -> int | None:
    if period is None or period <= 0:
        return None
    seconds_in_period = _clock_remaining_seconds(period, clock)
    if seconds_in_period is None:
        return None
    if period <= 4:
        return seconds_in_period + max(0, 4 - period) * WNBA_REGULATION_PERIOD_SECONDS
    return seconds_in_period


def wnba_clock_elapsed_seconds(period: int | None, clock: str | None) -> int | None:
    if period is None or period <= 0:
        return None
    seconds_to_end = wnba_seconds_to_game_end(period, clock)
    if seconds_to_end is None:
        return None
    if period <= 4:
        return WNBA_REGULATION_GAME_SECONDS - seconds_to_end
    overtime_elapsed = max(0, period - 5) * WNBA_OVERTIME_PERIOD_SECONDS
    return WNBA_REGULATION_GAME_SECONDS + overtime_elapsed + (WNBA_OVERTIME_PERIOD_SECONDS - seconds_to_end)


def _score_diff_bucket(score_diff: int | None) -> str:
    if score_diff is None:
        return "unknown"
    if score_diff <= -12:
        return "trail_12_plus"
    if score_diff <= -7:
        return "trail_7_11"
    if score_diff <= -3:
        return "trail_3_6"
    if score_diff <= -1:
        return "trail_1_2"
    if score_diff == 0:
        return "tied"
    if score_diff <= 2:
        return "lead_1_2"
    if score_diff <= 6:
        return "lead_3_6"
    if score_diff <= 11:
        return "lead_7_11"
    return "lead_12_plus"


def _market_points_for_row(
    market_df: pd.DataFrame | None,
    *,
    game_id: str,
    team_side: str,
    event_at: datetime | None,
) -> dict[str, Any]:
    if market_df is None or market_df.empty or event_at is None:
        return {"price_mode": "missing_clob"}
    required = {"game_id", "team_side", "captured_at"}
    if not required.issubset(market_df.columns):
        return {"price_mode": "missing_clob"}
    work = market_df[
        (market_df["game_id"].astype(str) == str(game_id))
        & (market_df["team_side"].astype(str) == team_side)
    ].copy()
    if work.empty:
        return {"price_mode": "missing_clob"}
    work["_captured_at"] = work["captured_at"].apply(_safe_dt)
    work = work[work["_captured_at"].notna()]
    if work.empty:
        return {"price_mode": "missing_clob"}
    work["_age_seconds"] = work["_captured_at"].apply(lambda ts: abs((event_at - ts).total_seconds()))
    nearest = work.sort_values("_age_seconds").iloc[0]
    best_bid = _safe_float(nearest.get("best_bid"))
    best_ask = _safe_float(nearest.get("best_ask"))
    mid_price = _safe_float(nearest.get("mid_price"))
    if mid_price is None and best_bid is not None and best_ask is not None:
        mid_price = (best_bid + best_ask) / 2.0
    spread = _safe_float(nearest.get("spread"))
    if spread is None and best_bid is not None and best_ask is not None:
        spread = best_ask - best_bid
    return {
        "price_mode": "nearest_clob_tick",
        "team_price": mid_price,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "market_captured_at": nearest["_captured_at"],
        "market_age_seconds": float(nearest["_age_seconds"]),
        "token_id": nearest.get("token_id"),
        "market_id": nearest.get("market_id"),
        "outcome_id": nearest.get("outcome_id"),
    }


def build_wnba_state_panel(
    pbp_df: pd.DataFrame,
    *,
    game: dict[str, Any],
    market_df: pd.DataFrame | None = None,
    analysis_version: str = WNBA_ANALYSIS_VERSION,
    computed_at: datetime | None = None,
) -> pd.DataFrame:
    """Build replay-ready WNBA state rows from normalized PBP, optionally joined to CLOB ticks."""
    computed_at = computed_at or datetime.now(timezone.utc)
    if pbp_df.empty:
        return pd.DataFrame()
    game_id = str(game.get("game_id") or pbp_df["game_id"].iloc[0])
    home_team_id = _safe_int(game.get("home_team_id"))
    away_team_id = _safe_int(game.get("away_team_id"))
    home_tri = str(game.get("home_team_tricode") or "HOME")
    away_tri = str(game.get("away_team_tricode") or "AWAY")

    work = pbp_df.copy().reset_index(drop=True)
    if "event_index" in work.columns:
        work = work.sort_values("event_index").reset_index(drop=True)

    rows: list[dict[str, Any]] = []
    recent_points: dict[str, list[int]] = {"home": [], "away": []}
    for source_index, pbp_row in work.iterrows():
        row = pbp_row.to_dict()
        period = _safe_int(row.get("period"))
        event_at = _safe_dt(row.get("time_actual"))
        points_home = _safe_int(row.get("points_home"), 0) or 0
        points_away = _safe_int(row.get("points_away"), 0) or 0
        recent_points["home"].append(points_home)
        recent_points["away"].append(points_away)
        recent_home = sum(recent_points["home"][-5:])
        recent_away = sum(recent_points["away"][-5:])
        home_score = _safe_int(row.get("home_score"), 0) or 0
        away_score = _safe_int(row.get("away_score"), 0) or 0
        clock = row.get("clock")
        seconds_to_game_end = wnba_seconds_to_game_end(period, str(clock) if clock is not None else None)
        clock_elapsed_seconds = wnba_clock_elapsed_seconds(period, str(clock) if clock is not None else None)
        scoring_side = "home" if points_home > points_away else "away" if points_away > points_home else None

        for team_side in ("home", "away"):
            is_home = team_side == "home"
            score_for = home_score if is_home else away_score
            score_against = away_score if is_home else home_score
            points_for = points_home if is_home else points_away
            points_against = points_away if is_home else points_home
            recent_for = recent_home if is_home else recent_away
            recent_against = recent_away if is_home else recent_home
            market = _market_points_for_row(market_df, game_id=game_id, team_side=team_side, event_at=event_at)
            score_diff = score_for - score_against
            rows.append(
                {
                    "game_id": game_id,
                    "team_side": team_side,
                    "state_index": int(source_index),
                    "analysis_version": analysis_version,
                    "computed_at": computed_at,
                    "event_index": _safe_int(row.get("event_index")),
                    "action_id": row.get("action_id"),
                    "event_at": event_at,
                    "period": period,
                    "period_label": f"Q{period}" if period and period <= 4 else f"OT{period - 4}" if period else None,
                    "clock": clock,
                    "clock_elapsed_seconds": clock_elapsed_seconds,
                    "seconds_to_game_end": seconds_to_game_end,
                    "team_id": home_team_id if is_home else away_team_id,
                    "team_tricode": home_tri if is_home else away_tri,
                    "opponent_team_id": away_team_id if is_home else home_team_id,
                    "opponent_tricode": away_tri if is_home else home_tri,
                    "score_for": score_for,
                    "score_against": score_against,
                    "score_diff": score_diff,
                    "score_diff_bucket": _score_diff_bucket(score_diff),
                    "scoring_side": scoring_side,
                    "points_scored": points_for,
                    "delta_for": points_for,
                    "delta_against": points_against,
                    "recent_team_points_5_events": recent_for,
                    "recent_opponent_points_5_events": recent_against,
                    "recent_net_points_5_events": recent_for - recent_against,
                    "player_id": _safe_int(row.get("person_id")),
                    "player_name": row.get("player_name"),
                    "action_type": row.get("action_type"),
                    "sub_type": row.get("sub_type"),
                    "substitution_direction": row.get("substitution_direction"),
                    "substitution_person_id": _safe_int(row.get("substitution_person_id")),
                    "substitution_player_name": row.get("substitution_player_name"),
                    "team_price": market.get("team_price"),
                    "best_bid": market.get("best_bid"),
                    "best_ask": market.get("best_ask"),
                    "spread": market.get("spread"),
                    "price_mode": market.get("price_mode"),
                    "market_age_seconds": market.get("market_age_seconds"),
                    "token_id": market.get("token_id"),
                    "market_id": market.get("market_id"),
                    "outcome_id": market.get("outcome_id"),
                    "backtest_eligible": market.get("team_price") is not None,
                    "raw_state_json": row,
                }
            )
    return pd.DataFrame(rows)
