from __future__ import annotations

from dataclasses import dataclass


WNBA_ANALYSIS_VERSION = "wnba_v0_1"
WNBA_FEATURE_VERSION = "wnba_pbp_ml_v0_1"
WNBA_DEFAULT_SEASON = "2026"
WNBA_DEFAULT_SEASON_PHASE = "regular_season"
WNBA_REGULATION_PERIOD_SECONDS = 10 * 60
WNBA_REGULATION_GAME_SECONDS = 40 * 60
WNBA_OVERTIME_PERIOD_SECONDS = 5 * 60


@dataclass(frozen=True)
class WnbaDataSufficiencyThresholds:
    min_schedule_games_for_lane_design: int = 40
    min_games_with_pbp_for_lane_design: int = 40
    min_games_with_boxscore_for_lane_design: int = 40
    min_market_links_for_replay: int = 20
    min_clob_ticks_for_replay: int = 5000
    min_clob_trades_for_microstructure: int = 250
    min_ml_feature_rows_for_experiment: int = 5000
    min_distinct_games_for_ml_experiment: int = 40


@dataclass(frozen=True)
class WnbaLaneSpec:
    lane_id: str
    family: str
    entry_price_min: float | None = None
    entry_price_max: float | None = None
    min_score_diff: int | None = None
    max_score_diff: int | None = None
    min_period: int | None = None
    max_period: int | None = None
    max_spread: float | None = 0.03
    target_return_fraction: float = 0.10
    min_target_move: float = 0.01
    stop_loss: float | None = 0.04
    max_horizon_states: int = 12


def default_shadow_lane_specs() -> tuple[WnbaLaneSpec, ...]:
    """Provisional WNBA lane specs for shadow research, not live trading authority."""
    return (
        WnbaLaneSpec(
            lane_id="wnba_underdog_range_scalp_shadow_v0",
            family="underdog_range_scalp",
            entry_price_min=0.20,
            entry_price_max=0.38,
            min_score_diff=-9,
            max_score_diff=4,
        ),
        WnbaLaneSpec(
            lane_id="wnba_favorite_floor_rebound_shadow_v0",
            family="favorite_floor_rebound",
            entry_price_min=0.42,
            entry_price_max=0.62,
            min_score_diff=-6,
            max_score_diff=10,
        ),
        WnbaLaneSpec(
            lane_id="wnba_micro_grid_reprice_shadow_v0",
            family="micro_grid_reprice",
            entry_price_min=0.05,
            entry_price_max=0.85,
            min_score_diff=-8,
            max_score_diff=8,
            max_spread=0.02,
            target_return_fraction=0.10,
            min_target_move=0.01,
            stop_loss=0.03,
            max_horizon_states=8,
        ),
        WnbaLaneSpec(
            lane_id="wnba_q4_clutch_shadow_v0",
            family="q4_clutch",
            entry_price_min=0.35,
            entry_price_max=0.65,
            min_score_diff=-5,
            max_score_diff=5,
            min_period=4,
            max_period=4,
            max_horizon_states=16,
        ),
    )
