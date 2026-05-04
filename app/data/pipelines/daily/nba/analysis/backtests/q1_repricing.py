from __future__ import annotations

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import simulate_trade_loop
from app.data.pipelines.daily.nba.analysis.backtests.specs import TradeSelection
from app.data.pipelines.daily.nba.analysis.contracts import (
    DEFAULT_Q1_REPRICING_ENTRY_MOVE,
    DEFAULT_Q1_REPRICING_MAX_CLOCK_ELAPSED,
    DEFAULT_Q1_REPRICING_MIN_MOMENTUM,
    DEFAULT_Q1_REPRICING_MIN_PRICE,
    DEFAULT_Q1_REPRICING_MIN_SCORE_DIFF,
    DEFAULT_Q1_REPRICING_OPEN_MAX,
    DEFAULT_Q1_REPRICING_OPEN_MIN,
    DEFAULT_Q1_REPRICING_STOP_LOSS,
    DEFAULT_Q1_REPRICING_TARGET_MOVE,
)


def _select_q1_repricing_entry(group: pd.DataFrame) -> TradeSelection | None:
    opening_price = float(group.iloc[0]["opening_price"]) if pd.notna(group.iloc[0]["opening_price"]) else None
    if opening_price is None or opening_price < DEFAULT_Q1_REPRICING_OPEN_MIN or opening_price > DEFAULT_Q1_REPRICING_OPEN_MAX:
        return None

    threshold = max(DEFAULT_Q1_REPRICING_MIN_PRICE, opening_price + DEFAULT_Q1_REPRICING_ENTRY_MOVE)
    previous_price = opening_price
    prices = pd.to_numeric(group["team_price"], errors="coerce").tolist()
    for index, price in enumerate(prices):
        if price is None or pd.isna(price):
            continue
        resolved_price = float(price)
        row = group.iloc[index]
        if str(row["period_label"]) != "Q1":
            previous_price = resolved_price
            continue
        if float(row["clock_elapsed_seconds"]) > DEFAULT_Q1_REPRICING_MAX_CLOCK_ELAPSED:
            previous_price = resolved_price
            continue
        crossed_threshold = previous_price < threshold <= resolved_price
        if crossed_threshold:
            if float(row["net_points_last_5_events"]) < DEFAULT_Q1_REPRICING_MIN_MOMENTUM:
                previous_price = resolved_price
                continue
            if float(row["score_diff"]) < DEFAULT_Q1_REPRICING_MIN_SCORE_DIFF:
                previous_price = resolved_price
                continue
            target_price = min(0.999999, resolved_price + DEFAULT_Q1_REPRICING_TARGET_MOVE)
            stop_price = max(0.05, max(opening_price + 0.01, resolved_price - DEFAULT_Q1_REPRICING_STOP_LOSS))
            signal_strength = (
                ((resolved_price - threshold) * 100.0)
                + max(0.0, float(row["net_points_last_5_events"]))
                + max(0.0, float(row["score_diff"])) * 0.5
            )
            return TradeSelection(
                entry_index=index,
                metadata={
                    "entry_threshold": threshold,
                    "target_price": target_price,
                    "stop_price": stop_price,
                    "entry_move_from_open": resolved_price - opening_price,
                    "signal_strength": signal_strength,
                },
            )
        previous_price = resolved_price
    return None


def _select_q1_repricing_exit(group: pd.DataFrame, selection: TradeSelection) -> int | None:
    target_price = float(selection.metadata["target_price"])
    stop_price = float(selection.metadata["stop_price"])
    future = group.iloc[selection.entry_index + 1 :]
    if future.empty:
        return int(len(group) - 1)

    last_q1_index = int(group[group["period_label"].astype(str) == "Q1"].index.max())
    for index, row in future.iterrows():
        if str(row["period_label"]) != "Q1":
            return max(selection.entry_index + 1, int(index) - 1)
        price = row["team_price"]
        if pd.isna(price):
            continue
        resolved_price = float(price)
        if resolved_price >= target_price or resolved_price <= stop_price:
            return int(index)
    return max(selection.entry_index + 1, last_q1_index)


def simulate_q1_repricing_trades(state_df: pd.DataFrame, *, slippage_cents: int) -> list[dict[str, object]]:
    return simulate_trade_loop(
        state_df,
        strategy_family="q1_repricing",
        entry_rule="q1_cross_plus_7c_with_momentum",
        exit_rule="plus_8c_or_minus_5c_or_end_of_q1",
        slippage_cents=slippage_cents,
        entry_selector=_select_q1_repricing_entry,
        exit_selector=_select_q1_repricing_exit,
    )


__all__ = ["simulate_q1_repricing_trades"]
