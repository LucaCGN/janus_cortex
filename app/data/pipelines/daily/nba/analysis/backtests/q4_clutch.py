from __future__ import annotations

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import simulate_trade_loop
from app.data.pipelines.daily.nba.analysis.backtests.specs import TradeSelection
from app.data.pipelines.daily.nba.analysis.contracts import (
    DEFAULT_Q4_CLUTCH_ENTRY_THRESHOLD,
    DEFAULT_Q4_CLUTCH_MAX_SCORE_MARGIN,
    DEFAULT_Q4_CLUTCH_MAX_SECONDS_LEFT,
    DEFAULT_Q4_CLUTCH_MIN_LEAD_CHANGES,
    DEFAULT_Q4_CLUTCH_MIN_MOMENTUM,
    DEFAULT_Q4_CLUTCH_MIN_SCORE_DIFF,
    DEFAULT_Q4_CLUTCH_OPEN_MAX,
    DEFAULT_Q4_CLUTCH_OPEN_MIN,
    DEFAULT_Q4_CLUTCH_STOP_LOSS,
    DEFAULT_Q4_CLUTCH_TARGET_PRICE,
)


def _select_q4_clutch_entry(group: pd.DataFrame) -> TradeSelection | None:
    opening_price = float(group.iloc[0]["opening_price"]) if pd.notna(group.iloc[0]["opening_price"]) else None
    if opening_price is None or opening_price < DEFAULT_Q4_CLUTCH_OPEN_MIN or opening_price > DEFAULT_Q4_CLUTCH_OPEN_MAX:
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
        row = group.iloc[index]
        if str(row["period_label"]) != "Q4":
            previous_price = resolved_price
            continue
        if float(row["seconds_to_game_end"]) > DEFAULT_Q4_CLUTCH_MAX_SECONDS_LEFT:
            previous_price = resolved_price
            continue
        if abs(float(row["score_diff"])) > DEFAULT_Q4_CLUTCH_MAX_SCORE_MARGIN:
            previous_price = resolved_price
            continue
        if float(row["score_diff"]) < DEFAULT_Q4_CLUTCH_MIN_SCORE_DIFF:
            previous_price = resolved_price
            continue
        if float(row["net_points_last_5_events"]) < DEFAULT_Q4_CLUTCH_MIN_MOMENTUM:
            previous_price = resolved_price
            continue
        if int(row["lead_changes_so_far"]) < DEFAULT_Q4_CLUTCH_MIN_LEAD_CHANGES and not bool(row["tied_flag"]):
            previous_price = resolved_price
            continue
        if previous_price < DEFAULT_Q4_CLUTCH_ENTRY_THRESHOLD <= resolved_price:
            target_price = min(0.999999, max(DEFAULT_Q4_CLUTCH_TARGET_PRICE, resolved_price + 0.08))
            stop_price = max(0.05, max(DEFAULT_Q4_CLUTCH_ENTRY_THRESHOLD - 0.03, resolved_price - DEFAULT_Q4_CLUTCH_STOP_LOSS))
            signal_strength = (
                ((resolved_price - DEFAULT_Q4_CLUTCH_ENTRY_THRESHOLD) * 100.0)
                + max(0.0, float(row["net_points_last_5_events"]))
                + max(0.0, float(row["lead_changes_so_far"]))
                + max(0.0, float(row["score_diff"])) * 0.5
            )
            return TradeSelection(
                entry_index=index,
                metadata={
                    "entry_threshold": DEFAULT_Q4_CLUTCH_ENTRY_THRESHOLD,
                    "target_price": target_price,
                    "stop_price": stop_price,
                    "signal_strength": signal_strength,
                },
            )
        previous_price = resolved_price
    return None


def _select_q4_clutch_exit(group: pd.DataFrame, selection: TradeSelection) -> int | None:
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
        if resolved_price >= target_price or resolved_price <= stop_price:
            return int(index)
    return int(len(group) - 1)


def simulate_q4_clutch_trades(state_df: pd.DataFrame, *, slippage_cents: int) -> list[dict[str, object]]:
    return simulate_trade_loop(
        state_df,
        strategy_family="q4_clutch",
        entry_rule="late_q4_cross_above_55c_in_close_game",
        exit_rule="plus_8c_or_break_back_or_end",
        slippage_cents=slippage_cents,
        entry_selector=_select_q4_clutch_entry,
        exit_selector=_select_q4_clutch_exit,
    )


__all__ = ["simulate_q4_clutch_trades"]
