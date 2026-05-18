"""Gate and ledger primitives for direct Polymarket fallback tooling.

The helpers in this module are deliberately inert: they produce a decision
record that can be reviewed or written to a local ledger, but they never call
Polymarket endpoints and never submit orders.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

NO_EXECUTION_STATEMENT = (
    "No order was placed, cancelled, replaced, submitted, prepared, signed, or executed."
)

REQUIRED_GATE_LABELS = {
    "direct_truth_fresh": "fresh direct CLOB/account truth",
    "janus_degraded_or_direct_path_selected": "explicit Janus degradation or direct-path selection",
    "risk_budget_selected": "separate global-portfolio or live-monitor risk budget",
    "minimum_order_policy_passed": "Polymarket minimum-size/minimum-notional policy",
    "kill_switch_clear": "kill switch clear",
    "ledger_idempotency_available": "local durable ledger and idempotency key",
    "reconciliation_plan_present": "reconciliation plan back into Janus",
    "explicit_execution_approval": "explicit approved execution flag/config",
    "market_token_resolved": "resolved market, token, side, price, and size state",
    "non_authoritative_truth_rejected": "screenshots/chat/Obsidian/GitHub/stale mirrors rejected as truth",
}


@dataclass(frozen=True)
class PolymarketExecutionGateSnapshot:
    direct_truth_fresh: bool = False
    janus_degraded_or_direct_path_selected: bool = False
    risk_budget_selected: bool = False
    minimum_order_policy_passed: bool = False
    kill_switch_clear: bool = False
    ledger_idempotency_available: bool = False
    reconciliation_plan_present: bool = False
    explicit_execution_approval: bool = False
    market_token_resolved: bool = False
    non_authoritative_truth_rejected: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)

    def missing_gates(self) -> list[str]:
        missing: list[str] = []
        for field_name in REQUIRED_GATE_LABELS:
            if not bool(getattr(self, field_name)):
                missing.append(field_name)
        return missing

    def execution_gates_satisfied(self) -> bool:
        return not self.missing_gates()


@dataclass(frozen=True)
class PolymarketFallbackIntent:
    action: str
    account_id: str
    market_slug: str
    token_id: str
    side: str
    price: str
    size: str
    reason: str
    dry_run: bool = True
    idempotency_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolymarketFallbackDecision:
    schema_version: str
    status: str
    action: str
    dry_run: bool
    execution_authorized: bool
    order_submission_attempted: bool
    idempotency_key: str
    ledger_id: str
    missing_gates: list[str]
    required_gates: dict[str, str]
    intent: dict[str, Any]
    gate_snapshot: dict[str, Any]
    ledger_write_required_before_submission: bool
    no_execution_statement: str


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def derive_idempotency_key(intent: PolymarketFallbackIntent) -> str:
    if intent.idempotency_key:
        return intent.idempotency_key
    canonical = _canonical_json(
        {
            "account_id": intent.account_id,
            "action": intent.action,
            "market_slug": intent.market_slug,
            "price": intent.price,
            "side": intent.side.upper(),
            "size": intent.size,
            "token_id": intent.token_id,
        }
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def build_fallback_decision(
    intent: PolymarketFallbackIntent,
    gate_snapshot: PolymarketExecutionGateSnapshot,
) -> PolymarketFallbackDecision:
    missing_gates = gate_snapshot.missing_gates()
    idempotency_key = derive_idempotency_key(intent)
    ledger_id = "polymarket-fallback-" + hashlib.sha256(
        _canonical_json(
            {
                "idempotency_key": idempotency_key,
                "intent": asdict(intent),
                "missing_gates": missing_gates,
            }
        ).encode("utf-8")
    ).hexdigest()[:24]

    if missing_gates:
        status = "blocked_missing_execution_gates"
        execution_authorized = False
    elif intent.dry_run:
        status = "dry_run_preview_only"
        execution_authorized = False
    else:
        status = "ready_for_approved_execution_path"
        execution_authorized = True

    return PolymarketFallbackDecision(
        schema_version="polymarket_fallback_decision_v1",
        status=status,
        action=intent.action,
        dry_run=intent.dry_run,
        execution_authorized=execution_authorized,
        order_submission_attempted=False,
        idempotency_key=idempotency_key,
        ledger_id=ledger_id,
        missing_gates=missing_gates,
        required_gates=REQUIRED_GATE_LABELS.copy(),
        intent=asdict(intent),
        gate_snapshot=asdict(gate_snapshot),
        ledger_write_required_before_submission=True,
        no_execution_statement=NO_EXECUTION_STATEMENT,
    )
