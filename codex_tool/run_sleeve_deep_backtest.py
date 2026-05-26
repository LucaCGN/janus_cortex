from __future__ import annotations

"""Run a cross-cohort sleeve/lane validation pack.

This is a research artifact generator. It does not mutate StrategyPlans,
worker state, event controls, or order-management surfaces.
"""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.api.db import to_jsonable  # noqa: E402
from app.data.databases.postgres import managed_connection  # noqa: E402
from app.data.pipelines.daily.nba.analysis.artifacts import write_frame, write_json, write_markdown  # noqa: E402
from app.data.pipelines.daily.nba.analysis.backtests.engine import BACKTEST_TRADE_COLUMNS, build_backtest_result  # noqa: E402
from app.data.pipelines.daily.nba.analysis.backtests.registry import (  # noqa: E402
    DEFAULT_STRATEGY_GROUP,
    REPLAY_HF_STRATEGY_GROUP,
)
from app.data.pipelines.daily.nba.analysis.contracts import (  # noqa: E402
    ANALYSIS_VERSION,
    DEFAULT_SEASON,
    BacktestRunRequest,
)
from app.data.pipelines.daily.wnba.analysis.backtests import run_shadow_backtests_for_lanes  # noqa: E402
from app.data.pipelines.daily.wnba.analysis.contracts import (  # noqa: E402
    WNBA_ANALYSIS_VERSION,
    WNBA_DEFAULT_SEASON,
    WNBA_DEFAULT_SEASON_PHASE,
)
from app.modules.agentic import llm_runtime  # noqa: E402
from app.data.pipelines.daily.nba.analysis import ml_trading_lane  # noqa: E402
from app.runtime.local_paths import resolve_shared_root  # noqa: E402


NBA_STRATEGY_TO_LIVE_SLEEVE = {
    "winner_definition": "core_hold",
    "inversion": "core_hold",
    "underdog_liftoff": "core_hold",
    "q1_repricing": "grid_scalp",
    "q4_clutch": "grid_scalp",
    "micro_momentum_continuation": "grid_scalp",
    "panic_fade_fast": "grid_scalp",
    "quarter_open_reprice": "grid_scalp",
    "halftime_gap_fill": "grid_scalp",
    "lead_fragility": "grid_scalp",
    "underdog_range_scalp": "grid_scalp",
    "favorite_floor_rebound": "grid_scalp",
    "ultra_low_rebound_probe": "ultra_low_rebound",
}


def _parse_seed_csv(value: str) -> tuple[int, ...]:
    seeds: list[int] = []
    for part in str(value or "").split(","):
        text = part.strip()
        if not text:
            continue
        try:
            seeds.append(int(text))
        except ValueError:
            continue
    return tuple(seeds) or (1107,)


def _now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _query_df(connection: Any, query: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description]
    return pd.DataFrame(rows, columns=columns)


def _sample_game_ids(frame: pd.DataFrame, *, sample_size: int, seed: int) -> list[str]:
    if frame.empty or "game_id" not in frame.columns:
        return []
    game_ids = sorted(str(value) for value in frame["game_id"].dropna().astype(str).unique())
    if len(game_ids) <= int(sample_size):
        return game_ids
    return sorted(pd.Series(game_ids).sample(n=int(sample_size), random_state=int(seed)).astype(str).tolist())


def _filter_games(frame: pd.DataFrame, game_ids: list[str]) -> pd.DataFrame:
    if frame.empty or not game_ids:
        return frame.iloc[0:0].copy()
    return frame[frame["game_id"].astype(str).isin(set(game_ids))].copy().reset_index(drop=True)


def _load_nba_state_panel_sample_df(
    connection: Any,
    *,
    season: str,
    season_phases: tuple[str, ...],
    analysis_version: str,
    sample_size: int,
    seed: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    placeholders = ", ".join(["%s"] * len(season_phases))
    counts_query = f"""
    SELECT count(*) AS state_rows, count(DISTINCT game_id) AS games
    FROM nba.nba_analysis_state_panel
    WHERE season = %s AND season_phase IN ({placeholders}) AND analysis_version = %s;
    """
    counts_params: tuple[Any, ...] = (season, *season_phases, analysis_version)
    counts_df = _query_df(connection, counts_query, counts_params)
    available_rows = int(counts_df.iloc[0]["state_rows"] or 0) if not counts_df.empty else 0
    available_games = int(counts_df.iloc[0]["games"] or 0) if not counts_df.empty else 0

    game_query = f"""
    SELECT game_id
    FROM (
        SELECT DISTINCT game_id
        FROM nba.nba_analysis_state_panel
        WHERE season = %s AND season_phase IN ({placeholders}) AND analysis_version = %s
    ) games
    ORDER BY md5(game_id::text || %s)
    LIMIT %s;
    """
    game_params: tuple[Any, ...] = (season, *season_phases, analysis_version, str(seed), int(sample_size))
    game_df = _query_df(connection, game_query, game_params)
    game_ids = [str(value) for value in game_df["game_id"].tolist()] if not game_df.empty else []
    if not game_ids:
        return pd.DataFrame(), {
            "available_games": available_games,
            "available_state_rows": available_rows,
            "sample_seed": seed,
            "sample_size_games": 0,
            "state_rows": 0,
            "game_ids": [],
        }

    row_query = """
    SELECT *
    FROM nba.nba_analysis_state_panel
    WHERE season = %s
      AND analysis_version = %s
      AND game_id = ANY(%s)
    ORDER BY game_date ASC NULLS LAST, game_id ASC, team_side ASC, state_index ASC;
    """
    frame = _query_df(connection, row_query, (season, analysis_version, game_ids))
    return frame, {
        "available_games": available_games,
        "available_state_rows": available_rows,
        "sample_seed": seed,
        "sample_size_games": len(game_ids),
        "state_rows": int(len(frame)),
        "game_ids": sorted(game_ids),
    }


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _summarize_trade_frame(
    frame: pd.DataFrame,
    *,
    cohort: str,
    sample_seed: int,
    strategy_family: str,
    strategy_group: str,
) -> dict[str, Any]:
    sleeve = NBA_STRATEGY_TO_LIVE_SLEEVE.get(strategy_family, "unmapped")
    if frame.empty:
        return {
            "cohort": cohort,
            "sample_seed": sample_seed,
            "strategy_group": strategy_group,
            "strategy_family": strategy_family,
            "live_sleeve_role": sleeve,
            "trade_count": 0,
            "win_rate": None,
            "avg_return": None,
            "median_return": None,
            "avg_return_with_slippage": None,
            "min_order_pnl_total": 0.0,
            "min_order_pnl_avg": None,
            "avg_entry_price": None,
            "avg_exit_price": None,
            "avg_hold_seconds": None,
            "avg_mfe": None,
            "avg_mae": None,
        }

    work = frame.copy()
    returns = pd.to_numeric(work["gross_return_with_slippage"], errors="coerce")
    entry_prices = pd.to_numeric(work["entry_price"], errors="coerce")
    exit_prices = pd.to_numeric(work["exit_price"], errors="coerce")
    min_order_pnls: list[float] = []
    for entry_price, exit_price in zip(entry_prices.tolist(), exit_prices.tolist(), strict=False):
        if entry_price is None or exit_price is None or pd.isna(entry_price) or pd.isna(exit_price) or float(entry_price) <= 0:
            continue
        shares = max(5.0, 1.0 / float(entry_price))
        min_order_pnls.append(float(shares * (float(exit_price) - float(entry_price))))
    return {
        "cohort": cohort,
        "sample_seed": sample_seed,
        "strategy_group": strategy_group,
        "strategy_family": strategy_family,
        "live_sleeve_role": sleeve,
        "trade_count": int(len(work)),
        "win_rate": float((returns > 0).mean()) if not returns.dropna().empty else None,
        "avg_return": float(pd.to_numeric(work["gross_return"], errors="coerce").mean()),
        "median_return": float(returns.median()) if not returns.dropna().empty else None,
        "avg_return_with_slippage": float(returns.mean()) if not returns.dropna().empty else None,
        "min_order_pnl_total": float(sum(min_order_pnls)),
        "min_order_pnl_avg": float(sum(min_order_pnls) / len(min_order_pnls)) if min_order_pnls else None,
        "avg_entry_price": float(entry_prices.mean()) if not entry_prices.dropna().empty else None,
        "avg_exit_price": float(exit_prices.mean()) if not exit_prices.dropna().empty else None,
        "avg_hold_seconds": float(pd.to_numeric(work["hold_time_seconds"], errors="coerce").mean()),
        "avg_mfe": float(pd.to_numeric(work["max_favorable_excursion_after_entry"], errors="coerce").mean()),
        "avg_mae": float(pd.to_numeric(work["max_adverse_excursion_after_entry"], errors="coerce").mean()),
    }


def _ultra_low_probe_rows(state_df: pd.DataFrame, *, slippage_cents: int = 0, max_trades_per_side: int = 12) -> list[dict[str, Any]]:
    if state_df.empty:
        return []
    trades: list[dict[str, Any]] = []
    for (_, _), group in state_df.groupby(["game_id", "team_side"], sort=True):
        ordered = group.sort_values("state_index", kind="mergesort").reset_index(drop=True)
        start = 0
        count = 0
        while start < len(ordered) and count < max_trades_per_side:
            window = ordered.iloc[start:].reset_index(drop=True)
            prices = pd.to_numeric(window["team_price"], errors="coerce")
            score_diff = pd.to_numeric(window["score_diff"], errors="coerce")
            seconds_left = pd.to_numeric(window["seconds_to_game_end"], errors="coerce")
            entry_index: int | None = None
            for idx, price in enumerate(prices.tolist()):
                if price is None or pd.isna(price):
                    continue
                if not (0.003 <= float(price) <= 0.05):
                    continue
                if pd.isna(seconds_left.iloc[idx]) or float(seconds_left.iloc[idx]) < 180:
                    continue
                if pd.notna(score_diff.iloc[idx]) and abs(float(score_diff.iloc[idx])) > 35:
                    continue
                entry_index = idx
                break
            if entry_index is None:
                break
            entry = window.iloc[entry_index]
            entry_price = float(entry["team_price"])
            target = min(0.25, max(entry_price + 0.003, entry_price * 1.5))
            stop = max(0.001, entry_price - 0.02)
            exit_index = len(window) - 1
            exit_reason = "end"
            future = window.iloc[entry_index + 1 :]
            for idx, row in future.iterrows():
                price = _safe_float(row.get("team_price"))
                if price is None:
                    continue
                if price >= target:
                    exit_index = int(idx)
                    exit_reason = "target"
                    break
                if price <= stop:
                    exit_index = int(idx)
                    exit_reason = "stop"
                    break
                left = _safe_float(row.get("seconds_to_game_end"))
                if left is not None and left <= 90:
                    exit_index = int(idx)
                    exit_reason = "late_flatten"
                    break
            exit_row = window.iloc[exit_index]
            exit_price = float(exit_row["team_price"])
            slippage = max(0, int(slippage_cents)) / 100.0
            entry_exec = min(0.999999, entry_price + slippage)
            exit_exec = max(0.0, exit_price - slippage)
            gross_return = (exit_price - entry_price) / entry_price if entry_price > 0 else 0.0
            net_return = (exit_exec - entry_exec) / entry_exec if entry_exec > 0 else 0.0
            segment = window.iloc[entry_index:]
            entry_at = pd.to_datetime(entry["event_at"], errors="coerce", utc=True)
            exit_at = pd.to_datetime(exit_row["event_at"], errors="coerce", utc=True)
            trades.append(
                {
                    "season": entry.get("season"),
                    "season_phase": entry.get("season_phase"),
                    "analysis_version": entry.get("analysis_version"),
                    "strategy_family": "ultra_low_rebound_probe",
                    "entry_rule": "buy_0p3c_to_5c_recoverable_score_gap",
                    "exit_rule": "plus_50pct_or_plus_0p3c_or_minus_2c_or_late_flatten",
                    "game_id": entry.get("game_id"),
                    "team_side": entry.get("team_side"),
                    "team_slug": entry.get("team_slug"),
                    "opponent_team_slug": entry.get("opponent_team_slug"),
                    "opening_band": entry.get("opening_band"),
                    "period_label": entry.get("period_label"),
                    "score_diff_bucket": entry.get("score_diff_bucket"),
                    "context_bucket": entry.get("context_bucket"),
                    "context_tags_json": {"exit_reason": exit_reason},
                    "entry_metadata_json": {"target_price": target, "stop_price": stop, "exit_reason": exit_reason},
                    "signal_strength": float((0.05 - entry_price) * 100.0),
                    "entry_state_index": int(entry.get("state_index") or entry_index),
                    "exit_state_index": int(exit_row.get("state_index") or exit_index),
                    "entry_at": entry_at,
                    "exit_at": exit_at,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "gross_return": gross_return,
                    "gross_return_with_slippage": net_return,
                    "max_favorable_excursion_after_entry": float(pd.to_numeric(segment["team_price"], errors="coerce").max() - entry_price),
                    "max_adverse_excursion_after_entry": float(entry_price - pd.to_numeric(segment["team_price"], errors="coerce").min()),
                    "hold_time_seconds": max(0.0, (exit_at - entry_at).total_seconds()) if pd.notna(entry_at) and pd.notna(exit_at) else 0.0,
                    "slippage_cents": int(slippage_cents),
                }
            )
            count += 1
            start += exit_index + 1
    return trades


def _nba_family_frames_for_sample(
    state_df: pd.DataFrame,
    *,
    slippage_cents: int,
) -> dict[str, tuple[str, pd.DataFrame]]:
    frames: dict[str, tuple[str, pd.DataFrame]] = {}
    for group in (DEFAULT_STRATEGY_GROUP, REPLAY_HF_STRATEGY_GROUP):
        result = build_backtest_result(
            state_df,
            BacktestRunRequest(
                strategy_family="all",
                strategy_group=group,
                slippage_cents=slippage_cents,
                analysis_version=ANALYSIS_VERSION,
            ),
        )
        for family, frame in result.trade_frames.items():
            frames[family] = (group, frame)
    ultra = pd.DataFrame(_ultra_low_probe_rows(state_df, slippage_cents=slippage_cents), columns=BACKTEST_TRADE_COLUMNS)
    frames["ultra_low_rebound_probe"] = ("custom_live_sleeve_probe", ultra)
    return frames


def _rank_sleeve_roles(summary_df: pd.DataFrame) -> list[dict[str, Any]]:
    if summary_df.empty:
        return []
    work = summary_df.copy()
    work = work[pd.to_numeric(work["trade_count"], errors="coerce").fillna(0) > 0].copy()
    if work.empty:
        return []
    grouped = (
        work.groupby(["cohort", "live_sleeve_role"], dropna=False)
        .agg(
            family_count=("strategy_family", "nunique"),
            trade_count=("trade_count", "sum"),
            avg_return_with_slippage=("avg_return_with_slippage", "mean"),
            min_order_pnl_total=("min_order_pnl_total", "sum"),
            avg_win_rate=("win_rate", "mean"),
        )
        .reset_index()
    )
    return to_jsonable(grouped.sort_values(["cohort", "avg_return_with_slippage"], ascending=[True, False]).to_dict(orient="records"))


def _aggregate_family_summary(summary_df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "cohort",
        "live_sleeve_role",
        "strategy_family",
        "sample_rows",
        "trade_count",
        "avg_return_with_slippage",
        "avg_win_rate",
        "min_order_pnl_total",
        "avg_entry_price",
        "avg_exit_price",
    ]
    if summary_df.empty:
        return pd.DataFrame(columns=columns)
    work = summary_df.copy()
    work["trade_count"] = pd.to_numeric(work["trade_count"], errors="coerce").fillna(0)
    positive = work[work["trade_count"] > 0].copy()
    if positive.empty:
        return pd.DataFrame(columns=columns)
    aggregate = (
        positive.groupby(["cohort", "live_sleeve_role", "strategy_family"], dropna=False)
        .agg(
            sample_rows=("sample_seed", "count"),
            trade_count=("trade_count", "sum"),
            avg_return_with_slippage=("avg_return_with_slippage", "mean"),
            avg_win_rate=("win_rate", "mean"),
            min_order_pnl_total=("min_order_pnl_total", "sum"),
            avg_entry_price=("avg_entry_price", "mean"),
            avg_exit_price=("avg_exit_price", "mean"),
        )
        .reset_index()
        .sort_values(["cohort", "avg_return_with_slippage", "trade_count"], ascending=[True, False, False])
    )
    return aggregate[columns]


def _recommendations(summary_df: pd.DataFrame, blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    if not summary_df.empty:
        aggregate_df = _aggregate_family_summary(summary_df)
        work = aggregate_df[pd.to_numeric(aggregate_df["trade_count"], errors="coerce").fillna(0) >= 5].copy()
        if not work.empty:
            leaders = work.sort_values(["avg_return_with_slippage", "trade_count"], ascending=[False, False]).head(8)
            recommendations.append(
                {
                    "area": "sleeve_promotion_candidates",
                    "finding": "Several replay families have enough sampled trades across seeds to evaluate as sleeve-specific signal sources.",
                    "top_rows": to_jsonable(
                        leaders[
                            [
                                "cohort",
                                "strategy_family",
                                "live_sleeve_role",
                                "sample_rows",
                                "trade_count",
                                "avg_win_rate",
                                "avg_return_with_slippage",
                                "min_order_pnl_total",
                            ]
                        ].to_dict(orient="records")
                    ),
                }
            )
        ultra = summary_df[summary_df["strategy_family"] == "ultra_low_rebound_probe"]
        if not ultra.empty:
            recommendations.append(
                {
                    "area": "ultra_low_rebound",
                    "finding": "Ultra-low behavior now has an explicit historical probe; do not judge it only from live anecdotes.",
                    "rows": to_jsonable(
                        ultra[
                            [
                                "cohort",
                                "trade_count",
                                "win_rate",
                                "avg_return_with_slippage",
                                "min_order_pnl_total",
                            ]
                        ].to_dict(orient="records")
                    ),
                }
            )
    if blockers:
        recommendations.append(
            {
                "area": "wnba_price_path_gap",
                "finding": "WNBA cannot receive the same depth of sleeve validation until market state/price history panels are populated.",
                "blocked_cohorts": blockers,
            }
        )
    recommendations.append(
        {
            "area": "llm_model_routing",
            "finding": "Nano is currently reserved for compression/tagging-only triggers. The next useful feature is a nano PBP event annotator that tags every play into cheap sleeve context, then escalates only aggregate trigger windows to mini/frontier.",
        }
    )
    return recommendations


def _llm_ml_usage_review() -> dict[str, Any]:
    return {
        "llm_runtime": {
            "nano_model": getattr(llm_runtime, "NANO_MODEL", None),
            "mini_model": getattr(llm_runtime, "MINI_MODEL", None),
            "frontier_model": getattr(llm_runtime, "FRONTIER_MODEL", None),
            "nano_trigger_types": sorted(getattr(llm_runtime, "_NANO_TRIGGER_TYPES", set())),
            "critical_trigger_types": sorted(getattr(llm_runtime, "_CRITICAL_TRIGGER_TYPES", set())),
            "order_lifecycle_trigger_types": sorted(getattr(llm_runtime, "_ORDER_LIFECYCLE_TRIGGER_TYPES", set())),
            "default_trigger_call_caps": getattr(llm_runtime, "_DEFAULT_TRIGGER_CALL_CAPS", {}),
            "routing_summary": (
                "No triggers -> mini if explicitly requested; compression/tagging-only -> nano; critical/open-exposure/"
                "late-uncertainty/order-protection gaps -> frontier unless budget/exposure gates downgrade to mini; "
                "ordinary StrategyPlanJSON revision -> mini."
            ),
            "gap": "No always-on nano PBP tagging stream is wired into sleeve backtests/live aggregation yet.",
        },
        "ml_trading_lane": {
            "focus_strategy_families": list(getattr(ml_trading_lane, "FOCUS_STRATEGY_FAMILIES", ())),
            "default_gate_threshold": getattr(ml_trading_lane, "DEFAULT_GATE_THRESHOLD", None),
            "live_max_entry_orders_per_game": getattr(ml_trading_lane, "LIVE_MAX_ENTRY_ORDERS_PER_GAME", None),
            "live_max_entry_notional_per_game_usd": getattr(ml_trading_lane, "LIVE_MAX_ENTRY_NOTIONAL_PER_GAME_USD", None),
            "current_role": (
                "Offline/replay sidecar for candidate ranking, calibrated confidence, execution likelihood, "
                "and focus-family gates. It is not the primary live tick loop."
            ),
            "gap": "ML features are NBA-heavy; WNBA ML rows are empty until WNBA state/price panels are populated.",
        },
    }


def _load_wnba_market_state_panel(connection: Any, *, season: str, season_phase: str) -> pd.DataFrame:
    query = """
    SELECT panel.*, games.season, games.season_phase
    FROM wnba.wnba_market_state_panels panel
    JOIN wnba.wnba_games games ON games.game_id = panel.game_id
    WHERE games.season = %s AND games.season_phase = %s
    ORDER BY games.game_date ASC NULLS LAST, panel.game_id ASC, panel.team_side ASC, panel.state_index ASC;
    """
    return _query_df(connection, query, (season, season_phase))


def _load_wnba_counts(connection: Any, *, season: str) -> dict[str, Any]:
    queries = {
        "games": "SELECT count(*) FROM wnba.wnba_games WHERE season = %s;",
        "pbp_rows": "SELECT count(*) FROM wnba.wnba_play_by_play pbp JOIN wnba.wnba_games games ON games.game_id = pbp.game_id WHERE games.season = %s;",
        "market_state_rows": "SELECT count(*) FROM wnba.wnba_market_state_panels panel JOIN wnba.wnba_games games ON games.game_id = panel.game_id WHERE games.season = %s;",
        "price_history_rows": "SELECT count(*) FROM wnba.wnba_polymarket_price_history history JOIN wnba.wnba_games games ON games.game_id = history.game_id WHERE games.season = %s;",
    }
    counts: dict[str, Any] = {"season": season}
    for key, query in queries.items():
        with connection.cursor() as cursor:
            cursor.execute(query, (season,))
            counts[key] = int(cursor.fetchone()[0] or 0)
    return counts


def _run_nba_cohort(
    connection: Any,
    *,
    cohort_name: str,
    season: str,
    season_phases: tuple[str, ...],
    analysis_version: str,
    sample_size: int,
    seeds: tuple[int, ...],
    slippage_cents: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    sample_payload: dict[str, Any] = {
        "cohort": cohort_name,
        "available_games": 0,
        "available_state_rows": 0,
        "samples": [],
    }
    best_trades: list[pd.DataFrame] = []
    for seed in seeds:
        sample_df, sample_meta = _load_nba_state_panel_sample_df(
            connection,
            season=season,
            season_phases=season_phases,
            analysis_version=analysis_version,
            sample_size=sample_size,
            seed=seed,
        )
        sample_payload["available_games"] = max(int(sample_payload["available_games"]), int(sample_meta["available_games"]))
        sample_payload["available_state_rows"] = max(
            int(sample_payload["available_state_rows"]),
            int(sample_meta["available_state_rows"]),
        )
        sample_payload["samples"].append(sample_meta)
        frames = _nba_family_frames_for_sample(sample_df, slippage_cents=slippage_cents)
        for family, (strategy_group, frame) in frames.items():
            rows.append(
                _summarize_trade_frame(
                    frame,
                    cohort=cohort_name,
                    sample_seed=seed,
                    strategy_family=family,
                    strategy_group=strategy_group,
                )
            )
            if not frame.empty:
                sample = frame.copy()
                sample["cohort"] = cohort_name
                sample["sample_seed"] = seed
                sample["live_sleeve_role"] = NBA_STRATEGY_TO_LIVE_SLEEVE.get(family, "unmapped")
                best_trades.append(sample.sort_values("gross_return_with_slippage", ascending=False).head(5))
    best_frame = pd.concat(best_trades, ignore_index=True) if best_trades else pd.DataFrame(columns=[*BACKTEST_TRADE_COLUMNS, "cohort", "sample_seed", "live_sleeve_role"])
    return rows, sample_payload, best_frame


def _run_wnba_cohort(
    connection: Any,
    *,
    cohort_name: str,
    season: str,
    season_phase: str,
    sample_size: int,
    seed: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counts = _load_wnba_counts(connection, season=season)
    state_df = _load_wnba_market_state_panel(connection, season=season, season_phase=season_phase)
    blockers: list[dict[str, Any]] = []
    if state_df.empty:
        blockers.append(
            {
                "cohort": cohort_name,
                "season": season,
                "season_phase": season_phase,
                "blocker": "missing_wnba_market_state_panel_or_price_history",
                "counts": counts,
            }
        )
        return {
            "cohort": cohort_name,
            "status": "blocked",
            "season": season,
            "season_phase": season_phase,
            "counts": counts,
            "backtests": None,
        }, blockers
    game_ids = _sample_game_ids(state_df, sample_size=sample_size, seed=seed)
    sample_df = _filter_games(state_df, game_ids)
    result = run_shadow_backtests_for_lanes(
        sample_df,
        season=season,
        season_phase=season_phase,
        analysis_version=WNBA_ANALYSIS_VERSION,
    )
    return {
        "cohort": cohort_name,
        "status": result.get("status"),
        "season": season,
        "season_phase": season_phase,
        "counts": counts,
        "sample_game_ids": game_ids,
        "backtests": {key: value for key, value in result.items() if key != "families"},
        "family_summaries": {
            family: {
                "status": payload.get("status"),
                "trade_count": payload.get("trade_count"),
                "summary": payload.get("summary"),
                "blockers": payload.get("blockers"),
            }
            for family, payload in (result.get("families") or {}).items()
        },
    }, blockers


def _render_markdown(payload: dict[str, Any], summary_df: pd.DataFrame) -> str:
    lines = [
        "# Sleeve Deep Backtest Review",
        "",
        f"- Generated at: `{payload['generated_at_utc']}`",
        f"- NBA sample seeds: `{payload['config']['seeds']}`",
        f"- NBA slippage cents: `{payload['config']['slippage_cents']}`",
        "",
        "## Cohort Status",
        "",
    ]
    for cohort in payload.get("cohorts", []):
        lines.append(f"- `{cohort['cohort']}`: `{cohort.get('status', 'complete')}`")
        if cohort.get("available_games") is not None:
            lines.append(f"  - available games: `{cohort.get('available_games')}`, rows: `{cohort.get('available_state_rows')}`")
        if cohort.get("counts"):
            lines.append(f"  - counts: `{json.dumps(cohort['counts'], sort_keys=True)}`")
    lines.extend(["", "## Best Sampled Families", ""])
    aggregate_df = _aggregate_family_summary(summary_df)
    if aggregate_df.empty:
        lines.append("No family summary rows generated.")
    else:
        ranked = aggregate_df[pd.to_numeric(aggregate_df["trade_count"], errors="coerce").fillna(0) >= 5].copy()
        if ranked.empty:
            lines.append("No family reached five trades in the sampled cohorts.")
        else:
            ranked = ranked.sort_values(["avg_return_with_slippage", "trade_count"], ascending=[False, False]).head(12)
            for row in ranked.to_dict(orient="records"):
                lines.append(
                    "- `{cohort}` `{family}` -> sleeve `{sleeve}`: samples `{samples}`, trades `{trades}`, win `{win:.1%}`, avg return `{ret:.2%}`, min-order PnL `{pnl:.2f}`".format(
                        cohort=row["cohort"],
                        family=row["strategy_family"],
                        sleeve=row["live_sleeve_role"],
                        samples=int(row["sample_rows"]),
                        trades=int(row["trade_count"]),
                        win=float(row["avg_win_rate"] or 0.0),
                        ret=float(row["avg_return_with_slippage"] or 0.0),
                        pnl=float(row["min_order_pnl_total"] or 0.0),
                    )
                )
    lines.extend(["", "## Findings", ""])
    for item in payload.get("recommendations", []):
        lines.append(f"- `{item.get('area')}`: {item.get('finding')}")
    lines.extend(["", "## LLM/ML Routing Read", ""])
    llm = payload.get("llm_ml_usage_review", {}).get("llm_runtime", {})
    ml = payload.get("llm_ml_usage_review", {}).get("ml_trading_lane", {})
    lines.append(f"- LLM routing: {llm.get('routing_summary')}")
    lines.append(f"- LLM gap: {llm.get('gap')}")
    lines.append(f"- ML lane role: {ml.get('current_role')}")
    lines.append(f"- ML gap: {ml.get('gap')}")
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    seeds = _parse_seed_csv(args.seeds)
    output_dir = Path(args.output_dir) if args.output_dir else resolve_shared_root() / "artifacts" / "sleeve-deep-backtests" / "2026-05-26" / _now_compact()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict[str, Any]] = []
    cohort_payloads: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    best_trade_frames: list[pd.DataFrame] = []

    with managed_connection() as connection:
        rows, cohort_payload, best = _run_nba_cohort(
            connection,
            cohort_name="current_nba_regular_random_sample",
            season=args.nba_season,
            season_phases=("regular_season",),
            analysis_version=args.analysis_version,
            sample_size=args.nba_regular_sample_size,
            seeds=seeds,
            slippage_cents=args.slippage_cents,
        )
        summary_rows.extend(rows)
        cohort_payload["status"] = "complete"
        cohort_payloads.append(cohort_payload)
        best_trade_frames.append(best)

        rows, cohort_payload, best = _run_nba_cohort(
            connection,
            cohort_name="current_nba_postseason_random_sample",
            season=args.nba_season,
            season_phases=("play_in", "playoffs"),
            analysis_version=args.analysis_version,
            sample_size=args.nba_postseason_sample_size,
            seeds=seeds,
            slippage_cents=args.slippage_cents,
        )
        summary_rows.extend(rows)
        cohort_payload["status"] = "complete"
        cohort_payloads.append(cohort_payload)
        best_trade_frames.append(best)

        current_wnba, current_blockers = _run_wnba_cohort(
            connection,
            cohort_name="current_wnba_regular_random_sample",
            season=args.wnba_current_season,
            season_phase=WNBA_DEFAULT_SEASON_PHASE,
            sample_size=args.wnba_sample_size,
            seed=seeds[0],
        )
        cohort_payloads.append(current_wnba)
        blockers.extend(current_blockers)

        prior_wnba, prior_blockers = _run_wnba_cohort(
            connection,
            cohort_name="prior_wnba_regular_random_sample",
            season=args.wnba_prior_season,
            season_phase=WNBA_DEFAULT_SEASON_PHASE,
            sample_size=args.wnba_sample_size,
            seed=seeds[0],
        )
        cohort_payloads.append(prior_wnba)
        blockers.extend(prior_blockers)

    summary_df = pd.DataFrame(summary_rows)
    aggregate_df = _aggregate_family_summary(summary_df)
    best_trades_df = pd.concat(best_trade_frames, ignore_index=True) if best_trade_frames else pd.DataFrame()
    sleeve_rankings = _rank_sleeve_roles(summary_df)
    payload = {
        "schema_version": "sleeve_deep_backtest_review_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": {
            "nba_season": args.nba_season,
            "analysis_version": args.analysis_version,
            "seeds": list(seeds),
            "nba_regular_sample_size": args.nba_regular_sample_size,
            "nba_postseason_sample_size": args.nba_postseason_sample_size,
            "wnba_current_season": args.wnba_current_season,
            "wnba_prior_season": args.wnba_prior_season,
            "wnba_sample_size": args.wnba_sample_size,
            "slippage_cents": args.slippage_cents,
        },
        "cohorts": to_jsonable(cohort_payloads),
        "strategy_to_live_sleeve_map": NBA_STRATEGY_TO_LIVE_SLEEVE,
        "sleeve_rankings": sleeve_rankings,
        "wnba_blockers": blockers,
        "llm_ml_usage_review": _llm_ml_usage_review(),
        "recommendations": _recommendations(summary_df, blockers),
        "artifacts": {},
    }

    payload["artifacts"].update({f"family_summary_{key}": value for key, value in write_frame(output_dir / "family_summary", summary_df).items()})
    payload["artifacts"].update({f"family_aggregate_{key}": value for key, value in write_frame(output_dir / "family_aggregate", aggregate_df).items()})
    payload["artifacts"].update({f"best_trade_samples_{key}": value for key, value in write_frame(output_dir / "best_trade_samples", best_trades_df).items()})
    payload["artifacts"]["json"] = write_json(output_dir / "sleeve_deep_backtest_review.json", payload)
    payload["artifacts"]["markdown"] = write_markdown(output_dir / "sleeve_deep_backtest_review.md", _render_markdown(payload, summary_df))
    return to_jsonable(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run cross-cohort sleeve/lane/strategy deep backtest review.")
    parser.add_argument("--nba-season", default=DEFAULT_SEASON)
    parser.add_argument("--analysis-version", default=ANALYSIS_VERSION)
    parser.add_argument("--wnba-current-season", default=WNBA_DEFAULT_SEASON)
    parser.add_argument("--wnba-prior-season", default="2025")
    parser.add_argument("--nba-regular-sample-size", type=int, default=120)
    parser.add_argument("--nba-postseason-sample-size", type=int, default=24)
    parser.add_argument("--wnba-sample-size", type=int, default=24)
    parser.add_argument("--seeds", default="1107,2113,3251,4421,5573")
    parser.add_argument("--slippage-cents", type=int, default=1)
    parser.add_argument("--output-dir", default=None)
    return parser


def main() -> int:
    payload = run(build_parser().parse_args())
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
