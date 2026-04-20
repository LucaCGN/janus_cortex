from __future__ import annotations

from typing import Any

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import BACKTEST_TRADE_COLUMNS
from app.data.pipelines.daily.nba.analysis.backtests.specs import BacktestResult, StrategyDefinition
from app.data.pipelines.daily.nba.analysis.contracts import (
    DEFAULT_BACKTEST_PORTFOLIO_GAME_LIMIT,
    DEFAULT_BACKTEST_PORTFOLIO_INITIAL_BANKROLL,
    DEFAULT_BACKTEST_PORTFOLIO_POSITION_SIZE_FRACTION,
)


PORTFOLIO_SCOPE_SINGLE_FAMILY = "single_family"
PORTFOLIO_SCOPE_COMBINED = "combined_family_set"
PORTFOLIO_SCOPE_ROUTED = "routed_family_set"
COMBINED_KEEP_FAMILIES_PORTFOLIO = "combined_keep_families"
STATISTICAL_ROUTING_PORTFOLIO = "statistical_routing_v1"

PORTFOLIO_SUMMARY_COLUMNS = (
    "sample_name",
    "strategy_family",
    "portfolio_scope",
    "strategy_family_members",
    "strategy_family_count",
    "starting_bankroll",
    "ending_bankroll",
    "total_pnl_amount",
    "compounded_return",
    "peak_bankroll",
    "min_bankroll",
    "max_drawdown_amount",
    "max_drawdown_pct",
    "trade_count_considered",
    "executed_trade_count",
    "skipped_overlap_count",
    "skipped_bankroll_count",
    "games_considered",
    "position_size_fraction",
    "game_limit",
    "avg_executed_trade_return_with_slippage",
    "first_entry_at",
    "last_exit_at",
)

PORTFOLIO_STEP_COLUMNS = (
    "sample_name",
    "strategy_family",
    "portfolio_scope",
    "strategy_family_members",
    "source_strategy_family",
    "trade_sequence",
    "game_sequence",
    "game_id",
    "team_side",
    "team_slug",
    "opponent_team_slug",
    "entry_at",
    "exit_at",
    "gross_return_with_slippage",
    "portfolio_action",
    "skip_reason",
    "bankroll_before",
    "stake_amount",
    "profit_loss_amount",
    "bankroll_after",
    "peak_bankroll_after_step",
    "drawdown_amount_after_step",
    "drawdown_pct_after_step",
)

PORTFOLIO_CANDIDATE_FREEZE_COLUMNS = (
    "strategy_family",
    "entry_rule",
    "exit_rule",
    "candidate_label",
    "label_reason",
    "full_executed_trade_count",
    "time_validation_executed_trade_count",
    "random_holdout_executed_trade_count",
    "full_ending_bankroll",
    "time_validation_ending_bankroll",
    "random_holdout_ending_bankroll",
    "full_compounded_return",
    "time_validation_compounded_return",
    "random_holdout_compounded_return",
    "full_max_drawdown_pct",
    "random_holdout_max_drawdown_pct",
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


def _normalize_family_members(
    strategy_family: str,
    family_members: tuple[str, ...] | list[str] | None,
) -> tuple[str, ...]:
    values = family_members if family_members else (strategy_family,)
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        resolved = str(value).strip()
        if not resolved or resolved in seen:
            continue
        normalized.append(resolved)
        seen.add(resolved)
    return tuple(normalized) if normalized else (strategy_family,)


def normalize_portfolio_initial_bankroll(value: float | None) -> float:
    bankroll = _safe_float(value)
    if bankroll is None:
        return DEFAULT_BACKTEST_PORTFOLIO_INITIAL_BANKROLL
    return max(0.01, bankroll)


def normalize_portfolio_position_size_fraction(value: float | None) -> float:
    fraction = _safe_float(value)
    if fraction is None:
        return DEFAULT_BACKTEST_PORTFOLIO_POSITION_SIZE_FRACTION
    return max(0.0, min(1.0, fraction))


def normalize_portfolio_game_limit(value: int | None) -> int | None:
    if value is None:
        return DEFAULT_BACKTEST_PORTFOLIO_GAME_LIMIT
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return DEFAULT_BACKTEST_PORTFOLIO_GAME_LIMIT
    if resolved <= 0:
        return None
    return resolved


def _prepare_trade_frame(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty:
        return pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS)
    work = trades_df.copy()
    for column in ("entry_at", "exit_at"):
        if column in work.columns:
            work[column] = pd.to_datetime(work[column], errors="coerce", utc=True)
    for column in (
        "entry_state_index",
        "exit_state_index",
        "entry_price",
        "exit_price",
        "gross_return",
        "gross_return_with_slippage",
        "hold_time_seconds",
    ):
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    sort_columns = [
        column
        for column in (
            "entry_at",
            "exit_at",
            "game_id",
            "team_side",
            "source_strategy_family",
            "entry_state_index",
            "exit_state_index",
        )
        if column in work.columns
    ]
    if sort_columns:
        work = work.sort_values(sort_columns, kind="mergesort", na_position="last").reset_index(drop=True)
    return work


def _apply_game_limit(trades_df: pd.DataFrame, *, game_limit: int | None) -> tuple[pd.DataFrame, dict[str, int]]:
    if trades_df.empty:
        return trades_df.copy(), {}
    order_frame = (
        trades_df.groupby("game_id", dropna=False)
        .agg(first_entry_at=("entry_at", "min"))
        .reset_index()
        .sort_values(["first_entry_at", "game_id"], kind="mergesort", na_position="last")
        .reset_index(drop=True)
    )
    if game_limit is not None:
        order_frame = order_frame.head(game_limit).reset_index(drop=True)
    game_order = {str(game_id): index for index, game_id in enumerate(order_frame["game_id"].tolist(), start=1)}
    limited = trades_df[trades_df["game_id"].astype(str).isin(game_order.keys())].copy()
    limited["game_sequence"] = limited["game_id"].astype(str).map(game_order)
    limited = limited.sort_values(
        ["game_sequence", "entry_at", "game_id", "team_side", "entry_state_index"],
        kind="mergesort",
        na_position="last",
    ).reset_index(drop=True)
    return limited, game_order


def simulate_trade_portfolio(
    trades_df: pd.DataFrame,
    *,
    sample_name: str,
    strategy_family: str,
    portfolio_scope: str = PORTFOLIO_SCOPE_SINGLE_FAMILY,
    strategy_family_members: tuple[str, ...] | list[str] | None = None,
    initial_bankroll: float,
    position_size_fraction: float,
    game_limit: int | None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    bankroll = normalize_portfolio_initial_bankroll(initial_bankroll)
    fraction = normalize_portfolio_position_size_fraction(position_size_fraction)
    resolved_game_limit = normalize_portfolio_game_limit(game_limit)
    resolved_family_members = _normalize_family_members(strategy_family, strategy_family_members)
    serialized_family_members = ",".join(resolved_family_members)
    work = _prepare_trade_frame(trades_df)
    limited, game_order = _apply_game_limit(work, game_limit=resolved_game_limit)

    peak_bankroll = bankroll
    min_bankroll = bankroll
    max_drawdown_amount = 0.0
    max_drawdown_pct = 0.0
    executed_trade_returns: list[float] = []
    step_rows: list[dict[str, Any]] = []
    last_exit_at = pd.NaT
    executed_trade_count = 0
    skipped_overlap_count = 0
    skipped_bankroll_count = 0

    for trade_sequence, row in enumerate(limited.to_dict(orient="records"), start=1):
        bankroll_before = bankroll
        entry_at = pd.to_datetime(row.get("entry_at"), errors="coerce", utc=True)
        exit_at = pd.to_datetime(row.get("exit_at"), errors="coerce", utc=True)
        trade_return = _safe_float(row.get("gross_return_with_slippage")) or 0.0
        action = "executed"
        skip_reason = None
        stake_amount = 0.0
        profit_loss_amount = 0.0

        if pd.notna(last_exit_at) and pd.notna(entry_at) and entry_at < last_exit_at:
            action = "skipped"
            skip_reason = "overlap"
            skipped_overlap_count += 1
        elif bankroll_before <= 0 or fraction <= 0:
            action = "skipped"
            skip_reason = "bankroll"
            skipped_bankroll_count += 1
        else:
            stake_amount = bankroll_before * fraction
            profit_loss_amount = stake_amount * trade_return
            bankroll = max(0.0, bankroll_before + profit_loss_amount)
            executed_trade_count += 1
            executed_trade_returns.append(trade_return)
            last_exit_at = exit_at if pd.notna(exit_at) else entry_at

        peak_bankroll = max(peak_bankroll, bankroll)
        min_bankroll = min(min_bankroll, bankroll)
        drawdown_amount = max(0.0, peak_bankroll - bankroll)
        drawdown_pct = drawdown_amount / peak_bankroll if peak_bankroll > 0 else 0.0
        max_drawdown_amount = max(max_drawdown_amount, drawdown_amount)
        max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)

        step_rows.append(
            {
                "sample_name": sample_name,
                "strategy_family": strategy_family,
                "portfolio_scope": portfolio_scope,
                "strategy_family_members": serialized_family_members,
                "source_strategy_family": row.get("source_strategy_family") or strategy_family,
                "trade_sequence": trade_sequence,
                "game_sequence": row.get("game_sequence"),
                "game_id": row.get("game_id"),
                "team_side": row.get("team_side"),
                "team_slug": row.get("team_slug"),
                "opponent_team_slug": row.get("opponent_team_slug"),
                "entry_at": _serialise_scalar(entry_at),
                "exit_at": _serialise_scalar(exit_at),
                "gross_return_with_slippage": trade_return,
                "portfolio_action": action,
                "skip_reason": skip_reason,
                "bankroll_before": bankroll_before,
                "stake_amount": stake_amount,
                "profit_loss_amount": profit_loss_amount,
                "bankroll_after": bankroll,
                "peak_bankroll_after_step": peak_bankroll,
                "drawdown_amount_after_step": drawdown_amount,
                "drawdown_pct_after_step": drawdown_pct,
            }
        )

    summary = {
        "sample_name": sample_name,
        "strategy_family": strategy_family,
        "portfolio_scope": portfolio_scope,
        "strategy_family_members": serialized_family_members,
        "strategy_family_count": int(len(resolved_family_members)),
        "starting_bankroll": normalize_portfolio_initial_bankroll(initial_bankroll),
        "ending_bankroll": bankroll,
        "total_pnl_amount": bankroll - normalize_portfolio_initial_bankroll(initial_bankroll),
        "compounded_return": (bankroll / normalize_portfolio_initial_bankroll(initial_bankroll)) - 1.0,
        "peak_bankroll": peak_bankroll,
        "min_bankroll": min_bankroll,
        "max_drawdown_amount": max_drawdown_amount,
        "max_drawdown_pct": max_drawdown_pct,
        "trade_count_considered": int(len(limited)),
        "executed_trade_count": executed_trade_count,
        "skipped_overlap_count": skipped_overlap_count,
        "skipped_bankroll_count": skipped_bankroll_count,
        "games_considered": int(len(game_order)),
        "position_size_fraction": fraction,
        "game_limit": resolved_game_limit,
        "avg_executed_trade_return_with_slippage": (
            sum(executed_trade_returns) / len(executed_trade_returns) if executed_trade_returns else None
        ),
        "first_entry_at": _serialise_scalar(limited["entry_at"].min()) if not limited.empty else None,
        "last_exit_at": _serialise_scalar(limited["exit_at"].max()) if not limited.empty else None,
    }
    return summary, pd.DataFrame(step_rows, columns=PORTFOLIO_STEP_COLUMNS)


def build_portfolio_benchmark_frames(
    split_results: dict[str, BacktestResult],
    registry: dict[str, StrategyDefinition],
    *,
    initial_bankroll: float,
    position_size_fraction: float,
    game_limit: int | None,
    split_order: tuple[str, ...],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows: list[dict[str, Any]] = []
    step_frames: list[pd.DataFrame] = []
    for sample_name in split_order:
        split_result = split_results.get(sample_name)
        if split_result is None:
            continue
        for family in registry:
            trades_df = split_result.trade_frames.get(family, pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS))
            summary, steps_df = simulate_trade_portfolio(
                trades_df,
                sample_name=sample_name,
                strategy_family=family,
                portfolio_scope=PORTFOLIO_SCOPE_SINGLE_FAMILY,
                strategy_family_members=(family,),
                initial_bankroll=initial_bankroll,
                position_size_fraction=position_size_fraction,
                game_limit=game_limit,
            )
            summary_rows.append(summary)
            if not steps_df.empty:
                step_frames.append(steps_df)
    summary_df = pd.DataFrame(summary_rows, columns=PORTFOLIO_SUMMARY_COLUMNS)
    steps_df = pd.concat(step_frames, ignore_index=True) if step_frames else pd.DataFrame(columns=PORTFOLIO_STEP_COLUMNS)
    return summary_df, steps_df


def build_combined_portfolio_benchmark_frames(
    split_results: dict[str, BacktestResult],
    *,
    strategy_families: tuple[str, ...] | list[str],
    initial_bankroll: float,
    position_size_fraction: float,
    game_limit: int | None,
    split_order: tuple[str, ...],
    combined_family_name: str = COMBINED_KEEP_FAMILIES_PORTFOLIO,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    combined_members = _normalize_family_members(combined_family_name, strategy_families)
    if len(combined_members) < 2:
        return pd.DataFrame(columns=PORTFOLIO_SUMMARY_COLUMNS), pd.DataFrame(columns=PORTFOLIO_STEP_COLUMNS)

    summary_rows: list[dict[str, Any]] = []
    step_frames: list[pd.DataFrame] = []
    empty_columns = [*BACKTEST_TRADE_COLUMNS, "source_strategy_family"]
    for sample_name in split_order:
        split_result = split_results.get(sample_name)
        if split_result is None:
            continue
        trade_frames: list[pd.DataFrame] = []
        for family in combined_members:
            trades_df = split_result.trade_frames.get(family, pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS))
            if trades_df.empty:
                continue
            family_frame = trades_df.copy()
            family_frame["source_strategy_family"] = family
            trade_frames.append(family_frame)
        combined_trades_df = (
            pd.concat(trade_frames, ignore_index=True)
            if trade_frames
            else pd.DataFrame(columns=empty_columns)
        )
        summary, steps_df = simulate_trade_portfolio(
            combined_trades_df,
            sample_name=sample_name,
            strategy_family=combined_family_name,
            portfolio_scope=PORTFOLIO_SCOPE_COMBINED,
            strategy_family_members=combined_members,
            initial_bankroll=initial_bankroll,
            position_size_fraction=position_size_fraction,
            game_limit=game_limit,
        )
        summary_rows.append(summary)
        if not steps_df.empty:
            step_frames.append(steps_df)

    summary_df = pd.DataFrame(summary_rows, columns=PORTFOLIO_SUMMARY_COLUMNS)
    steps_df = pd.concat(step_frames, ignore_index=True) if step_frames else pd.DataFrame(columns=PORTFOLIO_STEP_COLUMNS)
    return summary_df, steps_df


def build_routed_portfolio_benchmark_frames(
    split_results: dict[str, BacktestResult],
    *,
    opening_band_route_map: dict[str, str],
    strategy_families: tuple[str, ...] | list[str],
    initial_bankroll: float,
    position_size_fraction: float,
    game_limit: int | None,
    split_order: tuple[str, ...],
    routed_family_name: str = STATISTICAL_ROUTING_PORTFOLIO,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    routed_members = _normalize_family_members(routed_family_name, strategy_families)
    if not opening_band_route_map or not routed_members:
        return pd.DataFrame(columns=PORTFOLIO_SUMMARY_COLUMNS), pd.DataFrame(columns=PORTFOLIO_STEP_COLUMNS)

    summary_rows: list[dict[str, Any]] = []
    step_frames: list[pd.DataFrame] = []
    empty_columns = [*BACKTEST_TRADE_COLUMNS, "source_strategy_family", "route_selected_family"]
    valid_families = set(routed_members)
    for sample_name in split_order:
        split_result = split_results.get(sample_name)
        if split_result is None:
            continue
        trade_frames: list[pd.DataFrame] = []
        for family in routed_members:
            trades_df = split_result.trade_frames.get(family, pd.DataFrame(columns=BACKTEST_TRADE_COLUMNS))
            if trades_df.empty:
                continue
            family_frame = trades_df.copy()
            family_frame["route_selected_family"] = family_frame["opening_band"].map(opening_band_route_map)
            family_frame = family_frame[family_frame["route_selected_family"] == family].copy()
            if family_frame.empty:
                continue
            family_frame["source_strategy_family"] = family
            trade_frames.append(family_frame)
        combined_trades_df = (
            pd.concat(trade_frames, ignore_index=True)
            if trade_frames
            else pd.DataFrame(columns=empty_columns)
        )
        combined_trades_df = combined_trades_df[
            combined_trades_df["source_strategy_family"].isin(valid_families)
        ].copy() if not combined_trades_df.empty else combined_trades_df
        summary, steps_df = simulate_trade_portfolio(
            combined_trades_df,
            sample_name=sample_name,
            strategy_family=routed_family_name,
            portfolio_scope=PORTFOLIO_SCOPE_ROUTED,
            strategy_family_members=routed_members,
            initial_bankroll=initial_bankroll,
            position_size_fraction=position_size_fraction,
            game_limit=game_limit,
        )
        summary_rows.append(summary)
        if not steps_df.empty:
            step_frames.append(steps_df)

    summary_df = pd.DataFrame(summary_rows, columns=PORTFOLIO_SUMMARY_COLUMNS)
    steps_df = pd.concat(step_frames, ignore_index=True) if step_frames else pd.DataFrame(columns=PORTFOLIO_STEP_COLUMNS)
    return summary_df, steps_df


def _portfolio_candidate_label(
    *,
    full_executed_trade_count: int,
    time_validation_executed_trade_count: int,
    random_holdout_executed_trade_count: int,
    full_ending_bankroll: float | None,
    time_validation_ending_bankroll: float | None,
    random_holdout_ending_bankroll: float | None,
    starting_bankroll: float,
    min_trade_count: int,
) -> tuple[str, str]:
    required_trades = max(1, int(min_trade_count))
    if full_executed_trade_count < required_trades:
        return "experimental", "below_min_trade_count"
    if time_validation_executed_trade_count <= 0 or random_holdout_executed_trade_count <= 0:
        return "experimental", "missing_portfolio_sample"
    if full_ending_bankroll is not None and time_validation_ending_bankroll is not None and random_holdout_ending_bankroll is not None:
        if (
            full_ending_bankroll > starting_bankroll
            and time_validation_ending_bankroll > starting_bankroll
            and random_holdout_ending_bankroll > starting_bankroll
        ):
            return "keep", "profitable_on_full_time_and_holdout"
        if (
            full_ending_bankroll <= starting_bankroll
            and time_validation_ending_bankroll <= starting_bankroll
            and random_holdout_ending_bankroll <= starting_bankroll
        ):
            return "drop", "non_profitable_on_full_time_and_holdout"
    return "experimental", "mixed_portfolio_signal"


def build_portfolio_candidate_freeze_frame(
    portfolio_summary_df: pd.DataFrame,
    registry: dict[str, StrategyDefinition],
    *,
    starting_bankroll: float,
    min_trade_count: int,
) -> pd.DataFrame:
    lookup = {
        (str(row["sample_name"]), str(row["strategy_family"])): row
        for row in portfolio_summary_df.to_dict(orient="records")
    }
    rows: list[dict[str, Any]] = []
    for family, definition in registry.items():
        full = lookup.get(("full_sample", family)) or {}
        time_validation = lookup.get(("time_validation", family)) or {}
        random_holdout = lookup.get(("random_holdout", family)) or {}
        label, reason = _portfolio_candidate_label(
            full_executed_trade_count=int(full.get("executed_trade_count") or 0),
            time_validation_executed_trade_count=int(time_validation.get("executed_trade_count") or 0),
            random_holdout_executed_trade_count=int(random_holdout.get("executed_trade_count") or 0),
            full_ending_bankroll=_safe_float(full.get("ending_bankroll")),
            time_validation_ending_bankroll=_safe_float(time_validation.get("ending_bankroll")),
            random_holdout_ending_bankroll=_safe_float(random_holdout.get("ending_bankroll")),
            starting_bankroll=normalize_portfolio_initial_bankroll(starting_bankroll),
            min_trade_count=min_trade_count,
        )
        rows.append(
            {
                "strategy_family": family,
                "entry_rule": definition.entry_rule,
                "exit_rule": definition.exit_rule,
                "candidate_label": label,
                "label_reason": reason,
                "full_executed_trade_count": int(full.get("executed_trade_count") or 0),
                "time_validation_executed_trade_count": int(time_validation.get("executed_trade_count") or 0),
                "random_holdout_executed_trade_count": int(random_holdout.get("executed_trade_count") or 0),
                "full_ending_bankroll": full.get("ending_bankroll"),
                "time_validation_ending_bankroll": time_validation.get("ending_bankroll"),
                "random_holdout_ending_bankroll": random_holdout.get("ending_bankroll"),
                "full_compounded_return": full.get("compounded_return"),
                "time_validation_compounded_return": time_validation.get("compounded_return"),
                "random_holdout_compounded_return": random_holdout.get("compounded_return"),
                "full_max_drawdown_pct": full.get("max_drawdown_pct"),
                "random_holdout_max_drawdown_pct": random_holdout.get("max_drawdown_pct"),
            }
        )
    return pd.DataFrame(rows, columns=PORTFOLIO_CANDIDATE_FREEZE_COLUMNS)


__all__ = [
    "COMBINED_KEEP_FAMILIES_PORTFOLIO",
    "PORTFOLIO_CANDIDATE_FREEZE_COLUMNS",
    "PORTFOLIO_SCOPE_COMBINED",
    "PORTFOLIO_SCOPE_ROUTED",
    "PORTFOLIO_SCOPE_SINGLE_FAMILY",
    "PORTFOLIO_STEP_COLUMNS",
    "PORTFOLIO_SUMMARY_COLUMNS",
    "build_combined_portfolio_benchmark_frames",
    "build_portfolio_benchmark_frames",
    "build_portfolio_candidate_freeze_frame",
    "build_routed_portfolio_benchmark_frames",
    "normalize_portfolio_game_limit",
    "normalize_portfolio_initial_bankroll",
    "normalize_portfolio_position_size_fraction",
    "simulate_trade_portfolio",
    "STATISTICAL_ROUTING_PORTFOLIO",
]
