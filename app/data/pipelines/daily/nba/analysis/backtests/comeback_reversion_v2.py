from __future__ import annotations

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import simulate_trade_loop
from app.data.pipelines.daily.nba.analysis.backtests.specs import TradeSelection
from app.data.pipelines.daily.nba.analysis.contracts import (
    DEFAULT_COMEBACK_REVERSION_V2_ENTRY_PRICE_MAX,
    DEFAULT_COMEBACK_REVERSION_V2_ENTRY_PRICE_MIN,
    DEFAULT_COMEBACK_REVERSION_V2_MAX_SCORE_DIFF,
    DEFAULT_COMEBACK_REVERSION_V2_MIN_MOMENTUM,
    DEFAULT_COMEBACK_REVERSION_V2_MIN_SCORE_DIFF,
    DEFAULT_COMEBACK_REVERSION_V2_OPEN_CAP,
    DEFAULT_COMEBACK_REVERSION_V2_STOP_LOSS,
    DEFAULT_COMEBACK_REVERSION_V2_TARGET_MOVE,
    DEFAULT_COMEBACK_REVERSION_V2_TARGET_PRICE,
)


def _select_comeback_reversion_v2_entry(group: pd.DataFrame) -> TradeSelection | None:
    opening_price = float(group.iloc[0]["opening_price"]) if pd.notna(group.iloc[0]["opening_price"]) else None
    if opening_price is None or opening_price > DEFAULT_COMEBACK_REVERSION_V2_OPEN_CAP:
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
        if str(row["period_label"]) != "Q3":
            previous_price = resolved_price
            continue
        score_diff = float(row["score_diff"])
        if score_diff < DEFAULT_COMEBACK_REVERSION_V2_MIN_SCORE_DIFF or score_diff > DEFAULT_COMEBACK_REVERSION_V2_MAX_SCORE_DIFF:
            previous_price = resolved_price
            continue
        if resolved_price < DEFAULT_COMEBACK_REVERSION_V2_ENTRY_PRICE_MIN or resolved_price > DEFAULT_COMEBACK_REVERSION_V2_ENTRY_PRICE_MAX:
            previous_price = resolved_price
            continue
        if float(row["net_points_last_5_events"]) < DEFAULT_COMEBACK_REVERSION_V2_MIN_MOMENTUM:
            previous_price = resolved_price
            continue
        if resolved_price <= float(previous_price):
            previous_price = resolved_price
            continue
        target_price = min(DEFAULT_COMEBACK_REVERSION_V2_TARGET_PRICE, resolved_price + DEFAULT_COMEBACK_REVERSION_V2_TARGET_MOVE)
        stop_price = max(0.05, resolved_price - DEFAULT_COMEBACK_REVERSION_V2_STOP_LOSS)
        signal_strength = (
            ((DEFAULT_COMEBACK_REVERSION_V2_ENTRY_PRICE_MAX - resolved_price) * 100.0)
            + max(0.0, float(row["net_points_last_5_events"]))
            + max(0.0, abs(score_diff) - 4.0)
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


def _select_comeback_reversion_v2_exit(group: pd.DataFrame, selection: TradeSelection) -> int | None:
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


def simulate_comeback_reversion_v2_trades(state_df: pd.DataFrame, *, slippage_cents: int) -> list[dict[str, object]]:
    return simulate_trade_loop(
        state_df,
        strategy_family="comeback_reversion_v2",
        entry_rule="q3_trailing_underdog_snapback",
        exit_rule="plus_8c_or_minus_5c_or_end",
        slippage_cents=slippage_cents,
        entry_selector=_select_comeback_reversion_v2_entry,
        exit_selector=_select_comeback_reversion_v2_exit,
    )


__all__ = ["simulate_comeback_reversion_v2_trades"]
