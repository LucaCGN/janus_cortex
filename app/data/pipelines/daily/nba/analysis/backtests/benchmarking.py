from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
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
from app.data.pipelines.daily.nba.analysis.backtests.portfolio import (
    COMBINED_KEEP_FAMILIES_PORTFOLIO,
    PORTFOLIO_CANDIDATE_FREEZE_COLUMNS,
    PORTFOLIO_SCOPE_ROUTED,
    PORTFOLIO_SUMMARY_COLUMNS,
    PORTFOLIO_SCOPE_SINGLE_FAMILY,
    STATISTICAL_ROUTING_PORTFOLIO,
    build_combined_portfolio_benchmark_frames,
    build_portfolio_benchmark_frames,
    build_portfolio_candidate_freeze_frame,
    build_routed_portfolio_benchmark_frames,
    normalize_portfolio_game_limit,
    normalize_portfolio_initial_bankroll,
    normalize_portfolio_position_size_fraction,
    simulate_trade_portfolio,
)
from app.data.pipelines.daily.nba.analysis.backtests.registry import resolve_strategy_registry
from app.data.pipelines.daily.nba.analysis.backtests.specs import BacktestResult, BenchmarkRunResult, StrategyDefinition
from app.data.pipelines.daily.nba.analysis.contracts import (
    BACKTEST_BENCHMARK_CONTRACT_VERSION,
    DEFAULT_BACKTEST_ROBUSTNESS_SEEDS,
    BacktestRunRequest,
)
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

BENCHMARK_ROUTE_SUMMARY_COLUMNS = (
    "selection_sample_name",
    "opening_band",
    "selected_family",
    "selection_reason",
    "selected_trade_count",
    "selected_win_rate",
    "selected_avg_gross_return_with_slippage",
    "positive_family_count",
    "family_count_considered",
)

PORTFOLIO_ROBUSTNESS_DETAIL_COLUMNS = (
    "sample_name",
    "strategy_family",
    "portfolio_scope",
    "holdout_seed",
    "holdout_ratio",
    "selection_rule",
    "state_rows_considered",
    "games_considered",
    "trade_count_considered",
    "executed_trade_count",
    "ending_bankroll",
    "compounded_return",
    "max_drawdown_pct",
    "positive_bankroll_flag",
)

PORTFOLIO_ROBUSTNESS_SUMMARY_COLUMNS = (
    "strategy_family",
    "seed_count",
    "positive_seed_count",
    "positive_seed_rate",
    "min_ending_bankroll",
    "median_ending_bankroll",
    "max_ending_bankroll",
    "min_compounded_return",
    "median_compounded_return",
    "max_compounded_return",
    "worst_max_drawdown_pct",
    "min_executed_trade_count",
    "max_executed_trade_count",
    "robustness_label",
    "robustness_reason",
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


def _normalize_robustness_seeds(value: Any, *, fallback_seed: int) -> tuple[int, ...]:
    if value is None:
        seeds = DEFAULT_BACKTEST_ROBUSTNESS_SEEDS
    elif isinstance(value, str):
        seeds = tuple(
            int(chunk.strip())
            for chunk in value.split(",")
            if chunk.strip() and chunk.strip().lstrip("-").isdigit()
        )
    else:
        try:
            seeds = tuple(int(seed) for seed in value)
        except TypeError:
            seeds = ()
    normalized: list[int] = []
    seen: set[int] = set()
    for seed in seeds:
        resolved = int(seed)
        if resolved in seen:
            continue
        normalized.append(resolved)
        seen.add(resolved)
    if not normalized:
        return (int(fallback_seed),)
    return tuple(normalized)


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


def _build_opening_band_route_summary_frame(
    split_results: dict[str, BacktestResult],
    *,
    strategy_families: tuple[str, ...],
    selection_sample_name: str,
    min_trade_count: int,
) -> tuple[pd.DataFrame, dict[str, str]]:
    if not strategy_families:
        return pd.DataFrame(columns=BENCHMARK_ROUTE_SUMMARY_COLUMNS), {}
    selection_result = split_results.get(selection_sample_name)
    if selection_result is None:
        return pd.DataFrame(columns=BENCHMARK_ROUTE_SUMMARY_COLUMNS), {}

    rows: list[dict[str, Any]] = []
    required_trades = max(1, int(min_trade_count))
    for family in strategy_families:
        trades_df = selection_result.trade_frames.get(family, pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS))
        if trades_df.empty:
            continue
        summary = (
            trades_df.groupby("opening_band", dropna=False)
            .agg(
                trade_count=("game_id", "count"),
                win_rate=("gross_return_with_slippage", lambda values: float((pd.Series(values) > 0).mean())),
                avg_gross_return_with_slippage=("gross_return_with_slippage", "mean"),
            )
            .reset_index()
        )
        summary["strategy_family"] = family
        rows.extend(summary.to_dict(orient="records"))

    comparison_df = pd.DataFrame(rows)
    if comparison_df.empty:
        return pd.DataFrame(columns=BENCHMARK_ROUTE_SUMMARY_COLUMNS), {}

    route_rows: list[dict[str, Any]] = []
    route_map: dict[str, str] = {}
    for opening_band, band_df in comparison_df.groupby("opening_band", dropna=False, sort=True):
        eligible = band_df[band_df["trade_count"] >= required_trades].copy()
        if eligible.empty:
            eligible = band_df.copy()
            selection_reason = "best_available_avg_return"
        else:
            positive = eligible[eligible["avg_gross_return_with_slippage"] > 0].copy()
            if not positive.empty:
                eligible = positive
                selection_reason = "best_positive_avg_return"
            else:
                selection_reason = "best_non_positive_avg_return"
        selected = eligible.sort_values(
            ["avg_gross_return_with_slippage", "trade_count", "strategy_family"],
            ascending=[False, False, True],
            kind="mergesort",
        ).iloc[0]
        opening_band_key = str(opening_band)
        route_map[opening_band_key] = str(selected["strategy_family"])
        route_rows.append(
            {
                "selection_sample_name": selection_sample_name,
                "opening_band": opening_band_key,
                "selected_family": str(selected["strategy_family"]),
                "selection_reason": selection_reason,
                "selected_trade_count": int(selected["trade_count"]),
                "selected_win_rate": float(selected["win_rate"]),
                "selected_avg_gross_return_with_slippage": float(selected["avg_gross_return_with_slippage"]),
                "positive_family_count": int((band_df["avg_gross_return_with_slippage"] > 0).sum()),
                "family_count_considered": int(len(band_df)),
            }
        )
    route_summary_df = pd.DataFrame(route_rows, columns=BENCHMARK_ROUTE_SUMMARY_COLUMNS)
    return route_summary_df, route_map


def _portfolio_robustness_label(positive_seed_count: int, seed_count: int) -> tuple[str, str]:
    if seed_count <= 0:
        return "not_run", "no_robustness_seeds"
    if positive_seed_count == seed_count:
        return "stable_positive", "positive_on_all_seed_runs"
    if positive_seed_count == 0:
        return "stable_negative", "non_positive_on_all_seed_runs"
    return "mixed", "positive_only_on_subset_of_seed_runs"


def _build_portfolio_robustness_frames(
    state_df: pd.DataFrame,
    request: BacktestRunRequest,
    *,
    strategy_families: tuple[str, ...],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if state_df.empty or not strategy_families:
        return (
            pd.DataFrame(columns=PORTFOLIO_ROBUSTNESS_DETAIL_COLUMNS),
            pd.DataFrame(columns=PORTFOLIO_ROBUSTNESS_SUMMARY_COLUMNS),
        )

    detail_rows: list[dict[str, Any]] = []
    seeds = _normalize_robustness_seeds(request.robustness_seeds, fallback_seed=request.holdout_seed)
    for seed in seeds:
        _, holdout_df, holdout_meta = _build_random_holdout_frames(
            state_df,
            holdout_ratio=request.holdout_ratio,
            holdout_seed=seed,
        )
        selection_rule = f"{holdout_meta.get('selection_rule')}; ratio={_normalize_holdout_ratio(request.holdout_ratio):.2f}"
        for family in strategy_families:
            family_request = replace(request, strategy_family=family, holdout_seed=int(seed))
            split_result = build_backtest_result(holdout_df, family_request)
            trades_df = split_result.trade_frames.get(family, pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS))
            summary, _ = simulate_trade_portfolio(
                trades_df,
                sample_name="random_holdout_seed",
                strategy_family=family,
                portfolio_scope=PORTFOLIO_SCOPE_SINGLE_FAMILY,
                strategy_family_members=(family,),
                initial_bankroll=request.portfolio_initial_bankroll,
                position_size_fraction=request.portfolio_position_size_fraction,
                game_limit=request.portfolio_game_limit,
            )
            ending_bankroll = _safe_float(summary.get("ending_bankroll"))
            starting_bankroll = normalize_portfolio_initial_bankroll(request.portfolio_initial_bankroll)
            detail_rows.append(
                {
                    "sample_name": summary.get("sample_name"),
                    "strategy_family": family,
                    "portfolio_scope": summary.get("portfolio_scope"),
                    "holdout_seed": int(seed),
                    "holdout_ratio": _normalize_holdout_ratio(request.holdout_ratio),
                    "selection_rule": selection_rule,
                    "state_rows_considered": int(len(holdout_df)),
                    "games_considered": int(split_result.payload.get("games_considered") or 0),
                    "trade_count_considered": int(summary.get("trade_count_considered") or 0),
                    "executed_trade_count": int(summary.get("executed_trade_count") or 0),
                    "ending_bankroll": ending_bankroll,
                    "compounded_return": _safe_float(summary.get("compounded_return")),
                    "max_drawdown_pct": _safe_float(summary.get("max_drawdown_pct")),
                    "positive_bankroll_flag": bool(ending_bankroll is not None and ending_bankroll > starting_bankroll),
                }
            )

    detail_df = pd.DataFrame(detail_rows, columns=PORTFOLIO_ROBUSTNESS_DETAIL_COLUMNS)
    if detail_df.empty:
        return detail_df, pd.DataFrame(columns=PORTFOLIO_ROBUSTNESS_SUMMARY_COLUMNS)

    summary_rows: list[dict[str, Any]] = []
    for family, family_df in detail_df.groupby("strategy_family", sort=True):
        seed_count = int(len(family_df))
        positive_seed_count = int(family_df["positive_bankroll_flag"].sum())
        robustness_label, robustness_reason = _portfolio_robustness_label(positive_seed_count, seed_count)
        ending = pd.to_numeric(family_df["ending_bankroll"], errors="coerce")
        compounded = pd.to_numeric(family_df["compounded_return"], errors="coerce")
        drawdown = pd.to_numeric(family_df["max_drawdown_pct"], errors="coerce")
        executed = pd.to_numeric(family_df["executed_trade_count"], errors="coerce")
        summary_rows.append(
            {
                "strategy_family": family,
                "seed_count": seed_count,
                "positive_seed_count": positive_seed_count,
                "positive_seed_rate": positive_seed_count / seed_count if seed_count > 0 else None,
                "min_ending_bankroll": float(ending.min()) if not ending.dropna().empty else None,
                "median_ending_bankroll": float(ending.median()) if not ending.dropna().empty else None,
                "max_ending_bankroll": float(ending.max()) if not ending.dropna().empty else None,
                "min_compounded_return": float(compounded.min()) if not compounded.dropna().empty else None,
                "median_compounded_return": float(compounded.median()) if not compounded.dropna().empty else None,
                "max_compounded_return": float(compounded.max()) if not compounded.dropna().empty else None,
                "worst_max_drawdown_pct": float(drawdown.max()) if not drawdown.dropna().empty else None,
                "min_executed_trade_count": int(executed.min()) if not executed.dropna().empty else 0,
                "max_executed_trade_count": int(executed.max()) if not executed.dropna().empty else 0,
                "robustness_label": robustness_label,
                "robustness_reason": robustness_reason,
            }
        )
    summary_df = pd.DataFrame(summary_rows, columns=PORTFOLIO_ROBUSTNESS_SUMMARY_COLUMNS)
    return detail_df, summary_df


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
    portfolio_summary_df, portfolio_steps_df = build_portfolio_benchmark_frames(
        split_results,
        registry,
        initial_bankroll=request.portfolio_initial_bankroll,
        position_size_fraction=request.portfolio_position_size_fraction,
        game_limit=request.portfolio_game_limit,
        split_order=_SPLIT_ORDER,
    )
    portfolio_candidate_freeze_df = build_portfolio_candidate_freeze_frame(
        portfolio_summary_df,
        registry,
        starting_bankroll=request.portfolio_initial_bankroll,
        min_trade_count=request.min_trade_count,
    )
    starting_bankroll = normalize_portfolio_initial_bankroll(request.portfolio_initial_bankroll)
    positive_routing_families = tuple(
        sorted(
            str(row["strategy_family"])
            for row in portfolio_summary_df.to_dict(orient="records")
            if row.get("sample_name") == "full_sample"
            and row.get("portfolio_scope") == PORTFOLIO_SCOPE_SINGLE_FAMILY
            and str(row.get("strategy_family")) in registry
            and _safe_float(row.get("ending_bankroll")) is not None
            and float(row["ending_bankroll"]) > starting_bankroll
        )
    )
    route_summary_df, opening_band_route_map = _build_opening_band_route_summary_frame(
        split_results,
        strategy_families=positive_routing_families,
        selection_sample_name="time_train",
        min_trade_count=max(3, int(request.min_trade_count) // 4),
    )
    keep_families = tuple(
        sorted(
            row["strategy_family"]
            for row in portfolio_candidate_freeze_df.to_dict(orient="records")
            if row.get("candidate_label") == "keep"
        )
    )
    combined_portfolio_summary_df, combined_portfolio_steps_df = build_combined_portfolio_benchmark_frames(
        split_results,
        strategy_families=keep_families,
        initial_bankroll=request.portfolio_initial_bankroll,
        position_size_fraction=request.portfolio_position_size_fraction,
        game_limit=request.portfolio_game_limit,
        split_order=_SPLIT_ORDER,
        combined_family_name=COMBINED_KEEP_FAMILIES_PORTFOLIO,
    )
    if not combined_portfolio_summary_df.empty:
        portfolio_summary_df = pd.concat([portfolio_summary_df, combined_portfolio_summary_df], ignore_index=True)
    if not combined_portfolio_steps_df.empty:
        portfolio_steps_df = pd.concat([portfolio_steps_df, combined_portfolio_steps_df], ignore_index=True)
    routed_portfolio_summary_df, routed_portfolio_steps_df = build_routed_portfolio_benchmark_frames(
        split_results,
        opening_band_route_map=opening_band_route_map,
        strategy_families=positive_routing_families,
        initial_bankroll=request.portfolio_initial_bankroll,
        position_size_fraction=request.portfolio_position_size_fraction,
        game_limit=request.portfolio_game_limit,
        split_order=_SPLIT_ORDER,
        routed_family_name=STATISTICAL_ROUTING_PORTFOLIO,
    )
    if not routed_portfolio_summary_df.empty:
        portfolio_summary_df = pd.concat([portfolio_summary_df, routed_portfolio_summary_df], ignore_index=True)
    if not routed_portfolio_steps_df.empty:
        portfolio_steps_df = pd.concat([portfolio_steps_df, routed_portfolio_steps_df], ignore_index=True)
    robustness_families = tuple(sorted(registry.keys()))
    portfolio_robustness_detail_df, portfolio_robustness_summary_df = _build_portfolio_robustness_frames(
        work,
        request,
        strategy_families=robustness_families,
    )

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
        "robustness_seeds": list(_normalize_robustness_seeds(request.robustness_seeds, fallback_seed=request.holdout_seed)),
        "split_unit": "game_id",
        "portfolio_config": {
            "initial_bankroll": normalize_portfolio_initial_bankroll(request.portfolio_initial_bankroll),
            "position_size_fraction": normalize_portfolio_position_size_fraction(request.portfolio_position_size_fraction),
            "game_limit": normalize_portfolio_game_limit(request.portfolio_game_limit),
        },
        "portfolio_keep_families": list(keep_families),
        "portfolio_robustness_families": list(robustness_families),
        "portfolio_combined_family_name": COMBINED_KEEP_FAMILIES_PORTFOLIO if len(keep_families) >= 2 else None,
        "portfolio_routing_families": list(positive_routing_families),
        "portfolio_routing_family_name": STATISTICAL_ROUTING_PORTFOLIO if opening_band_route_map else None,
        "portfolio_opening_band_route_map": opening_band_route_map,
        "portfolio_metric_columns": [
            "ending_bankroll",
            "total_pnl_amount",
            "compounded_return",
            "max_drawdown_amount",
            "max_drawdown_pct",
            "executed_trade_count",
            "skipped_overlap_count",
            "skipped_bankroll_count",
        ],
        "split_summary": to_jsonable(split_summary_df.to_dict(orient="records")),
        "family_summary": to_jsonable(family_summary_df.to_dict(orient="records")),
        "comparator_summary": to_jsonable(comparator_summary_df.to_dict(orient="records")),
        "sample_vs_full": to_jsonable(sample_vs_full_df.to_dict(orient="records")),
        "context_rankings": to_jsonable(context_rankings_df.to_dict(orient="records")),
        "candidate_freeze": to_jsonable(candidate_freeze_df.to_dict(orient="records")),
        "route_summary": to_jsonable(route_summary_df.to_dict(orient="records")),
        "portfolio_summary": to_jsonable(portfolio_summary_df.to_dict(orient="records")),
        "portfolio_candidate_freeze": to_jsonable(portfolio_candidate_freeze_df.to_dict(orient="records")),
        "portfolio_robustness_detail": to_jsonable(portfolio_robustness_detail_df.to_dict(orient="records")),
        "portfolio_robustness_summary": to_jsonable(portfolio_robustness_summary_df.to_dict(orient="records")),
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
        "route_summary": route_summary_df,
        "portfolio_summary": portfolio_summary_df,
        "portfolio_steps": portfolio_steps_df,
        "portfolio_candidate_freeze": portfolio_candidate_freeze_df,
        "portfolio_robustness_detail": portfolio_robustness_detail_df,
        "portfolio_robustness_summary": portfolio_robustness_summary_df,
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
    route_rows = benchmark.get("route_summary") or []
    portfolio_freeze_rows = benchmark.get("portfolio_candidate_freeze") or []
    portfolio_robustness_rows = benchmark.get("portfolio_robustness_summary") or []
    portfolio_full_rows = [row for row in (benchmark.get("portfolio_summary") or []) if row.get("sample_name") == "full_sample"]
    portfolio_full_rows = sorted(
        portfolio_full_rows,
        key=lambda row: (
            _safe_float(row.get("ending_bankroll")) is not None,
            _safe_float(row.get("ending_bankroll")) or float("-inf"),
        ),
        reverse=True,
    )
    combined_full_row = next(
        (
            row
            for row in portfolio_full_rows
            if row.get("strategy_family") == benchmark.get("portfolio_combined_family_name")
        ),
        None,
    )
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
        f"- Robustness seeds: `{', '.join(str(seed) for seed in (benchmark.get('robustness_seeds') or []))}`",
        f"- Portfolio initial bankroll: `{_format_num((benchmark.get('portfolio_config') or {}).get('initial_bankroll'))}`",
        f"- Portfolio position fraction: `{_format_num((benchmark.get('portfolio_config') or {}).get('position_size_fraction'))}`",
        f"- Portfolio game limit: `{_format_num((benchmark.get('portfolio_config') or {}).get('game_limit'))}`",
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
    lines.extend(["## Sequential Portfolio Candidate Freeze", ""])
    if not portfolio_freeze_rows:
        lines.append("- No sequential portfolio candidate-freeze rows were produced.")
        lines.append("")
    else:
        for row in portfolio_freeze_rows:
            lines.append(
                f"- {row.get('strategy_family')}: `{row.get('candidate_label')}` because `{row.get('label_reason')}`"
            )
        lines.append("")
    lines.extend(["## Statistical Routing", ""])
    if not route_rows:
        lines.append("- No opening-band routing rows were produced.")
        lines.append("")
    else:
        for row in route_rows:
            lines.append(
                f"- {row.get('opening_band')}: `{row.get('selected_family')}` from `{row.get('selection_sample_name')}`"
                f" because `{row.get('selection_reason')}`;"
                f" avg return `{_format_num(row.get('selected_avg_gross_return_with_slippage'))}`"
                f" across `{row.get('selected_trade_count')}` trades"
            )
        lines.append("")
    lines.extend(["## Combined Keep-Family Sleeve", ""])
    if combined_full_row is None:
        lines.append("- No combined keep-family sleeve was produced.")
        lines.append("")
    else:
        lines.append(
            f"- {combined_full_row.get('strategy_family')}: members `{combined_full_row.get('strategy_family_members')}`,"
            f" ending bankroll `{_format_num(combined_full_row.get('ending_bankroll'))}`,"
            f" compounded return `{_format_num(combined_full_row.get('compounded_return'))}`,"
            f" max drawdown `{_format_num(combined_full_row.get('max_drawdown_pct'))}`"
        )
        lines.append("")
    lines.extend(["## Repeated-Seed Robustness", ""])
    if not portfolio_robustness_rows:
        lines.append("- No repeated-seed robustness rows were produced.")
        lines.append("")
    else:
        for row in portfolio_robustness_rows:
            lines.append(
                f"- {row.get('strategy_family')}: `{row.get('robustness_label')}` because `{row.get('robustness_reason')}`;"
                f" positive seeds `{row.get('positive_seed_count')}/{row.get('seed_count')}`,"
                f" median bankroll `{_format_num(row.get('median_ending_bankroll'))}`,"
                f" worst drawdown `{_format_num(row.get('worst_max_drawdown_pct'))}`"
            )
        lines.append("")
    lines.extend(["## Sequential Portfolio Ranking", ""])
    if not portfolio_full_rows:
        lines.append("- No full-sample sequential portfolio rows were produced.")
        lines.append("")
    else:
        for row in portfolio_full_rows:
            lines.append(
                f"- {row.get('strategy_family')}: ending bankroll `{_format_num(row.get('ending_bankroll'))}`,"
                f" compounded return `{_format_num(row.get('compounded_return'))}`,"
                f" max drawdown `{_format_num(row.get('max_drawdown_pct'))}`"
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
        "route_summary": "benchmark_route_summary",
        "portfolio_summary": "benchmark_portfolio_summary",
        "portfolio_steps": "benchmark_portfolio_steps",
        "portfolio_candidate_freeze": "benchmark_portfolio_candidate_freeze",
        "portfolio_robustness_detail": "benchmark_portfolio_robustness_detail",
        "portfolio_robustness_summary": "benchmark_portfolio_robustness_summary",
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
    "BENCHMARK_ROUTE_SUMMARY_COLUMNS",
    "PORTFOLIO_CANDIDATE_FREEZE_COLUMNS",
    "PORTFOLIO_ROBUSTNESS_DETAIL_COLUMNS",
    "PORTFOLIO_ROBUSTNESS_SUMMARY_COLUMNS",
    "PORTFOLIO_SUMMARY_COLUMNS",
    "BENCHMARK_SAMPLE_VS_FULL_COLUMNS",
    "BENCHMARK_SPLIT_SUMMARY_COLUMNS",
    "build_benchmark_run_result",
    "write_benchmark_artifacts",
]
