from __future__ import annotations

from crypto_options_app.strategies.registry import all_strategy_specs, strategy_registry
from crypto_options_app.trading.executor_boundary import ExecutorBoundaryConfig
from crypto_options_app.workers.runtime_adapter import (
    RuntimeScenario,
    SupervisedRuntimeConfig,
    _strategy_shadow_scenario,
    validate_all_strategy_scenarios,
    validate_runtime_config,
)


def test_runtime_adapter_dry_run_structurally_validates_all_10_without_live_orders_pytest() -> None:
    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="runtime-dry-run",
            mode="dry_run",
            executor_boundary=_blocked_boundary(),
        )
    )

    expected_count = len(all_strategy_specs())
    assert len(report.results) == expected_count
    assert {result.strategy_id for result in report.results} == set(strategy_registry())
    assert report.passed_count == expected_count
    assert report.blocked_count == 0
    assert report.strategy_bundle_comparison_may_begin is True
    assert report.manual_orders_avoided is True
    assert report.run_report.manual_orders_avoided is True
    assert report.run_report.summary["live_submission_attempted"] is False
    for result in report.results:
        assert result.status == "simulated_executed"
        assert result.lifecycle_covered is True
        assert result.reconciliation_status == "reconciled"
        assert result.orders_allowed is False
        assert result.live_trading_authorized is False
        assert result.intent_key is not None
        assert result.order_key is not None
        assert result.position_key is not None


def test_runtime_adapter_supervised_live_blocks_without_executor_boundary_pytest() -> None:
    blockers = validate_runtime_config(
        SupervisedRuntimeConfig(
            run_id="runtime-live-blocked",
            mode="supervised_live",
            executor_boundary=_blocked_boundary(),
        )
    )
    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="runtime-live-blocked",
            mode="supervised_live",
            executor_boundary=_blocked_boundary(),
        )
    )

    assert "executor_boundary_not_ready" in blockers
    assert "live_submission_not_allowed" in blockers
    assert "env_live_flags_missing" in blockers
    assert "credentials_access_failure" in blockers
    assert {result.status for result in report.results} == {"blocked"}
    for result in report.results:
        assert "executor_boundary_not_ready" in result.blockers
        assert "live_submission_not_allowed" in result.blockers
        assert "env_live_flags_missing" in result.blockers
        assert "credentials_access_failure" in result.blockers
        assert "supervised_executor_binding_missing" in result.blockers
        assert "live_market_candidate_not_verified" in result.blockers
        assert result.orders_allowed is False
        assert result.live_trading_authorized is False


def test_runtime_adapter_supervised_live_fake_executor_covers_all_10_pytest() -> None:
    calls = []

    def fake_executor(intent, order, scenario):
        calls.append((intent.intent_key, order.order_key, scenario.event_token_key))
        return {
            "exchange_order_id": f"fake-live:{order.order_key}",
            "status": "filled",
            "filled_shares": scenario.shares,
            "remote_filled_shares": scenario.shares,
            "fill_price": scenario.limit_price,
        }

    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="runtime-live-fake",
            mode="supervised_live",
            executor_boundary=_ready_boundary(),
            allow_live_submission=True,
            live_environment_approved=True,
            credentials_ready=True,
            supervised_executor=fake_executor,
        ),
        scenario=RuntimeScenario(
            event_key="verified-live-event",
            event_token_key="verified-live-event:up",
            live_market_verified=True,
        ),
    )

    expected_count = len(all_strategy_specs())
    assert len(calls) == expected_count
    assert report.passed_count == expected_count
    assert report.blocked_count == 0
    assert {result.status for result in report.results} == {"live_structural_executed"}
    assert report.run_report.summary["live_submission_attempted"] is True
    for result in report.results:
        assert result.orders_allowed is True
        assert result.live_trading_authorized is True
        assert result.live_submission_attempted is True
        assert result.lifecycle_covered is True
        assert result.reconciliation_status == "reconciled"
    coverage_by_strategy = {result.strategy_id: result.attribution["coverage_type"] for result in report.results}
    assert coverage_by_strategy["s_tier_outcome_consensus_cashout_v1"] == "active_cashout_order"
    assert coverage_by_strategy["s_tier_outcome_hold_to_settlement_v1"] == "hold_to_settlement_policy"
    assert coverage_by_strategy["profile_hedge_scalping_v1"] == "active_cashout_order"


def test_runtime_adapter_supervised_live_submit_error_does_not_fabricate_fill_pytest() -> None:
    def fake_submit_error(intent, order, scenario):
        return {
            "exchange_order_id": f"fake-live:{order.order_key}",
            "status": "submit_error",
            "filled_shares": 0.0,
            "remote_filled_shares": 0.0,
            "fill_price": scenario.limit_price,
        }

    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="runtime-live-submit-error",
            mode="supervised_live",
            executor_boundary=_ready_boundary(),
            allow_live_submission=True,
            live_environment_approved=True,
            credentials_ready=True,
            supervised_executor=fake_submit_error,
        ),
        scenario=RuntimeScenario(
            event_key="verified-live-event",
            event_token_key="verified-live-event:up",
            live_market_verified=True,
        ),
    )

    assert report.run_report.summary["live_submission_attempted"] is True
    assert {result.status for result in report.results} == {"blocked"}
    for result in report.results:
        assert "submit_error_no_fill" in result.blockers
        assert "reconciliation_mismatch" not in result.blockers
        assert result.position_key is None
        assert result.lifecycle_covered is False
        assert result.attribution["fill_key"] is None


def test_runtime_adapter_supervised_live_ambiguous_submit_error_stays_integrity_breaker_pytest() -> None:
    def fake_ambiguous_submit_error(intent, order, scenario):
        return {
            "exchange_order_id": f"fake-live:{order.order_key}",
            "status": "submit_error",
            "error": "read operation timed out after order submit",
            "filled_shares": 0.0,
            "remote_filled_shares": 0.0,
            "fill_price": scenario.limit_price,
        }

    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="runtime-live-ambiguous-submit-error",
            mode="supervised_live",
            executor_boundary=_ready_boundary(),
            allow_live_submission=True,
            live_environment_approved=True,
            credentials_ready=True,
            supervised_executor=fake_ambiguous_submit_error,
        ),
        scenario=RuntimeScenario(
            event_key="verified-live-event",
            event_token_key="verified-live-event:up",
            live_market_verified=True,
        ),
    )

    assert {result.status for result in report.results} == {"blocked"}
    for result in report.results:
        assert "submit_error_fill_ambiguous" in result.blockers
        assert "reconciliation_mismatch" in result.blockers
        assert result.position_key is None
        assert result.lifecycle_covered is False


def test_runtime_adapter_duplicate_cadence_blocks_all_candidates_pytest() -> None:
    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="runtime-duplicate",
            mode="dry_run",
            executor_boundary=_blocked_boundary(),
            live_cadence_count=2,
        )
    )

    assert {result.status for result in report.results} == {"blocked"}
    assert "mechanical_failure" in report.run_report.stop_gates
    for result in report.results:
        assert "duplicate_cadence" in result.blockers


def test_runtime_adapter_surfaces_missing_lifecycle_and_reconciliation_mismatch_pytest() -> None:
    missing_coverage = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="runtime-missing-coverage",
            mode="dry_run",
            executor_boundary=_blocked_boundary(),
            simulate_missing_lifecycle_coverage=True,
        )
    )
    reconciliation_mismatch = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="runtime-reconciliation-mismatch",
            mode="dry_run",
            executor_boundary=_blocked_boundary(),
            simulate_reconciliation_mismatch=True,
        )
    )

    assert {result.status for result in missing_coverage.results} == {"blocked"}
    assert {result.status for result in reconciliation_mismatch.results} == {"blocked"}
    assert "mechanical_failure" in missing_coverage.run_report.stop_gates
    assert "mechanical_failure" in reconciliation_mismatch.run_report.stop_gates
    for result in missing_coverage.results:
        assert "missing_lifecycle_coverage" in result.blockers
    for result in reconciliation_mismatch.results:
        assert "reconciliation_mismatch" in result.blockers


def test_runtime_adapter_records_candidate_data_blockers_without_order_path_pytest() -> None:
    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="runtime-missing-token",
            mode="dry_run",
            executor_boundary=_blocked_boundary(),
        ),
        scenario=RuntimeScenario(event_token_key=""),
    )

    assert {result.status for result in report.results} == {"blocked"}
    for result in report.results:
        assert "missing_event_token_key" in result.blockers
        assert result.intent_key is None
        assert result.order_key is None
        assert result.orders_allowed is False


def test_runtime_adapter_shadow_uses_fill_simulation_for_partial_fill_pytest() -> None:
    spec = strategy_registry()["event_context_outcome_v1"]
    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="runtime-shadow-partial-fill",
            mode="shadow",
            executor_boundary=_blocked_boundary(),
        ),
        scenario=RuntimeScenario(
            event_key="event-partial",
            event_token_key="event-partial:up",
            event_slug="btc-updown-5m-partial",
            outcome="Up",
            limit_price=0.51,
            liquidity_depth=10.0,
            signal_context={
                "best_bid": 0.50,
                "best_ask": 0.51,
                "mid_price": 0.505,
                "depth_top3_ask_size": 0.4,
                "system_received_at_utc": "2026-06-06T04:04:21+00:00",
                "forward_best_bid": 0.65,
                "forward_mark_price": 0.65,
            },
        ),
        specs=(spec,),
    )

    assert report.passed_count == 1
    result = report.results[0]
    assert result.status == "simulated_executed"
    assert result.order_status == "partially_filled"
    assert result.filled_shares == 0.4
    assert result.fill_price == 0.51
    assert result.attribution["signal_context"]["strategy_shadow_decision"]["adjusted_shares"] == 0.7


def test_runtime_adapter_shadow_limit_buy_does_not_fill_when_limit_is_below_ask_pytest() -> None:
    spec = strategy_registry()["event_context_outcome_v1"]
    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="runtime-shadow-limit-not-crossed",
            mode="shadow",
            executor_boundary=_blocked_boundary(),
        ),
        scenario=RuntimeScenario(
            event_key="event-maker",
            event_token_key="event-maker:up",
            event_slug="btc-updown-5m-maker",
            outcome="Up",
            limit_price=0.50,
            liquidity_depth=10.0,
            signal_context={
                "best_bid": 0.50,
                "best_ask": 0.51,
                "mid_price": 0.505,
                "system_received_at_utc": "2026-06-06T04:04:21+00:00",
                "forward_best_bid": 0.65,
                "forward_mark_price": 0.65,
            },
        ),
        specs=(spec,),
    )

    assert report.blocked_count == 1
    result = report.results[0]
    assert result.status == "blocked"
    assert result.order_status == "unfilled"
    assert result.filled_shares is None
    assert "limit_not_crossed" in result.blockers


def test_option_liquidity_micro_scalp_v5_blocks_when_friction_or_cashout_evidence_is_weak_pytest() -> None:
    spec = strategy_registry()["option_liquidity_micro_scalp_v5"]
    scenario = RuntimeScenario(
        event_key="event-friction",
        event_token_key="event-friction:up",
        event_slug="btc-updown-5m-friction",
        outcome="Up",
        limit_price=0.51,
        spread=0.02,
        liquidity_depth=90.0,
        signal_context={
            "best_bid": 0.50,
            "best_ask": 0.51,
            "forward_best_bid": 0.515,
            "forward_mark_price": 0.515,
            "option_path_ready": True,
            "option_path": {
                "snapshot_count": 10,
                "near_50c_sample_count": 3,
                "avg_rolling_60s_range": 0.021,
                "pair_sum_range": 0.01,
                "avg_pair_depth_pressure": 0.11,
                "max_source_latency_ms": 7000,
                "trade_print_count": 0,
            },
        },
    )

    adjusted, decision, blockers = _strategy_shadow_scenario(spec, scenario)

    assert adjusted.signal_context["strategy_shadow_decision"]["option_path_ready"] is True
    assert "option_path_trade_print_count_low" in blockers
    assert "option_path_source_latency_high" in blockers
    assert "option_spread_too_wide" in blockers
    assert "option_depth_top3_ask_size_low" in blockers
    assert "option_forward_cashout_edge_low" in blockers
    assert decision["required_forward_cashout_edge"] == 0.02


def test_master_paired_seed_v8_requires_balanced_profile_pressure_pytest() -> None:
    spec = strategy_registry()["master_hedge_grid_floor_paired_seed_builder_v8"]
    base_context = {
        "target_up_ratio": 0.52,
        "profile_distribution_ready": True,
        "profile_distribution": {
            "profile_count": 18,
            "source_age_seconds": 8.0,
            "coverage_warnings": [],
            "cost_vs_shares_up_gap_abs": 0.03,
            "cost_vs_profile_count_up_gap_abs": 0.04,
            "top_profile_cost_share": 0.22,
        },
        "option_path_ready": True,
        "option_path": {
            "snapshot_count": 12,
            "level_crossing_count": 5,
            "rebound_direction_flip_count": 3,
            "near_50c_sample_count": 8,
            "avg_rolling_60s_range": 0.06,
            "pair_sum_range": 0.02,
            "avg_pair_depth_pressure": 0.18,
            "tail_comeback_table": {"bucket_probabilities": {"touched_10c": {"gt180": 0.40}}},
        },
        "best_ask": 0.48,
        "best_bid": 0.47,
        "paired_down_ask": 0.52,
        "paired_down_bid": 0.51,
        "paired_path_snapshots": [
            {
                "seconds_from_entry": 0.0,
                "up_ask": 0.48,
                "up_bid": 0.47,
                "down_ask": 0.52,
                "down_bid": 0.51,
            },
            {
                "seconds_from_entry": 30.0,
                "up_ask": 0.43,
                "up_bid": 0.42,
                "down_ask": 0.57,
                "down_bid": 0.56,
            },
            {
                "seconds_from_entry": 50.0,
                "up_ask": 0.50,
                "up_bid": 0.49,
                "down_ask": 0.50,
                "down_bid": 0.49,
            },
        ],
        "paired_path_snapshot_count": 3,
    }
    balanced_scenario = RuntimeScenario(
        event_key="balanced-event",
        event_token_key="balanced-event:up",
        event_slug="btc-updown-5m-balanced",
        outcome="Up",
        limit_price=0.48,
        spread=0.01,
        liquidity_depth=120.0,
        time_remaining_seconds=240.0,
        live_market_verified=True,
        signal_context=base_context,
    )

    _, balanced_decision, balanced_blockers = _strategy_shadow_scenario(spec, balanced_scenario)

    assert "profile_distribution_pressure_not_balanced" not in balanced_blockers
    assert balanced_decision["profile_pressure_mode"] == "balanced_required"
    assert balanced_decision["profile_target_side"] == "balanced"
    assert balanced_decision["profile_confidence_status"] == "balanced"

    unbalanced_context = dict(base_context)
    unbalanced_context["target_up_ratio"] = 0.70
    unbalanced_scenario = RuntimeScenario(
        event_key="unbalanced-event",
        event_token_key="unbalanced-event:up",
        event_slug="btc-updown-5m-unbalanced",
        outcome="Up",
        limit_price=0.48,
        spread=0.01,
        liquidity_depth=120.0,
        time_remaining_seconds=240.0,
        live_market_verified=True,
        signal_context=unbalanced_context,
    )

    _, unbalanced_decision, unbalanced_blockers = _strategy_shadow_scenario(spec, unbalanced_scenario)

    assert "profile_distribution_pressure_not_balanced" in unbalanced_blockers
    assert unbalanced_decision["profile_target_side"] == "unbalanced"
    assert unbalanced_decision["profile_confidence_status"] == "unbalanced"


def test_master_paired_seed_v9_uses_profile_balance_as_optional_confidence_pytest() -> None:
    spec = strategy_registry()["master_hedge_grid_floor_paired_seed_builder_v9"]
    scenario = RuntimeScenario(
        event_key="v9-unbalanced-event",
        event_token_key="v9-unbalanced-event:up",
        event_slug="btc-updown-5m-v9-unbalanced",
        outcome="Up",
        limit_price=0.48,
        spread=0.01,
        liquidity_depth=120.0,
        time_remaining_seconds=240.0,
        live_market_verified=True,
        signal_context={
            "target_up_ratio": 0.70,
            "profile_distribution_ready": True,
            "profile_distribution": {
                "profile_count": 18,
                "source_age_seconds": 8.0,
                "coverage_warnings": [],
                "cost_vs_shares_up_gap_abs": 0.03,
                "cost_vs_profile_count_up_gap_abs": 0.04,
                "top_profile_cost_share": 0.22,
            },
            "option_path_ready": True,
            "option_path": {
                "snapshot_count": 12,
                "level_crossing_count": 6,
                "rebound_direction_flip_count": 3,
                "near_50c_sample_count": 9,
                "avg_rolling_60s_range": 0.06,
                "pair_sum_range": 0.02,
                "avg_pair_depth_pressure": 0.18,
                "tail_comeback_table": {"bucket_probabilities": {"touched_10c": {"gt180": 0.40}}},
            },
            "best_ask": 0.48,
            "best_bid": 0.47,
            "paired_down_ask": 0.52,
            "paired_down_bid": 0.51,
            "paired_path_snapshot_count": 3,
            "paired_path_snapshots": [
                {
                    "seconds_from_entry": 0.0,
                    "up_ask": 0.48,
                    "up_bid": 0.47,
                    "down_ask": 0.52,
                    "down_bid": 0.51,
                },
                {
                    "seconds_from_entry": 30.0,
                    "up_ask": 0.43,
                    "up_bid": 0.42,
                    "down_ask": 0.57,
                    "down_bid": 0.56,
                },
                {
                    "seconds_from_entry": 50.0,
                    "up_ask": 0.50,
                    "up_bid": 0.49,
                    "down_ask": 0.50,
                    "down_bid": 0.49,
                },
            ],
        },
    )

    _, decision, blockers = _strategy_shadow_scenario(spec, scenario)

    assert "profile_distribution_pressure_not_balanced" not in blockers
    assert "paired_seed_entry_ask_gap_low" not in blockers
    assert decision["profile_dependency_mode"] == "optional_confidence"
    assert decision["profile_pressure_mode"] == "balanced_required"
    assert decision["profile_confidence_status"] == "unbalanced"
    assert decision["profile_confidence_multiplier"] == 0.85
    assert decision["paired_seed_entry"]["entry_ask_gap"] == 0.04


def _blocked_boundary() -> ExecutorBoundaryConfig:
    return ExecutorBoundaryConfig(
        supervised_runtime_gate=True,
        ledger_gate=True,
        risk_gate=True,
        reconciliation_gate=True,
        execution_approved=False,
        live_risk_acknowledged=False,
    )


def _ready_boundary() -> ExecutorBoundaryConfig:
    return ExecutorBoundaryConfig(
        supervised_runtime_gate=True,
        ledger_gate=True,
        risk_gate=True,
        reconciliation_gate=True,
        execution_approved=True,
        live_risk_acknowledged=True,
    )
