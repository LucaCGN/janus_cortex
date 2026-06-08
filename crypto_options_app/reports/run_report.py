from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from crypto_options_app.reports.strategy_report import CandidateAttributionReport


@dataclass(frozen=True)
class RunReport:
    run_id: str
    generated_at_utc: datetime
    stop_gates: tuple[str, ...]
    risk_blockers: dict[str, int]
    candidate_reports: tuple[CandidateAttributionReport, ...]
    summary: dict[str, Any] = field(default_factory=dict)
    manual_orders_avoided: bool = True


def build_run_report(
    *,
    run_id: str,
    stop_gates: tuple[str, ...],
    candidate_reports: list[CandidateAttributionReport],
    summary: dict[str, Any] | None = None,
) -> RunReport:
    risk_blockers: dict[str, int] = {}
    for report in candidate_reports:
        for blocker, count in report.blocker_counts.items():
            risk_blockers[blocker] = risk_blockers.get(blocker, 0) + count
    return RunReport(
        run_id=run_id,
        generated_at_utc=datetime.now(UTC),
        stop_gates=stop_gates,
        risk_blockers=risk_blockers,
        candidate_reports=tuple(candidate_reports),
        summary=summary or {},
        manual_orders_avoided=True,
    )
