from __future__ import annotations

from typing import Any

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import simulate_trade_loop
from app.data.pipelines.daily.nba.analysis.backtests.specs import TradeSelection


def _select_comeback_entry(group: pd.DataFrame) -> TradeSelection | None:
    opening_price = float(group.iloc[0]["opening_price"]) if pd.notna(group.iloc[0]["opening_price"]) else None
    if opening_price is None or opening_price > 0.45:
        return None

    prices = pd.to_numeric(group["team_price"], errors="coerce")
    score_diff = pd.to_numeric(group["score_diff"], errors="coerce")
    net_recent = pd.to_numeric(group["net_points_last_5_events"], errors="coerce")
    period_labels = group["period_label"].fillna("").astype(str)
    trigger = (
        period_labels.isin(["Q2", "Q3"])
        & (score_diff <= -5)
        & (net_recent > 0)
        & prices.between(0.15, 0.40, inclusive="both")
    )
    if not bool(trigger.any()):
        return None

    entry_index = int(trigger[trigger].index[0])
    entry_price = float(prices.iloc[entry_index])
    entry_row = group.iloc[entry_index]
    signal_strength = (
        ((0.40 - entry_price) * 100.0)
        + max(0.0, float(entry_row["net_points_last_5_events"]))
        + max(0.0, -float(entry_row["score_diff"]) - 4.0)
    )
    return TradeSelection(
        entry_index=entry_index,
        metadata={
            "target_exit_price": min(0.5, entry_price + 0.08),
            "stop_price": max(0.05, entry_price - 0.06),
            "signal_strength": signal_strength,
        },
    )


def _select_comeback_exit(group: pd.DataFrame, selection: TradeSelection) -> int | None:
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


def simulate_comeback_reversion_trades(state_df: pd.DataFrame, *, slippage_cents: int) -> list[dict[str, Any]]:
    return simulate_trade_loop(
        state_df,
        strategy_family="comeback_reversion",
        entry_rule="q2_q3_underdog_trail_buy_rebound",
        exit_rule="plus_8c_or_minus_6c_or_end",
        slippage_cents=slippage_cents,
        entry_selector=_select_comeback_entry,
        exit_selector=_select_comeback_exit,
    )


__all__ = ["simulate_comeback_reversion_trades"]
