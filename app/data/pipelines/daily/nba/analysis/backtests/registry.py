from __future__ import annotations

from app.data.pipelines.daily.nba.analysis.backtests.comeback_reversion import simulate_comeback_reversion_trades
from app.data.pipelines.daily.nba.analysis.backtests.inversion import simulate_inversion_trades
from app.data.pipelines.daily.nba.analysis.backtests.reversion import simulate_reversion_trades
from app.data.pipelines.daily.nba.analysis.backtests.specs import StrategyDefinition
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
            entry_rule="first_cross_above_50c",
            exit_rule="break_back_below_50c_or_end",
            description="Underdog continuation once the in-game price first crosses above 50c.",
            comparator_group="underdog_continuation",
            tags=("underdog", "continuation", "50c_cross"),
            simulator=simulate_inversion_trades,
        ),
        StrategyDefinition(
            family="winner_definition",
            entry_rule="reach_80c",
            exit_rule="break_75c_or_end",
            description="Winner-definition continuation after the market reaches 80c.",
            comparator_group="winner_lock",
            tags=("winner_definition", "continuation", "late_game"),
            simulator=simulate_winner_definition_trades,
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
