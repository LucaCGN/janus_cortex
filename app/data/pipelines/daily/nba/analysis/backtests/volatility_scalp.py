from __future__ import annotations

from typing import Any

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import simulate_trade_loop
from app.data.pipelines.daily.nba.analysis.backtests.specs import TradeSelection


def _select_volatility_scalp_entry(group: pd.DataFrame) -> TradeSelection | None:
    opening_price = float(group.iloc[0]["opening_price"]) if pd.notna(group.iloc[0]["opening_price"]) else None
    if opening_price is None or opening_price < 0.45 or opening_price > 0.65:
        return None

    prices = pd.to_numeric(group["team_price"], errors="coerce")
    period_labels = group["period_label"].fillna("").astype(str)
    trigger = (period_labels == "Q1") & (prices <= opening_price - 0.12)
    if not bool(trigger.any()):
        return None

    entry_index = int(trigger[trigger].index[0])
    entry_price = float(prices.iloc[entry_index])
    return TradeSelection(
        entry_index=entry_index,
        metadata={
            "target_exit_price": min(0.999999, max(entry_price + 0.08, opening_price - 0.04)),
            "stop_price": max(0.05, entry_price - 0.05),
        },
    )


def _select_volatility_scalp_exit(group: pd.DataFrame, selection: TradeSelection) -> int | None:
    target_exit_price = float(selection.metadata["target_exit_price"])
    stop_price = float(selection.metadata["stop_price"])
    future = group.iloc[selection.entry_index + 1 :]
    if future.empty:
        return int(len(group) - 1)

    for index, row in future.iterrows():
        price = float(row["team_price"])
        if price >= target_exit_price or price <= stop_price:
            return int(index)
    return int(len(group) - 1)


def simulate_volatility_scalp_trades(state_df: pd.DataFrame, *, slippage_cents: int) -> list[dict[str, Any]]:
    return simulate_trade_loop(
        state_df,
        strategy_family="volatility_scalp",
        entry_rule="q1_midband_drawdown_scalp",
        exit_rule="partial_reclaim_or_minus_5c_or_end",
        slippage_cents=slippage_cents,
        entry_selector=_select_volatility_scalp_entry,
        exit_selector=_select_volatility_scalp_exit,
    )


__all__ = ["simulate_volatility_scalp_trades"]
