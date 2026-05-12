from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.data.pipelines.daily.wnba.analysis.contracts import WNBA_FEATURE_VERSION


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: bool | None) -> bool | None:
    return value if value is not None else None


def build_wnba_pbp_ml_feature_rows(
    state_panel_df: pd.DataFrame,
    *,
    horizon_states: int = 12,
    feature_version: str = WNBA_FEATURE_VERSION,
    computed_at: datetime | None = None,
) -> pd.DataFrame:
    """Build PBP-tagged short-horizon ML rows from WNBA state panel rows."""
    computed_at = computed_at or datetime.now(timezone.utc)
    if state_panel_df.empty:
        return pd.DataFrame()
    required = {"game_id", "team_side", "state_index"}
    missing = required - set(state_panel_df.columns)
    if missing:
        raise ValueError(f"state_panel_df missing required columns: {sorted(missing)}")

    rows: list[dict[str, Any]] = []
    sort_cols = ["game_id", "team_side", "state_index"]
    work = state_panel_df.copy().sort_values(sort_cols).reset_index(drop=True)
    for (_game_id, _team_side), group in work.groupby(["game_id", "team_side"], sort=False):
        group = group.sort_values("state_index").reset_index(drop=True)
        prices = [_safe_float(value) for value in group.get("team_price", pd.Series(dtype=float)).tolist()]
        for idx, state in group.iterrows():
            current_price = prices[idx] if idx < len(prices) else None
            future_idx = min(idx + max(1, horizon_states), len(prices) - 1)
            future_price = prices[future_idx] if future_idx > idx and future_idx < len(prices) else None
            if current_price is None:
                label_status = "missing_current_clob_price"
                price_delta = None
            elif future_price is None:
                label_status = "missing_future_clob_price"
                price_delta = None
            else:
                label_status = "labeled"
                price_delta = future_price - current_price
            crossed_50 = None
            if current_price is not None and future_price is not None:
                crossed_50 = (current_price < 0.50 <= future_price) or (current_price >= 0.50 > future_price)

            feature_payload = {
                "period": _safe_int(state.get("period")),
                "seconds_to_game_end": _safe_float(state.get("seconds_to_game_end")),
                "score_diff": _safe_int(state.get("score_diff")),
                "recent_net_points_5_events": _safe_int(state.get("recent_net_points_5_events")),
                "action_type": state.get("action_type"),
                "sub_type": state.get("sub_type"),
                "player_id": _safe_int(state.get("player_id")),
                "team_tricode": state.get("team_tricode"),
                "opponent_tricode": state.get("opponent_tricode"),
                "team_price": current_price,
                "spread": _safe_float(state.get("spread")),
                "price_mode": state.get("price_mode"),
            }
            rows.append(
                {
                    "game_id": state.get("game_id"),
                    "team_side": state.get("team_side"),
                    "state_index": _safe_int(state.get("state_index")),
                    "feature_version": feature_version,
                    "computed_at": computed_at,
                    "period": _safe_int(state.get("period")),
                    "clock": state.get("clock"),
                    "seconds_to_game_end": _safe_float(state.get("seconds_to_game_end")),
                    "score_diff": _safe_int(state.get("score_diff")),
                    "recent_net_points": _safe_int(state.get("recent_net_points_5_events")),
                    "action_type": state.get("action_type"),
                    "sub_type": state.get("sub_type"),
                    "player_id": _safe_int(state.get("player_id")),
                    "player_name": state.get("player_name"),
                    "team_tricode": state.get("team_tricode"),
                    "opponent_tricode": state.get("opponent_tricode"),
                    "team_price": current_price,
                    "best_bid": _safe_float(state.get("best_bid")),
                    "best_ask": _safe_float(state.get("best_ask")),
                    "spread": _safe_float(state.get("spread")),
                    "label_horizon_states": horizon_states,
                    "label_price_delta": price_delta,
                    "label_up_2c": _bool_or_none(price_delta >= 0.02 if price_delta is not None else None),
                    "label_down_2c": _bool_or_none(price_delta <= -0.02 if price_delta is not None else None),
                    "label_crossed_50c": crossed_50,
                    "label_status": label_status,
                    "features_json": feature_payload,
                    "raw_state_json": state.to_dict(),
                }
            )
    return pd.DataFrame(rows)


def summarize_ml_training_readiness(feature_df: pd.DataFrame) -> dict[str, Any]:
    if feature_df.empty:
        return {
            "status": "blocked",
            "feature_rows": 0,
            "labeled_rows": 0,
            "distinct_games": 0,
            "blockers": ["missing_feature_rows"],
        }
    labeled = feature_df[feature_df["label_status"] == "labeled"]
    blockers: list[str] = []
    if labeled.empty:
        blockers.append("missing_labeled_clob_price_windows")
    if int(labeled["game_id"].nunique()) < 40:
        blockers.append("insufficient_distinct_games_for_wnba_ml")
    if len(labeled) < 5000:
        blockers.append("insufficient_labeled_rows_for_wnba_ml")
    return {
        "status": "ready_for_experiment" if not blockers else "blocked",
        "feature_rows": int(len(feature_df)),
        "labeled_rows": int(len(labeled)),
        "distinct_games": int(labeled["game_id"].nunique()) if not labeled.empty else 0,
        "blockers": blockers,
    }
