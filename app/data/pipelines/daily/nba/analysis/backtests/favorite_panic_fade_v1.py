from __future__ import annotations

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import simulate_trade_loop
from app.data.pipelines.daily.nba.analysis.backtests.specs import TradeSelection
from app.data.pipelines.daily.nba.analysis.contracts import (
    DEFAULT_FAVORITE_PANIC_ENTRY_PRICE_MAX,
    DEFAULT_FAVORITE_PANIC_MIN_MOMENTUM,
    DEFAULT_FAVORITE_PANIC_MIN_SCORE_DIFF,
    DEFAULT_FAVORITE_PANIC_OPEN_MIN,
    DEFAULT_FAVORITE_PANIC_RECROSS_THRESHOLD,
    DEFAULT_FAVORITE_PANIC_SEEN_BELOW_PRICE,
    DEFAULT_FAVORITE_PANIC_STOP_LOSS,
    DEFAULT_FAVORITE_PANIC_TARGET_MOVE,
    DEFAULT_FAVORITE_PANIC_TARGET_PRICE,
)


_ACTIVE_PERIODS = {"Q2", "Q3", "Q4", "OT1", "OT2"}


def _select_favorite_panic_fade_v1_entry(group: pd.DataFrame) -> TradeSelection | None:
    opening_price = float(group.iloc[0]["opening_price"]) if pd.notna(group.iloc[0]["opening_price"]) else None
    if opening_price is None or opening_price < DEFAULT_FAVORITE_PANIC_OPEN_MIN:
        return None

    previous_price = opening_price
    has_seen_panic = False
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
        if str(row["period_label"]) not in _ACTIVE_PERIODS:
            previous_price = resolved_price
            continue
        if resolved_price <= DEFAULT_FAVORITE_PANIC_SEEN_BELOW_PRICE:
            has_seen_panic = True
        if not has_seen_panic:
            previous_price = resolved_price
            continue
        if resolved_price > DEFAULT_FAVORITE_PANIC_ENTRY_PRICE_MAX:
            previous_price = resolved_price
            continue
        if float(previous_price) < DEFAULT_FAVORITE_PANIC_RECROSS_THRESHOLD <= resolved_price:
            if float(row["net_points_last_5_events"]) < DEFAULT_FAVORITE_PANIC_MIN_MOMENTUM:
                previous_price = resolved_price
                continue
            if float(row["score_diff"]) < DEFAULT_FAVORITE_PANIC_MIN_SCORE_DIFF:
                previous_price = resolved_price
                continue
            target_price = min(0.999999, max(DEFAULT_FAVORITE_PANIC_TARGET_PRICE, resolved_price + DEFAULT_FAVORITE_PANIC_TARGET_MOVE))
            stop_price = max(0.05, resolved_price - DEFAULT_FAVORITE_PANIC_STOP_LOSS)
            signal_strength = (
                ((resolved_price - DEFAULT_FAVORITE_PANIC_RECROSS_THRESHOLD) * 100.0)
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


def _select_favorite_panic_fade_v1_exit(group: pd.DataFrame, selection: TradeSelection) -> int | None:
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


def simulate_favorite_panic_fade_v1_trades(state_df: pd.DataFrame, *, slippage_cents: int) -> list[dict[str, object]]:
    return simulate_trade_loop(
        state_df,
        strategy_family="favorite_panic_fade_v1",
        entry_rule="favorite_recross_after_panic",
        exit_rule="recover_to_62c_or_plus_8c_or_minus_5c_or_end",
        slippage_cents=slippage_cents,
        entry_selector=_select_favorite_panic_fade_v1_entry,
        exit_selector=_select_favorite_panic_fade_v1_exit,
    )


__all__ = ["simulate_favorite_panic_fade_v1_trades"]
