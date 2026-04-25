from __future__ import annotations

from app.data.pipelines.daily.nba.analysis.backtests.halftime_gap_fill import simulate_halftime_gap_fill_trades
from app.data.pipelines.daily.nba.analysis.backtests.inversion import simulate_inversion_trades
from app.data.pipelines.daily.nba.analysis.backtests.lead_fragility import simulate_lead_fragility_trades
from app.data.pipelines.daily.nba.analysis.backtests.micro_momentum_continuation import (
    simulate_micro_momentum_continuation_trades,
)
from app.data.pipelines.daily.nba.analysis.backtests.panic_fade_fast import simulate_panic_fade_fast_trades
from app.data.pipelines.daily.nba.analysis.backtests.quarter_open_reprice import simulate_quarter_open_reprice_trades
from app.data.pipelines.daily.nba.analysis.backtests.q1_repricing import simulate_q1_repricing_trades
from app.data.pipelines.daily.nba.analysis.backtests.q4_clutch import simulate_q4_clutch_trades
from app.data.pipelines.daily.nba.analysis.backtests.specs import StrategyDefinition
from app.data.pipelines.daily.nba.analysis.backtests.underdog_liftoff import simulate_underdog_liftoff_trades
from app.data.pipelines.daily.nba.analysis.backtests.winner_definition import simulate_winner_definition_trades


DEFAULT_STRATEGY_GROUP = "default"
REPLAY_HF_STRATEGY_GROUP = "replay_hf"


def build_strategy_registry(*, strategy_group: str = DEFAULT_STRATEGY_GROUP) -> dict[str, StrategyDefinition]:
    definitions = [
        StrategyDefinition(
            family="inversion",
            entry_rule="dynamic_cross_above_45c_or_50c_with_momentum",
            exit_rule="break_back_below_49c_or_end",
            description="Dynamic underdog continuation with deeper early entries for live underdogs and a tighter 49c protection line.",
            comparator_group="underdog_continuation",
            tags=("underdog", "continuation", "dynamic_thresholds"),
            simulator=simulate_inversion_trades,
        ),
        StrategyDefinition(
            family="winner_definition",
            entry_rule="reach_80c",
            exit_rule="dynamic_break_75c_or_76c_or_end",
            description="Winner-definition continuation after 80c with a slightly wider break line for stronger scoreboard control.",
            comparator_group="winner_lock",
            tags=("winner_definition", "continuation", "dynamic_exit"),
            simulator=simulate_winner_definition_trades,
        ),
        StrategyDefinition(
            family="underdog_liftoff",
            entry_rule="cross_above_36c_with_momentum",
            exit_rule="hit_50c_or_minus_3c_or_end",
            description="Underdog continuation that buys a rebound through 36c for sub-42c openers with looser scoreboard tolerance and exits at 50c or a 3c stop.",
            comparator_group="underdog_continuation",
            tags=("underdog", "continuation", "rebound_confirmation"),
            simulator=simulate_underdog_liftoff_trades,
        ),
        StrategyDefinition(
            family="q1_repricing",
            entry_rule="q1_cross_plus_7c_with_momentum",
            exit_rule="plus_8c_or_minus_5c_or_end_of_q1",
            description="First-quarter repricing continuation for 25c-75c openers that gain 7c or more with early scoreboard confirmation.",
            comparator_group="opening_band_momentum",
            tags=("q1", "continuation", "repricing"),
            simulator=simulate_q1_repricing_trades,
        ),
        StrategyDefinition(
            family="q4_clutch",
            entry_rule="late_q4_cross_above_55c_in_close_game",
            exit_rule="plus_8c_or_break_back_or_end",
            description="Late-game close-contest continuation after multiple lead changes and a fresh Q4 push above 55c.",
            comparator_group="clutch_volatility",
            tags=("q4", "close_game", "continuation"),
            simulator=simulate_q4_clutch_trades,
        ),
    ]
    if str(strategy_group).strip() == REPLAY_HF_STRATEGY_GROUP:
        definitions.extend(
            [
                StrategyDefinition(
                    family="micro_momentum_continuation",
                    entry_rule="q1_cross_plus_3c_with_fast_momentum_below_70c",
                    exit_rule="plus_4p5c_or_minus_3c_or_within_8_states",
                    description="Short-hold continuation that stays in Q1 and below 70c so the signal can survive replay before the stale window closes.",
                    comparator_group="higher_frequency_continuation",
                    tags=("higher_frequency", "momentum", "short_hold"),
                    simulator=simulate_micro_momentum_continuation_trades,
                ),
                StrategyDefinition(
                    family="panic_fade_fast",
                    entry_rule="favorite_recross_after_10c_panic",
                    exit_rule="recover_to_58c_or_plus_6c_or_minus_4c_or_end",
                    description="Fast favorite panic fade that buys a broader Q2-Q4 recross after a 10c collapse and keeps the setup explicit for replay testing.",
                    comparator_group="higher_frequency_recovery",
                    tags=("higher_frequency", "favorite", "panic_fade"),
                    simulator=simulate_panic_fade_fast_trades,
                ),
                StrategyDefinition(
                    family="quarter_open_reprice",
                    entry_rule="q1_cross_plus_3c_with_early_support_below_72c",
                    exit_rule="plus_6c_or_minus_4c_or_end_of_q1",
                    description="Quarter-open repricing that avoids late high-80c favorite entries and stays inside the Q1 window that survives replay.",
                    comparator_group="higher_frequency_opening_band",
                    tags=("higher_frequency", "q1", "repricing"),
                    simulator=simulate_quarter_open_reprice_trades,
                ),
                StrategyDefinition(
                    family="halftime_gap_fill",
                    entry_rule="early_q3_cross_plus_3c_over_halftime_anchor",
                    exit_rule="plus_5c_or_minus_4c_or_end_of_q3",
                    description="Halftime gap-fill continuation that keys off the halftime close and only trades the first seven minutes of Q3.",
                    comparator_group="higher_frequency_halftime",
                    tags=("higher_frequency", "q3", "gap_fill"),
                    simulator=simulate_halftime_gap_fill_trades,
                ),
                StrategyDefinition(
                    family="lead_fragility",
                    entry_rule="fragile_q3_rebound_entry_38c_to_48c",
                    exit_rule="plus_6c_or_minus_4c_or_end",
                    description="Fragile-lead rebound narrowed to early Q3 and 38c-48c entries so it can stay on the replay-friendly side of the stale gate.",
                    comparator_group="higher_frequency_fragility",
                    tags=("higher_frequency", "fragility", "rebound"),
                    simulator=simulate_lead_fragility_trades,
                ),
            ]
        )
    return {definition.family: definition for definition in definitions}


def resolve_strategy_registry(
    strategy_family: str,
    *,
    strategy_group: str = DEFAULT_STRATEGY_GROUP,
) -> dict[str, StrategyDefinition]:
    registry = build_strategy_registry(strategy_group=strategy_group)
    if strategy_family == "all":
        return registry
    definition = registry.get(strategy_family)
    if definition is None:
        return {}
    return {strategy_family: definition}


__all__ = [
    "DEFAULT_STRATEGY_GROUP",
    "REPLAY_HF_STRATEGY_GROUP",
    "build_strategy_registry",
    "resolve_strategy_registry",
]
