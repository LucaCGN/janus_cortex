from __future__ import annotations

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import simulate_trade_loop
from app.data.pipelines.daily.nba.analysis.backtests.specs import TradeSelection


_OPEN_MIN = 0.25
_OPEN_MAX = 0.74
_MIN_PRICE = 0.50
_ENTRY_PRICE_MAX = 0.72
_ENTRY_MOVE = 0.03
_MIN_MOMENTUM = 2.0
_MIN_SCORE_DIFF = 1.0
_TARGET_MOVE = 0.06
_STOP_LOSS = 0.04
_MAX_PERIOD_ELAPSED_SECONDS = 360.0


def _select_quarter_open_reprice_entry(group: pd.DataFrame) -> TradeSelection | None:
    opening_price = float(group.iloc[0]["opening_price"]) if pd.notna(group.iloc[0]["opening_price"]) else None
    if opening_price is None or opening_price < _OPEN_MIN or opening_price > _OPEN_MAX:
        return None

    q1_rows = group[group["period_label"].astype(str) == "Q1"]
    if q1_rows.empty:
        return None
    q1_start_clock = float(q1_rows.iloc[0]["clock_elapsed_seconds"])
    threshold = max(_MIN_PRICE, opening_price + _ENTRY_MOVE)
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
        period_elapsed_seconds = float(row["clock_elapsed_seconds"]) - q1_start_clock
        if period_elapsed_seconds > _MAX_PERIOD_ELAPSED_SECONDS:
            previous_price = resolved_price
            continue
        if resolved_price > _ENTRY_PRICE_MAX:
            previous_price = resolved_price
            continue
        crossed_threshold = float(previous_price) < threshold <= resolved_price
        if crossed_threshold:
            if float(row["net_points_last_5_events"]) < _MIN_MOMENTUM:
                previous_price = resolved_price
                continue
            if float(row["score_diff"]) < _MIN_SCORE_DIFF:
                previous_price = resolved_price
                continue
            target_price = min(0.999999, resolved_price + _TARGET_MOVE)
            stop_price = max(0.05, max(opening_price + 0.01, resolved_price - _STOP_LOSS))
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


def _select_quarter_open_reprice_exit(group: pd.DataFrame, selection: TradeSelection) -> int | None:
    target_price = float(selection.metadata["target_price"])
    stop_price = float(selection.metadata["stop_price"])
    future = group.iloc[selection.entry_index + 1 :]
    if future.empty:
        return int(len(group) - 1)

    q1_indexes = group[group["period_label"].astype(str) == "Q1"].index
    last_q1_index = int(q1_indexes.max()) if len(q1_indexes) else int(len(group) - 1)
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


def simulate_quarter_open_reprice_trades(state_df: pd.DataFrame, *, slippage_cents: int) -> list[dict[str, object]]:
    return simulate_trade_loop(
        state_df,
        strategy_family="quarter_open_reprice",
        entry_rule="q1_cross_plus_3c_with_early_support_below_72c",
        exit_rule="plus_6c_or_minus_4c_or_end_of_q1",
        slippage_cents=slippage_cents,
        entry_selector=_select_quarter_open_reprice_entry,
        exit_selector=_select_quarter_open_reprice_exit,
    )


__all__ = ["simulate_quarter_open_reprice_trades"]
