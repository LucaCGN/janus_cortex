from __future__ import annotations

from typing import Any

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import (
    DEFAULT_REVERSION_DRAWDOWN,
    DEFAULT_REVERSION_EXIT_BUFFER,
    DEFAULT_REVERSION_OPEN_THRESHOLD,
    TradeSelection,
    simulate_trade_loop,
)


def _select_reversion_entry(group: pd.DataFrame) -> TradeSelection | None:
    opening_price = float(group.iloc[0]["opening_price"]) if pd.notna(group.iloc[0]["opening_price"]) else None
    if opening_price is None or opening_price < DEFAULT_REVERSION_OPEN_THRESHOLD:
        return None
    prices = pd.to_numeric(group["team_price"], errors="coerce")
    trigger = prices <= opening_price - DEFAULT_REVERSION_DRAWDOWN
    if not bool(trigger.any()):
        return None
    entry_index = int(trigger[trigger].index[0])
    return TradeSelection(
        entry_index=entry_index,
        metadata={
            "opening_price": opening_price,
            "target_exit_price": opening_price - DEFAULT_REVERSION_EXIT_BUFFER,
        },
    )


def _select_reversion_exit(group: pd.DataFrame, selection: TradeSelection) -> int | None:
    target_exit_price = float(selection.metadata["target_exit_price"])
    future = group.iloc[selection.entry_index + 1 :]
    exit_candidates = future[future["team_price"] >= target_exit_price]
    if exit_candidates.empty:
        return int(len(group) - 1)
    return int(exit_candidates.index[0])


def simulate_reversion_trades(state_df: pd.DataFrame, *, slippage_cents: int) -> list[dict[str, Any]]:
    return simulate_trade_loop(
        state_df,
        strategy_family="reversion",
        entry_rule="favorite_drawdown_buy_10c",
        exit_rule="reclaim_open_minus_2c_or_end",
        slippage_cents=slippage_cents,
        entry_selector=_select_reversion_entry,
        exit_selector=_select_reversion_exit,
    )


__all__ = ["simulate_reversion_trades"]
