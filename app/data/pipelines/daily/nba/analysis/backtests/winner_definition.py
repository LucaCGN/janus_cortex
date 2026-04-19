from __future__ import annotations

from typing import Any

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import DEFAULT_WINNER_DEFINITION_BREAK, DEFAULT_WINNER_DEFINITION_ENTRY, TradeSelection, simulate_trade_loop


def _select_winner_definition_entry(group: pd.DataFrame) -> TradeSelection | None:
    prices = pd.to_numeric(group["team_price"], errors="coerce")
    trigger = prices >= DEFAULT_WINNER_DEFINITION_ENTRY
    if not bool(trigger.any()):
        return None
    entry_index = int(trigger[trigger].index[0])
    return TradeSelection(
        entry_index=entry_index,
        metadata={
            "entry_threshold": DEFAULT_WINNER_DEFINITION_ENTRY,
            "exit_threshold": DEFAULT_WINNER_DEFINITION_BREAK,
        },
    )


def _select_winner_definition_exit(group: pd.DataFrame, selection: TradeSelection) -> int | None:
    exit_threshold = float(selection.metadata["exit_threshold"])
    future = group.iloc[selection.entry_index + 1 :]
    exit_candidates = future[future["team_price"] < exit_threshold]
    if exit_candidates.empty:
        return int(len(group) - 1)
    return int(exit_candidates.index[0])


def simulate_winner_definition_trades(state_df: pd.DataFrame, *, slippage_cents: int) -> list[dict[str, Any]]:
    return simulate_trade_loop(
        state_df,
        strategy_family="winner_definition",
        entry_rule="reach_80c",
        exit_rule="break_75c_or_end",
        slippage_cents=slippage_cents,
        entry_selector=_select_winner_definition_entry,
        exit_selector=_select_winner_definition_exit,
    )


__all__ = ["simulate_winner_definition_trades"]
