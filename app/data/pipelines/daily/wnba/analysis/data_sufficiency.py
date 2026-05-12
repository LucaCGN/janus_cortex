from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from app.data.pipelines.daily.wnba.analysis.contracts import (
    WnbaDataSufficiencyThresholds,
    default_shadow_lane_specs,
)


_NAMESPACE = uuid.UUID("542d0953-9233-45d7-bac5-c3ddafcd3d21")


@dataclass(frozen=True)
class WnbaDataCounts:
    season: str
    season_phase: str | None = None
    schedule_games: int = 0
    games_with_boxscore: int = 0
    games_with_play_by_play: int = 0
    play_by_play_rows: int = 0
    player_boxscore_rows: int = 0
    market_link_count: int = 0
    clob_tick_count: int = 0
    clob_trade_count: int = 0
    state_panel_rows: int = 0
    ml_feature_rows: int = 0
    labeled_ml_feature_rows: int = 0
    distinct_ml_games: int = 0


def _uuid_for(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, "|".join(str(part) for part in parts)))


def _lane_status(
    *,
    lane_id: str,
    family: str,
    counts: WnbaDataCounts,
    thresholds: WnbaDataSufficiencyThresholds,
    requires_clob: bool = True,
    requires_trade_microstructure: bool = False,
) -> dict[str, Any]:
    blockers: list[str] = []
    if counts.games_with_play_by_play < thresholds.min_games_with_pbp_for_lane_design:
        blockers.append("insufficient_wnba_pbp_games")
    if counts.games_with_boxscore < thresholds.min_games_with_boxscore_for_lane_design:
        blockers.append("insufficient_wnba_boxscore_games")
    if counts.market_link_count < thresholds.min_market_links_for_replay:
        blockers.append("insufficient_wnba_polymarket_market_links")
    if requires_clob and counts.clob_tick_count < thresholds.min_clob_ticks_for_replay:
        blockers.append("insufficient_wnba_clob_tick_history")
    if requires_trade_microstructure and counts.clob_trade_count < thresholds.min_clob_trades_for_microstructure:
        blockers.append("insufficient_wnba_clob_trade_history")

    if not blockers:
        status = "ready_for_shadow_backtest"
    elif "insufficient_wnba_clob_tick_history" in blockers and counts.games_with_play_by_play > 0:
        status = "proxy_state_panel_only"
    else:
        status = "blocked"
    return {
        "lane_id": lane_id,
        "family": family,
        "status": status,
        "blockers": blockers,
        "requires_clob": requires_clob,
        "requires_trade_microstructure": requires_trade_microstructure,
    }


def evaluate_wnba_data_sufficiency(
    counts: WnbaDataCounts,
    *,
    thresholds: WnbaDataSufficiencyThresholds | None = None,
    audited_at: datetime | None = None,
) -> dict[str, Any]:
    thresholds = thresholds or WnbaDataSufficiencyThresholds()
    audited_at = audited_at or datetime.now(timezone.utc)
    lane_readiness = [
        _lane_status(
            lane_id=lane.lane_id,
            family=lane.family,
            counts=counts,
            thresholds=thresholds,
            requires_clob=lane.requires_clob,
            requires_trade_microstructure=lane.requires_trade_microstructure,
        )
        for lane in default_shadow_lane_specs()
    ]

    ml_blockers: list[str] = []
    if counts.labeled_ml_feature_rows < thresholds.min_ml_feature_rows_for_experiment:
        ml_blockers.append("insufficient_labeled_wnba_ml_rows")
    if counts.distinct_ml_games < thresholds.min_distinct_games_for_ml_experiment:
        ml_blockers.append("insufficient_distinct_wnba_ml_games")
    if counts.clob_tick_count < thresholds.min_clob_ticks_for_replay:
        ml_blockers.append("missing_wnba_clob_before_after_price_windows")
    ml_readiness = {
        "status": "ready_for_experiment" if not ml_blockers else "blocked",
        "blockers": ml_blockers,
        "required_target": "short_horizon_clob_repricing_by_pbp_state",
        "labeled_rows": counts.labeled_ml_feature_rows,
        "distinct_games": counts.distinct_ml_games,
    }

    global_blockers: list[str] = []
    if counts.schedule_games < thresholds.min_schedule_games_for_lane_design:
        global_blockers.append("insufficient_schedule_games_for_lane_design")
    if all(row["status"] == "blocked" for row in lane_readiness):
        global_blockers.append("no_wnba_lane_ready_for_backtest")
    if ml_readiness["status"] == "blocked":
        global_blockers.append("wnba_ml_training_not_ready")

    if any(row["status"] == "ready_for_shadow_backtest" for row in lane_readiness):
        status = "ready_for_shadow_backtest"
    elif any(row["status"] == "proxy_state_panel_only" for row in lane_readiness):
        status = "proxy_state_panel_only"
    else:
        status = "blocked"

    return {
        "audit_id": _uuid_for("wnba_data_sufficiency", counts.season, audited_at.isoformat()),
        "season": counts.season,
        "season_phase": counts.season_phase,
        "audited_at": audited_at,
        "status": status,
        "counts": asdict(counts),
        "thresholds": asdict(thresholds),
        "lane_readiness": lane_readiness,
        "ml_readiness": ml_readiness,
        "blockers": global_blockers,
        "verdict": _verdict_text(status=status, counts=counts, ml_blockers=ml_blockers),
    }


def _verdict_text(*, status: str, counts: WnbaDataCounts, ml_blockers: list[str]) -> str:
    if status == "ready_for_shadow_backtest":
        return "WNBA has enough linked PBP/boxscore/CLOB history for shadow lane backtests."
    if status == "proxy_state_panel_only":
        return (
            "WNBA PBP/boxscore can build replay state panels, but calibrated lane and ML backtests "
            "remain blocked until WNBA Polymarket CLOB tick/trade history is captured."
        )
    if counts.schedule_games > 0:
        return (
            "WNBA schedule is available, but lane calibration, ML training, and replay backtests are not "
            "sufficiently supported until boxscore/PBP and WNBA CLOB observations are persisted."
        )
    if ml_blockers:
        return "WNBA ML training is blocked by missing labeled before/after CLOB price windows."
    return "WNBA data is not sufficient for lane design or ML training yet."
