from __future__ import annotations

from datetime import UTC, datetime

from crypto_options_app.trading.executor_boundary import ExecutorBoundaryConfig
from crypto_options_app.trading.polymarket_supervised_executor import (
    PolymarketSupervisedExecutorConfig,
    build_polymarket_order_request,
    build_polymarket_supervised_executor,
    normalize_polymarket_submission,
    reconcile_ambiguous_submission_from_recent_trades,
)
from crypto_options_app.strategies.registry import all_strategy_specs
from crypto_options_app.workers.runtime_adapter import RuntimeScenario, SupervisedRuntimeConfig, validate_all_strategy_scenarios


def test_polymarket_supervised_executor_builds_legacy_request_without_submitting_in_test_pytest() -> None:
    captured_requests = []

    def fake_submitter(order_request):
        captured_requests.append(order_request)
        return {
            "success": True,
            "status": "submitted",
            "raw": {"orderID": "exchange-123"},
            "order_request": order_request,
        }

    executor = build_polymarket_supervised_executor(
        PolymarketSupervisedExecutorConfig(operator="codex-automation", reason="minimal structural validation"),
        submitter=fake_submitter,
    )
    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="runtime-polymarket-binding",
            mode="supervised_live",
            executor_boundary=_ready_boundary(),
            allow_live_submission=True,
            live_environment_approved=True,
            credentials_ready=True,
            supervised_executor=executor,
        ),
        scenario=_verified_scenario(),
    )

    assert len(captured_requests) == len(all_strategy_specs())
    assert report.run_report.summary["live_submission_attempted"] is True
    assert report.blocked_count == 0
    assert {result.status for result in report.results} == {"live_structural_executed"}
    assert {request["token_id"] for request in captured_requests} == {"token-123"}
    assert all(request["schema_version"] == "crypto_options_app_supervised_order_request_v1" for request in captured_requests)
    assert all(request["max_position_cost_usd"] == 5.0 for request in captured_requests)
    assert all(request["min_order_notional_usd"] == 1.0 for request in captured_requests)


def test_polymarket_supervised_executor_failed_submitter_does_not_create_position_pytest() -> None:
    executor = build_polymarket_supervised_executor(
        PolymarketSupervisedExecutorConfig(operator="codex-automation", reason="minimal structural validation"),
        submitter=lambda order_request: {
            "success": False,
            "status": "blocked_asset_check",
            "order_request": order_request,
        },
    )
    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="runtime-polymarket-binding-failed",
            mode="supervised_live",
            executor_boundary=_ready_boundary(),
            allow_live_submission=True,
            live_environment_approved=True,
            credentials_ready=True,
            supervised_executor=executor,
        ),
        scenario=_verified_scenario(),
    )

    assert {result.status for result in report.results} == {"blocked"}
    for result in report.results:
        assert result.position_key is None
        assert result.attribution["fill_key"] is None
        assert "submit_error_no_fill" in result.blockers


def test_polymarket_supervised_executor_blocks_before_submit_when_clob_under_maintenance_pytest() -> None:
    captured_requests = []

    def fake_submitter(order_request):
        captured_requests.append(order_request)
        return {"success": True, "status": "submitted", "order_request": order_request}

    executor = build_polymarket_supervised_executor(
        PolymarketSupervisedExecutorConfig(
            operator="codex-automation",
            reason="minimal structural validation",
            status_provider=lambda: {
                "schema_version": "polymarket_status_v1",
                "status": "maintenance",
                "clob_api": {"status": "undermaintenance", "trading_available": False},
                "blockers": ["exchange_status_not_operational", "polymarket_active_maintenance"],
            },
        ),
        submitter=fake_submitter,
    )
    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="runtime-polymarket-maintenance-gate",
            mode="supervised_live",
            executor_boundary=_ready_boundary(),
            allow_live_submission=True,
            live_environment_approved=True,
            credentials_ready=True,
            supervised_executor=executor,
        ),
        scenario=_verified_scenario(),
    )

    assert captured_requests == []
    assert {result.status for result in report.results} == {"blocked"}
    for result in report.results:
        assert result.order_status == "unfilled"
        assert "exchange_status_not_operational" in result.blockers
        assert "reconciliation_mismatch" not in result.blockers
        assert result.position_key is None
        assert result.lifecycle_covered is False


def test_polymarket_supervised_executor_can_use_verified_market_when_status_page_unavailable_pytest() -> None:
    captured_requests = []

    def fake_submitter(order_request):
        captured_requests.append(order_request)
        return {
            "success": True,
            "status": "submitted",
            "raw": {"orderID": "exchange-status-fallback"},
            "remote_order": {
                "id": "exchange-status-fallback",
                "status": "FILLED",
                "filledSize": "1",
                "price": str(order_request["price"]),
            },
            "order_request": order_request,
        }

    executor = build_polymarket_supervised_executor(
        PolymarketSupervisedExecutorConfig(
            operator="codex-automation",
            reason="minimal structural validation",
            status_provider=lambda: {
                "schema_version": "polymarket_status_v1",
                "status": "unknown",
                "clob_api": {"status": "unknown", "trading_available": False},
                "blockers": ["polymarket_status_unavailable"],
            },
            allow_status_unavailable_with_verified_market=True,
        ),
        submitter=fake_submitter,
    )
    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="runtime-polymarket-status-fallback",
            mode="supervised_live",
            executor_boundary=_ready_boundary(),
            allow_live_submission=True,
            live_environment_approved=True,
            credentials_ready=True,
            supervised_executor=executor,
        ),
        scenario=_verified_scenario(),
    )

    assert captured_requests
    assert all(request["status_fallback_used"] is True for request in captured_requests)
    assert {result.status for result in report.results} == {"live_structural_executed"}
    assert report.blocked_count == 0


def test_polymarket_supervised_executor_can_use_fresh_quote_when_status_times_out_pytest() -> None:
    captured_requests = []

    def fake_submitter(order_request):
        captured_requests.append(order_request)
        return {
            "success": True,
            "status": "submitted",
            "raw": {"orderID": "exchange-status-timeout-fallback"},
            "remote_order": {
                "id": "exchange-status-timeout-fallback",
                "status": "FILLED",
                "filledSize": "1",
                "price": str(order_request["price"]),
            },
            "order_request": order_request,
        }

    executor = build_polymarket_supervised_executor(
        PolymarketSupervisedExecutorConfig(
            operator="codex-automation",
            reason="minimal structural validation",
            status_provider=lambda: {
                "schema_version": "polymarket_status_v1",
                "status": "unknown",
                "clob_api": {"status": "unknown", "trading_available": False},
                "blockers": ["polymarket_status_timeout"],
            },
            allow_status_unavailable_with_verified_market=True,
        ),
        submitter=fake_submitter,
    )
    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="runtime-polymarket-status-timeout-fallback",
            mode="supervised_live",
            executor_boundary=_ready_boundary(),
            allow_live_submission=True,
            live_environment_approved=True,
            credentials_ready=True,
            supervised_executor=executor,
        ),
        scenario=_verified_scenario(quote_age_seconds=0.0),
    )

    assert captured_requests
    assert all(request["status_fallback_used"] is True for request in captured_requests)
    assert {result.status for result in report.results} == {"live_structural_executed"}
    assert report.blocked_count == 0


def test_polymarket_supervised_executor_status_fallback_does_not_mask_maintenance_pytest() -> None:
    captured_requests = []
    executor = build_polymarket_supervised_executor(
        PolymarketSupervisedExecutorConfig(
            operator="codex-automation",
            reason="minimal structural validation",
            status_provider=lambda: {
                "schema_version": "polymarket_status_v1",
                "status": "maintenance",
                "clob_api": {"status": "undermaintenance", "trading_available": False},
                "blockers": ["exchange_status_not_operational", "polymarket_active_maintenance"],
            },
            allow_status_unavailable_with_verified_market=True,
        ),
        submitter=lambda order_request: captured_requests.append(order_request) or {"success": True},
    )
    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id="runtime-polymarket-status-fallback-maintenance",
            mode="supervised_live",
            executor_boundary=_ready_boundary(),
            allow_live_submission=True,
            live_environment_approved=True,
            credentials_ready=True,
            supervised_executor=executor,
        ),
        scenario=_verified_scenario(),
    )

    assert captured_requests == []
    assert {result.status for result in report.results} == {"blocked"}
    assert all("polymarket_active_maintenance" in result.blockers for result in report.results)


def test_normalize_polymarket_submission_uses_remote_order_fill_evidence_pytest() -> None:
    from crypto_options_app.strategies.registry import get_strategy
    from crypto_options_app.trading.intents import create_execution_intent
    from crypto_options_app.trading.orders import create_order_from_intent

    spec = get_strategy("s_tier_outcome_consensus_cashout_v1")
    intent = create_execution_intent(
        candidate_key="candidate-1",
        strategy_id=spec.strategy_id,
        run_id="run-1",
        event_key="btc-updown-5m-1780455300",
        event_token_key="btc-updown-5m-1780455300:up",
        side="BUY",
        intent_type="entry",
        order_type="limit_buy",
        shares=1.0,
        limit_price=0.51,
        decision_at_utc=datetime(2026, 6, 3, 3, 0, 0, tzinfo=UTC),
        source_attribution=("outcome_expectation",),
    )
    order = create_order_from_intent(intent)

    normalized = normalize_polymarket_submission(
        {
            "success": True,
            "raw": {"orderID": "exchange-123"},
            "remote_order": {"id": "exchange-123", "status": "FILLED", "filledSize": "2.5", "price": "0.51"},
        },
        fallback_order=order,
        fallback_price=0.51,
    )

    assert normalized["exchange_order_id"] == "exchange-123"
    assert normalized["status"] == "filled"
    assert normalized["filled_shares"] == 2.5
    assert normalized["remote_filled_shares"] == 2.5
    assert normalized["fill_price"] == 0.51


def test_normalize_polymarket_submission_prefers_realized_execution_quality_pytest() -> None:
    from crypto_options_app.strategies.registry import get_strategy
    from crypto_options_app.trading.intents import create_execution_intent
    from crypto_options_app.trading.orders import create_order_from_intent

    spec = get_strategy("s_tier_outcome_consensus_cashout_v1")
    intent = create_execution_intent(
        candidate_key="candidate-1",
        strategy_id=spec.strategy_id,
        run_id="run-1",
        event_key="btc-updown-5m-1780455300",
        event_token_key="btc-updown-5m-1780455300:up",
        side="BUY",
        intent_type="entry",
        order_type="limit_buy",
        shares=1.0,
        limit_price=0.47,
        decision_at_utc=datetime(2026, 6, 3, 3, 0, 0, tzinfo=UTC),
        source_attribution=("outcome_expectation",),
    )
    order = create_order_from_intent(intent)

    normalized = normalize_polymarket_submission(
        {
            "success": True,
            "raw": {"orderID": "0xabc1234567890"},
            "remote_order": {"id": "0xabc1234567890", "status": "FILLED", "filledSize": "3.133332", "price": "0.47"},
            "execution_quality": {
                "realized_price": 0.45,
                "filled_shares": 3.133332,
                "filled_notional_usd": 1.409999,
            },
        },
        fallback_order=order,
        fallback_price=0.47,
    )

    assert normalized["exchange_order_id"] == "0xabc1234567890"
    assert normalized["status"] == "filled"
    assert normalized["filled_shares"] == 3.133332
    assert normalized["fill_price"] == 0.45


def test_reconcile_ambiguous_submission_from_recent_trades_recovers_timeout_fill_pytest() -> None:
    submission = {
        "success": False,
        "status": "submit_error",
        "error": "The read operation timed out",
    }
    order_request = {
        "token_id": "token-123",
        "side": "BUY",
        "price": 0.33,
        "size": 4.551723,
    }

    enriched = reconcile_ambiguous_submission_from_recent_trades(
        submission,
        order_request=order_request,
        trade_provider=lambda: [
            {
                "id": "trade-1",
                "taker_order_id": "0xrecovered1234567890",
                "asset_id": "token-123",
                "side": "BUY",
                "price": 0.29,
                "size": 4.551723,
            }
        ],
    )

    assert enriched["success"] is True
    assert enriched["status"] == "submitted"
    assert enriched["exchange_order_id"] == "0xrecovered1234567890"
    assert enriched["filled_shares"] == 4.551723
    assert enriched["fill_price"] == 0.29
    assert enriched["post_error_reconciliation"]["status"] == "filled_trade_found"


def test_build_polymarket_order_request_uses_verified_scenario_token_identity_pytest() -> None:
    from crypto_options_app.strategies.registry import get_strategy
    from crypto_options_app.trading.intents import create_execution_intent
    from crypto_options_app.trading.orders import create_order_from_intent

    spec = get_strategy("s_tier_outcome_consensus_cashout_v1")
    intent = create_execution_intent(
        candidate_key="candidate-1",
        strategy_id=spec.strategy_id,
        run_id="run-1",
        event_key="btc-updown-5m-1780455300",
        event_token_key="btc-updown-5m-1780455300:up",
        side="BUY",
        intent_type="entry",
        order_type="limit_buy",
        shares=1.0,
        limit_price=0.51,
        decision_at_utc=datetime(2026, 6, 3, 3, 0, 0, tzinfo=UTC),
        source_attribution=("outcome_expectation",),
    )
    order_request = build_polymarket_order_request(
        intent=intent,
        order=create_order_from_intent(intent),
        scenario=_verified_scenario(),
        config=PolymarketSupervisedExecutorConfig(operator="codex", reason="test"),
    )

    assert order_request["token_id"] == "token-123"
    assert order_request["event_slug"] == "btc-updown-5m-1780455300"
    assert order_request["outcome"] == "Up"
    assert order_request["side"] == "BUY"
    assert order_request["price"] == 0.51


def _verified_scenario(**overrides) -> RuntimeScenario:
    payload = {
        "event_key": "btc-updown-5m-1780455300",
        "event_token_key": "btc-updown-5m-1780455300:up",
        "token_id": "token-123",
        "event_slug": "btc-updown-5m-1780455300",
        "outcome": "Up",
        "shares": 1.0,
        "limit_price": 0.51,
        "spread": 0.01,
        "quote_age_seconds": 0.0,
        "live_market_verified": True,
    }
    payload.update(overrides)
    return RuntimeScenario(
        **payload,
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
