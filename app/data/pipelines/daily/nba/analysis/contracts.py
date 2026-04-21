from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ANALYSIS_VERSION = "v1_0_1"
DEFAULT_SEASON = "2025-26"
DEFAULT_SEASON_PHASE = "regular_season"
DEFAULT_LOCAL_ROOT_ENV_VAR = "JANUS_LOCAL_ROOT"
WINDOWS_LOCAL_ROOT = Path(r"C:\code-personal\janus-local\janus_cortex")


def resolve_default_output_root() -> Path:
    configured_root = os.getenv(DEFAULT_LOCAL_ROOT_ENV_VAR)
    if configured_root:
        return Path(configured_root) / "archives" / "output" / "nba_analysis"
    if os.name == "nt" and WINDOWS_LOCAL_ROOT.exists():
        return WINDOWS_LOCAL_ROOT / "archives" / "output" / "nba_analysis"
    return Path("output") / "nba_analysis"


DEFAULT_OUTPUT_ROOT = resolve_default_output_root()
DEFAULT_LOOKAHEAD_STATES = 12
DEFAULT_LARGE_SWING_THRESHOLD = 0.10
DEFAULT_WINNER_THRESHOLDS = (70, 80, 90, 95)
DEFAULT_REVERSION_OPEN_THRESHOLD = 0.70
DEFAULT_REVERSION_DRAWDOWN = 0.10
DEFAULT_REVERSION_EXIT_BUFFER = 0.02
DEFAULT_INVERSION_ENTRY_THRESHOLD = 0.50
DEFAULT_INVERSION_EXIT_THRESHOLD = 0.50
DEFAULT_DYNAMIC_INVERSION_LOW_OPEN_CUT = 0.25
DEFAULT_DYNAMIC_INVERSION_LOW_ENTRY_THRESHOLD = 0.45
DEFAULT_DYNAMIC_INVERSION_STANDARD_ENTRY_THRESHOLD = 0.50
DEFAULT_DYNAMIC_INVERSION_EXIT_THRESHOLD = 0.49
DEFAULT_DYNAMIC_INVERSION_MIN_MOMENTUM = 2.0
DEFAULT_DYNAMIC_INVERSION_MIN_SCORE_DIFF = -6.0
DEFAULT_WINNER_DEFINITION_ENTRY = 0.80
DEFAULT_WINNER_DEFINITION_BREAK = 0.75
DEFAULT_WINNER_DEFINITION_BIG_LEAD_BREAK = 0.76
DEFAULT_WINNER_DEFINITION_BIG_LEAD_SCORE_DIFF = 8.0
DEFAULT_UNDERDOG_LIFTOFF_OPEN_CAP = 0.42
DEFAULT_UNDERDOG_LIFTOFF_ENTRY_THRESHOLD = 0.36
DEFAULT_UNDERDOG_LIFTOFF_MIN_MOMENTUM = 1.0
DEFAULT_UNDERDOG_LIFTOFF_MIN_SCORE_DIFF = -4.0
DEFAULT_UNDERDOG_LIFTOFF_MIN_SECONDS_LEFT = 900.0
DEFAULT_UNDERDOG_LIFTOFF_TARGET_PRICE = 0.50
DEFAULT_UNDERDOG_LIFTOFF_STOP_LOSS = 0.03
DEFAULT_Q1_REPRICING_OPEN_MIN = 0.25
DEFAULT_Q1_REPRICING_OPEN_MAX = 0.75
DEFAULT_Q1_REPRICING_ENTRY_MOVE = 0.07
DEFAULT_Q1_REPRICING_MIN_PRICE = 0.52
DEFAULT_Q1_REPRICING_MIN_MOMENTUM = 3.0
DEFAULT_Q1_REPRICING_MIN_SCORE_DIFF = 1.0
DEFAULT_Q1_REPRICING_TARGET_MOVE = 0.08
DEFAULT_Q1_REPRICING_STOP_LOSS = 0.05
DEFAULT_Q1_REPRICING_MAX_CLOCK_ELAPSED = 600.0
DEFAULT_Q4_CLUTCH_OPEN_MIN = 0.25
DEFAULT_Q4_CLUTCH_OPEN_MAX = 0.75
DEFAULT_Q4_CLUTCH_ENTRY_THRESHOLD = 0.55
DEFAULT_Q4_CLUTCH_MIN_MOMENTUM = 2.0
DEFAULT_Q4_CLUTCH_MAX_SCORE_MARGIN = 6.0
DEFAULT_Q4_CLUTCH_MIN_SCORE_DIFF = 1.0
DEFAULT_Q4_CLUTCH_MIN_LEAD_CHANGES = 2
DEFAULT_Q4_CLUTCH_MAX_SECONDS_LEFT = 300.0
DEFAULT_Q4_CLUTCH_TARGET_PRICE = 0.72
DEFAULT_Q4_CLUTCH_STOP_LOSS = 0.06
BACKTEST_BENCHMARK_CONTRACT_VERSION = "v7"
DEFAULT_BACKTEST_HOLDOUT_RATIO = 0.10
DEFAULT_BACKTEST_HOLDOUT_SEED = 1107
DEFAULT_BACKTEST_ROBUSTNESS_SEEDS = (1107, 2113, 3251, 4421, 5573)
DEFAULT_BACKTEST_MIN_TRADE_COUNT = 20
DEFAULT_BACKTEST_PORTFOLIO_INITIAL_BANKROLL = 10.0
DEFAULT_BACKTEST_PORTFOLIO_POSITION_SIZE_FRACTION = 1.0
DEFAULT_BACKTEST_PORTFOLIO_GAME_LIMIT = 100
DEFAULT_BACKTEST_PORTFOLIO_MIN_ORDER_DOLLARS = 1.0
DEFAULT_BACKTEST_PORTFOLIO_MIN_SHARES = 5.0
DEFAULT_BACKTEST_PORTFOLIO_MAX_CONCURRENT_POSITIONS = 3
DEFAULT_BACKTEST_PORTFOLIO_CONCURRENCY_MODE = "shared_cash_equal_split"
DEFAULT_OPENING_BAND_SIZE = 10
REGULATION_PERIOD_SECONDS = 12 * 60
OVERTIME_PERIOD_SECONDS = 5 * 60
RESEARCH_READY_STATUSES = {"covered_pre_and_ingame"}
DESCRIPTIVE_ONLY_STATUSES = {"pregame_only", "covered_partial", "no_matching_event"}

GAME_PROFILE_COLUMNS = (
    "game_id",
    "team_side",
    "team_id",
    "team_slug",
    "opponent_team_id",
    "opponent_team_slug",
    "event_id",
    "market_id",
    "outcome_id",
    "season",
    "season_phase",
    "analysis_version",
    "computed_at",
    "game_date",
    "game_start_time",
    "coverage_status",
    "research_ready_flag",
    "price_path_reconciled_flag",
    "final_winner_flag",
    "opening_price",
    "closing_price",
    "opening_band",
    "opening_band_rank",
    "pregame_price_min",
    "pregame_price_max",
    "pregame_price_range",
    "ingame_price_min",
    "ingame_price_max",
    "ingame_price_range",
    "total_price_min",
    "total_price_max",
    "total_swing",
    "max_favorable_excursion",
    "max_adverse_excursion",
    "inversion_count",
    "first_inversion_at",
    "seconds_above_50c",
    "seconds_below_50c",
    "winner_stable_70_at",
    "winner_stable_80_at",
    "winner_stable_90_at",
    "winner_stable_95_at",
    "winner_stable_70_clock_elapsed_seconds",
    "winner_stable_80_clock_elapsed_seconds",
    "winner_stable_90_clock_elapsed_seconds",
    "winner_stable_95_clock_elapsed_seconds",
    "notes_json",
)

STATE_PANEL_COLUMNS = (
    "game_id",
    "team_side",
    "state_index",
    "team_id",
    "team_slug",
    "opponent_team_id",
    "opponent_team_slug",
    "event_id",
    "market_id",
    "outcome_id",
    "season",
    "season_phase",
    "analysis_version",
    "computed_at",
    "game_date",
    "event_index",
    "action_id",
    "event_at",
    "period",
    "period_label",
    "clock",
    "clock_elapsed_seconds",
    "seconds_to_game_end",
    "score_for",
    "score_against",
    "score_diff",
    "score_diff_bucket",
    "context_bucket",
    "team_led_flag",
    "team_trailed_flag",
    "tied_flag",
    "market_favorite_flag",
    "scoreboard_control_mismatch_flag",
    "final_winner_flag",
    "scoring_side",
    "points_scored",
    "delta_for",
    "delta_against",
    "lead_changes_so_far",
    "team_points_last_5_events",
    "opponent_points_last_5_events",
    "net_points_last_5_events",
    "opening_price",
    "opening_band",
    "team_price",
    "price_delta_from_open",
    "abs_price_delta_from_open",
    "price_mode",
    "gap_before_seconds",
    "gap_after_seconds",
    "mfe_from_state",
    "mae_from_state",
    "large_swing_next_12_states_flag",
    "crossed_50c_next_12_states_flag",
    "winner_stable_70_after_state_flag",
    "winner_stable_80_after_state_flag",
    "winner_stable_90_after_state_flag",
    "winner_stable_95_after_state_flag",
)

TEAM_SEASON_PROFILE_COLUMNS = (
    "team_id",
    "team_slug",
    "season",
    "season_phase",
    "analysis_version",
    "computed_at",
    "sample_games",
    "research_ready_games",
    "wins",
    "losses",
    "favorite_games",
    "underdog_games",
    "avg_opening_price",
    "avg_closing_price",
    "avg_pregame_range",
    "avg_ingame_range",
    "avg_total_swing",
    "avg_max_favorable_excursion",
    "avg_max_adverse_excursion",
    "avg_inversion_count",
    "games_with_inversion",
    "inversion_rate",
    "avg_seconds_above_50c",
    "avg_seconds_below_50c",
    "avg_favorite_drawdown",
    "avg_underdog_spike",
    "control_confidence_mismatch_rate",
    "opening_price_trend_slope",
    "winner_stable_70_rate",
    "winner_stable_80_rate",
    "winner_stable_90_rate",
    "winner_stable_95_rate",
    "avg_winner_stable_70_clock_elapsed_seconds",
    "avg_winner_stable_80_clock_elapsed_seconds",
    "avg_winner_stable_90_clock_elapsed_seconds",
    "avg_winner_stable_95_clock_elapsed_seconds",
    "rolling_10_json",
    "rolling_20_json",
    "notes_json",
)

OPENING_BAND_PROFILE_COLUMNS = (
    "season",
    "season_phase",
    "opening_band",
    "analysis_version",
    "computed_at",
    "sample_games",
    "win_rate",
    "avg_opening_price",
    "avg_closing_price",
    "avg_ingame_range",
    "avg_total_swing",
    "avg_max_favorable_excursion",
    "avg_max_adverse_excursion",
    "avg_inversion_count",
    "inversion_rate",
    "winner_stable_70_rate",
    "winner_stable_80_rate",
    "winner_stable_90_rate",
    "winner_stable_95_rate",
    "notes_json",
)

WINNER_DEFINITION_PROFILE_COLUMNS = (
    "season",
    "season_phase",
    "threshold_cents",
    "context_bucket",
    "analysis_version",
    "computed_at",
    "sample_states",
    "distinct_games",
    "stable_states",
    "stable_rate",
    "reopen_rate",
    "avg_score_diff",
    "avg_team_price",
    "avg_seconds_to_game_end",
    "notes_json",
)


@dataclass(slots=True)
class AnalysisUniverseRequest:
    season: str = DEFAULT_SEASON
    season_phase: str = DEFAULT_SEASON_PHASE
    coverage_filter: str = "all"
    analysis_version: str = ANALYSIS_VERSION


@dataclass(slots=True)
class AnalysisMartBuildRequest:
    season: str = DEFAULT_SEASON
    season_phase: str = DEFAULT_SEASON_PHASE
    rebuild: bool = False
    game_ids: list[str] | None = None
    analysis_version: str = ANALYSIS_VERSION
    output_root: str | None = None


@dataclass(slots=True)
class BacktestRunRequest:
    season: str = DEFAULT_SEASON
    season_phase: str = DEFAULT_SEASON_PHASE
    strategy_family: str = "all"
    entry_rule: str | None = None
    exit_rule: str | None = None
    slippage_cents: int = 0
    train_cutoff: str | None = None
    holdout_ratio: float = DEFAULT_BACKTEST_HOLDOUT_RATIO
    holdout_seed: int = DEFAULT_BACKTEST_HOLDOUT_SEED
    robustness_seeds: tuple[int, ...] = DEFAULT_BACKTEST_ROBUSTNESS_SEEDS
    min_trade_count: int = DEFAULT_BACKTEST_MIN_TRADE_COUNT
    portfolio_initial_bankroll: float = DEFAULT_BACKTEST_PORTFOLIO_INITIAL_BANKROLL
    portfolio_position_size_fraction: float = DEFAULT_BACKTEST_PORTFOLIO_POSITION_SIZE_FRACTION
    portfolio_game_limit: int | None = DEFAULT_BACKTEST_PORTFOLIO_GAME_LIMIT
    portfolio_min_order_dollars: float = DEFAULT_BACKTEST_PORTFOLIO_MIN_ORDER_DOLLARS
    portfolio_min_shares: float = DEFAULT_BACKTEST_PORTFOLIO_MIN_SHARES
    portfolio_max_concurrent_positions: int = DEFAULT_BACKTEST_PORTFOLIO_MAX_CONCURRENT_POSITIONS
    portfolio_concurrency_mode: str = DEFAULT_BACKTEST_PORTFOLIO_CONCURRENCY_MODE
    analysis_version: str = ANALYSIS_VERSION
    output_root: str | None = None


@dataclass(slots=True)
class AnalysisConsumerRequest:
    season: str = DEFAULT_SEASON
    season_phase: str = DEFAULT_SEASON_PHASE
    analysis_version: str | None = None
    backtest_experiment_id: str | None = None
    output_root: str | None = None


@dataclass(slots=True)
class ModelRunRequest:
    season: str = DEFAULT_SEASON
    season_phase: str = DEFAULT_SEASON_PHASE
    target_family: str = "all"
    train_cutoff: str | None = None
    validation_window: str | None = None
    feature_set_version: str = ANALYSIS_VERSION
    analysis_version: str = ANALYSIS_VERSION
    output_root: str | None = None
