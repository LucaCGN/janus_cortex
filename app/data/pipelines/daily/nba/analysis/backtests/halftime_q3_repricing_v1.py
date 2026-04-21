from __future__ import annotations

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import simulate_trade_loop
from app.data.pipelines.daily.nba.analysis.backtests.specs import TradeSelection
from app.data.pipelines.daily.nba.analysis.contracts import (
    DEFAULT_HALFTIME_Q3_REPRICING_ENTRY_MOVE,
    DEFAULT_HALFTIME_Q3_REPRICING_MAX_SECONDS_LEFT,
    DEFAULT_HALFTIME_Q3_REPRICING_MIN_MOMENTUM,
    DEFAULT_HALFTIME_Q3_REPRICING_MIN_PRICE,
    DEFAULT_HALFTIME_Q3_REPRICING_MIN_SCORE_DIFF,
    DEFAULT_HALFTIME_Q3_REPRICING_MIN_SECONDS_LEFT,
    DEFAULT_HALFTIME_Q3_REPRICING_OPEN_MAX,
    DEFAULT_HALFTIME_Q3_REPRICING_OPEN_MIN,
    DEFAULT_HALFTIME_Q3_REPRICING_STOP_LOSS,
    DEFAULT_HALFTIME_Q3_REPRICING_TARGET_MOVE,
)


def _select_halftime_q3_repricing_v1_entry(group: pd.DataFrame) -> TradeSelection | None:
    opening_price = float(group.iloc[0]["opening_price"]) if pd.notna(group.iloc[0]["opening_price"]) else None
    if opening_price is None or opening_price < DEFAULT_HALFTIME_Q3_REPRICING_OPEN_MIN or opening_price > DEFAULT_HALFTIME_Q3_REPRICING_OPEN_MAX:
        return None

    threshold = max(DEFAULT_HALFTIME_Q3_REPRICING_MIN_PRICE, opening_price + DEFAULT_HALFTIME_Q3_REPRICING_ENTRY_MOVE)
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
        row = group.iloc[index]
        if str(row["period_label"]) != "Q3":
            previous_price = resolved_price
            continue
        seconds_left = float(row["seconds_to_game_end"])
        if seconds_left < DEFAULT_HALFTIME_Q3_REPRICING_MIN_SECONDS_LEFT or seconds_left > DEFAULT_HALFTIME_Q3_REPRICING_MAX_SECONDS_LEFT:
            previous_price = resolved_price
            continue
        if float(previous_price) < threshold <= resolved_price:
            if float(row["net_points_last_5_events"]) < DEFAULT_HALFTIME_Q3_REPRICING_MIN_MOMENTUM:
                previous_price = resolved_price
                continue
            if float(row["score_diff"]) < DEFAULT_HALFTIME_Q3_REPRICING_MIN_SCORE_DIFF:
                previous_price = resolved_price
                continue
            target_price = min(0.999999, resolved_price + DEFAULT_HALFTIME_Q3_REPRICING_TARGET_MOVE)
            stop_price = max(0.05, max(opening_price + 0.01, resolved_price - DEFAULT_HALFTIME_Q3_REPRICING_STOP_LOSS))
            signal_strength = (
                ((resolved_price - threshold) * 100.0)
                + max(0.0, float(row["net_points_last_5_events"]))
                + max(0.0, float(row["score_diff"])) * 0.5
            )
            return TradeSelection(
                entry_index=index,
                metadata={
                    "target_price": target_price,
                    "stop_price": stop_price,
                    "signal_strength": signal_strength,
                },
            )
        previous_price = resolved_price
    return None


def _select_halftime_q3_repricing_v1_exit(group: pd.DataFrame, selection: TradeSelection) -> int | None:
    target_price = float(selection.metadata["target_price"])
    stop_price = float(selection.metadata["stop_price"])
    future = group.iloc[selection.entry_index + 1 :]
    if future.empty:
        return int(len(group) - 1)

    q3_indexes = group[group["period_label"].astype(str) == "Q3"].index
    last_q3_index = int(q3_indexes.max()) if len(q3_indexes) else int(len(group) - 1)
    for index, row in future.iterrows():
        if str(row["period_label"]) != "Q3":
            return max(selection.entry_index + 1, int(index) - 1)
        price = row["team_price"]
        if pd.isna(price):
            continue
        resolved_price = float(price)
        if resolved_price >= target_price or resolved_price <= stop_price:
            return int(index)
    return last_q3_index


def simulate_halftime_q3_repricing_v1_trades(state_df: pd.DataFrame, *, slippage_cents: int) -> list[dict[str, object]]:
    return simulate_trade_loop(
        state_df,
        strategy_family="halftime_q3_repricing_v1",
        entry_rule="early_q3_cross_plus_5c_with_momentum",
        exit_rule="plus_7c_or_minus_4c_or_end_of_q3",
        slippage_cents=slippage_cents,
        entry_selector=_select_halftime_q3_repricing_v1_entry,
        exit_selector=_select_halftime_q3_repricing_v1_exit,
    )


__all__ = ["simulate_halftime_q3_repricing_v1_trades"]
