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
    min_price_history_points_for_sample_backtest: int = 500
    min_games_with_price_history_for_sample_backtest: int = 1
    min_ml_feature_rows_for_experiment: int = 5000
    min_distinct_games_for_ml_experiment: int = 40


@dataclass(frozen=True)
class WnbaLaneSpec:
    lane_id: str
    family: str
    entry_rule: str = "provisional_wnba_shadow_rule"
    exit_rule: str = "shadow_target_stop_or_horizon"
    description: str = "WNBA provisional shadow-only lane."
    comparator_group: str = "wnba_shadow"
    tags: tuple[str, ...] = ("wnba", "shadow_only")
    entry_price_min: float | None = None
    entry_price_max: float | None = None
    min_score_diff: int | None = None
    max_score_diff: int | None = None
    min_period: int | None = None
    max_period: int | None = None
    min_seconds_to_game_end: int | None = None
    max_seconds_to_game_end: int | None = None
    min_clock_elapsed_seconds: int | None = None
    max_clock_elapsed_seconds: int | None = None
    min_recent_net_points: int | None = None
    max_recent_net_points: int | None = None
    min_price_delta_from_open: float | None = None
    max_price_delta_from_open: float | None = None
    max_spread: float | None = 0.03
    target_return_fraction: float = 0.10
    min_target_move: float = 0.01
    stop_loss: float | None = 0.04
    max_horizon_states: int = 12
    requires_clob: bool = True
    requires_trade_microstructure: bool = False
    shadow_only: bool = True
    orders_allowed: bool = False


def default_shadow_lane_specs() -> tuple[WnbaLaneSpec, ...]:
    """Provisional WNBA lane specs for shadow research, not live trading authority."""
    return (
        WnbaLaneSpec(
            lane_id="wnba_underdog_range_scalp_shadow_v0",
            family="underdog_range_scalp",
            entry_rule="close_score_underdog_buy_20c_to_38c_rebound",
            exit_rule="plus_6c_or_minus_4c_or_12_state_horizon",
            description="WNBA close-score underdog range scalp, widened slightly from NBA until WNBA liquidity is measured.",
            comparator_group="higher_frequency_underdog_range",
            tags=("wnba", "higher_frequency", "underdog", "range_scalp", "shadow_only"),
            entry_price_min=0.20,
            entry_price_max=0.38,
            min_score_diff=-9,
            max_score_diff=4,
            max_period=4,
            target_return_fraction=0.12,
            min_target_move=0.06,
        ),
        WnbaLaneSpec(
            lane_id="wnba_favorite_floor_rebound_shadow_v0",
            family="favorite_floor_rebound",
            entry_rule="collapsed_favorite_10c_to_26c_when_score_gap_recoverable",
            exit_rule="plus_7c_or_minus_4c_or_14_state_horizon",
            description="WNBA collapsed-favorite rebound candidate; pregame favorite identity is a future enrichment but the price/score harness is present.",
            comparator_group="higher_frequency_favorite_rebound",
            tags=("wnba", "higher_frequency", "favorite", "panic_fade", "shadow_only"),
            entry_price_min=0.10,
            entry_price_max=0.26,
            min_score_diff=-12,
            max_score_diff=2,
            min_period=2,
            max_period=4,
            min_recent_net_points=-2,
            target_return_fraction=0.16,
            min_target_move=0.07,
            max_horizon_states=14,
        ),
        WnbaLaneSpec(
            lane_id="wnba_panic_fade_fast_shadow_v0",
            family="panic_fade_fast",
            entry_rule="fast_recovering_side_after_panic_drop_22c_to_58c",
            exit_rule="plus_6c_or_minus_4c_or_10_state_horizon",
            description="WNBA fast panic-fade scaffold that requires a recent scoring response and avoids assuming NBA-sized clock pressure.",
            comparator_group="higher_frequency_recovery",
            tags=("wnba", "higher_frequency", "panic_fade", "shadow_only"),
            entry_price_min=0.22,
            entry_price_max=0.58,
            min_score_diff=-10,
            max_score_diff=5,
            min_period=2,
            max_period=4,
            min_recent_net_points=2,
            target_return_fraction=0.12,
            min_target_move=0.06,
            max_horizon_states=10,
        ),
        WnbaLaneSpec(
            lane_id="wnba_quarter_open_reprice_shadow_v0",
            family="quarter_open_reprice",
            entry_rule="early_q1_cross_plus_3c_with_scoreboard_support",
            exit_rule="plus_6c_or_minus_4c_or_end_of_q1_window",
            description="WNBA quarter-open repricing harness limited to early Q1 because WNBA games are 40 minutes with 10-minute quarters.",
            comparator_group="higher_frequency_opening_band",
            tags=("wnba", "higher_frequency", "q1", "repricing", "shadow_only"),
            entry_price_min=0.25,
            entry_price_max=0.72,
            min_score_diff=-5,
            max_score_diff=9,
            min_period=1,
            max_period=1,
            max_clock_elapsed_seconds=420,
            min_price_delta_from_open=0.03,
            target_return_fraction=0.10,
            min_target_move=0.06,
            max_horizon_states=10,
        ),
        WnbaLaneSpec(
            lane_id="wnba_halftime_gap_fill_shadow_v0",
            family="halftime_gap_fill",
            entry_rule="early_q3_continuation_with_recoverable_gap",
            exit_rule="plus_5c_or_minus_4c_or_end_of_q3_window",
            description="WNBA halftime gap-fill scaffold keyed to the first seven minutes of Q3 on a 40-minute game clock.",
            comparator_group="higher_frequency_halftime",
            tags=("wnba", "higher_frequency", "q3", "gap_fill", "shadow_only"),
            entry_price_min=0.30,
            entry_price_max=0.74,
            min_score_diff=-8,
            max_score_diff=10,
            min_period=3,
            max_period=3,
            min_clock_elapsed_seconds=1200,
            max_clock_elapsed_seconds=1620,
            min_price_delta_from_open=0.01,
            target_return_fraction=0.09,
            min_target_move=0.05,
            max_horizon_states=12,
        ),
        WnbaLaneSpec(
            lane_id="wnba_lead_fragility_shadow_v0",
            family="lead_fragility",
            entry_rule="fragile_leader_38c_to_54c_with_single_digit_lead",
            exit_rule="plus_6c_or_minus_4c_or_12_state_horizon",
            description="WNBA fragile-lead continuation/rebound harness for thin leads that may be mispriced by low-liquidity markets.",
            comparator_group="higher_frequency_fragility",
            tags=("wnba", "higher_frequency", "fragility", "shadow_only"),
            entry_price_min=0.38,
            entry_price_max=0.54,
            min_score_diff=1,
            max_score_diff=8,
            min_period=2,
            max_period=4,
            target_return_fraction=0.11,
            min_target_move=0.06,
            max_horizon_states=12,
        ),
        WnbaLaneSpec(
            lane_id="wnba_micro_grid_reprice_shadow_v0",
            family="micro_grid_reprice",
            entry_rule="low_spread_any_side_5c_to_85c_micro_reprice",
            exit_rule="plus_1c_or_scaled_10pct_or_minus_3c",
            description="WNBA passive micro-grid scaffold. Requires trade microstructure before it can be considered calibrated.",
            comparator_group="higher_frequency_micro_grid",
            tags=("wnba", "higher_frequency", "micro_grid", "liquidity_sensitive", "shadow_only"),
            entry_price_min=0.05,
            entry_price_max=0.85,
            min_score_diff=-8,
            max_score_diff=8,
            max_spread=0.02,
            target_return_fraction=0.10,
            min_target_move=0.01,
            stop_loss=0.03,
            max_horizon_states=8,
            requires_trade_microstructure=True,
        ),
        WnbaLaneSpec(
            lane_id="wnba_q4_clutch_shadow_v0",
            family="q4_clutch",
            entry_rule="late_q4_close_game_35c_to_65c_continuation",
            exit_rule="plus_8c_or_minus_4c_or_final_horizon",
            description="WNBA late-game clutch scaffold using 10-minute Q4 timing rather than NBA 12-minute assumptions.",
            comparator_group="clutch_volatility",
            tags=("wnba", "q4", "close_game", "clutch", "shadow_only"),
            entry_price_min=0.35,
            entry_price_max=0.65,
            min_score_diff=-5,
            max_score_diff=5,
            min_period=4,
            max_period=4,
            max_seconds_to_game_end=360,
            target_return_fraction=0.13,
            min_target_move=0.08,
            max_horizon_states=16,
        ),
        WnbaLaneSpec(
            lane_id="wnba_winner_definition_shadow_v0",
            family="winner_definition",
            entry_rule="reach_78c_with_positive_scoreboard_control",
            exit_rule="break_73c_or_end",
            description="WNBA winner-definition scaffold with lower provisional entry than NBA until market lock timing is measured.",
            comparator_group="winner_lock",
            tags=("wnba", "winner_definition", "continuation", "shadow_only"),
            entry_price_min=0.78,
            entry_price_max=0.97,
            min_score_diff=2,
            max_period=5,
            target_return_fraction=0.08,
            min_target_move=0.04,
            stop_loss=0.05,
            max_horizon_states=20,
        ),
    )


def default_shadow_lane_families() -> tuple[str, ...]:
    return tuple(lane.family for lane in default_shadow_lane_specs())
