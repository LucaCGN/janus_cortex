from __future__ import annotations

from datetime import UTC, datetime

from crypto_options_app.trading.live_candidates import verify_live_market_candidate
from crypto_options_app.trading.live_preflight import LiveEnvironmentFlags, TrustedCashBalanceSnapshot
from crypto_options_app.strategies.registry import all_strategy_specs
from crypto_options_app.workers.live_minimal_validator import run_minimal_supervised_live_validation, run_minimal_supervised_live_validation_batch


def test_minimal_live_validator_blocks_before_executor_when_env_flags_are_off_pytest() -> None:
    calls = []
    result = run_minimal_supervised_live_validation(
        run_id="minimal-live-blocked",
        candidate_verification=_verified_candidate(),
        credentials_ready=True,
        env_flags=LiveEnvironmentFlags(False, False, False),
        submitter=lambda order_request: calls.append(order_request) or {"success": True, "raw": {"orderID": "should-not-call"}},
    )

    assert result.status == "blocked"
    assert result.runtime_report is None
    assert result.live_submission_attempted is False
    assert calls == []
    assert "env_live_flags_missing" in result.blockers
    assert "executor_boundary_not_ready" in result.blockers


def test_minimal_live_validator_executes_all_10_with_fake_submitter_when_all_gates_ready_pytest() -> None:
    calls = []

    def fake_submitter(order_request):
        calls.append(order_request)
        return {"success": True, "status": "submitted", "raw": {"orderID": f"exchange-{len(calls)}"}, "order_request": order_request}

    result = run_minimal_supervised_live_validation(
        run_id="minimal-live-ready",
        candidate_verification=_verified_candidate(),
        credentials_ready=True,
        env_flags=LiveEnvironmentFlags(True, True, True),
        submitter=fake_submitter,
    )

    assert result.status == "live_structural_executed"
    assert result.preflight.live_submission_permitted is True
    assert result.runtime_report is not None
    expected_count = len(all_strategy_specs())
    assert result.runtime_report.passed_count == expected_count
    assert result.live_submission_attempted is True
    assert len(calls) == expected_count
    assert {request["token_id"] for request in calls} == {"token-123"}


def test_minimal_live_batch_validates_each_strategy_once_with_fake_submitter_pytest() -> None:
    calls = []

    def fake_submitter(order_request):
        calls.append(order_request)
        return {"success": True, "status": "submitted", "raw": {"orderID": f"exchange-{len(calls)}"}, "order_request": order_request}

    result = run_minimal_supervised_live_validation_batch(
        run_id="minimal-live-batch",
        candidate_verifications=(_verified_candidate(), _verified_candidate_for(token_id="token-456", event_suffix="down")),
        credentials_ready=True,
        env_flags=LiveEnvironmentFlags(True, True, True),
        submitter=fake_submitter,
    )

    assert result.live_submission_attempted is True
    expected_count = len(all_strategy_specs())
    assert len(result.results) == expected_count
    assert len(calls) == expected_count
    assert set(result.blockers_by_strategy.values()) == {()}
    assert {request["token_id"] for request in calls} == {"token-123", "token-456"}


def test_minimal_live_validator_blocks_before_executor_on_cash_balance_hard_stop_pytest() -> None:
    calls = []
    result = run_minimal_supervised_live_validation(
        run_id="minimal-live-balance-stop",
        candidate_verification=_verified_candidate(),
        credentials_ready=True,
        env_flags=LiveEnvironmentFlags(True, True, True),
        submitter=lambda order_request: calls.append(order_request) or {"success": True, "raw": {"orderID": "should-not-call"}},
        cash_balance_snapshot=TrustedCashBalanceSnapshot(balance_usd=100.0, source="pytest-balance"),
    )

    assert result.status == "blocked"
    assert result.runtime_report is None
    assert result.live_submission_attempted is False
    assert calls == []
    assert "cash_balance_hard_stop_breach" in result.blockers


def _verified_candidate():
    return _verified_candidate_for()


def _verified_candidate_for(*, token_id: str = "token-123", event_suffix: str = "up"):
    return verify_live_market_candidate(
        {
            "token_id": token_id,
            "event_key": "btc-updown-5m-1780455300",
            "event_token_key": f"btc-updown-5m-1780455300:{event_suffix}",
            "event_slug": "btc-updown-5m-1780455300",
            "outcome": "Up",
            "best_bid": 0.5,
            "best_ask": 0.51,
            "spread": 0.01,
            "ask_size": 10.0,
            "depth_top3_ask_size": 30.0,
            "observed_at_utc": "2026-06-03T03:05:00Z",
        },
        now_utc=datetime(2026, 6, 3, 3, 5, 1, tzinfo=UTC),
    )
