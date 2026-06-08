from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from crypto_options_app.replay.fill_simulation import FillSimulationResult


@dataclass(frozen=True)
class ExitSimulationResult:
    exit_type: str
    status: str
    pnl_usd: float
    realized: bool
    blockers: tuple[str, ...] = ()
    simulation: dict[str, Any] = field(default_factory=dict)


def simulate_hold_to_settlement(
    *,
    entry_fill: FillSimulationResult,
    entry_outcome: str,
    resolved_outcome: str | None,
) -> ExitSimulationResult:
    if entry_fill.fillability_status not in {"filled", "partial"} or entry_fill.fill_price is None:
        return ExitSimulationResult("hold_to_settlement", "blocked", 0.0, False, ("entry_not_filled",))
    cost = entry_fill.filled_shares * entry_fill.fill_price
    if resolved_outcome is None:
        return ExitSimulationResult(
            "hold_to_settlement",
            "mark_to_market_unknown_settlement",
            -cost,
            False,
            ("unknown_settlement",),
            {"cost": cost},
        )
    payout = entry_fill.filled_shares if _normalize_outcome(entry_outcome) == _normalize_outcome(resolved_outcome) else 0.0
    return ExitSimulationResult(
        "hold_to_settlement",
        "settled",
        round(payout - cost, 6),
        True,
        (),
        {"cost": cost, "payout": payout},
    )


def simulate_cashout(
    *,
    entry_fill: FillSimulationResult,
    exit_fill: FillSimulationResult,
) -> ExitSimulationResult:
    if entry_fill.fillability_status not in {"filled", "partial"} or entry_fill.fill_price is None:
        return ExitSimulationResult("cashout", "blocked", 0.0, False, ("entry_not_filled",))
    if exit_fill.fillability_status not in {"filled", "partial"} or exit_fill.fill_price is None:
        return ExitSimulationResult("cashout", "blocked", 0.0, False, ("exit_not_filled", *exit_fill.blockers))
    shares = min(entry_fill.filled_shares, exit_fill.filled_shares)
    pnl = shares * exit_fill.fill_price - shares * entry_fill.fill_price
    return ExitSimulationResult(
        "cashout",
        "closed",
        round(pnl, 6),
        True,
        (),
        {"entry_price": entry_fill.fill_price, "exit_price": exit_fill.fill_price, "shares": shares},
    )


def simulate_no_exit_loser(entry_fill: FillSimulationResult) -> ExitSimulationResult:
    if entry_fill.fillability_status not in {"filled", "partial"} or entry_fill.fill_price is None:
        return ExitSimulationResult("no_exit_loser", "blocked", 0.0, False, ("entry_not_filled",))
    cost = entry_fill.filled_shares * entry_fill.fill_price
    return ExitSimulationResult("no_exit_loser", "settled_loser", round(-cost, 6), True, (), {"cost": cost})


def _normalize_outcome(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"yes", "up"}:
        return "up"
    if normalized in {"no", "down"}:
        return "down"
    return normalized
