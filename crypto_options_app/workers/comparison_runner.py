from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from crypto_options_app.strategies.registry import all_strategy_specs, starting_strategy_ids
from crypto_options_app.strategies.schema import StrategySpec
from crypto_options_app.trading.executor_boundary import ExecutorBoundaryConfig
from crypto_options_app.workers.runtime_adapter import (
    RuntimeMode,
    RuntimeScenario,
    SupervisedRuntimeConfig,
    validate_all_strategy_scenarios,
)


VolumeClass = Literal["low_volume", "high_volume"]
CORE_FLOW_STRATEGY_IDS = (
    "event_context_outcome_v1",
    "buying_ahead_pre_event_v1",
    "s_tier_outcome_consensus_cashout_v1",
)


@dataclass(frozen=True)
class StrategyComparisonConstraints:
    strategy_id: str
    volume_class: VolumeClass
    first_run_sizing: str
    high_volume_trade_cap: int | None
    low_volume_event_target: int | None
    hard_event_cap: int
    hard_time_limit_seconds: int
    total_budget_cap_usd: float | None = None


@dataclass(frozen=True)
class StrategyBundleGroup:
    group_id: str
    strategy_ids: tuple[str, ...]
    constraints: tuple[StrategyComparisonConstraints, ...]


@dataclass(frozen=True)
class BundleComparisonPlan:
    generated_at_utc: datetime
    mode: RuntimeMode
    groups: tuple[StrategyBundleGroup, ...]
    plan_id: str = "standard_bundle_comparison"
    manual_orders_avoided: bool = True


@dataclass(frozen=True)
class CandidateBundleResult:
    strategy_id: str
    group_id: str
    status: str
    blockers: tuple[str, ...]
    volume_class: VolumeClass
    first_run_sizing: str
    high_volume_trade_cap: int | None
    low_volume_event_target: int | None
    hard_event_cap: int
    hard_time_limit_seconds: int
    total_budget_cap_usd: float | None
    orders_allowed: bool
    live_trading_authorized: bool


@dataclass(frozen=True)
class BundleGroupResult:
    group_id: str
    status: str
    candidate_results: tuple[CandidateBundleResult, ...]

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(
            f"{candidate.strategy_id}:{blocker}"
            for candidate in self.candidate_results
            for blocker in candidate.blockers
        )


@dataclass(frozen=True)
class BundleComparisonRunResult:
    run_id: str
    mode: RuntimeMode
    generated_at_utc: datetime
    plan: BundleComparisonPlan
    group_results: tuple[BundleGroupResult, ...]
    manual_orders_avoided: bool = True

    @property
    def dry_run_shadow_comparison_may_begin(self) -> bool:
        return self.mode in {"dry_run", "shadow"} and not self.remaining_blockers

    @property
    def remaining_blockers(self) -> tuple[str, ...]:
        return tuple(blocker for group in self.group_results for blocker in group.blockers)


def build_bundle_comparison_plan(
    *,
    mode: RuntimeMode = "dry_run",
    specs: tuple[StrategySpec, ...] | None = None,
    group_size: int = 5,
) -> BundleComparisonPlan:
    specs = specs or all_strategy_specs()
    spec_by_id = {spec.strategy_id: spec for spec in specs}
    ordered_ids = tuple(strategy_id for strategy_id in starting_strategy_ids() if strategy_id in spec_by_id) + tuple(
        spec.strategy_id for spec in specs if spec.strategy_id not in starting_strategy_ids()
    )
    groups: list[StrategyBundleGroup] = []
    for index in range(0, len(ordered_ids), group_size):
        strategy_ids = ordered_ids[index : index + group_size]
        groups.append(
            StrategyBundleGroup(
                group_id=f"bundle_group_{len(groups) + 1}",
                strategy_ids=strategy_ids,
                constraints=tuple(_constraints_for_strategy(spec_by_id[strategy_id]) for strategy_id in strategy_ids),
            )
        )
    return BundleComparisonPlan(generated_at_utc=datetime.now(UTC), mode=mode, groups=tuple(groups))


def build_core_flow_test_plan(*, mode: RuntimeMode = "dry_run", specs: tuple[StrategySpec, ...] | None = None) -> BundleComparisonPlan:
    specs = specs or all_strategy_specs()
    spec_by_id = {spec.strategy_id: spec for spec in specs}
    missing = [strategy_id for strategy_id in CORE_FLOW_STRATEGY_IDS if strategy_id not in spec_by_id]
    if missing:
        raise ValueError(f"missing core flow strategies: {missing}")
    constraints = tuple(_core_flow_constraints_for_strategy(spec_by_id[strategy_id]) for strategy_id in CORE_FLOW_STRATEGY_IDS)
    return BundleComparisonPlan(
        generated_at_utc=datetime.now(UTC),
        mode=mode,
        groups=(
            StrategyBundleGroup(
                group_id="core_flow_bucket_1",
                strategy_ids=CORE_FLOW_STRATEGY_IDS,
                constraints=constraints,
            ),
        ),
        plan_id="core_flow_max_3_events_15m_10usd_minimal",
    )


def run_bundle_comparison_readiness(
    *,
    run_id: str,
    mode: RuntimeMode,
    executor_boundary: ExecutorBoundaryConfig,
    scenario: RuntimeScenario | None = None,
    specs: tuple[StrategySpec, ...] | None = None,
    allow_live_submission: bool = False,
) -> BundleComparisonRunResult:
    specs = specs or all_strategy_specs()
    plan = build_bundle_comparison_plan(mode=mode, specs=specs)
    spec_by_id = {spec.strategy_id: spec for spec in specs}
    group_results: list[BundleGroupResult] = []
    for group in plan.groups:
        runtime = validate_all_strategy_scenarios(
            SupervisedRuntimeConfig(
                run_id=f"{run_id}:{group.group_id}",
                mode=mode,
                executor_boundary=executor_boundary,
                max_trades_per_strategy=3,
                minimal_sizing=True,
                allow_live_submission=allow_live_submission,
            ),
            scenario=scenario,
            specs=tuple(spec_by_id[strategy_id] for strategy_id in group.strategy_ids),
        )
        constraints_by_strategy = {constraint.strategy_id: constraint for constraint in group.constraints}
        candidate_results = tuple(
            _candidate_bundle_result(
                group_id=group.group_id,
                constraints=constraints_by_strategy[result.strategy_id],
                status="ready" if not result.blockers else "blocked",
                blockers=result.blockers,
                orders_allowed=result.orders_allowed,
                live_trading_authorized=result.live_trading_authorized,
            )
            for result in runtime.results
        )
        group_results.append(
            BundleGroupResult(
                group_id=group.group_id,
                status="ready" if not any(candidate.blockers for candidate in candidate_results) else "blocked",
                candidate_results=candidate_results,
            )
        )
    return BundleComparisonRunResult(
        run_id=run_id,
        mode=mode,
        generated_at_utc=datetime.now(UTC),
        plan=plan,
        group_results=tuple(group_results),
        manual_orders_avoided=True,
    )


def _candidate_bundle_result(
    *,
    group_id: str,
    constraints: StrategyComparisonConstraints,
    status: str,
    blockers: tuple[str, ...],
    orders_allowed: bool,
    live_trading_authorized: bool,
) -> CandidateBundleResult:
    return CandidateBundleResult(
        strategy_id=constraints.strategy_id,
        group_id=group_id,
        status=status,
        blockers=blockers,
        volume_class=constraints.volume_class,
        first_run_sizing=constraints.first_run_sizing,
        high_volume_trade_cap=constraints.high_volume_trade_cap,
        low_volume_event_target=constraints.low_volume_event_target,
        hard_event_cap=constraints.hard_event_cap,
        hard_time_limit_seconds=constraints.hard_time_limit_seconds,
        total_budget_cap_usd=constraints.total_budget_cap_usd,
        orders_allowed=orders_allowed,
        live_trading_authorized=live_trading_authorized,
    )


def _constraints_for_strategy(spec: StrategySpec) -> StrategyComparisonConstraints:
    volume_class: VolumeClass = "high_volume" if _is_high_volume_strategy(spec) else "low_volume"
    return StrategyComparisonConstraints(
        strategy_id=spec.strategy_id,
        volume_class=volume_class,
        first_run_sizing="minimal",
        high_volume_trade_cap=50 if volume_class == "high_volume" else None,
        low_volume_event_target=10 if volume_class == "low_volume" else None,
        hard_event_cap=2,
        hard_time_limit_seconds=3600,
    )


def _core_flow_constraints_for_strategy(spec: StrategySpec) -> StrategyComparisonConstraints:
    return StrategyComparisonConstraints(
        strategy_id=spec.strategy_id,
        volume_class="low_volume",
        first_run_sizing="minimal",
        high_volume_trade_cap=None,
        low_volume_event_target=3,
        hard_event_cap=3,
        hard_time_limit_seconds=900,
        total_budget_cap_usd=10.0,
    )


def _is_high_volume_strategy(spec: StrategySpec) -> bool:
    high_volume_families = {
        "outcome_prediction_with_hedge",
        "outcome_prediction_with_scalping",
        "hedge_management",
        "scalping",
        "hybrid",
    }
    if spec.strategy_family in high_volume_families:
        return True
    profile_inputs = set(spec.signal_inputs.get("profile", ()))
    return bool(profile_inputs & {"hedge_proportion", "band_rebound", "volatility_liquidity"})
