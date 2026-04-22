from __future__ import annotations

from typing import Any

import pandas as pd

from app.data.pipelines.daily.nba.analysis.backtests.engine import DEFAULT_WINNER_DEFINITION_BREAK, DEFAULT_WINNER_DEFINITION_ENTRY, simulate_trade_loop
from app.data.pipelines.daily.nba.analysis.backtests.specs import TradeSelection
from app.data.pipelines.daily.nba.analysis.contracts import (
    DEFAULT_WINNER_DEFINITION_BIG_LEAD_BREAK,
    DEFAULT_WINNER_DEFINITION_BIG_LEAD_SCORE_DIFF,
)


def _select_winner_definition_entry(group: pd.DataFrame) -> TradeSelection | None:
    prices = pd.to_numeric(group["team_price"], errors="coerce")
    trigger = prices >= DEFAULT_WINNER_DEFINITION_ENTRY
    if not bool(trigger.any()):
        return None
    entry_index = int(trigger[trigger].index[0])
    entry_row = group.iloc[entry_index]
    entry_score_diff = float(entry_row["score_diff"]) if pd.notna(entry_row["score_diff"]) else None
    exit_threshold = (
        DEFAULT_WINNER_DEFINITION_BIG_LEAD_BREAK
        if entry_score_diff is not None and entry_score_diff >= DEFAULT_WINNER_DEFINITION_BIG_LEAD_SCORE_DIFF
        else DEFAULT_WINNER_DEFINITION_BREAK
    )
    signal_strength = ((float(entry_row["team_price"]) - DEFAULT_WINNER_DEFINITION_ENTRY) * 100.0) + max(
        0.0, float(entry_score_diff or 0.0)
    ) * 0.5
    return TradeSelection(
        entry_index=entry_index,
        metadata={
            "entry_threshold": DEFAULT_WINNER_DEFINITION_ENTRY,
            "exit_threshold": exit_threshold,
            "entry_score_diff": entry_score_diff,
            "signal_strength": signal_strength,
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
    trades = simulate_trade_loop(
        state_df,
        strategy_family="winner_definition",
        entry_rule="reach_80c",
        exit_rule="dynamic_break_75c_or_76c_or_end",
        slippage_cents=slippage_cents,
        entry_selector=_select_winner_definition_entry,
        exit_selector=_select_winner_definition_exit,
    )
    if not trades:
        return []
    # Winner-definition is a continuation thesis, not a side-flip strategy.
    # Keep the first qualifying signal per game and ignore later opposite-side triggers.
    work = (
        pd.DataFrame(trades)
        .assign(entry_at=lambda frame: pd.to_datetime(frame["entry_at"], errors="coerce", utc=True))
        .sort_values(["entry_at", "game_id", "team_side", "entry_state_index"], kind="mergesort", na_position="last")
        .drop_duplicates(subset=["game_id"], keep="first")
    )
    return work.to_dict(orient="records")


__all__ = ["simulate_winner_definition_trades"]
