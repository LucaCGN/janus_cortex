from __future__ import annotations

from typing import Any

import pandas as pd

from app.data.pipelines.daily.nba.analysis.contracts import (
    DEFAULT_DYNAMIC_INVERSION_EXIT_THRESHOLD,
    DEFAULT_DYNAMIC_INVERSION_LOW_ENTRY_THRESHOLD,
    DEFAULT_DYNAMIC_INVERSION_LOW_OPEN_CUT,
    DEFAULT_DYNAMIC_INVERSION_MIN_MOMENTUM,
    DEFAULT_DYNAMIC_INVERSION_MIN_SCORE_DIFF,
    DEFAULT_DYNAMIC_INVERSION_STANDARD_ENTRY_THRESHOLD,
)
from app.data.pipelines.daily.nba.analysis.backtests.engine import simulate_trade_loop
from app.data.pipelines.daily.nba.analysis.backtests.specs import TradeSelection


def _resolve_inversion_entry_threshold(opening_price: float) -> float:
    if opening_price < DEFAULT_DYNAMIC_INVERSION_LOW_OPEN_CUT:
        return DEFAULT_DYNAMIC_INVERSION_LOW_ENTRY_THRESHOLD
    return DEFAULT_DYNAMIC_INVERSION_STANDARD_ENTRY_THRESHOLD


def _select_inversion_entry(group: pd.DataFrame) -> TradeSelection | None:
    opening_price = float(group.iloc[0]["opening_price"]) if pd.notna(group.iloc[0]["opening_price"]) else None
    if opening_price is None or opening_price >= 0.5:
        return None
    entry_threshold = _resolve_inversion_entry_threshold(opening_price)
    previous_price = opening_price
    prices = pd.to_numeric(group["team_price"], errors="coerce").tolist()
    for index, price in enumerate(prices):
        if price is None or pd.isna(price):
            previous_price = price
            continue
        if index == 0:
            previous_price = float(price)
            continue
        row = group.iloc[index]
        if previous_price < entry_threshold and float(price) >= entry_threshold:
            if float(row["net_points_last_5_events"]) < DEFAULT_DYNAMIC_INVERSION_MIN_MOMENTUM:
                previous_price = float(price)
                continue
            if float(row["score_diff"]) < DEFAULT_DYNAMIC_INVERSION_MIN_SCORE_DIFF:
                previous_price = float(price)
                continue
            resolved_price = float(price)
            signal_strength = (
                ((resolved_price - entry_threshold) * 100.0)
                + max(0.0, float(row["net_points_last_5_events"]))
                + max(0.0, float(row["score_diff"])) * 0.5
            )
            return TradeSelection(
                entry_index=index,
                metadata={
                    "entry_threshold": entry_threshold,
                    "exit_threshold": DEFAULT_DYNAMIC_INVERSION_EXIT_THRESHOLD,
                    "entry_momentum_min": DEFAULT_DYNAMIC_INVERSION_MIN_MOMENTUM,
                    "entry_score_diff_min": DEFAULT_DYNAMIC_INVERSION_MIN_SCORE_DIFF,
                    "signal_strength": signal_strength,
                },
            )
        previous_price = float(price)
    return None


def _select_inversion_exit(group: pd.DataFrame, selection: TradeSelection) -> int | None:
    exit_threshold = float(selection.metadata["exit_threshold"])
    future = group.iloc[selection.entry_index + 1 :]
    exit_candidates = future[future["team_price"] < exit_threshold]
    if exit_candidates.empty:
        return int(len(group) - 1)
    return int(exit_candidates.index[0])


def simulate_inversion_trades(state_df: pd.DataFrame, *, slippage_cents: int) -> list[dict[str, Any]]:
    return simulate_trade_loop(
        state_df,
        strategy_family="inversion",
        entry_rule="dynamic_cross_above_45c_or_50c_with_momentum",
        exit_rule="break_back_below_49c_or_end",
        slippage_cents=slippage_cents,
        entry_selector=_select_inversion_entry,
        exit_selector=_select_inversion_exit,
    )


__all__ = ["simulate_inversion_trades"]
