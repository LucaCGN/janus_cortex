from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from crypto_options_app.strategies.schema import StrategySpec, validate_strategy_spec


STRATEGY_STATES = {
    "draft",
    "validated",
    "replay_ready",
    "replay_rejected",
    "pulse_ready",
    "pulse_rejected",
    "standard_test_ready",
    "promoted",
    "disabled",
}


@dataclass(frozen=True)
class StrategyReadiness:
    strategy_id: str
    readiness_state: str
    blockers: tuple[str, ...]
    evidence: dict[str, Any]
    orders_allowed: bool = False
    live_trading_authorized: bool = False


def evaluate_replay_readiness(spec: StrategySpec, *, data_blockers: tuple[str, ...] = ()) -> StrategyReadiness:
    validation = validate_strategy_spec(spec)
    blockers = list(validation.errors)
    blockers.extend(data_blockers)
    state = "replay_ready" if not blockers else "blocked"
    return StrategyReadiness(
        strategy_id=spec.strategy_id,
        readiness_state=state,
        blockers=tuple(blockers),
        evidence={"schema_valid": validation.valid, "data_blockers": data_blockers},
    )


def evaluate_pulse_readiness(spec: StrategySpec, *, replay_rejected: bool, executor_boundary_configured: bool) -> StrategyReadiness:
    validation = validate_strategy_spec(spec)
    blockers = list(validation.errors)
    if replay_rejected:
        blockers.append("replay_rejected")
    if not executor_boundary_configured:
        blockers.append("executor_boundary_not_configured")
    if spec.exit_rules.get("lifecycle_coverage") is None:
        blockers.append("lifecycle_coverage_missing")
    state = "pulse_ready" if not blockers else "blocked"
    return StrategyReadiness(
        strategy_id=spec.strategy_id,
        readiness_state=state,
        blockers=tuple(blockers),
        evidence={"schema_valid": validation.valid, "replay_rejected": replay_rejected},
    )


def transition_strategy_state(*, current_state: str, next_state: str, evidence: dict[str, Any] | None) -> str:
    if current_state not in STRATEGY_STATES:
        raise ValueError(f"unknown current state: {current_state}")
    if next_state not in STRATEGY_STATES:
        raise ValueError(f"unknown next state: {next_state}")
    if not evidence:
        raise ValueError("strategy state transitions require evidence")
    return next_state
