from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CandidateAttributionReport:
    strategy_id: str
    ready_count: int
    blocked_count: int
    executed_count: int
    blocker_counts: dict[str, int] = field(default_factory=dict)
    source_counts: dict[str, int] = field(default_factory=dict)
    rows: tuple[dict[str, Any], ...] = ()


def build_candidate_attribution_report(*, strategy_id: str, candidate_rows: list[dict[str, Any]]) -> CandidateAttributionReport:
    ready = blocked = executed = 0
    blocker_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for row in candidate_rows:
        status = row.get("status")
        if status == "ready":
            ready += 1
        elif status == "blocked":
            blocked += 1
        elif status == "executed":
            executed += 1
        for blocker in row.get("blockers", ()):
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
        for source in row.get("sources", ()):
            source_counts[source] = source_counts.get(source, 0) + 1
    return CandidateAttributionReport(strategy_id, ready, blocked, executed, blocker_counts, source_counts, tuple(candidate_rows))
