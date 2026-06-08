from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutorBoundaryConfig:
    supervised_runtime_gate: bool
    ledger_gate: bool
    risk_gate: bool
    reconciliation_gate: bool
    execution_approved: bool = False
    live_risk_acknowledged: bool = False


def executor_boundary_ready(config: ExecutorBoundaryConfig) -> bool:
    return all(
        (
            config.supervised_runtime_gate,
            config.ledger_gate,
            config.risk_gate,
            config.reconciliation_gate,
            config.execution_approved,
            config.live_risk_acknowledged,
        )
    )


def reject_manual_order_instruction(message: str) -> bool:
    lowered = message.lower()
    manual_verbs = ("place order", "cancel order", "broadcast", "sign order", "redeem", "route order")
    return any(verb in lowered for verb in manual_verbs)
