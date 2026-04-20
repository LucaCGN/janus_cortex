from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd


@dataclass(slots=True)
class TradeSelection:
    entry_index: int
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    family: str
    entry_rule: str
    exit_rule: str
    description: str
    comparator_group: str
    tags: tuple[str, ...]
    simulator: Callable[[pd.DataFrame, int], list[dict[str, Any]]]


@dataclass(slots=True)
class BacktestResult:
    payload: dict[str, Any]
    trade_frames: dict[str, pd.DataFrame]
    state_df: pd.DataFrame
    strategy_registry: dict[str, StrategyDefinition]


__all__ = [
    "BacktestResult",
    "StrategyDefinition",
    "TradeSelection",
]
