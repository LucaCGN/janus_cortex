from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.api.db import to_jsonable
from app.data.pipelines.daily.nba.analysis.artifacts import write_frame, write_json, write_markdown
from app.data.pipelines.daily.nba.analysis.backtests.engine import (
    BACKTEST_TRADE_COLUMNS,
    build_backtest_result,
    write_backtest_artifacts,
)
from app.data.pipelines.daily.nba.analysis.backtests.registry import resolve_strategy_registry
from app.data.pipelines.daily.nba.analysis.backtests.specs import BacktestResult, BenchmarkRunResult, StrategyDefinition
from app.data.pipelines.daily.nba.analysis.contracts import BACKTEST_BENCHMARK_CONTRACT_VERSION, BacktestRunRequest
from app.data.pipelines.daily.nba.analysis.models.features import resolve_train_cutoff, split_frame_by_cutoff


BENCHMARK_SPLIT_SUMMARY_COLUMNS = (
    "sample_name",
    "split_kind",
    "selection_rule",
    "state_rows_considered",
    "games_considered",
    "game_sides_considered",
    "min_game_date",
    "max_game_date",
)

BENCHMARK_FAMILY_SUMMARY_COLUMNS = (
    "sample_name",
    "strategy_family",
    "entry_rule",
    "exit_rule",
    "description",
    "comparator_group",
    "trade_count",
    "meets_min_trade_count_flag",
    "win_rate",
    "avg_gross_return",
    "median_gross_return",
    "avg_gross_return_with_slippage",
    "avg_hold_time_seconds",
    "avg_mfe_after_entry",
    "avg_mae_after_entry",
    "delta_vs_no_trade_avg_gross_return_with_slippage",
    "delta_vs_winner_prediction_hold_to_end_avg_gross_return_with_slippage",
)

BENCHMARK_COMPARATOR_COLUMNS = (
    "sample_name",
    "strategy_family",
    "comparator_name",
    "trade_count",
    "win_rate",
    "avg_gross_return",
    "median_gross_return",
    "avg_gross_return_with_slippage",
    "avg_hold_time_seconds",
    "avg_mfe_after_entry",
    "avg_mae_after_entry",
)

BENCHMARK_SAMPLE_VS_FULL_COLUMNS = (
    "sample_name",
    "strategy_family",
    "trade_count_delta_vs_full",
    "win_rate_delta_vs_full",
    "avg_gross_return_with_slippage_delta_vs_full",
    "avg_hold_time_seconds_delta_vs_full",
)

BENCHMARK_CONTEXT_RANK_COLUMNS = (
    "sample_name",
    "strategy_family",
    "ranking_side",
    "rank",
    "period_label",
    "opening_band",
    "context_bucket",
    "trade_count",
    "win_rate",
    "avg_gross_return_with_slippage",
    "avg_hold_time_seconds",
)

BENCHMARK_CANDIDATE_FREEZE_COLUMNS = (
    "strategy_family",
    "entry_rule",
    "exit_rule",
    "candidate_label",
    "label_reason",
    "full_trade_count",
    "time_validation_trade_count",
    "random_holdout_trade_count",
    "full_avg_gross_return_with_slippage",
    "time_validation_avg_gross_return_with_slippage",
    "random_holdout_avg_gross_return_with_slippage",
    "full_delta_vs_no_trade",
    "random_holdout_delta_vs_no_trade",
    "full_delta_vs_winner_prediction_hold_to_end",
)

_SPLIT_ORDER = (
    "full_sample",
    "time_train",
    "time_validation",
    "random_train",
    "random_holdout",
)

_COMPARATOR_DESCRIPTIONS = (
    {
        "name": "no_trade",
        "description": "Zero-return baseline for the same opportunity count.",
    },
    {
        "name": "winner_prediction_hold_to_end",
        "description": "Buy the selected side at the strategy entry and hold until the final observed state.",
    },
)


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _serialise_scalar(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _format_num(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _normalize_holdout_ratio(holdout_ratio: float) -> float:
    ratio = _safe_float(holdout_ratio)
    if ratio is None:
        return 0.10
    return max(0.0, min(0.50, ratio))


def _summarize_return_frame(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "trade_count": 0,
            "win_rate": None,
            "avg_gross_return": None,
            "median_gross_return": None,
            "avg_gross_return_with_slippage": None,
            "avg_hold_time_seconds": None,
            "avg_mfe_after_entry": None,
            "avg_mae_after_entry": None,
        }
    return {
        "trade_count": int(len(frame)),
        "win_rate": float((frame["gross_return_with_slippage"] > 0).mean()),
        "avg_gross_return": float(frame["gross_return"].mean()),
        "median_gross_return": float(frame["gross_return"].median()),
        "avg_gross_return_with_slippage": float(frame["gross_return_with_slippage"].mean()),
        "avg_hold_time_seconds": float(frame["hold_time_seconds"].mean()),
        "avg_mfe_after_entry": float(frame["max_favorable_excursion_after_entry"].mean()),
        "avg_mae_after_entry": float(frame["max_adverse_excursion_after_entry"].mean()),
    }


def _build_random_holdout_frames(
    state_df: pd.DataFrame,
    *,
    holdout_ratio: float,
    holdout_seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if state_df.empty or "game_id" not in state_df.columns:
        return state_df.copy(), state_df.iloc[0:0].copy(), {"selection_rule": "no_games"}

    games = (
        state_df[["game_id", "game_date"]]
        .drop_duplicates(subset=["game_id"])
        .sort_values(["game_date", "game_id"], kind="mergesort", na_position="last")
        .reset_index(drop=True)
    )
    ratio = _normalize_holdout_ratio(holdout_ratio)
    if len(games) <= 1 or ratio <= 0:
        return state_df.copy(), state_df.iloc[0:0].copy(), {"selection_rule": "holdout_disabled"}

    holdout_count = int(round(len(games) * ratio))
    holdout_count = max(1, min(len(games) - 1, holdout_count))
    rng = np.random.default_rng(int(holdout_seed))
    chosen_index = rng.choice(len(games), size=holdout_count, replace=False)
    holdout_ids = set(games.iloc[np.sort(chosen_index)]["game_id"].tolist())

    holdout_df = state_df[state_df["game_id"].isin(holdout_ids)].copy()
    train_df = state_df[~state_df["game_id"].isin(holdout_ids)].copy()
    return train_df, holdout_df, {"selection_rule": f"sorted_game_id_rng(seed={int(holdout_seed)})"}


def _split_summary_row(sample_name: str, split_kind: str, selection_rule: str, frame: pd.DataFrame) -> dict[str, Any]:
    games_considered = int(frame["game_id"].nunique()) if not frame.empty and "game_id" in frame.columns else 0
    game_sides_considered = (
        int(frame[["game_id", "team_side"]].drop_duplicates().shape[0])
        if not frame.empty and {"game_id", "team_side"}.issubset(frame.columns)
        else 0
    )
    min_game_date = None
    max_game_date = None
    if not frame.empty and "game_date" in frame.columns:
        game_dates = pd.to_datetime(frame["game_date"], errors="coerce").dropna()
        if not game_dates.empty:
            min_game_date = _serialise_scalar(game_dates.min())
            max_game_date = _serialise_scalar(game_dates.max())
    return {
        "sample_name": sample_name,
        "split_kind": split_kind,
        "selection_rule": selection_rule,
        "state_rows_considered": int(len(frame)),
        "games_considered": games_considered,
        "game_sides_considered": game_sides_considered,
        "min_game_date": min_game_date,
        "max_game_date": max_game_date,
    }


def _build_split_summary_frame(split_frames: dict[str, pd.DataFrame], split_meta: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for sample_name in _SPLIT_ORDER:
        frame = split_frames.get(sample_name)
        meta = split_meta.get(sample_name) or {}
        if frame is None:
            continue
        rows.append(
            _split_summary_row(
                sample_name,
                str(meta.get("split_kind", "unknown")),
                str(meta.get("selection_rule", "n/a")),
                frame,
            )
        )
    return pd.DataFrame(rows, columns=BENCHMARK_SPLIT_SUMMARY_COLUMNS)


def _build_no_trade_summary(trade_count: int) -> dict[str, Any]:
    if trade_count <= 0:
        return {
            "trade_count": 0,
            "win_rate": None,
            "avg_gross_return": None,
            "median_gross_return": None,
            "avg_gross_return_with_slippage": None,
            "avg_hold_time_seconds": None,
            "avg_mfe_after_entry": None,
            "avg_mae_after_entry": None,
        }
    return {
        "trade_count": int(trade_count),
        "win_rate": 0.0,
        "avg_gross_return": 0.0,
        "median_gross_return": 0.0,
        "avg_gross_return_with_slippage": 0.0,
        "avg_hold_time_seconds": 0.0,
        "avg_mfe_after_entry": 0.0,
        "avg_mae_after_entry": 0.0,
    }


def _build_hold_to_end_frame(state_df: pd.DataFrame, trades_df: pd.DataFrame, *, slippage_cents: int) -> pd.DataFrame:
    if state_df.empty or trades_df.empty:
        return pd.DataFrame(
            columns=(
                "gross_return",
                "gross_return_with_slippage",
                "hold_time_seconds",
                "max_favorable_excursion_after_entry",
                "max_adverse_excursion_after_entry",
            )
        )

    slippage = max(0, int(slippage_cents)) / 100.0
    grouped = {
        (str(game_id), str(team_side)): group.sort_values("state_index", kind="mergesort").reset_index(drop=True)
        for (game_id, team_side), group in state_df.groupby(["game_id", "team_side"], sort=False)
    }
    rows: list[dict[str, Any]] = []
    for trade in trades_df.to_dict(orient="records"):
        key = (str(trade["game_id"]), str(trade["team_side"]))
        group = grouped.get(key)
        if group is None or group.empty:
            continue
        start_index = int(trade["entry_state_index"])
        window = group[group["state_index"] >= start_index].copy()
        if window.empty:
            continue
        entry_price = float(trade["entry_price"])
        exit_price = float(window.iloc[-1]["team_price"])
        entry_exec = min(0.999999, entry_price + slippage)
        exit_exec = max(0.0, exit_price - slippage)
        gross_return = (exit_price - entry_price) / entry_price if entry_price > 0 else 0.0
        gross_return_with_slippage = (exit_exec - entry_exec) / entry_exec if entry_exec > 0 else 0.0
        entry_at = pd.to_datetime(trade["entry_at"], errors="coerce", utc=True)
        exit_at = pd.to_datetime(window.iloc[-1]["event_at"], errors="coerce", utc=True)
        rows.append(
            {
                "gross_return": gross_return,
                "gross_return_with_slippage": gross_return_with_slippage,
                "hold_time_seconds": max(0.0, (exit_at - entry_at).total_seconds()),
                "max_favorable_excursion_after_entry": float(window["team_price"].max() - entry_price),
                "max_adverse_excursion_after_entry": float(entry_price - window["team_price"].min()),
            }
        )
    return pd.DataFrame(rows)


def _build_comparator_summary_frame(
    split_results: dict[str, BacktestResult],
    registry: dict[str, StrategyDefinition],
    *,
    slippage_cents: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for sample_name in _SPLIT_ORDER:
        split_result = split_results.get(sample_name)
        if split_result is None:
            continue
        for family in registry:
            trades_df = split_result.trade_frames.get(family, pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS))
            summaries = {
                "no_trade": _build_no_trade_summary(int(len(trades_df))),
                "winner_prediction_hold_to_end": _summarize_return_frame(
                    _build_hold_to_end_frame(split_result.state_df, trades_df, slippage_cents=slippage_cents)
                ),
            }
            for comparator_name, summary in summaries.items():
                rows.append(
                    {
                        "sample_name": sample_name,
                        "strategy_family": family,
                        "comparator_name": comparator_name,
                        "trade_count": summary.get("trade_count"),
                        "win_rate": summary.get("win_rate"),
                        "avg_gross_return": summary.get("avg_gross_return"),
                        "median_gross_return": summary.get("median_gross_return"),
                        "avg_gross_return_with_slippage": summary.get("avg_gross_return_with_slippage"),
                        "avg_hold_time_seconds": summary.get("avg_hold_time_seconds"),
                        "avg_mfe_after_entry": summary.get("avg_mfe_after_entry"),
                        "avg_mae_after_entry": summary.get("avg_mae_after_entry"),
                    }
                )
    return pd.DataFrame(rows, columns=BENCHMARK_COMPARATOR_COLUMNS)


def _build_family_benchmark_frame(
    split_results: dict[str, BacktestResult],
    registry: dict[str, StrategyDefinition],
    comparator_summary_df: pd.DataFrame,
    *,
    min_trade_count: int,
) -> pd.DataFrame:
    comparator_lookup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in comparator_summary_df.to_dict(orient="records"):
        comparator_lookup[(str(row["sample_name"]), str(row["strategy_family"]), str(row["comparator_name"]))] = row

    rows: list[dict[str, Any]] = []
    for sample_name in _SPLIT_ORDER:
        split_result = split_results.get(sample_name)
        if split_result is None:
            continue
        family_summaries = split_result.payload.get("families") or {}
        for family, definition in registry.items():
            summary = family_summaries.get(family) or {}
            no_trade = comparator_lookup.get((sample_name, family, "no_trade")) or {}
            hold_to_end = comparator_lookup.get((sample_name, family, "winner_prediction_hold_to_end")) or {}
            strategy_avg = _safe_float(summary.get("avg_gross_return_with_slippage"))
            rows.append(
                {
                    "sample_name": sample_name,
                    "strategy_family": family,
                    "entry_rule": definition.entry_rule,
                    "exit_rule": definition.exit_rule,
                    "description": definition.description,
                    "comparator_group": definition.comparator_group,
                    "trade_count": int(summary.get("trade_count") or 0),
                    "meets_min_trade_count_flag": int(summary.get("trade_count") or 0) >= max(1, int(min_trade_count)),
                    "win_rate": summary.get("win_rate"),
                    "avg_gross_return": summary.get("avg_gross_return"),
                    "median_gross_return": summary.get("median_gross_return"),
                    "avg_gross_return_with_slippage": strategy_avg,
                    "avg_hold_time_seconds": summary.get("avg_hold_time_seconds"),
                    "avg_mfe_after_entry": summary.get("avg_mfe_after_entry"),
                    "avg_mae_after_entry": summary.get("avg_mae_after_entry"),
                    "delta_vs_no_trade_avg_gross_return_with_slippage": (
                        None
                        if strategy_avg is None or _safe_float(no_trade.get("avg_gross_return_with_slippage")) is None
                        else strategy_avg - float(no_trade["avg_gross_return_with_slippage"])
                    ),
                    "delta_vs_winner_prediction_hold_to_end_avg_gross_return_with_slippage": (
                        None
                        if strategy_avg is None or _safe_float(hold_to_end.get("avg_gross_return_with_slippage")) is None
                        else strategy_avg - float(hold_to_end["avg_gross_return_with_slippage"])
                    ),
                }
            )
    return pd.DataFrame(rows, columns=BENCHMARK_FAMILY_SUMMARY_COLUMNS)


def _build_sample_vs_full_frame(family_summary_df: pd.DataFrame) -> pd.DataFrame:
    if family_summary_df.empty:
        return pd.DataFrame(columns=BENCHMARK_SAMPLE_VS_FULL_COLUMNS)
    full_lookup = {
        str(row["strategy_family"]): row
        for row in family_summary_df[family_summary_df["sample_name"] == "full_sample"].to_dict(orient="records")
    }
    rows: list[dict[str, Any]] = []
    for row in family_summary_df[family_summary_df["sample_name"] != "full_sample"].to_dict(orient="records"):
        baseline = full_lookup.get(str(row["strategy_family"])) or {}
        rows.append(
            {
                "sample_name": row["sample_name"],
                "strategy_family": row["strategy_family"],
                "trade_count_delta_vs_full": int(row.get("trade_count") or 0) - int(baseline.get("trade_count") or 0),
                "win_rate_delta_vs_full": (
                    None
                    if _safe_float(row.get("win_rate")) is None or _safe_float(baseline.get("win_rate")) is None
                    else float(row["win_rate"]) - float(baseline["win_rate"])
                ),
                "avg_gross_return_with_slippage_delta_vs_full": (
                    None
                    if _safe_float(row.get("avg_gross_return_with_slippage")) is None
                    or _safe_float(baseline.get("avg_gross_return_with_slippage")) is None
                    else float(row["avg_gross_return_with_slippage"]) - float(baseline["avg_gross_return_with_slippage"])
                ),
                "avg_hold_time_seconds_delta_vs_full": (
                    None
                    if _safe_float(row.get("avg_hold_time_seconds")) is None
                    or _safe_float(baseline.get("avg_hold_time_seconds")) is None
                    else float(row["avg_hold_time_seconds"]) - float(baseline["avg_hold_time_seconds"])
                ),
            }
        )
    return pd.DataFrame(rows, columns=BENCHMARK_SAMPLE_VS_FULL_COLUMNS)


def _context_summary_frame(sample_name: str, family: str, trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty:
        return pd.DataFrame(columns=BENCHMARK_CONTEXT_RANK_COLUMNS)
    summary = (
        trades_df.groupby(["period_label", "opening_band", "context_bucket"], dropna=False)
        .agg(
            trade_count=("game_id", "count"),
            win_rate=("gross_return_with_slippage", lambda values: float((pd.Series(values) > 0).mean())),
            avg_gross_return_with_slippage=("gross_return_with_slippage", "mean"),
            avg_hold_time_seconds=("hold_time_seconds", "mean"),
        )
        .reset_index()
    )
    summary.insert(0, "sample_name", sample_name)
    summary.insert(1, "strategy_family", family)
    return summary


def _build_context_rankings_frame(split_results: dict[str, BacktestResult], registry: dict[str, StrategyDefinition]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for sample_name in _SPLIT_ORDER:
        split_result = split_results.get(sample_name)
        if split_result is None:
            continue
        for family in registry:
            trades_df = split_result.trade_frames.get(family, pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS))
            summary = _context_summary_frame(sample_name, family, trades_df)
            if summary.empty:
                continue
            ranked_sides = {
                "best": summary.sort_values(
                    ["avg_gross_return_with_slippage", "trade_count", "context_bucket"],
                    ascending=[False, False, True],
                    kind="mergesort",
                ),
                "worst": summary.sort_values(
                    ["avg_gross_return_with_slippage", "trade_count", "context_bucket"],
                    ascending=[True, False, True],
                    kind="mergesort",
                ),
            }
            for ranking_side, ranked in ranked_sides.items():
                for rank, row in enumerate(ranked.head(3).to_dict(orient="records"), start=1):
                    rows.append(
                        {
                            "sample_name": sample_name,
                            "strategy_family": family,
                            "ranking_side": ranking_side,
                            "rank": rank,
                            "period_label": row.get("period_label"),
                            "opening_band": row.get("opening_band"),
                            "context_bucket": row.get("context_bucket"),
                            "trade_count": row.get("trade_count"),
                            "win_rate": row.get("win_rate"),
                            "avg_gross_return_with_slippage": row.get("avg_gross_return_with_slippage"),
                            "avg_hold_time_seconds": row.get("avg_hold_time_seconds"),
                        }
                    )
    return pd.DataFrame(rows, columns=BENCHMARK_CONTEXT_RANK_COLUMNS)


def _candidate_label(
    *,
    full_trade_count: int,
    time_trade_count: int,
    holdout_trade_count: int,
    full_return: float | None,
    time_return: float | None,
    holdout_return: float | None,
    min_trade_count: int,
) -> tuple[str, str]:
    required_trades = max(1, int(min_trade_count))
    if full_trade_count < required_trades:
        return "experimental", "below_min_trade_count"
    if time_trade_count <= 0 or holdout_trade_count <= 0:
        return "experimental", "missing_benchmark_sample"
    if full_return is not None and time_return is not None and holdout_return is not None:
        if full_return > 0 and time_return > 0 and holdout_return > 0:
            return "keep", "positive_on_full_time_and_holdout"
        if full_return <= 0 and time_return <= 0 and holdout_return <= 0:
            return "drop", "non_positive_on_full_time_and_holdout"
    return "experimental", "mixed_benchmark_signal"


def _build_candidate_freeze_frame(
    family_summary_df: pd.DataFrame,
    registry: dict[str, StrategyDefinition],
    *,
    min_trade_count: int,
) -> pd.DataFrame:
    lookup = {
        (str(row["sample_name"]), str(row["strategy_family"])): row
        for row in family_summary_df.to_dict(orient="records")
    }
    rows: list[dict[str, Any]] = []
    for family, definition in registry.items():
        full = lookup.get(("full_sample", family)) or {}
        time_validation = lookup.get(("time_validation", family)) or {}
        random_holdout = lookup.get(("random_holdout", family)) or {}
        label, reason = _candidate_label(
            full_trade_count=int(full.get("trade_count") or 0),
            time_trade_count=int(time_validation.get("trade_count") or 0),
            holdout_trade_count=int(random_holdout.get("trade_count") or 0),
            full_return=_safe_float(full.get("avg_gross_return_with_slippage")),
            time_return=_safe_float(time_validation.get("avg_gross_return_with_slippage")),
            holdout_return=_safe_float(random_holdout.get("avg_gross_return_with_slippage")),
            min_trade_count=min_trade_count,
        )
        rows.append(
            {
                "strategy_family": family,
                "entry_rule": definition.entry_rule,
                "exit_rule": definition.exit_rule,
                "candidate_label": label,
                "label_reason": reason,
                "full_trade_count": int(full.get("trade_count") or 0),
                "time_validation_trade_count": int(time_validation.get("trade_count") or 0),
                "random_holdout_trade_count": int(random_holdout.get("trade_count") or 0),
                "full_avg_gross_return_with_slippage": full.get("avg_gross_return_with_slippage"),
                "time_validation_avg_gross_return_with_slippage": time_validation.get("avg_gross_return_with_slippage"),
                "random_holdout_avg_gross_return_with_slippage": random_holdout.get("avg_gross_return_with_slippage"),
                "full_delta_vs_no_trade": full.get("delta_vs_no_trade_avg_gross_return_with_slippage"),
                "random_holdout_delta_vs_no_trade": random_holdout.get("delta_vs_no_trade_avg_gross_return_with_slippage"),
                "full_delta_vs_winner_prediction_hold_to_end": full.get(
                    "delta_vs_winner_prediction_hold_to_end_avg_gross_return_with_slippage"
                ),
            }
        )
    return pd.DataFrame(rows, columns=BENCHMARK_CANDIDATE_FREEZE_COLUMNS)


def _build_experiment_id(request: BacktestRunRequest, registry: dict[str, StrategyDefinition]) -> str:
    payload = {
        "request": asdict(request),
        "strategy_families": sorted(registry.keys()),
        "contract_version": BACKTEST_BENCHMARK_CONTRACT_VERSION,
    }
    fingerprint = hashlib.sha1(json.dumps(to_jsonable(payload), sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return f"nba_backtest_benchmark_{fingerprint}"


def build_benchmark_run_result(state_df: pd.DataFrame, request: BacktestRunRequest) -> BenchmarkRunResult:
    full_result = build_backtest_result(state_df, request)
    work = full_result.state_df
    registry = full_result.strategy_registry or resolve_strategy_registry(request.strategy_family)

    requested_cutoff = request.train_cutoff if request.train_cutoff else None
    time_cutoff = resolve_train_cutoff(
        work["game_date"] if not work.empty and "game_date" in work.columns else None,
        requested_cutoff=requested_cutoff,
    )
    time_train_df, time_validation_df = split_frame_by_cutoff(work, cutoff=time_cutoff)
    random_train_df, random_holdout_df, random_meta = _build_random_holdout_frames(
        work,
        holdout_ratio=request.holdout_ratio,
        holdout_seed=request.holdout_seed,
    )

    split_frames = {
        "full_sample": work,
        "time_train": time_train_df,
        "time_validation": time_validation_df,
        "random_train": random_train_df,
        "random_holdout": random_holdout_df,
    }
    split_meta = {
        "full_sample": {"split_kind": "reference", "selection_rule": "all_rows"},
        "time_train": {
            "split_kind": "train",
            "selection_rule": f"game_date <= {time_cutoff.date().isoformat()}" if time_cutoff is not None else "time_cutoff_unavailable",
        },
        "time_validation": {
            "split_kind": "validation",
            "selection_rule": f"game_date > {time_cutoff.date().isoformat()}" if time_cutoff is not None else "time_cutoff_unavailable",
        },
        "random_train": {
            "split_kind": "train",
            "selection_rule": f"game_id not in holdout; ratio={_normalize_holdout_ratio(request.holdout_ratio):.2f}",
        },
        "random_holdout": {
            "split_kind": "holdout",
            "selection_rule": f"{random_meta.get('selection_rule')}; ratio={_normalize_holdout_ratio(request.holdout_ratio):.2f}",
        },
    }
    split_results = {"full_sample": full_result}
    for sample_name in ("time_train", "time_validation", "random_train", "random_holdout"):
        split_results[sample_name] = build_backtest_result(split_frames[sample_name], request)

    split_summary_df = _build_split_summary_frame(split_frames, split_meta)
    comparator_summary_df = _build_comparator_summary_frame(split_results, registry, slippage_cents=request.slippage_cents)
    family_summary_df = _build_family_benchmark_frame(
        split_results,
        registry,
        comparator_summary_df,
        min_trade_count=request.min_trade_count,
    )
    sample_vs_full_df = _build_sample_vs_full_frame(family_summary_df)
    context_rankings_df = _build_context_rankings_frame(split_results, registry)
    candidate_freeze_df = _build_candidate_freeze_frame(family_summary_df, registry, min_trade_count=request.min_trade_count)

    payload = full_result.payload
    payload["benchmark"] = {
        "contract_version": BACKTEST_BENCHMARK_CONTRACT_VERSION,
        "metric_columns": [
            "trade_count",
            "win_rate",
            "avg_gross_return",
            "median_gross_return",
            "avg_gross_return_with_slippage",
            "avg_hold_time_seconds",
            "avg_mfe_after_entry",
            "avg_mae_after_entry",
        ],
        "minimum_trade_count": int(request.min_trade_count),
        "comparators": list(_COMPARATOR_DESCRIPTIONS),
        "time_validation_cutoff": time_cutoff.isoformat() if time_cutoff is not None else None,
        "random_holdout_ratio": _normalize_holdout_ratio(request.holdout_ratio),
        "random_holdout_seed": int(request.holdout_seed),
        "split_unit": "game_id",
        "split_summary": to_jsonable(split_summary_df.to_dict(orient="records")),
        "family_summary": to_jsonable(family_summary_df.to_dict(orient="records")),
        "comparator_summary": to_jsonable(comparator_summary_df.to_dict(orient="records")),
        "sample_vs_full": to_jsonable(sample_vs_full_df.to_dict(orient="records")),
        "context_rankings": to_jsonable(context_rankings_df.to_dict(orient="records")),
        "candidate_freeze": to_jsonable(candidate_freeze_df.to_dict(orient="records")),
    }
    payload["experiment"] = {
        "experiment_id": _build_experiment_id(request, registry),
        "run_at": datetime.now(timezone.utc).isoformat(),
        "request": to_jsonable(asdict(request)),
        "strategy_families": sorted(registry.keys()),
        "artifact_index": [],
    }

    benchmark_frames = {
        "split_summary": split_summary_df,
        "family_summary": family_summary_df,
        "comparator_summary": comparator_summary_df,
        "sample_vs_full": sample_vs_full_df,
        "context_rankings": context_rankings_df,
        "candidate_freeze": candidate_freeze_df,
    }
    return BenchmarkRunResult(
        payload=payload,
        full_result=full_result,
        split_results=split_results,
        benchmark_frames=benchmark_frames,
    )


def _render_benchmark_markdown(payload: dict[str, Any]) -> str:
    benchmark = payload.get("benchmark") or {}
    freeze_rows = benchmark.get("candidate_freeze") or []
    full_rows = [row for row in (benchmark.get("family_summary") or []) if row.get("sample_name") == "full_sample"]
    full_rows = sorted(
        full_rows,
        key=lambda row: (
            _safe_float(row.get("avg_gross_return_with_slippage")) is not None,
            _safe_float(row.get("avg_gross_return_with_slippage")) or float("-inf"),
        ),
        reverse=True,
    )
    lines = [
        "# NBA Analysis Backtests",
        "",
        f"- Season: `{payload['season']}`",
        f"- Season phase: `{payload['season_phase']}`",
        f"- Analysis version: `{payload['analysis_version']}`",
        f"- State rows considered: `{payload.get('state_rows_considered')}`",
        f"- Games considered: `{payload.get('games_considered')}`",
        f"- Benchmark contract: `{benchmark.get('contract_version')}`",
        f"- Time validation cutoff: `{benchmark.get('time_validation_cutoff')}`",
        f"- Random holdout ratio: `{_format_num(benchmark.get('random_holdout_ratio'))}`",
        f"- Random holdout seed: `{benchmark.get('random_holdout_seed')}`",
        "",
        "## Candidate Freeze",
        "",
    ]
    if not freeze_rows:
        lines.append("- No candidate-freeze rows were produced.")
        lines.append("")
    else:
        for row in freeze_rows:
            lines.append(
                f"- {row.get('strategy_family')}: `{row.get('candidate_label')}` because `{row.get('label_reason')}`"
            )
        lines.append("")
    lines.extend(["## Full Sample Ranking", ""])
    if not full_rows:
        lines.append("- No full-sample strategy rows were produced.")
        lines.append("")
    else:
        for row in full_rows:
            lines.append(
                f"- {row.get('strategy_family')}: trade_count `{row.get('trade_count')}`,"
                f" avg return with slippage `{_format_num(row.get('avg_gross_return_with_slippage'))}`,"
                f" delta vs no-trade `{_format_num(row.get('delta_vs_no_trade_avg_gross_return_with_slippage'))}`"
            )
        lines.append("")
    return "\n".join(lines)


def write_benchmark_artifacts(result: BenchmarkRunResult, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_backtest_artifacts(result.full_result, output_dir)
    payload = result.payload
    payload["artifacts"] = dict(payload.get("artifacts") or {})

    artifact_stems = {
        "split_summary": "benchmark_split_summary",
        "family_summary": "benchmark_family_summary",
        "comparator_summary": "benchmark_comparator_summary",
        "sample_vs_full": "benchmark_sample_vs_full",
        "context_rankings": "benchmark_context_rankings",
        "candidate_freeze": "benchmark_candidate_freeze",
    }
    for frame_name, frame in result.benchmark_frames.items():
        stem = artifact_stems.get(frame_name)
        if not stem:
            continue
        payload["artifacts"].update(
            {f"benchmark_{frame_name}_{key}": value for key, value in write_frame(output_dir / stem, frame).items()}
        )

    payload["artifacts"]["experiment_registry_json"] = write_json(output_dir / "benchmark_experiment_registry.json", payload["experiment"])
    payload["artifacts"]["json"] = write_json(output_dir / "run_analysis_backtests.json", payload)
    payload["artifacts"]["markdown"] = write_markdown(output_dir / "run_analysis_backtests.md", _render_benchmark_markdown(payload))
    payload["experiment"]["artifact_index"] = [{"name": key, "path": value} for key, value in sorted(payload["artifacts"].items())]
    payload["artifacts"]["experiment_registry_json"] = write_json(output_dir / "benchmark_experiment_registry.json", payload["experiment"])
    payload["artifacts"]["json"] = write_json(output_dir / "run_analysis_backtests.json", payload)
    return to_jsonable(payload)


__all__ = [
    "BENCHMARK_CANDIDATE_FREEZE_COLUMNS",
    "BENCHMARK_COMPARATOR_COLUMNS",
    "BENCHMARK_CONTEXT_RANK_COLUMNS",
    "BENCHMARK_FAMILY_SUMMARY_COLUMNS",
    "BENCHMARK_SAMPLE_VS_FULL_COLUMNS",
    "BENCHMARK_SPLIT_SUMMARY_COLUMNS",
    "build_benchmark_run_result",
    "write_benchmark_artifacts",
]
