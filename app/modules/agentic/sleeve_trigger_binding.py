from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agentic.contracts import (
    LiveSignal,
    LiveSignalFreshness,
    LiveSignalPriceBand,
    LiveSignalRiskRequest,
    LiveSignalSource,
    LiveSignalType,
)


SleeveTriggerAction = Literal["buy", "sell", "rebuy", "reduce", "monitor", "block"]


class SleeveTriggerBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "sleeve_trigger_binding_v1"
    binding_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    trigger_type: str = Field(min_length=1)
    trigger_source: str = Field(min_length=1)
    action: SleeveTriggerAction
    side: str | None = None
    market_id: str | None = None
    outcome_id: str | None = None
    market_token_id: str | None = None
    requested_shares: float | None = Field(default=None, ge=0.0)
    requested_notional_usd: float | None = Field(default=None, ge=0.0)
    max_price: float | None = Field(default=None, ge=0.0, le=1.0)
    current_price: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_source: str | None = None
    strategy_id: str | None = None
    strategy_family: str | None = None
    sleeve_id: str | None = None
    sleeve_group: str | None = None
    sleeve_role: str | None = None
    cycle_id: str | None = None
    local_block: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    evidence_paths: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class SleeveTriggerBindingEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "sleeve_trigger_binding_evidence_v1"
    event_id: str = Field(min_length=1)
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    binding_count: int = Field(default=0, ge=0)
    actionable_binding_count: int = Field(default=0, ge=0)
    blocker_binding_count: int = Field(default=0, ge=0)
    microcycle_binding_count: int = Field(default=0, ge=0)
    strategy_state_binding_count: int = Field(default=0, ge=0)
    llm_trigger_binding_count: int = Field(default=0, ge=0)
    execution_boundary: Literal["evidence_only"] = "evidence_only"
    bindings: list[SleeveTriggerBinding] = Field(default_factory=list)


def build_sleeve_trigger_binding_evidence(
    *,
    event_id: str,
    plan: dict[str, Any],
    evaluation: dict[str, Any],
    market_state: dict[str, Any],
    min_size: float,
    source: str,
) -> SleeveTriggerBindingEvidence:
    strategies = _strategies_by_id(plan)
    evidence_paths = list(_nested_dict(market_state.get("normalized_live_snapshot")).get("evidence_paths") or [])
    bindings: list[SleeveTriggerBinding] = []
    bindings.extend(
        _bindings_from_sleeve_states(
            event_id=event_id,
            plan=plan,
            evaluation=evaluation,
            market_state=market_state,
            strategies=strategies,
            min_size=min_size,
            source=source,
            evidence_paths=evidence_paths,
        )
    )
    bindings.extend(
        _bindings_from_paired_microcycle(
            event_id=event_id,
            plan=plan,
            market_state=market_state,
            strategies=strategies,
            min_size=min_size,
            source=source,
            evidence_paths=evidence_paths,
        )
    )
    bindings.extend(
        _bindings_from_llm_runtime_triggers(
            event_id=event_id,
            market_state=market_state,
            source=source,
            evidence_paths=evidence_paths,
        )
    )
    return SleeveTriggerBindingEvidence(
        event_id=event_id,
        binding_count=len(bindings),
        actionable_binding_count=sum(1 for binding in bindings if binding.action in {"buy", "sell", "rebuy", "reduce"}),
        blocker_binding_count=sum(1 for binding in bindings if binding.action == "block"),
        microcycle_binding_count=sum(1 for binding in bindings if binding.trigger_source == "paired_microcycle"),
        strategy_state_binding_count=sum(1 for binding in bindings if binding.trigger_source == "strategy_plan_evaluation"),
        llm_trigger_binding_count=sum(1 for binding in bindings if binding.trigger_source == "llm_runtime"),
        bindings=bindings,
    )


def live_signals_from_sleeve_trigger_bindings(
    evidence: SleeveTriggerBindingEvidence,
    *,
    source: LiveSignalSource = "deterministic",
) -> list[LiveSignal]:
    signals: list[LiveSignal] = []
    now = evidence.generated_at_utc
    for binding in evidence.bindings:
        signal_type = _signal_type_from_binding_action(binding.action)
        if signal_type is None:
            continue
        signals.append(
            LiveSignal(
                event_id=binding.event_id,
                market_id=binding.market_id,
                outcome_id=binding.outcome_id,
                market_token_id=binding.market_token_id,
                source=source,
                signal_type=signal_type,
                side=binding.side,
                emitted_at_utc=now,
                price_band=(
                    LiveSignalPriceBand(current_price=binding.current_price, target_price=binding.max_price)
                    if binding.current_price is not None or binding.max_price is not None
                    else None
                ),
                confidence=binding.confidence,
                confidence_source=binding.confidence_source,
                freshness=LiveSignalFreshness(source_timestamp_utc=now, stale=False),
                reason_codes=binding.reason_codes,
                risk_request=LiveSignalRiskRequest(
                    sleeve_id=binding.sleeve_id,
                    sleeve_role=binding.sleeve_role,
                    requested_notional_usd=binding.requested_notional_usd,
                    requested_shares=binding.requested_shares,
                    max_price=binding.max_price,
                ),
                evidence_paths=binding.evidence_paths,
                payload={
                    **binding.payload,
                    "aggregation_scope": "local_sleeve" if binding.local_block else "candidate",
                    "binding_id": binding.binding_id,
                    "trigger_type": binding.trigger_type,
                    "trigger_source": binding.trigger_source,
                    "sleeve_id": binding.sleeve_id,
                    "sleeve_group": binding.sleeve_group,
                    "sleeve_role": binding.sleeve_role,
                    "strategy_id": binding.strategy_id,
                    "strategy_family": binding.strategy_family,
                    "cycle_id": binding.cycle_id,
                },
            )
        )
    return signals


def _bindings_from_sleeve_states(
    *,
    event_id: str,
    plan: dict[str, Any],
    evaluation: dict[str, Any],
    market_state: dict[str, Any],
    strategies: dict[str, dict[str, Any]],
    min_size: float,
    source: str,
    evidence_paths: list[str],
) -> list[SleeveTriggerBinding]:
    states = evaluation.get("sleeve_states") if isinstance(evaluation, dict) else None
    bindings: list[SleeveTriggerBinding] = []
    for index, state in enumerate(states or []):
        if not isinstance(state, dict):
            continue
        strategy_id = str(state.get("strategy_id") or "").strip()
        strategy = strategies.get(strategy_id, {})
        entry_rules = _nested_dict(strategy.get("entry_rules"))
        status = str(state.get("status") or "").strip()
        action = _action_from_sleeve_state(status, entry_rules, strategy=strategy, state=state)
        outcome_id = _clean(entry_rules.get("outcome_id"))
        token_id = _clean(entry_rules.get("token_id") or entry_rules.get("asset_id"))
        strategy_state = _strategy_market_state(market_state=market_state, outcome_id=outcome_id, token_id=token_id, strategy_id=strategy_id)
        price = _first_float({**strategy_state, **entry_rules}, ("price", "current_price", "best_ask", "best_bid", "max_price"))
        sleeve_id = _clean(state.get("sleeve_id") or entry_rules.get("sleeve_id") or strategy_id) or strategy_id
        reasons = _string_list(state.get("blocker_reasons")) or [f"strategy_plan_{status or 'monitor'}"]
        requested_notional = _requested_notional_from_minimum_sizing(entry_rules, strategy=strategy, state=state)
        if requested_notional is not None:
            reasons = _unique_strings([*reasons, "ultra_low_min_notional_resize_candidate"])
        bindings.append(
            SleeveTriggerBinding(
                binding_id=_binding_id(event_id, "strategy", strategy_id or sleeve_id or str(index), status or str(index)),
                event_id=event_id,
                trigger_type="strategy_plan_sleeve_state",
                trigger_source="strategy_plan_evaluation",
                action=action,
                side=_clean(strategy.get("side") or entry_rules.get("outcome_label") or state.get("sleeve_side")),
                market_id=_clean(entry_rules.get("market_id") or plan.get("market_id")),
                outcome_id=outcome_id,
                market_token_id=token_id,
                requested_shares=_first_float(entry_rules, ("size", "requested_shares", "shares")) or min_size,
                requested_notional_usd=requested_notional,
                max_price=price,
                current_price=price,
                confidence=_confidence_from_status(status),
                confidence_source=f"{source}:strategy_plan_sleeve_state",
                strategy_id=strategy_id or None,
                strategy_family=_clean(state.get("strategy_family") or strategy.get("family")),
                sleeve_id=sleeve_id,
                sleeve_group=_clean(state.get("sleeve_group") or strategy.get("sleeve_group")),
                sleeve_role=_clean(state.get("sleeve_role") or strategy.get("sleeve_role") or entry_rules.get("sleeve_role")),
                local_block=action == "block",
                reason_codes=reasons,
                evidence_paths=evidence_paths,
                payload={
                    "sleeve_status": status,
                    "intent_count": state.get("intent_count"),
                    "blocker_count": state.get("blocker_count"),
                },
            )
        )
    return bindings


def _bindings_from_paired_microcycle(
    *,
    event_id: str,
    plan: dict[str, Any],
    market_state: dict[str, Any],
    strategies: dict[str, dict[str, Any]],
    min_size: float,
    source: str,
    evidence_paths: list[str],
) -> list[SleeveTriggerBinding]:
    paired = _nested_dict(market_state.get("paired_microcycle"))
    cycles = paired.get("cycles") if isinstance(paired.get("cycles"), list) else []
    market_id = _clean(plan.get("market_id"))
    bindings: list[SleeveTriggerBinding] = []
    for index, cycle in enumerate(cycles):
        if not isinstance(cycle, dict):
            continue
        action = _action_from_microcycle(cycle)
        if action is None:
            continue
        strategy_id = _clean(cycle.get("strategy_id"))
        strategy = strategies.get(strategy_id or "", {})
        entry_rules = _nested_dict(strategy.get("entry_rules"))
        sleeve_id = _clean(cycle.get("sleeve_id") or entry_rules.get("sleeve_id") or strategy_id)
        leg = _microcycle_leg_for_action(cycle, action)
        requested_shares = _first_float(leg, ("shares",)) or _first_float(cycle, ("configured_entry_shares",)) or min_size
        price = _first_float(leg, ("price",)) or _first_float(cycle, ("configured_target_price",)) or _first_float(entry_rules, ("price", "max_price"))
        status = _clean(cycle.get("status")) or "unknown"
        next_action = _clean(cycle.get("next_action")) or action
        local_block = action == "block"
        bindings.append(
            SleeveTriggerBinding(
                binding_id=_binding_id(event_id, "microcycle", sleeve_id or strategy_id or str(index), next_action),
                event_id=event_id,
                trigger_type="paired_microcycle_next_leg" if not local_block else "paired_microcycle_local_block",
                trigger_source="paired_microcycle",
                action=action,
                side=_clean(cycle.get("outcome_label") or strategy.get("side") or entry_rules.get("outcome_label")),
                market_id=market_id,
                outcome_id=_clean(cycle.get("outcome_id") or entry_rules.get("outcome_id")),
                market_token_id=_clean(cycle.get("token_id") or entry_rules.get("token_id") or entry_rules.get("asset_id")),
                requested_shares=requested_shares,
                max_price=price,
                current_price=_first_float(leg, ("price",)),
                confidence=0.8 if not local_block else 0.7,
                confidence_source=f"{source}:paired_microcycle",
                strategy_id=strategy_id,
                strategy_family=_clean(strategy.get("family")),
                sleeve_id=sleeve_id,
                sleeve_group=_clean(strategy.get("sleeve_group")),
                sleeve_role=_clean(cycle.get("sleeve_role") or strategy.get("sleeve_role") or entry_rules.get("sleeve_role")),
                cycle_id=_clean(cycle.get("cycle_id")),
                local_block=local_block,
                reason_codes=_string_list(cycle.get("reason_codes")) or [status],
                evidence_paths=evidence_paths,
                payload={
                    "microcycle_status": status,
                    "microcycle_next_action": next_action,
                    "duplicate_buy_blocked": bool(cycle.get("duplicate_buy_blocked")),
                    "next_leg_candidate": bool(cycle.get("next_leg_candidate")),
                },
            )
        )
    return bindings


def _bindings_from_llm_runtime_triggers(
    *,
    event_id: str,
    market_state: dict[str, Any],
    source: str,
    evidence_paths: list[str],
) -> list[SleeveTriggerBinding]:
    triggers = market_state.get("llm_runtime_triggers")
    if not isinstance(triggers, list):
        return []
    bindings: list[SleeveTriggerBinding] = []
    for index, trigger in enumerate(triggers):
        if not isinstance(trigger, dict):
            continue
        trigger_type = _clean(trigger.get("trigger_type") or trigger.get("type") or trigger.get("name")) or "llm_runtime_trigger"
        bindings.append(
            SleeveTriggerBinding(
                binding_id=_binding_id(event_id, "llm", trigger_type, str(index)),
                event_id=event_id,
                trigger_type=trigger_type,
                trigger_source="llm_runtime",
                action="monitor",
                confidence=_first_float(trigger, ("confidence",)),
                confidence_source=f"{source}:llm_runtime",
                reason_codes=_string_list(trigger.get("reason_codes")) or [trigger_type],
                evidence_paths=evidence_paths,
                payload={"trigger": trigger},
            )
        )
    return bindings


def _action_from_sleeve_state(
    status: str,
    entry_rules: dict[str, Any],
    *,
    strategy: dict[str, Any],
    state: dict[str, Any],
) -> SleeveTriggerAction:
    if status == "intent_created":
        side = str(entry_rules.get("side") or "buy").strip().lower()
        return "sell" if side == "sell" else "buy"
    if status == "blocked" and _is_ultra_low_min_notional_resize_candidate(entry_rules, strategy=strategy, state=state):
        return "buy"
    if status == "blocked":
        return "block"
    return "monitor"


def _is_ultra_low_min_notional_resize_candidate(
    entry_rules: dict[str, Any],
    *,
    strategy: dict[str, Any],
    state: dict[str, Any],
) -> bool:
    blockers = set(_string_list(state.get("blocker_reasons")))
    if "minimum_buy_notional_not_met" not in blockers:
        return False
    if not _is_ultra_low_strategy(strategy, entry_rules):
        return False
    if not _truthy_any(entry_rules, ("allow_sub_10c_underdog_grid", "allow_ultra_low_grid", "allow_0_5c_to_5c_grid")):
        return False
    return _truthy_any(entry_rules, ("allow_ultra_low_underdog", "allow_underdog_below_19c"))


def _requested_notional_from_minimum_sizing(
    entry_rules: dict[str, Any],
    *,
    strategy: dict[str, Any],
    state: dict[str, Any],
) -> float | None:
    if not _is_ultra_low_min_notional_resize_candidate(entry_rules, strategy=strategy, state=state):
        return None
    value = _first_float(entry_rules, ("min_buy_notional_usd", "minimum_buy_notional_usd"))
    return value if value is not None and value > 0.0 else None


def _is_ultra_low_strategy(strategy: dict[str, Any], entry_rules: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(value or "").lower()
        for value in (
            strategy.get("family"),
            strategy.get("sleeve_role"),
            strategy.get("sleeve_id"),
            strategy.get("strategy_id"),
            entry_rules.get("sleeve_role"),
        )
    )
    return any(marker in haystack for marker in ("ultra_low", "ultralow", "subpenny", "decimal_grid"))


def _truthy_any(values: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for key in keys:
        value = values.get(key)
        if isinstance(value, str):
            if value.strip().lower() in {"1", "true", "yes", "y"}:
                return True
            continue
        if bool(value):
            return True
    return False


def _action_from_microcycle(cycle: dict[str, Any]) -> SleeveTriggerAction | None:
    next_action = str(cycle.get("next_action") or "").strip()
    if next_action in {"place_paired_sell", "replace_paired_sell"} and cycle.get("next_leg_candidate"):
        return "sell"
    if next_action == "place_paired_rebuy" and cycle.get("next_leg_candidate"):
        return "rebuy"
    if cycle.get("duplicate_buy_blocked"):
        return "block"
    return None


def _microcycle_leg_for_action(cycle: dict[str, Any], action: SleeveTriggerAction) -> dict[str, Any]:
    if action == "rebuy":
        return _nested_dict(cycle.get("rebuy_leg"))
    if action in {"sell", "block"}:
        return _nested_dict(cycle.get("sell_leg"))
    return {}


def _signal_type_from_binding_action(action: SleeveTriggerAction) -> LiveSignalType | None:
    if action in {"buy", "sell", "rebuy", "reduce", "monitor", "block"}:
        return action
    return None


def _strategies_by_id(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(strategy.get("strategy_id") or "").strip(): strategy
        for strategy in plan.get("active_strategies") or []
        if isinstance(strategy, dict) and str(strategy.get("strategy_id") or "").strip()
    }


def _strategy_market_state(
    *,
    market_state: dict[str, Any],
    outcome_id: str | None,
    token_id: str | None,
    strategy_id: str | None,
) -> dict[str, Any]:
    state: dict[str, Any] = {}
    for bucket, key in (
        ("strategy_states", strategy_id),
        ("strategy_market_states", strategy_id),
        ("outcome_states", outcome_id),
        ("outcome_market_states", outcome_id),
        ("token_states", token_id),
        ("token_market_states", token_id),
    ):
        values = market_state.get(bucket)
        if isinstance(values, dict) and key and isinstance(values.get(key), dict):
            state.update(values[key])
    return state


def _confidence_from_status(status: str) -> float | None:
    if status == "intent_created":
        return 0.72
    if status == "blocked":
        return 0.65
    if status:
        return 0.5
    return None


def _first_float(values: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = values.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _nested_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _binding_id(event_id: str, *parts: str) -> str:
    raw = [event_id, *parts]
    return ":".join(_slug(part) for part in raw if _slug(part))


def _slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    keep = []
    for char in text:
        keep.append(char if char.isalnum() else "-")
    return "-".join(part for part in "".join(keep).split("-") if part)
