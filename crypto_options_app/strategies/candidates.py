from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from crypto_options_app.strategies.schema import StrategySpec, validate_strategy_spec


@dataclass(frozen=True)
class StrategyCandidateRecord:
    strategy_id: str
    status: str
    candidate: dict[str, Any] | None
    blockers: tuple[str, ...]
    orders_allowed: bool = False
    live_trading_authorized: bool = False


def build_structural_candidate(
    spec: StrategySpec,
    *,
    event_key: str | None,
    event_token_key: str | None,
    side: str | None = None,
) -> StrategyCandidateRecord:
    if not spec.enabled:
        return StrategyCandidateRecord(spec.strategy_id, "disabled", None, ("strategy_disabled",))
    validation = validate_strategy_spec(spec)
    if not validation.valid:
        return StrategyCandidateRecord(spec.strategy_id, "blocked", None, validation.errors)
    if not event_token_key:
        return StrategyCandidateRecord(spec.strategy_id, "blocked", None, ("missing_event_token_key",))
    return StrategyCandidateRecord(
        spec.strategy_id,
        "candidate_ready",
        {
            "strategy_id": spec.strategy_id,
            "strategy_version": spec.strategy_version,
            "event_key": event_key,
            "event_token_key": event_token_key,
            "side": side,
            "requires_executor_boundary": True,
        },
        (),
    )
