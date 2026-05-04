from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from app.api.db import to_jsonable
from app.data.databases.postgres import managed_connection
from app.data.pipelines.daily.nba.analysis.artifacts import ensure_output_dir, write_frame, write_json, write_markdown
from app.data.pipelines.daily.nba.analysis.backtests.specs import BacktestResult, BenchmarkRunResult, StrategyDefinition, TradeSelection
from app.data.pipelines.daily.nba.analysis.contracts import (
    ANALYSIS_VERSION,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_REVERSION_DRAWDOWN,
    DEFAULT_REVERSION_EXIT_BUFFER,
    DEFAULT_REVERSION_OPEN_THRESHOLD,
    DEFAULT_SEASON,
    DEFAULT_SEASON_PHASE,
    DEFAULT_TAKE_PROFIT_EXIT_PRICE,
    DEFAULT_WINNER_DEFINITION_BREAK,
    DEFAULT_WINNER_DEFINITION_ENTRY,
    BacktestRunRequest,
)

BACKTEST_TRADE_COLUMNS = (
    "season",
    "season_phase",
    "analysis_version",
    "strategy_family",
    "entry_rule",
    "exit_rule",
    "game_id",
    "team_side",
    "team_slug",
    "opponent_team_slug",
    "opening_band",
    "period_label",
    "score_diff_bucket",
    "context_bucket",
    "context_tags_json",
    "entry_metadata_json",
    "signal_strength",
    "entry_state_index",
    "exit_state_index",
    "entry_at",
    "exit_at",
    "entry_price",
    "exit_price",
    "gross_return",
    "gross_return_with_slippage",
    "max_favorable_excursion_after_entry",
    "max_adverse_excursion_after_entry",
    "hold_time_seconds",
    "slippage_cents",
)

BACKTEST_FAMILY_SUMMARY_COLUMNS = (
    "strategy_family",
    "entry_rule",
    "exit_rule",
    "description",
    "comparator_group",
    "tags_json",
    "trade_count",
    "win_rate",
    "avg_gross_return",
    "avg_gross_return_with_slippage",
    "avg_hold_time_seconds",
    "avg_mfe_after_entry",
    "avg_mae_after_entry",
)

BACKTEST_CONTEXT_SUMMARY_COLUMNS = (
    "strategy_family",
    "period_label",
    "opening_band",
    "context_bucket",
    "trade_count",
    "win_rate",
    "avg_gross_return_with_slippage",
    "avg_hold_time_seconds",
)

BACKTEST_TRACE_STATE_COLUMNS = (
    "state_index",
    "event_at",
    "period_label",
    "score_diff",
    "score_diff_bucket",
    "context_bucket",
    "team_price",
    "opening_price",
    "price_delta_from_open",
    "net_points_last_5_events",
)

_STATE_PANEL_NUMERIC_COLUMNS = (
    "state_index",
    "event_index",
    "period",
    "clock_elapsed_seconds",
    "seconds_to_game_end",
    "score_for",
    "score_against",
    "score_diff",
    "points_scored",
    "delta_for",
    "delta_against",
    "lead_changes_so_far",
    "team_points_last_5_events",
    "opponent_points_last_5_events",
    "net_points_last_5_events",
    "opening_price",
    "team_price",
    "price_delta_from_open",
    "abs_price_delta_from_open",
    "gap_before_seconds",
    "gap_after_seconds",
)


EntrySelector = Callable[[pd.DataFrame], TradeSelection | None]
ExitSelector = Callable[[pd.DataFrame, TradeSelection], int | None]


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _prepare_state_panel_frame(state_df: pd.DataFrame) -> pd.DataFrame:
    if state_df.empty:
        return state_df.copy()
    work = state_df.copy()
    for column in ("computed_at", "event_at"):
        if column in work.columns:
            work[column] = pd.to_datetime(work[column], errors="coerce", utc=True)
    if "game_date" in work.columns:
        work["game_date"] = pd.to_datetime(work["game_date"], errors="coerce").dt.date
    for column in _STATE_PANEL_NUMERIC_COLUMNS:
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    sort_columns = [column for column in ("game_date", "game_id", "team_side", "state_index", "event_at") if column in work.columns]
    if sort_columns:
        work = work.sort_values(sort_columns, kind="mergesort").reset_index(drop=True)
    return work


def _query_df(connection: Any, query: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def load_analysis_backtest_state_panel_df(
    connection: Any,
    *,
    season: str,
    season_phase: str,
    season_phases: tuple[str, ...] | None = None,
    analysis_version: str,
) -> pd.DataFrame:
    resolved_season_phases = tuple(str(value).strip() for value in (season_phases or ()) if str(value).strip())
    if resolved_season_phases:
        placeholders = ", ".join(["%s"] * len(resolved_season_phases))
        query = f"""
        SELECT *
        FROM nba.nba_analysis_state_panel
        WHERE season = %s AND season_phase IN ({placeholders}) AND analysis_version = %s
        ORDER BY game_date ASC NULLS LAST, game_id ASC, team_side ASC, state_index ASC;
        """
        params: tuple[Any, ...] = (season, *resolved_season_phases, analysis_version)
    else:
        query = """
        SELECT *
        FROM nba.nba_analysis_state_panel
        WHERE season = %s AND season_phase = %s AND analysis_version = %s
        ORDER BY game_date ASC NULLS LAST, game_id ASC, team_side ASC, state_index ASC;
        """
        params = (season, season_phase, analysis_version)
    frame = _query_df(
        connection,
        query,
        params,
    )
    return _prepare_state_panel_frame(frame)


def _trade_row(
    group: pd.DataFrame,
    *,
    entry_index: int,
    exit_index: int,
    strategy_family: str,
    entry_rule: str,
    exit_rule: str,
    slippage_cents: int,
    selection_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry = group.iloc[entry_index]
    exit_row = group.iloc[exit_index]
    metadata = dict(selection_metadata or {})
    entry_price = float(entry["team_price"])
    exit_price = float(exit_row["team_price"])
    mfe_after_entry = float(group.iloc[entry_index:]["team_price"].max() - entry_price)
    mae_after_entry = float(entry_price - group.iloc[entry_index:]["team_price"].min())
    gross_return = (exit_price - entry_price) / entry_price if entry_price > 0 else 0.0
    slippage = max(0, int(slippage_cents)) / 100.0
    entry_exec = min(0.999999, entry_price + slippage)
    exit_exec = max(0.0, exit_price - slippage)
    net_return = (exit_exec - entry_exec) / entry_exec if entry_exec > 0 else 0.0
    entry_at = pd.to_datetime(entry["event_at"], utc=True)
    exit_at = pd.to_datetime(exit_row["event_at"], utc=True)
    return {
        "season": entry["season"],
        "season_phase": entry["season_phase"],
        "analysis_version": entry["analysis_version"],
        "strategy_family": strategy_family,
        "entry_rule": entry_rule,
        "exit_rule": exit_rule,
        "game_id": entry["game_id"],
        "team_side": entry["team_side"],
        "team_slug": entry["team_slug"],
        "opponent_team_slug": entry["opponent_team_slug"],
        "opening_band": entry["opening_band"],
        "period_label": entry["period_label"],
        "score_diff_bucket": entry["score_diff_bucket"],
        "context_bucket": entry["context_bucket"],
        "context_tags_json": {
            "opening_band": entry["opening_band"],
            "period_label": entry["period_label"],
            "score_diff_bucket": entry["score_diff_bucket"],
            "context_bucket": entry["context_bucket"],
        },
        "entry_metadata_json": metadata,
        "signal_strength": _safe_float(metadata.get("signal_strength")),
        "entry_state_index": int(entry["state_index"]),
        "exit_state_index": int(exit_row["state_index"]),
        "entry_at": entry_at,
        "exit_at": exit_at,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross_return": gross_return,
        "gross_return_with_slippage": net_return,
        "max_favorable_excursion_after_entry": mfe_after_entry,
        "max_adverse_excursion_after_entry": mae_after_entry,
        "hold_time_seconds": max(0.0, (exit_at - entry_at).total_seconds()),
        "slippage_cents": int(slippage_cents),
    }


def _take_profit_exit_index(group: pd.DataFrame, *, entry_index: int) -> int | None:
    if "team_price" not in group.columns:
        return None
    future_prices = pd.to_numeric(group.iloc[entry_index + 1 :]["team_price"], errors="coerce")
    hits = future_prices[future_prices >= DEFAULT_TAKE_PROFIT_EXIT_PRICE]
    if hits.empty:
        return None
    return int(hits.index[0])


def simulate_trade_loop(
    state_df: pd.DataFrame,
    *,
    strategy_family: str,
    entry_rule: str,
    exit_rule: str,
    slippage_cents: int,
    entry_selector: EntrySelector,
    exit_selector: ExitSelector,
) -> list[dict[str, Any]]:
    if state_df.empty:
        return []
    work = _prepare_state_panel_frame(state_df)
    trades: list[dict[str, Any]] = []
    for (_, _), group in work.groupby(["game_id", "team_side"], sort=True):
        ordered = group.sort_values("state_index", kind="mergesort").reset_index(drop=True)
        if ordered.empty:
            continue
        selection = entry_selector(ordered)
        if selection is None:
            continue
        exit_index = exit_selector(ordered, selection)
        take_profit_index = _take_profit_exit_index(ordered, entry_index=selection.entry_index)
        if take_profit_index is not None and (exit_index is None or take_profit_index < exit_index):
            exit_index = take_profit_index
            selection.metadata["exit_override"] = "take_profit_95"
            selection.metadata["target_price"] = DEFAULT_TAKE_PROFIT_EXIT_PRICE
        if exit_index is None or exit_index <= selection.entry_index:
            continue
        trades.append(
            _trade_row(
                ordered,
                entry_index=selection.entry_index,
                exit_index=exit_index,
                strategy_family=strategy_family,
                entry_rule=entry_rule,
                exit_rule=exit_rule,
                slippage_cents=slippage_cents,
                selection_metadata=selection.metadata,
            )
        )
    return trades


def _summarize_trades(trades_df: pd.DataFrame) -> dict[str, Any]:
    if trades_df.empty:
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
        "trade_count": int(len(trades_df)),
        "win_rate": float((trades_df["gross_return"] > 0).mean()),
        "avg_gross_return": float(trades_df["gross_return"].mean()),
        "median_gross_return": float(trades_df["gross_return"].median()),
        "avg_gross_return_with_slippage": float(trades_df["gross_return_with_slippage"].mean()),
        "avg_hold_time_seconds": float(trades_df["hold_time_seconds"].mean()),
        "avg_mfe_after_entry": float(trades_df["max_favorable_excursion_after_entry"].mean()),
        "avg_mae_after_entry": float(trades_df["max_adverse_excursion_after_entry"].mean()),
    }


def _format_num(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _format_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.1f}%"


def _render_backtest_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# NBA Analysis Backtests",
        "",
        f"- Season: `{payload['season']}`",
        f"- Season phase: `{payload['season_phase']}`",
        f"- Analysis version: `{payload['analysis_version']}`",
        f"- State rows considered: `{payload.get('state_rows_considered')}`",
        f"- Games considered: `{payload.get('games_considered')}`",
        "",
    ]
    for family, summary in (payload.get("families") or {}).items():
        registry_meta = (payload.get("registry") or {}).get(family) or {}
        lines.extend(
            [
                f"## {family}",
                "",
                f"- Description: `{registry_meta.get('description', 'n/a')}`",
                f"- Entry rule: `{registry_meta.get('entry_rule', 'n/a')}`",
                f"- Exit rule: `{registry_meta.get('exit_rule', 'n/a')}`",
                f"- Trade count: `{summary.get('trade_count')}`",
                f"- Win rate: `{_format_pct(summary.get('win_rate'))}`",
                f"- Average gross return: `{_format_num(summary.get('avg_gross_return'))}`",
                f"- Average gross return with slippage: `{_format_num(summary.get('avg_gross_return_with_slippage'))}`",
                f"- Average hold time seconds: `{_format_num(summary.get('avg_hold_time_seconds'))}`",
                "",
            ]
        )
    return "\n".join(lines)


def _registry_payload(registry: dict[str, StrategyDefinition]) -> dict[str, Any]:
    return {
        family: {
            "family": definition.family,
            "entry_rule": definition.entry_rule,
            "exit_rule": definition.exit_rule,
            "description": definition.description,
            "comparator_group": definition.comparator_group,
            "tags": list(definition.tags),
        }
        for family, definition in registry.items()
    }


def _build_family_summary_frame(
    family_summaries: dict[str, Any],
    registry: dict[str, StrategyDefinition],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for family, definition in registry.items():
        summary = family_summaries.get(family) or {}
        rows.append(
            {
                "strategy_family": family,
                "entry_rule": definition.entry_rule,
                "exit_rule": definition.exit_rule,
                "description": definition.description,
                "comparator_group": definition.comparator_group,
                "tags_json": list(definition.tags),
                "trade_count": summary.get("trade_count"),
                "win_rate": summary.get("win_rate"),
                "avg_gross_return": summary.get("avg_gross_return"),
                "avg_gross_return_with_slippage": summary.get("avg_gross_return_with_slippage"),
                "avg_hold_time_seconds": summary.get("avg_hold_time_seconds"),
                "avg_mfe_after_entry": summary.get("avg_mfe_after_entry"),
                "avg_mae_after_entry": summary.get("avg_mae_after_entry"),
            }
        )
    return pd.DataFrame(rows, columns=BACKTEST_FAMILY_SUMMARY_COLUMNS)


def _build_context_summary_frame(trades_df: pd.DataFrame, *, family: str) -> pd.DataFrame:
    if trades_df.empty:
        return pd.DataFrame(columns=BACKTEST_CONTEXT_SUMMARY_COLUMNS)
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
    summary.insert(0, "strategy_family", family)
    return summary[list(BACKTEST_CONTEXT_SUMMARY_COLUMNS)]


def _build_trade_extremes_frame(trades_df: pd.DataFrame, *, ascending: bool) -> pd.DataFrame:
    if trades_df.empty:
        return trades_df.copy()
    return (
        trades_df.sort_values(
            ["gross_return_with_slippage", "hold_time_seconds"],
            ascending=[ascending, True],
            kind="mergesort",
        )
        .head(5)
        .reset_index(drop=True)
    )


def _build_trade_traces(
    state_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    *,
    family: str,
) -> list[dict[str, Any]]:
    if trades_df.empty or state_df.empty:
        return []

    ranked = pd.concat(
        [
            _build_trade_extremes_frame(trades_df, ascending=False),
            _build_trade_extremes_frame(trades_df, ascending=True),
        ],
        ignore_index=True,
    ).drop_duplicates(subset=["game_id", "team_side", "entry_state_index", "exit_state_index"])
    traces: list[dict[str, Any]] = []
    for _, trade in ranked.iterrows():
        group = state_df[
            (state_df["game_id"] == trade["game_id"])
            & (state_df["team_side"] == trade["team_side"])
        ].sort_values("state_index", kind="mergesort")
        if group.empty:
            continue
        start_index = max(0, int(trade["entry_state_index"]) - 1)
        end_index = int(trade["exit_state_index"]) + 2
        trace_states = group[
            (group["state_index"] >= start_index) & (group["state_index"] <= end_index)
        ][list(BACKTEST_TRACE_STATE_COLUMNS)].copy()
        if "event_at" in trace_states.columns:
            trace_states["event_at"] = pd.to_datetime(trace_states["event_at"], errors="coerce", utc=True)
        traces.append(
            {
                "strategy_family": family,
                "game_id": trade["game_id"],
                "team_side": trade["team_side"],
                "team_slug": trade["team_slug"],
                "entry_state_index": int(trade["entry_state_index"]),
                "exit_state_index": int(trade["exit_state_index"]),
                "entry_at": trade["entry_at"],
                "exit_at": trade["exit_at"],
                "gross_return_with_slippage": trade["gross_return_with_slippage"],
                "states": to_jsonable(trace_states.to_dict(orient="records")),
            }
        )
    return traces


def build_backtest_result(state_df: pd.DataFrame, request: BacktestRunRequest) -> BacktestResult:
    from app.data.pipelines.daily.nba.analysis.backtests.registry import resolve_strategy_registry

    work = _prepare_state_panel_frame(state_df)
    strategy_registry = resolve_strategy_registry(request.strategy_family, strategy_group=request.strategy_group)
    if work.empty:
        payload = {
            "season": request.season,
            "season_phase": request.season_phase,
            "analysis_version": request.analysis_version,
            "slippage_cents": int(request.slippage_cents),
            "state_rows_considered": 0,
            "games_considered": 0,
            "families": {},
            "registry": _registry_payload(strategy_registry),
            "error": "state_panel_empty",
        }
        empty_frames: dict[str, pd.DataFrame] = {}
        return BacktestResult(payload=payload, trade_frames=empty_frames, state_df=work, strategy_registry=strategy_registry)

    family_summaries: dict[str, Any] = {}
    family_trades: dict[str, pd.DataFrame] = {}
    for family, definition in strategy_registry.items():
        trades = definition.simulator(work, slippage_cents=request.slippage_cents)
        trades_df = pd.DataFrame(trades, columns=BACKTEST_TRADE_COLUMNS)
        if not trades_df.empty:
            trades_df = trades_df.sort_values(
                ["game_id", "team_side", "entry_state_index", "exit_state_index"],
                kind="mergesort",
            ).reset_index(drop=True)
        family_trades[family] = trades_df
        family_summaries[family] = _summarize_trades(trades_df)

    payload = {
        "season": request.season,
        "season_phase": request.season_phase,
        "analysis_version": request.analysis_version,
        "slippage_cents": int(request.slippage_cents),
        "state_rows_considered": int(len(work)),
        "games_considered": int(work["game_id"].nunique()) if "game_id" in work.columns else 0,
        "families": family_summaries,
        "registry": _registry_payload(strategy_registry),
        "artifacts": {},
    }
    return BacktestResult(
        payload=payload,
        trade_frames=family_trades,
        state_df=work,
        strategy_registry=strategy_registry,
    )


def write_backtest_artifacts(result: BacktestResult, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = result.payload
    payload["artifacts"] = {}
    family_summary_df = _build_family_summary_frame(result.payload.get("families") or {}, result.strategy_registry)
    payload["artifacts"].update(
        {f"family_summary_{key}": value for key, value in write_frame(output_dir / "family_summary", family_summary_df).items()}
    )
    for family, trades_df in result.trade_frames.items():
        payload["artifacts"].update(
            {f"{family}_{key}": value for key, value in write_frame(output_dir / f"{family}_trades", trades_df).items()}
        )
        best_df = _build_trade_extremes_frame(trades_df, ascending=False)
        worst_df = _build_trade_extremes_frame(trades_df, ascending=True)
        context_summary_df = _build_context_summary_frame(trades_df, family=family)
        payload["artifacts"].update(
            {f"{family}_best_trades_{key}": value for key, value in write_frame(output_dir / f"{family}_best_trades", best_df).items()}
        )
        payload["artifacts"].update(
            {f"{family}_worst_trades_{key}": value for key, value in write_frame(output_dir / f"{family}_worst_trades", worst_df).items()}
        )
        payload["artifacts"].update(
            {
                f"{family}_context_summary_{key}": value
                for key, value in write_frame(output_dir / f"{family}_context_summary", context_summary_df).items()
            }
        )
        payload["artifacts"][f"{family}_trade_traces_json"] = write_json(
            output_dir / f"{family}_trade_traces.json",
            _build_trade_traces(result.state_df, trades_df, family=family),
        )
    payload["artifacts"]["json"] = write_json(output_dir / "run_analysis_backtests.json", payload)
    payload["artifacts"]["markdown"] = write_markdown(output_dir / "run_analysis_backtests.md", _render_backtest_markdown(payload))
    return to_jsonable(payload)


def build_benchmark_run_result(state_df: pd.DataFrame, request: BacktestRunRequest) -> BenchmarkRunResult:
    from app.data.pipelines.daily.nba.analysis.backtests.benchmarking import build_benchmark_run_result as _build_benchmark_run_result

    return _build_benchmark_run_result(state_df, request)


def write_benchmark_artifacts(result: BenchmarkRunResult, output_dir: Path) -> dict[str, Any]:
    from app.data.pipelines.daily.nba.analysis.backtests.benchmarking import write_benchmark_artifacts as _write_benchmark_artifacts

    return _write_benchmark_artifacts(result, output_dir)


def run_analysis_backtests(request: BacktestRunRequest) -> dict[str, Any]:
    output_dir = ensure_output_dir(request.output_root, request.season, request.season_phase, request.analysis_version) / "backtests"
    with managed_connection() as connection:
        state_df = load_analysis_backtest_state_panel_df(
            connection,
            season=request.season,
            season_phase=request.season_phase,
            season_phases=request.season_phases,
            analysis_version=request.analysis_version,
        )
    result = build_benchmark_run_result(state_df, request)
    return write_benchmark_artifacts(result, output_dir)


__all__ = [
    "BACKTEST_TRADE_COLUMNS",
    "BacktestResult",
    "BenchmarkRunResult",
    "build_backtest_result",
    "build_benchmark_run_result",
    "load_analysis_backtest_state_panel_df",
    "run_analysis_backtests",
    "simulate_trade_loop",
    "write_backtest_artifacts",
    "write_benchmark_artifacts",
]
