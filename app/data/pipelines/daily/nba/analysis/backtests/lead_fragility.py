from __future__ import annotations

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import simulate_trade_loop
from app.data.pipelines.daily.nba.analysis.backtests.specs import TradeSelection


_OPEN_CAP = 0.45
_ENTRY_PRICE_MIN = 0.38
_ENTRY_PRICE_MAX = 0.48
_MIN_MOMENTUM = 2.0
_MIN_SCORE_DIFF = -6.0
_MAX_SCORE_DIFF = 2.0
_MIN_LEAD_CHANGES = 1
_TARGET_PRICE = 0.52
_TARGET_MOVE = 0.06
_STOP_LOSS = 0.04
_MAX_PERIOD_ELAPSED_SECONDS = 180.0
_ACTIVE_PERIODS = {"Q3"}


def _select_lead_fragility_entry(group: pd.DataFrame) -> TradeSelection | None:
    opening_price = float(group.iloc[0]["opening_price"]) if pd.notna(group.iloc[0]["opening_price"]) else None
    if opening_price is None or opening_price > _OPEN_CAP:
        return None

    period_starts = {
        str(period_label): float(chunk.iloc[0]["clock_elapsed_seconds"])
        for period_label, chunk in group.groupby(group["period_label"].astype(str), sort=False)
        if not chunk.empty and pd.notna(chunk.iloc[0]["clock_elapsed_seconds"])
    }
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
        score_diff = float(row["score_diff"])
        if score_diff < _MIN_SCORE_DIFF or score_diff > _MAX_SCORE_DIFF:
            previous_price = resolved_price
            continue
        if resolved_price < _ENTRY_PRICE_MIN or resolved_price > _ENTRY_PRICE_MAX:
            previous_price = resolved_price
            continue
        if float(row["net_points_last_5_events"]) < _MIN_MOMENTUM:
            previous_price = resolved_price
            continue
        if int(row["lead_changes_so_far"]) < _MIN_LEAD_CHANGES:
            previous_price = resolved_price
            continue
        if resolved_price <= float(previous_price):
            previous_price = resolved_price
            continue
        target_price = min(_TARGET_PRICE, resolved_price + _TARGET_MOVE)
        stop_price = max(0.05, resolved_price - _STOP_LOSS)
        signal_strength = (
            ((_ENTRY_PRICE_MAX - resolved_price) * 100.0)
            + max(0.0, float(row["net_points_last_5_events"]))
            + max(0.0, abs(score_diff))
        )
        return TradeSelection(
            entry_index=index,
            metadata={
                "target_price": target_price,
                "stop_price": stop_price,
                "signal_strength": signal_strength,
            },
        )
    return None


def _select_lead_fragility_exit(group: pd.DataFrame, selection: TradeSelection) -> int | None:
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


def simulate_lead_fragility_trades(state_df: pd.DataFrame, *, slippage_cents: int) -> list[dict[str, object]]:
    return simulate_trade_loop(
        state_df,
        strategy_family="lead_fragility",
        entry_rule="fragile_q3_rebound_entry_38c_to_48c",
        exit_rule="plus_6c_or_minus_4c_or_end",
        slippage_cents=slippage_cents,
        entry_selector=_select_lead_fragility_entry,
        exit_selector=_select_lead_fragility_exit,
    )


__all__ = ["simulate_lead_fragility_trades"]
