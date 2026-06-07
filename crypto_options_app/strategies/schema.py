from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from crypto_options_app.indicators.definitions import indicator_definition_map
from crypto_options_app.signals.contracts import GENERATOR_IDS


REQUIRED_FIELDS = {
    "strategy_id",
    "strategy_version",
    "strategy_family",
    "enabled",
    "budget_usd",
    "allowed_order_types",
    "signal_inputs",
    "signal_aggregation",
    "event_timing_rules",
    "indicator_timing_rules",
    "entry_rules",
    "exit_rules",
    "hedge_rules",
    "cashout_rules",
    "duplicate_exposure_rules",
    "risk_gates",
    "stop_gates",
    "replay_requirements",
    "live_pulse_requirements",
    "observability_fields",
}

ALLOWED_ORDER_TYPES = {"market_buy", "limit_buy", "market_sell", "limit_sell"}
ALLOWED_EVENT_SIGNALS = {"target_delta", "time_remaining", "last_event_outcome", "event_phase", "yes_no_price_phase"}
ALLOWED_FAMILIES = {
    "outcome_prediction",
    "outcome_prediction_with_hedge",
    "outcome_prediction_with_scalping",
    "outcome_prediction_with_cashout",
    "hedge_management",
    "scalping",
    "hybrid",
}


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    strategy_version: str
    strategy_family: str
    enabled: bool
    budget_usd: float
    allowed_order_types: tuple[str, ...]
    signal_inputs: Mapping[str, Any]
    signal_aggregation: Mapping[str, Any]
    event_timing_rules: Mapping[str, Any]
    indicator_timing_rules: Mapping[str, Any]
    entry_rules: Mapping[str, Any]
    exit_rules: Mapping[str, Any]
    hedge_rules: Mapping[str, Any]
    cashout_rules: Mapping[str, Any]
    duplicate_exposure_rules: Mapping[str, Any]
    risk_gates: Mapping[str, Any]
    stop_gates: Mapping[str, Any]
    replay_requirements: Mapping[str, Any]
    live_pulse_requirements: Mapping[str, Any]
    observability_fields: tuple[str, ...]
    state: str = "draft"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def with_enabled(self, enabled: bool) -> "StrategySpec":
        return replace(self, enabled=enabled)


@dataclass(frozen=True)
class StrategyValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()


def strategy_from_dict(payload: Mapping[str, Any]) -> StrategySpec:
    missing = REQUIRED_FIELDS - set(payload)
    if missing:
        raise ValueError(f"missing required strategy fields: {sorted(missing)}")
    return StrategySpec(
        strategy_id=str(payload["strategy_id"]),
        strategy_version=str(payload["strategy_version"]),
        strategy_family=str(payload["strategy_family"]),
        enabled=bool(payload["enabled"]),
        budget_usd=float(payload["budget_usd"]),
        allowed_order_types=tuple(payload["allowed_order_types"]),
        signal_inputs=dict(payload["signal_inputs"]),
        signal_aggregation=dict(payload["signal_aggregation"]),
        event_timing_rules=dict(payload["event_timing_rules"]),
        indicator_timing_rules=dict(payload["indicator_timing_rules"]),
        entry_rules=dict(payload["entry_rules"]),
        exit_rules=dict(payload["exit_rules"]),
        hedge_rules=dict(payload["hedge_rules"]),
        cashout_rules=dict(payload["cashout_rules"]),
        duplicate_exposure_rules=dict(payload["duplicate_exposure_rules"]),
        risk_gates=dict(payload["risk_gates"]),
        stop_gates=dict(payload["stop_gates"]),
        replay_requirements=dict(payload["replay_requirements"]),
        live_pulse_requirements=dict(payload["live_pulse_requirements"]),
        observability_fields=tuple(payload["observability_fields"]),
        state=str(payload.get("state", "draft")),
        metadata=dict(payload.get("metadata", {})),
    )


def validate_strategy_spec(spec: StrategySpec) -> StrategyValidationResult:
    errors: list[str] = []
    if spec.strategy_family not in ALLOWED_FAMILIES:
        errors.append("unsupported_strategy_family")
    if spec.budget_usd <= 0:
        errors.append("budget_must_be_positive")
    unsupported_orders = sorted(set(spec.allowed_order_types) - ALLOWED_ORDER_TYPES)
    if unsupported_orders:
        errors.append(f"unsupported_order_types:{unsupported_orders}")
    _validate_signal_inputs(spec, errors)
    if not spec.entry_rules.get("requires_event_token_key"):
        errors.append("entry_requires_event_token_key_missing")
    if not spec.exit_rules.get("lifecycle_coverage"):
        errors.append("exit_lifecycle_coverage_missing")
    if spec.strategy_family in {"outcome_prediction_with_hedge", "hedge_management"} and not spec.hedge_rules.get("aggregate_inventory_model"):
        errors.append("hedge_requires_aggregate_inventory_model")
    if spec.strategy_family in {"scalping", "outcome_prediction_with_scalping"} and not spec.exit_rules.get("scalping_exit_coverage"):
        errors.append("scalping_exit_coverage_missing")
    if not spec.risk_gates:
        errors.append("risk_gates_missing")
    if not spec.stop_gates:
        errors.append("stop_gates_missing")
    if not spec.replay_requirements.get("requires_replay_frame"):
        errors.append("replay_frame_requirement_missing")
    if not spec.live_pulse_requirements.get("supervised_runtime_gate"):
        errors.append("pulse_supervised_runtime_gate_missing")
    if "event_token_key" not in spec.observability_fields:
        errors.append("event_token_key_observability_missing")
    return StrategyValidationResult(not errors, tuple(errors))


def _validate_signal_inputs(spec: StrategySpec, errors: list[str]) -> None:
    profile_inputs = spec.signal_inputs.get("profile", [])
    for signal_id in profile_inputs:
        if signal_id not in GENERATOR_IDS:
            errors.append(f"unknown_profile_signal:{signal_id}")
    event_inputs = spec.signal_inputs.get("event", [])
    for signal_id in event_inputs:
        if signal_id not in ALLOWED_EVENT_SIGNALS:
            errors.append(f"unknown_event_signal:{signal_id}")
    indicator_inputs = spec.signal_inputs.get("indicator", [])
    known_indicators = set(indicator_definition_map())
    for signal_id in indicator_inputs:
        if signal_id not in known_indicators:
            errors.append(f"unknown_indicator_signal:{signal_id}")
