from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.data.pipelines.daily.wnba.analysis.contracts import (
    WNBA_ANALYSIS_VERSION,
    WnbaLaneSpec,
    default_shadow_lane_specs,
)


_NAMESPACE = uuid.UUID("ec3d4e4e-8b42-49c1-a359-20f031f7c898")


def _uuid_for(*parts: Any) -> str:
    return str(uuid.uuid5(_NAMESPACE, "|".join(str(part) for part in parts)))


def _safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    if value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int | None = None) -> int | None:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    if value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def build_wnba_lane_registry(
    lane_specs: tuple[WnbaLaneSpec, ...] | None = None,
) -> dict[str, WnbaLaneSpec]:
    specs = lane_specs or default_shadow_lane_specs()
    return {lane.family: lane for lane in specs}


def _recent_net_points(row: pd.Series) -> int | None:
    for column in ("recent_net_points_5_events", "net_points_last_5_events", "recent_net_points"):
        value = _safe_int(row.get(column))
        if value is not None:
            return value
    return None


def _non_price_candidate_blockers(row: pd.Series, lane: WnbaLaneSpec) -> list[str]:
    blockers: list[str] = []
    score_diff = _safe_int(row.get("score_diff"))
    if lane.min_score_diff is not None and (score_diff is None or score_diff < lane.min_score_diff):
        blockers.append("score_diff_below_lane_floor")
    if lane.max_score_diff is not None and (score_diff is None or score_diff > lane.max_score_diff):
        blockers.append("score_diff_above_lane_ceiling")

    period = _safe_int(row.get("period"))
    if lane.min_period is not None and (period is None or period < lane.min_period):
        blockers.append("period_before_lane_window")
    if lane.max_period is not None and (period is None or period > lane.max_period):
        blockers.append("period_after_lane_window")

    seconds_to_game_end = _safe_int(row.get("seconds_to_game_end"))
    if lane.min_seconds_to_game_end is not None and (
        seconds_to_game_end is None or seconds_to_game_end < lane.min_seconds_to_game_end
    ):
        blockers.append("seconds_to_game_end_below_lane_floor")
    if lane.max_seconds_to_game_end is not None and (
        seconds_to_game_end is None or seconds_to_game_end > lane.max_seconds_to_game_end
    ):
        blockers.append("seconds_to_game_end_above_lane_ceiling")

    clock_elapsed = _safe_int(row.get("clock_elapsed_seconds"))
    if lane.min_clock_elapsed_seconds is not None and (
        clock_elapsed is None or clock_elapsed < lane.min_clock_elapsed_seconds
    ):
        blockers.append("clock_elapsed_before_lane_window")
    if lane.max_clock_elapsed_seconds is not None and (
        clock_elapsed is None or clock_elapsed > lane.max_clock_elapsed_seconds
    ):
        blockers.append("clock_elapsed_after_lane_window")

    recent_net = _recent_net_points(row)
    if lane.min_recent_net_points is not None and (recent_net is None or recent_net < lane.min_recent_net_points):
        blockers.append("recent_net_points_below_lane_floor")
    if lane.max_recent_net_points is not None and (recent_net is None or recent_net > lane.max_recent_net_points):
        blockers.append("recent_net_points_above_lane_ceiling")
    return blockers


def _price_candidate_status(row: pd.Series, lane: WnbaLaneSpec) -> tuple[str, list[str]]:
    blockers: list[str] = []
    price = _safe_float(row.get("team_price"))
    if price is None:
        if lane.requires_clob:
            blockers.append("missing_wnba_clob_price_path")
        return "blocked" if blockers else "entry_candidate", blockers
    if lane.entry_price_min is not None and price < lane.entry_price_min:
        return "no_signal", ["price_below_lane_floor"]
    if lane.entry_price_max is not None and price > lane.entry_price_max:
        return "no_signal", ["price_above_lane_ceiling"]

    spread = _safe_float(row.get("spread"))
    if lane.max_spread is not None and spread is not None and spread > lane.max_spread:
        return "blocked", ["spread_above_lane_ceiling"]

    price_delta = _safe_float(row.get("price_delta_from_open"))
    if lane.min_price_delta_from_open is not None:
        if price_delta is None:
            return "blocked", ["missing_opening_clob_price"]
        if price_delta < lane.min_price_delta_from_open:
            return "no_signal", ["price_delta_from_open_below_lane_floor"]
    if lane.max_price_delta_from_open is not None:
        if price_delta is None:
            return "blocked", ["missing_opening_clob_price"]
        if price_delta > lane.max_price_delta_from_open:
            return "no_signal", ["price_delta_from_open_above_lane_ceiling"]
    return "entry_candidate", []


def _target_price(entry_price: float | None, lane: WnbaLaneSpec) -> float | None:
    if entry_price is None:
        return None
    return min(0.99, entry_price + max(lane.min_target_move, entry_price * lane.target_return_fraction))


def _stop_price(entry_price: float | None, lane: WnbaLaneSpec) -> float | None:
    if entry_price is None or lane.stop_loss is None:
        return None
    return max(0.01, entry_price - lane.stop_loss)


def evaluate_wnba_lane_signal(
    row: pd.Series | dict[str, Any],
    lane: WnbaLaneSpec,
    *,
    analysis_version: str = WNBA_ANALYSIS_VERSION,
    computed_at: datetime | None = None,
) -> dict[str, Any]:
    computed_at = computed_at or datetime.now(timezone.utc)
    series = row if isinstance(row, pd.Series) else pd.Series(row)
    non_price_blockers = _non_price_candidate_blockers(series, lane)
    entry_price = _safe_float(series.get("team_price"))
    signal_status = "no_signal"
    blockers = list(non_price_blockers)
    if not non_price_blockers:
        signal_status, blockers = _price_candidate_status(series, lane)
    elif "period_" in ",".join(non_price_blockers) or "score_diff_" in ",".join(non_price_blockers):
        signal_status = "no_signal"

    game_id = str(series.get("game_id") or "")
    team_side = str(series.get("team_side") or "")
    state_index = _safe_int(series.get("state_index"), 0) or 0
    features = {
        "period": _safe_int(series.get("period")),
        "clock": series.get("clock"),
        "clock_elapsed_seconds": _safe_int(series.get("clock_elapsed_seconds")),
        "seconds_to_game_end": _safe_int(series.get("seconds_to_game_end")),
        "score_diff": _safe_int(series.get("score_diff")),
        "score_diff_bucket": series.get("score_diff_bucket"),
        "context_bucket": series.get("context_bucket"),
        "recent_net_points": _recent_net_points(series),
        "team_price": entry_price,
        "spread": _safe_float(series.get("spread")),
        "opening_price": _safe_float(series.get("opening_price")),
        "price_delta_from_open": _safe_float(series.get("price_delta_from_open")),
        "action_type": series.get("action_type"),
        "sub_type": series.get("sub_type"),
        "player_id": _safe_int(series.get("player_id")),
    }
    return {
        "lane_signal_id": _uuid_for(lane.lane_id, game_id, team_side, state_index),
        "game_id": game_id,
        "team_side": team_side,
        "state_index": state_index,
        "analysis_version": analysis_version,
        "computed_at": computed_at,
        "lane_id": lane.lane_id,
        "family": lane.family,
        "signal_status": signal_status,
        "signal_type": "long_yes_shadow" if signal_status == "entry_candidate" else None,
        "shadow_only": lane.shadow_only,
        "orders_allowed": lane.orders_allowed,
        "requires_clob": lane.requires_clob,
        "requires_trade_microstructure": lane.requires_trade_microstructure,
        "entry_price": entry_price,
        "target_price": _target_price(entry_price, lane),
        "stop_price": _stop_price(entry_price, lane),
        "score_diff": features["score_diff"],
        "period": features["period"],
        "seconds_to_game_end": features["seconds_to_game_end"],
        "blockers_json": blockers,
        "features_json": features,
        "lane_config_json": asdict(lane),
    }


def build_wnba_lane_signal_rows(
    state_panel_df: pd.DataFrame,
    *,
    lane_specs: tuple[WnbaLaneSpec, ...] | None = None,
    include_no_signal: bool = False,
    analysis_version: str = WNBA_ANALYSIS_VERSION,
    computed_at: datetime | None = None,
) -> pd.DataFrame:
    if state_panel_df.empty:
        return pd.DataFrame()
    computed_at = computed_at or datetime.now(timezone.utc)
    specs = lane_specs or default_shadow_lane_specs()
    rows: list[dict[str, Any]] = []
    for _, state_row in state_panel_df.iterrows():
        for lane in specs:
            signal = evaluate_wnba_lane_signal(
                state_row,
                lane,
                analysis_version=analysis_version,
                computed_at=computed_at,
            )
            if include_no_signal or signal["signal_status"] != "no_signal":
                rows.append(signal)
    return pd.DataFrame(rows)


__all__ = [
    "build_wnba_lane_registry",
    "build_wnba_lane_signal_rows",
    "evaluate_wnba_lane_signal",
]
