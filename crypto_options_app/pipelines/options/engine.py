from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from crypto_options_app.pipelines.options.metrics import compute_trade_metrics
from crypto_options_app.pipelines.options.strategies import TAKER_FEE_RATE


def run_crypto_options_replay_engine(
    panel_df: pd.DataFrame,
    *,
    fee_rate: float = TAKER_FEE_RATE,
    allow_midpoint_entries: bool = True,
) -> dict[str, Any]:
    """Chronological research replay engine for crypto-options panels.

    This engine is intentionally conservative about status. If it uses midpoint
    prices because executable bid/ask is missing, results are marked theoretical.
    """

    if panel_df.empty:
        return _blocked(["missing_joined_event_state_panel"])
    required = {"event_id", "market_id", "token_id", "observed_at", "outcome", "resolved_direction"}
    missing = sorted(required - set(panel_df.columns))
    if missing:
        return _blocked([f"missing_field:{field}" for field in missing])
    frame = _work(panel_df)
    if not frame["resolved_direction"].notna().any():
        return _blocked(["missing_replay_labels"])
    if not _has_entry_prices(frame, allow_midpoint_entries=allow_midpoint_entries):
        return _blocked(["missing_executable_or_midpoint_entry_prices"])

    strategies = [
        _prewindow_directional_replay(frame, fee_rate=fee_rate, allow_midpoint_entries=allow_midpoint_entries),
        _early_directional_replay(frame, fee_rate=fee_rate, allow_midpoint_entries=allow_midpoint_entries),
        _four_minute_continuation_replay(frame, fee_rate=fee_rate, allow_midpoint_entries=allow_midpoint_entries),
        _fair_value_gap_replay(frame, fee_rate=fee_rate, allow_midpoint_entries=allow_midpoint_entries),
        _odds_momentum_replay(frame, fee_rate=fee_rate, allow_midpoint_entries=allow_midpoint_entries),
        _no_trade_replay(frame),
    ]
    blockers = sorted({blocker for result in strategies for blocker in (result.get("blockers") or [])})
    complete = [result for result in strategies if result.get("trade_count")]
    return {
        "schema_version": "crypto_options_chronological_replay_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "theoretical_midpoint_replay_complete" if complete and _uses_midpoint(frame) else ("replay_complete" if complete else "no_trades"),
        "execution_boundary": "research_backtest_only",
        "allow_midpoint_entries": bool(allow_midpoint_entries),
        "live_trading_authorized": False,
        "blockers": blockers,
        "results": strategies,
    }


def _early_directional_replay(frame: pd.DataFrame, *, fee_rate: float, allow_midpoint_entries: bool) -> dict[str, Any]:
    candidates = frame.copy()
    candidates["_elapsed_s"] = (candidates["observed_at"] - candidates["window_start_time"]).dt.total_seconds()
    candidates = candidates[(candidates["_elapsed_s"] >= 0) & (candidates["_elapsed_s"] <= 30)].copy()
    candidates["_side"] = candidates.apply(_directional_side, axis=1)
    candidates = _first_per_market_side(candidates)
    return _hold_result("replay_early_directional_hold_30s", candidates, fee_rate, allow_midpoint_entries)


def _prewindow_directional_replay(frame: pd.DataFrame, *, fee_rate: float, allow_midpoint_entries: bool) -> dict[str, Any]:
    candidates = frame.copy()
    candidates["_seconds_to_open"] = (candidates["window_start_time"] - candidates["observed_at"]).dt.total_seconds()
    candidates = candidates[(candidates["_seconds_to_open"] >= 0) & (candidates["_seconds_to_open"] <= 900)].copy()
    candidates["_side"] = candidates.apply(_directional_side, axis=1)
    candidates = candidates.sort_values("observed_at").groupby(["market_id", "_side"], as_index=False, sort=False).tail(1)
    return _hold_result(
        "replay_prewindow_directional_hold",
        candidates,
        fee_rate,
        allow_midpoint_entries,
        summary={"max_seconds_before_open": 900, "purpose": "Coarse replay fallback for price-history data with no in-window points."},
        extra_blockers=["coarse_price_history_prewindow_only"],
    )


def _four_minute_continuation_replay(frame: pd.DataFrame, *, fee_rate: float, allow_midpoint_entries: bool) -> dict[str, Any]:
    candidates = frame[(frame["time_to_close_seconds"] >= 45) & (frame["time_to_close_seconds"] <= 75)].copy()
    candidates["_side"] = candidates.apply(_directional_side, axis=1)
    candidates = _first_per_market_side(candidates)
    return _hold_result("replay_four_minute_continuation", candidates, fee_rate, allow_midpoint_entries)


def _fair_value_gap_replay(frame: pd.DataFrame, *, fee_rate: float, allow_midpoint_entries: bool) -> dict[str, Any]:
    candidates = frame.copy()
    candidates["_entry_price"] = candidates.apply(lambda row: _entry_price(row, allow_midpoint_entries=allow_midpoint_entries), axis=1)
    candidates["_fair_up_prob"] = candidates.apply(_fair_up_probability, axis=1)
    candidates["_side"] = candidates["_fair_up_prob"].apply(lambda value: "up" if value >= 0.5 else "down")
    candidates["_fair_side_prob"] = candidates.apply(lambda row: row["_fair_up_prob"] if row["_side"] == "up" else 1.0 - row["_fair_up_prob"], axis=1)
    candidates["_edge"] = candidates["_fair_side_prob"] - candidates["_entry_price"] - candidates["_entry_price"].apply(lambda price: _fee(price, fee_rate))
    candidates = candidates[candidates["_edge"] >= 0.02].copy()
    candidates = _first_per_market_side(candidates)
    return _hold_result("replay_fair_value_gap_taker", candidates, fee_rate, allow_midpoint_entries, summary={"min_edge_after_fee": 0.02})


def _odds_momentum_replay(frame: pd.DataFrame, *, fee_rate: float, allow_midpoint_entries: bool) -> dict[str, Any]:
    trades: list[dict[str, Any]] = []
    if "polymarket_mid_price" not in frame.columns:
        return _result("replay_odds_momentum_scalp", [], ["missing_polymarket_mid_price"])
    for token_id, group in frame.dropna(subset=["polymarket_mid_price"]).sort_values(["token_id", "observed_at"]).groupby("token_id", sort=False):
        path = group.reset_index(drop=True)
        path["_odds_delta"] = path["polymarket_mid_price"].diff()
        for idx in path.index[path["_odds_delta"] >= 0.015].tolist():
            if idx + 1 >= len(path):
                continue
            entry = path.loc[idx]
            exit_row = path.loc[idx + 1]
            entry_price = _entry_price(entry, allow_midpoint_entries=allow_midpoint_entries)
            exit_price = _exit_price(exit_row, allow_midpoint_entries=allow_midpoint_entries)
            if entry_price is None or exit_price is None:
                continue
            fees = _fee(entry_price, fee_rate) + _fee(exit_price, fee_rate)
            pnl = exit_price - entry_price - fees
            trades.append(_trade("replay_odds_momentum_scalp", entry, entry_price, exit_price, fees, pnl, token_id=str(token_id), side=entry.get("outcome")))
    blockers = _midpoint_blockers(frame) if trades else []
    return _result("replay_odds_momentum_scalp", trades, blockers, summary={"min_odds_delta": 0.015, "exit_horizon_rows": 1})


def _no_trade_replay(frame: pd.DataFrame) -> dict[str, Any]:
    return _result(
        "replay_no_trade_baseline",
        [],
        [],
        summary={"observation_rows": int(len(frame)), "pnl": 0.0, "purpose": "Zero-risk comparator."},
        status="baseline_complete",
    )


def _hold_result(
    strategy_id: str,
    candidates: pd.DataFrame,
    fee_rate: float,
    allow_midpoint_entries: bool,
    *,
    summary: dict[str, Any] | None = None,
    extra_blockers: list[str] | None = None,
) -> dict[str, Any]:
    trades: list[dict[str, Any]] = []
    for _, row in candidates.iterrows():
        entry_price = _entry_price(row, allow_midpoint_entries=allow_midpoint_entries)
        if entry_price is None:
            continue
        side = str(row.get("_side") or _normalize_side(row.get("outcome")))
        if _normalize_side(row.get("outcome")) != _normalize_side(side):
            continue
        win = _normalize_side(row.get("resolved_direction")) == _normalize_side(side)
        fees = _fee(entry_price, fee_rate)
        pnl = (1.0 - entry_price - fees) if win else (0.0 - entry_price - fees)
        trades.append(_trade(strategy_id, row, entry_price, 1.0 if win else 0.0, fees, pnl, side=side, win=win))
    blockers = (_midpoint_blockers(candidates) if trades else []) + (extra_blockers or [])
    return _result(strategy_id, trades, blockers, summary=summary or {})


def _result(
    strategy_id: str,
    trades: list[dict[str, Any]],
    blockers: list[str],
    *,
    summary: dict[str, Any] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    metrics = compute_trade_metrics(pd.DataFrame(trades), sample_column="sample_id")
    return {
        "strategy_id": strategy_id,
        "status": status or ("theoretical_proxy_complete" if trades and blockers else ("research_backtest_complete" if trades else "no_trades")),
        "trade_count": int(len(trades)),
        "blockers": sorted(set(blockers + (metrics.get("blockers") or []))),
        "summary": summary or {},
        "metrics": metrics,
        "trades": trades[:25],
        "execution_boundary": "research_backtest_only",
    }


def _blocked(blockers: list[str]) -> dict[str, Any]:
    return {
        "schema_version": "crypto_options_chronological_replay_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "blocked",
        "execution_boundary": "research_backtest_only",
        "allow_midpoint_entries": False,
        "live_trading_authorized": False,
        "blockers": blockers,
        "results": [],
    }


def _work(panel_df: pd.DataFrame) -> pd.DataFrame:
    frame = panel_df.copy()
    for column in ("observed_at", "window_start_time", "window_end_time"):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    for column in (
        "best_bid",
        "best_ask",
        "polymarket_mid_price",
        "time_to_close_seconds",
        "underlying_close",
        "distance_to_threshold",
        "volatility",
        "momentum_3",
    ):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if ("time_to_close_seconds" not in frame.columns or not frame["time_to_close_seconds"].notna().any()) and {
        "window_end_time",
        "observed_at",
    }.issubset(frame.columns):
        frame["time_to_close_seconds"] = (frame["window_end_time"] - frame["observed_at"]).dt.total_seconds()
    return frame.sort_values(["observed_at", "market_id", "token_id"], kind="mergesort").reset_index(drop=True)


def _first_per_market_side(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return candidates
    candidates["_outcome_norm"] = candidates["outcome"].apply(_normalize_side)
    candidates["_side_norm"] = candidates["_side"].apply(_normalize_side)
    selected = candidates[candidates["_outcome_norm"] == candidates["_side_norm"]].copy()
    return selected.sort_values("observed_at").groupby(["market_id", "_side_norm"], as_index=False, sort=False).head(1)


def _entry_price(row: pd.Series, *, allow_midpoint_entries: bool) -> float | None:
    price = _to_float(row.get("best_ask"))
    if price is not None:
        return price
    if allow_midpoint_entries:
        return _to_float(row.get("polymarket_mid_price"))
    return None


def _exit_price(row: pd.Series, *, allow_midpoint_entries: bool) -> float | None:
    price = _to_float(row.get("best_bid"))
    if price is not None:
        return price
    if allow_midpoint_entries:
        return _to_float(row.get("polymarket_mid_price"))
    return None


def _directional_side(row: pd.Series) -> str:
    distance = _to_float(row.get("distance_to_threshold"))
    if distance is not None and distance != 0:
        return "up" if distance >= 0 else "down"
    momentum = _to_float(row.get("momentum_3"))
    if momentum is not None:
        return "up" if momentum >= 0 else "down"
    resolved = _normalize_side(row.get("resolved_direction"))
    return resolved if resolved in {"up", "down"} else "up"


def _fair_up_probability(row: pd.Series) -> float:
    distance = _to_float(row.get("distance_to_threshold")) or 0.0
    close = abs(_to_float(row.get("underlying_close")) or _to_float(row.get("reference_start_price")) or 0.0)
    volatility = abs(_to_float(row.get("volatility")) or 0.0005)
    time_remaining = max(_to_float(row.get("time_to_close_seconds")) or 60.0, 1.0)
    dollar_sigma = max(close * volatility * (time_remaining / 60.0) ** 0.5, 1.0)
    z_score = max(min(distance / dollar_sigma, 8.0), -8.0)
    momentum = _to_float(row.get("momentum_3")) or 0.0
    logit = (1.6 * z_score) + max(min(momentum * 50.0, 2.0), -2.0)
    import math

    return 1.0 / (1.0 + math.exp(-logit))


def _trade(
    strategy_id: str,
    row: pd.Series,
    entry_price: float,
    exit_price: float,
    fees: float,
    pnl: float,
    *,
    token_id: str | None = None,
    side: Any = None,
    win: bool | None = None,
) -> dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "event_id": row.get("event_id"),
        "market_id": row.get("market_id"),
        "token_id": token_id or row.get("token_id"),
        "observed_at": row.get("observed_at"),
        "sample_id": row.get("event_id") or row.get("market_id"),
        "side": _normalize_side(side),
        "entry_price": float(entry_price),
        "exit_price": float(exit_price),
        "fees": float(fees),
        "pnl_net": float(pnl),
        "return_net": float(pnl),
        "win": bool(win) if win is not None else pnl > 0,
        "label_authority": row.get("label_authority"),
        "execution_model": "midpoint_theoretical" if pd.isna(row.get("best_ask")) else "executable_bid_ask",
    }


def _has_entry_prices(frame: pd.DataFrame, *, allow_midpoint_entries: bool) -> bool:
    if "best_ask" in frame.columns and frame["best_ask"].notna().any():
        return True
    return bool(allow_midpoint_entries and "polymarket_mid_price" in frame.columns and frame["polymarket_mid_price"].notna().any())


def _uses_midpoint(frame: pd.DataFrame) -> bool:
    return "best_ask" not in frame.columns or not frame["best_ask"].notna().any()


def _midpoint_blockers(frame: pd.DataFrame) -> list[str]:
    return ["midpoint_theoretical_only_missing_executable_bid_ask"] if _uses_midpoint(frame) else []


def _fee(price: float | None, fee_rate: float) -> float:
    if price is None:
        return 0.0
    bounded = min(max(float(price), 0.0), 1.0)
    return float(fee_rate) * bounded * (1.0 - bounded)


def _normalize_side(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"yes", "true", "1", "above", "up"}:
        return "up"
    if normalized in {"no", "false", "0", "below", "down"}:
        return "down"
    return normalized


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["run_crypto_options_replay_engine"]
