from __future__ import annotations

from crypto_options_app.strategies.registry import starting_strategy_ids, strategy_registry
from crypto_options_app.trading.executor_boundary import ExecutorBoundaryConfig
from crypto_options_app.workers.comparison_runner import (
    CORE_FLOW_STRATEGY_IDS,
    build_bundle_comparison_plan,
    build_core_flow_test_plan,
    run_bundle_comparison_readiness,
)
from crypto_options_app.workers.runtime_adapter import RuntimeScenario


def test_bundle_comparison_plan_groups_10_candidates_with_starting_5_first_pytest() -> None:
    plan = build_bundle_comparison_plan()
    expected_count = len(strategy_registry())

    assert len(plan.groups) == (expected_count + 4) // 5
    assert tuple(plan.groups[0].strategy_ids) == starting_strategy_ids()
    assert len(plan.groups[0].strategy_ids) == 5
    assert all(len(group.strategy_ids) <= 5 for group in plan.groups)
    assert {
        strategy_id
        for group in plan.groups
        for strategy_id in group.strategy_ids
    } == set(strategy_registry())
    assert plan.manual_orders_avoided is True


def test_bundle_comparison_constraints_match_standard_test_spec_pytest() -> None:
    plan = build_bundle_comparison_plan()
    constraints = {
        constraint.strategy_id: constraint
        for group in plan.groups
        for constraint in group.constraints
    }

    assert set(constraints) == set(strategy_registry())
    for constraint in constraints.values():
        assert constraint.first_run_sizing == "minimal"
        assert constraint.hard_event_cap == 2
        assert constraint.hard_time_limit_seconds == 3600
        if constraint.volume_class == "high_volume":
            assert constraint.high_volume_trade_cap == 50
            assert constraint.low_volume_event_target is None
        else:
            assert constraint.high_volume_trade_cap is None
            assert constraint.low_volume_event_target == 10


def test_core_flow_plan_uses_first_bucket_with_3_events_15m_and_10usd_cap_pytest() -> None:
    plan = build_core_flow_test_plan()

    assert plan.plan_id == "core_flow_max_3_events_15m_10usd_minimal"
    assert len(plan.groups) == 1
    group = plan.groups[0]
    assert group.group_id == "core_flow_bucket_1"
    assert group.strategy_ids == CORE_FLOW_STRATEGY_IDS
    for constraint in group.constraints:
        assert constraint.first_run_sizing == "minimal"
        assert constraint.volume_class == "low_volume"
        assert constraint.low_volume_event_target == 3
        assert constraint.high_volume_trade_cap is None
        assert constraint.hard_event_cap == 3
        assert constraint.hard_time_limit_seconds == 900
        assert constraint.total_budget_cap_usd == 10.0


def test_bundle_comparison_dry_run_covers_all_10_without_live_orders_pytest() -> None:
    result = run_bundle_comparison_readiness(
        run_id="bundle-dry-run",
        mode="dry_run",
        executor_boundary=_blocked_boundary(),
    )

    candidate_results = [
        candidate
        for group in result.group_results
        for candidate in group.candidate_results
    ]
    assert len(candidate_results) == len(strategy_registry())
    assert {candidate.strategy_id for candidate in candidate_results} == set(strategy_registry())
    assert {group.status for group in result.group_results} == {"ready"}
    assert result.dry_run_shadow_comparison_may_begin is True
    assert result.remaining_blockers == ()
    assert result.manual_orders_avoided is True
    assert all(candidate.orders_allowed is False for candidate in candidate_results)
    assert all(candidate.live_trading_authorized is False for candidate in candidate_results)


def test_bundle_comparison_supervised_live_blocks_without_executor_boundary_pytest() -> None:
    result = run_bundle_comparison_readiness(
        run_id="bundle-live-blocked",
        mode="supervised_live",
        executor_boundary=_blocked_boundary(),
    )

    assert {group.status for group in result.group_results} == {"blocked"}
    assert result.dry_run_shadow_comparison_may_begin is False
    assert any("executor_boundary_not_ready" in blocker for blocker in result.remaining_blockers)
    assert any("live_submission_not_allowed" in blocker for blocker in result.remaining_blockers)


def test_bundle_comparison_propagates_adapter_blockers_pytest() -> None:
    result = run_bundle_comparison_readiness(
        run_id="bundle-missing-token",
        mode="dry_run",
        executor_boundary=_blocked_boundary(),
        scenario=RuntimeScenario(event_token_key=""),
    )

    assert {group.status for group in result.group_results} == {"blocked"}
    assert result.dry_run_shadow_comparison_may_begin is False
    assert all("missing_event_token_key" in blocker for blocker in result.remaining_blockers)


def _blocked_boundary() -> ExecutorBoundaryConfig:
    return ExecutorBoundaryConfig(
        supervised_runtime_gate=True,
        ledger_gate=True,
        risk_gate=True,
        reconciliation_gate=True,
        execution_approved=False,
        live_risk_acknowledged=False,
    )
