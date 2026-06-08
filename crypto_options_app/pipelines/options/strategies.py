from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd

from crypto_options_app.pipelines.options.metrics import compute_trade_metrics
from crypto_options_app.services.crypto_options.strategy_catalog import crypto_options_strategy_catalog


TAKER_FEE_RATE = 0.07


def run_registered_strategy_backtests(panel_df: pd.DataFrame, *, fee_rate: float = TAKER_FEE_RATE) -> list[dict[str, Any]]:
    """Run the first crypto-options research strategy set.

    These are replay-only algorithms. They never place, cancel, sign, broadcast, redeem,
    or route orders.
    """

    runners: dict[str, Callable[[pd.DataFrame, float], dict[str, Any]]] = {
        "early_directional_hold_30s": _run_early_directional_hold,
        "four_minute_continuation_hold": _run_four_minute_continuation,
        "fair_value_gap_taker": _run_fair_value_gap_taker,
        "fakeout_reversal_opening_range": _run_fakeout_reversal,
        "polymarket_odds_momentum_scalp": _run_odds_momentum_scalp,
        "passive_fair_value_maker": _run_passive_fair_value_maker,
        "binary_pair_sum_arb": _run_binary_pair_sum_arb,
        "no_trade_filter": _run_no_trade_filter,
    }
    return [runners[spec["strategy_id"]](panel_df, fee_rate) for spec in crypto_options_strategy_catalog()]


def _run_early_directional_hold(panel_df: pd.DataFrame, fee_rate: float) -> dict[str, Any]:
    required = {"observed_at", "window_start_time", "best_ask", "outcome"}
    blockers = _missing_required(panel_df, required)
    if blockers:
        return _result("early_directional_hold_30s", "directional_prediction", "blocked", blockers)
    candidates = _work(panel_df)
    candidates["_elapsed_s"] = (candidates["observed_at"] - candidates["window_start_time"]).dt.total_seconds()
    candidates = candidates[(candidates["_elapsed_s"] >= 0) & (candidates["_elapsed_s"] <= 30)].copy()
    candidates["_side"] = candidates.apply(_directional_side, axis=1)
    candidates = _select_side_rows(candidates, side_column="_side")
    return _hold_to_settlement_result(
        "early_directional_hold_30s",
        "directional_prediction",
        candidates,
        fee_rate,
        blocker_context=["missing_settlement_labels_for_early_directional_hold"],
    )


def _run_four_minute_continuation(panel_df: pd.DataFrame, fee_rate: float) -> dict[str, Any]:
    required = {"observed_at", "time_to_close_seconds", "best_ask", "outcome"}
    blockers = _missing_required(panel_df, required)
    if blockers:
        return _result("four_minute_continuation_hold", "directional_prediction", "blocked", blockers)
    candidates = _work(panel_df)
    candidates = candidates[(candidates["time_to_close_seconds"] >= 45) & (candidates["time_to_close_seconds"] <= 75)].copy()
    candidates["_side"] = candidates.apply(_directional_side, axis=1)
    candidates = _select_side_rows(candidates, side_column="_side")
    return _hold_to_settlement_result(
        "four_minute_continuation_hold",
        "directional_prediction",
        candidates,
        fee_rate,
        blocker_context=["missing_settlement_labels_for_four_minute_continuation"],
    )


def _run_fair_value_gap_taker(panel_df: pd.DataFrame, fee_rate: float) -> dict[str, Any]:
    required = {"best_ask", "outcome", "time_to_close_seconds"}
    blockers = _missing_required(panel_df, required)
    if blockers:
        return _result("fair_value_gap_taker", "hybrid", "blocked", blockers)
    work = _work(panel_df)
    work["_fair_up_prob"] = work.apply(_fair_up_probability, axis=1)
    work["_side"] = work["_fair_up_prob"].apply(lambda value: "up" if value >= 0.5 else "down")
    work["_fair_side_prob"] = work.apply(lambda row: row["_fair_up_prob"] if row["_side"] == "up" else 1.0 - row["_fair_up_prob"], axis=1)
    work["_entry_price"] = pd.to_numeric(work["best_ask"], errors="coerce")
    work["_entry_fee"] = work["_entry_price"].apply(lambda price: _taker_fee(price, fee_rate))
    candidates = work[(work["_fair_side_prob"] - work["_entry_price"] - work["_entry_fee"]) >= 0.02].copy()
    candidates = _select_side_rows(candidates, side_column="_side")
    return _hold_to_settlement_result(
        "fair_value_gap_taker",
        "hybrid",
        candidates,
        fee_rate,
        blocker_context=["missing_settlement_labels_for_fair_value_gap_taker"],
        extra_summary={"min_edge_after_fee": 0.02},
    )


def _run_fakeout_reversal(panel_df: pd.DataFrame, fee_rate: float) -> dict[str, Any]:
    required = {"underlying_close", "opening_range_high", "opening_range_low", "best_ask", "outcome"}
    blockers = _missing_required(panel_df, required)
    if blockers:
        return _result(
            "fakeout_reversal_opening_range",
            "mean_reversion",
            "blocked",
            blockers + ["opening_range_path_not_available_from_current_panel"],
        )
    work = _work(panel_df)
    work["_side"] = work.apply(
        lambda row: "down"
        if float(row["underlying_close"]) < float(row["opening_range_high"])
        and bool(row.get("broke_above_opening_range"))
        else "up",
        axis=1,
    )
    candidates = _select_side_rows(work, side_column="_side")
    return _hold_to_settlement_result(
        "fakeout_reversal_opening_range",
        "mean_reversion",
        candidates,
        fee_rate,
        blocker_context=["missing_settlement_labels_for_fakeout_reversal"],
    )


def _run_odds_momentum_scalp(panel_df: pd.DataFrame, fee_rate: float) -> dict[str, Any]:
    required = {"token_id", "observed_at", "polymarket_mid_price"}
    blockers = _missing_required(panel_df, required)
    if blockers:
        return _result("polymarket_odds_momentum_scalp", "microstructure_scalping", "blocked", blockers)
    work = _work(panel_df).dropna(subset=["polymarket_mid_price"]).copy()
    if work.empty:
        return _result("polymarket_odds_momentum_scalp", "microstructure_scalping", "blocked", ["missing_odds_path"])
    trades: list[dict[str, Any]] = []
    executable = {"best_bid", "best_ask"}.issubset(work.columns) and work[["best_bid", "best_ask"]].notna().all(axis=1).any()
    for token_id, group in work.sort_values(["token_id", "observed_at"], kind="mergesort").groupby("token_id", sort=False):
        path = group.reset_index(drop=True)
        path["_odds_delta"] = pd.to_numeric(path["polymarket_mid_price"], errors="coerce").diff()
        for idx in path.index[path["_odds_delta"] >= 0.015].tolist():
            exit_idx = min(idx + 1, len(path) - 1)
            if exit_idx == idx:
                continue
            entry = path.loc[idx]
            exit_row = path.loc[exit_idx]
            entry_price = _entry_price(entry, allow_midpoint=True)
            exit_price = _exit_price(exit_row, allow_midpoint=True)
            if entry_price is None or exit_price is None:
                continue
            fee = _taker_fee(entry_price, fee_rate) + _taker_fee(exit_price, fee_rate)
            pnl = exit_price - entry_price - fee
            trades.append(_trade_record("polymarket_odds_momentum_scalp", entry, entry_price, exit_price, fee, pnl, token_id=str(token_id)))
    blockers = [] if executable else ["midpoint_theoretical_only_missing_executable_bid_ask"]
    status = "research_proxy_complete" if trades else "no_trades"
    if blockers and trades:
        status = "theoretical_proxy_complete"
    return _result(
        "polymarket_odds_momentum_scalp",
        "microstructure_scalping",
        status,
        blockers,
        trades=trades,
        summary={"min_odds_delta": 0.015, "exit_horizon_rows": 1, "execution_model": "bid_ask_when_available_else_midpoint_proxy"},
    )


def _run_passive_fair_value_maker(panel_df: pd.DataFrame, fee_rate: float) -> dict[str, Any]:
    required = {"best_bid", "best_ask", "outcome", "time_to_close_seconds"}
    blockers = _missing_required(panel_df, required)
    if blockers:
        return _result("passive_fair_value_maker", "microstructure_market_making", "blocked", blockers)
    work = _work(panel_df)
    work["_fair_up_prob"] = work.apply(_fair_up_probability, axis=1)
    quoteable = int(((work["best_bid"].notna()) & (work["best_ask"].notna())).sum())
    return _result(
        "passive_fair_value_maker",
        "microstructure_market_making",
        "blocked",
        ["queue_position_not_modelled", "maker_fill_probability_not_modelled"],
        summary={"quoteable_rows": quoteable, "fee_rate_used_for_stress": fee_rate},
    )


def _run_binary_pair_sum_arb(panel_df: pd.DataFrame, fee_rate: float) -> dict[str, Any]:
    required = {"market_id", "observed_at", "outcome", "best_ask"}
    blockers = _missing_required(panel_df, required)
    if blockers:
        return _result("binary_pair_sum_arb", "relative_value", "blocked", blockers)
    work = _work(panel_df).dropna(subset=["market_id", "observed_at", "best_ask"]).copy()
    work["_outcome_norm"] = work["outcome"].apply(_normalize_side)
    trades: list[dict[str, Any]] = []
    for (_, observed_at), group in work.groupby(["market_id", "observed_at"], sort=False):
        up_rows = group[group["_outcome_norm"].isin({"up", "yes", "above"})]
        down_rows = group[group["_outcome_norm"].isin({"down", "no", "below"})]
        if up_rows.empty or down_rows.empty:
            continue
        up = up_rows.iloc[0]
        down = down_rows.iloc[0]
        up_ask = _to_float(up["best_ask"])
        down_ask = _to_float(down["best_ask"])
        if up_ask is None or down_ask is None:
            continue
        fee = _taker_fee(up_ask, fee_rate) + _taker_fee(down_ask, fee_rate)
        pnl = 1.0 - up_ask - down_ask - fee
        if pnl <= 0:
            continue
        trades.append(
            {
                "strategy_id": "binary_pair_sum_arb",
                "event_id": up.get("event_id"),
                "market_id": up.get("market_id"),
                "token_id": f"{up.get('token_id')}+{down.get('token_id')}",
                "observed_at": observed_at,
                "sample_id": up.get("event_id") or up.get("market_id"),
                "side": "both",
                "entry_price": up_ask + down_ask,
                "exit_price": 1.0,
                "fees": fee,
                "pnl_net": pnl,
                "return_net": pnl,
                "win": pnl > 0,
                "execution_model": "buy_both_sides_taker",
            }
        )
    return _result("binary_pair_sum_arb", "relative_value", "research_backtest_complete" if trades else "no_trades", [], trades=trades)


def _run_no_trade_filter(panel_df: pd.DataFrame, fee_rate: float) -> dict[str, Any]:
    rows = int(len(panel_df))
    return _result(
        "no_trade_filter",
        "risk_control",
        "baseline_complete",
        [],
        summary={
            "observation_rows": rows,
            "pnl": 0.0,
            "purpose": "Default posture until a strategy clears data, cost, latency, and drawdown gates.",
        },
    )


def _hold_to_settlement_result(
    strategy_id: str,
    family: str,
    candidates: pd.DataFrame,
    fee_rate: float,
    *,
    blocker_context: list[str],
    extra_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if candidates.empty:
        return _result(strategy_id, family, "no_trades", [], summary=extra_summary or {})
    if not _has_labels(candidates):
        return _result(
            strategy_id,
            family,
            "blocked",
            blocker_context,
            summary={**(extra_summary or {}), "candidate_rows": int(len(candidates))},
        )
    trades: list[dict[str, Any]] = []
    for _, row in candidates.iterrows():
        entry_price = _entry_price(row, allow_midpoint=False)
        if entry_price is None:
            continue
        side = str(row.get("_side") or _normalize_side(row.get("outcome")) or "")
        win = _row_wins(row, side)
        fee = _taker_fee(entry_price, fee_rate)
        pnl = (1.0 - entry_price - fee) if win else (0.0 - entry_price - fee)
        trades.append(_trade_record(strategy_id, row, entry_price, 1.0 if win else 0.0, fee, pnl, side=side, win=win))
    status = "research_backtest_complete" if trades else "blocked"
    blockers = [] if trades else ["missing_executable_entry_price"]
    return _result(strategy_id, family, status, blockers, trades=trades, summary=extra_summary or {})


def _result(
    strategy_id: str,
    family: str,
    status: str,
    blockers: list[str],
    *,
    trades: list[dict[str, Any]] | None = None,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trades = trades or []
    metrics = compute_trade_metrics(pd.DataFrame(trades), sample_column="sample_id")
    return {
        "strategy_id": strategy_id,
        "strategy_family": family,
        "status": status,
        "trade_count": int(len(trades)),
        "blockers": sorted(set(blockers + (metrics.get("blockers") or []))),
        "summary": summary or {},
        "metrics": metrics,
        "trades": trades[:25],
        "execution_boundary": "research_backtest_only",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
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
        "settlement_threshold",
        "distance_to_threshold",
        "volatility",
        "momentum_3",
    ):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _missing_required(panel_df: pd.DataFrame, required: set[str]) -> list[str]:
    if panel_df.empty:
        return ["missing_joined_event_state_panel"]
    return [f"missing_field:{field}" for field in sorted(required - set(panel_df.columns))]


def _select_side_rows(frame: pd.DataFrame, *, side_column: str) -> pd.DataFrame:
    if "outcome" not in frame.columns:
        return frame.iloc[0:0].copy()
    selected = frame[frame["outcome"].apply(_normalize_side) == frame[side_column].apply(_normalize_side)].copy()
    return selected


def _directional_side(row: pd.Series) -> str:
    distance = _to_float(row.get("distance_to_threshold"))
    if distance is not None and distance != 0:
        return "up" if distance >= 0 else "down"
    momentum = _to_float(row.get("momentum_3"))
    if momentum is not None:
        return "up" if momentum >= 0 else "down"
    close = _to_float(row.get("underlying_close"))
    threshold = _to_float(row.get("settlement_threshold"))
    if close is not None and threshold is not None:
        return "up" if close >= threshold else "down"
    return "up"


def _fair_up_probability(row: pd.Series) -> float:
    distance = _to_float(row.get("distance_to_threshold")) or 0.0
    close = abs(_to_float(row.get("underlying_close")) or 0.0)
    volatility = abs(_to_float(row.get("volatility")) or 0.0)
    time_remaining = max(_to_float(row.get("time_to_close_seconds")) or 60.0, 1.0)
    dollar_sigma = max(close * volatility * math.sqrt(time_remaining / 60.0), 1.0)
    z_score = max(min(distance / dollar_sigma, 8.0), -8.0)
    momentum = _to_float(row.get("momentum_3")) or 0.0
    logit = (1.6 * z_score) + max(min(momentum * 50.0, 2.0), -2.0)
    return 1.0 / (1.0 + math.exp(-logit))


def _has_labels(frame: pd.DataFrame) -> bool:
    return any(column in frame.columns and frame[column].notna().any() for column in ("resolved_outcome", "resolved_direction", "label"))


def _row_wins(row: pd.Series, side: str) -> bool:
    for column in ("resolved_outcome", "resolved_direction", "label"):
        if column not in row or pd.isna(row.get(column)):
            continue
        resolved = _normalize_side(row.get(column))
        if resolved in {"true", "1"}:
            return _normalize_side(row.get("outcome")) == _normalize_side(side)
        return resolved == _normalize_side(side)
    return False


def _trade_record(
    strategy_id: str,
    row: pd.Series,
    entry_price: float,
    exit_price: float,
    fee: float,
    pnl: float,
    *,
    token_id: str | None = None,
    side: str | None = None,
    win: bool | None = None,
) -> dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "event_id": row.get("event_id"),
        "market_id": row.get("market_id"),
        "token_id": token_id or row.get("token_id"),
        "observed_at": row.get("observed_at"),
        "sample_id": row.get("event_id") or row.get("market_id"),
        "side": side or _normalize_side(row.get("outcome")),
        "entry_price": float(entry_price),
        "exit_price": float(exit_price),
        "fees": float(fee),
        "pnl_net": float(pnl),
        "return_net": float(pnl),
        "win": bool(win) if win is not None else pnl > 0,
        "time_to_close_seconds": _to_float(row.get("time_to_close_seconds")),
        "execution_model": "taker_research_replay",
    }


def _entry_price(row: pd.Series, *, allow_midpoint: bool) -> float | None:
    price = _to_float(row.get("best_ask"))
    if price is not None:
        return price
    return _to_float(row.get("polymarket_mid_price")) if allow_midpoint else None


def _exit_price(row: pd.Series, *, allow_midpoint: bool) -> float | None:
    price = _to_float(row.get("best_bid"))
    if price is not None:
        return price
    return _to_float(row.get("polymarket_mid_price")) if allow_midpoint else None


def _taker_fee(price: float | None, fee_rate: float) -> float:
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


__all__ = ["TAKER_FEE_RATE", "run_registered_strategy_backtests"]
