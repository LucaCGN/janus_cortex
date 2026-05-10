from __future__ import annotations

from typing import Any

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import simulate_repeating_trade_loop
from app.data.pipelines.daily.nba.analysis.backtests.specs import TradeSelection


_ACTIVE_PERIODS = {"Q3", "Q4", "OT1", "OT2"}
_FAVORITE_OPEN_MIN = 0.55
_PRIOR_CONTROL_MIN_PRICE = 0.55
_ENTRY_MIN_PRICE = 0.10
_ENTRY_MAX_PRICE = 0.20
_MAX_TRAILING_SCORE_GAP = 10.0
_MIN_SECONDS_LEFT = 90.0
_TARGET_GAIN = 0.07
_STOP_LOSS = 0.04


def _select_favorite_floor_rebound_entry(group: pd.DataFrame) -> TradeSelection | None:
    opening_price = float(group.iloc[0]["opening_price"]) if pd.notna(group.iloc[0]["opening_price"]) else None
    prices = pd.to_numeric(group["team_price"], errors="coerce")
    score_diff = pd.to_numeric(group["score_diff"], errors="coerce")
    net_recent = pd.to_numeric(group["net_points_last_5_events"], errors="coerce").fillna(0.0)
    seconds_left = pd.to_numeric(group["seconds_to_game_end"], errors="coerce")
    period_labels = group["period_label"].fillna("").astype(str)

    if opening_price is None and prices.dropna().empty:
        return None
    resolved_opening = opening_price if opening_price is not None else float(prices.dropna().iloc[0])
    seen_control_price = resolved_opening >= _FAVORITE_OPEN_MIN

    for index, price in enumerate(prices.tolist()):
        if price is None or pd.isna(price):
            continue
        resolved_price = float(price)
        seen_control_price = seen_control_price or resolved_price >= _PRIOR_CONTROL_MIN_PRICE
        row = group.iloc[index]
        if not seen_control_price:
            continue
        if str(row["period_label"]) not in _ACTIVE_PERIODS:
            continue
        if not (_ENTRY_MIN_PRICE <= resolved_price <= _ENTRY_MAX_PRICE):
            continue
        current_score_diff = float(score_diff.iloc[index]) if pd.notna(score_diff.iloc[index]) else None
        if current_score_diff is None or current_score_diff < -_MAX_TRAILING_SCORE_GAP:
            continue
        if pd.isna(seconds_left.iloc[index]) or float(seconds_left.iloc[index]) < _MIN_SECONDS_LEFT:
            continue
        prior_window = prices.iloc[: index + 1].dropna()
        prior_high = float(prior_window.max()) if not prior_window.empty else resolved_price
        panic_drop = max(0.0, prior_high - resolved_price)
        target_price = min(0.95, resolved_price + _TARGET_GAIN)
        stop_price = max(0.01, resolved_price - _STOP_LOSS)
        signal_strength = (
            panic_drop * 100.0
            + (_ENTRY_MAX_PRICE - resolved_price) * 100.0
            + max(0.0, _MAX_TRAILING_SCORE_GAP + current_score_diff)
            + max(0.0, float(net_recent.iloc[index]))
        )
        return TradeSelection(
            entry_index=index,
            metadata={
                "favorite_open_min": _FAVORITE_OPEN_MIN,
                "prior_control_min_price": _PRIOR_CONTROL_MIN_PRICE,
                "entry_min_price": _ENTRY_MIN_PRICE,
                "entry_max_price": _ENTRY_MAX_PRICE,
                "target_price": target_price,
                "stop_price": stop_price,
                "target_gain": _TARGET_GAIN,
                "stop_loss": _STOP_LOSS,
                "max_trailing_score_gap": _MAX_TRAILING_SCORE_GAP,
                "min_seconds_left": _MIN_SECONDS_LEFT,
                "prior_high_price": prior_high,
                "panic_drop": panic_drop,
                "entry_score_diff": current_score_diff,
                "entry_price_mode": row.get("price_mode"),
                "signal_strength": signal_strength,
            },
        )
    return None


def _select_favorite_floor_rebound_exit(group: pd.DataFrame, selection: TradeSelection) -> int | None:
    target_price = float(selection.metadata["target_price"])
    stop_price = float(selection.metadata["stop_price"])
    future = group.iloc[selection.entry_index + 1 :]
    if future.empty:
        return int(len(group) - 1)

    for index, row in future.iterrows():
        price = row["team_price"]
        if pd.isna(price):
            continue
        resolved_price = float(price)
        seconds_left = row.get("seconds_to_game_end")
        if resolved_price >= target_price or resolved_price <= stop_price:
            return int(index)
        if pd.notna(seconds_left) and float(seconds_left) <= _MIN_SECONDS_LEFT:
            return int(index)
    return int(len(group) - 1)


def simulate_favorite_floor_rebound_trades(state_df: pd.DataFrame, *, slippage_cents: int) -> list[dict[str, Any]]:
    return simulate_repeating_trade_loop(
        state_df,
        strategy_family="favorite_floor_rebound",
        entry_rule="favorite_buy_10c_to_20c_when_score_gap_recoverable",
        exit_rule="plus_7c_or_minus_4c_or_late_flatten",
        slippage_cents=slippage_cents,
        entry_selector=_select_favorite_floor_rebound_entry,
        exit_selector=_select_favorite_floor_rebound_exit,
    )


__all__ = ["simulate_favorite_floor_rebound_trades"]
