from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from crypto_options_app.pipelines.options.metrics import compute_trade_metrics
from crypto_options_app.pipelines.options.reporting import strict_jsonable
from crypto_options_app.pipelines.options.strategies import TAKER_FEE_RATE


STATISTICAL_SIGNAL_SCHEMA_VERSION = "crypto_options_statistical_signal_v1"
STATISTICAL_BACKTEST_SCHEMA_VERSION = "crypto_options_statistical_backtest_v1"
STATISTICAL_COMPONENT_IDS = (
    "stat_vwap_ema_trend_confluence",
    "stat_volume_profile_acceptance_rejection",
    "stat_support_resistance_bounce_decay",
    "stat_false_breakout_reversal_filter",
    "stat_volatility_regime_filter",
    "stat_settlement_threshold_distance_model",
)

StatisticalComponentRunner = Callable[[pd.DataFrame], list[dict[str, Any]]]


def build_statistical_feature_frame(panel_df: pd.DataFrame) -> pd.DataFrame:
    """Normalize joined panel fields for deterministic statistical signal replays."""

    frame = panel_df.copy()
    if frame.empty:
        return frame
    for column in ("observed_at", "window_start_time", "window_end_time"):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    for column in (
        "best_bid",
        "best_ask",
        "polymarket_mid_price",
        "spread",
        "underlying_close",
        "settlement_threshold",
        "distance_to_threshold",
        "time_to_close_seconds",
        "reference_start_price",
        "reference_end_price",
        "return_1",
        "momentum_3",
        "volatility",
        "ma_fast",
        "ma_slow",
        "rsi",
    ):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "time_to_close_seconds" not in frame.columns and {"observed_at", "window_end_time"}.issubset(frame.columns):
        frame["time_to_close_seconds"] = (frame["window_end_time"] - frame["observed_at"]).dt.total_seconds()
    if "distance_to_threshold" not in frame.columns and {"underlying_close", "settlement_threshold"}.issubset(frame.columns):
        frame["distance_to_threshold"] = frame["underlying_close"] - frame["settlement_threshold"]
    frame["_normalized_outcome"] = frame.get("outcome", pd.Series(index=frame.index, dtype=object)).apply(_normalize_side)
    frame["_normalized_resolved_direction"] = frame.get("resolved_direction", pd.Series(index=frame.index, dtype=object)).apply(_normalize_side)
    return frame.sort_values(["observed_at", "market_id", "token_id"], kind="mergesort").reset_index(drop=True)


def evaluate_statistical_data_quality(frame: pd.DataFrame, *, allow_midpoint_entries: bool = False) -> dict[str, Any]:
    blockers: list[str] = []
    if frame.empty:
        blockers.append("missing_joined_event_state_panel")
    required = ("event_id", "market_id", "token_id", "observed_at", "outcome")
    blockers.extend(f"missing_field:{field}" for field in required if field not in frame.columns)
    if "resolved_direction" not in frame.columns or not frame["resolved_direction"].notna().any():
        blockers.append("missing_settlement_labels")
    candle_fields = ("underlying_close", "distance_to_threshold", "momentum_3", "volatility")
    if not any(field in frame.columns and frame[field].notna().any() for field in candle_fields):
        blockers.append("missing_exchange_candle_features")
    if not allow_midpoint_entries and ("best_ask" not in frame.columns or not frame["best_ask"].notna().any()):
        blockers.append("missing_executable_best_ask")
    if not allow_midpoint_entries and ("best_bid" not in frame.columns or not frame["best_bid"].notna().any()):
        blockers.append("missing_executable_best_bid")
    if allow_midpoint_entries and not _has_entry_prices(frame, allow_midpoint_entries=True):
        blockers.append("missing_executable_or_midpoint_entry_prices")
    return strict_jsonable(
        {
            "row_count": int(len(frame)),
            "event_count": _nunique(frame, "event_id"),
            "market_count": _nunique(frame, "market_id"),
            "token_count": _nunique(frame, "token_id"),
            "rows_with_labels": _notna_count(frame, "resolved_direction"),
            "rows_with_bid_ask": _bid_ask_count(frame),
            "rows_with_exchange_features": _exchange_feature_count(frame),
            "allow_midpoint_entries": bool(allow_midpoint_entries),
            "blockers": sorted(set(blockers)),
        }
    )


def assign_chronological_splits(
    frame: pd.DataFrame,
    *,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
) -> pd.DataFrame:
    work = frame.copy()
    if work.empty:
        work["split"] = pd.Series(dtype=object)
        return work
    sample_column = "event_id" if "event_id" in work.columns else "market_id"
    sample_times = (
        work.groupby(sample_column, dropna=False, sort=False)["observed_at"].min().reset_index().sort_values("observed_at", kind="mergesort").reset_index(drop=True)
    )
    sample_times["_rank"] = range(len(sample_times))
    total = max(len(sample_times), 1)
    train_cut = int(total * train_fraction)
    validation_cut = int(total * (train_fraction + validation_fraction))
    if total >= 3:
        train_cut = max(1, min(train_cut, total - 2))
        validation_cut = max(train_cut + 1, min(validation_cut, total - 1))
    else:
        train_cut = max(1, min(train_cut, total))
        validation_cut = max(train_cut, min(validation_cut, total))

    def split_for_rank(rank: int) -> str:
        if rank < train_cut:
            return "train"
        if rank < validation_cut:
            return "validation"
        return "holdout"

    sample_times["split"] = sample_times["_rank"].apply(split_for_rank)
    return work.merge(sample_times[[sample_column, "split"]], on=sample_column, how="left")


def make_statistical_signal(
    component_id: str,
    row: pd.Series | dict[str, Any],
    *,
    side: str,
    confidence: float,
    feature_values: dict[str, Any] | None = None,
    blockers: list[str] | None = None,
    required_data_quality: list[str] | None = None,
    entry_window: dict[str, Any] | None = None,
    replay_timestamp: Any | None = None,
) -> dict[str, Any]:
    source = row.to_dict() if isinstance(row, pd.Series) else dict(row)
    source_index = row.name if isinstance(row, pd.Series) else source.get("source_row_index")
    observed_at = replay_timestamp or source.get("observed_at")
    return strict_jsonable(
        {
            "schema_version": STATISTICAL_SIGNAL_SCHEMA_VERSION,
            "component_id": component_id,
            "symbol": source.get("primary_symbol") or source.get("symbol"),
            "event_id": source.get("event_id"),
            "market_id": source.get("market_id"),
            "token_id": source.get("token_id"),
            "side": _normalize_side(side),
            "confidence": max(0.0, min(float(confidence), 1.0)),
            "feature_values": feature_values or {},
            "entry_window": entry_window or _entry_window(source),
            "required_data_quality": required_data_quality or [],
            "blockers": blockers or [],
            "replay_timestamp_utc": _iso(observed_at),
            "source_row_index": int(source_index) if isinstance(source_index, int) else source_index,
            "entry_price": _entry_price(source, allow_midpoint_entries=True),
            "resolved_direction": source.get("resolved_direction"),
            "split": source.get("split"),
        }
    )


def run_statistical_signal_backtest(
    panel_df: pd.DataFrame,
    *,
    component_runners: dict[str, StatisticalComponentRunner] | None = None,
    component_ids: tuple[str, ...] = STATISTICAL_COMPONENT_IDS,
    fee_rate: float = TAKER_FEE_RATE,
    allow_midpoint_entries: bool = False,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    feature_frame = assign_chronological_splits(build_statistical_feature_frame(panel_df))
    data_quality = evaluate_statistical_data_quality(feature_frame, allow_midpoint_entries=allow_midpoint_entries)
    global_blockers = data_quality["blockers"]
    runners = component_runners or {}
    results = [
        _run_component(
            component_id,
            feature_frame,
            runners.get(component_id),
            global_blockers=global_blockers,
            fee_rate=fee_rate,
            allow_midpoint_entries=allow_midpoint_entries,
        )
        for component_id in component_ids
    ]
    return strict_jsonable(
        {
            "schema_version": STATISTICAL_BACKTEST_SCHEMA_VERSION,
            "generated_at_utc": generated_at.astimezone(timezone.utc).isoformat(),
            "status": _report_status(results, global_blockers),
            "execution_boundary": "research_backtest_only",
            "live_trading_authorized": False,
            "component_ids": list(component_ids),
            "data_quality": data_quality,
            "split_summary": _split_summary(feature_frame),
            "results": results,
            "safety_boundary": {
                "orders_allowed": False,
                "live_trading_authorized": False,
                "scope": "deterministic_statistical_signal_backtest_only",
            },
        }
    )


def _run_component(
    component_id: str,
    frame: pd.DataFrame,
    runner: StatisticalComponentRunner | None,
    *,
    global_blockers: list[str],
    fee_rate: float,
    allow_midpoint_entries: bool,
) -> dict[str, Any]:
    if global_blockers:
        return _component_result(component_id, [], [], global_blockers)
    if runner is None:
        return _component_result(component_id, [], [], ["component_not_implemented"])
    signals = [signal for signal in runner(frame.copy())]
    signal_blockers = sorted({blocker for signal in signals for blocker in (signal.get("blockers") or [])})
    trades: list[dict[str, Any]] = []
    trade_blockers: list[str] = []
    for signal in signals:
        if signal.get("blockers"):
            continue
        trade, blockers = _trade_from_signal(signal, frame, fee_rate=fee_rate, allow_midpoint_entries=allow_midpoint_entries)
        trade_blockers.extend(blockers)
        if trade is not None:
            trades.append(trade)
    blockers = sorted(set(signal_blockers + trade_blockers))
    return _component_result(component_id, signals, trades, blockers)


def _component_result(component_id: str, signals: list[dict[str, Any]], trades: list[dict[str, Any]], blockers: list[str]) -> dict[str, Any]:
    metrics = compute_trade_metrics(pd.DataFrame(trades), sample_column="sample_id")
    all_blockers = sorted(set(blockers + (metrics.get("blockers") or [])))
    status = "blocked" if all_blockers and not trades else ("research_backtest_complete" if trades else "no_signals")
    return strict_jsonable(
        {
            "component_id": component_id,
            "status": status,
            "signal_count": len(signals),
            "blocked_signal_count": sum(1 for signal in signals if signal.get("blockers")),
            "trade_count": len(trades),
            "blockers": all_blockers,
            "metrics": metrics,
            "metrics_by_split": _metrics_by_split(trades),
            "metrics_by_group": {
                "symbol": _metrics_by_group(trades, "symbol"),
                "time_in_event_bucket": _metrics_by_group(trades, "time_in_event_bucket"),
                "entry_price_bucket": _metrics_by_group(trades, "entry_price_bucket"),
                "volatility_regime": _metrics_by_group(trades, "volatility_regime"),
            },
            "comparison": {
                "no_trade_baseline_pnl": 0.0,
                "return_sum_vs_no_trade": metrics.get("return_sum"),
            },
            "signals": signals[:25],
            "trades": trades[:25],
            "execution_boundary": "research_backtest_only",
            "live_trading_authorized": False,
        }
    )


def _trade_from_signal(
    signal: dict[str, Any],
    frame: pd.DataFrame,
    *,
    fee_rate: float,
    allow_midpoint_entries: bool,
) -> tuple[dict[str, Any] | None, list[str]]:
    row = _locate_signal_row(signal, frame)
    if row is None:
        return None, ["signal_row_not_found"]
    entry = _entry_price(row, allow_midpoint_entries=allow_midpoint_entries)
    if entry is None:
        return None, ["missing_entry_price_for_signal"]
    side = _normalize_side(signal.get("side"))
    resolved = _normalize_side(row.get("resolved_direction") or signal.get("resolved_direction"))
    if resolved not in {"up", "down"}:
        return None, ["missing_settlement_label_for_signal"]
    win = side == resolved
    fees = _fee(entry, fee_rate)
    pnl = (1.0 - entry - fees) if win else (0.0 - entry - fees)
    return (
        strict_jsonable(
            {
                "component_id": signal.get("component_id"),
                "event_id": row.get("event_id"),
                "market_id": row.get("market_id"),
                "token_id": row.get("token_id"),
                "observed_at": row.get("observed_at"),
                "sample_id": row.get("event_id") or row.get("market_id"),
                "symbol": row.get("primary_symbol") or row.get("symbol"),
                "side": side,
                "confidence": signal.get("confidence"),
                "entry_price": entry,
                "entry_price_bucket": _entry_price_bucket(entry),
                "time_in_event_bucket": _time_in_event_bucket(row),
                "volatility_regime": _volatility_regime(row),
                "exit_price": 1.0 if win else 0.0,
                "fees": fees,
                "pnl_net": pnl,
                "return_net": pnl,
                "win": win,
                "split": row.get("split"),
                "execution_model": "midpoint_theoretical" if pd.isna(row.get("best_ask")) else "executable_bid_ask",
            }
        ),
        [],
    )


def _locate_signal_row(signal: dict[str, Any], frame: pd.DataFrame) -> pd.Series | None:
    source_index = signal.get("source_row_index")
    if isinstance(source_index, int) and source_index in frame.index:
        row = frame.loc[source_index]
        if _normalize_side(row.get("outcome")) == _normalize_side(signal.get("side")):
            return row
    subset = frame.copy()
    for column in ("event_id", "market_id"):
        value = signal.get(column)
        if value is not None and column in subset.columns:
            subset = subset[subset[column].astype(str) == str(value)]
    if "outcome" in subset.columns:
        subset = subset[subset["outcome"].apply(_normalize_side) == _normalize_side(signal.get("side"))]
    if subset.empty:
        return None
    return subset.sort_values("observed_at", kind="mergesort").iloc[0]


def _metrics_by_split(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {}
    rows: dict[str, Any] = {}
    frame = pd.DataFrame(trades)
    for split, group in frame.groupby("split", dropna=False, sort=False):
        rows[str(split)] = compute_trade_metrics(group, sample_column="sample_id")
    return rows


def _metrics_by_group(trades: list[dict[str, Any]], group_column: str) -> dict[str, Any]:
    if not trades:
        return {}
    frame = pd.DataFrame(trades)
    if group_column not in frame.columns:
        return {}
    rows: dict[str, Any] = {}
    for key, group in frame.groupby(group_column, dropna=False, sort=False):
        rows[str(key)] = compute_trade_metrics(group, sample_column="sample_id")
    return rows


def _report_status(results: list[dict[str, Any]], blockers: list[str]) -> str:
    if blockers and all(result.get("status") == "blocked" for result in results):
        return "blocked"
    if any(result.get("trade_count") for result in results):
        return "statistical_backtest_complete"
    if any(result.get("status") == "blocked" for result in results):
        return "partial_or_blocked"
    return "no_signals"


def _split_summary(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty or "split" not in frame.columns:
        return {"train": 0, "validation": 0, "holdout": 0}
    counts = frame["split"].value_counts(dropna=False).to_dict()
    return {split: int(counts.get(split, 0)) for split in ("train", "validation", "holdout")}


def _entry_window(source: dict[str, Any]) -> dict[str, Any]:
    return strict_jsonable(
        {
            "window_start_time": _iso(source.get("window_start_time")),
            "window_end_time": _iso(source.get("window_end_time")),
            "time_to_close_seconds": _to_float(source.get("time_to_close_seconds")),
        }
    )


def _entry_price(row: pd.Series | dict[str, Any], *, allow_midpoint_entries: bool) -> float | None:
    getter = row.get
    price = _to_float(getter("best_ask"))
    if price is not None:
        return price
    if allow_midpoint_entries:
        return _to_float(getter("polymarket_mid_price"))
    return None


def _entry_price_bucket(entry_price: float | None) -> str | None:
    price = _to_float(entry_price)
    if price is None:
        return None
    buckets = ((0.05, 0.10), (0.10, 0.25), (0.25, 0.40), (0.40, 0.55), (0.55, 0.70), (0.70, 0.90))
    for low, high in buckets:
        if low <= price < high:
            return f"{low:.2f}-{high:.2f}"
    return "out_of_policy_range"


def _time_in_event_bucket(row: pd.Series) -> str | None:
    elapsed = None
    if pd.notna(row.get("observed_at")) and pd.notna(row.get("window_start_time")):
        elapsed = (row.get("observed_at") - row.get("window_start_time")).total_seconds()
    elif pd.notna(row.get("time_to_close_seconds")):
        cadence = _to_float(row.get("cadence_seconds")) or 300.0
        elapsed = cadence - (_to_float(row.get("time_to_close_seconds")) or 0.0)
    if elapsed is None:
        return None
    if elapsed < 60:
        return "0-60s"
    if elapsed < 180:
        return "60-180s"
    return "180-300s"


def _volatility_regime(row: pd.Series) -> str:
    volatility = _to_float(row.get("volatility"))
    if volatility is None:
        return "unknown"
    if volatility < 0.0005:
        return "low"
    if volatility < 0.0015:
        return "medium"
    return "high"


def _has_entry_prices(frame: pd.DataFrame, *, allow_midpoint_entries: bool) -> bool:
    if "best_ask" in frame.columns and frame["best_ask"].notna().any():
        return True
    return bool(allow_midpoint_entries and "polymarket_mid_price" in frame.columns and frame["polymarket_mid_price"].notna().any())


def _exchange_feature_count(frame: pd.DataFrame) -> int:
    fields = [field for field in ("underlying_close", "distance_to_threshold", "momentum_3", "volatility") if field in frame.columns]
    if not fields:
        return 0
    return int(frame[fields].notna().any(axis=1).sum())


def _bid_ask_count(frame: pd.DataFrame) -> int:
    if not {"best_bid", "best_ask"}.issubset(frame.columns):
        return 0
    return int((frame["best_bid"].notna() & frame["best_ask"].notna()).sum())


def _notna_count(frame: pd.DataFrame, column: str) -> int:
    return int(frame[column].notna().sum()) if column in frame.columns else 0


def _nunique(frame: pd.DataFrame, column: str) -> int:
    return int(frame[column].nunique(dropna=True)) if column in frame.columns else 0


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


def _iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, pd.Timestamp):
        return value.tz_convert("UTC").isoformat() if value.tzinfo else value.tz_localize("UTC").isoformat()
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat() if value.tzinfo else value.replace(tzinfo=timezone.utc).isoformat()
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return str(value)
    return parsed.isoformat()


__all__ = [
    "STATISTICAL_BACKTEST_SCHEMA_VERSION",
    "STATISTICAL_COMPONENT_IDS",
    "STATISTICAL_SIGNAL_SCHEMA_VERSION",
    "assign_chronological_splits",
    "build_statistical_feature_frame",
    "evaluate_statistical_data_quality",
    "make_statistical_signal",
    "run_statistical_signal_backtest",
]
