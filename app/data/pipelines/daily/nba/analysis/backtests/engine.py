from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from app.api.db import to_jsonable
from app.data.databases.postgres import managed_connection
from app.data.pipelines.daily.nba.analysis.artifacts import ensure_output_dir, write_frame, write_json, write_markdown
from app.data.pipelines.daily.nba.analysis.contracts import (
    ANALYSIS_VERSION,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_REVERSION_DRAWDOWN,
    DEFAULT_REVERSION_EXIT_BUFFER,
    DEFAULT_REVERSION_OPEN_THRESHOLD,
    DEFAULT_SEASON,
    DEFAULT_SEASON_PHASE,
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


@dataclass(slots=True)
class TradeSelection:
    entry_index: int
    metadata: dict[str, Any]


@dataclass(slots=True)
class BacktestResult:
    payload: dict[str, Any]
    trade_frames: dict[str, pd.DataFrame]


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
    analysis_version: str,
) -> pd.DataFrame:
    frame = _query_df(
        connection,
        """
        SELECT *
        FROM nba.nba_analysis_state_panel
        WHERE season = %s AND season_phase = %s AND analysis_version = %s
        ORDER BY game_date ASC NULLS LAST, game_id ASC, team_side ASC, state_index ASC;
        """,
        (season, season_phase, analysis_version),
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
) -> dict[str, Any]:
    entry = group.iloc[entry_index]
    exit_row = group.iloc[exit_index]
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
        if exit_index is None or exit_index < selection.entry_index:
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
        lines.extend(
            [
                f"## {family}",
                "",
                f"- Trade count: `{summary.get('trade_count')}`",
                f"- Win rate: `{_format_pct(summary.get('win_rate'))}`",
                f"- Average gross return: `{_format_num(summary.get('avg_gross_return'))}`",
                f"- Average gross return with slippage: `{_format_num(summary.get('avg_gross_return_with_slippage'))}`",
                f"- Average hold time seconds: `{_format_num(summary.get('avg_hold_time_seconds'))}`",
                "",
            ]
        )
    return "\n".join(lines)


def build_backtest_result(state_df: pd.DataFrame, request: BacktestRunRequest) -> BacktestResult:
    work = _prepare_state_panel_frame(state_df)
    if work.empty:
        payload = {
            "season": request.season,
            "season_phase": request.season_phase,
            "analysis_version": request.analysis_version,
            "slippage_cents": int(request.slippage_cents),
            "state_rows_considered": 0,
            "games_considered": 0,
            "families": {},
            "error": "state_panel_empty",
        }
        empty_frames: dict[str, pd.DataFrame] = {}
        return BacktestResult(payload=payload, trade_frames=empty_frames)

    from app.data.pipelines.daily.nba.analysis.backtests.inversion import simulate_inversion_trades
    from app.data.pipelines.daily.nba.analysis.backtests.reversion import simulate_reversion_trades
    from app.data.pipelines.daily.nba.analysis.backtests.winner_definition import simulate_winner_definition_trades

    families_to_run = (
        [request.strategy_family]
        if request.strategy_family != "all"
        else ["reversion", "inversion", "winner_definition"]
    )
    family_summaries: dict[str, Any] = {}
    family_trades: dict[str, pd.DataFrame] = {}
    trade_families = {
        "reversion": simulate_reversion_trades,
        "inversion": simulate_inversion_trades,
        "winner_definition": simulate_winner_definition_trades,
    }
    for family in families_to_run:
        simulator = trade_families.get(family)
        if simulator is None:
            continue
        trades = simulator(work, slippage_cents=request.slippage_cents)
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
        "artifacts": {},
    }
    return BacktestResult(payload=payload, trade_frames=family_trades)


def write_backtest_artifacts(result: BacktestResult, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = result.payload
    payload["artifacts"] = {}
    payload["artifacts"]["json"] = write_json(output_dir / "run_analysis_backtests.json", payload)
    payload["artifacts"]["markdown"] = write_markdown(output_dir / "run_analysis_backtests.md", _render_backtest_markdown(payload))
    for family, trades_df in result.trade_frames.items():
        payload["artifacts"].update(
            {f"{family}_{key}": value for key, value in write_frame(output_dir / f"{family}_trades", trades_df).items()}
        )
    return to_jsonable(payload)


def run_analysis_backtests(request: BacktestRunRequest) -> dict[str, Any]:
    output_dir = ensure_output_dir(request.output_root, request.season, request.season_phase, request.analysis_version) / "backtests"
    with managed_connection() as connection:
        state_df = load_analysis_backtest_state_panel_df(
            connection,
            season=request.season,
            season_phase=request.season_phase,
            analysis_version=request.analysis_version,
        )
    result = build_backtest_result(state_df, request)
    return write_backtest_artifacts(result, output_dir)


__all__ = [
    "BACKTEST_TRADE_COLUMNS",
    "BacktestResult",
    "TradeSelection",
    "build_backtest_result",
    "load_analysis_backtest_state_panel_df",
    "run_analysis_backtests",
    "simulate_trade_loop",
    "write_backtest_artifacts",
]
