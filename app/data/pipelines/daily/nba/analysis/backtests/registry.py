from __future__ import annotations

from app.data.pipelines.daily.nba.analysis.backtests.comeback_reversion import simulate_comeback_reversion_trades
from app.data.pipelines.daily.nba.analysis.backtests.inversion import simulate_inversion_trades
from app.data.pipelines.daily.nba.analysis.backtests.q1_repricing import simulate_q1_repricing_trades
from app.data.pipelines.daily.nba.analysis.backtests.q4_clutch import simulate_q4_clutch_trades
from app.data.pipelines.daily.nba.analysis.backtests.reversion import simulate_reversion_trades
from app.data.pipelines.daily.nba.analysis.backtests.specs import StrategyDefinition
from app.data.pipelines.daily.nba.analysis.backtests.underdog_liftoff import simulate_underdog_liftoff_trades
from app.data.pipelines.daily.nba.analysis.backtests.volatility_scalp import simulate_volatility_scalp_trades
from app.data.pipelines.daily.nba.analysis.backtests.winner_definition import simulate_winner_definition_trades


def build_strategy_registry() -> dict[str, StrategyDefinition]:
    definitions = [
        StrategyDefinition(
            family="reversion",
            entry_rule="favorite_drawdown_buy_10c",
            exit_rule="reclaim_open_minus_2c_or_end",
            description="Favorite drawdown reversion after a 10c drop from the opening price.",
            comparator_group="favorite_reversion",
            tags=("favorite", "reversion", "drawdown"),
            simulator=simulate_reversion_trades,
        ),
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
            family="comeback_reversion",
            entry_rule="q2_q3_underdog_trail_buy_rebound",
            exit_rule="plus_8c_or_minus_6c_or_end",
            description="Underdog comeback reversion when Q2 or Q3 momentum improves despite a 5+ point deficit.",
            comparator_group="underdog_reversion",
            tags=("underdog", "comeback", "q2_q3"),
            simulator=simulate_comeback_reversion_trades,
        ),
        StrategyDefinition(
            family="volatility_scalp",
            entry_rule="q1_midband_drawdown_scalp",
            exit_rule="partial_reclaim_or_minus_5c_or_end",
            description="Opening-band volatility scalp for mid-band teams that drop 12c or more in Q1.",
            comparator_group="opening_band_scalp",
            tags=("mid_band", "q1", "scalp"),
            simulator=simulate_volatility_scalp_trades,
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
    return {definition.family: definition for definition in definitions}


def resolve_strategy_registry(strategy_family: str) -> dict[str, StrategyDefinition]:
    registry = build_strategy_registry()
    if strategy_family == "all":
        return registry
    definition = registry.get(strategy_family)
    if definition is None:
        return {}
    return {strategy_family: definition}


__all__ = [
    "build_strategy_registry",
    "resolve_strategy_registry",
]
