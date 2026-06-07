from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from crypto_options_app.strategies.registry import all_strategy_specs
from crypto_options_app.strategies.schema import validate_strategy_spec
from crypto_options_app.trading.executor_boundary import ExecutorBoundaryConfig, executor_boundary_ready


PRIMARY_SOURCES_OF_TRUTH = (
    "crypto_options_app/docs/reference/crypto_options/technical_specs",
    "GitHub parent issue #108",
    "GitHub child issues #109-#118",
    "GitHub parent issue #123",
    "GitHub child issues #124-#134",
    "crypto_options_app/docs/reference/crypto_options",
)
RESEARCH_ONLY_SOURCES = ("#47", "issue #47")

REQUIRED_STRUCTURAL_TEST_GROUPS = (
    "app_startup",
    "canonical_db_initialization",
    "shard_import_parity",
    "profile_grading_usage_gates",
    "event_level_reconstruction",
    "signal_generator_contracts",
    "indicator_computation",
    "replay_frame_generation",
    "fill_exit_simulation",
    "strategy_spec_validation",
    "candidate_to_intent_conversion",
    "market_limit_buy_sell_intents",
    "partial_fill_unfilled_order_handling",
    "lifecycle_coverage",
    "reconciliation_mismatch",
    "risk_stop_gates",
    "restart_state_reload",
    "report_attribution",
)

STOP_PAUSE_CONDITIONS = {
    "mechanical_failure",
    "missing_lifecycle_coverage",
    "reconciliation_mismatch",
    "excess_drawdown",
    "stale_feeds",
    "stale_service",
    "strategy_spec_violation",
    "duplicate_cadence",
    "credentials_access_failure",
}


@dataclass(frozen=True)
class StructuralValidationResult:
    passed: bool
    missing_groups: tuple[str, ...]
    failed_groups: tuple[str, ...]
    evidence: dict[str, Any]
    live_pulse_allowed: bool = False


@dataclass(frozen=True)
class CandidateStructuralValidation:
    strategy_id: str
    status: str
    max_trades: int
    minimal_sizing: bool
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReadinessReport:
    generated_at_utc: datetime
    structural: StructuralValidationResult
    candidates: tuple[CandidateStructuralValidation, ...]
    stop_pause_conditions: tuple[str, ...]
    issue_summary: dict[str, str]
    specs_used: tuple[str, ...]
    strategy_bundle_comparison_may_begin: bool
    remaining_blockers: tuple[str, ...] = ()


def validate_sources_of_truth(sources: tuple[str, ...]) -> tuple[str, ...]:
    blockers: list[str] = []
    if not any("technical_specs" in source for source in sources):
        blockers.append("missing_technical_specs_source")
    if not any("#108" in source for source in sources):
        blockers.append("missing_parent_issue_108")
    if any(research in source for source in sources for research in RESEARCH_ONLY_SOURCES):
        blockers.append("research_issue_47_cannot_be_implementation_authority")
    return tuple(blockers)


def evaluate_structural_validation(test_results: dict[str, bool]) -> StructuralValidationResult:
    missing = tuple(group for group in REQUIRED_STRUCTURAL_TEST_GROUPS if group not in test_results)
    failed = tuple(group for group, passed in test_results.items() if group in REQUIRED_STRUCTURAL_TEST_GROUPS and not passed)
    passed = not missing and not failed
    return StructuralValidationResult(
        passed=passed,
        missing_groups=missing,
        failed_groups=failed,
        evidence={"test_results": dict(test_results)},
        live_pulse_allowed=passed,
    )


def validate_minimal_structural_candidates(
    structural: StructuralValidationResult,
    *,
    data_blockers_by_strategy: dict[str, tuple[str, ...]] | None = None,
) -> tuple[CandidateStructuralValidation, ...]:
    data_blockers_by_strategy = data_blockers_by_strategy or {}
    results: list[CandidateStructuralValidation] = []
    for spec in all_strategy_specs():
        validation = validate_strategy_spec(spec)
        blockers: list[str] = []
        if not structural.passed:
            blockers.append("structural_tests_not_passed")
        if not validation.valid:
            blockers.extend(validation.errors)
        blockers.extend(data_blockers_by_strategy.get(spec.strategy_id, ()))
        if blockers:
            status = "structurally_blocked"
        else:
            status = "minimal_structural_ready"
        results.append(
            CandidateStructuralValidation(
                strategy_id=spec.strategy_id,
                status=status,
                max_trades=3,
                minimal_sizing=True,
                blockers=tuple(blockers),
            )
        )
    return tuple(results)


def validate_minimal_live_structural_candidates(
    structural: StructuralValidationResult,
    *,
    executor_boundary: ExecutorBoundaryConfig,
    supervised_runtime_adapter_available: bool,
    data_blockers_by_strategy: dict[str, tuple[str, ...]] | None = None,
) -> tuple[CandidateStructuralValidation, ...]:
    data_blockers_by_strategy = data_blockers_by_strategy or {}
    results: list[CandidateStructuralValidation] = []
    boundary_ready = executor_boundary_ready(executor_boundary)
    for spec in all_strategy_specs():
        validation = validate_strategy_spec(spec)
        blockers: list[str] = []
        if not structural.passed:
            blockers.append("structural_tests_not_passed")
        if not validation.valid:
            blockers.extend(validation.errors)
        if not supervised_runtime_adapter_available:
            blockers.append("supervised_runtime_adapter_missing")
        if not boundary_ready:
            blockers.append("executor_boundary_not_ready")
        blockers.extend(data_blockers_by_strategy.get(spec.strategy_id, ()))
        status = "minimal_live_structural_ready" if not blockers else "valid_structural_blocker"
        results.append(
            CandidateStructuralValidation(
                strategy_id=spec.strategy_id,
                status=status,
                max_trades=3,
                minimal_sizing=True,
                blockers=tuple(blockers),
            )
        )
    return tuple(results)


def validate_runtime_adapter_structural_candidates(
    config: Any,
) -> tuple[CandidateStructuralValidation, ...]:
    from crypto_options_app.workers.runtime_adapter import validate_all_strategy_scenarios

    runtime_report = validate_all_strategy_scenarios(config)
    results: list[CandidateStructuralValidation] = []
    for result in runtime_report.results:
        status = "minimal_runtime_structural_ready" if not result.blockers else "valid_structural_blocker"
        results.append(
            CandidateStructuralValidation(
                strategy_id=result.strategy_id,
                status=status,
                max_trades=config.max_trades_per_strategy,
                minimal_sizing=config.minimal_sizing,
                blockers=result.blockers,
            )
        )
    return tuple(results)


def evaluate_stop_pause_conditions(observed_conditions: set[str]) -> tuple[str, ...]:
    return tuple(sorted(condition for condition in observed_conditions if condition in STOP_PAUSE_CONDITIONS))


def build_cdci_readiness_report(
    *,
    structural: StructuralValidationResult,
    candidates: tuple[CandidateStructuralValidation, ...],
    issue_summary: dict[str, str],
    specs_used: tuple[str, ...] = PRIMARY_SOURCES_OF_TRUTH,
    observed_stop_conditions: set[str] | None = None,
) -> ReadinessReport:
    source_blockers = validate_sources_of_truth(specs_used)
    stop_conditions = evaluate_stop_pause_conditions(observed_stop_conditions or set())
    candidate_blockers = tuple(
        f"{candidate.strategy_id}:{blocker}"
        for candidate in candidates
        for blocker in candidate.blockers
    )
    remaining = (
        tuple(f"structural_missing:{group}" for group in structural.missing_groups)
        + tuple(f"structural_failed:{group}" for group in structural.failed_groups)
        + source_blockers
        + stop_conditions
        + candidate_blockers
    )
    all_candidates_ready_or_validly_blocked = all(
        candidate.status
        in {
            "minimal_structural_ready",
            "structurally_blocked",
            "minimal_live_structural_ready",
            "minimal_runtime_structural_ready",
            "valid_structural_blocker",
        }
        for candidate in candidates
    )
    comparison_may_begin = structural.passed and not source_blockers and not stop_conditions and all_candidates_ready_or_validly_blocked and not candidate_blockers
    return ReadinessReport(
        generated_at_utc=datetime.now(UTC),
        structural=structural,
        candidates=candidates,
        stop_pause_conditions=stop_conditions,
        issue_summary=issue_summary,
        specs_used=specs_used,
        strategy_bundle_comparison_may_begin=comparison_may_begin,
        remaining_blockers=remaining,
    )


def default_issue_summary(status: str = "structural_complete") -> dict[str, str]:
    return {f"#{issue}": status for issue in range(109, 119)}
