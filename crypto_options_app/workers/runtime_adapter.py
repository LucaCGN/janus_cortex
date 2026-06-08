from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from collections.abc import Callable, Mapping
from typing import Any, Literal

from crypto_options_app.replay.fill_simulation import ReplayOrder, simulate_fill
from crypto_options_app.replay.frames import ReplayFrame
from crypto_options_app.reports.run_report import RunReport, build_run_report
from crypto_options_app.reports.strategy_report import CandidateAttributionReport, build_candidate_attribution_report
from crypto_options_app.risk.gates import CandidateRiskContext, RiskLimits, evaluate_risk_gates
from crypto_options_app.risk.stop_gates import RunPerformance, evaluate_stop_gates
from crypto_options_app.strategies.hedge_floor import (
    _liquidity_bucket,
    _slippage_bucket,
    _spread_bucket,
    crypto_tail_context_bucket,
    crypto_tail_distance_bucket,
    floor_preserving_order_gate,
    grid_viability,
    hedge_floor_state,
    paired_seed_entry_projection,
    profile_tail_context_bucket,
    profile_tail_price_context_bucket,
    protected_floor_improvement,
    surplus_tail_budget,
    tail_reversal_probability,
)
from crypto_options_app.strategies.candidates import build_structural_candidate
from crypto_options_app.strategies.registry import all_strategy_specs
from crypto_options_app.strategies.schema import StrategySpec
from crypto_options_app.trading.executor_boundary import ExecutorBoundaryConfig, executor_boundary_ready
from crypto_options_app.trading.exits import create_exit_order, create_exit_plan, validate_duplicate_exit_orders, validate_lifecycle_coverage
from crypto_options_app.trading.fills import FillState, record_fill
from crypto_options_app.trading.intents import ExecutionIntent, create_execution_intent
from crypto_options_app.trading.managed_positions import (
    ManagedIntentPlan,
    ManagedPositionState,
    cashout_rebuy_plan,
    hedge_rebalance_plan,
)
from crypto_options_app.trading.orders import OrderState, create_order_from_intent, transition_order
from crypto_options_app.trading.positions import PositionState, create_position_from_buy_fill
from crypto_options_app.trading.reconciliation import ReconciliationResult, reconcile_order


RuntimeMode = Literal["dry_run", "shadow", "live", "supervised_live"]


def _is_live_mode(mode: str) -> bool:
    return mode in {"live", "supervised_live"}


@dataclass(frozen=True)
class RuntimeScenario:
    event_key: str = "fixture-event"
    event_token_key: str = "fixture-event:up"
    token_id: str | None = None
    event_slug: str | None = None
    outcome: str | None = None
    side: str = "BUY"
    shares: float = 1.0
    limit_price: float = 0.5
    quote_age_seconds: float = 1.0
    signal_age_seconds: float = 1.0
    profile_age_seconds: float = 20.0
    spread: float = 0.01
    expected_slippage: float = 0.01
    liquidity_depth: float = 10.0
    time_remaining_seconds: float = 300.0
    live_market_verified: bool = False
    signal_context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SupervisedRuntimeConfig:
    run_id: str
    mode: RuntimeMode
    executor_boundary: ExecutorBoundaryConfig
    max_trades_per_strategy: int = 3
    minimal_sizing: bool = True
    live_cadence_count: int = 1
    allow_live_submission: bool = False
    live_environment_approved: bool = False
    credentials_ready: bool = False
    risk_limits: RiskLimits = field(default_factory=RiskLimits)
    simulate_missing_lifecycle_coverage: bool = False
    simulate_reconciliation_mismatch: bool = False
    supervised_executor: Callable[[ExecutionIntent, OrderState, RuntimeScenario], dict[str, Any]] | None = None
    validation_budget_cap_usd: float = 50.0
    validation_budget_spent_usd: float = 0.0
    cash_balance_before_usd: float | None = None
    cash_balance_hard_stop_usd: float = 100.0
    cash_balance_status: str = "cash_balance_unavailable"


@dataclass(frozen=True)
class RuntimeStrategyResult:
    strategy_id: str
    status: str
    blockers: tuple[str, ...]
    event_key: str | None = None
    event_token_key: str | None = None
    token_id: str | None = None
    event_slug: str | None = None
    outcome: str | None = None
    candidate_key: str | None = None
    intent_key: str | None = None
    order_key: str | None = None
    exchange_order_id: str | None = None
    order_status: str | None = None
    filled_shares: float | None = None
    fill_price: float | None = None
    remote_filled_shares: float | None = None
    position_key: str | None = None
    lifecycle_covered: bool = False
    reconciliation_status: str | None = None
    reconciliation_evidence: dict[str, Any] = field(default_factory=dict)
    orders_allowed: bool = False
    live_trading_authorized: bool = False
    live_submission_attempted: bool = False
    attribution: dict[str, Any] = field(default_factory=dict)

    def to_candidate_row(self) -> dict[str, Any]:
        if self.status == "structural_passed":
            row_status = "ready"
        elif self.status in {"simulated_executed", "live_structural_executed"}:
            row_status = "executed"
        else:
            row_status = "blocked"
        return {
            "strategy_id": self.strategy_id,
            "status": row_status,
            "blockers": self.blockers,
            "sources": self.attribution.get("sources", ()),
            "event_key": self.event_key,
            "event_token_key": self.event_token_key,
            "token_id": self.token_id,
            "event_slug": self.event_slug,
            "outcome": self.outcome,
            "candidate_key": self.candidate_key,
            "intent_key": self.intent_key,
            "order_key": self.order_key,
            "exchange_order_id": self.exchange_order_id,
            "order_status": self.order_status,
            "filled_shares": self.filled_shares,
            "fill_price": self.fill_price,
            "remote_filled_shares": self.remote_filled_shares,
            "position_key": self.position_key,
            "lifecycle_covered": self.lifecycle_covered,
            "reconciliation_status": self.reconciliation_status,
            "reconciliation_evidence": self.reconciliation_evidence,
            "orders_allowed": self.orders_allowed,
            "live_trading_authorized": self.live_trading_authorized,
            "live_submission_attempted": self.live_submission_attempted,
        }


@dataclass(frozen=True)
class RuntimeValidationReport:
    run_id: str
    mode: RuntimeMode
    generated_at_utc: datetime
    results: tuple[RuntimeStrategyResult, ...]
    run_report: RunReport
    validation_budget_cap_usd: float = 50.0
    validation_budget_spent_usd: float = 0.0
    cash_balance_before_usd: float | None = None
    cash_balance_after_usd: float | None = None
    cash_balance_hard_stop_usd: float = 100.0
    cash_balance_status: str = "cash_balance_unavailable"
    manual_orders_avoided: bool = True

    @property
    def passed_count(self) -> int:
        return sum(1 for result in self.results if result.status in {"structural_passed", "simulated_executed", "live_structural_executed"})

    @property
    def blocked_count(self) -> int:
        return sum(1 for result in self.results if result.blockers)

    @property
    def strategy_bundle_comparison_may_begin(self) -> bool:
        return self.mode in {"dry_run", "shadow"} and self.blocked_count == 0


def validate_runtime_config(config: SupervisedRuntimeConfig) -> tuple[str, ...]:
    blockers: list[str] = []
    if config.mode not in {"dry_run", "shadow", "live", "supervised_live"}:
        blockers.append("unsupported_runtime_mode")
    if config.live_cadence_count != 1:
        blockers.append("duplicate_cadence")
    if config.max_trades_per_strategy <= 0:
        blockers.append("invalid_trade_cap")
    if _is_live_mode(config.mode):
        if not executor_boundary_ready(config.executor_boundary):
            blockers.append("executor_boundary_not_ready")
        if not config.allow_live_submission:
            blockers.append("live_submission_not_allowed")
        if not config.live_environment_approved:
            blockers.append("env_live_flags_missing")
        if not config.credentials_ready:
            blockers.append("credentials_access_failure")
        if config.supervised_executor is None:
            blockers.append("live_executor_binding_missing")
    return tuple(blockers)


def validate_all_strategy_scenarios(
    config: SupervisedRuntimeConfig,
    *,
    scenario: RuntimeScenario | None = None,
    specs: tuple[StrategySpec, ...] | None = None,
) -> RuntimeValidationReport:
    scenario = scenario or RuntimeScenario()
    results = tuple(_validate_strategy_scenario(spec, config=config, scenario=scenario) for spec in (specs or all_strategy_specs()))
    live_submission_attempted = any(result.live_submission_attempted for result in results)
    total_submitted_notional = sum(
        float(result.filled_shares) * float(result.fill_price)
        for result in results
        if result.filled_shares is not None and result.fill_price is not None
    )
    cash_balance_after_usd = None
    if config.cash_balance_before_usd is not None:
        cash_balance_after_usd = max(0.0, float(config.cash_balance_before_usd) - total_submitted_notional)
    candidate_reports = [
        build_candidate_attribution_report(strategy_id=result.strategy_id, candidate_rows=[result.to_candidate_row()])
        for result in results
    ]
    mechanical_blockers = tuple(
        blocker
        for result in results
        for blocker in result.blockers
        if blocker in {"missing_lifecycle_coverage", "reconciliation_mismatch", "duplicate_cadence"}
    )
    stop_gates = evaluate_stop_gates(RunPerformance(mechanical_failures=mechanical_blockers)).triggered_gates
    return RuntimeValidationReport(
        run_id=config.run_id,
        mode=config.mode,
        generated_at_utc=datetime.now(UTC),
        results=results,
        run_report=build_run_report(
            run_id=config.run_id,
            stop_gates=stop_gates,
            candidate_reports=candidate_reports,
            summary={
                "mode": config.mode,
                "manual_orders_avoided": True,
                "live_submission_attempted": live_submission_attempted,
                "strategy_count": len(results),
                "validation_budget_cap_usd": config.validation_budget_cap_usd,
                "validation_budget_spent_usd": config.validation_budget_spent_usd,
                "cash_balance_status": config.cash_balance_status,
            },
        ),
        validation_budget_cap_usd=config.validation_budget_cap_usd,
        validation_budget_spent_usd=config.validation_budget_spent_usd,
        cash_balance_before_usd=config.cash_balance_before_usd,
        cash_balance_after_usd=cash_balance_after_usd,
        cash_balance_hard_stop_usd=config.cash_balance_hard_stop_usd,
        cash_balance_status=config.cash_balance_status,
        manual_orders_avoided=True,
    )


def _validate_strategy_scenario(
    spec: StrategySpec,
    *,
    config: SupervisedRuntimeConfig,
    scenario: RuntimeScenario,
) -> RuntimeStrategyResult:
    config_blockers = list(validate_runtime_config(config))
    enabled_spec = spec.with_enabled(True)
    scenario, strategy_decision, strategy_blockers = _strategy_shadow_scenario(enabled_spec, scenario)
    candidate_record = build_structural_candidate(
        enabled_spec,
        event_key=scenario.event_key,
        event_token_key=scenario.event_token_key,
        side=scenario.side,
    )
    blockers = list(config_blockers) + list(candidate_record.blockers) + list(strategy_blockers)
    if _is_live_mode(config.mode) and not scenario.live_market_verified:
        blockers.append("live_market_candidate_not_verified")
    candidate_key = f"candidate:{config.run_id}:{spec.strategy_id}:{scenario.event_token_key}"
    if candidate_record.status != "candidate_ready" or candidate_record.candidate is None:
        return RuntimeStrategyResult(
            strategy_id=spec.strategy_id,
            status="blocked",
            blockers=tuple(blockers or ("candidate_not_ready",)),
            event_key=scenario.event_key,
            event_token_key=scenario.event_token_key,
            token_id=scenario.token_id,
            event_slug=scenario.event_slug,
            outcome=scenario.outcome,
            candidate_key=candidate_key,
            attribution={**_strategy_attribution(enabled_spec), "signal_context": scenario.signal_context, "strategy_decision": strategy_decision},
        )

    risk_result = evaluate_risk_gates(_risk_context_for_scenario(scenario), config.risk_limits)
    blockers.extend(risk_result.blockers)
    if blockers:
        return RuntimeStrategyResult(
            strategy_id=spec.strategy_id,
            status="blocked",
            blockers=tuple(blockers),
            event_key=scenario.event_key,
            event_token_key=scenario.event_token_key,
            token_id=scenario.token_id,
            event_slug=scenario.event_slug,
            outcome=scenario.outcome,
            candidate_key=candidate_key,
            attribution={**_strategy_attribution(enabled_spec), "signal_context": scenario.signal_context, "strategy_decision": strategy_decision},
        )

    intent = _create_entry_intent(enabled_spec, config=config, scenario=scenario, candidate_key=candidate_key)
    order, fill, reconciliation = _execute_order_lifecycle(intent, config=config, scenario=scenario)
    live_submission_attempted = _is_live_mode(config.mode)
    blockers.extend(_shadow_fill_blockers(config=config, order=order, fill=fill))
    blockers.extend(reconciliation.blockers)
    if _has_reconciliation_integrity_mismatch(reconciliation):
        blockers.append("reconciliation_mismatch")
    position = create_position_from_buy_fill(fill) if fill is not None else None
    positions = [position] if position is not None else []
    exit_plans = []
    exit_orders = []
    paired_exit_payloads: list[dict[str, Any]] = []
    coverage_type = _coverage_type_for_strategy(enabled_spec)
    if position is not None and not config.simulate_missing_lifecycle_coverage:
        plan = create_exit_plan(position, coverage_type=coverage_type)
        exit_plans.append(plan)
        if _live_paired_exit_required(config=config, position=position):
            paired_exit_intent = _create_paired_exit_intent(
                enabled_spec,
                config=config,
                scenario=scenario,
                candidate_key=candidate_key,
                position=position,
                fill=fill,
                entry_order=order,
            )
            paired_exit_order, paired_exit_fill, paired_exit_reconciliation = _execute_paired_exit_lifecycle(
                paired_exit_intent,
                config=config,
                scenario=scenario,
            )
            exit_orders.append(create_exit_order(plan, order_key=paired_exit_order.order_key, status=paired_exit_order.status))
            paired_exit_payload = _paired_exit_payload(
                paired_exit_intent,
                paired_exit_order,
                paired_exit_fill,
                paired_exit_reconciliation,
            )
            paired_exit_payloads.append(paired_exit_payload)
            blockers.extend(_paired_exit_blockers(paired_exit_order, paired_exit_reconciliation))
            if _has_reconciliation_integrity_mismatch(paired_exit_reconciliation):
                blockers.append("paired_exit_reconciliation_mismatch")
        else:
            exit_orders.append(create_exit_order(plan, order_key=None, status="created"))
    managed_runtime_plans = _managed_runtime_plans_for_strategy(
        enabled_spec,
        scenario=scenario,
        position=position,
        fill=fill,
    )
    lifecycle_blockers = validate_lifecycle_coverage(positions, exit_plans)
    duplicate_exit_blockers = validate_duplicate_exit_orders(exit_orders)
    blockers.extend(_normalize_lifecycle_blockers(lifecycle_blockers))
    blockers.extend(duplicate_exit_blockers)
    if (
        _is_live_mode(config.mode)
        and order.status in {"submitted", "accepted", "partially_filled", "filled"}
        and fill is None
    ):
        blockers.append("fill_evidence_missing")
    paired_exit_blocked = any(
        blocker.startswith("paired_exit_") or blocker == "exit_order_not_submitted"
        for blocker in blockers
    )
    status = "live_structural_executed" if _is_live_mode(config.mode) and not blockers else "simulated_executed" if not blockers else "blocked"
    return RuntimeStrategyResult(
        strategy_id=spec.strategy_id,
        status=status,
        blockers=tuple(blockers),
        event_key=scenario.event_key,
        event_token_key=scenario.event_token_key,
        token_id=scenario.token_id,
        event_slug=scenario.event_slug,
        outcome=scenario.outcome,
        candidate_key=candidate_key,
        intent_key=intent.intent_key,
        order_key=order.order_key,
        exchange_order_id=order.exchange_order_id,
        order_status=order.status,
        filled_shares=None if fill is None else fill.filled_shares,
        fill_price=None if fill is None else fill.fill_price,
        remote_filled_shares=_optional_float(reconciliation.evidence.get("remote_filled")),
        position_key=None if position is None else position.position_key,
        lifecycle_covered=bool(position is not None and not lifecycle_blockers and not paired_exit_blocked),
        reconciliation_status=reconciliation.status,
        reconciliation_evidence=reconciliation.evidence,
        orders_allowed=_is_live_mode(config.mode) and executor_boundary_ready(config.executor_boundary) and config.allow_live_submission,
        live_trading_authorized=_is_live_mode(config.mode) and executor_boundary_ready(config.executor_boundary) and config.allow_live_submission,
        live_submission_attempted=live_submission_attempted,
        attribution={
            **_strategy_attribution(enabled_spec),
            "signal_context": scenario.signal_context,
            "strategy_decision": strategy_decision,
            "risk_details": risk_result.details,
            "order_type": intent.order_type,
            "order_status": order.status,
            "exchange_order_id": order.exchange_order_id,
            "fill_key": None if fill is None else fill.fill_key,
            "filled_shares": None if fill is None else fill.filled_shares,
            "fill_price": None if fill is None else fill.fill_price,
            "reconciliation_evidence": reconciliation.evidence,
            "exit_plan_count": len(exit_plans),
            "coverage_type": coverage_type,
            "order_source_payload": order.source_payload if isinstance(order.source_payload, dict) else {},
            "paired_exit_orders": paired_exit_payloads,
            "paired_exit_order_count": len(paired_exit_payloads),
            "managed_runtime_plans": [_managed_plan_payload(plan) for plan in managed_runtime_plans],
            "managed_runtime_plan_count": len(managed_runtime_plans),
        },
    )


def _strategy_shadow_scenario(spec: StrategySpec, scenario: RuntimeScenario) -> tuple[RuntimeScenario, dict[str, Any], tuple[str, ...]]:
    context = scenario.signal_context if isinstance(scenario.signal_context, dict) else {}
    blockers: list[str] = []
    decision: dict[str, Any] = {
        "schema_version": "crypto_options_strategy_shadow_decision_v1",
        "strategy_id": spec.strategy_id,
        "base_outcome": scenario.outcome,
        "base_limit_price": scenario.limit_price,
        "base_shares": scenario.shares,
    }
    profile_sources = tuple(spec.signal_inputs.get("profile", ()))
    indicator_sources = tuple(spec.signal_inputs.get("indicator", ()))
    strategy_id = spec.strategy_id
    family = spec.strategy_family
    target_up_ratio, profile_ratio_source, profile_ratio_blockers = _profile_target_ratio(spec, context)
    profile_dependency_mode = str(spec.metadata.get("profile_dependency_mode") or "required").strip().lower()
    profile_confidence_only = profile_dependency_mode in {"confidence_modifier", "optional_confidence", "optional"}
    best_bid = _optional_float(context.get("best_bid"))
    best_ask = _optional_float(context.get("best_ask"))
    spread = _optional_float(scenario.spread) or 0.0
    liquidity = _optional_float(scenario.liquidity_depth) or 0.0
    outcome_is_up = _is_up_outcome(scenario.outcome)
    option_path_decision, option_path_blockers = _option_path_requirements(spec, context, scenario)
    decision.update(option_path_decision)
    blockers.extend(option_path_blockers)
    hedge_floor_decision, hedge_floor_blockers = _hedge_floor_requirements(
        spec,
        context=context,
        scenario=scenario,
        target_up_ratio=target_up_ratio,
    )
    decision.update(hedge_floor_decision)
    blockers.extend(hedge_floor_blockers)
    shadow_decision_mode = str(spec.metadata.get("shadow_decision_mode") or "").strip().lower()
    if shadow_decision_mode:
        decision["shadow_decision_mode"] = shadow_decision_mode
    if shadow_decision_mode == "low_range_no_edge_control":
        path = context.get("option_path") if isinstance(context.get("option_path"), dict) else {}
        avg_range = _optional_float(path.get("avg_rolling_60s_range"))
        entry_price_for_edge = _optional_float(scenario.limit_price)
        forward_best_bid = _optional_float(context.get("forward_best_bid"))
        forward_edge = None if entry_price_for_edge is None or forward_best_bid is None else forward_best_bid - entry_price_for_edge
        max_avg_range = _optional_float(spec.metadata.get("low_range_control_max_avg_rolling_60s_range") or 0.03)
        max_forward_edge = _optional_float(spec.metadata.get("low_range_control_max_forward_cashout_edge") or 0.01)
        decision["shadow_decision_mode"] = shadow_decision_mode
        decision["diagnostic_control_lane"] = True
        decision["no_live_promotion"] = True
        decision["low_range_control"] = {
            "avg_rolling_60s_range": avg_range,
            "max_avg_rolling_60s_range": max_avg_range,
            "forward_best_bid": forward_best_bid,
            "forward_cashout_edge": forward_edge,
            "max_forward_cashout_edge": max_forward_edge,
        }
        if avg_range is None:
            blockers.append("low_range_no_edge_control_range_missing")
        elif max_avg_range is not None and avg_range > max_avg_range:
            blockers.append("low_range_no_edge_control_range_above_baseline")
        if forward_edge is None:
            blockers.append("low_range_no_edge_control_forward_edge_missing")
        elif max_forward_edge is not None and forward_edge > max_forward_edge:
            blockers.append("low_range_no_edge_control_forward_edge_present")
        blockers.append("diagnostic_no_live_promotion_control_lane")

    if profile_sources:
        profile_requirement_blockers: tuple[str, ...] = ()
        if not context.get("profile_distribution_ready"):
            if profile_confidence_only:
                decision["profile_dependency_mode"] = profile_dependency_mode
                decision["profile_confidence_status"] = "missing"
            else:
                blockers.append("profile_distribution_context_missing")
        else:
            profile_requirement_decision, profile_requirement_blockers = _profile_distribution_requirements(spec, context)
            decision.update(profile_requirement_decision)
            if profile_requirement_blockers and profile_confidence_only:
                decision["profile_dependency_mode"] = profile_dependency_mode
                decision["profile_confidence_status"] = "requirements_not_met"
                decision["profile_confidence_blockers"] = list(profile_requirement_blockers)
            else:
                blockers.extend(profile_requirement_blockers)
        if context.get("profile_distribution_ready") and not profile_requirement_blockers and profile_ratio_blockers:
            if profile_confidence_only:
                decision["profile_dependency_mode"] = profile_dependency_mode
                decision["profile_confidence_status"] = "ratio_not_available"
                decision["profile_confidence_blockers"] = list(profile_ratio_blockers)
            else:
                blockers.extend(profile_ratio_blockers)
        elif context.get("profile_distribution_ready") and not profile_requirement_blockers and target_up_ratio is not None:
            desired_up: bool | None
            threshold = float(spec.metadata.get("profile_direction_threshold") or 0.08)
            upper_bound = 0.5 + threshold
            lower_bound = 0.5 - threshold
            profile_pressure_mode = str(spec.metadata.get("profile_pressure_mode") or "directional").strip().lower()
            if profile_pressure_mode == "balanced_required":
                desired_up = None
                pressure_balanced = lower_bound < target_up_ratio < upper_bound
                decision["profile_target_up_ratio"] = round(float(target_up_ratio), 8)
                decision["profile_ratio_source"] = profile_ratio_source
                decision["profile_direction_threshold"] = round(threshold, 8)
                decision["profile_pressure_mode"] = profile_pressure_mode
                decision["profile_target_side"] = "balanced" if pressure_balanced else "unbalanced"
                decision["profile_dependency_mode"] = profile_dependency_mode
                decision["profile_confidence_status"] = "balanced" if pressure_balanced else "unbalanced"
                decision["profile_confidence_multiplier"] = 1.05 if pressure_balanced else 0.85
                if not pressure_balanced and not profile_confidence_only:
                    blockers.append("profile_distribution_pressure_not_balanced")
                desired_up = None
            elif target_up_ratio >= upper_bound:
                desired_up = True
            elif target_up_ratio <= lower_bound:
                desired_up = False
            else:
                desired_up = None
                if spec.metadata.get("profile_distribution_allow_balanced_live_entry"):
                    decision["profile_distribution_balanced_live_entry_allowed"] = True
                elif not profile_confidence_only:
                    blockers.append("profile_distribution_pressure_too_balanced")
            if profile_pressure_mode != "balanced_required":
                profile_direction_mode = str(spec.metadata.get("profile_direction_mode") or "follow")
                if desired_up is not None and profile_direction_mode == "contrarian":
                    desired_up = not desired_up
                profile_aligned = None if desired_up is None else desired_up == outcome_is_up
                decision["profile_target_up_ratio"] = round(float(target_up_ratio), 8)
                decision["profile_ratio_source"] = profile_ratio_source
                decision["profile_direction_threshold"] = round(threshold, 8)
                decision["profile_direction_mode"] = profile_direction_mode
                decision["profile_target_side"] = "Up" if desired_up is True else "Down" if desired_up is False else "balanced"
                decision["profile_dependency_mode"] = profile_dependency_mode
                decision["profile_confidence_status"] = (
                    "aligned"
                    if profile_aligned is True
                    else "opposed"
                    if profile_aligned is False
                    else "balanced"
                )
                decision["profile_confidence_multiplier"] = (
                    1.12
                    if profile_aligned is True
                    else 0.88
                    if profile_aligned is False
                    else 1.0
                )
                if desired_up is not None and desired_up != outcome_is_up and not profile_confidence_only:
                    blockers.append("profile_distribution_side_mismatch")

    if strategy_id.startswith("crypto_direction") or (
        indicator_sources and not profile_sources and family == "outcome_prediction"
    ):
        desired_up = _crypto_observer_prefers_up(context)
        crypto_direction_mode = str(spec.metadata.get("crypto_direction_mode") or "follow")
        if desired_up is not None and crypto_direction_mode == "contrarian":
            desired_up = not desired_up
        decision["crypto_direction_mode"] = crypto_direction_mode
        decision["crypto_target_side"] = "Up" if desired_up is True else "Down" if desired_up is False else "neutral_or_unknown"
        if desired_up is None:
            blockers.append("crypto_observer_direction_missing")
        elif desired_up != outcome_is_up:
            blockers.append("crypto_direction_side_mismatch")

    adjusted_limit_price = float(scenario.limit_price)
    adjusted_shares = float(scenario.shares)
    if "grid" in strategy_id or "rebound" in strategy_id:
        enforce_grid_price_band = bool(spec.metadata.get("grid_price_band_required", True))
        if enforce_grid_price_band and (adjusted_limit_price < 0.10 or adjusted_limit_price > 0.90):
            blockers.append("grid_price_band_not_reached")
        if spread > 0.04:
            blockers.append("grid_spread_too_wide")
        if liquidity < 5.0:
            blockers.append("grid_liquidity_too_thin")
        adjusted_limit_price = max(0.01, min(adjusted_limit_price, (best_bid or adjusted_limit_price) + 0.01))
        adjusted_shares = 1.4
    elif "scalping" in family or "scalping" in strategy_id:
        if spread > 0.03:
            blockers.append("scalp_spread_too_wide")
        if liquidity < 10.0:
            blockers.append("scalp_liquidity_too_thin")
        adjusted_limit_price = max(0.01, min(adjusted_limit_price, (best_bid or adjusted_limit_price) + 0.01))
        adjusted_shares = 1.2
    elif "hedger" in strategy_id or "hedge" in family:
        ratio_distance = abs((target_up_ratio if target_up_ratio is not None else 0.5) - 0.5)
        adjusted_shares = round(0.6 + min(0.8, ratio_distance * 2.0), 8)
    elif "cashout" in family:
        adjusted_shares = 0.8
    elif "event_context" in strategy_id:
        adjusted_shares = 0.7

    target_notional = _optional_float(spec.metadata.get("live_target_order_notional_usd"))
    if target_notional is not None and target_notional > 0:
        adjusted_shares = max(adjusted_shares, target_notional / max(adjusted_limit_price, 0.01))
    min_live_shares = _optional_float(spec.metadata.get("live_min_order_shares"))
    if min_live_shares is not None and min_live_shares > 0:
        adjusted_shares = max(adjusted_shares, min_live_shares)
    policy = spec.metadata.get("promotion_policy") if isinstance(spec.metadata.get("promotion_policy"), dict) else {}
    budget_cap = _optional_float(spec.metadata.get("live_budget_cap_usd") or policy.get("live_budget_cap_usd"))
    if budget_cap is not None and budget_cap > 0:
        adjusted_shares = min(adjusted_shares, budget_cap / max(adjusted_limit_price, 0.01))

    for key in (
        "shadow_economics_min_liquidation_pnl_usd",
        "shadow_economics_max_spread_drag_usd",
    ):
        value = _optional_float(spec.metadata.get(key))
        if value is not None:
            decision[key] = round(value, 8)
    if bool(spec.metadata.get("shadow_economics_require_liquidation_non_negative")):
        decision["shadow_economics_require_liquidation_non_negative"] = True

    adjusted_context = dict(context)
    adjusted_context["strategy_shadow_decision"] = {
        **decision,
        "adjusted_limit_price": round(adjusted_limit_price, 8),
        "adjusted_shares": round(adjusted_shares, 8),
        "blockers": list(blockers),
    }
    adjusted = RuntimeScenario(
        event_key=scenario.event_key,
        event_token_key=scenario.event_token_key,
        token_id=scenario.token_id,
        event_slug=scenario.event_slug,
        outcome=scenario.outcome,
        side=scenario.side,
        shares=adjusted_shares,
        limit_price=adjusted_limit_price,
        quote_age_seconds=scenario.quote_age_seconds,
        signal_age_seconds=scenario.signal_age_seconds,
        profile_age_seconds=scenario.profile_age_seconds,
        spread=scenario.spread,
        expected_slippage=scenario.expected_slippage,
        liquidity_depth=scenario.liquidity_depth,
        time_remaining_seconds=scenario.time_remaining_seconds,
        live_market_verified=scenario.live_market_verified,
        signal_context=adjusted_context,
    )
    return adjusted, adjusted_context["strategy_shadow_decision"], tuple(blockers)


def _profile_distribution_requirements(spec: StrategySpec, context: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    relevant_metadata_keys = {
        "profile_distribution_max_source_age_seconds",
        "profile_distribution_min_profile_count",
        "profile_distribution_max_cost_share_gap",
        "profile_distribution_max_cost_count_gap",
        "profile_distribution_blocked_coverage_warnings",
        "profile_distribution_max_top_profile_cost_share",
    }
    if not any(key in spec.metadata for key in relevant_metadata_keys):
        return {}, ()

    profile = context.get("profile_distribution") if isinstance(context.get("profile_distribution"), dict) else {}
    decision: dict[str, Any] = {
        "profile_distribution_ready": bool(context.get("profile_distribution_ready")),
    }
    blockers: list[str] = []

    max_source_age = _optional_float(spec.metadata.get("profile_distribution_max_source_age_seconds"))
    if max_source_age is not None:
        source_age = _optional_float(profile.get("source_age_seconds"))
        decision["profile_source_age_seconds"] = source_age
        decision["profile_max_source_age_seconds"] = max_source_age
        if source_age is None or source_age > max_source_age:
            blockers.append("profile_distribution_source_age_high")

    min_profile_count = _optional_float(spec.metadata.get("profile_distribution_min_profile_count"))
    if min_profile_count is not None:
        profile_count = _optional_float(profile.get("profile_count"))
        decision["profile_count"] = profile_count
        decision["required_profile_count"] = min_profile_count
        if profile_count is None or profile_count < min_profile_count:
            blockers.append("profile_distribution_profile_count_low")

    max_cost_share_gap = _optional_float(spec.metadata.get("profile_distribution_max_cost_share_gap"))
    if max_cost_share_gap is not None:
        gap = _optional_float(profile.get("cost_vs_shares_up_gap_abs"))
        decision["cost_vs_shares_up_gap_abs"] = gap
        decision["max_cost_share_gap"] = max_cost_share_gap
        if gap is None or gap > max_cost_share_gap:
            blockers.append("profile_distribution_cost_share_divergence_high")

    max_cost_count_gap = _optional_float(spec.metadata.get("profile_distribution_max_cost_count_gap"))
    if max_cost_count_gap is not None:
        gap = _optional_float(profile.get("cost_vs_profile_count_up_gap_abs"))
        decision["cost_vs_profile_count_up_gap_abs"] = gap
        decision["max_cost_count_gap"] = max_cost_count_gap
        if gap is None or gap > max_cost_count_gap:
            blockers.append("profile_distribution_cost_count_divergence_high")

    blocked_warnings = {
        str(item).strip()
        for item in (spec.metadata.get("profile_distribution_blocked_coverage_warnings") or [])
        if str(item).strip()
    }
    if blocked_warnings:
        warnings = {
            str(item).strip()
            for item in (profile.get("coverage_warnings") or [])
            if str(item).strip()
        }
        decision["profile_coverage_warnings"] = sorted(warnings)
        decision["blocked_profile_coverage_warnings"] = sorted(blocked_warnings)
        for warning in sorted(warnings & blocked_warnings):
            blockers.append(f"profile_distribution_coverage_warning:{warning}")

    max_top_profile_cost_share = _optional_float(spec.metadata.get("profile_distribution_max_top_profile_cost_share"))
    if max_top_profile_cost_share is not None:
        top_cost_share = _optional_float(profile.get("top_profile_cost_share"))
        decision["top_profile_cost_share"] = top_cost_share
        decision["max_top_profile_cost_share"] = max_top_profile_cost_share
        if top_cost_share is None or top_cost_share > max_top_profile_cost_share:
            blockers.append("profile_distribution_top_profile_concentration_high")

    return decision, tuple(blockers)


def _profile_target_ratio(spec: StrategySpec, context: dict[str, Any]) -> tuple[float | None, str, tuple[str, ...]]:
    group_kind = str(spec.metadata.get("profile_distribution_group_kind") or "").strip()
    group_label = str(spec.metadata.get("profile_distribution_group_label") or "").strip()
    if not group_kind or not group_label:
        return _optional_float(context.get("target_up_ratio")), "aggregate_cost_weighted", ()
    distribution = context.get("profile_distribution") if isinstance(context.get("profile_distribution"), dict) else {}
    breakdown = distribution.get("component_breakdown") if isinstance(distribution.get("component_breakdown"), dict) else {}
    group = breakdown.get(group_kind) if isinstance(breakdown.get(group_kind), dict) else {}
    row = group.get(group_label) if isinstance(group.get(group_label), dict) else None
    if not isinstance(row, dict):
        if bool(spec.metadata.get("profile_distribution_group_fallback_to_aggregate")):
            return _optional_float(context.get("target_up_ratio")), "aggregate_cost_weighted_fallback", ()
        return None, f"{group_kind}:{group_label}", (f"profile_distribution_group_missing:{group_kind}:{group_label}",)
    min_components = int(spec.metadata.get("profile_distribution_min_components") or 1)
    if int(row.get("component_count") or 0) < min_components:
        return None, f"{group_kind}:{group_label}", (f"profile_distribution_group_thin:{group_kind}:{group_label}",)
    pair_sum = _optional_float(row.get("reconstructed_profile_pair_sum"))
    pair_min = _optional_float(spec.metadata.get("profile_reconstructed_pair_sum_min"))
    pair_max = _optional_float(spec.metadata.get("profile_reconstructed_pair_sum_max"))
    if pair_min is not None and (pair_sum is None or pair_sum < pair_min):
        return None, f"{group_kind}:{group_label}", (f"profile_distribution_pair_sum_low:{group_kind}:{group_label}",)
    if pair_max is not None and (pair_sum is None or pair_sum > pair_max):
        return None, f"{group_kind}:{group_label}", (f"profile_distribution_pair_sum_high:{group_kind}:{group_label}",)
    ratio = _optional_float(row.get("up_pressure_ratio"))
    if ratio is None:
        return None, f"{group_kind}:{group_label}", (f"profile_distribution_group_ratio_missing:{group_kind}:{group_label}",)
    return ratio, f"{group_kind}:{group_label}", ()


def _option_path_requirements(
    spec: StrategySpec,
    context: dict[str, Any],
    scenario: RuntimeScenario,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    relevant_metadata_keys = {
        "option_path_required",
        "option_path_min_snapshot_count",
        "option_path_min_level_crossing_count",
        "option_path_min_rebound_direction_flip_count",
        "option_path_min_strong_rebound_touch_count",
        "option_path_min_avg_rolling_30s_range",
        "option_path_min_avg_rolling_60s_range",
        "option_path_min_max_rolling_60s_range",
        "option_path_min_near_50c_sample_count",
        "option_path_min_extreme_sample_count",
        "option_path_min_trade_print_count",
        "option_path_min_abs_pair_depth_pressure",
        "option_path_max_pair_sum_range",
        "option_path_max_source_latency_ms",
        "option_path_max_spread",
        "option_path_min_depth_top3_ask_size",
        "option_path_min_forward_cashout_edge",
        "option_entry_price_min",
        "option_entry_price_max",
    }
    if not any(key in spec.metadata for key in relevant_metadata_keys):
        return {}, ()

    blockers: list[str] = []
    path = context.get("option_path") if isinstance(context.get("option_path"), dict) else {}
    decision: dict[str, Any] = {
        "option_path_required": True,
        "option_path_ready": bool(context.get("option_path_ready")),
    }
    if not context.get("option_path_ready"):
        blockers.append("option_path_context_missing")
        return decision, tuple(blockers)

    checks = (
        ("option_path_min_snapshot_count", "snapshot_count", "option_path_snapshot_count_low"),
        ("option_path_min_level_crossing_count", "level_crossing_count", "option_path_level_crossings_low"),
        (
            "option_path_min_rebound_direction_flip_count",
            "rebound_direction_flip_count",
            "option_path_rebound_flips_low",
        ),
        (
            "option_path_min_strong_rebound_touch_count",
            "strong_rebound_touch_count",
            "option_path_strong_rebound_touches_low",
        ),
        ("option_path_min_near_50c_sample_count", "near_50c_sample_count", "option_path_near_50c_samples_low"),
        ("option_path_min_extreme_sample_count", "extreme_sample_count", "option_path_extreme_samples_low"),
        ("option_path_min_trade_print_count", "trade_print_count", "option_path_trade_print_count_low"),
    )
    for metadata_key, path_key, blocker in checks:
        required = _optional_float(spec.metadata.get(metadata_key))
        if required is None:
            continue
        observed = _optional_float(path.get(path_key))
        decision[path_key] = observed
        decision[f"required_{path_key}"] = required
        if observed is None or observed < required:
            blockers.append(blocker)

    range_checks = (
        ("option_path_min_avg_rolling_30s_range", "avg_rolling_30s_range", "option_path_avg_rolling_30s_range_low"),
        ("option_path_min_avg_rolling_60s_range", "avg_rolling_60s_range", "option_path_avg_rolling_60s_range_low"),
        ("option_path_min_max_rolling_60s_range", "max_rolling_60s_range", "option_path_max_rolling_60s_range_low"),
    )
    for metadata_key, path_key, blocker in range_checks:
        required = _optional_float(spec.metadata.get(metadata_key))
        if required is None:
            continue
        observed = _optional_float(path.get(path_key))
        decision[path_key] = observed
        decision[f"required_{path_key}"] = required
        if observed is None or observed < required:
            blockers.append(blocker)

    max_pair_sum_range = _optional_float(spec.metadata.get("option_path_max_pair_sum_range"))
    if max_pair_sum_range is not None:
        observed_pair_sum_range = _optional_float(path.get("pair_sum_range"))
        decision["pair_sum_range"] = observed_pair_sum_range
        decision["max_pair_sum_range"] = max_pair_sum_range
        if observed_pair_sum_range is None or observed_pair_sum_range > max_pair_sum_range:
            blockers.append("option_path_pair_sum_range_high")

    min_abs_pair_depth_pressure = _optional_float(spec.metadata.get("option_path_min_abs_pair_depth_pressure"))
    if min_abs_pair_depth_pressure is not None:
        observed_pressure = _optional_float(path.get("avg_pair_depth_pressure"))
        decision["avg_pair_depth_pressure"] = observed_pressure
        decision["required_abs_pair_depth_pressure"] = min_abs_pair_depth_pressure
        if observed_pressure is None or abs(observed_pressure) < min_abs_pair_depth_pressure:
            blockers.append("option_path_pair_depth_pressure_weak")

    max_latency = _optional_float(spec.metadata.get("option_path_max_source_latency_ms"))
    if max_latency is not None:
        observed_latency = _optional_float(path.get("max_source_latency_ms"))
        decision["max_source_latency_ms"] = observed_latency
        decision["required_max_source_latency_ms"] = max_latency
        if observed_latency is None or observed_latency > max_latency:
            blockers.append("option_path_source_latency_high")

    max_spread = _optional_float(spec.metadata.get("option_path_max_spread"))
    if max_spread is not None:
        observed_spread = _optional_float(scenario.spread)
        decision["spread"] = observed_spread
        decision["max_spread"] = max_spread
        if observed_spread is None or observed_spread > max_spread:
            blockers.append("option_spread_too_wide")

    min_depth = _optional_float(spec.metadata.get("option_path_min_depth_top3_ask_size"))
    if min_depth is not None:
        observed_depth = _optional_float(scenario.liquidity_depth)
        decision["depth_top3_ask_size"] = observed_depth
        decision["required_depth_top3_ask_size"] = min_depth
        if observed_depth is None or observed_depth < min_depth:
            blockers.append("option_depth_top3_ask_size_low")

    min_forward_cashout_edge = _optional_float(spec.metadata.get("option_path_min_forward_cashout_edge"))
    if min_forward_cashout_edge is not None:
        entry_price_for_edge = _optional_float(scenario.limit_price)
        forward_best_bid = _optional_float(context.get("forward_best_bid"))
        forward_edge = None if entry_price_for_edge is None or forward_best_bid is None else forward_best_bid - entry_price_for_edge
        decision["forward_best_bid"] = forward_best_bid
        decision["forward_cashout_edge"] = forward_edge
        decision["required_forward_cashout_edge"] = min_forward_cashout_edge
        if forward_edge is None and spec.metadata.get("option_path_allow_missing_forward_cashout_edge"):
            decision["forward_cashout_edge_missing_allowed"] = True
        elif forward_edge is None or forward_edge < min_forward_cashout_edge:
            blockers.append("option_forward_cashout_edge_low")

    entry_min = _optional_float(spec.metadata.get("option_entry_price_min"))
    entry_max = _optional_float(spec.metadata.get("option_entry_price_max"))
    entry_price = _optional_float(scenario.limit_price)
    if entry_min is not None:
        decision["entry_price"] = entry_price
        decision["entry_price_min"] = entry_min
        if entry_price is None or entry_price < entry_min:
            blockers.append("option_entry_price_below_band")
    if entry_max is not None:
        decision["entry_price"] = entry_price
        decision["entry_price_max"] = entry_max
        if entry_price is None or entry_price > entry_max:
            blockers.append("option_entry_price_above_band")

    return decision, tuple(blockers)


def _hedge_floor_requirements(
    spec: StrategySpec,
    *,
    context: dict[str, Any],
    scenario: RuntimeScenario,
    target_up_ratio: float | None,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    if not spec.metadata.get("hedge_floor_mode"):
        return {}, ()
    blockers: list[str] = []
    path = context.get("option_path") if isinstance(context.get("option_path"), dict) else {}
    if not context.get("option_path_ready"):
        return {"hedge_floor_mode": spec.metadata.get("hedge_floor_mode"), "hedge_floor_ready": False}, ("option_path_context_missing",)
    viability = grid_viability(path, spread=scenario.spread, liquidity_depth=scenario.liquidity_depth)
    if viability.inversion_intensity < _metadata_float(spec.metadata, "hedge_floor_min_inversion_intensity", 0.5):
        blockers.append("hedge_floor_inversion_intensity_low")
    if viability.viability_score < _metadata_float(spec.metadata, "hedge_floor_min_grid_viability", 0.5):
        blockers.append("hedge_floor_grid_viability_low")

    current_ask = _optional_float(context.get("best_ask"))
    if current_ask is None:
        current_ask = float(scenario.limit_price)
    outcome_is_up = str(scenario.outcome or "").strip().lower() == "up"
    up_price = _optional_float(context.get("paired_entry_up_ask"))
    down_price = _optional_float(context.get("paired_entry_down_ask"))
    if up_price is None and outcome_is_up:
        up_price = current_ask
    if down_price is None and not outcome_is_up:
        down_price = current_ask
    paired_opposite_ask = _optional_float(context.get("paired_opposite_ask"))
    if paired_opposite_ask is None:
        paired_opposite_ask = _optional_float(context.get("paired_down_ask"))
    if down_price is None and outcome_is_up:
        down_price = paired_opposite_ask
    if up_price is None and not outcome_is_up:
        up_price = paired_opposite_ask
    profile_distribution_context_for_prices = (
        context.get("profile_distribution") if isinstance(context.get("profile_distribution"), dict) else {}
    )
    if up_price is None:
        up_price = _optional_float(profile_distribution_context_for_prices.get("up_reconstructed_profile_price"))
    if down_price is None:
        down_price = _optional_float(profile_distribution_context_for_prices.get("down_reconstructed_profile_price"))
    pair_sum_hint = _optional_float(context.get("pair_sum_hint"))
    if pair_sum_hint is None:
        pair_sum_hint = _optional_float(path.get("pair_sum_hint"))
    if pair_sum_hint is None and up_price is not None and down_price is not None:
        pair_sum_hint = up_price + down_price
    if pair_sum_hint is None:
        pair_sum_hint = 1.0
    if up_price is None and down_price is not None:
        up_price = max(0.01, min(0.99, pair_sum_hint - down_price))
    if up_price is None:
        up_price = current_ask
    if down_price is None:
        down_price = max(0.01, min(0.99, pair_sum_hint - up_price))
    seed_budget = max(0.5, _optional_float(spec.metadata.get("hedge_floor_seed_budget_usd") or 2.0))
    per_side_budget = seed_budget / 2.0
    realized_cash = _optional_float(context.get("realized_cash"))
    if realized_cash is None:
        realized_cash = -seed_budget + (_optional_float(context.get("realized_profit_offset")) or 0.0)
    up_shares = _optional_float(context.get("inventory_up_shares"))
    if up_shares is None:
        up_shares = per_side_budget / max(up_price, 0.01)
    down_shares = _optional_float(context.get("inventory_down_shares"))
    if down_shares is None:
        down_shares = per_side_budget / max(down_price, 0.01)
    protected_floor = _optional_float(context.get("protected_floor"))
    seed_allocation_mode = str(spec.metadata.get("hedge_floor_seed_allocation_mode") or "").strip().lower()
    paired_seed_required = bool(spec.metadata.get("hedge_floor_paired_seed_required"))
    paired_seed = None
    if seed_allocation_mode == "equal_shares":
        paired_seed = paired_seed_entry_projection(
            up_price=up_price,
            down_price=down_price,
            budget_usd=seed_budget,
            protected_floor=protected_floor,
        )
        realized_cash = paired_seed.realized_cash
        up_shares = paired_seed.equal_shares
        down_shares = paired_seed.equal_shares
        state = paired_seed.state
    else:
        state = hedge_floor_state(
            realized_cash=realized_cash,
            up_shares=up_shares,
            down_shares=down_shares,
            protected_floor=protected_floor,
        )
    if paired_seed_required and paired_seed is None:
        blockers.append("paired_seed_projection_missing")
    if paired_seed is not None:
        max_pair_sum = _optional_float(spec.metadata.get("hedge_floor_paired_seed_max_pair_sum"))
        min_seed_floor = _optional_float(spec.metadata.get("hedge_floor_paired_seed_min_floor"))
        if max_pair_sum is not None and paired_seed.pair_sum > max_pair_sum + 1e-9:
            blockers.append("paired_seed_pair_sum_high")
        if min_seed_floor is not None and paired_seed.guaranteed_floor < min_seed_floor - 1e-9:
            blockers.append("paired_seed_floor_too_low")
        ask_gap = abs(paired_seed.up_price - paired_seed.down_price)
        min_abs_gap = _optional_float(spec.metadata.get("hedge_floor_paired_seed_min_abs_entry_ask_gap"))
        max_abs_gap = _optional_float(spec.metadata.get("hedge_floor_paired_seed_max_abs_entry_ask_gap"))
        if min_abs_gap is not None and ask_gap < min_abs_gap - 1e-9:
            blockers.append("paired_seed_entry_ask_gap_low")
        if max_abs_gap is not None and ask_gap > max_abs_gap + 1e-9:
            blockers.append("paired_seed_entry_ask_gap_high")
    if state.guaranteed_floor < _metadata_float(spec.metadata, "hedge_floor_min_current_floor", -0.25):
        blockers.append("hedge_floor_current_floor_too_low")

    order_side_mode = str(spec.metadata.get("hedge_floor_order_side_mode") or "profile_ratio").strip().lower()
    if order_side_mode == "scenario_outcome":
        outcome_is_up = str(scenario.outcome or "").strip().lower() == "up"
        buy_side = "up" if outcome_is_up else "down"
    else:
        ratio = 0.5 if target_up_ratio is None else float(target_up_ratio)
        buy_side = "up" if ratio >= 0.5 else "down"
    if paired_seed is not None:
        order_allowed = paired_seed.guaranteed_floor >= state.protected_floor - 1e-9
    else:
        order_allowed, _floor_change = floor_preserving_order_gate(
            state,
            side=buy_side,
            price=up_price if buy_side == "up" else down_price,
            shares=float(scenario.shares),
        )
    if state.guaranteed_floor > 0.0 and not order_allowed:
        blockers.append("hedge_floor_order_not_floor_preserving")

    tail_budget = surplus_tail_budget(state)
    require_surplus = bool(spec.metadata.get("hedge_floor_require_surplus_for_tails"))
    tail_mode = str(spec.metadata.get("hedge_floor_tail_mode") or "")
    tail_orders_allowed = not (require_surplus and tail_mode == "protected_only" and tail_budget <= 0.0)

    touched_tail_cents = int(spec.metadata.get("hedge_floor_tail_touch_cents") or 5)
    profile_distribution_context = (
        context.get("profile_distribution") if isinstance(context.get("profile_distribution"), dict) else None
    )
    crypto_observer_context = context.get("crypto_observer") if isinstance(context.get("crypto_observer"), dict) else None
    profile_bucket = profile_tail_context_bucket(profile_distribution_context)
    profile_price_bucket = profile_tail_price_context_bucket(profile_distribution_context)
    crypto_bucket = crypto_tail_context_bucket(crypto_observer_context)
    crypto_distance_bucket = crypto_tail_distance_bucket(crypto_observer_context)
    tail_probability = tail_reversal_probability(
        touch_price_cents=touched_tail_cents,
        time_remaining_seconds=scenario.time_remaining_seconds,
        inversion_score=viability.inversion_intensity,
        volatility_score=min(1.0, (_optional_float(path.get("max_rolling_60s_range")) or 0.0) / 0.12),
        spread=scenario.spread,
        liquidity_depth=scenario.liquidity_depth,
        depth_pressure_score=_optional_float(path.get("avg_pair_depth_pressure")),
        crypto_context_bucket=crypto_bucket,
        crypto_distance_bucket=crypto_distance_bucket,
        profile_context_bucket=profile_bucket,
        profile_price_context_bucket=profile_price_bucket,
        tables=path.get("tail_comeback_table") if isinstance(path.get("tail_comeback_table"), dict) else None,
    )
    if tail_probability < _metadata_float(spec.metadata, "hedge_floor_min_tail_reversal_probability", 0.2):
        blockers.append("hedge_floor_tail_reversal_probability_low")

    if paired_seed is not None:
        improvement_payload = {
            "guaranteed_floor_after": round(state.guaranteed_floor, 8),
            "weaker_side_improved": True,
            "preserves_floor": True,
        }
    else:
        improvement = protected_floor_improvement(
            state,
            side=buy_side,
            price=up_price if buy_side == "up" else down_price,
            shares=float(scenario.shares),
        )
        improvement_payload = {
            "guaranteed_floor_after": round(improvement.guaranteed_floor_after, 8),
            "weaker_side_improved": improvement.weaker_side_improved,
            "preserves_floor": improvement.preserves_floor,
        }
    decision = {
        "hedge_floor_mode": spec.metadata.get("hedge_floor_mode"),
        "hedge_floor_seed_allocation_mode": seed_allocation_mode or None,
        "hedge_floor_order_side_mode": order_side_mode,
        "hedge_floor_ready": True,
        "hedge_floor_state": {
            "realized_cash": round(state.realized_cash, 8),
            "up_shares": round(state.up_shares, 8),
            "down_shares": round(state.down_shares, 8),
            "payout_if_up": round(state.payout_if_up, 8),
            "payout_if_down": round(state.payout_if_down, 8),
            "guaranteed_floor": round(state.guaranteed_floor, 8),
            "protected_floor": round(state.protected_floor, 8),
            "surplus_above_floor": round(state.surplus_above_floor, 8),
            "weaker_side": state.weaker_side,
        },
        "inversion_intensity": round(viability.inversion_intensity, 8),
        "grid_viability": round(viability.viability_score, 8),
        "grid_recommended_spacing": round(viability.recommended_spacing, 8),
        "grid_rebound_support": round(viability.rebound_support, 8),
        "tail_reversal_probability": round(tail_probability, 8),
        "tail_reversal_context": {
            "crypto_context_bucket": crypto_bucket,
            "crypto_distance_bucket": crypto_distance_bucket,
            "profile_context_bucket": profile_bucket,
            "profile_price_context_bucket": profile_price_bucket,
            "profile_price_crypto_context_bucket": (
                f"{profile_price_bucket}||{crypto_distance_bucket}"
                if profile_price_bucket and crypto_distance_bucket
                else None
            ),
            "profile_price_execution_context_bucket": (
                f"{profile_price_bucket}||"
                f"{'|'.join([_spread_bucket(float(scenario.spread)), _liquidity_bucket(float(scenario.liquidity_depth)), _slippage_bucket(max(0.0, float(scenario.spread)) / max(float(scenario.liquidity_depth), 1.0))])}"
                if profile_price_bucket
                else None
            ),
            "depth_pressure_score": _optional_float(path.get("avg_pair_depth_pressure")),
            "spread": round(float(scenario.spread), 8),
            "liquidity_depth": round(float(scenario.liquidity_depth), 8),
        },
        "tail_orders_allowed": tail_orders_allowed,
        "surplus_tail_budget": round(tail_budget, 8),
        "floor_preserving_order_gate": order_allowed,
        "protected_floor_improvement": improvement_payload,
    }
    if bool(spec.metadata.get("paired_seed_scalp_simulation_required")):
        decision["paired_seed_scalp_simulation_required"] = True
        decision["paired_seed_scalp_buy_drop"] = _metadata_float(spec.metadata, "paired_seed_scalp_buy_drop", 0.03)
        decision["paired_seed_scalp_target"] = _metadata_float(spec.metadata, "paired_seed_scalp_target", 0.03)
        decision["paired_seed_scalp_notional_usd"] = _metadata_float(
            spec.metadata,
            "paired_seed_scalp_notional_usd",
            0.25,
        )
        decision["paired_seed_scalp_max_open_per_side"] = int(
            _metadata_float(spec.metadata, "paired_seed_scalp_max_open_per_side", 1)
        )
        if "paired_seed_scalp_min_path_snapshots" in spec.metadata:
            min_path_snapshots = int(
                _metadata_float(spec.metadata, "paired_seed_scalp_min_path_snapshots", 2)
            )
            decision["paired_seed_scalp_min_path_snapshots"] = min_path_snapshots
            path_snapshot_count = _optional_float(context.get("paired_path_snapshot_count"))
            if path_snapshot_count is None:
                blockers.append("paired_scalp_path_density_missing")
            elif int(path_snapshot_count) < min_path_snapshots:
                blockers.append("paired_scalp_path_density_low")
        if "paired_seed_scalp_entry_window_fraction" in spec.metadata:
            entry_window_fraction = _metadata_float(
                spec.metadata,
                "paired_seed_scalp_entry_window_fraction",
                1.0,
            )
            decision["paired_seed_scalp_entry_window_fraction"] = max(
                0.0,
                min(1.0, float(entry_window_fraction)),
            )
        if bool(spec.metadata.get("paired_seed_scalp_preflight_required")):
            preflight_payload, preflight_blockers = _paired_seed_scalp_path_preflight(
                spec,
                context=context,
                state=state,
                up_entry_ask=up_price,
                down_entry_ask=down_price,
                paired_seed_cost_usd=None if paired_seed is None else paired_seed.cost_usd,
            )
            decision["paired_seed_scalp_preflight_required"] = True
            decision["paired_seed_scalp_preflight"] = preflight_payload
            blockers.extend(preflight_blockers)
    if paired_seed is not None:
        decision["paired_seed_entry"] = {
            "up_price": round(paired_seed.up_price, 8),
            "down_price": round(paired_seed.down_price, 8),
            "pair_sum": round(paired_seed.pair_sum, 8),
            "entry_ask_gap": round(abs(paired_seed.up_price - paired_seed.down_price), 8),
            "budget_usd": round(paired_seed.budget_usd, 8),
            "equal_shares": round(paired_seed.equal_shares, 8),
            "cost_usd": round(paired_seed.cost_usd, 8),
            "guaranteed_floor": round(paired_seed.guaranteed_floor, 8),
            "floor_margin_per_share": round(paired_seed.floor_margin_per_share, 8),
            "floor_margin_ratio": round(paired_seed.floor_margin_ratio, 8),
        }
    return decision, tuple(blockers)


def _paired_seed_scalp_path_preflight(
    spec: StrategySpec,
    *,
    context: dict[str, Any],
    state: Any,
    up_entry_ask: float | None,
    down_entry_ask: float | None,
    paired_seed_cost_usd: float | None,
) -> tuple[dict[str, Any], list[str]]:
    snapshots = context.get("paired_path_snapshots")
    if not isinstance(snapshots, list):
        snapshots = []

    buy_drop = max(0.0, _metadata_float(spec.metadata, "paired_seed_scalp_buy_drop", 0.03))
    scalp_target = max(0.0, _metadata_float(spec.metadata, "paired_seed_scalp_target", 0.03))
    scalp_notional = max(0.01, _metadata_float(spec.metadata, "paired_seed_scalp_notional_usd", 0.25))
    max_open_per_side = max(1, int(_metadata_float(spec.metadata, "paired_seed_scalp_max_open_per_side", 1)))
    min_path_snapshots = max(2, int(_metadata_float(spec.metadata, "paired_seed_scalp_min_path_snapshots", 2)))
    entry_window_fraction = max(
        0.0,
        min(1.0, _metadata_float(spec.metadata, "paired_seed_scalp_entry_window_fraction", 1.0)),
    )
    buy_cutoff_index = max(1, int(len(snapshots) * entry_window_fraction)) if snapshots else 0

    realized_cash = float(getattr(state, "realized_cash", 0.0))
    if paired_seed_cost_usd is not None:
        realized_cash = -float(paired_seed_cost_usd)
    side_shares = {
        "up": float(getattr(state, "up_shares", 0.0)),
        "down": float(getattr(state, "down_shares", 0.0)),
    }
    entry_ask = {"up": up_entry_ask, "down": down_entry_ask}
    open_positions: dict[str, list[dict[str, float]]] = {"up": [], "down": []}
    completed_cycles = 0
    completed_profit = 0.0
    min_floor = min(realized_cash + side_shares["up"], realized_cash + side_shares["down"])

    for snapshot_index, snapshot in enumerate(snapshots):
        if not isinstance(snapshot, dict):
            continue
        for side in ("up", "down"):
            bid = _optional_float(snapshot.get(f"{side}_bid"))
            if bid is None:
                continue
            retained: list[dict[str, float]] = []
            for position in open_positions[side]:
                buy_price = float(position["price"])
                if bid >= buy_price + scalp_target:
                    sold_shares = float(position["shares"])
                    realized_cash += sold_shares * bid
                    side_shares[side] -= sold_shares
                    completed_cycles += 1
                    completed_profit += sold_shares * (bid - buy_price)
                else:
                    retained.append(position)
            open_positions[side] = retained

        can_open_new_scalp = snapshot_index < buy_cutoff_index
        if can_open_new_scalp:
            for side in ("up", "down"):
                ask = _optional_float(snapshot.get(f"{side}_ask"))
                side_entry = entry_ask.get(side)
                if ask is None or side_entry is None:
                    continue
                if len(open_positions[side]) >= max_open_per_side:
                    continue
                if ask <= float(side_entry) - buy_drop:
                    bought_shares = scalp_notional / max(float(ask), 0.01)
                    realized_cash -= scalp_notional
                    side_shares[side] += bought_shares
                    open_positions[side].append({"price": float(ask), "shares": bought_shares})
        min_floor = min(min_floor, realized_cash + side_shares["up"], realized_cash + side_shares["down"])

    final_payout_if_up = realized_cash + side_shares["up"]
    final_payout_if_down = realized_cash + side_shares["down"]
    final_floor = min(final_payout_if_up, final_payout_if_down)
    open_count = sum(len(items) for items in open_positions.values())
    blockers: list[str] = []
    if len(snapshots) < min_path_snapshots:
        blockers.append("paired_scalp_preflight_path_density_low")
    if bool(spec.metadata.get("paired_seed_scalp_require_completed_cycle", True)) and completed_cycles <= 0:
        blockers.append("paired_scalp_preflight_no_completed_cycles")
    if bool(spec.metadata.get("paired_seed_scalp_require_floor_positive", True)) and final_floor <= 0.0:
        blockers.append("paired_scalp_preflight_floor_not_positive")
    if bool(spec.metadata.get("paired_seed_scalp_require_no_open_positions", True)) and open_count > 0:
        blockers.append("paired_scalp_preflight_open_positions_remain")
    min_floor_required = _optional_float(spec.metadata.get("paired_seed_scalp_min_floor_during_path"))
    if min_floor_required is not None and min_floor < min_floor_required:
        blockers.append("paired_scalp_preflight_min_floor_too_low")
    payload = {
        "snapshot_count": len(snapshots),
        "min_path_snapshots": min_path_snapshots,
        "buy_cutoff_index": buy_cutoff_index,
        "buy_drop": round(float(buy_drop), 8),
        "target": round(float(scalp_target), 8),
        "notional_usd": round(float(scalp_notional), 8),
        "completed_scalp_cycle_count": completed_cycles,
        "completed_scalp_profit_usd": round(float(completed_profit), 8),
        "open_scalp_position_count": open_count,
        "payout_if_up_after_scalp": round(float(final_payout_if_up), 8),
        "payout_if_down_after_scalp": round(float(final_payout_if_down), 8),
        "guaranteed_floor_pnl_usd": round(float(final_floor), 8),
        "min_floor_during_path_usd": round(float(min_floor), 8),
        "blockers": blockers,
    }
    return payload, blockers


def _crypto_observer_prefers_up(context: dict[str, Any]) -> bool | None:
    observer = context.get("crypto_observer") if isinstance(context.get("crypto_observer"), dict) else {}
    summaries = observer.get("summaries") if isinstance(observer.get("summaries"), list) else []
    scores = [
        score
        for summary in summaries
        for score in [_optional_float(summary.get("summary_score") if isinstance(summary, dict) else None)]
        if score is not None
    ]
    if scores:
        average = sum(scores) / len(scores)
        if average > 0.05:
            return True
        if average < -0.05:
            return False
    labels = " ".join(str(summary.get("summary_label") or "").lower() for summary in summaries if isinstance(summary, dict))
    if "buy" in labels and "sell" not in labels:
        return True
    if "sell" in labels and "buy" not in labels:
        return False
    return None


def _create_entry_intent(
    spec: StrategySpec,
    *,
    config: SupervisedRuntimeConfig,
    scenario: RuntimeScenario,
    candidate_key: str,
) -> ExecutionIntent:
    order_type = "limit_buy" if "limit_buy" in spec.allowed_order_types else "market_buy"
    return create_execution_intent(
        candidate_key=candidate_key,
        strategy_id=spec.strategy_id,
        run_id=config.run_id,
        event_key=scenario.event_key,
        event_token_key=scenario.event_token_key,
        side="BUY",
        intent_type="entry",
        order_type=order_type,
        shares=scenario.shares,
        limit_price=scenario.limit_price if order_type == "limit_buy" else None,
        decision_at_utc=_decision_at_for_scenario(scenario),
        source_attribution=tuple(spec.signal_inputs.get("profile", ())) + tuple(spec.signal_inputs.get("event", ())) + tuple(spec.signal_inputs.get("indicator", ())),
    )


def _live_paired_exit_required(*, config: SupervisedRuntimeConfig, position: PositionState | None) -> bool:
    return bool(_is_live_mode(config.mode) and position is not None)


def _create_paired_exit_intent(
    spec: StrategySpec,
    *,
    config: SupervisedRuntimeConfig,
    scenario: RuntimeScenario,
    candidate_key: str,
    position: PositionState,
    fill: FillState | None,
    entry_order: OrderState | None = None,
) -> ExecutionIntent:
    return create_execution_intent(
        candidate_key=candidate_key,
        strategy_id=spec.strategy_id,
        run_id=config.run_id,
        event_key=scenario.event_key,
        event_token_key=scenario.event_token_key,
        side="SELL",
        intent_type="paired_exit",
        order_type="limit_sell",
        shares=position.shares,
        limit_price=_paired_exit_limit_price(spec, scenario=scenario, position=position, fill=fill, entry_order=entry_order),
        decision_at_utc=_decision_at_for_scenario(scenario),
        source_attribution=tuple(spec.signal_inputs.get("profile", ())) + tuple(spec.signal_inputs.get("event", ())) + tuple(spec.signal_inputs.get("indicator", ())),
    )


def _paired_exit_limit_price(
    spec: StrategySpec,
    *,
    scenario: RuntimeScenario,
    position: PositionState | None = None,
    fill: FillState | None,
    entry_order: OrderState | None = None,
) -> float:
    entry_price = _paired_exit_entry_basis_price(scenario=scenario, position=position, fill=fill, entry_order=entry_order)
    target_profit_cents = _first_non_null_float(
        spec.metadata.get("paired_exit_profit_cents"),
        spec.metadata.get("live_exit_profit_cents"),
        spec.exit_rules.get("paired_exit_profit_cents"),
        spec.exit_rules.get("take_profit_cents"),
        1.0,
    )
    target_profit = max(0.0, float(target_profit_cents or 0.0) / 100.0)
    return round(min(0.99, max(entry_price, entry_price + target_profit)), 4)


def _paired_exit_entry_basis_price(
    *,
    scenario: RuntimeScenario,
    position: PositionState | None,
    fill: FillState | None,
    entry_order: OrderState | None,
) -> float:
    candidates: list[float] = []
    if fill is not None:
        _append_price_candidate(candidates, fill.fill_price)
    if position is not None and float(position.shares or 0.0) > 0:
        _append_price_candidate(candidates, float(position.cost_basis_usd) / float(position.shares))
    if entry_order is not None:
        source_payload = entry_order.source_payload if isinstance(entry_order.source_payload, dict) else {}
        response = source_payload.get("live_executor_response") if isinstance(source_payload.get("live_executor_response"), dict) else {}
        legacy = response.get("legacy_submission") if isinstance(response.get("legacy_submission"), dict) else {}
        order_request = response.get("order_request") if isinstance(response.get("order_request"), dict) else {}
        if not order_request:
            order_request = legacy.get("order_request") if isinstance(legacy.get("order_request"), dict) else {}
        remote_order = response.get("remote_order") if isinstance(response.get("remote_order"), dict) else {}
        if not remote_order:
            remote_order = legacy.get("remote_order") if isinstance(legacy.get("remote_order"), dict) else {}
        execution_quality = response.get("execution_quality") if isinstance(response.get("execution_quality"), dict) else {}
        if not execution_quality:
            execution_quality = legacy.get("execution_quality") if isinstance(legacy.get("execution_quality"), dict) else {}
        jit_quote = legacy.get("jit_quote") if isinstance(legacy.get("jit_quote"), dict) else {}
        _append_price_candidate(candidates, response.get("fill_price"))
        _append_price_candidate(candidates, order_request.get("price"))
        _append_price_candidate(candidates, order_request.get("submitted_limit_price"))
        _append_price_candidate(candidates, order_request.get("pre_jit_price"))
        _append_price_candidate(candidates, remote_order.get("price"))
        _append_price_candidate(candidates, execution_quality.get("submitted_limit_price"))
        _append_price_candidate(candidates, execution_quality.get("pre_jit_limit_price"))
        _append_price_candidate(candidates, execution_quality.get("realized_price"))
        _append_price_candidate(candidates, jit_quote.get("submitted_limit_price"))
        requested_shares = _first_non_null_float(order_request.get("size"), entry_order.requested_shares)
        for total_key in ("estimated_total_cost_usd", "market_amount_usd", "marketable_buy_notional_usd"):
            total_cost = _first_non_null_float(order_request.get(total_key))
            if total_cost is not None and requested_shares is not None and requested_shares > 0:
                _append_price_candidate(candidates, float(total_cost) / float(requested_shares))
        if not candidates:
            _append_price_candidate(candidates, entry_order.limit_price)
    if not candidates:
        _append_price_candidate(candidates, scenario.limit_price)
    return max(candidates) if candidates else float(scenario.limit_price)


def _append_price_candidate(candidates: list[float], value: object) -> None:
    parsed = _first_non_null_float(value)
    if parsed is None:
        return
    price = float(parsed)
    if 0.0 < price < 1.0:
        candidates.append(price)


def _paired_exit_payload(
    intent: ExecutionIntent,
    order: OrderState,
    fill: FillState | None,
    reconciliation: ReconciliationResult,
) -> dict[str, Any]:
    return {
        "intent_key": intent.intent_key,
        "candidate_key": intent.candidate_key,
        "strategy_id": intent.strategy_id,
        "event_key": intent.event_key,
        "event_token_key": intent.event_token_key,
        "intent_type": intent.intent_type,
        "order_type": intent.order_type,
        "side": intent.side,
        "shares": intent.shares,
        "limit_price": intent.limit_price,
        "order_key": order.order_key,
        "exchange_order_id": order.exchange_order_id,
        "order_status": order.status,
        "filled_shares": None if fill is None else fill.filled_shares,
        "fill_price": None if fill is None else fill.fill_price,
        "reconciliation_status": reconciliation.status,
        "reconciliation_evidence": reconciliation.evidence,
        "order_source_payload": order.source_payload if isinstance(order.source_payload, dict) else {},
    }


def _paired_exit_blockers(order: OrderState, reconciliation: ReconciliationResult) -> tuple[str, ...]:
    blockers: list[str] = []
    if order.status not in {"submitted", "accepted", "partially_filled", "filled"}:
        blockers.append("paired_exit_order_not_submitted")
    blockers.extend(f"paired_exit_{blocker}" for blocker in reconciliation.blockers)
    return tuple(blockers)


def _execute_order_lifecycle(
    intent: ExecutionIntent,
    *,
    config: SupervisedRuntimeConfig,
    scenario: RuntimeScenario,
) -> tuple[OrderState, FillState | None, ReconciliationResult]:
    if _is_live_mode(config.mode):
        return _supervised_live_order_lifecycle(intent, config=config, scenario=scenario)
    return _simulate_order_lifecycle(intent, config=config, scenario=scenario)


def _execute_paired_exit_lifecycle(
    intent: ExecutionIntent,
    *,
    config: SupervisedRuntimeConfig,
    scenario: RuntimeScenario,
) -> tuple[OrderState, FillState | None, ReconciliationResult]:
    order, fill, reconciliation = _execute_order_lifecycle(intent, config=config, scenario=scenario)
    attempts: list[dict[str, Any]] = [_paired_exit_attempt_payload(order, reconciliation, attempt_index=1)]
    if not _is_live_mode(config.mode):
        return order, fill, reconciliation
    retry_delays_seconds = (0.75, 1.5, 3.0, 5.0)
    for attempt_index, delay_seconds in enumerate(retry_delays_seconds, start=2):
        if not _paired_exit_should_retry(order, fill=fill, reconciliation=reconciliation):
            break
        time.sleep(delay_seconds)
        order, fill, reconciliation = _execute_order_lifecycle(intent, config=config, scenario=scenario)
        attempts.append(_paired_exit_attempt_payload(order, reconciliation, attempt_index=attempt_index))
    if len(attempts) > 1:
        source_payload = order.source_payload if isinstance(order.source_payload, dict) else {}
        order = transition_order(
            order,
            status=order.status,
            source_payload={
                **source_payload,
                "paired_exit_retry_attempts": attempts,
                "paired_exit_retry_count": len(attempts) - 1,
            },
        )
    return order, fill, reconciliation


def _paired_exit_should_retry(
    order: OrderState,
    *,
    fill: FillState | None,
    reconciliation: ReconciliationResult,
) -> bool:
    if order.status in {"submitted", "accepted", "partially_filled", "filled"}:
        return False
    if fill is not None:
        return False
    payload = order.source_payload if isinstance(order.source_payload, dict) else {}
    response = payload.get("live_executor_response") if isinstance(payload.get("live_executor_response"), dict) else {}
    legacy = response.get("legacy_submission") if isinstance(response.get("legacy_submission"), dict) else {}
    if _payload_has_conditional_balance_delay(legacy):
        return True
    response_text = " ".join(
        str(value).lower()
        for value in (
            response.get("error"),
            response.get("message"),
            legacy.get("error"),
            legacy.get("message"),
            legacy.get("reason"),
            *response.get("blockers", []),
            *reconciliation.blockers,
        )
        if value is not None
    )
    return "conditional_balance_too_low" in response_text or "conditional token balance" in response_text


def _payload_has_conditional_balance_delay(payload: dict[str, Any]) -> bool:
    for key in ("asset_check", "conditional_token"):
        row = payload.get(key)
        if not isinstance(row, dict):
            continue
        reason = str(row.get("reason") or "").lower()
        balance = _optional_float(row.get("balance"))
        required = _optional_float(row.get("required_amount") or row.get("required_shares"))
        if "conditional_balance_too_low" in reason:
            return True
        if balance is not None and required is not None and required > 0 and balance + 1e-9 < required:
            return True
    return False


def _paired_exit_attempt_payload(
    order: OrderState,
    reconciliation: ReconciliationResult,
    *,
    attempt_index: int,
) -> dict[str, Any]:
    payload = order.source_payload if isinstance(order.source_payload, dict) else {}
    response = payload.get("live_executor_response") if isinstance(payload.get("live_executor_response"), dict) else {}
    legacy = response.get("legacy_submission") if isinstance(response.get("legacy_submission"), dict) else {}
    asset_check = legacy.get("asset_check") if isinstance(legacy.get("asset_check"), dict) else {}
    return {
        "attempt_index": attempt_index,
        "order_status": order.status,
        "exchange_order_id": order.exchange_order_id,
        "reconciliation_status": reconciliation.status,
        "reconciliation_blockers": list(reconciliation.blockers),
        "conditional_balance_reason": asset_check.get("reason"),
        "conditional_balance": asset_check.get("balance"),
        "required_shares": asset_check.get("required_shares") or asset_check.get("required_amount"),
    }


def _simulate_order_lifecycle(
    intent: ExecutionIntent,
    *,
    config: SupervisedRuntimeConfig,
    scenario: RuntimeScenario,
) -> tuple[OrderState, FillState | None, ReconciliationResult]:
    exchange_order_id = f"{config.mode}:{intent.intent_key}"
    created_order = create_order_from_intent(intent, exchange_order_id=exchange_order_id)
    simulation = simulate_fill(_replay_order_for_intent(intent, scenario), _replay_frame_for_scenario(scenario))
    order_status = {
        "filled": "filled",
        "partial": "partially_filled",
        "unfilled": "unfilled",
        "blocked": "unfilled",
    }.get(simulation.fillability_status, "unfilled")
    order = transition_order(
        created_order,
        status=order_status,
        reason=";".join(simulation.blockers) if simulation.blockers else simulation.fillability_status,
        source_payload={"fill_simulation": _fill_simulation_payload(simulation)},
    )
    fill = None
    if simulation.filled_shares > 0 and simulation.fill_price is not None:
        fill = record_fill(
            order,
            filled_shares=float(simulation.filled_shares),
            fill_price=float(simulation.fill_price),
            filled_at_utc=_decision_at_for_scenario(scenario),
            reconciled=True,
        )
    fill_shares = 0.0 if fill is None else float(fill.filled_shares)
    remote_filled = max(0.0, fill_shares - 0.25) if config.simulate_reconciliation_mismatch else fill_shares
    reconciliation = reconcile_order(
        local_order=order,
        local_fills=[] if fill is None else [fill],
        remote_order={"exchange_order_id": exchange_order_id, "filled_shares": remote_filled, "status": order.status},
    )
    return order, fill, reconciliation


def _supervised_live_order_lifecycle(
    intent: ExecutionIntent,
    *,
    config: SupervisedRuntimeConfig,
    scenario: RuntimeScenario,
) -> tuple[OrderState, FillState | None, ReconciliationResult]:
    if config.supervised_executor is None:
        raise RuntimeError("live_executor_binding_missing")
    created_order = create_order_from_intent(intent)
    submitted_order = transition_order(created_order, status="submitted")
    response = config.supervised_executor(intent, submitted_order, scenario)
    exchange_order_id = str(response.get("exchange_order_id") or submitted_order.order_key)
    filled_shares = float(response.get("filled_shares") or 0.0)
    fill_price = float(response.get("fill_price") or scenario.limit_price)
    remote_filled = float(response.get("remote_filled_shares") if response.get("remote_filled_shares") is not None else filled_shares)
    response_status = str(response.get("status") or ("filled" if filled_shares > 0.0 else "submit_error"))
    response_blockers = tuple(str(blocker) for blocker in response.get("blockers") or [])
    filled_order = transition_order(
        create_order_from_intent(intent, exchange_order_id=exchange_order_id),
        status=response_status,
        source_payload={"live_executor_response": response},
    )
    fill = (
        record_fill(filled_order, filled_shares=filled_shares, fill_price=fill_price, reconciled=True)
        if filled_shares > 0.0
        else None
    )
    reconciliation_blockers = []
    if response_status in {"submit_error", "rejected"}:
        if _submit_failure_has_fill_ambiguity(response, filled_shares=filled_shares, remote_filled=remote_filled):
            reconciliation_blockers.append(f"{response_status}_fill_ambiguous")
        else:
            reconciliation_blockers.append(f"{response_status}_no_fill")
    reconciliation_blockers.extend(response_blockers)
    reconciliation = reconcile_order(
        local_order=filled_order,
        local_fills=[] if fill is None else [fill],
        remote_order={"exchange_order_id": exchange_order_id, "filled_shares": remote_filled, "status": filled_order.status},
    )
    if reconciliation_blockers:
        reconciliation = ReconciliationResult(
            reconciled=False,
            status="reconciliation_failed",
            blockers=tuple((*reconciliation.blockers, *reconciliation_blockers)),
            evidence=reconciliation.evidence,
        )
    return filled_order, fill, reconciliation


def _has_reconciliation_integrity_mismatch(reconciliation: ReconciliationResult) -> bool:
    non_integrity_blockers = {
        "submit_error_no_fill",
        "rejected_no_fill",
        "exchange_status_not_operational",
        "polymarket_status_unavailable",
        "polymarket_active_maintenance",
        "polymarket_active_incident",
        "polymarket_page_not_operational",
    }
    non_integrity_prefixes = ("polymarket_status:",)
    return any(
        blocker not in non_integrity_blockers
        and not any(str(blocker).startswith(prefix) for prefix in non_integrity_prefixes)
        for blocker in reconciliation.blockers
    )


def _submit_failure_has_fill_ambiguity(response: dict[str, Any], *, filled_shares: float, remote_filled: float) -> bool:
    if filled_shares > 0.0 or remote_filled > 0.0:
        return True
    legacy = response.get("legacy_submission") if isinstance(response.get("legacy_submission"), dict) else {}
    if isinstance(legacy.get("post_error_reconciliation"), dict):
        return True
    values = [
        response.get("error"),
        response.get("message"),
        legacy.get("error"),
        legacy.get("message"),
        legacy.get("body"),
    ]
    error_text = " ".join(str(value).lower() for value in values if value is not None)
    return any(fragment in error_text for fragment in ("timeout", "timed out", "read operation", "connection", "temporarily"))


def _risk_context_for_scenario(scenario: RuntimeScenario) -> CandidateRiskContext:
    return CandidateRiskContext(
        notional_usd=scenario.shares * scenario.limit_price,
        active_cost_usd=0.0,
        event_exposure_usd=0.0,
        same_side_exposure_usd=0.0,
        correlated_exposure_usd=0.0,
        spread=scenario.spread,
        quote_age_seconds=scenario.quote_age_seconds,
        profile_age_seconds=scenario.profile_age_seconds,
        signal_age_seconds=scenario.signal_age_seconds,
        expected_slippage=scenario.expected_slippage,
        liquidity_depth=scenario.liquidity_depth,
        time_remaining_seconds=scenario.time_remaining_seconds,
        has_event_token_key=bool(scenario.event_token_key),
        has_lifecycle_coverage=True,
    )


def _coverage_type_for_strategy(spec: StrategySpec) -> str:
    lifecycle = str(spec.exit_rules.get("lifecycle_coverage") or "")
    if "managed_cashout_rebuy" in lifecycle or "cashout_rebuy" in lifecycle:
        return "split_exit_plan"
    if "managed_cashout_or_settlement" in lifecycle:
        return "hedge_managed_inventory_plan"
    if spec.strategy_family in {"outcome_prediction_with_hedge", "hedge_management"}:
        return "hedge_managed_inventory_plan"
    if spec.strategy_family in {"scalping", "outcome_prediction_with_scalping"}:
        return "active_cashout_order"
    if spec.exit_rules.get("hold_to_settlement"):
        return "hold_to_settlement_policy"
    return "active_cashout_order"


def _managed_runtime_plans_for_strategy(
    spec: StrategySpec,
    *,
    scenario: RuntimeScenario,
    position: PositionState | None,
    fill: FillState | None,
) -> tuple[ManagedIntentPlan, ...]:
    if position is None or fill is None:
        return ()
    lifecycle = str(spec.exit_rules.get("lifecycle_coverage") or "")
    if "managed" not in lifecycle and "cashout_rebuy" not in lifecycle:
        return ()
    current_bid = _optional_float(scenario.signal_context.get("best_bid"))
    if current_bid is None:
        current_bid = max(0.0, float(scenario.limit_price) - float(scenario.spread))
    current_ask = _optional_float(scenario.signal_context.get("best_ask"))
    if current_ask is None:
        current_ask = float(scenario.limit_price)
    managed_position = ManagedPositionState(
        strategy_id=spec.strategy_id,
        event_token_key=scenario.event_token_key,
        outcome=scenario.outcome or "",
        shares=position.shares,
        entry_price=fill.fill_price,
        current_bid=current_bid,
        current_ask=current_ask,
        opened_at_utc=position.opened_at_utc,
    )
    plans: list[ManagedIntentPlan] = []
    if "cashout" in lifecycle or spec.exit_rules.get("cashout_required"):
        plans.extend(cashout_rebuy_plan(managed_position))
        if not plans:
            plans.append(
                ManagedIntentPlan(
                    intent_type="cashout_watch",
                    side="SELL",
                    price=current_bid,
                    shares=position.shares,
                    reason="waiting_for_cashout_or_rebound_trigger",
                )
            )
    if spec.hedge_rules.get("managed_rebalance"):
        target_up_ratio = _optional_float(scenario.signal_context.get("target_up_ratio"))
        if target_up_ratio is None:
            target_up_ratio = 0.50
        rebalance = hedge_rebalance_plan(
            current_up_cost=position.cost_basis_usd if _is_up_outcome(scenario.outcome) else 0.0,
            current_down_cost=0.0 if _is_up_outcome(scenario.outcome) else position.cost_basis_usd,
            target_up_ratio=target_up_ratio,
            max_additional_notional_usd=max(1.0, position.cost_basis_usd),
            current_up_ask=current_ask if _is_up_outcome(scenario.outcome) else None,
            current_down_ask=current_ask if not _is_up_outcome(scenario.outcome) else None,
        )
        if rebalance is not None:
            plans.append(rebalance)
    return tuple(plans)


def _managed_plan_payload(plan: ManagedIntentPlan) -> dict[str, Any]:
    return {
        "intent_type": plan.intent_type,
        "side": plan.side,
        "price": plan.price,
        "shares": plan.shares,
        "reason": plan.reason,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def _is_up_outcome(outcome: str | None) -> bool:
    return str(outcome or "").strip().lower() in {"up", "yes"}


def _strategy_attribution(spec: StrategySpec) -> dict[str, Any]:
    return {
        "strategy_family": spec.strategy_family,
        "sources": tuple(spec.signal_inputs.get("profile", ())) + tuple(spec.signal_inputs.get("event", ())) + tuple(spec.signal_inputs.get("indicator", ())),
        "profile_sources": tuple(spec.signal_inputs.get("profile", ())),
        "event_sources": tuple(spec.signal_inputs.get("event", ())),
        "indicator_sources": tuple(spec.signal_inputs.get("indicator", ())),
    }


def _normalize_lifecycle_blockers(blockers: tuple[str, ...]) -> tuple[str, ...]:
    if not blockers:
        return ()
    return tuple("missing_lifecycle_coverage" if blocker.startswith("missing_lifecycle_coverage:") else blocker for blocker in blockers)


def _apply_strategy_shadow_decision(spec: StrategySpec, scenario: RuntimeScenario) -> tuple[RuntimeScenario, dict[str, Any], tuple[str, ...]]:
    return _strategy_shadow_scenario(spec.with_enabled(True), scenario)


def _decision_at_for_scenario(scenario: RuntimeScenario) -> datetime:
    context = scenario.signal_context if isinstance(scenario.signal_context, dict) else {}
    for key in ("decision_at_utc", "system_received_at_utc", "source_observed_at_utc"):
        value = context.get(key)
        if value:
            return _parse_datetime(value)
    return datetime.now(UTC)


def _replay_order_for_intent(intent: ExecutionIntent, scenario: RuntimeScenario) -> ReplayOrder:
    replay_order_type = "LIMIT" if str(intent.order_type or "").lower().startswith("limit") else "MARKET"
    return ReplayOrder(
        order_type=replay_order_type,
        side=intent.side,
        shares=float(intent.shares),
        decision_at_utc=_decision_at_for_scenario(scenario),
        limit_price=None if intent.limit_price is None else float(intent.limit_price),
        max_quote_age_seconds=max(1.0, float(scenario.quote_age_seconds)),
    )


def _replay_frame_for_scenario(scenario: RuntimeScenario) -> ReplayFrame:
    context = scenario.signal_context if isinstance(scenario.signal_context, dict) else {}
    decision_at = _decision_at_for_scenario(scenario)
    quote_timestamp = _parse_datetime(context.get("system_received_at_utc") or decision_at.isoformat())
    best_bid = _optional_float(context.get("best_bid"))
    best_ask = _optional_float(context.get("best_ask"))
    mid_price = _first_non_null_float(
        context.get("mid_price"),
        ((best_bid + best_ask) / 2.0) if best_bid is not None and best_ask is not None else None,
        scenario.limit_price,
    )
    return ReplayFrame(
        replay_frame_key=f"runtime-shadow:{scenario.event_token_key}:{decision_at.isoformat()}",
        event_key=scenario.event_key,
        event_token_key=scenario.event_token_key,
        replay_timestamp_utc=decision_at,
        source_observed_at_utc=quote_timestamp,
        decision_at_utc=decision_at,
        market_state={
            "system_received_at_utc": quote_timestamp.isoformat(),
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": mid_price,
            "spread": _optional_float(scenario.spread),
            "depth_top3_bid_size": _optional_float(context.get("depth_top3_bid_size")),
            "depth_top3_ask_size": _first_non_null_float(context.get("depth_top3_ask_size"), scenario.liquidity_depth),
        },
        underlying_state={"source": "runtime_shadow_scenario"},
    )


def _fill_simulation_payload(simulation: Any) -> dict[str, Any]:
    return {
        "fillability_status": simulation.fillability_status,
        "order_type": simulation.order_type,
        "side": simulation.side,
        "requested_shares": simulation.requested_shares,
        "filled_shares": simulation.filled_shares,
        "fill_price": simulation.fill_price,
        "slippage": simulation.slippage,
        "blockers": list(simulation.blockers),
        "simulation": dict(simulation.simulation),
    }


def _shadow_fill_blockers(
    *,
    config: SupervisedRuntimeConfig,
    order: OrderState,
    fill: FillState | None,
) -> tuple[str, ...]:
    if _is_live_mode(config.mode):
        return ()
    payload = order.source_payload if isinstance(order.source_payload, dict) else {}
    simulation = payload.get("fill_simulation") if isinstance(payload.get("fill_simulation"), dict) else {}
    blockers = tuple(str(blocker) for blocker in simulation.get("blockers") or [])
    if fill is not None:
        return blockers
    if blockers:
        return blockers
    return ("shadow_fill_unfilled",)


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _first_non_null_float(*values: Any) -> float | None:
    for value in values:
        parsed = _optional_float(value)
        if parsed is not None:
            return parsed
    return None


def _metadata_float(metadata: Mapping[str, Any], key: str, default: float) -> float:
    parsed = _optional_float(metadata.get(key))
    return default if parsed is None else parsed


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
