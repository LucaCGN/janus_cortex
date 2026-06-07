from __future__ import annotations

from crypto_options_app.reports.run_report import build_run_report
from crypto_options_app.reports.strategy_report import build_candidate_attribution_report
from crypto_options_app.reports.system_status import build_system_status_snapshot, detect_duplicate_cadence
from crypto_options_app.risk.exposure import compute_exposure_snapshot
from crypto_options_app.risk.gates import CandidateRiskContext, RiskLimits, evaluate_risk_gates
from crypto_options_app.risk.stop_gates import RunPerformance, evaluate_stop_gates


def test_risk_gate_pass_and_fail_cases_pytest() -> None:
    limits = RiskLimits()
    passing = CandidateRiskContext(
        notional_usd=5,
        active_cost_usd=10,
        event_exposure_usd=5,
        same_side_exposure_usd=4,
        correlated_exposure_usd=10,
        spread=0.02,
        quote_age_seconds=1,
        profile_age_seconds=30,
        signal_age_seconds=10,
        expected_slippage=0.01,
        liquidity_depth=10,
        time_remaining_seconds=180,
    )
    failing = CandidateRiskContext(
        notional_usd=20,
        active_cost_usd=45,
        event_exposure_usd=19,
        same_side_exposure_usd=14,
        correlated_exposure_usd=29,
        spread=0.20,
        quote_age_seconds=10,
        profile_age_seconds=1000,
        signal_age_seconds=120,
        expected_slippage=0.20,
        liquidity_depth=0,
        time_remaining_seconds=30,
        has_event_token_key=False,
        has_lifecycle_coverage=False,
    )

    pass_result = evaluate_risk_gates(passing, limits)
    fail_result = evaluate_risk_gates(failing, limits)

    assert pass_result.passed is True
    assert pass_result.orders_allowed is False
    assert fail_result.passed is False
    assert set(fail_result.blockers) >= {
        "missing_event_token_key",
        "missing_lifecycle_coverage",
        "active_cost_cap",
        "event_exposure_cap",
        "same_side_exposure_cap",
        "correlated_exposure_cap",
        "spread_too_wide",
        "stale_quote",
        "stale_profile_data",
        "stale_signal_data",
        "slippage_too_high",
        "insufficient_liquidity",
        "final_minute_entry_block",
    }


def test_stop_gate_trigger_cases_pytest() -> None:
    trade_cap = evaluate_stop_gates(RunPerformance(submitted_entry_groups=100))
    severe = evaluate_stop_gates(RunPerformance(closed_entry_groups=20, wins=5, pnl_usd=-45))
    giveback = evaluate_stop_gates(RunPerformance(closed_entry_groups=20, wins=15, pnl_usd=5, peak_pnl_usd=30))
    mechanical = evaluate_stop_gates(RunPerformance(mechanical_failures=("missing_lifecycle_coverage",)))
    stale = evaluate_stop_gates(RunPerformance(stale_service_seconds=301, no_valid_candidate_seconds=300))

    assert trade_cap.triggered_gates == ("trade_cap",)
    assert "severe_failure" in severe.triggered_gates
    assert "profit_giveback" in giveback.triggered_gates
    assert mechanical.triggered_gates == ("mechanical_failure",)
    assert set(stale.triggered_gates) == {"stale_service", "no_valid_candidate_report_gate"}


def test_exposure_snapshot_rolls_up_active_cost_by_event_side_and_correlation_pytest() -> None:
    snapshot = compute_exposure_snapshot(
        strategy_id="strategy-1",
        positions=[
            {"status": "open", "event_key": "event-1", "event_token_key": "event-1:up", "side": "Up", "cost_basis_usd": 5.0, "correlated_key": "btc-1m"},
            {"status": "open", "event_key": "event-1", "event_token_key": "event-1:down", "side": "Down", "cost_basis_usd": 4.0, "correlated_key": "btc-1m"},
            {"status": "closed", "event_key": "event-2", "side": "Up", "cost_basis_usd": 100.0},
        ],
    )

    assert snapshot.active_cost_usd == 9.0
    assert snapshot.event_exposure_usd == {"event-1": 9.0}
    assert snapshot.same_side_exposure_usd == {"event-1:Up": 5.0, "event-1:Down": 4.0}
    assert snapshot.correlated_exposure_usd == {"btc-1m": 9.0}
    assert snapshot.open_position_count == 2


def test_stale_service_duplicate_cadence_and_status_snapshot_pytest() -> None:
    assert detect_duplicate_cadence(["python service.py", "python service.py"], service_marker="service.py") is True
    snapshot = build_system_status_snapshot(
        run_id="run-1",
        service_alive=True,
        service_staleness_seconds=301,
        process_commands=["python run_crypto_options.py", "python run_crypto_options.py"],
        service_marker="run_crypto_options.py",
        feed_watermarks={"profile_activity": {"status": "healthy"}},
        candidate_counts={"ready": 1, "blocked": 2, "executed": 0},
        blockers_by_strategy={"strategy-1": ["stale_quote"]},
        pnl_usd=-1,
        active_cost_usd=5,
    )

    assert snapshot.live_cadence_count == 2
    assert set(snapshot.errors) == {"duplicate_live_cadence", "stale_service"}
    assert snapshot.manual_orders_avoided is True


def test_report_attribution_and_blocker_counts_pytest() -> None:
    strategy_report = build_candidate_attribution_report(
        strategy_id="strategy-1",
        candidate_rows=[
            {"status": "ready", "sources": ["profile:a"]},
            {"status": "blocked", "blockers": ["stale_quote"], "sources": ["profile:b"]},
            {"status": "blocked", "blockers": ["stale_quote", "missing_lifecycle_coverage"]},
            {"status": "executed", "sources": ["profile:a"]},
        ],
    )
    run_report = build_run_report(
        run_id="run-1",
        stop_gates=("mechanical_failure",),
        candidate_reports=[strategy_report],
        summary={"pnl_usd": -1.0},
    )

    assert strategy_report.ready_count == 1
    assert strategy_report.blocked_count == 2
    assert strategy_report.executed_count == 1
    assert strategy_report.blocker_counts == {"stale_quote": 2, "missing_lifecycle_coverage": 1}
    assert strategy_report.source_counts == {"profile:a": 2, "profile:b": 1}
    assert run_report.risk_blockers["stale_quote"] == 2
    assert run_report.manual_orders_avoided is True
