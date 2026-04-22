from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import BACKTEST_TRADE_COLUMNS
from app.data.pipelines.daily.nba.analysis.backtests.master_router import (
    DEFAULT_MASTER_ROUTER_CORE_FAMILIES,
    DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES,
    DEFAULT_MASTER_ROUTER_SELECTION_SAMPLE,
    MASTER_ROUTER_DECISION_COLUMNS,
    MASTER_ROUTER_PORTFOLIO,
    build_master_router_selection_priors,
    build_master_router_trade_frame,
)
from app.data.pipelines.daily.nba.analysis.backtests.specs import BacktestResult, StrategyDefinition
from app.data.pipelines.daily.nba.analysis.contracts import (
    DEFAULT_BACKTEST_PORTFOLIO_CONCURRENCY_MODE,
    DEFAULT_BACKTEST_PORTFOLIO_GAME_LIMIT,
    DEFAULT_BACKTEST_PORTFOLIO_INITIAL_BANKROLL,
    DEFAULT_BACKTEST_PORTFOLIO_MAX_CONCURRENT_POSITIONS,
    DEFAULT_BACKTEST_PORTFOLIO_MIN_ORDER_DOLLARS,
    DEFAULT_BACKTEST_PORTFOLIO_MIN_SHARES,
    DEFAULT_BACKTEST_PORTFOLIO_POSITION_SIZE_FRACTION,
    DEFAULT_BACKTEST_PORTFOLIO_RANDOM_SLIPPAGE_MAX_CENTS,
    DEFAULT_BACKTEST_PORTFOLIO_RANDOM_SLIPPAGE_SEED,
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
    "portfolio_min_order_dollars",
    "portfolio_min_shares",
    "portfolio_max_concurrent_positions",
    "portfolio_concurrency_mode",
    "portfolio_random_slippage_max_cents",
    "portfolio_random_slippage_seed",
    "max_concurrent_positions_observed",
    "avg_executed_trade_return_with_slippage",
    "avg_executed_share_count",
    "first_entry_at",
    "last_exit_at",
    "skipped_concurrency_count",
    "skipped_min_order_count",
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
    "settled_at",
    "settlement_sequence",
    "signal_strength",
    "candidate_batch_size",
    "strategy_selection_rank",
    "gross_return_with_slippage",
    "entry_exec_price",
    "exit_exec_price",
    "random_entry_slippage_cents",
    "random_exit_slippage_cents",
    "portfolio_action",
    "skip_reason",
    "bankroll_before",
    "cash_before",
    "cash_after_entry",
    "stake_amount",
    "minimum_required_stake",
    "share_count",
    "profit_loss_amount",
    "bankroll_after",
    "peak_bankroll_after_step",
    "drawdown_amount_after_step",
    "drawdown_pct_after_step",
    "open_positions_before",
    "open_positions_after",
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
    if value is None or value == "" or pd.isna(value):
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


def normalize_portfolio_min_order_dollars(value: float | None) -> float:
    minimum = _safe_float(value)
    if minimum is None:
        return DEFAULT_BACKTEST_PORTFOLIO_MIN_ORDER_DOLLARS
    return max(0.01, minimum)


def normalize_portfolio_min_shares(value: float | None) -> float:
    minimum = _safe_float(value)
    if minimum is None:
        return DEFAULT_BACKTEST_PORTFOLIO_MIN_SHARES
    return max(0.0, minimum)


def normalize_portfolio_max_concurrent_positions(value: int | None) -> int:
    if value is None:
        return DEFAULT_BACKTEST_PORTFOLIO_MAX_CONCURRENT_POSITIONS
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return DEFAULT_BACKTEST_PORTFOLIO_MAX_CONCURRENT_POSITIONS
    return max(1, resolved)


def normalize_portfolio_concurrency_mode(value: str | None) -> str:
    resolved = str(value or "").strip().lower()
    if resolved == "shared_cash_equal_split":
        return resolved
    return DEFAULT_BACKTEST_PORTFOLIO_CONCURRENCY_MODE


def normalize_portfolio_random_slippage_max_cents(value: int | None) -> int:
    if value is None:
        return DEFAULT_BACKTEST_PORTFOLIO_RANDOM_SLIPPAGE_MAX_CENTS
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return DEFAULT_BACKTEST_PORTFOLIO_RANDOM_SLIPPAGE_MAX_CENTS
    return max(0, resolved)


def normalize_portfolio_random_slippage_seed(value: int | None) -> int:
    if value is None:
        return DEFAULT_BACKTEST_PORTFOLIO_RANDOM_SLIPPAGE_SEED
    try:
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_BACKTEST_PORTFOLIO_RANDOM_SLIPPAGE_SEED


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
        "entry_price",
        "signal_strength",
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


def _portfolio_equity(cash_balance: float, open_positions: list[dict[str, Any]]) -> float:
    reserved_capital = sum(float(position.get("stake_amount") or 0.0) for position in open_positions)
    return cash_balance + reserved_capital


def _minimum_required_stake(entry_price: float | None, *, min_order_dollars: float, min_shares: float) -> float:
    resolved_price = max(0.0, _safe_float(entry_price) or 0.0)
    share_floor = resolved_price * max(0.0, min_shares)
    return max(min_order_dollars, share_floor)


def _build_execution_rng(*, seed: int, sample_name: str, strategy_family: str) -> np.random.Generator:
    digest = hashlib.sha256(f"{seed}|{sample_name}|{strategy_family}".encode("utf-8")).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], byteorder="big", signed=False))


def _resolve_trade_execution(
    record: dict[str, Any],
    *,
    rng: np.random.Generator,
    random_slippage_max_cents: int,
) -> tuple[float, float, float, int, int]:
    entry_price = max(1e-6, _safe_float(record.get("entry_price")) or 0.0)
    exit_price_raw = _safe_float(record.get("exit_price"))
    fallback_trade_return = _safe_float(record.get("gross_return_with_slippage"))
    base_slippage = max(0.0, (_safe_float(record.get("slippage_cents")) or 0.0) / 100.0)
    random_entry_slippage_cents = int(rng.integers(0, random_slippage_max_cents + 1)) if random_slippage_max_cents > 0 else 0
    random_exit_slippage_cents = int(rng.integers(0, random_slippage_max_cents + 1)) if random_slippage_max_cents > 0 else 0
    entry_exec = min(0.999999, entry_price + base_slippage + (random_entry_slippage_cents / 100.0))
    if exit_price_raw is None:
        trade_return = fallback_trade_return or 0.0
        exit_exec = max(0.0, entry_exec * (1.0 + trade_return))
    else:
        exit_exec = max(0.0, exit_price_raw - base_slippage - (random_exit_slippage_cents / 100.0))
        trade_return = (exit_exec - entry_exec) / entry_exec if entry_exec > 0 else 0.0
    return trade_return, entry_exec, exit_exec, random_entry_slippage_cents, random_exit_slippage_cents


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
    min_order_dollars: float | None = None,
    min_shares: float | None = None,
    max_concurrent_positions: int | None = None,
    concurrency_mode: str | None = None,
    random_slippage_max_cents: int | None = None,
    random_slippage_seed: int | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    cash_balance = normalize_portfolio_initial_bankroll(initial_bankroll)
    fraction = normalize_portfolio_position_size_fraction(position_size_fraction)
    resolved_game_limit = normalize_portfolio_game_limit(game_limit)
    resolved_min_order_dollars = normalize_portfolio_min_order_dollars(min_order_dollars)
    resolved_min_shares = normalize_portfolio_min_shares(min_shares)
    resolved_max_concurrent_positions = normalize_portfolio_max_concurrent_positions(max_concurrent_positions)
    resolved_concurrency_mode = normalize_portfolio_concurrency_mode(concurrency_mode)
    resolved_random_slippage_max_cents = normalize_portfolio_random_slippage_max_cents(random_slippage_max_cents)
    resolved_random_slippage_seed = normalize_portfolio_random_slippage_seed(random_slippage_seed)
    resolved_family_members = _normalize_family_members(strategy_family, strategy_family_members)
    serialized_family_members = ",".join(resolved_family_members)
    execution_rng = _build_execution_rng(
        seed=resolved_random_slippage_seed,
        sample_name=sample_name,
        strategy_family=strategy_family,
    )
    work = _prepare_trade_frame(trades_df)
    limited, game_order = _apply_game_limit(work, game_limit=resolved_game_limit)

    bankroll = cash_balance
    peak_bankroll = bankroll
    min_bankroll = bankroll
    max_drawdown_amount = 0.0
    max_drawdown_pct = 0.0
    executed_trade_returns: list[float] = []
    executed_share_counts: list[float] = []
    step_rows: list[dict[str, Any]] = []
    open_positions: list[dict[str, Any]] = []
    executed_trade_count = 0
    skipped_overlap_count = 0
    skipped_bankroll_count = 0
    skipped_concurrency_count = 0
    skipped_min_order_count = 0
    max_concurrent_positions_observed = 0
    settlement_sequence = 0

    def settle_until(timestamp: pd.Timestamp | None) -> None:
        nonlocal cash_balance
        nonlocal bankroll
        nonlocal peak_bankroll
        nonlocal min_bankroll
        nonlocal max_drawdown_amount
        nonlocal max_drawdown_pct
        nonlocal settlement_sequence
        if not open_positions:
            return
        remaining: list[dict[str, Any]] = []
        to_settle: list[dict[str, Any]] = []
        for position in sorted(
            open_positions,
            key=lambda item: (
                pd.to_datetime(item.get("exit_at"), errors="coerce", utc=True),
                int(item.get("trade_sequence") or 0),
            ),
        ):
            exit_at = pd.to_datetime(position.get("exit_at"), errors="coerce", utc=True)
            if timestamp is None or (pd.notna(exit_at) and exit_at <= timestamp):
                to_settle.append(position)
            else:
                remaining.append(position)
        if not to_settle:
            return
        open_positions.clear()
        open_positions.extend(remaining)
        for position in to_settle:
            cash_balance += float(position.get("stake_amount") or 0.0) + float(position.get("profit_loss_amount") or 0.0)
            bankroll = _portfolio_equity(cash_balance, open_positions)
            peak_bankroll = max(peak_bankroll, bankroll)
            min_bankroll = min(min_bankroll, bankroll)
            drawdown_amount = max(0.0, peak_bankroll - bankroll)
            drawdown_pct = drawdown_amount / peak_bankroll if peak_bankroll > 0 else 0.0
            max_drawdown_amount = max(max_drawdown_amount, drawdown_amount)
            max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)
            settlement_sequence += 1
            step_rows[position["step_index"]].update(
                {
                    "settled_at": _serialise_scalar(position.get("exit_at")),
                    "settlement_sequence": settlement_sequence,
                    "bankroll_after": bankroll,
                    "peak_bankroll_after_step": peak_bankroll,
                    "drawdown_amount_after_step": drawdown_amount,
                    "drawdown_pct_after_step": drawdown_pct,
                    "open_positions_after": len(open_positions),
                }
            )

    limited_records = limited.to_dict(orient="records")
    trade_sequence = 0
    index = 0
    while index < len(limited_records):
        row = limited_records[index]
        entry_at = pd.to_datetime(row.get("entry_at"), errors="coerce", utc=True)
        batch_records: list[dict[str, Any]] = []
        while index < len(limited_records):
            candidate = limited_records[index]
            candidate_entry_at = pd.to_datetime(candidate.get("entry_at"), errors="coerce", utc=True)
            if (
                (pd.isna(entry_at) and pd.isna(candidate_entry_at))
                or (pd.notna(entry_at) and pd.notna(candidate_entry_at) and candidate_entry_at == entry_at)
            ):
                batch_records.append(candidate)
                index += 1
                continue
            break

        settle_until(entry_at)
        equity_before_batch = _portfolio_equity(cash_balance, open_positions)
        open_positions_before = len(open_positions)
        available_slots = max(0, resolved_max_concurrent_positions - open_positions_before)
        ranked_batch = sorted(
            batch_records,
            key=lambda item: (
                _safe_float(item.get("signal_strength")) is not None,
                _safe_float(item.get("signal_strength")) or float("-inf"),
                str(item.get("source_strategy_family") or strategy_family),
                str(item.get("game_id") or ""),
                str(item.get("team_side") or ""),
            ),
            reverse=True,
        )
        selected_records = ranked_batch[:available_slots] if available_slots > 0 else []
        selected_count = len(selected_records)
        stake_pool = cash_balance * fraction if fraction > 0 else 0.0
        per_trade_budget = (
            (stake_pool / selected_count)
            if selected_count > 0 and resolved_concurrency_mode == "shared_cash_equal_split"
            else 0.0
        )
        selected_keys = {id(record): rank for rank, record in enumerate(selected_records, start=1)}

        for batch_rank, record in enumerate(ranked_batch, start=1):
            trade_sequence += 1
            exit_at = pd.to_datetime(record.get("exit_at"), errors="coerce", utc=True)
            (
                trade_return,
                entry_exec_price,
                exit_exec_price,
                random_entry_slippage_cents,
                random_exit_slippage_cents,
            ) = _resolve_trade_execution(
                record,
                rng=execution_rng,
                random_slippage_max_cents=resolved_random_slippage_max_cents,
            )
            signal_strength = _safe_float(record.get("signal_strength"))
            step_index = len(step_rows)
            equity_before = _portfolio_equity(cash_balance, open_positions)
            cash_before = cash_balance
            open_positions_before_current = len(open_positions)
            minimum_required_stake = _minimum_required_stake(
                _safe_float(record.get("entry_price")),
                min_order_dollars=resolved_min_order_dollars,
                min_shares=resolved_min_shares,
            )
            action = "skipped"
            skip_reason: str | None = None
            stake_amount = 0.0
            share_count = 0.0
            profit_loss_amount = 0.0
            cash_after_entry = cash_before
            bankroll_after = equity_before
            peak_after = peak_bankroll
            drawdown_amount_after = max(0.0, peak_bankroll - bankroll_after)
            drawdown_pct_after = drawdown_amount_after / peak_bankroll if peak_bankroll > 0 else 0.0
            open_positions_after = len(open_positions)
            settlement_value: Any = _serialise_scalar(entry_at)
            settlement_value_sequence: int | None = None

            if id(record) not in selected_keys:
                skip_reason = "concurrency"
                skipped_concurrency_count += 1
            elif equity_before <= 0 or cash_before <= 0 or fraction <= 0:
                skip_reason = "bankroll"
                skipped_bankroll_count += 1
            else:
                target_stake = max(per_trade_budget, minimum_required_stake)
                if target_stake > cash_before + 1e-9:
                    skip_reason = "min_order"
                    skipped_min_order_count += 1
                else:
                    stake_amount = target_stake
                    share_count = stake_amount / max(_safe_float(record.get("entry_price")) or 1.0, 1e-6)
                    profit_loss_amount = stake_amount * trade_return
                    cash_balance = max(0.0, cash_before - stake_amount)
                    cash_after_entry = cash_balance
                    action = "executed"
                    executed_trade_count += 1
                    executed_trade_returns.append(trade_return)
                    executed_share_counts.append(share_count)
                    open_positions.append(
                        {
                            "step_index": step_index,
                            "trade_sequence": trade_sequence,
                            "entry_at": entry_at,
                            "exit_at": exit_at if pd.notna(exit_at) else entry_at,
                            "stake_amount": stake_amount,
                            "profit_loss_amount": profit_loss_amount,
                        }
                    )
                    max_concurrent_positions_observed = max(max_concurrent_positions_observed, len(open_positions))
                    open_positions_after = len(open_positions)
                    bankroll_after = _portfolio_equity(cash_balance, open_positions)
                    settlement_value = None
            if action == "skipped":
                bankroll = _portfolio_equity(cash_balance, open_positions)
                peak_bankroll = max(peak_bankroll, bankroll)
                min_bankroll = min(min_bankroll, bankroll)
                drawdown_amount_after = max(0.0, peak_bankroll - bankroll)
                drawdown_pct_after = drawdown_amount_after / peak_bankroll if peak_bankroll > 0 else 0.0
                max_drawdown_amount = max(max_drawdown_amount, drawdown_amount_after)
                max_drawdown_pct = max(max_drawdown_pct, drawdown_pct_after)
                bankroll_after = bankroll
                peak_after = peak_bankroll

            step_rows.append(
                {
                    "sample_name": sample_name,
                    "strategy_family": strategy_family,
                    "portfolio_scope": portfolio_scope,
                    "strategy_family_members": serialized_family_members,
                    "source_strategy_family": record.get("source_strategy_family") or strategy_family,
                    "trade_sequence": trade_sequence,
                    "game_sequence": record.get("game_sequence"),
                    "game_id": record.get("game_id"),
                    "team_side": record.get("team_side"),
                    "team_slug": record.get("team_slug"),
                    "opponent_team_slug": record.get("opponent_team_slug"),
                    "entry_at": _serialise_scalar(entry_at),
                    "exit_at": _serialise_scalar(exit_at),
                    "settled_at": settlement_value,
                    "settlement_sequence": settlement_value_sequence,
                    "signal_strength": signal_strength,
                    "candidate_batch_size": len(batch_records),
                    "strategy_selection_rank": selected_keys.get(id(record), batch_rank),
                    "gross_return_with_slippage": trade_return,
                    "entry_exec_price": entry_exec_price,
                    "exit_exec_price": exit_exec_price,
                    "random_entry_slippage_cents": random_entry_slippage_cents,
                    "random_exit_slippage_cents": random_exit_slippage_cents,
                    "portfolio_action": action,
                    "skip_reason": skip_reason,
                    "bankroll_before": equity_before,
                    "cash_before": cash_before,
                    "cash_after_entry": cash_after_entry,
                    "stake_amount": stake_amount,
                    "minimum_required_stake": minimum_required_stake,
                    "share_count": share_count,
                    "profit_loss_amount": profit_loss_amount,
                    "bankroll_after": bankroll_after,
                    "peak_bankroll_after_step": peak_after,
                    "drawdown_amount_after_step": drawdown_amount_after,
                    "drawdown_pct_after_step": drawdown_pct_after,
                    "open_positions_before": open_positions_before_current,
                    "open_positions_after": open_positions_after,
                }
            )

    settle_until(None)
    bankroll = _portfolio_equity(cash_balance, open_positions)

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
        "skipped_concurrency_count": skipped_concurrency_count,
        "skipped_min_order_count": skipped_min_order_count,
        "games_considered": int(len(game_order)),
        "position_size_fraction": fraction,
        "game_limit": resolved_game_limit,
        "portfolio_min_order_dollars": resolved_min_order_dollars,
        "portfolio_min_shares": resolved_min_shares,
        "portfolio_max_concurrent_positions": resolved_max_concurrent_positions,
        "portfolio_concurrency_mode": resolved_concurrency_mode,
        "portfolio_random_slippage_max_cents": resolved_random_slippage_max_cents,
        "portfolio_random_slippage_seed": resolved_random_slippage_seed,
        "max_concurrent_positions_observed": max_concurrent_positions_observed,
        "avg_executed_trade_return_with_slippage": (
            sum(executed_trade_returns) / len(executed_trade_returns) if executed_trade_returns else None
        ),
        "avg_executed_share_count": (
            sum(executed_share_counts) / len(executed_share_counts) if executed_share_counts else None
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
    min_order_dollars: float | None,
    min_shares: float | None,
    max_concurrent_positions: int | None,
    concurrency_mode: str | None,
    random_slippage_max_cents: int | None,
    random_slippage_seed: int | None,
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
                min_order_dollars=min_order_dollars,
                min_shares=min_shares,
                max_concurrent_positions=max_concurrent_positions,
                concurrency_mode=concurrency_mode,
                random_slippage_max_cents=random_slippage_max_cents,
                random_slippage_seed=random_slippage_seed,
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
    min_order_dollars: float | None,
    min_shares: float | None,
    max_concurrent_positions: int | None,
    concurrency_mode: str | None,
    random_slippage_max_cents: int | None = None,
    random_slippage_seed: int | None = None,
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
            min_order_dollars=min_order_dollars,
            min_shares=min_shares,
            max_concurrent_positions=max_concurrent_positions,
            concurrency_mode=concurrency_mode,
            random_slippage_max_cents=random_slippage_max_cents,
            random_slippage_seed=random_slippage_seed,
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
    min_order_dollars: float | None,
    min_shares: float | None,
    max_concurrent_positions: int | None,
    concurrency_mode: str | None,
    random_slippage_max_cents: int | None = None,
    random_slippage_seed: int | None = None,
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
            min_order_dollars=min_order_dollars,
            min_shares=min_shares,
            max_concurrent_positions=max_concurrent_positions,
            concurrency_mode=concurrency_mode,
            random_slippage_max_cents=random_slippage_max_cents,
            random_slippage_seed=random_slippage_seed,
        )
        summary_rows.append(summary)
        if not steps_df.empty:
            step_frames.append(steps_df)

    summary_df = pd.DataFrame(summary_rows, columns=PORTFOLIO_SUMMARY_COLUMNS)
    steps_df = pd.concat(step_frames, ignore_index=True) if step_frames else pd.DataFrame(columns=PORTFOLIO_STEP_COLUMNS)
    return summary_df, steps_df


def build_master_router_portfolio_benchmark_frames(
    split_results: dict[str, BacktestResult],
    *,
    initial_bankroll: float,
    position_size_fraction: float,
    game_limit: int | None,
    min_order_dollars: float | None,
    min_shares: float | None,
    max_concurrent_positions: int | None,
    concurrency_mode: str | None,
    random_slippage_max_cents: int | None = None,
    random_slippage_seed: int | None = None,
    split_order: tuple[str, ...],
    selection_sample_name: str = DEFAULT_MASTER_ROUTER_SELECTION_SAMPLE,
    core_strategy_families: tuple[str, ...] | list[str] = DEFAULT_MASTER_ROUTER_CORE_FAMILIES,
    extra_strategy_families: tuple[str, ...] | list[str] = DEFAULT_MASTER_ROUTER_EXTRA_FAMILIES,
    master_router_family_name: str = MASTER_ROUTER_PORTFOLIO,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    selection_result = split_results.get(selection_sample_name)
    priors = build_master_router_selection_priors(
        selection_result,
        core_strategy_families=core_strategy_families,
    )
    core_members = tuple(str(family) for family in core_strategy_families if str(family))
    extra_members = tuple(str(family) for family in extra_strategy_families if str(family))
    if not priors or not core_members:
        return (
            pd.DataFrame(columns=PORTFOLIO_SUMMARY_COLUMNS),
            pd.DataFrame(columns=PORTFOLIO_STEP_COLUMNS),
            pd.DataFrame(columns=MASTER_ROUTER_DECISION_COLUMNS),
        )

    summary_rows: list[dict[str, Any]] = []
    step_frames: list[pd.DataFrame] = []
    decision_frames: list[pd.DataFrame] = []
    routed_members = (*core_members, *extra_members)
    for sample_name in split_order:
        split_result = split_results.get(sample_name)
        if split_result is None:
            continue
        combined_trades_df, decisions_df = build_master_router_trade_frame(
            split_result,
            sample_name=sample_name,
            selection_sample_name=selection_sample_name,
            priors=priors,
            core_strategy_families=core_members,
            extra_strategy_families=extra_members,
        )
        summary, steps_df = simulate_trade_portfolio(
            combined_trades_df,
            sample_name=sample_name,
            strategy_family=master_router_family_name,
            portfolio_scope=PORTFOLIO_SCOPE_ROUTED,
            strategy_family_members=routed_members,
            initial_bankroll=initial_bankroll,
            position_size_fraction=position_size_fraction,
            game_limit=game_limit,
            min_order_dollars=min_order_dollars,
            min_shares=min_shares,
            max_concurrent_positions=max_concurrent_positions,
            concurrency_mode=concurrency_mode,
            random_slippage_max_cents=random_slippage_max_cents,
            random_slippage_seed=random_slippage_seed,
        )
        summary_rows.append(summary)
        if not steps_df.empty:
            step_frames.append(steps_df)
        if not decisions_df.empty:
            decision_frames.append(decisions_df)

    summary_df = pd.DataFrame(summary_rows, columns=PORTFOLIO_SUMMARY_COLUMNS)
    steps_df = pd.concat(step_frames, ignore_index=True) if step_frames else pd.DataFrame(columns=PORTFOLIO_STEP_COLUMNS)
    decisions_df = pd.concat(decision_frames, ignore_index=True) if decision_frames else pd.DataFrame(columns=MASTER_ROUTER_DECISION_COLUMNS)
    return summary_df, steps_df, decisions_df


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
    "build_master_router_portfolio_benchmark_frames",
    "build_portfolio_benchmark_frames",
    "build_portfolio_candidate_freeze_frame",
    "build_routed_portfolio_benchmark_frames",
    "normalize_portfolio_concurrency_mode",
    "normalize_portfolio_game_limit",
    "normalize_portfolio_initial_bankroll",
    "normalize_portfolio_max_concurrent_positions",
    "normalize_portfolio_min_order_dollars",
    "normalize_portfolio_min_shares",
    "normalize_portfolio_position_size_fraction",
    "normalize_portfolio_random_slippage_max_cents",
    "normalize_portfolio_random_slippage_seed",
    "simulate_trade_portfolio",
    "STATISTICAL_ROUTING_PORTFOLIO",
]
