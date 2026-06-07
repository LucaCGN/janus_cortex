from __future__ import annotations

from crypto_options_app.reports.bundle_comparison import render_bundle_comparison_report
from crypto_options_app.strategies.registry import strategy_registry
from crypto_options_app.trading.executor_boundary import ExecutorBoundaryConfig
from crypto_options_app.workers.comparison_runner import run_bundle_comparison_readiness


def test_bundle_comparison_report_renders_all_candidates_and_live_gate_blockers_pytest() -> None:
    dry_run = run_bundle_comparison_readiness(
        run_id="bundle-report-dry-run",
        mode="dry_run",
        executor_boundary=_blocked_boundary(),
    )
    supervised_live = run_bundle_comparison_readiness(
        run_id="bundle-report-live-gate",
        mode="supervised_live",
        executor_boundary=_blocked_boundary(),
    )

    rendered = render_bundle_comparison_report(
        dry_run_result=dry_run,
        supervised_live_gate_result=supervised_live,
        generated_at_utc="2026-06-03T02:18:00Z",
    )

    assert "Dry-run/shadow strategy bundle comparison completed" in rendered
    assert "Actual supervised-live strategy comparison must not begin" in rendered
    assert "No live order path was invoked" in rendered
    assert "Supervised-live comparison may begin: no" in rendered
    assert "executor_boundary_not_ready" in rendered
    assert "live_submission_not_allowed" in rendered
    assert "50 trades" in rendered
    assert "10 events" in rendered
    for strategy_id in strategy_registry():
        assert strategy_id in rendered


def _blocked_boundary() -> ExecutorBoundaryConfig:
    return ExecutorBoundaryConfig(
        supervised_runtime_gate=True,
        ledger_gate=True,
        risk_gate=True,
        reconciliation_gate=True,
        execution_approved=False,
        live_risk_acknowledged=False,
    )
