from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SleeveAction = Literal["buy", "sell", "rebuy", "reduce", "monitor", "hold"]
SleeveTransitionStatus = Literal["intent_candidate", "blocked", "monitor_only"]


class EventRiskBudgetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_cap_pct: float = Field(default=0.10, ge=0.0, le=1.0)
    cash_cap_pct: float = Field(default=0.20, ge=0.0, le=1.0)
    absolute_event_cap_usd: float = Field(default=10.0, ge=0.0)
    max_concurrent_events: int = Field(default=5, ge=1)
    max_grid_leg_shares: float = Field(default=5.0, ge=0.0)
    core_hold_shares: float = Field(default=5.0, ge=0.0)


class EventRiskBudgetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "event_risk_budget_snapshot_v1"
    event_id: str = Field(min_length=1)
    event_cap_usd: float = Field(ge=0.0)
    portfolio_cap_usd: float = Field(ge=0.0)
    cash_cap_usd: float = Field(ge=0.0)
    absolute_event_cap_usd: float = Field(ge=0.0)
    current_position_notional_usd: float = Field(default=0.0, ge=0.0)
    open_order_notional_usd: float = Field(default=0.0, ge=0.0)
    pending_intent_notional_usd: float = Field(default=0.0, ge=0.0)
    used_notional_usd: float = Field(default=0.0, ge=0.0)
    remaining_notional_usd: float = Field(default=0.0, ge=0.0)
    budget_status: Literal["within_budget", "exhausted", "over_budget"] = "within_budget"
    blocker_codes: list[str] = Field(default_factory=list)
    policy: EventRiskBudgetPolicy


class SleeveTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sleeve_id: str = Field(min_length=1)
    sleeve_role: str = Field(min_length=1)
    action: SleeveAction
    side: str | None = None
    requested_shares: float = Field(default=0.0, ge=0.0)
    max_price: float | None = Field(default=None, ge=0.0, le=1.0)
    existing_position_shares: float = Field(default=0.0, ge=0.0)
    open_order_shares: float = Field(default=0.0, ge=0.0)
    pending_intent_shares: float = Field(default=0.0, ge=0.0)
    target_coverage_shares: float = Field(default=0.0, ge=0.0)
    enabled: bool = True


class SleeveTransitionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sleeve_id: str = Field(min_length=1)
    sleeve_role: str = Field(min_length=1)
    action: SleeveAction
    status: SleeveTransitionStatus
    side: str | None = None
    requested_notional_usd: float = Field(default=0.0, ge=0.0)
    remaining_notional_usd_after: float = Field(default=0.0, ge=0.0)
    reason_codes: list[str] = Field(default_factory=list)


class EventSleeveTransitionBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "event_sleeve_transition_bundle_v1"
    event_id: str = Field(min_length=1)
    budget: EventRiskBudgetSnapshot
    decisions: list[SleeveTransitionDecision] = Field(default_factory=list)

    @property
    def has_order_candidates(self) -> bool:
        return any(decision.status == "intent_candidate" for decision in self.decisions)


def derive_event_risk_budget(
    *,
    event_id: str,
    portfolio_value_usd: float,
    available_cash_usd: float,
    current_position_notional_usd: float = 0.0,
    open_order_notional_usd: float = 0.0,
    pending_intent_notional_usd: float = 0.0,
    policy: EventRiskBudgetPolicy | None = None,
) -> EventRiskBudgetSnapshot:
    policy = policy or EventRiskBudgetPolicy()
    portfolio_cap = max(portfolio_value_usd, 0.0) * policy.event_cap_pct
    cash_cap = max(available_cash_usd, 0.0) * policy.cash_cap_pct
    event_cap = min(portfolio_cap, cash_cap, policy.absolute_event_cap_usd)
    used = current_position_notional_usd + open_order_notional_usd + pending_intent_notional_usd
    remaining = max(event_cap - used, 0.0)
    blockers: list[str] = []
    if event_cap <= 0.0:
        blockers.append("event_budget_zero")
    if used > event_cap:
        blockers.append("event_budget_over_limit")
        status: Literal["within_budget", "exhausted", "over_budget"] = "over_budget"
    elif remaining <= 0.0:
        blockers.append("event_budget_exhausted")
        status = "exhausted"
    else:
        status = "within_budget"
    return EventRiskBudgetSnapshot(
        event_id=event_id,
        event_cap_usd=round(event_cap, 6),
        portfolio_cap_usd=round(portfolio_cap, 6),
        cash_cap_usd=round(cash_cap, 6),
        absolute_event_cap_usd=policy.absolute_event_cap_usd,
        current_position_notional_usd=current_position_notional_usd,
        open_order_notional_usd=open_order_notional_usd,
        pending_intent_notional_usd=pending_intent_notional_usd,
        used_notional_usd=round(used, 6),
        remaining_notional_usd=round(remaining, 6),
        budget_status=status,
        blocker_codes=blockers,
        policy=policy,
    )


def evaluate_event_sleeve_transitions(
    *,
    event_id: str,
    budget: EventRiskBudgetSnapshot,
    sleeves: list[SleeveTransitionRequest],
) -> EventSleeveTransitionBundle:
    remaining = budget.remaining_notional_usd
    decisions: list[SleeveTransitionDecision] = []
    for sleeve in sleeves:
        decision = _evaluate_sleeve(sleeve, remaining=remaining, budget=budget)
        decisions.append(decision)
        if decision.status == "intent_candidate" and sleeve.action in {"buy", "rebuy"}:
            remaining = max(remaining - decision.requested_notional_usd, 0.0)
    return EventSleeveTransitionBundle(event_id=event_id, budget=budget, decisions=decisions)


def _evaluate_sleeve(
    sleeve: SleeveTransitionRequest,
    *,
    remaining: float,
    budget: EventRiskBudgetSnapshot,
) -> SleeveTransitionDecision:
    requested_notional = _requested_notional(sleeve)
    if not sleeve.enabled:
        return _decision(sleeve, "monitor_only", requested_notional, remaining, ["sleeve_disabled"])
    if sleeve.action in {"monitor", "hold"}:
        return _decision(sleeve, "monitor_only", requested_notional, remaining, ["monitor_only"])
    if sleeve.action in {"buy", "rebuy"}:
        blockers = _buy_blockers(sleeve, requested_notional=requested_notional, remaining=remaining, budget=budget)
        if blockers:
            return _decision(sleeve, "blocked", requested_notional, remaining, blockers)
        return _decision(sleeve, "intent_candidate", requested_notional, remaining - requested_notional, ["budget_available"])
    blockers = _sell_or_reduce_blockers(sleeve)
    if blockers:
        return _decision(sleeve, "blocked", requested_notional, remaining, blockers)
    if sleeve.target_coverage_shares >= sleeve.existing_position_shares > 0:
        return _decision(sleeve, "monitor_only", requested_notional, remaining, ["target_already_covers_position"])
    return _decision(sleeve, "intent_candidate", requested_notional, remaining, ["position_reduction_available"])


def _buy_blockers(
    sleeve: SleeveTransitionRequest,
    *,
    requested_notional: float,
    remaining: float,
    budget: EventRiskBudgetSnapshot,
) -> list[str]:
    blockers: list[str] = []
    if budget.budget_status != "within_budget":
        blockers.extend(budget.blocker_codes or ["event_budget_unavailable"])
    if sleeve.requested_shares <= 0.0:
        blockers.append("requested_shares_required")
    if sleeve.max_price is None:
        blockers.append("max_price_required")
    if sleeve.open_order_shares > 0.0 or sleeve.pending_intent_shares > 0.0:
        blockers.append("duplicate_pending_exposure")
    if sleeve.existing_position_shares > 0.0 and sleeve.action == "buy":
        blockers.append("duplicate_open_position")
    if sleeve.action == "rebuy" and sleeve.target_coverage_shares < sleeve.existing_position_shares:
        blockers.append("rebuy_requires_existing_position_covered")
    if requested_notional > remaining:
        blockers.append("event_budget_exceeded")
    return _unique(blockers)


def _sell_or_reduce_blockers(sleeve: SleeveTransitionRequest) -> list[str]:
    blockers: list[str] = []
    if sleeve.existing_position_shares <= 0.0:
        blockers.append("position_required")
    if sleeve.open_order_shares > 0.0 or sleeve.pending_intent_shares > 0.0:
        blockers.append("duplicate_pending_reduction")
    return blockers


def _decision(
    sleeve: SleeveTransitionRequest,
    status: SleeveTransitionStatus,
    requested_notional_usd: float,
    remaining_after: float,
    reason_codes: list[str],
) -> SleeveTransitionDecision:
    return SleeveTransitionDecision(
        sleeve_id=sleeve.sleeve_id,
        sleeve_role=sleeve.sleeve_role,
        action=sleeve.action,
        status=status,
        side=sleeve.side,
        requested_notional_usd=round(requested_notional_usd, 6),
        remaining_notional_usd_after=round(max(remaining_after, 0.0), 6),
        reason_codes=_unique(reason_codes),
    )


def _requested_notional(sleeve: SleeveTransitionRequest) -> float:
    if sleeve.max_price is None:
        return 0.0
    return max(sleeve.requested_shares, 0.0) * sleeve.max_price


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


__all__ = [
    "EventRiskBudgetPolicy",
    "EventRiskBudgetSnapshot",
    "EventSleeveTransitionBundle",
    "SleeveAction",
    "SleeveTransitionDecision",
    "SleeveTransitionRequest",
    "SleeveTransitionStatus",
    "derive_event_risk_budget",
    "evaluate_event_sleeve_transitions",
]
