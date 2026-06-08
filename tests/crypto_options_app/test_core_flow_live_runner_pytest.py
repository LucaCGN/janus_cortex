from __future__ import annotations

from datetime import UTC, datetime, timedelta

from crypto_options_app.trading.live_candidates import verify_live_market_candidate
from crypto_options_app.workers.core_flow_live_runner import (
    CoreFlowLiveRunConfig,
    _projected_minimal_event_cost,
    run_core_flow_live_test,
)


def _future_event_end() -> str:
    return (datetime.now(UTC) + timedelta(minutes=10)).isoformat()


def test_core_flow_live_runner_runs_three_event_cycles_with_fake_submitter_pytest(tmp_path):
    calls = []
    events = iter(["event-a", "event-b", "event-c"])

    def candidate_provider(_processed, _config):
        event = next(events)
        return verify_live_market_candidate(
            {
                "token_id": f"token-{event}",
                "event_key": event,
                "event_token_key": f"{event}:up",
                "event_slug": event,
                "outcome": "Up",
                "best_bid": 0.50,
                "best_ask": 0.51,
                "spread": 0.01,
                "ask_size": 20.0,
                "depth_top3_ask_size": 30.0,
                "observed_at_utc": datetime.now(UTC).isoformat(),
                "event_end_time_utc": _future_event_end(),
            }
        )

    def submitter(order_request):
        calls.append(order_request)
        return {
            "success": True,
            "raw": {"orderID": f"exchange-{len(calls)}"},
            "remote_order": {
                "id": f"exchange-{len(calls)}",
                "status": "FILLED",
                "filledSize": "1",
                "price": str(order_request["price"]),
            },
            "order_request": order_request,
        }

    result = run_core_flow_live_test(
        CoreFlowLiveRunConfig(
            run_id="pytest-core-flow",
            db_path=tmp_path / "db.sqlite",
            artifact_root=tmp_path / "artifacts",
            max_event_cycles=3,
            total_budget_cap_usd=10.0,
            poll_seconds=0.01,
        ),
        candidate_provider=candidate_provider,
        submitter=submitter,
        sleep=lambda _seconds: None,
    )

    assert result.status == "validated"
    assert len(result.event_results) == 3
    assert len(calls) == 9
    assert result.estimated_spent_usd == 4.59
    assert not result.blockers


def test_core_flow_live_runner_stops_on_runtime_breaker_pytest(tmp_path):
    def candidate_provider(_processed, _config):
        return verify_live_market_candidate(
            {
                "token_id": "token-a",
                "event_key": "event-a",
                "event_token_key": "event-a:up",
                "event_slug": "event-a",
                "outcome": "Up",
                "best_bid": 0.50,
                "best_ask": 0.51,
                "spread": 0.01,
                "ask_size": 20.0,
                "depth_top3_ask_size": 30.0,
                "observed_at_utc": datetime.now(UTC).isoformat(),
                "event_end_time_utc": _future_event_end(),
            }
        )

    result = run_core_flow_live_test(
        CoreFlowLiveRunConfig(
            run_id="pytest-core-flow-breaker",
            db_path=tmp_path / "db.sqlite",
            artifact_root=tmp_path / "artifacts",
            max_event_cycles=3,
            poll_seconds=0.01,
        ),
        candidate_provider=candidate_provider,
        submitter=lambda _request: {"success": False, "status": "submit_error"},
        sleep=lambda _seconds: None,
    )

    assert result.status == "blocked"
    assert len(result.event_results) == 1
    assert "submit_error_no_fill" in result.blockers


def test_core_flow_projection_accounts_for_exchange_minimum_notional_pytest():
    verification = verify_live_market_candidate(
        {
            "token_id": "token-low",
            "event_key": "event-low",
            "event_token_key": "event-low:up",
            "event_slug": "event-low",
            "outcome": "Up",
            "best_bid": 0.19,
            "best_ask": 0.20,
            "spread": 0.01,
            "ask_size": 20.0,
            "depth_top3_ask_size": 30.0,
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "event_end_time_utc": _future_event_end(),
        }
    )

    assert round(_projected_minimal_event_cost(verification, strategy_count=3), 6) == 3.3


def test_core_flow_runner_blocks_before_budget_overshoot_from_minimum_notional_pytest(tmp_path):
    events = iter(
        [
            ("event-a", 0.20),
            ("event-b", 0.50),
            ("event-c", 0.49),
        ]
    )

    def candidate_provider(_processed, _config):
        event, ask = next(events)
        return verify_live_market_candidate(
            {
                "token_id": f"token-{event}",
                "event_key": event,
                "event_token_key": f"{event}:up",
                "event_slug": event,
                "outcome": "Up",
                "best_bid": round(ask - 0.01, 2),
                "best_ask": ask,
                "spread": 0.01,
                "ask_size": 20.0,
                "depth_top3_ask_size": 30.0,
                "observed_at_utc": datetime.now(UTC).isoformat(),
                "event_end_time_utc": _future_event_end(),
            }
        )

    def submitter(order_request):
        price = float(order_request["price"])
        shares = 5.0 if price == 0.20 else 3.0
        return {
            "success": True,
            "raw": {"orderID": f"exchange:{order_request['app_order_key']}"},
            "execution_quality": {
                "realized_price": price,
                "filled_shares": shares,
            },
            "remote_order": {
                "id": f"exchange:{order_request['app_order_key']}",
                "status": "FILLED",
                "filledSize": str(shares),
                "price": str(price),
            },
            "order_request": order_request,
        }

    result = run_core_flow_live_test(
        CoreFlowLiveRunConfig(
            run_id="pytest-budget-projection",
            db_path=tmp_path / "db.sqlite",
            artifact_root=tmp_path / "artifacts",
            max_event_cycles=3,
            total_budget_cap_usd=10.0,
            poll_seconds=0.01,
        ),
        candidate_provider=candidate_provider,
        submitter=submitter,
        sleep=lambda _seconds: None,
    )

    assert result.status == "blocked"
    assert result.estimated_spent_usd == 7.5
    assert len(result.event_results) == 2
    assert result.blockers == ("core_flow_budget_cap_would_be_exceeded",)
