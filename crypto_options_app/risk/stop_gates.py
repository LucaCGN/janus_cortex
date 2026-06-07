from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunPerformance:
    submitted_entry_groups: int = 0
    closed_entry_groups: int = 0
    wins: int = 0
    pnl_usd: float = 0.0
    peak_pnl_usd: float = 0.0
    mechanical_failures: tuple[str, ...] = ()
    stale_service_seconds: float = 0.0
    no_valid_candidate_seconds: float = 0.0

    @property
    def win_rate(self) -> float:
        return 0.0 if self.closed_entry_groups <= 0 else self.wins / self.closed_entry_groups


@dataclass(frozen=True)
class StopGateResult:
    should_stop: bool
    triggered_gates: tuple[str, ...]
    details: dict[str, float | int]


def evaluate_stop_gates(performance: RunPerformance) -> StopGateResult:
    gates: list[str] = []
    if performance.submitted_entry_groups >= 100:
        gates.append("trade_cap")
    if performance.pnl_usd <= -50:
        gates.append("hard_loss")
    if performance.closed_entry_groups >= 10 and performance.pnl_usd <= -15 and performance.win_rate < 0.20:
        gates.append("early_failure")
    if performance.pnl_usd <= -25 and performance.win_rate < 0.40:
        gates.append("mid_failure")
    if performance.pnl_usd <= -40 and performance.win_rate < 0.50:
        gates.append("severe_failure")
    drawdown = performance.peak_pnl_usd - performance.pnl_usd
    if performance.peak_pnl_usd >= 25 and drawdown >= max(20, 0.35 * performance.peak_pnl_usd):
        gates.append("profit_giveback")
    if performance.closed_entry_groups >= 30 and performance.win_rate < 0.45 and performance.pnl_usd < 0:
        gates.append("quality_floor")
    if performance.mechanical_failures:
        gates.append("mechanical_failure")
    if performance.stale_service_seconds > 300:
        gates.append("stale_service")
    if performance.no_valid_candidate_seconds >= 300:
        gates.append("no_valid_candidate_report_gate")
    return StopGateResult(
        should_stop=bool(gates),
        triggered_gates=tuple(gates),
        details={
            "submitted_entry_groups": performance.submitted_entry_groups,
            "closed_entry_groups": performance.closed_entry_groups,
            "wins": performance.wins,
            "win_rate": performance.win_rate,
            "pnl_usd": performance.pnl_usd,
            "peak_pnl_usd": performance.peak_pnl_usd,
            "drawdown_from_peak": drawdown,
        },
    )
