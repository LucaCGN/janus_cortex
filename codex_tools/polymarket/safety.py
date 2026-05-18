"""Safety checks for direct Polymarket fallback gate snapshots.

This module translates read-only state into gate booleans. It does not prepare,
sign, submit, cancel, or replace orders.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from codex_tools.polymarket.execution_gate import (
    NO_EXECUTION_STATEMENT,
    PolymarketExecutionGateSnapshot,
    PolymarketFallbackIntent,
    derive_idempotency_key,
)

SAFETY_SCHEMA_VERSION = "polymarket_fallback_safety_v1"
DEFAULT_DIRECT_TRUTH_MAX_AGE_SECONDS = 300.0
DEFAULT_MIN_ORDER_SIZE = 5.0
DEFAULT_MIN_BUY_NOTIONAL_USD = 1.0
NON_AUTHORITATIVE_TRUTH_SOURCES = {
    "chat",
    "chat_memory",
    "github",
    "github_issue",
    "obsidian",
    "portfolio_mirror",
    "screenshot",
    "screenshots",
    "stale_mirror",
    "ui",
    "web_ui",
}


@dataclass(frozen=True)
class PolymarketSafetyCheck:
    schema_version: str
    name: str
    passed: bool
    blockers: list[str]
    evidence: dict[str, Any]
    no_execution_statement: str = NO_EXECUTION_STATEMENT


def _snapshot_get(snapshot: Any, key: str, default: Any = None) -> Any:
    if snapshot is None:
        return default
    if isinstance(snapshot, dict):
        return snapshot.get(key, default)
    return getattr(snapshot, key, default)


def _coerce_utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalized_truth_sources(truth_sources: list[str] | None) -> list[str]:
    return sorted({str(source).strip().lower() for source in truth_sources or [] if str(source).strip()})


def evaluate_direct_truth_freshness(
    snapshot: Any | None,
    *,
    now_utc: datetime | str | None = None,
    max_age_seconds: float = DEFAULT_DIRECT_TRUTH_MAX_AGE_SECONDS,
    required_sections: tuple[str, ...] = ("open_positions", "open_orders", "trades"),
) -> PolymarketSafetyCheck:
    blockers: list[str] = []
    status = str(_snapshot_get(snapshot, "status", "") or "")
    section_status = dict(_snapshot_get(snapshot, "section_status", {}) or {})
    read_at = _coerce_utc(_snapshot_get(snapshot, "read_at_utc"))
    now = _coerce_utc(now_utc) or datetime.now(UTC)
    age_seconds: float | None = None

    if snapshot is None:
        blockers.append("direct_truth_snapshot_missing")
    if status != "read_only_snapshot":
        blockers.append("direct_truth_snapshot_not_complete")
    if read_at is None:
        blockers.append("direct_truth_timestamp_missing_or_invalid")
    else:
        age_seconds = max(0.0, (now - read_at).total_seconds())
        if age_seconds > max_age_seconds:
            blockers.append("direct_truth_snapshot_stale")

    for section in required_sections:
        section_value = str(section_status.get(section, "missing") or "missing")
        if section_value != "ok":
            blockers.append(f"direct_truth_section_{section}_{section_value}")

    return PolymarketSafetyCheck(
        schema_version=SAFETY_SCHEMA_VERSION,
        name="direct_truth_freshness",
        passed=not blockers,
        blockers=blockers,
        evidence={
            "status": status,
            "read_at_utc": _iso_z(read_at),
            "now_utc": _iso_z(now),
            "age_seconds": age_seconds,
            "max_age_seconds": max_age_seconds,
            "required_sections": list(required_sections),
            "section_status": section_status,
            "open_order_count": _snapshot_get(snapshot, "open_order_count", 0),
            "open_position_count": _snapshot_get(snapshot, "open_position_count", 0),
            "trade_count": _snapshot_get(snapshot, "trade_count", 0),
        },
    )


def evaluate_risk_budget(
    *,
    budget_name: str | None = None,
    max_notional_usd: float | str | None = None,
    used_notional_usd: float | str | None = 0.0,
    proposed_notional_usd: float | str | None = None,
) -> PolymarketSafetyCheck:
    blockers: list[str] = []
    max_notional = _safe_float(max_notional_usd)
    used_notional = _safe_float(used_notional_usd) or 0.0
    proposed_notional = _safe_float(proposed_notional_usd)

    if not str(budget_name or "").strip():
        blockers.append("risk_budget_not_selected")
    if max_notional is None or max_notional <= 0.0:
        blockers.append("risk_budget_limit_missing")
    if used_notional < 0.0:
        blockers.append("risk_budget_used_notional_invalid")
    remaining = None if max_notional is None else max(0.0, max_notional - used_notional)
    if proposed_notional is None or proposed_notional <= 0.0:
        blockers.append("risk_budget_proposed_notional_missing")
    elif remaining is not None and proposed_notional > remaining + 1e-9:
        blockers.append("risk_budget_notional_exceeded")

    return PolymarketSafetyCheck(
        schema_version=SAFETY_SCHEMA_VERSION,
        name="risk_budget",
        passed=not blockers,
        blockers=blockers,
        evidence={
            "budget_name": budget_name,
            "max_notional_usd": max_notional,
            "used_notional_usd": used_notional,
            "remaining_notional_usd": remaining,
            "proposed_notional_usd": proposed_notional,
        },
    )


def evaluate_minimum_order_policy(
    intent: PolymarketFallbackIntent,
    *,
    min_size: float = DEFAULT_MIN_ORDER_SIZE,
    min_buy_notional_usd: float = DEFAULT_MIN_BUY_NOTIONAL_USD,
    market_order_exception_approved: bool = False,
) -> PolymarketSafetyCheck:
    blockers: list[str] = []
    side = str(intent.side or "").strip().upper()
    order_type = str(intent.metadata.get("order_type") or "limit").strip().lower()
    price = _safe_float(intent.price)
    size = _safe_float(intent.size)
    notional = price * size if price is not None and size is not None else None

    if side not in {"BUY", "SELL"}:
        blockers.append("minimum_order_invalid_side")
    if order_type == "market" and not market_order_exception_approved:
        blockers.append("market_order_exception_missing")
    if order_type not in {"limit", "market"}:
        blockers.append("minimum_order_invalid_order_type")
    if price is None or not 0.0 < price <= 1.0:
        blockers.append("minimum_order_invalid_price")
    if size is None or size <= 0.0:
        blockers.append("minimum_order_invalid_size")
    elif size < min_size:
        blockers.append("minimum_order_size_not_met")
    if side == "BUY" and (notional is None or notional < min_buy_notional_usd):
        blockers.append("minimum_buy_notional_not_met")

    return PolymarketSafetyCheck(
        schema_version=SAFETY_SCHEMA_VERSION,
        name="minimum_order_policy",
        passed=not blockers,
        blockers=blockers,
        evidence={
            "side": side,
            "order_type": order_type,
            "price": price,
            "size": size,
            "notional_usd": notional,
            "min_size": min_size,
            "min_buy_notional_usd": min_buy_notional_usd,
            "market_order_exception_approved": market_order_exception_approved,
        },
    )


def evaluate_kill_switch(
    *,
    kill_switch_clear: bool = False,
    source: str | None = None,
    blocked_reasons: list[str] | None = None,
) -> PolymarketSafetyCheck:
    blockers = [str(reason) for reason in blocked_reasons or [] if str(reason).strip()]
    if not kill_switch_clear:
        blockers.append("kill_switch_not_clear")

    return PolymarketSafetyCheck(
        schema_version=SAFETY_SCHEMA_VERSION,
        name="kill_switch",
        passed=not blockers,
        blockers=blockers,
        evidence={
            "kill_switch_clear": kill_switch_clear,
            "source": source,
        },
    )


def evaluate_target_stop_rebuy_policy(
    *,
    policy: dict[str, Any] | str | None = None,
    action: str | None = None,
) -> PolymarketSafetyCheck:
    blockers: list[str] = []
    resolved_policy = policy if isinstance(policy, dict) else {}
    required_keys = ("policy_name", "target_policy", "stop_policy", "rebuy_policy", "reason")
    missing_keys = [key for key in required_keys if not str(resolved_policy.get(key) or "").strip()]
    blockers.extend(f"target_stop_rebuy_policy_missing_{key}" for key in missing_keys)

    action_text = str(action or "").strip().lower()
    target_price = _safe_float(resolved_policy.get("target_price") or resolved_policy.get("limit_price"))
    if action_text in {"existing_position_target", "existing_position_replace", "target", "replace_target"}:
        if target_price is None or not 0.0 < target_price <= 1.0:
            blockers.append("target_stop_rebuy_policy_invalid_target_price")

    return PolymarketSafetyCheck(
        schema_version=SAFETY_SCHEMA_VERSION,
        name="target_stop_rebuy_policy",
        passed=not blockers,
        blockers=blockers,
        evidence={
            "policy": resolved_policy,
            "action": action,
        },
    )


def build_polymarket_safety_gate_snapshot(
    intent: PolymarketFallbackIntent,
    *,
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
    target_stop_rebuy_policy: dict[str, Any] | str | None = None,
    janus_degraded_or_direct_path_selected: bool = False,
    ledger_available: bool = False,
    reconciliation_plan: str | dict[str, Any] | None = None,
    explicit_execution_approval: bool = False,
    truth_sources: list[str] | None = None,
) -> PolymarketExecutionGateSnapshot:
    price = _safe_float(intent.price)
    size = _safe_float(intent.size)
    proposed_notional = price * size if price is not None and size is not None else None

    direct_truth = evaluate_direct_truth_freshness(
        direct_truth_snapshot,
        now_utc=now_utc,
        max_age_seconds=direct_truth_max_age_seconds,
    )
    risk_budget = evaluate_risk_budget(
        budget_name=risk_budget_name,
        max_notional_usd=risk_budget_max_notional_usd,
        used_notional_usd=risk_budget_used_notional_usd,
        proposed_notional_usd=proposed_notional,
    )
    minimum_order = evaluate_minimum_order_policy(
        intent,
        min_size=min_size,
        min_buy_notional_usd=min_buy_notional_usd,
        market_order_exception_approved=market_order_exception_approved,
    )
    kill_switch = evaluate_kill_switch(
        kill_switch_clear=kill_switch_clear,
        source=kill_switch_source,
        blocked_reasons=kill_switch_blocked_reasons,
    )
    target_policy = evaluate_target_stop_rebuy_policy(
        policy=target_stop_rebuy_policy,
        action=intent.action,
    )
    idempotency_key = derive_idempotency_key(intent)
    normalized_truth_sources = _normalized_truth_sources(truth_sources)
    rejected_sources = [
        source for source in normalized_truth_sources if source in NON_AUTHORITATIVE_TRUTH_SOURCES
    ]
    reconciliation_plan_present = bool(reconciliation_plan)
    market_token_resolved = all(
        str(value or "").strip()
        for value in (intent.market_slug, intent.token_id, intent.side, intent.price, intent.size)
    )

    return PolymarketExecutionGateSnapshot(
        direct_truth_fresh=direct_truth.passed,
        janus_degraded_or_direct_path_selected=janus_degraded_or_direct_path_selected,
        risk_budget_selected=risk_budget.passed,
        minimum_order_policy_passed=minimum_order.passed,
        target_stop_rebuy_policy_present=target_policy.passed,
        kill_switch_clear=kill_switch.passed,
        ledger_idempotency_available=bool(ledger_available and idempotency_key),
        reconciliation_plan_present=reconciliation_plan_present,
        explicit_execution_approval=explicit_execution_approval,
        market_token_resolved=market_token_resolved,
        non_authoritative_truth_rejected=not rejected_sources,
        evidence={
            "schema_version": SAFETY_SCHEMA_VERSION,
            "direct_truth": asdict(direct_truth),
            "risk_budget": asdict(risk_budget),
            "minimum_order_policy": asdict(minimum_order),
            "target_stop_rebuy_policy": asdict(target_policy),
            "kill_switch": asdict(kill_switch),
            "ledger_idempotency": {
                "ledger_available": ledger_available,
                "idempotency_key_present": bool(idempotency_key),
            },
            "reconciliation_plan_present": reconciliation_plan_present,
            "market_token_resolved": market_token_resolved,
            "truth_sources": normalized_truth_sources,
            "rejected_truth_sources": rejected_sources,
            "order_preparation_attempted": False,
            "order_submission_attempted": False,
            "no_execution_statement": NO_EXECUTION_STATEMENT,
        },
    )


__all__ = [
    "DEFAULT_DIRECT_TRUTH_MAX_AGE_SECONDS",
    "DEFAULT_MIN_BUY_NOTIONAL_USD",
    "DEFAULT_MIN_ORDER_SIZE",
    "NON_AUTHORITATIVE_TRUTH_SOURCES",
    "SAFETY_SCHEMA_VERSION",
    "PolymarketSafetyCheck",
    "build_polymarket_safety_gate_snapshot",
    "evaluate_direct_truth_freshness",
    "evaluate_kill_switch",
    "evaluate_minimum_order_policy",
    "evaluate_risk_budget",
    "evaluate_target_stop_rebuy_policy",
]
