"""Preview-first entrypoints for direct Polymarket fallback decisions.

These helpers are service/CLI-safe wrappers around the existing gate and ledger
primitives. They build reviewable decisions and may write inert ledger records,
but they never prepare, sign, submit, cancel, or replace orders.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from codex_tools.polymarket.execution_gate import (
    NO_EXECUTION_STATEMENT,
    PolymarketExecutionGateSnapshot,
    PolymarketFallbackIntent,
    build_fallback_decision,
)
from codex_tools.polymarket.ledger import write_fallback_decision_ledger
from codex_tools.polymarket.safety import (
    DEFAULT_DIRECT_TRUTH_MAX_AGE_SECONDS,
    DEFAULT_MIN_BUY_NOTIONAL_USD,
    DEFAULT_MIN_ORDER_SIZE,
    build_polymarket_safety_gate_snapshot,
)

PREVIEW_SCHEMA_VERSION = "polymarket_fallback_preview_v1"


@dataclass(frozen=True)
class PolymarketFallbackPreview:
    schema_version: str
    status: str
    decision: dict[str, Any]
    gate_snapshot: dict[str, Any]
    direct_truth_snapshot: dict[str, Any] | None
    ledger_write: dict[str, Any] | None
    order_preparation_attempted: bool
    order_submission_attempted: bool
    no_execution_statement: str


def _jsonable_payload(value: Any | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict):
            return dumped
    legacy_dict = getattr(value, "dict", None)
    if callable(legacy_dict):
        dumped = legacy_dict()
        if isinstance(dumped, dict):
            return dumped
    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, dict):
        return dict(attrs)
    return {"value": value}


def build_fallback_preview(
    intent: PolymarketFallbackIntent,
    *,
    gate_snapshot: PolymarketExecutionGateSnapshot | None = None,
    direct_truth_snapshot: Any | None = None,
    now_utc: datetime | str | None = None,
    direct_truth_max_age_seconds: float = DEFAULT_DIRECT_TRUTH_MAX_AGE_SECONDS,
    risk_budget_name: str | None = None,
    risk_budget_max_notional_usd: float | str | None = None,
    risk_budget_used_notional_usd: float | str | None = 0.0,
    min_size: float = DEFAULT_MIN_ORDER_SIZE,
    min_buy_notional_usd: float = DEFAULT_MIN_BUY_NOTIONAL_USD,
    market_order_exception_approved: bool = False,
    kill_switch_clear: bool = False,
    kill_switch_source: str | None = None,
    kill_switch_blocked_reasons: list[str] | None = None,
    janus_degraded_or_direct_path_selected: bool = False,
    ledger_available: bool = False,
    reconciliation_plan: str | dict[str, Any] | None = None,
    explicit_execution_approval: bool = False,
    truth_sources: list[str] | None = None,
    write_ledger: bool = False,
    ledger_root: Path | None = None,
    written_at_utc: datetime | None = None,
) -> PolymarketFallbackPreview:
    """Build a non-executing fallback preview and optionally record its ledger row."""

    resolved_gate = gate_snapshot or build_polymarket_safety_gate_snapshot(
        intent,
        direct_truth_snapshot=direct_truth_snapshot,
        now_utc=now_utc,
        direct_truth_max_age_seconds=direct_truth_max_age_seconds,
        risk_budget_name=risk_budget_name,
        risk_budget_max_notional_usd=risk_budget_max_notional_usd,
        risk_budget_used_notional_usd=risk_budget_used_notional_usd,
        min_size=min_size,
        min_buy_notional_usd=min_buy_notional_usd,
        market_order_exception_approved=market_order_exception_approved,
        kill_switch_clear=kill_switch_clear,
        kill_switch_source=kill_switch_source,
        kill_switch_blocked_reasons=kill_switch_blocked_reasons,
        janus_degraded_or_direct_path_selected=janus_degraded_or_direct_path_selected,
        ledger_available=ledger_available,
        reconciliation_plan=reconciliation_plan,
        explicit_execution_approval=explicit_execution_approval,
        truth_sources=truth_sources,
    )
    decision = build_fallback_decision(intent, resolved_gate)
    ledger_write = None
    if write_ledger:
        ledger_write = asdict(
            write_fallback_decision_ledger(
                decision,
                ledger_root=ledger_root,
                written_at_utc=written_at_utc,
            )
        )

    return PolymarketFallbackPreview(
        schema_version=PREVIEW_SCHEMA_VERSION,
        status=decision.status,
        decision=asdict(decision),
        gate_snapshot=asdict(resolved_gate),
        direct_truth_snapshot=_jsonable_payload(direct_truth_snapshot),
        ledger_write=ledger_write,
        order_preparation_attempted=False,
        order_submission_attempted=False,
        no_execution_statement=NO_EXECUTION_STATEMENT,
    )


__all__ = [
    "PREVIEW_SCHEMA_VERSION",
    "PolymarketFallbackPreview",
    "build_fallback_preview",
]
