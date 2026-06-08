from __future__ import annotations

from crypto_options_app.strategies.registry import strategy_registry
from crypto_options_app.workers.supervisor import (
    PRIMARY_SOURCES_OF_TRUTH,
    REQUIRED_STRUCTURAL_TEST_GROUPS,
    build_cdci_readiness_report,
    default_issue_summary,
    evaluate_stop_pause_conditions,
    evaluate_structural_validation,
    validate_minimal_live_structural_candidates,
    validate_minimal_structural_candidates,
    validate_runtime_adapter_structural_candidates,
    validate_sources_of_truth,
)
from crypto_options_app.trading.executor_boundary import ExecutorBoundaryConfig
from crypto_options_app.workers.runtime_adapter import SupervisedRuntimeConfig


def test_sources_of_truth_reject_issue_47_as_implementation_authority_pytest() -> None:
    assert validate_sources_of_truth(PRIMARY_SOURCES_OF_TRUTH) == ()
    blockers = validate_sources_of_truth(("issue #47", "GitHub parent issue #108"))
    assert "missing_technical_specs_source" in blockers
    assert "research_issue_47_cannot_be_implementation_authority" in blockers


def test_structural_validation_blocks_live_pulse_until_all_groups_pass_pytest() -> None:
    partial = evaluate_structural_validation({"app_startup": True})
    full = evaluate_structural_validation({group: True for group in REQUIRED_STRUCTURAL_TEST_GROUPS})

    assert partial.passed is False
    assert partial.live_pulse_allowed is False
    assert "canonical_db_initialization" in partial.missing_groups
    assert full.passed is True
    assert full.live_pulse_allowed is True


def test_stop_pause_condition_handling_filters_known_safety_blockers_pytest() -> None:
    conditions = evaluate_stop_pause_conditions(
        {"mechanical_failure", "duplicate_cadence", "not_a_stop_condition", "stale_service"}
    )

    assert conditions == ("duplicate_cadence", "mechanical_failure", "stale_service")


def test_minimal_structural_candidate_validation_covers_all_10_pytest() -> None:
    structural = evaluate_structural_validation({group: True for group in REQUIRED_STRUCTURAL_TEST_GROUPS})
    candidates = validate_minimal_structural_candidates(
        structural,
        data_blockers_by_strategy={"event_context_outcome_v1": ("missing_event_context_data",)},
    )

    assert len(candidates) == len(strategy_registry())
    assert {candidate.strategy_id for candidate in candidates} == set(strategy_registry())
    blocked = {candidate.strategy_id: candidate for candidate in candidates if candidate.blockers}
    assert blocked["event_context_outcome_v1"].status == "structurally_blocked"
    assert blocked["event_context_outcome_v1"].max_trades == 3
    assert all(candidate.minimal_sizing is True for candidate in candidates)


def test_minimal_live_structural_validation_records_valid_safety_blockers_pytest() -> None:
    structural = evaluate_structural_validation({group: True for group in REQUIRED_STRUCTURAL_TEST_GROUPS})
    candidates = validate_minimal_live_structural_candidates(
        structural,
        executor_boundary=ExecutorBoundaryConfig(
            supervised_runtime_gate=True,
            ledger_gate=True,
            risk_gate=True,
            reconciliation_gate=True,
            execution_approved=False,
            live_risk_acknowledged=False,
        ),
        supervised_runtime_adapter_available=False,
    )

    assert len(candidates) == len(strategy_registry())
    assert {candidate.strategy_id for candidate in candidates} == set(strategy_registry())
    assert {candidate.status for candidate in candidates} == {"valid_structural_blocker"}
    for candidate in candidates:
        assert candidate.max_trades == 3
        assert candidate.minimal_sizing is True
        assert "supervised_runtime_adapter_missing" in candidate.blockers
        assert "executor_boundary_not_ready" in candidate.blockers


def test_runtime_adapter_candidates_are_cdci_readiness_compatible_pytest() -> None:
    candidates = validate_runtime_adapter_structural_candidates(
        SupervisedRuntimeConfig(
            run_id="runtime-adapter-cdci",
            mode="dry_run",
            executor_boundary=ExecutorBoundaryConfig(
                supervised_runtime_gate=True,
                ledger_gate=True,
                risk_gate=True,
                reconciliation_gate=True,
                execution_approved=False,
                live_risk_acknowledged=False,
            ),
        )
    )
    structural = evaluate_structural_validation({group: True for group in REQUIRED_STRUCTURAL_TEST_GROUPS})
    report = build_cdci_readiness_report(
        structural=structural,
        candidates=candidates,
        issue_summary=default_issue_summary(status="structural_complete"),
    )

    assert len(candidates) == len(strategy_registry())
    assert {candidate.status for candidate in candidates} == {"minimal_runtime_structural_ready"}
    assert report.strategy_bundle_comparison_may_begin is True
    assert report.remaining_blockers == ()


def test_cdci_readiness_report_allows_comparison_only_when_clear_pytest() -> None:
    structural = evaluate_structural_validation({group: True for group in REQUIRED_STRUCTURAL_TEST_GROUPS})
    ready_candidates = validate_minimal_structural_candidates(structural)
    blocked_candidates = validate_minimal_structural_candidates(
        structural,
        data_blockers_by_strategy={"event_context_outcome_v1": ("missing_event_context_data",)},
    )

    ready_report = build_cdci_readiness_report(
        structural=structural,
        candidates=ready_candidates,
        issue_summary=default_issue_summary(),
    )
    blocked_report = build_cdci_readiness_report(
        structural=structural,
        candidates=blocked_candidates,
        issue_summary=default_issue_summary(),
        observed_stop_conditions={"stale_service"},
    )

    assert ready_report.strategy_bundle_comparison_may_begin is True
    assert ready_report.remaining_blockers == ()
    assert blocked_report.strategy_bundle_comparison_may_begin is False
    assert "stale_service" in blocked_report.remaining_blockers
    assert "event_context_outcome_v1:missing_event_context_data" in blocked_report.remaining_blockers
