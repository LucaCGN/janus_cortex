from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class SystemStatusSnapshot:
    generated_at_utc: datetime
    run_id: str
    service_alive: bool
    service_staleness_seconds: float
    live_cadence_count: int
    feed_watermarks: dict[str, Any] = field(default_factory=dict)
    ready_count: int = 0
    blocked_count: int = 0
    executed_count: int = 0
    blockers_by_strategy: dict[str, list[str]] = field(default_factory=dict)
    pnl_usd: float = 0.0
    active_cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)
    manual_orders_avoided: bool = True


def detect_duplicate_cadence(process_commands: list[str], *, service_marker: str) -> bool:
    return sum(1 for command in process_commands if service_marker in command) > 1


def build_system_status_snapshot(
    *,
    run_id: str,
    service_alive: bool,
    service_staleness_seconds: float,
    process_commands: list[str],
    service_marker: str,
    feed_watermarks: dict[str, Any],
    candidate_counts: dict[str, int],
    blockers_by_strategy: dict[str, list[str]],
    pnl_usd: float,
    active_cost_usd: float,
    errors: list[str] | None = None,
) -> SystemStatusSnapshot:
    duplicate = detect_duplicate_cadence(process_commands, service_marker=service_marker)
    all_errors = list(errors or [])
    if duplicate:
        all_errors.append("duplicate_live_cadence")
    if service_staleness_seconds > 300:
        all_errors.append("stale_service")
    return SystemStatusSnapshot(
        generated_at_utc=datetime.now(UTC),
        run_id=run_id,
        service_alive=service_alive,
        service_staleness_seconds=service_staleness_seconds,
        live_cadence_count=sum(1 for command in process_commands if service_marker in command),
        feed_watermarks=feed_watermarks,
        ready_count=candidate_counts.get("ready", 0),
        blocked_count=candidate_counts.get("blocked", 0),
        executed_count=candidate_counts.get("executed", 0),
        blockers_by_strategy=blockers_by_strategy,
        pnl_usd=pnl_usd,
        active_cost_usd=active_cost_usd,
        errors=all_errors,
        manual_orders_avoided=True,
    )
