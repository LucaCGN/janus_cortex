from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
from typing import Any


V3_POLICY_SCHEMA_VERSION = "crypto_options_v3_policy_v1"
V3_COMPONENT_STATE_SCHEMA_VERSION = "crypto_options_v3_component_state_v1"
V3_READINESS_SCHEMA_VERSION = "crypto_options_v3_live_test_readiness_v1"

V3_FINAL_COMPONENT_IDS = (
    "banded_profile_cashout_router_v1",
    "dynamic_parallel_conflict_cashout_v1",
    "dynamic_bucketed_takeprofit_v1",
    "ev_overlay_cashout_parallel_v1",
    "consensus_conflict_filter_cashout_v2",
)

V3_GLOBAL_BLOCKED_ENTRY_BUCKETS = ((0.55, 0.70),)


@dataclass(frozen=True)
class V3BudgetPolicy:
    total_budget_usd: float = 250.0
    component_budget_usd: float = 50.0
    order_fraction_of_current_budget: float = 0.10
    min_order_notional_usd: float = 5.0
    min_pairable_filled_shares: float = 5.0
    max_component_loss_usd: float = 50.0
    max_active_cost_usd: float = 50.0
    stale_unresolved_grace_seconds: int = 120
    stale_unresolved_stop_cost_usd: float = 15.0
    development_drawdown_min_closed_trades: int = 10
    development_drawdown_worst_case_loss_usd: float = 25.0
    development_drawdown_win_rate_below: float = 0.45
    max_submitted_entry_groups: int = 100
    quality_floor_min_closed_trades: int = 30
    quality_floor_win_rate_below: float = 0.45
    profit_giveback_min_peak_pnl_usd: float = 25.0
    profit_giveback_min_drawdown_usd: float = 20.0
    profit_giveback_fraction_of_peak: float = 0.35
    early_probe_min_closed_trades: int = 2
    early_probe_loss_usd: float = 5.0
    early_probe_win_rate_below: float = 0.01
    stop_loss_tiers: tuple[tuple[float, float], ...] = (
        (15.0, 0.20),
        (25.0, 0.40),
        (40.0, 0.50),
    )


@dataclass(frozen=True)
class V3ComponentDefinition:
    component_id: str
    role: str
    cashout_managed: bool = True
    min_order_notional_usd: float = 5.0
    min_pairable_filled_shares: float = 5.0
    blocked_entry_buckets: tuple[tuple[float, float], ...] = V3_GLOBAL_BLOCKED_ENTRY_BUCKETS


def default_v3_budget_policy() -> dict[str, Any]:
    policy = V3BudgetPolicy()
    payload = asdict(policy)
    payload["schema_version"] = V3_POLICY_SCHEMA_VERSION
    payload["component_count"] = len(V3_FINAL_COMPONENT_IDS)
    return _jsonable(payload)


def default_v3_component_definitions() -> list[dict[str, Any]]:
    roles = {
        "banded_profile_cashout_router_v1": "core_reliability",
        "dynamic_parallel_conflict_cashout_v1": "aggressive_upside",
        "dynamic_bucketed_takeprofit_v1": "bucketed_cashout_control",
        "ev_overlay_cashout_parallel_v1": "clean_overlay_lane",
        "consensus_conflict_filter_cashout_v2": "experimental_consensus_lane",
    }
    return [
        _jsonable(
            asdict(
                V3ComponentDefinition(
                    component_id=component_id,
                    role=roles[component_id],
                )
            )
            | {"schema_version": V3_POLICY_SCHEMA_VERSION}
        )
        for component_id in V3_FINAL_COMPONENT_IDS
    ]


def evaluate_v3_component_state(
    ledger_or_entries: dict[str, Any] | list[dict[str, Any]] | None,
    *,
    component_id: str,
    policy: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    policy = policy or default_v3_budget_policy()
    generated_at = generated_at or datetime.now(timezone.utc)
    entries = _entries(ledger_or_entries)
    settled = [row for row in entries if _is_settled_outcome(row)]
    submitted_buys = [row for row in entries if str(row.get("side") or "BUY").upper() == "BUY" and row.get("status") == "submitted"]
    active_open_buys = [row for row in submitted_buys if str(row.get("settlement_status") or "").lower() == "open_requires_reconciliation"]
    active_cost = _sum(_entry_cost(row) for row in active_open_buys)
    stale_open_buys = [
        row
        for row in active_open_buys
        if _is_stale_unresolved_closed_event(
            row,
            generated_at=generated_at,
            grace_seconds=int(policy.get("stale_unresolved_grace_seconds") or 120),
        )
    ]
    stale_unresolved_cost = _sum(_entry_cost(row) for row in stale_open_buys)
    realized_pnl = _sum(row.get("realized_pnl_net_usd") for row in settled)
    wins = [row for row in settled if (_float(row.get("realized_pnl_net_usd")) or 0.0) > 0.0]
    losses = [row for row in settled if (_float(row.get("realized_pnl_net_usd")) or 0.0) < 0.0]
    win_rate = len(wins) / len(settled) if settled else None
    loss_usd = max(0.0, -realized_pnl)
    worst_case_loss_usd = loss_usd + active_cost
    equity_curve = _equity_curve(settled)
    peak_pnl = max(equity_curve) if equity_curve else max(0.0, realized_pnl)
    drawdown_from_peak = max(0.0, peak_pnl - realized_pnl)
    current_budget = max(0.0, float(policy["component_budget_usd"]) + realized_pnl)
    order_size = max(float(policy["min_order_notional_usd"]), current_budget * float(policy["order_fraction_of_current_budget"]))
    stop_reasons = _component_stop_reasons(
        loss_usd=loss_usd,
        win_rate=win_rate,
        settled_count=len(settled),
        submitted_buy_count=len(submitted_buys),
        active_cost_usd=active_cost,
        stale_unresolved_count=len(stale_open_buys),
        stale_unresolved_cost_usd=stale_unresolved_cost,
        worst_case_loss_usd=worst_case_loss_usd,
        realized_pnl=realized_pnl,
        peak_pnl=peak_pnl,
        drawdown_from_peak=drawdown_from_peak,
        policy=policy,
    )
    return _jsonable(
        {
            "schema_version": V3_COMPONENT_STATE_SCHEMA_VERSION,
            "generated_at_utc": generated_at.astimezone(timezone.utc).isoformat(),
            "component_id": component_id,
            "policy": policy,
            "submitted_buy_count": len(submitted_buys),
            "settled_count": len(settled),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "realized_pnl_usd": round(realized_pnl, 6),
            "loss_usd": round(loss_usd, 6),
            "active_open_buy_count": len(active_open_buys),
            "active_cost_usd": round(active_cost, 6),
            "stale_unresolved_open_buy_count": len(stale_open_buys),
            "stale_unresolved_cost_usd": round(stale_unresolved_cost, 6),
            "worst_case_loss_usd": round(worst_case_loss_usd, 6),
            "peak_pnl_usd": round(peak_pnl, 6),
            "drawdown_from_peak_usd": round(drawdown_from_peak, 6),
            "current_budget_usd": round(current_budget, 6),
            "next_order_notional_usd": round(order_size, 6),
            "min_order_notional_usd": float(policy["min_order_notional_usd"]),
            "min_pairable_filled_shares": float(policy["min_pairable_filled_shares"]),
            "disabled": bool(stop_reasons),
            "disabled_reason": stop_reasons[0] if stop_reasons else None,
            "stop_reasons": stop_reasons,
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    )


def v3_entry_blockers(candidate: dict[str, Any], *, component_definition: dict[str, Any] | None = None) -> list[str]:
    component_definition = component_definition or {}
    blockers: list[str] = []
    price = _entry_price(candidate)
    if price is None:
        blockers.append("entry_price_missing")
    else:
        for low, high in component_definition.get("blocked_entry_buckets") or V3_GLOBAL_BLOCKED_ENTRY_BUCKETS:
            if float(low) <= price < float(high):
                blockers.append(f"entry_price_bucket_blocked:{low:.2f}-{high:.2f}")
                break
    notional = _float(candidate.get("min_order_total_cost") or candidate.get("estimated_ticket_notional_usd"))
    min_notional = float(component_definition.get("min_order_notional_usd") or 5.0)
    if notional is not None and notional + 1e-9 < min_notional:
        blockers.append("entry_notional_below_v3_min_pairable")
    shares = _candidate_shares(candidate)
    min_shares = float(component_definition.get("min_pairable_filled_shares") or 5.0)
    if shares is not None and shares + 1e-9 < min_shares:
        blockers.append("entry_filled_shares_below_v3_min_pairable")
    return sorted(set(blockers))


def classify_quote_health(error: Any = None, *, status_code: int | None = None, quote: dict[str, Any] | None = None) -> dict[str, Any]:
    quote = quote or {}
    raw = " ".join(str(item) for item in (error, quote.get("error"), quote.get("reason")) if item)
    lowered = raw.lower()
    code = status_code or _status_code_from_text(raw)
    if code == 404 or "no orderbook exists" in lowered:
        kind = "quote_404_no_orderbook"
        severity = "block_token"
    elif code == 429 or "rate limit" in lowered or "too many requests" in lowered:
        kind = "quote_rate_limited"
        severity = "degraded_retry"
    elif any(token in lowered for token in ("timeout", "timed out", "read timed out")):
        kind = "quote_timeout"
        severity = "degraded_retry"
    elif any(token in lowered for token in ("network is unreachable", "connectionerror", "connection refused", "name resolution", "dns", "temporary failure")):
        kind = "network_unreachable"
        severity = "block_entries_keep_reconciliation"
    elif raw:
        kind = "quote_unknown_error"
        severity = "degraded_block_entries"
    elif quote.get("best_bid") is None or quote.get("best_ask") is None:
        kind = "quote_missing_bid_or_ask"
        severity = "block_token"
    else:
        kind = "quote_ok"
        severity = "ok"
    return {
        "schema_version": "crypto_options_quote_health_v1",
        "status_code": code,
        "quote_error_type": kind,
        "severity": severity,
        "raw_error": raw or None,
        "entry_submission_allowed": severity == "ok",
        "cashout_reconciliation_allowed": severity in {"ok", "block_token", "degraded_retry", "degraded_block_entries", "block_entries_keep_reconciliation"},
    }


def build_v3_live_test_readiness(
    *,
    component_states: list[dict[str, Any]],
    signal_snapshot: dict[str, Any] | None = None,
    quote_health: list[dict[str, Any]] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    signal_snapshot = signal_snapshot or {}
    quote_health = quote_health or []
    known_profiles = int(((signal_snapshot.get("profile_registry") or {}).get("known_profile_count")) or 0)
    active_profiles = int(((signal_snapshot.get("profile_registry") or {}).get("active_profile_count")) or 0)
    has_all_components = {row.get("component_id") for row in component_states} >= set(V3_FINAL_COMPONENT_IDS)
    gates = [
        _gate("five_final_components_defined", has_all_components, ["missing_v3_final_component"]),
        _gate("component_budgets_initialized", _component_budgets_ok(component_states), ["invalid_component_budget_state"]),
        _gate("min_pairable_order_policy", _pairable_policy_ok(component_states), ["min_pairable_order_policy_missing"]),
        _gate("global_entry_bucket_lock_055_070", True, []),
        _gate("profile_registry_available", known_profiles > 0, ["profile_registry_empty"]),
        _gate("active_profile_pulse_available", active_profiles > 0, ["active_profile_pool_empty"]),
        _gate("quote_health_typed", all((row.get("quote_error_type") or "") for row in quote_health), ["quote_health_untyped"]),
        _gate("orders_remain_disabled_until_launch_approval", True, []),
    ]
    return {
        "schema_version": V3_READINESS_SCHEMA_VERSION,
        "generated_at_utc": generated_at.astimezone(timezone.utc).isoformat(),
        "ready_for_live_test_review": all(row["status"] == "pass" for row in gates),
        "live_trading_authorized": False,
        "orders_allowed": False,
        "component_count": len(component_states),
        "component_states": component_states,
        "signal_registry": signal_snapshot.get("profile_registry") or {},
        "quote_health": quote_health,
        "gates": gates,
    }


def _component_stop_reasons(
    *,
    loss_usd: float,
    win_rate: float | None,
    settled_count: int,
    submitted_buy_count: int,
    active_cost_usd: float,
    stale_unresolved_count: int,
    stale_unresolved_cost_usd: float,
    worst_case_loss_usd: float,
    realized_pnl: float,
    peak_pnl: float,
    drawdown_from_peak: float,
    policy: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    max_trades = int(policy.get("max_submitted_entry_groups") or 0)
    if max_trades > 0 and submitted_buy_count >= max_trades:
        reasons.append("development_trade_cap_100_hit")
    if active_cost_usd >= float(policy.get("max_active_cost_usd") or policy["max_component_loss_usd"]):
        reasons.append("active_cost_component_budget_hit")
    if worst_case_loss_usd >= float(policy["max_component_loss_usd"]):
        reasons.append("worst_case_component_hard_stop_loss_50_usd")
    if stale_unresolved_count > 0 and stale_unresolved_cost_usd >= float(policy.get("stale_unresolved_stop_cost_usd") or 15.0):
        if win_rate is None or win_rate < 0.20:
            reasons.append("stale_unresolved_closed_event_loss_risk_15_usd")
    if loss_usd >= float(policy["max_component_loss_usd"]):
        reasons.append("component_hard_stop_loss_50_usd")
    if win_rate is not None:
        if (
            settled_count >= int(policy.get("early_probe_min_closed_trades") or 2)
            and loss_usd >= float(policy.get("early_probe_loss_usd") or 5.0)
            and win_rate < float(policy.get("early_probe_win_rate_below") or 0.01)
        ):
            reasons.append("early_probe_two_loss_zero_win_rate_stop")
        if (
            settled_count >= int(policy.get("development_drawdown_min_closed_trades") or 10)
            and worst_case_loss_usd >= float(policy.get("development_drawdown_worst_case_loss_usd") or 25.0)
            and win_rate < float(policy.get("development_drawdown_win_rate_below") or 0.45)
        ):
            reasons.append("development_worst_case_drawdown_25_win_rate_below_0.45")
        for loss_threshold, max_win_rate in policy["stop_loss_tiers"]:
            if loss_usd >= float(loss_threshold) and win_rate < float(max_win_rate):
                reasons.append(f"component_loss_{loss_threshold:g}_win_rate_below_{max_win_rate:.2f}")
            if worst_case_loss_usd >= float(loss_threshold) and win_rate < float(max_win_rate):
                reasons.append(f"worst_case_component_loss_{loss_threshold:g}_win_rate_below_{max_win_rate:.2f}")
        if (
            settled_count >= int(policy.get("quality_floor_min_closed_trades") or 30)
            and realized_pnl < 0.0
            and win_rate < float(policy.get("quality_floor_win_rate_below") or 0.45)
        ):
            reasons.append("quality_floor_win_rate_below_45_negative_pnl")
    giveback_threshold = max(
        float(policy.get("profit_giveback_min_drawdown_usd") or 20.0),
        float(peak_pnl) * float(policy.get("profit_giveback_fraction_of_peak") or 0.35),
    )
    if (
        peak_pnl >= float(policy.get("profit_giveback_min_peak_pnl_usd") or 25.0)
        and drawdown_from_peak >= giveback_threshold
    ):
        reasons.append("profit_giveback_stop_hit")
    return reasons


def _equity_curve(entries: list[dict[str, Any]]) -> list[float]:
    total = 0.0
    values: list[float] = []
    for entry in sorted(entries, key=lambda row: str(row.get("settlement_reconciled_at_utc") or row.get("recorded_at_utc") or "")):
        total += _float(entry.get("realized_pnl_net_usd")) or 0.0
        values.append(total)
    return values


def _entry_cost(entry: dict[str, Any]) -> float | None:
    execution_quality = entry.get("execution_quality") if isinstance(entry.get("execution_quality"), dict) else {}
    for value in (
        execution_quality.get("filled_notional_usd"),
        entry.get("estimated_total_cost_usd"),
    ):
        parsed = _float(value)
        if parsed is not None:
            return parsed
    price = _float(execution_quality.get("realized_price")) or _float(entry.get("price"))
    size = _float(execution_quality.get("filled_shares")) or _float(entry.get("size"))
    if price is None or size is None:
        return None
    return price * size


def _is_stale_unresolved_closed_event(entry: dict[str, Any], *, generated_at: datetime, grace_seconds: int) -> bool:
    event_end = _event_end_from_entry(entry)
    if event_end is None:
        return False
    return generated_at.timestamp() > event_end.timestamp() + max(0, int(grace_seconds))


def _event_end_from_entry(entry: dict[str, Any]) -> datetime | None:
    explicit = str(entry.get("event_end_utc") or entry.get("market_end_utc") or "").strip()
    if explicit:
        try:
            parsed = datetime.fromisoformat(explicit.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed.astimezone(timezone.utc)
    slug = str(entry.get("event_slug") or "")
    match = re.search(r"-(\d{10})$", slug)
    if not match:
        return None
    return datetime.fromtimestamp(int(match.group(1)) + 300, tz=timezone.utc)


def _component_budgets_ok(component_states: list[dict[str, Any]]) -> bool:
    return bool(component_states) and all((_float(row.get("current_budget_usd")) is not None and row.get("next_order_notional_usd")) for row in component_states)


def _pairable_policy_ok(component_states: list[dict[str, Any]]) -> bool:
    return bool(component_states) and all(
        (_float(row.get("min_order_notional_usd")) or 0.0) >= 5.0 and (_float(row.get("min_pairable_filled_shares")) or 0.0) >= 5.0
        for row in component_states
    )


def _gate(gate_id: str, passed: bool, blockers: list[str]) -> dict[str, Any]:
    return {"gate_id": gate_id, "status": "pass" if passed else "fail", "blockers": [] if passed else blockers}


def _entries(ledger_or_entries: dict[str, Any] | list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if ledger_or_entries is None:
        return []
    if isinstance(ledger_or_entries, dict):
        values = ledger_or_entries.get("entries") or []
    else:
        values = ledger_or_entries
    return [row for row in values if isinstance(row, dict)]


def _is_settled_outcome(row: dict[str, Any]) -> bool:
    if row.get("realized_pnl_net_usd") is None:
        return False
    status = str(row.get("settlement_status") or row.get("status") or "").lower()
    return status in {"settled", "closed", "submitted"} or row.get("settlement_win") is not None


def _entry_price(candidate: dict[str, Any]) -> float | None:
    return _float(candidate.get("best_ask") or candidate.get("observed_execution_price") or candidate.get("price"))


def _candidate_shares(candidate: dict[str, Any]) -> float | None:
    ticket = candidate.get("manual_execution_ticket") if isinstance(candidate.get("manual_execution_ticket"), dict) else {}
    return _float(ticket.get("shares") or candidate.get("filled_shares") or candidate.get("size"))


def _status_code_from_text(value: str) -> int | None:
    match = re.search(r"\b([1-5]\d\d)\b", value)
    return int(match.group(1)) if match else None


def _sum(values: Any) -> float:
    total = 0.0
    for value in values:
        parsed = _float(value)
        if parsed is not None:
            total += parsed
    return total


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


__all__ = [
    "V3_FINAL_COMPONENT_IDS",
    "V3_GLOBAL_BLOCKED_ENTRY_BUCKETS",
    "build_v3_live_test_readiness",
    "classify_quote_health",
    "default_v3_budget_policy",
    "default_v3_component_definitions",
    "evaluate_v3_component_state",
    "v3_entry_blockers",
]
