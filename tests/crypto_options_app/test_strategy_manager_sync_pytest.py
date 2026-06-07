from __future__ import annotations

from pathlib import Path

from crypto_options_app.db.connection import connect, count_rows
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.db.runtime_persistence import persist_runtime_validation_report
from crypto_options_app.reports.run_report import RunReport
from crypto_options_app.strategies.manager import (
    strategy_catalog_summary,
    strategy_readiness_summary,
    strategy_replay_summary,
    strategy_validation_lab_summary,
    sync_strategy_registry,
)
from crypto_options_app.strategies.registry import all_strategy_specs, starting_strategy_ids
from crypto_options_app.workers.runtime_adapter import RuntimeStrategyResult, RuntimeValidationReport
from datetime import UTC, datetime


def test_strategy_registry_sync_persists_specs_versions_and_readiness_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-sync.sqlite")

    with connect(db_path) as conn:
        counts = sync_strategy_registry(conn)

        expected_count = len(all_strategy_specs())
        assert counts["strategy_specs"] == expected_count
        assert counts["strategy_versions"] == expected_count
        assert counts["strategy_readiness"] == expected_count * 2
        assert count_rows(conn, "strategy_specs") == expected_count
        assert count_rows(conn, "strategy_versions") == expected_count
        assert count_rows(conn, "strategy_readiness") == expected_count * 2


def test_strategy_catalog_and_readiness_summaries_are_read_only_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-summary.sqlite")

    with connect(db_path) as conn:
        catalog = strategy_catalog_summary(conn)
        readiness = strategy_readiness_summary(conn)

    assert catalog["schema_version"] == "crypto_options_strategy_catalog_v1"
    assert catalog["strategy_count"] == len(all_strategy_specs())
    assert tuple(catalog["starting_strategy_ids"]) == starting_strategy_ids()
    assert catalog["orders_allowed"] is False
    assert catalog["live_trading_authorized"] is False
    assert readiness["schema_version"] == "crypto_options_strategy_readiness_v1"
    assert readiness["strategy_count"] == len(all_strategy_specs())
    assert readiness["by_readiness_type"]["replay"]["replay_ready"] == len(all_strategy_specs())
    assert readiness["by_readiness_type"]["pulse"]["blocked"] == len(all_strategy_specs())
    first = readiness["strategies"][0]
    assert first["orders_allowed"] is False
    assert first["live_trading_authorized"] is False
    assert "executor_boundary_not_configured" in first["pulse"]["blockers"]


def test_strategy_validation_lab_summary_surfaces_budget_state_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-validation-lab.sqlite")

    with connect(db_path) as conn:
        summary = strategy_validation_lab_summary(conn)

    assert summary["schema_version"] == "crypto_options_strategy_validation_lab_v1"
    assert summary["strategy_count"] == len(all_strategy_specs())
    assert summary["strategy_validation_run_count"] == 0
    assert summary["validation_budget_ledger_count"] == 0
    assert summary["ledger_required_run_without_entry_count"] == 0
    assert summary["cash_balance_status"] == "cash_balance_unavailable"
    assert summary["supervised_live_reconciled_count"] == 0
    assert summary["supervised_live_lifecycle_pass_count"] == 0
    assert summary["supervised_live_trusted_balance_count"] == 0
    assert summary["supervised_live_cash_balance_unavailable_count"] == 0
    assert summary["supervised_live_scoped_live_flag_count"] == 0
    assert summary["repeatable_supervised_live_proof_ready"] is False
    assert summary["orders_allowed"] is False
    assert summary["live_trading_authorized"] is False


def test_strategy_replay_summary_separates_historical_and_live_replay_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-replay-summary.sqlite")
    dry_report = RuntimeValidationReport(
        run_id="pytest-dry-replay",
        mode="dry_run",
        generated_at_utc=datetime(2026, 6, 5, 9, 0, tzinfo=UTC),
        run_report=RunReport(
            run_id="pytest-dry-replay",
            generated_at_utc=datetime(2026, 6, 5, 9, 0, tzinfo=UTC),
            stop_gates=tuple(),
            risk_blockers={},
            candidate_reports=tuple(),
            summary={"max_events": 1, "max_trades_per_strategy": 1, "max_wall_time_seconds": 60},
            manual_orders_avoided=True,
        ),
        results=(
            RuntimeStrategyResult(
                strategy_id="profile_hedge_scalping_v1",
                status="simulated_executed",
                blockers=tuple(),
                event_key="event-1",
                event_token_key="event-1:up",
                candidate_key="candidate-1",
                intent_key="intent-1",
                order_key="order-1",
                position_key="position-1",
                lifecycle_covered=True,
                reconciliation_status="reconciled",
                attribution={"sources": ["pytest"]},
            ),
        ),
    )
    shadow_report = RuntimeValidationReport(
        run_id="pytest-live-replay",
        mode="shadow",
        generated_at_utc=datetime(2026, 6, 5, 9, 1, tzinfo=UTC),
        run_report=RunReport(
            run_id="pytest-live-replay",
            generated_at_utc=datetime(2026, 6, 5, 9, 1, tzinfo=UTC),
            stop_gates=tuple(),
            risk_blockers={},
            candidate_reports=tuple(),
            summary={"max_events": 1, "max_trades_per_strategy": 1, "max_wall_time_seconds": 60},
            manual_orders_avoided=True,
        ),
        results=(
            RuntimeStrategyResult(
                strategy_id="profile_hedge_scalping_v1",
                status="simulated_executed",
                blockers=tuple(),
                event_key="event-2",
                event_token_key="event-2:up",
                candidate_key="candidate-2",
                intent_key="intent-2",
                order_key="order-2",
                position_key="position-2",
                lifecycle_covered=True,
                reconciliation_status="reconciled",
                attribution={"sources": ["pytest"]},
            ),
        ),
    )
    persist_runtime_validation_report(dry_report, db_path)
    persist_runtime_validation_report(shadow_report, db_path)

    with connect(db_path) as conn:
        historical = strategy_replay_summary(conn, replay_mode="historical_backtest")
        live = strategy_replay_summary(conn, replay_mode="live_replay")

    assert historical["strategy_run_count"] == 1
    assert historical["rows"][0]["run_phase"] == "historical_replay"
    assert live["strategy_run_count"] == 1
    assert live["rows"][0]["run_phase"] == "live_replay"
    assert live["orders_allowed"] is False
    assert live["live_trading_authorized"] is False


def test_strategy_validation_lab_summary_surfaces_supervised_proof_quality_pytest(tmp_path: Path) -> None:
    db_path = initialize_schema(tmp_path / "strategy-validation-proof.sqlite")
    generated_at = datetime(2026, 6, 5, 8, 20, tzinfo=UTC)
    report = RuntimeValidationReport(
        run_id="pytest-supervised-proof",
        mode="supervised_live",
        generated_at_utc=generated_at,
        validation_budget_cap_usd=50.0,
        validation_budget_spent_usd=0.0,
        cash_balance_before_usd=140.0,
        cash_balance_hard_stop_usd=100.0,
        cash_balance_status="trusted_balance_reported",
        run_report=RunReport(
            run_id="pytest-supervised-proof",
            generated_at_utc=generated_at,
            stop_gates=tuple(),
            risk_blockers={},
            candidate_reports=tuple(),
            summary={"max_events": 1, "max_trades_per_strategy": 1, "max_wall_time_seconds": 60},
            manual_orders_avoided=True,
        ),
        results=(
            RuntimeStrategyResult(
                strategy_id="indicator_confirmed_outcome_v1",
                status="live_structural_executed",
                blockers=tuple(),
                event_key="event-1",
                event_token_key="token-1",
                token_id="token-1",
                event_slug="btc-updown-5m",
                outcome="YES",
                candidate_key="candidate-1",
                intent_key="intent-1",
                order_key="order-1",
                exchange_order_id="remote-1",
                order_status="filled",
                filled_shares=1.0,
                fill_price=0.51,
                remote_filled_shares=1.0,
                position_key="position-1",
                lifecycle_covered=True,
                reconciliation_status="reconciled",
                reconciliation_evidence={"matched": True},
                orders_allowed=True,
                live_trading_authorized=True,
                live_submission_attempted=True,
                attribution={"sources": ["signal-a"], "managed_runtime_plans": []},
            ),
        ),
    )

    persist_runtime_validation_report(report, db_path)

    with connect(db_path) as conn:
        summary = strategy_validation_lab_summary(conn)

    assert summary["strategy_validation_run_count"] == 1
    assert summary["supervised_live_run_count"] == 1
    assert summary["supervised_live_reconciled_count"] == 1
    assert summary["supervised_live_lifecycle_pass_count"] == 1
    assert summary["supervised_live_trusted_balance_count"] == 1
    assert summary["supervised_live_cash_balance_unavailable_count"] == 0
    assert summary["supervised_live_scoped_live_flag_count"] == 1
    assert summary["repeatable_supervised_live_proof_ready"] is False
    assert summary["latest_strategy_validation_run"]["scoped_live_flags"]["orders_allowed"] is True
    assert summary["latest_strategy_validation_run"]["scoped_live_flags"]["live_trading_authorized"] is True
