from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from crypto_options_app.pipelines.options.metrics import summarize_strategy_metrics
from crypto_options_app.pipelines.options.strategies import run_registered_strategy_backtests


def run_directional_prediction_backtest(panel_df: pd.DataFrame) -> dict[str, Any]:
    """Minimal directional replay from exchange indicators.

    The result is research-only and requires settlement labels before it can score real edge.
    """

    required = {"underlying_close", "settlement_threshold", "momentum_3", "observed_at"}
    if panel_df.empty:
        return _blocked("directional_prediction", ["missing_joined_event_state_panel"])
    if missing := sorted(required - set(panel_df.columns)):
        return _blocked("directional_prediction", [f"missing_field:{field}" for field in missing])
    work = panel_df.dropna(subset=["underlying_close", "settlement_threshold"]).copy()
    if work.empty:
        return _blocked("directional_prediction", ["missing_underlying_exchange_candles_or_thresholds"])
    work["directional_signal"] = work["momentum_3"].fillna(0.0).apply(lambda value: "above" if float(value) >= 0 else "below")
    work["current_label"] = work.apply(
        lambda row: "above" if float(row["underlying_close"]) >= float(row["settlement_threshold"]) else "below",
        axis=1,
    )
    scoreable = work.dropna(subset=["resolved_direction"]) if "resolved_direction" in work.columns else pd.DataFrame()
    summary: dict[str, Any] = {
        "candidate_rows": int(len(work)),
        "above_signal_rows": int((work["directional_signal"] == "above").sum()),
        "below_signal_rows": int((work["directional_signal"] == "below").sum()),
        "mean_abs_distance_to_threshold": _mean_abs(work.get("distance_to_threshold")),
    }
    if scoreable.empty:
        return {
            "strategy_family": "directional_prediction",
            "status": "blocked",
            "trade_count": 0,
            "blockers": ["missing_settlement_labels_for_directional_backtest"],
            "summary": summary,
            "trades": [],
            "execution_boundary": "research_backtest_only",
        }
    hits = scoreable["directional_signal"].astype(str) == scoreable["resolved_direction"].astype(str)
    summary["hit_rate"] = float(hits.mean())
    return {
        "strategy_family": "directional_prediction",
        "status": "research_backtest_complete",
        "trade_count": int(len(scoreable)),
        "blockers": [],
        "summary": summary,
        "trades": _sample_records(scoreable, ["event_id", "market_id", "observed_at", "directional_signal", "resolved_direction"]),
        "execution_boundary": "research_backtest_only",
    }


def run_microstructure_scalping_backtest(panel_df: pd.DataFrame, *, min_spread: float = 0.01) -> dict[str, Any]:
    """Proxy spread/fillability replay from Polymarket CLOB observations."""

    required = {"token_id", "observed_at", "best_bid", "best_ask"}
    if panel_df.empty:
        return _blocked("microstructure_scalping", ["missing_polymarket_clob_history"])
    if missing := sorted(required - set(panel_df.columns)):
        return _blocked("microstructure_scalping", [f"missing_field:{field}" for field in missing])
    work = panel_df.dropna(subset=["best_bid", "best_ask"]).copy()
    if work.empty:
        return _blocked("microstructure_scalping", ["missing_bid_ask_depth"])
    work["spread"] = pd.to_numeric(work["best_ask"], errors="coerce") - pd.to_numeric(work["best_bid"], errors="coerce")
    candidates = work[work["spread"] >= float(min_spread)].copy()
    summary = {
        "observation_rows": int(len(work)),
        "candidate_rows": int(len(candidates)),
        "mean_spread": _mean(work["spread"]),
        "median_spread": _median(work["spread"]),
        "min_spread": float(min_spread),
        "queue_model": "not_modelled",
    }
    if candidates.empty:
        return {
            "strategy_family": "microstructure_scalping",
            "status": "no_trades",
            "trade_count": 0,
            "blockers": [],
            "summary": summary,
            "trades": [],
            "execution_boundary": "research_backtest_only",
        }
    summary["gross_spread_capture_proxy"] = float(candidates["spread"].sum())
    return {
        "strategy_family": "microstructure_scalping",
        "status": "research_proxy_complete",
        "trade_count": int(len(candidates)),
        "blockers": ["queue_position_not_modelled", "maker_taker_fees_need_confirmation"],
        "summary": summary,
        "trades": _sample_records(candidates, ["event_id", "market_id", "token_id", "observed_at", "best_bid", "best_ask", "spread"]),
        "execution_boundary": "research_backtest_only",
    }


def run_hybrid_backtest(panel_df: pd.DataFrame) -> dict[str, Any]:
    directional = run_directional_prediction_backtest(panel_df)
    scalping = run_microstructure_scalping_backtest(panel_df)
    blockers = sorted(set((directional.get("blockers") or []) + (scalping.get("blockers") or [])))
    hard_blockers = [blocker for blocker in blockers if blocker not in {"queue_position_not_modelled", "maker_taker_fees_need_confirmation"}]
    if directional.get("status") == "blocked" or scalping.get("status") == "blocked" or hard_blockers:
        return {
            "strategy_family": "hybrid",
            "status": "blocked",
            "trade_count": 0,
            "blockers": hard_blockers or blockers,
            "summary": {
                "directional_status": directional.get("status"),
                "scalping_status": scalping.get("status"),
            },
            "trades": [],
            "execution_boundary": "research_backtest_only",
        }
    return {
        "strategy_family": "hybrid",
        "status": "research_proxy_complete",
        "trade_count": min(int(directional.get("trade_count") or 0), int(scalping.get("trade_count") or 0)),
        "blockers": blockers,
        "summary": {
            "finding": "Directional signal can choose side while CLOB proxy chooses entry/exit, but settlement labels and queue modelling remain required.",
            "directional": directional.get("summary"),
            "scalping": scalping.get("summary"),
        },
        "trades": [],
        "execution_boundary": "research_backtest_only",
    }


def run_no_trade_baseline(panel_df: pd.DataFrame) -> dict[str, Any]:
    return {
        "strategy_family": "no_trade_baseline",
        "status": "baseline_complete",
        "trade_count": 0,
        "blockers": [],
        "summary": {
            "pnl": 0.0,
            "observation_rows": int(len(panel_df)),
            "purpose": "Comparator for prediction/scalping/hybrid behavior.",
        },
        "trades": [],
        "execution_boundary": "research_backtest_only",
    }


def compare_crypto_options_strategies(panel_df: pd.DataFrame) -> dict[str, Any]:
    results = [
        run_directional_prediction_backtest(panel_df),
        run_microstructure_scalping_backtest(panel_df),
        run_hybrid_backtest(panel_df),
        run_no_trade_baseline(panel_df),
    ]
    registered_results = run_registered_strategy_backtests(panel_df)
    all_results = results + registered_results
    blockers = sorted({blocker for result in all_results for blocker in (result.get("blockers") or [])})
    if any(result.get("status") in {"research_backtest_complete", "research_proxy_complete"} for result in all_results):
        status = "comparison_partial"
    elif blockers:
        status = "blocked"
    else:
        status = "baseline_only"
    return {
        "schema_version": "crypto_options_strategy_comparison_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "results": all_results,
        "metric_summary": summarize_strategy_metrics(all_results),
        "blockers": blockers,
        "initial_conclusion": _initial_conclusion(all_results),
        "live_trading_authorized": False,
    }


def _initial_conclusion(results: list[dict[str, Any]]) -> str:
    statuses = {result["strategy_family"]: result["status"] for result in results}
    if statuses.get("hybrid") == "research_proxy_complete":
        return "Hybrid is the preferred research family once exchange candles, CLOB depth, and settlement labels are all present."
    if statuses.get("microstructure_scalping") == "research_proxy_complete":
        return "Scalping can be studied from Polymarket CLOB data first, but queue/fillability modelling is still a blocker for promotion."
    if statuses.get("directional_prediction") == "research_backtest_complete":
        return "Directional prediction can be studied once exchange candles and settlement labels are available."
    return "No strategy has enough joined data yet; preserve no-trade baseline and fill data gaps first."


def _blocked(strategy_family: str, blockers: list[str]) -> dict[str, Any]:
    return {
        "strategy_family": strategy_family,
        "status": "blocked",
        "trade_count": 0,
        "blockers": blockers,
        "summary": {},
        "trades": [],
        "execution_boundary": "research_backtest_only",
    }


def _sample_records(frame: pd.DataFrame, columns: list[str], *, limit: int = 25) -> list[dict[str, Any]]:
    selected = [column for column in columns if column in frame.columns]
    return frame[selected].head(limit).to_dict(orient="records") if selected else []


def _mean(series: pd.Series) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.mean()) if not values.empty else None


def _median(series: pd.Series) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.median()) if not values.empty else None


def _mean_abs(series: Any) -> float | None:
    if series is None:
        return None
    values = pd.to_numeric(series, errors="coerce").dropna().abs()
    return float(values.mean()) if not values.empty else None


__all__ = [
    "compare_crypto_options_strategies",
    "run_directional_prediction_backtest",
    "run_hybrid_backtest",
    "run_microstructure_scalping_backtest",
    "run_no_trade_baseline",
]
