from __future__ import annotations

from typing import Any

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import simulate_repeating_trade_loop
from app.data.pipelines.daily.nba.analysis.backtests.specs import TradeSelection


_ACTIVE_PERIODS = {"Q2", "Q3", "Q4", "OT1", "OT2"}
_FLOOR_ENTRY_MIN_PRICE = 0.10
_STANDARD_ENTRY_MIN_PRICE = 0.20
_ENTRY_MAX_PRICE = 0.35
_TARGET_GAIN = 0.06
_FLOOR_TARGET_PRICE = 0.27
_LOW_PRICE_REBOUND_MAX_ENTRY_PRICE = 0.22
_LOW_PRICE_REBOUND_TARGET_CAP = 0.40
_LOW_PRICE_REBOUND_TARGET_MULTIPLE = 2.0
_STOP_LOSS = 0.04
_MAX_CLOSE_SCORE_GAP = 10.0
_FLOOR_CLOSE_SCORE_GAP = 6.0
_FLOOR_CONTEXT_SECONDS = 90.0
_FLOOR_CONTEXT_MIN_ROWS = 2
_MIN_SECONDS_LEFT = 120.0


def _sustained_close_score_context(
    group: pd.DataFrame,
    *,
    index: int,
    max_score_gap: float,
) -> tuple[bool, dict[str, Any]]:
    history = group.iloc[: index + 1].copy()
    if history.empty:
        return False, {"context_row_count": 0}

    score_diff = pd.to_numeric(history["score_diff"], errors="coerce").dropna()
    if score_diff.empty:
        return False, {"context_row_count": int(len(history))}

    event_at = pd.to_datetime(history.get("event_at"), errors="coerce", utc=True)
    current_event_at = event_at.iloc[-1] if not event_at.empty else pd.NaT
    if pd.notna(current_event_at):
        starts_at = current_event_at - pd.Timedelta(seconds=_FLOOR_CONTEXT_SECONDS)
        mask = event_at >= starts_at
        context = history.loc[mask].copy()
        context_event_at = event_at.loc[mask]
    else:
        context = history.tail(max(_FLOOR_CONTEXT_MIN_ROWS, 4)).copy()
        context_event_at = pd.to_datetime(context.get("event_at"), errors="coerce", utc=True)

    context_score_diff = pd.to_numeric(context["score_diff"], errors="coerce").dropna()
    if len(context_score_diff) < _FLOOR_CONTEXT_MIN_ROWS:
        return False, {
            "context_row_count": int(len(context_score_diff)),
            "context_seconds": 0.0,
            "context_max_abs_score_gap": float(context_score_diff.abs().max()) if not context_score_diff.empty else None,
        }

    valid_times = context_event_at.dropna()
    context_seconds = 0.0
    if len(valid_times) >= 2:
        context_seconds = float((valid_times.iloc[-1] - valid_times.iloc[0]).total_seconds())
    context_max_abs_score_gap = float(context_score_diff.abs().max())
    context_current_gap = abs(float(context_score_diff.iloc[-1]))
    sustained = (
        context_seconds >= _FLOOR_CONTEXT_SECONDS
        and context_max_abs_score_gap <= max_score_gap
        and context_current_gap <= max_score_gap
    )
    return sustained, {
        "context_row_count": int(len(context_score_diff)),
        "context_seconds": context_seconds,
        "context_max_abs_score_gap": context_max_abs_score_gap,
        "context_current_score_gap": context_current_gap,
    }


def _select_underdog_range_scalp_entry(group: pd.DataFrame) -> TradeSelection | None:
    opening_price = float(group.iloc[0]["opening_price"]) if pd.notna(group.iloc[0]["opening_price"]) else None
    prices = pd.to_numeric(group["team_price"], errors="coerce")
    score_diff = pd.to_numeric(group["score_diff"], errors="coerce")
    net_recent = pd.to_numeric(group["net_points_last_5_events"], errors="coerce").fillna(0.0)
    seconds_left = pd.to_numeric(group["seconds_to_game_end"], errors="coerce")
    period_labels = group["period_label"].fillna("").astype(str)

    if opening_price is None:
        opening_price = float(prices.dropna().iloc[0]) if not prices.dropna().empty else None
    if opening_price is None:
        return None

    market_underdog = (opening_price <= 0.45) | (prices <= 0.45)
    standard_close_score = score_diff.abs() <= _MAX_CLOSE_SCORE_GAP
    floor_close_score = score_diff.abs() <= _FLOOR_CLOSE_SCORE_GAP
    standard_entry_band = prices.between(_STANDARD_ENTRY_MIN_PRICE, _ENTRY_MAX_PRICE, inclusive="both")
    floor_entry_band = prices.between(_FLOOR_ENTRY_MIN_PRICE, _STANDARD_ENTRY_MIN_PRICE, inclusive="left")
    active_clock = period_labels.isin(_ACTIVE_PERIODS) & (seconds_left >= _MIN_SECONDS_LEFT)

    for index, price in enumerate(prices.tolist()):
        if price is None or pd.isna(price):
            continue
        is_standard_entry = bool(standard_close_score.iloc[index] and standard_entry_band.iloc[index])
        context_ok = False
        context_metadata: dict[str, Any] = {}
        is_floor_entry = bool(floor_close_score.iloc[index] and floor_entry_band.iloc[index])
        if is_floor_entry:
            context_ok, context_metadata = _sustained_close_score_context(
                group,
                index=index,
                max_score_gap=_FLOOR_CLOSE_SCORE_GAP,
            )
            is_floor_entry = context_ok
        if not bool(market_underdog.iloc[index] and (is_standard_entry or is_floor_entry) and active_clock.iloc[index]):
            continue
        recent_window = prices.iloc[max(0, index - 8) : index + 1].dropna()
        recent_min = float(recent_window.min()) if not recent_window.empty else float(price)
        rebound_from_recent_low = max(0.0, float(price) - recent_min)
        if is_standard_entry and float(net_recent.iloc[index]) < 0.0 and rebound_from_recent_low < 0.02:
            continue
        row = group.iloc[index]
        entry_context = "close_score_floor" if is_floor_entry else "standard_range"
        target_price = min(0.95, float(price) + _TARGET_GAIN)
        if is_floor_entry or float(price) <= _LOW_PRICE_REBOUND_MAX_ENTRY_PRICE:
            rebound_target = min(
                _LOW_PRICE_REBOUND_TARGET_CAP,
                float(price) * _LOW_PRICE_REBOUND_TARGET_MULTIPLE,
            )
            floor_target = _FLOOR_TARGET_PRICE if is_floor_entry else 0.0
            target_price = min(0.95, max(float(price) + _TARGET_GAIN, rebound_target, floor_target))
        target_price = round(target_price, 4)
        stop_price = round(max(0.01, float(price) - _STOP_LOSS), 4)
        signal_strength = (
            (_ENTRY_MAX_PRICE - float(price)) * 100.0
            + max(0.0, _MAX_CLOSE_SCORE_GAP - abs(float(score_diff.iloc[index])))
            + max(0.0, float(net_recent.iloc[index]))
            + rebound_from_recent_low * 100.0
        )
        if is_floor_entry:
            signal_strength += max(0.0, _FLOOR_CLOSE_SCORE_GAP - abs(float(score_diff.iloc[index]))) * 2.0
            signal_strength += max(0.0, float(context_metadata.get("context_seconds") or 0.0) / 30.0)
        return TradeSelection(
            entry_index=index,
            metadata={
                "entry_min_price": _FLOOR_ENTRY_MIN_PRICE,
                "standard_entry_min_price": _STANDARD_ENTRY_MIN_PRICE,
                "entry_max_price": _ENTRY_MAX_PRICE,
                "floor_close_score_gap": _FLOOR_CLOSE_SCORE_GAP,
                "floor_context_seconds": _FLOOR_CONTEXT_SECONDS,
                "target_price": target_price,
                "stop_price": stop_price,
                "target_gain": _TARGET_GAIN,
                "low_price_rebound_max_entry_price": _LOW_PRICE_REBOUND_MAX_ENTRY_PRICE,
                "low_price_rebound_target_cap": _LOW_PRICE_REBOUND_TARGET_CAP,
                "low_price_rebound_target_multiple": _LOW_PRICE_REBOUND_TARGET_MULTIPLE,
                "stop_loss": _STOP_LOSS,
                "max_close_score_gap": _MAX_CLOSE_SCORE_GAP,
                "min_seconds_left": _MIN_SECONDS_LEFT,
                "entry_context": entry_context,
                "entry_score_diff": float(score_diff.iloc[index]) if pd.notna(score_diff.iloc[index]) else None,
                "entry_price_mode": row.get("price_mode"),
                "rebound_from_recent_low": rebound_from_recent_low,
                "signal_strength": signal_strength,
                **context_metadata,
            },
        )
    return None


def _select_underdog_range_scalp_exit(group: pd.DataFrame, selection: TradeSelection) -> int | None:
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
        seconds_left = row.get("seconds_to_game_end")
        if resolved_price >= target_price or resolved_price <= stop_price:
            return int(index)
        if pd.notna(seconds_left) and float(seconds_left) <= _MIN_SECONDS_LEFT:
            return int(index)
    return int(len(group) - 1)


def simulate_underdog_range_scalp_trades(state_df: pd.DataFrame, *, slippage_cents: int) -> list[dict[str, Any]]:
    return simulate_repeating_trade_loop(
        state_df,
        strategy_family="underdog_range_scalp",
        entry_rule="close_score_underdog_buy_below_35c_with_sustained_context",
        exit_rule="plus_6c_or_minus_4c_or_late_flatten",
        slippage_cents=slippage_cents,
        entry_selector=_select_underdog_range_scalp_entry,
        exit_selector=_select_underdog_range_scalp_exit,
    )


__all__ = ["simulate_underdog_range_scalp_trades"]
