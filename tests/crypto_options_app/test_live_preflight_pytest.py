from __future__ import annotations

from datetime import UTC, datetime

from crypto_options_app.trading.executor_boundary import ExecutorBoundaryConfig
from crypto_options_app.trading.live_candidates import verify_live_market_candidate
from crypto_options_app.trading.live_preflight import (
    LIVE_APPROVED_FLAG,
    LIVE_EXECUTE_FLAG,
    LIVE_RISK_ACK_FLAG,
    LiveEnvironmentFlags,
    TrustedCashBalanceSnapshot,
    evaluate_supervised_live_preflight,
    read_live_environment_flags,
)


def test_live_preflight_blocks_when_live_executor_requirements_are_missing_pytest() -> None:
    verification = verify_live_market_candidate(
        _live_candidate_row(),
        now_utc=datetime(2026, 6, 3, 2, 50, 0, tzinfo=UTC),
    )

    preflight = evaluate_supervised_live_preflight(
        executor_boundary=_blocked_boundary(),
        candidate_verification=verification,
        supervised_executor_bound=False,
        credentials_ready=False,
        env_flags=LiveEnvironmentFlags(False, False, False),
    )

    assert preflight.status == "blocked"
    assert preflight.live_submission_permitted is False
    assert preflight.manual_orders_avoided is True
    assert "executor_boundary_not_ready" in preflight.blockers
    assert "env_live_flags_missing" in preflight.blockers
    assert "credentials_access_failure" in preflight.blockers
    assert "supervised_executor_binding_missing" in preflight.blockers
    assert "live_market_candidate_not_verified" not in preflight.blockers


def test_live_preflight_blocks_unverified_market_candidate_even_when_other_gates_ready_pytest() -> None:
    verification = verify_live_market_candidate(
        {**_live_candidate_row(), "observed_at_utc": "2026-06-03T02:49:00Z"},
        now_utc=datetime(2026, 6, 3, 2, 50, 0, tzinfo=UTC),
    )

    preflight = evaluate_supervised_live_preflight(
        executor_boundary=_ready_boundary(),
        candidate_verification=verification,
        supervised_executor_bound=True,
        credentials_ready=True,
        env_flags=LiveEnvironmentFlags(True, True, True),
    )

    assert preflight.status == "blocked"
    assert preflight.live_market_verified is False
    assert "live_market_candidate_not_verified" in preflight.blockers
    assert "stale_quote" in preflight.blockers


def test_live_preflight_permits_only_when_all_supervised_live_gates_are_ready_pytest() -> None:
    verification = verify_live_market_candidate(
        _live_candidate_row(),
        now_utc=datetime(2026, 6, 3, 2, 50, 0, tzinfo=UTC),
    )

    preflight = evaluate_supervised_live_preflight(
        executor_boundary=_ready_boundary(),
        candidate_verification=verification,
        supervised_executor_bound=True,
        credentials_ready=True,
        env_flags=LiveEnvironmentFlags(True, True, True),
    )

    assert preflight.status == "ready"
    assert preflight.blockers == ()
    assert preflight.live_submission_permitted is True
    assert preflight.executor_boundary_ready is True
    assert preflight.supervised_executor_bound is True


def test_read_live_environment_flags_maps_required_env_names_pytest() -> None:
    flags = read_live_environment_flags(
        {
            LIVE_EXECUTE_FLAG: "1",
            LIVE_APPROVED_FLAG: "0",
            LIVE_RISK_ACK_FLAG: "1",
        }
    )

    assert flags.ready is False
    assert flags.live_execute is True
    assert flags.execution_approved is False
    assert flags.risk_acknowledged is True
    assert flags.missing == (LIVE_APPROVED_FLAG,)


def test_live_preflight_blocks_when_trusted_cash_balance_hits_hard_stop_pytest() -> None:
    verification = verify_live_market_candidate(
        _live_candidate_row(),
        now_utc=datetime(2026, 6, 3, 2, 50, 0, tzinfo=UTC),
    )

    preflight = evaluate_supervised_live_preflight(
        executor_boundary=_ready_boundary(),
        candidate_verification=verification,
        supervised_executor_bound=True,
        credentials_ready=True,
        env_flags=LiveEnvironmentFlags(True, True, True),
        cash_balance_snapshot=TrustedCashBalanceSnapshot(balance_usd=100.0, source="pytest-balance"),
        projected_order_notional_usd=0.5,
    )

    assert preflight.status == "blocked"
    assert preflight.cash_balance_status == "trusted_balance_reported"
    assert preflight.cash_balance_before_usd == 100.0
    assert "cash_balance_hard_stop_breach" in preflight.blockers


def _live_candidate_row() -> dict[str, object]:
    return {
        "token_id": "123",
        "event_key": "btc-updown-5m-1780455000",
        "event_token_key": "btc-updown-5m-1780455000:up",
        "best_bid": 0.49,
        "best_ask": 0.5,
        "spread": 0.01,
        "ask_size": 10.0,
        "depth_top3_ask_size": 30.0,
        "observed_at_utc": "2026-06-03T02:49:58Z",
    }


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
