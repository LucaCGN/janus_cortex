from __future__ import annotations

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import simulate_trade_loop
from app.data.pipelines.daily.nba.analysis.backtests.specs import TradeSelection


_OPEN_MIN = 0.35
_OPEN_MAX = 0.72
_MIN_PRICE = 0.50
_ENTRY_PRICE_MAX = 0.70
_ENTRY_MOVE = 0.03
_MIN_MOMENTUM = 2.0
_MIN_SCORE_DIFF = 0.0
_TARGET_MOVE = 0.045
_STOP_LOSS = 0.03
_MAX_HOLD_STATES = 8
_MAX_PERIOD_ELAPSED_SECONDS = 360.0
_ACTIVE_PERIODS = {"Q1"}


def _select_micro_momentum_continuation_entry(group: pd.DataFrame) -> TradeSelection | None:
    opening_price = float(group.iloc[0]["opening_price"]) if pd.notna(group.iloc[0]["opening_price"]) else None
    if opening_price is None or opening_price < _OPEN_MIN or opening_price > _OPEN_MAX:
        return None

    period_starts = {
        str(period_label): float(chunk.iloc[0]["clock_elapsed_seconds"])
        for period_label, chunk in group.groupby(group["period_label"].astype(str), sort=False)
        if not chunk.empty and pd.notna(chunk.iloc[0]["clock_elapsed_seconds"])
    }
    threshold = max(_MIN_PRICE, opening_price + _ENTRY_MOVE)
    previous_price = opening_price
    prices = pd.to_numeric(group["team_price"], errors="coerce").tolist()
    for index, price in enumerate(prices):
        if price is None or pd.isna(price):
            continue
        resolved_price = float(price)
        row = group.iloc[index]
        period_label = str(row["period_label"])
        if period_label not in _ACTIVE_PERIODS:
            previous_price = resolved_price
            continue
        period_start = period_starts.get(period_label)
        if period_start is None:
            previous_price = resolved_price
            continue
        period_elapsed_seconds = float(row["clock_elapsed_seconds"]) - period_start
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
            stop_price = max(0.05, resolved_price - _STOP_LOSS)
            signal_strength = (
                ((resolved_price - threshold) * 100.0)
                + max(0.0, float(row["net_points_last_5_events"]))
                + max(0.0, float(row["score_diff"]))
            )
            return TradeSelection(
                entry_index=index,
                metadata={
                    "target_price": target_price,
                    "stop_price": stop_price,
                    "max_exit_state_index": min(int(len(group) - 1), index + _MAX_HOLD_STATES),
                    "signal_strength": signal_strength,
                },
            )
        previous_price = resolved_price
    return None


def _select_micro_momentum_continuation_exit(group: pd.DataFrame, selection: TradeSelection) -> int | None:
    target_price = float(selection.metadata["target_price"])
    stop_price = float(selection.metadata["stop_price"])
    max_exit_state_index = int(selection.metadata["max_exit_state_index"])
    future = group.iloc[selection.entry_index + 1 :]
    if future.empty:
        return int(len(group) - 1)
    for index, row in future.iterrows():
        if int(row["state_index"]) > max_exit_state_index:
            return max_exit_state_index
        price = row["team_price"]
        if pd.isna(price):
            continue
        resolved_price = float(price)
        if resolved_price >= target_price or resolved_price <= stop_price:
            return int(index)
    return max_exit_state_index


def simulate_micro_momentum_continuation_trades(state_df: pd.DataFrame, *, slippage_cents: int) -> list[dict[str, object]]:
    return simulate_trade_loop(
        state_df,
        strategy_family="micro_momentum_continuation",
        entry_rule="q1_cross_plus_3c_with_fast_momentum_below_70c",
        exit_rule="plus_4p5c_or_minus_3c_or_within_8_states",
        slippage_cents=slippage_cents,
        entry_selector=_select_micro_momentum_continuation_entry,
        exit_selector=_select_micro_momentum_continuation_exit,
    )


__all__ = ["simulate_micro_momentum_continuation_trades"]
