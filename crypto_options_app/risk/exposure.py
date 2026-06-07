from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExposureSnapshot:
    strategy_id: str
    active_cost_usd: float
    event_exposure_usd: dict[str, float] = field(default_factory=dict)
    same_side_exposure_usd: dict[str, float] = field(default_factory=dict)
    correlated_exposure_usd: dict[str, float] = field(default_factory=dict)
    open_position_count: int = 0


def compute_exposure_snapshot(*, strategy_id: str, positions: list[dict[str, Any]]) -> ExposureSnapshot:
    active_cost = 0.0
    event_exposure: dict[str, float] = {}
    side_exposure: dict[str, float] = {}
    correlated_exposure: dict[str, float] = {}
    open_positions = [position for position in positions if position.get("status") == "open"]
    for position in open_positions:
        cost = float(position.get("cost_basis_usd") or 0.0)
        active_cost += cost
        event_key = str(position.get("event_key") or position.get("event_token_key") or "unknown_event")
        side_key = f"{event_key}:{position.get('side') or position.get('outcome') or 'unknown'}"
        correlated_key = str(position.get("correlated_key") or event_key)
        event_exposure[event_key] = event_exposure.get(event_key, 0.0) + cost
        side_exposure[side_key] = side_exposure.get(side_key, 0.0) + cost
        correlated_exposure[correlated_key] = correlated_exposure.get(correlated_key, 0.0) + cost
    return ExposureSnapshot(
        strategy_id=strategy_id,
        active_cost_usd=round(active_cost, 6),
        event_exposure_usd={key: round(value, 6) for key, value in event_exposure.items()},
        same_side_exposure_usd={key: round(value, 6) for key, value in side_exposure.items()},
        correlated_exposure_usd={key: round(value, 6) for key, value in correlated_exposure.items()},
        open_position_count=len(open_positions),
    )
