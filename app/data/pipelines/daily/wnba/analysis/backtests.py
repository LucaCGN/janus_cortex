from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.data.pipelines.daily.wnba.analysis.contracts import (
    WNBA_ANALYSIS_VERSION,
    WnbaLaneSpec,
    default_shadow_lane_specs,
)
from app.data.pipelines.daily.wnba.analysis.deterministic_lanes import evaluate_wnba_lane_signal


_NAMESPACE = uuid.UUID("6d2be286-d8f7-4abe-a968-b98822c6a688")


def _uuid_for(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, "|".join(str(part) for part in parts)))


def _safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _entry_allowed(row: pd.Series, lane: WnbaLaneSpec) -> bool:
    return evaluate_wnba_lane_signal(row, lane)["signal_status"] == "entry_candidate"


def run_shadow_price_path_backtest(
    state_panel_df: pd.DataFrame,
    *,
    lane: WnbaLaneSpec,
    season: str | None = None,
    season_phase: str | None = None,
    analysis_version: str = WNBA_ANALYSIS_VERSION,
) -> dict[str, Any]:
    """Run a shadow-only price-path replay. Requires WNBA CLOB price states."""
    run_id = _uuid_for("wnba_shadow_backtest", lane.lane_id, datetime.now(timezone.utc).isoformat())
    if state_panel_df.empty:
        return {
            "backtest_run_id": run_id,
            "status": "blocked",
            "lane_id": lane.lane_id,
            "family": lane.family,
            "analysis_version": analysis_version,
            "sample_states": 0,
            "trade_count": 0,
            "blockers": ["missing_state_panel_rows"],
            "config": asdict(lane),
            "trades": [],
        }
    if "team_price" not in state_panel_df.columns or state_panel_df["team_price"].dropna().empty:
        return {
            "backtest_run_id": run_id,
            "status": "blocked",
            "lane_id": lane.lane_id,
            "family": lane.family,
            "analysis_version": analysis_version,
            "sample_states": int(len(state_panel_df)),
            "trade_count": 0,
            "blockers": ["missing_wnba_clob_price_path"],
            "config": asdict(lane),
            "trades": [],
        }

    trades: list[dict[str, Any]] = []
    work = state_panel_df.copy().sort_values(["game_id", "team_side", "state_index"])
    for (game_id, team_side), group in work.groupby(["game_id", "team_side"], sort=False):
        group = group.reset_index(drop=True)
        open_until_index = -1
        for idx, row in group.iterrows():
            state_index = _safe_int(row.get("state_index"), idx) or idx
            if state_index <= open_until_index:
                continue
            if not _entry_allowed(row, lane):
                continue
            entry_price = _safe_float(row.get("team_price"))
            if entry_price is None:
                continue
            target_price = min(0.99, entry_price + max(lane.min_target_move, entry_price * lane.target_return_fraction))
            stop_price = max(0.01, entry_price - lane.stop_loss) if lane.stop_loss is not None else None
            future = group.iloc[idx + 1 : idx + 1 + lane.max_horizon_states]
            if future.empty:
                continue
            exit_row = future.iloc[-1]
            exit_reason = "horizon"
            exit_price = _safe_float(exit_row.get("team_price"), entry_price) or entry_price
            for _, candidate in future.iterrows():
                candidate_price = _safe_float(candidate.get("team_price"))
                if candidate_price is None:
                    continue
                if candidate_price >= target_price:
                    exit_row = candidate
                    exit_price = target_price
                    exit_reason = "target"
                    break
                if stop_price is not None and candidate_price <= stop_price:
                    exit_row = candidate
                    exit_price = stop_price
                    exit_reason = "stop"
                    break
            exit_state_index = _safe_int(exit_row.get("state_index"), state_index) or state_index
            open_until_index = exit_state_index
            trades.append(
                {
                    "game_id": game_id,
                    "team_side": team_side,
                    "entry_state_index": state_index,
                    "exit_state_index": exit_state_index,
                    "entry_price": entry_price,
                    "target_price": target_price,
                    "stop_price": stop_price,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "return": exit_price - entry_price,
                    "period": _safe_int(row.get("period")),
                    "score_diff": _safe_int(row.get("score_diff")),
                }
            )

    if not trades:
        return {
            "backtest_run_id": run_id,
            "status": "no_trades",
            "lane_id": lane.lane_id,
            "family": lane.family,
            "analysis_version": analysis_version,
            "season": season,
            "season_phase": season_phase,
            "sample_states": int(len(state_panel_df)),
            "trade_count": 0,
            "blockers": [],
            "config": asdict(lane),
            "trades": [],
            "summary": {"total_return": 0.0, "avg_return": None, "win_rate": None},
        }

    returns = [float(row["return"]) for row in trades]
    wins = [value > 0 for value in returns]
    return {
        "backtest_run_id": run_id,
        "status": "shadow_complete",
        "lane_id": lane.lane_id,
        "family": lane.family,
        "analysis_version": analysis_version,
        "season": season,
        "season_phase": season_phase,
        "sample_states": int(len(state_panel_df)),
        "trade_count": int(len(trades)),
        "blockers": [],
        "config": asdict(lane),
        "trades": trades,
        "summary": {
            "total_return": float(sum(returns)),
            "avg_return": float(sum(returns) / len(returns)),
            "win_rate": float(sum(1 for flag in wins if flag) / len(wins)),
        },
    }


def run_shadow_backtests_for_lanes(
    state_panel_df: pd.DataFrame,
    *,
    lanes: tuple[WnbaLaneSpec, ...] | None = None,
    season: str | None = None,
    season_phase: str | None = None,
    analysis_version: str = WNBA_ANALYSIS_VERSION,
) -> dict[str, Any]:
    """Run every WNBA shadow lane over a state panel and summarize structural/calibration blockers."""
    lane_specs = lanes or default_shadow_lane_specs()
    results = {
        lane.family: run_shadow_price_path_backtest(
            state_panel_df,
            lane=lane,
            season=season,
            season_phase=season_phase,
            analysis_version=analysis_version,
        )
        for lane in lane_specs
    }
    blocked_families = [
        family
        for family, result in results.items()
        if result.get("status") == "blocked"
    ]
    complete_families = [
        family
        for family, result in results.items()
        if result.get("status") == "shadow_complete"
    ]
    no_trade_families = [
        family
        for family, result in results.items()
        if result.get("status") == "no_trades"
    ]
    shared_blockers = sorted(
        {
            blocker
            for result in results.values()
            for blocker in (result.get("blockers") or [])
        }
    )
    status = "shadow_complete" if complete_families else "blocked" if blocked_families else "no_trades"
    return {
        "status": status,
        "analysis_version": analysis_version,
        "season": season,
        "season_phase": season_phase,
        "lane_count": int(len(lane_specs)),
        "complete_families": complete_families,
        "blocked_families": blocked_families,
        "no_trade_families": no_trade_families,
        "blockers": shared_blockers,
        "families": results,
    }
