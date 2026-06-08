from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from crypto_options_app.replay.exit_simulation import ExitSimulationResult


@dataclass(frozen=True)
class ReplayComponentReport:
    component_id: str
    sample_count: int
    hit_rate: float | None
    average_forward_return: float | None
    blocker_reasons: dict[str, int]
    metrics: dict[str, Any] = field(default_factory=dict)


def build_component_report(
    *,
    component_id: str,
    outcomes: list[ExitSimulationResult],
) -> ReplayComponentReport:
    sample_count = len(outcomes)
    realized = [outcome for outcome in outcomes if outcome.realized]
    wins = [outcome for outcome in realized if outcome.pnl_usd > 0]
    blockers: dict[str, int] = {}
    for outcome in outcomes:
        for blocker in outcome.blockers:
            blockers[blocker] = blockers.get(blocker, 0) + 1
    hit_rate = None if not realized else len(wins) / len(realized)
    average_forward_return = None if not realized else sum(outcome.pnl_usd for outcome in realized) / len(realized)
    return ReplayComponentReport(
        component_id=component_id,
        sample_count=sample_count,
        hit_rate=None if hit_rate is None else round(hit_rate, 6),
        average_forward_return=None if average_forward_return is None else round(average_forward_return, 6),
        blocker_reasons=blockers,
        metrics={
            "realized_count": len(realized),
            "win_count": len(wins),
            "loss_count": len(realized) - len(wins),
        },
    )


def insert_component_report(conn: Any, *, replay_run_key: str | None, report: ReplayComponentReport) -> None:
    key = _stable_key("component_report", replay_run_key, report.component_id, datetime.now(UTC).isoformat())
    conn.execute(
        """
        INSERT INTO replay_component_results(
            replay_component_result_key, replay_run_key, component_id, sample_count,
            hit_rate, average_forward_return, blocker_reasons_json, metrics_json, inserted_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            key,
            replay_run_key,
            report.component_id,
            report.sample_count,
            report.hit_rate,
            report.average_forward_return,
            json.dumps(report.blocker_reasons, sort_keys=True, default=str),
            json.dumps(report.metrics, sort_keys=True, default=str),
            datetime.now(UTC).isoformat(),
        ),
    )


def _stable_key(*parts: Any) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
