from __future__ import annotations

from typing import Any

import pandas as pd


METRIC_CONTRACT = {
    "trade_count": "Number of simulated trades.",
    "win_rate": "Share of trades with positive net PnL.",
    "return_sum": "Sum of per-trade net returns or PnL values.",
    "return_avg": "Average per-trade net return.",
    "return_min": "Worst single-trade net return.",
    "return_max": "Best single-trade net return.",
    "sample_return_avg": "Average sample return across event/time samples.",
    "sample_return_min": "Worst sample return.",
    "sample_return_max": "Best sample return.",
    "sample_win_rate_avg": "Average win rate across event/time samples.",
    "sample_win_rate_min": "Worst sample win rate.",
    "sample_win_rate_max": "Best sample win rate.",
    "max_sequential_losses": "Longest global consecutive losing-trade streak.",
    "avg_sequential_losses": "Average losing streak length.",
    "sample_max_sequential_losses_max": "Worst per-sample losing streak.",
    "sample_max_sequential_losses_avg": "Average of per-sample max losing streaks.",
    "max_drawdown": "Worst cumulative PnL drawdown.",
    "profit_factor": "Gross wins divided by gross losses.",
    "expectancy": "Average net PnL per trade.",
    "avg_win": "Average positive-trade PnL.",
    "avg_loss": "Average negative-trade PnL.",
    "payoff_ratio": "Average win divided by absolute average loss.",
    "fill_rate": "Filled size divided by requested size when size fields exist.",
    "partial_fill_rate": "Share of trades with 0 < fill_ratio < 1.",
}


def metric_contract() -> dict[str, str]:
    return dict(METRIC_CONTRACT)


def compute_trade_metrics(trades_df: pd.DataFrame, *, sample_column: str | None = None) -> dict[str, Any]:
    """Compute research metrics for executed or simulated trade rows.

    The function is deliberately data-frame based so strategy replays, shadow logs, and
    random/block samples can all report with the same schema.
    """

    if trades_df.empty:
        return _empty_metrics()

    frame = trades_df.copy()
    pnl_column = _first_present(frame, ("pnl_net", "return_net", "pnl", "return"))
    if pnl_column is None:
        return {**_empty_metrics(), "blockers": ["missing_pnl_or_return_column"]}

    frame["_metric_pnl"] = pd.to_numeric(frame[pnl_column], errors="coerce").fillna(0.0)
    if "win" in frame.columns:
        frame["_metric_win"] = frame["win"].fillna(frame["_metric_pnl"] > 0).astype(bool)
    else:
        frame["_metric_win"] = frame["_metric_pnl"] > 0
    frame["_metric_loss"] = frame["_metric_pnl"] < 0

    sample_column = sample_column or _first_present(frame, ("sample_id", "sample", "event_id", "market_id"))
    sample_metrics = _compute_sample_metrics(frame, sample_column=sample_column)
    streaks = _loss_streaks(frame["_metric_loss"].tolist())
    win_streaks = _loss_streaks((~frame["_metric_win"]).tolist(), invert=True)
    equity = frame["_metric_pnl"].cumsum()
    drawdown = equity - equity.cummax()
    wins = frame.loc[frame["_metric_pnl"] > 0, "_metric_pnl"]
    losses = frame.loc[frame["_metric_pnl"] < 0, "_metric_pnl"]
    requested = pd.to_numeric(frame.get("requested_size"), errors="coerce") if "requested_size" in frame.columns else pd.Series(dtype=float)
    filled = pd.to_numeric(frame.get("filled_size"), errors="coerce") if "filled_size" in frame.columns else pd.Series(dtype=float)
    fill_ratio = pd.to_numeric(frame.get("fill_ratio"), errors="coerce") if "fill_ratio" in frame.columns else pd.Series(dtype=float)
    if fill_ratio.empty and not requested.empty and not filled.empty:
        fill_ratio = (filled / requested.replace(0.0, pd.NA)).dropna()

    return {
        "trade_count": int(len(frame)),
        "win_count": int(frame["_metric_win"].sum()),
        "loss_count": int(frame["_metric_loss"].sum()),
        "win_rate": _safe_float(frame["_metric_win"].mean()),
        "return_sum": _safe_float(frame["_metric_pnl"].sum()),
        "return_avg": _safe_float(frame["_metric_pnl"].mean()),
        "return_min": _safe_float(frame["_metric_pnl"].min()),
        "return_max": _safe_float(frame["_metric_pnl"].max()),
        "return_median": _safe_float(frame["_metric_pnl"].median()),
        "return_std": _safe_float(frame["_metric_pnl"].std(ddof=0)),
        "avg_win": _safe_float(wins.mean()) if not wins.empty else None,
        "avg_loss": _safe_float(losses.mean()) if not losses.empty else None,
        "profit_factor": _profit_factor(wins, losses),
        "expectancy": _safe_float(frame["_metric_pnl"].mean()),
        "payoff_ratio": _payoff_ratio(wins, losses),
        "max_sequential_losses": max(streaks) if streaks else 0,
        "avg_sequential_losses": _safe_float(pd.Series(streaks).mean()) if streaks else 0.0,
        "p95_sequential_losses": _safe_float(pd.Series(streaks).quantile(0.95)) if streaks else 0.0,
        "max_sequential_wins": max(win_streaks) if win_streaks else 0,
        "avg_sequential_wins": _safe_float(pd.Series(win_streaks).mean()) if win_streaks else 0.0,
        "max_drawdown": _safe_float(drawdown.min()) if not drawdown.empty else 0.0,
        "avg_drawdown": _safe_float(drawdown[drawdown < 0].mean()) if bool((drawdown < 0).any()) else 0.0,
        "time_underwater_trades": int((drawdown < 0).sum()),
        "fill_rate": _safe_float(fill_ratio.mean()) if not fill_ratio.empty else None,
        "partial_fill_rate": _safe_float(((fill_ratio > 0) & (fill_ratio < 1)).mean()) if not fill_ratio.empty else None,
        "sample_column": sample_column,
        "sample_metrics": sample_metrics,
        "blockers": [],
    }


def summarize_strategy_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for result in results:
        metrics = result.get("metrics") or {}
        rows.append(
            {
                "strategy_id": result.get("strategy_id") or result.get("strategy_family"),
                "status": result.get("status"),
                "trade_count": int(result.get("trade_count") or metrics.get("trade_count") or 0),
                "win_rate": metrics.get("win_rate"),
                "return_sum": metrics.get("return_sum"),
                "return_avg": metrics.get("return_avg"),
                "max_sequential_losses": metrics.get("max_sequential_losses"),
                "max_drawdown": metrics.get("max_drawdown"),
                "profit_factor": metrics.get("profit_factor"),
                "blockers": result.get("blockers") or [],
            }
        )
    return {
        "schema_version": "crypto_options_metric_summary_v1",
        "metric_contract": metric_contract(),
        "results": rows,
    }


def _compute_sample_metrics(frame: pd.DataFrame, *, sample_column: str | None) -> dict[str, Any]:
    if sample_column is None or sample_column not in frame.columns:
        return {
            "sample_count": 1,
            "win_rate_avg": _safe_float(frame["_metric_win"].mean()),
            "win_rate_min": _safe_float(frame["_metric_win"].mean()),
            "win_rate_max": _safe_float(frame["_metric_win"].mean()),
            "return_avg": _safe_float(frame["_metric_pnl"].sum()),
            "return_min": _safe_float(frame["_metric_pnl"].sum()),
            "return_max": _safe_float(frame["_metric_pnl"].sum()),
            "max_sequential_losses_avg": float(max(_loss_streaks(frame["_metric_loss"].tolist()) or [0])),
            "max_sequential_losses_max": max(_loss_streaks(frame["_metric_loss"].tolist()) or [0]),
        }
    rows: list[dict[str, Any]] = []
    for sample_id, group in frame.groupby(sample_column, dropna=False, sort=False):
        loss_streaks = _loss_streaks(group["_metric_loss"].tolist())
        rows.append(
            {
                "sample_id": str(sample_id),
                "trade_count": int(len(group)),
                "win_rate": _safe_float(group["_metric_win"].mean()),
                "return_sum": _safe_float(group["_metric_pnl"].sum()),
                "return_avg": _safe_float(group["_metric_pnl"].mean()),
                "max_sequential_losses": max(loss_streaks) if loss_streaks else 0,
            }
        )
    sample_frame = pd.DataFrame(rows)
    return {
        "sample_count": int(len(sample_frame)),
        "win_rate_avg": _safe_float(sample_frame["win_rate"].mean()),
        "win_rate_min": _safe_float(sample_frame["win_rate"].min()),
        "win_rate_max": _safe_float(sample_frame["win_rate"].max()),
        "return_avg": _safe_float(sample_frame["return_sum"].mean()),
        "return_min": _safe_float(sample_frame["return_sum"].min()),
        "return_max": _safe_float(sample_frame["return_sum"].max()),
        "max_sequential_losses_avg": _safe_float(sample_frame["max_sequential_losses"].mean()),
        "max_sequential_losses_max": int(sample_frame["max_sequential_losses"].max()),
        "rows": rows[:50],
    }


def _loss_streaks(values: list[bool], *, invert: bool = False) -> list[int]:
    streaks: list[int] = []
    current = 0
    for value in values:
        target = bool(value) if not invert else not bool(value)
        if target:
            current += 1
            continue
        if current:
            streaks.append(current)
        current = 0
    if current:
        streaks.append(current)
    return streaks


def _empty_metrics() -> dict[str, Any]:
    return {
        "trade_count": 0,
        "win_count": 0,
        "loss_count": 0,
        "win_rate": None,
        "return_sum": 0.0,
        "return_avg": None,
        "return_min": None,
        "return_max": None,
        "sample_metrics": {
            "sample_count": 0,
            "win_rate_avg": None,
            "win_rate_min": None,
            "win_rate_max": None,
            "return_avg": None,
            "return_min": None,
            "return_max": None,
            "max_sequential_losses_avg": None,
            "max_sequential_losses_max": 0,
        },
        "max_sequential_losses": 0,
        "avg_sequential_losses": 0.0,
        "max_drawdown": 0.0,
        "profit_factor": None,
        "expectancy": None,
        "blockers": [],
    }


def _first_present(frame: pd.DataFrame, columns: tuple[str, ...]) -> str | None:
    for column in columns:
        if column in frame.columns:
            return column
    return None


def _safe_float(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _profit_factor(wins: pd.Series, losses: pd.Series) -> float | None:
    gross_win = float(wins.sum()) if not wins.empty else 0.0
    gross_loss = abs(float(losses.sum())) if not losses.empty else 0.0
    if gross_loss == 0.0:
        return None if gross_win == 0.0 else float("inf")
    return gross_win / gross_loss


def _payoff_ratio(wins: pd.Series, losses: pd.Series) -> float | None:
    if wins.empty or losses.empty:
        return None
    avg_loss = abs(float(losses.mean()))
    if avg_loss == 0.0:
        return None
    return float(wins.mean()) / avg_loss


__all__ = [
    "compute_trade_metrics",
    "metric_contract",
    "summarize_strategy_metrics",
]
