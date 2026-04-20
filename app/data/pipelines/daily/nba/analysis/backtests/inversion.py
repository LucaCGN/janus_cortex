from __future__ import annotations

from typing import Any

import pandas as pd

from app.data.pipelines.daily.nba.analysis.contracts import (
    DEFAULT_INVERSION_ENTRY_THRESHOLD,
    DEFAULT_INVERSION_EXIT_THRESHOLD,
)
from app.data.pipelines.daily.nba.analysis.backtests.engine import simulate_trade_loop
from app.data.pipelines.daily.nba.analysis.backtests.specs import TradeSelection


def _select_inversion_entry(group: pd.DataFrame) -> TradeSelection | None:
    opening_price = float(group.iloc[0]["opening_price"]) if pd.notna(group.iloc[0]["opening_price"]) else None
    if opening_price is None or opening_price >= 0.5:
        return None
    previous_price = opening_price
    prices = pd.to_numeric(group["team_price"], errors="coerce").tolist()
    for index, price in enumerate(prices):
        if price is None or pd.isna(price):
            previous_price = price
            continue
        if index == 0:
            previous_price = float(price)
            continue
        if previous_price < DEFAULT_INVERSION_ENTRY_THRESHOLD and float(price) >= DEFAULT_INVERSION_ENTRY_THRESHOLD:
            return TradeSelection(
                entry_index=index,
                metadata={
                    "entry_threshold": DEFAULT_INVERSION_ENTRY_THRESHOLD,
                    "exit_threshold": DEFAULT_INVERSION_EXIT_THRESHOLD,
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
        entry_rule="first_cross_above_50c",
        exit_rule="break_back_below_50c_or_end",
        slippage_cents=slippage_cents,
        entry_selector=_select_inversion_entry,
        exit_selector=_select_inversion_exit,
    )


__all__ = ["simulate_inversion_trades"]
