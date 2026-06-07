from __future__ import annotations

from pathlib import Path

from crypto_options_app.db.connection import connect, count_rows
from crypto_options_app.db.runtime_persistence import persist_runtime_validation_report
from crypto_options_app.strategies.registry import all_strategy_specs
from crypto_options_app.trading.executor_boundary import ExecutorBoundaryConfig
from crypto_options_app.trading.live_candidates import LiveMarketCandidateVerification, VerifiedLiveMarketCandidate
from crypto_options_app.trading.live_preflight import LiveEnvironmentFlags, TrustedCashBalanceSnapshot
from crypto_options_app.workers.live_minimal_validator import run_minimal_supervised_live_validation
from crypto_options_app.workers.runtime_adapter import RuntimeScenario, SupervisedRuntimeConfig, validate_all_strategy_scenarios


def test_runtime_validation_report_persists_lifecycle_rows_pytest(tmp_path: Path) -> None:
    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="persist-dry-run",
            mode="dry_run",
            executor_boundary=_blocked_boundary(),
        ),
        scenario=RuntimeScenario(event_key="event-1", event_token_key="event-1:up"),
    )

    counts = persist_runtime_validation_report(report, tmp_path / "runtime.sqlite")

    expected_count = len(all_strategy_specs())
    assert counts["strategy_validation_runs"] == expected_count
    assert counts["validation_budget_ledger"] == 0
    assert counts["strategy_candidates"] == expected_count
    assert counts["execution_intents"] == expected_count
    assert counts["orders"] == expected_count
    assert counts["fills"] == expected_count
    assert counts["positions"] == expected_count
    assert counts["exit_plans"] == expected_count
    assert counts["run_reports"] == 1
    with connect(tmp_path / "runtime.sqlite") as conn:
        assert count_rows(conn, "strategy_validation_runs") == expected_count
        phases = {
            row["run_phase"]
            for row in conn.execute("SELECT DISTINCT run_phase FROM strategy_validation_runs").fetchall()
        }
        assert phases == {"historical_replay"}
        assert count_rows(conn, "validation_budget_ledger") == 0
        assert count_rows(conn, "strategy_candidates") == expected_count
        assert count_rows(conn, "execution_intents") == expected_count
        assert count_rows(conn, "orders") == expected_count
        assert count_rows(conn, "fills") == expected_count
        assert count_rows(conn, "positions") == expected_count
        assert count_rows(conn, "exit_plans") == expected_count
        assert count_rows(conn, "run_reports") == 1


def test_live_minimal_validator_can_persist_supervised_result_pytest(tmp_path: Path) -> None:
    def fake_submitter(order_request):
        return {
            "success": True,
            "raw": {"orderID": f"exchange:{order_request['app_order_key']}"},
            "remote_order": {"id": f"exchange:{order_request['app_order_key']}", "status": "FILLED", "filledSize": "1", "price": "0.51"},
            "order_request": order_request,
        }

    result = run_minimal_supervised_live_validation(
        run_id="persist-live-minimal",
        candidate_verification=_verified_candidate(),
        credentials_ready=True,
        env_flags=LiveEnvironmentFlags(live_execute=True, execution_approved=True, risk_acknowledged=True),
        submitter=fake_submitter,
        persist_db_path=tmp_path / "live.sqlite",
        cash_balance_snapshot=TrustedCashBalanceSnapshot(balance_usd=150.0, source="pytest-balance"),
    )

    assert result.status == "live_structural_executed"
    expected_count = len(all_strategy_specs())
    with connect(tmp_path / "live.sqlite") as conn:
        assert count_rows(conn, "strategy_validation_runs") == expected_count
        assert count_rows(conn, "validation_budget_ledger") == expected_count
        assert count_rows(conn, "strategy_candidates") == expected_count
        assert count_rows(conn, "orders") == expected_count
        assert count_rows(conn, "fills") == expected_count
        assert count_rows(conn, "positions") == expected_count
        assert count_rows(conn, "run_reports") == 1
        budget = conn.execute(
            """
            SELECT validation_run_id, cash_balance_status, budget_cap_usd,
                   remaining_validation_budget_usd, cash_balance_before_usd,
                   cash_balance_after_usd
            FROM validation_budget_ledger
            ORDER BY validation_run_id
            LIMIT 1
            """
        ).fetchone()
        assert budget["validation_run_id"].startswith("validation-run:persist-live-minimal:")
        assert budget["cash_balance_status"] == "trusted_balance_reported"
        assert budget["budget_cap_usd"] == 50.0
        assert budget["remaining_validation_budget_usd"] <= 50.0
        assert budget["cash_balance_before_usd"] == 150.0
        assert budget["cash_balance_after_usd"] < 150.0
        phases = {
            row["run_phase"]
            for row in conn.execute("SELECT DISTINCT run_phase FROM strategy_validation_runs").fetchall()
        }
        assert phases == {"supervised_live"}


def test_runtime_persistence_uses_registry_strategy_versions_pytest(tmp_path: Path) -> None:
    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="persist-version-check",
            mode="dry_run",
            executor_boundary=_blocked_boundary(),
        ),
        scenario=RuntimeScenario(event_key="event-1", event_token_key="event-1:up"),
    )

    persist_runtime_validation_report(report, tmp_path / "runtime-versions.sqlite")

    with connect(tmp_path / "runtime-versions.sqlite") as conn:
        versions = {
            row["strategy_id"]: row["strategy_version"]
            for row in conn.execute(
                "SELECT strategy_id, strategy_version FROM strategy_validation_runs"
            ).fetchall()
        }

    assert versions["hedger_ratio_replication_v4"] == "v4"
    assert versions["master_hedge_grid_floor_tail_reversal_probe_v3"] == "v3"
    assert versions["master_hedge_grid_floor_tail_reversal_probe_v4"] == "v4"
    assert versions["master_hedge_grid_floor_tail_reversal_probe_v5"] == "v5"


def _verified_candidate() -> LiveMarketCandidateVerification:
    from datetime import UTC, datetime

    return LiveMarketCandidateVerification(
        True,
        VerifiedLiveMarketCandidate(
            event_key="btc-updown-5m-1",
            event_token_key="btc-updown-5m-1:up",
            token_id="token-1",
            event_slug="btc-updown-5m-1",
            outcome="Up",
            best_bid=0.5,
            best_ask=0.51,
            spread=0.01,
            ask_size=10.0,
            depth_top3_ask_size=20.0,
            observed_at_utc=datetime.now(UTC),
            quote_age_seconds=1.0,
            source="pytest",
        ),
        (),
    )


def _blocked_boundary() -> ExecutorBoundaryConfig:
    return ExecutorBoundaryConfig(
        supervised_runtime_gate=True,
        ledger_gate=True,
        risk_gate=True,
        reconciliation_gate=True,
        execution_approved=False,
        live_risk_acknowledged=False,
    )
