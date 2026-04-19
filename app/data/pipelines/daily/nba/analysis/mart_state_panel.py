from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any, Iterable

import pandas as pd

from app.data.pipelines.daily.nba.analysis.contracts import (
    DEFAULT_LARGE_SWING_THRESHOLD,
    DEFAULT_LOOKAHEAD_STATES,
    DEFAULT_WINNER_THRESHOLDS,
    OVERTIME_PERIOD_SECONDS,
    REGULATION_PERIOD_SECONDS,
)


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
    raw = str(value).strip()
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _safe_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    parsed = _safe_datetime(value)
    if parsed is not None:
        return parsed.date()
    raw = str(value).strip()
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _mean_or_none(values: Iterable[Any]) -> float | None:
    clean = [float(value) for value in values if value is not None and not pd.isna(value)]
    if not clean:
        return None
    return float(sum(clean) / len(clean))


def _score_diff_bucket(score_diff: int | None) -> str:
    if score_diff is None:
        return "unknown"
    if score_diff <= -15:
        return "trail_15_plus"
    if score_diff <= -10:
        return "trail_10_14"
    if score_diff <= -5:
        return "trail_5_9"
    if score_diff <= -1:
        return "trail_1_4"
    if score_diff == 0:
        return "tied"
    if score_diff <= 4:
        return "lead_1_4"
    if score_diff <= 9:
        return "lead_5_9"
    if score_diff <= 14:
        return "lead_10_14"
    return "lead_15_plus"


def _period_duration_seconds(period: int | None) -> int:
    if period is None or period <= 4:
        return REGULATION_PERIOD_SECONDS
    return OVERTIME_PERIOD_SECONDS


def _total_game_duration_seconds(max_period: int | None) -> float | None:
    if max_period is None:
        return None
    total = 0.0
    for period in range(1, max_period + 1):
        total += float(_period_duration_seconds(period))
    return total


def _compute_lead_change_counts(timed_items: list[dict[str, Any]]) -> list[int]:
    lead_changes = 0
    previous_non_tied_sign = 0
    counts: list[int] = []
    for item in timed_items:
        home_score = _safe_int(item.get("home_score")) or 0
        away_score = _safe_int(item.get("away_score")) or 0
        sign = 1 if home_score > away_score else -1 if away_score > home_score else 0
        if sign != 0 and previous_non_tied_sign != 0 and sign != previous_non_tied_sign:
            lead_changes += 1
        if sign != 0:
            previous_non_tied_sign = sign
        counts.append(lead_changes)
    return counts


def find_stable_index(prices: list[float], threshold: float) -> int | None:
    if not prices:
        return None
    running_min = math.inf
    stable_index: int | None = None
    for index in range(len(prices) - 1, -1, -1):
        running_min = min(running_min, prices[index])
        if prices[index] >= threshold and running_min >= threshold:
            stable_index = index
    return stable_index


def _crossed_fifty_within_window(current_price: float, future_prices: list[float]) -> bool:
    current_flag = current_price >= 0.5
    return any((price >= 0.5) != current_flag for price in future_prices)


def compute_time_above_below_fifty(prices: list[float], event_times: list[datetime]) -> tuple[float | None, float | None]:
    if len(prices) < 2 or len(event_times) < 2:
        return None, None
    seconds_above = 0.0
    seconds_below = 0.0
    for index in range(len(prices) - 1):
        duration = max(0.0, (event_times[index + 1] - event_times[index]).total_seconds())
        if prices[index] >= 0.5:
            seconds_above += duration
        else:
            seconds_below += duration
    return seconds_above, seconds_below


def _state_sort_key(item: dict[str, Any], *, event_at: datetime, original_index: int) -> tuple[datetime, float | int, str, int]:
    event_index = _safe_int(item.get("event_index"))
    return (
        event_at,
        event_index if event_index is not None else math.inf,
        str(item.get("action_id") or ""),
        original_index,
    )


def _prepare_state_path(
    timed_items: list[dict[str, Any]],
    *,
    outcome_id: str,
) -> tuple[list[dict[str, Any]], list[datetime], list[float]]:
    valid_entries: list[tuple[dict[str, Any], datetime, float, int]] = []
    for original_index, item in enumerate(timed_items):
        market_point = item.get("market_points", {}).get(outcome_id)
        team_price = _safe_float(market_point.get("price") if market_point else None)
        event_at = _safe_datetime(item.get("time_actual"))
        if team_price is None or event_at is None:
            continue
        valid_entries.append((item, event_at, team_price, original_index))
    valid_entries.sort(key=lambda entry: _state_sort_key(entry[0], event_at=entry[1], original_index=entry[3]))
    valid_items = [entry[0] for entry in valid_entries]
    event_times = [entry[1] for entry in valid_entries]
    prices = [entry[2] for entry in valid_entries]
    return valid_items, event_times, prices


def build_state_rows_for_side(
    *,
    game: dict[str, Any],
    timed_items: list[dict[str, Any]],
    team_side: str,
    team_id: int | None,
    team_slug: str | None,
    opponent_team_id: int | None,
    opponent_team_slug: str | None,
    outcome_id: str,
    event_id: str | None,
    market_id: str | None,
    opening_price: float | None,
    opening_band: str | None,
    final_winner_flag: bool,
    season: str,
    season_phase: str,
    analysis_version: str,
    computed_at: datetime,
) -> list[dict[str, Any]]:
    if not timed_items:
        return []
    valid_items, event_times, prices = _prepare_state_path(timed_items, outcome_id=outcome_id)
    if not valid_items:
        return []

    lead_changes_counts = _compute_lead_change_counts(valid_items)
    max_period = max((_safe_int(item.get("period")) or 0) for item in valid_items) or None
    total_game_seconds = _total_game_duration_seconds(max_period)
    side_delta_for: list[int] = []
    side_delta_against: list[int] = []
    for item in valid_items:
        if team_side == "home":
            side_delta_for.append(_safe_int(item.get("delta_home")) or 0)
            side_delta_against.append(_safe_int(item.get("delta_away")) or 0)
        else:
            side_delta_for.append(_safe_int(item.get("delta_away")) or 0)
            side_delta_against.append(_safe_int(item.get("delta_home")) or 0)
    stable_indices = {
        threshold: find_stable_index(prices, threshold / 100.0) if final_winner_flag else None
        for threshold in DEFAULT_WINNER_THRESHOLDS
    }

    rows: list[dict[str, Any]] = []
    for index, item in enumerate(valid_items):
        market_point = item.get("market_points", {}).get(outcome_id) or {}
        team_price = prices[index]
        event_at = event_times[index]
        if team_side == "home":
            score_for = _safe_int(item.get("home_score")) or 0
            score_against = _safe_int(item.get("away_score")) or 0
        else:
            score_for = _safe_int(item.get("away_score")) or 0
            score_against = _safe_int(item.get("home_score")) or 0
        score_diff = score_for - score_against
        future_prices = prices[index + 1 : index + 1 + DEFAULT_LOOKAHEAD_STATES]
        path_prices = prices[index:]
        team_points_last_5 = int(sum(side_delta_for[max(0, index - 4) : index + 1]))
        opponent_points_last_5 = int(sum(side_delta_against[max(0, index - 4) : index + 1]))
        clock_elapsed = _safe_float(item.get("clock_elapsed_seconds"))
        rows.append(
            {
                "game_id": str(game["game_id"]),
                "team_side": team_side,
                "state_index": index,
                "team_id": team_id,
                "team_slug": team_slug,
                "opponent_team_id": opponent_team_id,
                "opponent_team_slug": opponent_team_slug,
                "event_id": event_id,
                "market_id": market_id,
                "outcome_id": outcome_id,
                "season": season,
                "season_phase": season_phase,
                "analysis_version": analysis_version,
                "computed_at": computed_at,
                "game_date": _safe_date(game.get("game_date")),
                "event_index": _safe_int(item.get("event_index")),
                "action_id": str(item.get("action_id")) if item.get("action_id") is not None else None,
                "event_at": event_at,
                "period": _safe_int(item.get("period")),
                "period_label": item.get("period_label"),
                "clock": item.get("clock"),
                "clock_elapsed_seconds": clock_elapsed,
                "seconds_to_game_end": max(total_game_seconds - clock_elapsed, 0.0) if total_game_seconds is not None and clock_elapsed is not None else None,
                "score_for": score_for,
                "score_against": score_against,
                "score_diff": score_diff,
                "score_diff_bucket": _score_diff_bucket(score_diff),
                "context_bucket": f"{item.get('period_label')}|{_score_diff_bucket(score_diff)}",
                "team_led_flag": score_diff > 0,
                "team_trailed_flag": score_diff < 0,
                "tied_flag": score_diff == 0,
                "market_favorite_flag": team_price >= 0.5,
                "scoreboard_control_mismatch_flag": (score_diff > 0 and team_price < 0.5) or (score_diff < 0 and team_price >= 0.5),
                "final_winner_flag": final_winner_flag,
                "scoring_side": item.get("scoring_side"),
                "points_scored": _safe_int(item.get("points_scored")),
                "delta_for": side_delta_for[index],
                "delta_against": side_delta_against[index],
                "lead_changes_so_far": lead_changes_counts[index],
                "team_points_last_5_events": team_points_last_5,
                "opponent_points_last_5_events": opponent_points_last_5,
                "net_points_last_5_events": team_points_last_5 - opponent_points_last_5,
                "opening_price": opening_price,
                "opening_band": opening_band,
                "team_price": team_price,
                "price_delta_from_open": (team_price - opening_price) if opening_price is not None else None,
                "abs_price_delta_from_open": abs(team_price - opening_price) if opening_price is not None else None,
                "price_mode": market_point.get("mode"),
                "gap_before_seconds": _safe_float(market_point.get("gap_before_seconds")),
                "gap_after_seconds": _safe_float(market_point.get("gap_after_seconds")),
                "mfe_from_state": max(path_prices) - team_price if path_prices else None,
                "mae_from_state": team_price - min(path_prices) if path_prices else None,
                "large_swing_next_12_states_flag": bool(future_prices) and any(abs(price - team_price) >= DEFAULT_LARGE_SWING_THRESHOLD for price in future_prices),
                "crossed_50c_next_12_states_flag": bool(future_prices) and _crossed_fifty_within_window(team_price, future_prices),
                "winner_stable_70_after_state_flag": final_winner_flag and stable_indices[70] is not None and index >= int(stable_indices[70]),
                "winner_stable_80_after_state_flag": final_winner_flag and stable_indices[80] is not None and index >= int(stable_indices[80]),
                "winner_stable_90_after_state_flag": final_winner_flag and stable_indices[90] is not None and index >= int(stable_indices[90]),
                "winner_stable_95_after_state_flag": final_winner_flag and stable_indices[95] is not None and index >= int(stable_indices[95]),
            }
        )
    return rows


def build_winner_definition_profile_rows(
    state_df: pd.DataFrame,
    *,
    season: str,
    season_phase: str,
    analysis_version: str,
    computed_at: datetime,
) -> list[dict[str, Any]]:
    if state_df.empty:
        return []
    winner_states = state_df[state_df["final_winner_flag"] == True].copy()
    if winner_states.empty:
        return []
    rows: list[dict[str, Any]] = []
    for threshold in DEFAULT_WINNER_THRESHOLDS:
        price_threshold = threshold / 100.0
        threshold_df = winner_states[winner_states["team_price"].fillna(-1.0) >= price_threshold].copy()
        if threshold_df.empty:
            continue
        stable_column = f"winner_stable_{threshold}_after_state_flag"
        for context_bucket, group in threshold_df.groupby("context_bucket"):
            stable_states = int(group[stable_column].fillna(False).sum())
            sample_states = int(len(group))
            stable_rate = stable_states / max(sample_states, 1)
            rows.append(
                {
                    "season": season,
                    "season_phase": season_phase,
                    "threshold_cents": int(threshold),
                    "context_bucket": str(context_bucket or "unknown"),
                    "analysis_version": analysis_version,
                    "computed_at": computed_at,
                    "sample_states": sample_states,
                    "distinct_games": int(group["game_id"].nunique()),
                    "stable_states": stable_states,
                    "stable_rate": stable_rate,
                    "reopen_rate": 1.0 - stable_rate,
                    "avg_score_diff": _mean_or_none(group["score_diff"].tolist()),
                    "avg_team_price": _mean_or_none(group["team_price"].tolist()),
                    "avg_seconds_to_game_end": _mean_or_none(group["seconds_to_game_end"].tolist()),
                    "notes_json": {
                        "price_threshold": price_threshold,
                    },
                }
            )
    rows.sort(key=lambda row: (row["threshold_cents"], row["context_bucket"]))
    return rows


__all__ = [
    "build_state_rows_for_side",
    "build_winner_definition_profile_rows",
    "compute_time_above_below_fifty",
    "find_stable_index",
]
