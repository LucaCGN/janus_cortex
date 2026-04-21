from __future__ import annotations

import bisect
import json
import math
from typing import Any

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import BACKTEST_TRADE_COLUMNS


MASTER_ROUTER_PORTFOLIO = "master_strategy_router_v1"
DEFAULT_MASTER_ROUTER_SELECTION_SAMPLE = "time_train"
DEFAULT_MASTER_ROUTER_CORE_FAMILIES = (
    "winner_definition",
    "inversion",
    "underdog_liftoff",
    "favorite_panic_fade_v1",
)
DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES = (
    "q1_repricing",
    "halftime_q3_repricing_v1",
    "q4_clutch",
)

MASTER_ROUTER_DECISION_COLUMNS = (
    "sample_name",
    "selection_sample_name",
    "game_id",
    "game_date",
    "opening_band",
    "selected_core_family",
    "selected_team_side",
    "selected_team_slug",
    "selected_opponent_team_slug",
    "selected_entry_period_label",
    "selected_entry_context_bucket",
    "selected_entry_at",
    "selected_confidence",
    "selected_signal_strength",
    "selected_confidence_components_json",
    "triggered_core_family_count",
    "triggered_core_families_json",
    "triggered_extra_family_count",
    "triggered_extra_families_json",
)
MASTER_ROUTER_TRADE_COLUMNS = (
    *BACKTEST_TRADE_COLUMNS,
    "source_strategy_family",
    "master_router_role",
    "master_router_confidence",
)


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _serialize_scalar(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _clean_trades_df(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty:
        return pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS)
    work = trades_df.copy()
    for column in ("entry_at", "exit_at"):
        if column in work.columns:
            work[column] = pd.to_datetime(work[column], errors="coerce", utc=True)
    for column in ("signal_strength", "gross_return_with_slippage"):
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    return work


def _build_context_lookup(trades_df: pd.DataFrame) -> tuple[dict[tuple[str, str, str], dict[str, Any]], dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    if trades_df.empty:
        return {}, {}, {"trade_count": 0, "win_rate": None, "avg_return": None}

    exact = (
        trades_df.groupby(["opening_band", "period_label", "context_bucket"], dropna=False)
        .agg(
            trade_count=("game_id", "count"),
            win_rate=("gross_return_with_slippage", lambda values: float((pd.Series(values) > 0).mean())),
            avg_return=("gross_return_with_slippage", "mean"),
        )
        .reset_index()
    )
    band_period = (
        trades_df.groupby(["opening_band", "period_label"], dropna=False)
        .agg(
            trade_count=("game_id", "count"),
            win_rate=("gross_return_with_slippage", lambda values: float((pd.Series(values) > 0).mean())),
            avg_return=("gross_return_with_slippage", "mean"),
        )
        .reset_index()
    )
    exact_lookup = {
        (str(row["opening_band"]), str(row["period_label"]), str(row["context_bucket"])): {
            "trade_count": int(row["trade_count"]),
            "win_rate": _safe_float(row["win_rate"]),
            "avg_return": _safe_float(row["avg_return"]),
        }
        for row in exact.to_dict(orient="records")
    }
    band_period_lookup = {
        (str(row["opening_band"]), str(row["period_label"])): {
            "trade_count": int(row["trade_count"]),
            "win_rate": _safe_float(row["win_rate"]),
            "avg_return": _safe_float(row["avg_return"]),
        }
        for row in band_period.to_dict(orient="records")
    }
    overall = {
        "trade_count": int(len(trades_df)),
        "win_rate": float((trades_df["gross_return_with_slippage"] > 0).mean()),
        "avg_return": float(trades_df["gross_return_with_slippage"].mean()),
    }
    return exact_lookup, band_period_lookup, overall


def build_master_router_selection_priors(
    selection_result: Any | None,
    *,
    core_strategy_families: tuple[str, ...] | list[str],
) -> dict[str, dict[str, Any]]:
    if selection_result is None:
        return {}

    priors: dict[str, dict[str, Any]] = {}
    for family in core_strategy_families:
        trades_df = selection_result.trade_frames.get(family, pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS))
        work = _clean_trades_df(trades_df)
        if work.empty:
            continue
        signals = sorted(
            float(value)
            for value in work["signal_strength"].tolist()
            if value is not None and not pd.isna(value)
        )
        exact_lookup, band_period_lookup, overall = _build_context_lookup(work)
        priors[str(family)] = {
            "signal_strengths": signals,
            "exact_context_lookup": exact_lookup,
            "band_period_lookup": band_period_lookup,
            "overall": overall,
        }
    return priors


def _signal_percentile(signal_strengths: list[float], value: float | None) -> float:
    if not signal_strengths or value is None:
        return 0.5
    index = bisect.bisect_right(signal_strengths, float(value))
    return max(0.0, min(1.0, index / len(signal_strengths)))


def _resolve_context_stats(prior: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    opening_band = str(record.get("opening_band") or "")
    period_label = str(record.get("period_label") or "")
    context_bucket = str(record.get("context_bucket") or "")
    exact_lookup = prior.get("exact_context_lookup") or {}
    band_period_lookup = prior.get("band_period_lookup") or {}
    return (
        exact_lookup.get((opening_band, period_label, context_bucket))
        or band_period_lookup.get((opening_band, period_label))
        or (prior.get("overall") or {"trade_count": 0, "win_rate": None, "avg_return": None})
    )


def score_master_router_candidate(
    record: dict[str, Any],
    *,
    family: str,
    priors: dict[str, dict[str, Any]],
) -> tuple[float, dict[str, float | int | None]]:
    prior = priors.get(family) or {}
    context_stats = _resolve_context_stats(prior, record)
    signal_percentile = _signal_percentile(prior.get("signal_strengths") or [], _safe_float(record.get("signal_strength")))
    avg_return = _safe_float(context_stats.get("avg_return")) or 0.0
    win_rate = _safe_float(context_stats.get("win_rate"))
    trade_count = int(context_stats.get("trade_count") or 0)

    edge_score = max(0.0, min(1.0, (avg_return + 0.10) / 0.30))
    win_rate_score = max(0.0, min(1.0, win_rate if win_rate is not None else 0.5))
    support_score = max(0.0, min(1.0, math.log1p(trade_count) / math.log(16.0))) if trade_count > 0 else 0.0
    confidence = ((0.50 * signal_percentile) + (0.35 * edge_score) + (0.15 * win_rate_score)) * (0.75 + (0.25 * support_score))
    confidence = max(0.0, min(1.0, confidence))
    components = {
        "signal_percentile": round(signal_percentile, 6),
        "edge_score": round(edge_score, 6),
        "win_rate_score": round(win_rate_score, 6),
        "support_score": round(support_score, 6),
        "context_avg_return": round(avg_return, 6),
        "context_win_rate": round(win_rate, 6) if win_rate is not None else None,
        "context_trade_count": trade_count,
    }
    return confidence, components


def _decorate_core_candidate(
    record: dict[str, Any],
    *,
    confidence: float,
    source_family: str,
) -> dict[str, Any]:
    decorated = dict(record)
    original_signal = _safe_float(record.get("signal_strength")) or 0.0
    # Keep the core route above extra sleeves when timestamps collide without using future-return leakage.
    decorated["signal_strength"] = (1000.0 + (confidence * 100.0) + min(original_signal, 99.0))
    decorated["source_strategy_family"] = source_family
    decorated["master_router_role"] = "core_selected"
    decorated["master_router_confidence"] = confidence
    return decorated


def _decorate_extra_candidate(record: dict[str, Any], *, source_family: str) -> dict[str, Any]:
    decorated = dict(record)
    original_signal = _safe_float(record.get("signal_strength")) or 0.0
    decorated["signal_strength"] = 500.0 + min(original_signal, 99.0)
    decorated["source_strategy_family"] = source_family
    decorated["master_router_role"] = "extra_sleeve"
    decorated["master_router_confidence"] = None
    return decorated


def build_master_router_trade_frame(
    split_result: Any,
    *,
    sample_name: str,
    selection_sample_name: str,
    priors: dict[str, dict[str, Any]],
    core_strategy_families: tuple[str, ...] | list[str],
    extra_strategy_families: tuple[str, ...] | list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    core_frames: list[pd.DataFrame] = []
    extra_frames: list[pd.DataFrame] = []
    for family in core_strategy_families:
        trades_df = split_result.trade_frames.get(family, pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS))
        work = _clean_trades_df(trades_df)
        if work.empty:
            continue
        work["source_strategy_family"] = str(family)
        core_frames.append(work)
    for family in extra_strategy_families:
        trades_df = split_result.trade_frames.get(family, pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS))
        work = _clean_trades_df(trades_df)
        if work.empty:
            continue
        work["source_strategy_family"] = str(family)
        extra_frames.append(work)

    decision_rows: list[dict[str, Any]] = []
    selected_core_records: list[dict[str, Any]] = []
    combined_core = pd.concat(core_frames, ignore_index=True) if core_frames else pd.DataFrame(columns=[*BACKTEST_TRADE_COLUMNS, "source_strategy_family"])
    combined_extra = pd.concat(extra_frames, ignore_index=True) if extra_frames else pd.DataFrame(columns=[*BACKTEST_TRADE_COLUMNS, "source_strategy_family"])
    if not combined_core.empty:
        combined_core["entry_at"] = pd.to_datetime(combined_core["entry_at"], errors="coerce", utc=True)
        combined_core["game_date"] = pd.to_datetime(combined_core["entry_at"], errors="coerce", utc=True).dt.date
        for game_id, game_df in combined_core.groupby("game_id", sort=True):
            candidates: list[dict[str, Any]] = []
            for record in game_df.to_dict(orient="records"):
                source_family = str(record.get("source_strategy_family") or "")
                confidence, components = score_master_router_candidate(record, family=source_family, priors=priors)
                record["master_router_confidence"] = confidence
                record["master_router_confidence_components_json"] = components
                candidates.append(record)
            ranked = sorted(
                candidates,
                key=lambda item: (
                    _safe_float(item.get("master_router_confidence")) is not None,
                    _safe_float(item.get("master_router_confidence")) or float("-inf"),
                    _safe_float(item.get("signal_strength")) or float("-inf"),
                    str(item.get("source_strategy_family") or ""),
                    str(item.get("team_side") or ""),
                ),
                reverse=True,
            )
            best = ranked[0]
            selected_core_records.append(
                _decorate_core_candidate(
                    best,
                    confidence=float(best["master_router_confidence"]),
                    source_family=str(best["source_strategy_family"]),
                )
            )
            extra_triggered_families = sorted(
                combined_extra[combined_extra["game_id"].astype(str) == str(game_id)]["source_strategy_family"].dropna().astype(str).unique().tolist()
            ) if not combined_extra.empty else []
            decision_rows.append(
                {
                    "sample_name": sample_name,
                    "selection_sample_name": selection_sample_name,
                    "game_id": game_id,
                    "game_date": _serialize_scalar(best.get("game_date")),
                    "opening_band": best.get("opening_band"),
                    "selected_core_family": best.get("source_strategy_family"),
                    "selected_team_side": best.get("team_side"),
                    "selected_team_slug": best.get("team_slug"),
                    "selected_opponent_team_slug": best.get("opponent_team_slug"),
                    "selected_entry_period_label": best.get("period_label"),
                    "selected_entry_context_bucket": best.get("context_bucket"),
                    "selected_entry_at": _serialize_scalar(best.get("entry_at")),
                    "selected_confidence": best.get("master_router_confidence"),
                    "selected_signal_strength": _safe_float(best.get("signal_strength")),
                    "selected_confidence_components_json": json.dumps(best.get("master_router_confidence_components_json") or {}, sort_keys=True),
                    "triggered_core_family_count": len({str(item.get("source_strategy_family") or "") for item in ranked}),
                    "triggered_core_families_json": json.dumps(
                        sorted({str(item.get("source_strategy_family") or "") for item in ranked}),
                        sort_keys=True,
                    ),
                    "triggered_extra_family_count": len(extra_triggered_families),
                    "triggered_extra_families_json": json.dumps(extra_triggered_families, sort_keys=True),
                }
            )

    selected_core_df = pd.DataFrame(selected_core_records)
    selected_core_df = selected_core_df if not selected_core_df.empty else pd.DataFrame(columns=MASTER_ROUTER_TRADE_COLUMNS)
    decorated_extra_df = (
        pd.DataFrame(
            [_decorate_extra_candidate(record, source_family=str(record.get("source_strategy_family") or "")) for record in combined_extra.to_dict(orient="records")]
        )
        if not combined_extra.empty
        else pd.DataFrame(columns=MASTER_ROUTER_TRADE_COLUMNS)
    )
    combined_trade_records = [
        {column: record.get(column) for column in MASTER_ROUTER_TRADE_COLUMNS}
        for record in selected_core_df.to_dict(orient="records")
    ]
    combined_trade_records.extend(
        {column: record.get(column) for column in MASTER_ROUTER_TRADE_COLUMNS}
        for record in decorated_extra_df.to_dict(orient="records")
    )
    combined_trades_df = pd.DataFrame(combined_trade_records, columns=MASTER_ROUTER_TRADE_COLUMNS)
    decision_df = pd.DataFrame(decision_rows, columns=MASTER_ROUTER_DECISION_COLUMNS)
    return combined_trades_df, decision_df


__all__ = [
    "DEFAULT_MASTER_ROUTER_CORE_FAMILIES",
    "DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES",
    "DEFAULT_MASTER_ROUTER_SELECTION_SAMPLE",
    "MASTER_ROUTER_DECISION_COLUMNS",
    "MASTER_ROUTER_PORTFOLIO",
    "build_master_router_selection_priors",
    "build_master_router_trade_frame",
    "score_master_router_candidate",
]
