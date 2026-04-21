from __future__ import annotations

from typing import Any

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import simulate_trade_loop
from app.data.pipelines.daily.nba.analysis.backtests.specs import TradeSelection
from app.data.pipelines.daily.nba.analysis.contracts import (
    DEFAULT_UNDERDOG_LIFTOFF_ENTRY_THRESHOLD,
    DEFAULT_UNDERDOG_LIFTOFF_MIN_MOMENTUM,
    DEFAULT_UNDERDOG_LIFTOFF_MIN_SCORE_DIFF,
    DEFAULT_UNDERDOG_LIFTOFF_MIN_SECONDS_LEFT,
    DEFAULT_UNDERDOG_LIFTOFF_OPEN_CAP,
    DEFAULT_UNDERDOG_LIFTOFF_STOP_LOSS,
    DEFAULT_UNDERDOG_LIFTOFF_TARGET_PRICE,
)


_ACTIVE_PERIODS = {"Q2", "Q3", "Q4", "OT1", "OT2"}


def _select_underdog_liftoff_entry(group: pd.DataFrame) -> TradeSelection | None:
    opening_price = float(group.iloc[0]["opening_price"]) if pd.notna(group.iloc[0]["opening_price"]) else None
    if opening_price is None or opening_price >= DEFAULT_UNDERDOG_LIFTOFF_OPEN_CAP:
        return None
    previous_price = opening_price
    prices = pd.to_numeric(group["team_price"], errors="coerce").tolist()
    for index, price in enumerate(prices):
        if price is None or pd.isna(price):
            previous_price = price
            continue
        resolved_price = float(price)
        if index == 0:
            previous_price = resolved_price
            continue
        if previous_price < DEFAULT_UNDERDOG_LIFTOFF_ENTRY_THRESHOLD <= resolved_price:
            row = group.iloc[index]
            if str(row["period_label"]) not in _ACTIVE_PERIODS:
                previous_price = resolved_price
                continue
            if float(row["net_points_last_5_events"]) < DEFAULT_UNDERDOG_LIFTOFF_MIN_MOMENTUM:
                previous_price = resolved_price
                continue
            if float(row["score_diff"]) < DEFAULT_UNDERDOG_LIFTOFF_MIN_SCORE_DIFF:
                previous_price = resolved_price
                continue
            if float(row["seconds_to_game_end"]) < DEFAULT_UNDERDOG_LIFTOFF_MIN_SECONDS_LEFT:
                previous_price = resolved_price
                continue
            return TradeSelection(
                entry_index=index,
                metadata={
                    "entry_threshold": DEFAULT_UNDERDOG_LIFTOFF_ENTRY_THRESHOLD,
                    "target_price": DEFAULT_UNDERDOG_LIFTOFF_TARGET_PRICE,
                    "stop_price": max(0.01, resolved_price - DEFAULT_UNDERDOG_LIFTOFF_STOP_LOSS),
                    "entry_momentum_min": DEFAULT_UNDERDOG_LIFTOFF_MIN_MOMENTUM,
                    "entry_score_diff_min": DEFAULT_UNDERDOG_LIFTOFF_MIN_SCORE_DIFF,
                    "min_seconds_left": DEFAULT_UNDERDOG_LIFTOFF_MIN_SECONDS_LEFT,
                },
            )
        previous_price = resolved_price
    return None


def _select_underdog_liftoff_exit(group: pd.DataFrame, selection: TradeSelection) -> int | None:
    target_price = float(selection.metadata["target_price"])
    stop_price = float(selection.metadata["stop_price"])
    future = group.iloc[selection.entry_index + 1 :]
    for index, row in future.iterrows():
        price = row["team_price"]
        if pd.isna(price):
            continue
        resolved_price = float(price)
        if resolved_price >= target_price or resolved_price <= stop_price:
            return int(index)
    return int(len(group) - 1)


def simulate_underdog_liftoff_trades(state_df: pd.DataFrame, *, slippage_cents: int) -> list[dict[str, Any]]:
    return simulate_trade_loop(
        state_df,
        strategy_family="underdog_liftoff",
        entry_rule="cross_above_36c_with_momentum",
        exit_rule="hit_50c_or_minus_3c_or_end",
        slippage_cents=slippage_cents,
        entry_selector=_select_underdog_liftoff_entry,
        exit_selector=_select_underdog_liftoff_exit,
    )


__all__ = ["simulate_underdog_liftoff_trades"]
