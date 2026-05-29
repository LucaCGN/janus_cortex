from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SleeveAction = Literal["buy", "sell", "rebuy", "reduce", "monitor", "hold"]
SleeveTransitionStatus = Literal["intent_candidate", "blocked", "monitor_only"]
SideBudgetMode = Literal[
    "none",
    "balanced_50_50",
    "favorite_heavy",
    "underdog_heavy",
    "winner_only",
    "contrarian_only",
    "custom",
]
PhaseBudgetMode = Literal["none", "active_phase_only", "custom"]
RiskMode = Literal["validation", "development", "production"]


_RISK_MODE_DEFAULTS: dict[RiskMode, dict[str, object]] = {
    "validation": {
        "event_cap_pct": 0.03,
        "cash_cap_pct": 0.10,
        "absolute_event_cap_usd": 5.0,
        "max_concurrent_events": 2,
        "max_active_cycles": 1,
        "max_same_side_exposure_pct": 0.60,
        "min_expected_edge_after_slippage_cents": 1.0,
        "default_sleeve_budget_pct": 0.50,
        "profit_ratcheted_reinvestment_pct": 0.10,
        "max_profit_ratcheted_addon_usd": 1.0,
        "loss_cut_event_cap_multiplier": 0.50,
    },
    "development": {
        "event_cap_pct": 0.10,
        "cash_cap_pct": 0.20,
        "absolute_event_cap_usd": 10.0,
        "max_concurrent_events": 5,
        "max_active_cycles": 4,
        "max_same_side_exposure_pct": 0.70,
        "min_expected_edge_after_slippage_cents": 0.2,
        "default_sleeve_budget_pct": 0.50,
        "profit_ratcheted_reinvestment_pct": 0.40,
        "max_profit_ratcheted_addon_usd": 5.0,
        "loss_cut_event_cap_multiplier": 0.75,
    },
    "production": {
        "event_cap_pct": 0.02,
        "cash_cap_pct": 0.05,
        "absolute_event_cap_usd": 5.0,
        "max_concurrent_events": 3,
        "max_active_cycles": 2,
        "max_same_side_exposure_pct": 0.55,
        "min_expected_edge_after_slippage_cents": 1.5,
        "default_sleeve_budget_pct": 0.35,
        "profit_ratcheted_reinvestment_pct": 0.15,
        "max_profit_ratcheted_addon_usd": 2.0,
        "loss_cut_event_cap_multiplier": 0.40,
    },
}


class EventRiskBudgetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_mode: RiskMode = "development"
    event_cap_pct: float = Field(default=0.10, ge=0.0, le=1.0)
    cash_cap_pct: float = Field(default=0.20, ge=0.0, le=1.0)
    absolute_event_cap_usd: float = Field(default=10.0, ge=0.0)
    max_concurrent_events: int = Field(default=5, ge=1)
    max_active_cycles: int = Field(default=4, ge=0)
    max_grid_leg_shares: float = Field(default=5.0, ge=0.0)
    core_hold_shares: float = Field(default=5.0, ge=0.0)
    max_same_side_exposure_pct: float = Field(default=1.0, ge=0.0, le=1.0)
    min_expected_edge_after_slippage_cents: float = Field(default=0.0, ge=0.0)
    side_budget_mode: SideBudgetMode = "none"
    side_budget_pct: dict[str, float] = Field(default_factory=dict)
    phase_budget_mode: PhaseBudgetMode = "none"
    phase_budget_pct: dict[str, float] = Field(default_factory=dict)
    sleeve_budget_caps_usd: dict[str, float] = Field(default_factory=dict)
    sleeve_role_budget_pct: dict[str, float] = Field(default_factory=dict)
    default_sleeve_budget_pct: float = Field(default=1.0, ge=0.0, le=1.0)
    profit_ratcheted_reinvestment_pct: float = Field(default=0.0, ge=0.0, le=1.0)
    max_profit_ratcheted_addon_usd: float = Field(default=0.0, ge=0.0)
    loss_cut_event_cap_multiplier: float = Field(default=1.0, ge=0.0, le=1.0)


class EventRiskBudgetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "event_risk_budget_snapshot_v1"
    event_id: str = Field(min_length=1)
    event_cap_usd: float = Field(ge=0.0)
    base_event_cap_usd: float = Field(default=0.0, ge=0.0)
    portfolio_cap_usd: float = Field(ge=0.0)
    cash_cap_usd: float = Field(ge=0.0)
    absolute_event_cap_usd: float = Field(ge=0.0)
    profit_ratcheted_addon_usd: float = Field(default=0.0, ge=0.0)
    loss_cut_usd: float = Field(default=0.0, ge=0.0)
    realized_event_pnl_usd: float = 0.0
    realized_day_pnl_usd: float = 0.0
    unresolved_loss_exposure_usd: float = Field(default=0.0, ge=0.0)
    current_position_notional_usd: float = Field(default=0.0, ge=0.0)
    open_order_notional_usd: float = Field(default=0.0, ge=0.0)
    pending_intent_notional_usd: float = Field(default=0.0, ge=0.0)
    used_notional_usd: float = Field(default=0.0, ge=0.0)
    remaining_notional_usd: float = Field(default=0.0, ge=0.0)
    budget_status: Literal["within_budget", "exhausted", "over_budget"] = "within_budget"
    blocker_codes: list[str] = Field(default_factory=list)
    policy: EventRiskBudgetPolicy
    side_budget_caps_usd: dict[str, float] = Field(default_factory=dict)
    phase_budget_caps_usd: dict[str, float] = Field(default_factory=dict)
    sleeve_role_budget_caps_usd: dict[str, float] = Field(default_factory=dict)


class SleeveTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sleeve_id: str = Field(min_length=1)
    sleeve_role: str = Field(min_length=1)
    action: SleeveAction
    side: str | None = None
    phase: str | None = None
    requested_shares: float = Field(default=0.0, ge=0.0)
    max_price: float | None = Field(default=None, ge=0.0, le=1.0)
    existing_position_shares: float = Field(default=0.0, ge=0.0)
    open_order_shares: float = Field(default=0.0, ge=0.0)
    pending_intent_shares: float = Field(default=0.0, ge=0.0)
    target_coverage_shares: float = Field(default=0.0, ge=0.0)
    side_used_notional_usd: float = Field(default=0.0, ge=0.0)
    phase_used_notional_usd: float = Field(default=0.0, ge=0.0)
    sleeve_used_notional_usd: float = Field(default=0.0, ge=0.0)
    active_cycle_count: int = Field(default=0, ge=0)
    allow_existing_position_add: bool = False
    enabled: bool = True


def build_event_risk_budget_policy(
    risk_mode: RiskMode = "development",
    **overrides: object,
) -> EventRiskBudgetPolicy:
    defaults = dict(_RISK_MODE_DEFAULTS[risk_mode])
    defaults["risk_mode"] = risk_mode
    defaults.update({key: value for key, value in overrides.items() if value is not None})
    return EventRiskBudgetPolicy(**defaults)


class SleeveTransitionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sleeve_id: str = Field(min_length=1)
    sleeve_role: str = Field(min_length=1)
    action: SleeveAction
    status: SleeveTransitionStatus
    side: str | None = None
    phase: str | None = None
    requested_notional_usd: float = Field(default=0.0, ge=0.0)
    remaining_notional_usd_after: float = Field(default=0.0, ge=0.0)
    side_remaining_notional_usd_after: float | None = Field(default=None, ge=0.0)
    phase_remaining_notional_usd_after: float | None = Field(default=None, ge=0.0)
    sleeve_remaining_notional_usd_after: float | None = Field(default=None, ge=0.0)
    reason_codes: list[str] = Field(default_factory=list)


class EventSleeveTransitionBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "event_sleeve_transition_bundle_v1"
    event_id: str = Field(min_length=1)
    budget: EventRiskBudgetSnapshot
    decisions: list[SleeveTransitionDecision] = Field(default_factory=list)
    side_usage_usd: dict[str, float] = Field(default_factory=dict)
    phase_usage_usd: dict[str, float] = Field(default_factory=dict)
    sleeve_usage_usd: dict[str, float] = Field(default_factory=dict)

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
    realized_event_pnl_usd: float = 0.0,
    realized_day_pnl_usd: float = 0.0,
    unresolved_loss_exposure_usd: float = 0.0,
) -> EventRiskBudgetSnapshot:
    policy = policy or EventRiskBudgetPolicy()
    portfolio_cap = max(portfolio_value_usd, 0.0) * policy.event_cap_pct
    cash_cap = max(available_cash_usd, 0.0) * policy.cash_cap_pct
    base_event_cap = min(portfolio_cap, cash_cap, policy.absolute_event_cap_usd)
    loss_adjusted_event_cap, loss_cut = _apply_loss_cut(
        base_event_cap,
        policy=policy,
        realized_event_pnl_usd=realized_event_pnl_usd,
        unresolved_loss_exposure_usd=unresolved_loss_exposure_usd,
    )
    profit_addon = _profit_ratcheted_addon(
        policy=policy,
        realized_event_pnl_usd=realized_event_pnl_usd,
        realized_day_pnl_usd=realized_day_pnl_usd,
        unresolved_loss_exposure_usd=unresolved_loss_exposure_usd,
    )
    event_cap = min(loss_adjusted_event_cap + profit_addon, max(available_cash_usd, 0.0))
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
        base_event_cap_usd=round(base_event_cap, 6),
        portfolio_cap_usd=round(portfolio_cap, 6),
        cash_cap_usd=round(cash_cap, 6),
        absolute_event_cap_usd=policy.absolute_event_cap_usd,
        profit_ratcheted_addon_usd=round(profit_addon, 6),
        loss_cut_usd=round(loss_cut, 6),
        realized_event_pnl_usd=round(realized_event_pnl_usd, 6),
        realized_day_pnl_usd=round(realized_day_pnl_usd, 6),
        unresolved_loss_exposure_usd=round(max(unresolved_loss_exposure_usd, 0.0), 6),
        current_position_notional_usd=current_position_notional_usd,
        open_order_notional_usd=open_order_notional_usd,
        pending_intent_notional_usd=pending_intent_notional_usd,
        used_notional_usd=round(used, 6),
        remaining_notional_usd=round(remaining, 6),
        budget_status=status,
        blocker_codes=blockers,
        policy=policy,
        side_budget_caps_usd=_side_budget_caps(policy, event_cap),
        phase_budget_caps_usd=_phase_budget_caps(policy, event_cap),
        sleeve_role_budget_caps_usd=_sleeve_role_budget_caps(policy, event_cap),
    )


def _apply_loss_cut(
    base_event_cap: float,
    *,
    policy: EventRiskBudgetPolicy,
    realized_event_pnl_usd: float,
    unresolved_loss_exposure_usd: float,
) -> tuple[float, float]:
    if realized_event_pnl_usd >= max(unresolved_loss_exposure_usd, 0.0):
        return base_event_cap, 0.0
    if policy.loss_cut_event_cap_multiplier >= 1.0:
        return base_event_cap, 0.0
    adjusted = base_event_cap * policy.loss_cut_event_cap_multiplier
    return adjusted, max(base_event_cap - adjusted, 0.0)


def _profit_ratcheted_addon(
    *,
    policy: EventRiskBudgetPolicy,
    realized_event_pnl_usd: float,
    realized_day_pnl_usd: float,
    unresolved_loss_exposure_usd: float,
) -> float:
    realized_profit = max(realized_event_pnl_usd, 0.0) + max(realized_day_pnl_usd, 0.0)
    if realized_profit <= 0.0 or unresolved_loss_exposure_usd >= realized_profit:
        return 0.0
    net_profit = realized_profit - max(unresolved_loss_exposure_usd, 0.0)
    return min(
        net_profit * policy.profit_ratcheted_reinvestment_pct,
        policy.max_profit_ratcheted_addon_usd,
    )


def calibrate_event_risk_policy_from_history(
    rows: list[dict[str, object]],
    *,
    risk_mode: RiskMode = "development",
    min_sample_size: int = 5,
) -> dict[str, object]:
    """Build a risk-policy readback from realized account/DB history rows.

    The return value is evidence for operators and automations. It never mutates
    runtime event controls by itself.
    """

    base = build_event_risk_budget_policy(risk_mode)
    realized_rows = [row for row in rows if row.get("source_confidence") in {"account_confirmed", "db_confirmed"}]
    pnl_values = [_safe_float(row.get("realized_pnl_usd")) for row in realized_rows]
    pnl_values = [value for value in pnl_values if value is not None]
    if len(pnl_values) < min_sample_size:
        return {
            "schema_version": "event_risk_policy_calibration_v1",
            "status": "insufficient_sample",
            "sample_size": len(pnl_values),
            "min_sample_size": min_sample_size,
            "policy": base.model_dump(mode="json"),
            "recommended_changes": [],
            "source_confidence": "insufficient",
        }

    wins = [value for value in pnl_values if value > 0.0]
    losses = [value for value in pnl_values if value < 0.0]
    avg_pnl = sum(pnl_values) / len(pnl_values)
    win_rate = len(wins) / len(pnl_values)
    loss_tail = min(losses) if losses else 0.0
    overrides: dict[str, object] = {}
    recommended_changes: list[str] = []
    if avg_pnl > 0.0 and win_rate >= 0.55:
        overrides["profit_ratcheted_reinvestment_pct"] = min(base.profit_ratcheted_reinvestment_pct + 0.10, 0.60)
        overrides["max_profit_ratcheted_addon_usd"] = min(base.max_profit_ratcheted_addon_usd + 2.0, 10.0)
        recommended_changes.append("increase_realized_profit_reinvestment")
    if loss_tail <= -5.0 or win_rate < 0.45:
        overrides["loss_cut_event_cap_multiplier"] = min(base.loss_cut_event_cap_multiplier, 0.50)
        overrides["max_active_cycles"] = max(base.max_active_cycles - 1, 1)
        recommended_changes.append("tighten_loss_cut_and_active_cycles")
    calibrated = build_event_risk_budget_policy(risk_mode, **overrides)
    return {
        "schema_version": "event_risk_policy_calibration_v1",
        "status": "calibrated" if recommended_changes else "no_change",
        "sample_size": len(pnl_values),
        "avg_realized_pnl_usd": round(avg_pnl, 6),
        "win_rate": round(win_rate, 6),
        "max_loss_usd": round(loss_tail, 6),
        "policy": calibrated.model_dump(mode="json"),
        "recommended_changes": recommended_changes,
        "source_confidence": "account_or_db_confirmed",
    }


def evaluate_event_sleeve_transitions(
    *,
    event_id: str,
    budget: EventRiskBudgetSnapshot,
    sleeves: list[SleeveTransitionRequest],
) -> EventSleeveTransitionBundle:
    remaining = budget.remaining_notional_usd
    decisions: list[SleeveTransitionDecision] = []
    side_usage = _initial_usage_by_key(sleeves, key_name="side", value_name="side_used_notional_usd")
    phase_usage = _initial_usage_by_key(sleeves, key_name="phase", value_name="phase_used_notional_usd")
    sleeve_usage = {sleeve.sleeve_id: round(sleeve.sleeve_used_notional_usd, 6) for sleeve in sleeves}
    for sleeve in sleeves:
        decision = _evaluate_sleeve(
            sleeve,
            remaining=remaining,
            budget=budget,
            side_usage=side_usage,
            phase_usage=phase_usage,
            sleeve_usage=sleeve_usage,
        )
        decisions.append(decision)
        if decision.status == "intent_candidate" and sleeve.action in {"buy", "rebuy"}:
            remaining = max(remaining - decision.requested_notional_usd, 0.0)
            if sleeve.side:
                side_usage[_budget_key(sleeve.side)] = round(
                    side_usage.get(_budget_key(sleeve.side), 0.0) + decision.requested_notional_usd,
                    6,
                )
            if sleeve.phase:
                phase_usage[_budget_key(sleeve.phase)] = round(
                    phase_usage.get(_budget_key(sleeve.phase), 0.0) + decision.requested_notional_usd,
                    6,
                )
            sleeve_usage[sleeve.sleeve_id] = round(
                sleeve_usage.get(sleeve.sleeve_id, 0.0) + decision.requested_notional_usd,
                6,
            )
    return EventSleeveTransitionBundle(
        event_id=event_id,
        budget=budget,
        decisions=decisions,
        side_usage_usd=side_usage,
        phase_usage_usd=phase_usage,
        sleeve_usage_usd=sleeve_usage,
    )


def _evaluate_sleeve(
    sleeve: SleeveTransitionRequest,
    *,
    remaining: float,
    budget: EventRiskBudgetSnapshot,
    side_usage: dict[str, float],
    phase_usage: dict[str, float],
    sleeve_usage: dict[str, float],
) -> SleeveTransitionDecision:
    requested_notional = _requested_notional(sleeve)
    local_remaining = _local_budget_remaining(
        sleeve,
        budget=budget,
        side_usage=side_usage,
        phase_usage=phase_usage,
        sleeve_usage=sleeve_usage,
    )
    if not sleeve.enabled:
        return _decision(sleeve, "monitor_only", requested_notional, remaining, ["sleeve_disabled"], local_remaining)
    if sleeve.action in {"monitor", "hold"}:
        return _decision(sleeve, "monitor_only", requested_notional, remaining, ["monitor_only"], local_remaining)
    if sleeve.action in {"buy", "rebuy"}:
        blockers = _buy_blockers(
            sleeve,
            requested_notional=requested_notional,
            remaining=remaining,
            budget=budget,
            local_remaining=local_remaining,
        )
        if blockers:
            return _decision(sleeve, "blocked", requested_notional, remaining, blockers, local_remaining)
        return _decision(
            sleeve,
            "intent_candidate",
            requested_notional,
            remaining - requested_notional,
            ["budget_available"],
            _decrement_local_remaining(local_remaining, requested_notional),
        )
    blockers = _sell_or_reduce_blockers(sleeve)
    if blockers:
        return _decision(sleeve, "blocked", requested_notional, remaining, blockers, local_remaining)
    if sleeve.target_coverage_shares >= sleeve.existing_position_shares > 0:
        return _decision(
            sleeve,
            "monitor_only",
            requested_notional,
            remaining,
            ["target_already_covers_position"],
            local_remaining,
        )
    return _decision(sleeve, "intent_candidate", requested_notional, remaining, ["position_reduction_available"], local_remaining)


def _buy_blockers(
    sleeve: SleeveTransitionRequest,
    *,
    requested_notional: float,
    remaining: float,
    budget: EventRiskBudgetSnapshot,
    local_remaining: dict[str, float | None],
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
    if sleeve.existing_position_shares > 0.0 and sleeve.action == "buy" and not sleeve.allow_existing_position_add:
        blockers.append("duplicate_open_position")
    if budget.policy.max_active_cycles > 0 and sleeve.active_cycle_count >= budget.policy.max_active_cycles:
        blockers.append("max_active_cycles_reached")
    if sleeve.action == "rebuy" and sleeve.target_coverage_shares < sleeve.existing_position_shares:
        blockers.append("rebuy_requires_existing_position_covered")
    if requested_notional > remaining:
        blockers.append("event_budget_exceeded")
    if _exceeds_local_budget(requested_notional, local_remaining.get("side")):
        blockers.append("side_budget_exceeded")
    if _exceeds_local_budget(requested_notional, local_remaining.get("phase")):
        blockers.append("phase_budget_exceeded")
    if _exceeds_local_budget(requested_notional, local_remaining.get("sleeve")):
        blockers.append("sleeve_budget_exceeded")
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
    local_remaining: dict[str, float | None] | None = None,
) -> SleeveTransitionDecision:
    local_remaining = local_remaining or {}
    return SleeveTransitionDecision(
        sleeve_id=sleeve.sleeve_id,
        sleeve_role=sleeve.sleeve_role,
        action=sleeve.action,
        status=status,
        side=sleeve.side,
        phase=sleeve.phase,
        requested_notional_usd=round(requested_notional_usd, 6),
        remaining_notional_usd_after=round(max(remaining_after, 0.0), 6),
        side_remaining_notional_usd_after=_round_optional(local_remaining.get("side")),
        phase_remaining_notional_usd_after=_round_optional(local_remaining.get("phase")),
        sleeve_remaining_notional_usd_after=_round_optional(local_remaining.get("sleeve")),
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


def _safe_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _side_budget_caps(policy: EventRiskBudgetPolicy, event_cap: float) -> dict[str, float]:
    caps = _pct_caps(policy.side_budget_pct, event_cap)
    if policy.side_budget_mode == "balanced_50_50" and not caps:
        return {"side_a": round(event_cap * 0.5, 6), "side_b": round(event_cap * 0.5, 6)}
    if policy.max_same_side_exposure_pct < 1.0:
        caps["default"] = round(event_cap * policy.max_same_side_exposure_pct, 6)
    return caps


def _phase_budget_caps(policy: EventRiskBudgetPolicy, event_cap: float) -> dict[str, float]:
    if policy.phase_budget_mode == "none":
        return {}
    return _pct_caps(policy.phase_budget_pct, event_cap)


def _sleeve_role_budget_caps(policy: EventRiskBudgetPolicy, event_cap: float) -> dict[str, float]:
    caps = _pct_caps(policy.sleeve_role_budget_pct, event_cap)
    if policy.default_sleeve_budget_pct < 1.0:
        caps["default"] = round(event_cap * policy.default_sleeve_budget_pct, 6)
    for key, value in policy.sleeve_budget_caps_usd.items():
        caps[_budget_key(key)] = round(max(float(value), 0.0), 6)
    return caps


def _pct_caps(values: dict[str, float], event_cap: float) -> dict[str, float]:
    caps: dict[str, float] = {}
    for key, pct in values.items():
        normalized = _budget_key(key)
        if not normalized:
            continue
        caps[normalized] = round(event_cap * min(max(float(pct), 0.0), 1.0), 6)
    return caps


def _initial_usage_by_key(sleeves: list[SleeveTransitionRequest], *, key_name: str, value_name: str) -> dict[str, float]:
    usage: dict[str, float] = {}
    for sleeve in sleeves:
        key = _budget_key(getattr(sleeve, key_name) or "")
        if not key:
            continue
        usage[key] = max(usage.get(key, 0.0), float(getattr(sleeve, value_name) or 0.0))
    return usage


def _local_budget_remaining(
    sleeve: SleeveTransitionRequest,
    *,
    budget: EventRiskBudgetSnapshot,
    side_usage: dict[str, float],
    phase_usage: dict[str, float],
    sleeve_usage: dict[str, float],
) -> dict[str, float | None]:
    side_key = _budget_key(sleeve.side or "")
    phase_key = _budget_key(sleeve.phase or "")
    side_cap = _cap_for_side(budget, side_key)
    phase_cap = _cap_for_phase(budget, phase_key)
    sleeve_cap = _cap_for_sleeve(budget, sleeve)
    return {
        "side": _remaining_from_cap(side_cap, side_usage.get(side_key, 0.0)),
        "phase": _remaining_from_cap(phase_cap, phase_usage.get(phase_key, 0.0)),
        "sleeve": _remaining_from_cap(sleeve_cap, sleeve_usage.get(sleeve.sleeve_id, 0.0)),
    }


def _decrement_local_remaining(values: dict[str, float | None], requested_notional: float) -> dict[str, float | None]:
    return {
        key: None if value is None else max(float(value) - requested_notional, 0.0)
        for key, value in values.items()
    }


def _cap_for_side(budget: EventRiskBudgetSnapshot, side_key: str) -> float | None:
    if not side_key:
        return None
    caps = budget.side_budget_caps_usd
    if side_key in caps:
        return caps[side_key]
    if budget.policy.side_budget_mode == "balanced_50_50":
        return round(budget.event_cap_usd * 0.5, 6)
    if "default" in caps:
        return caps["default"]
    return None


def _cap_for_phase(budget: EventRiskBudgetSnapshot, phase_key: str) -> float | None:
    if not phase_key or budget.policy.phase_budget_mode == "none":
        return None
    return budget.phase_budget_caps_usd.get(phase_key)


def _cap_for_sleeve(budget: EventRiskBudgetSnapshot, sleeve: SleeveTransitionRequest) -> float | None:
    caps = budget.sleeve_role_budget_caps_usd
    sleeve_key = _budget_key(sleeve.sleeve_id)
    role_key = _budget_key(sleeve.sleeve_role)
    if sleeve_key in caps:
        return caps[sleeve_key]
    if role_key in caps:
        return caps[role_key]
    return caps.get("default")


def _remaining_from_cap(cap: float | None, used: float) -> float | None:
    if cap is None:
        return None
    return round(max(cap - used, 0.0), 6)


def _exceeds_local_budget(requested_notional: float, remaining: float | None) -> bool:
    return remaining is not None and requested_notional > remaining


def _round_optional(value: float | None) -> float | None:
    if value is None:
        return None
    return round(max(float(value), 0.0), 6)


def _budget_key(value: str) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


__all__ = [
    "EventRiskBudgetPolicy",
    "EventRiskBudgetSnapshot",
    "EventSleeveTransitionBundle",
    "RiskMode",
    "SleeveAction",
    "SideBudgetMode",
    "PhaseBudgetMode",
    "SleeveTransitionDecision",
    "SleeveTransitionRequest",
    "SleeveTransitionStatus",
    "build_event_risk_budget_policy",
    "calibrate_event_risk_policy_from_history",
    "derive_event_risk_budget",
    "evaluate_event_sleeve_transitions",
]
