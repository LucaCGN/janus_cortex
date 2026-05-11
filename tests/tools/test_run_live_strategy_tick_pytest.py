from __future__ import annotations

from typing import Any

from codex_tool import run_live_strategy_tick as live_tick


def test_auto_protect_direct_position_places_target_sell_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"api_root": api_root, "method": method, "path": path, "payload": payload, "kwargs": kwargs})
        if path == "/v1/portfolio/orders":
            return {"ok": True, "status": "submitted", "external_order_id": "0xabc"}
        if path == "/v1/operator/interventions/reconcile":
            return {"ok": True, "status": "recorded"}
        return {"ok": True}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._auto_protect_direct_positions(
        api_root="http://test",
        account_id="account-1",
        event_id="nba-sas-min-2026-05-10",
        plan={
            "market_id": "market-1",
            "active_strategies": [
                {
                    "entry_rules": {
                        "token_id": "token-sas",
                        "outcome_id": "outcome-sas",
                    }
                }
            ],
        },
        direct_clob={
            "open_positions": {
                "positions": [
                    {
                        "asset": "token-sas",
                        "avg_price": 0.60,
                        "event_slug": "nba-sas-min-2026-05-10",
                        "outcome": "Spurs",
                        "size": 5.0,
                    }
                ]
            },
            "open_orders": {"orders": []},
        },
        execute=True,
        live_money=True,
        integrity_ready=True,
        source="pytest",
        min_size=5.0,
        target_delta_cents=5.0,
        enabled=True,
    )

    order_calls = [call for call in calls if call["path"] == "/v1/portfolio/orders"]
    assert result["submitted_orders"] == [{"ok": True, "status": "submitted", "external_order_id": "0xabc"}]
    assert len(order_calls) == 1
    order_payload = order_calls[0]["payload"]
    assert order_payload["side"] == "sell"
    assert order_payload["market_id"] == "market-1"
    assert order_payload["outcome_id"] == "outcome-sas"
    assert order_payload["limit_price"] == 0.65
    assert order_payload["size"] == 5.0
    assert order_payload["metadata_json"]["reaction_type"] == "operator_intervention_target"


def test_auto_protect_direct_position_skips_when_target_already_covers_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"path": path, "payload": payload})
        return {"ok": True}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._auto_protect_direct_positions(
        api_root="http://test",
        account_id="account-1",
        event_id="nba-sas-min-2026-05-10",
        plan={
            "market_id": "market-1",
            "active_strategies": [{"entry_rules": {"token_id": "token-sas", "outcome_id": "outcome-sas"}}],
        },
        direct_clob={
            "open_positions": {
                "positions": [
                    {
                        "asset": "token-sas",
                        "avg_price": 0.60,
                        "event_slug": "nba-sas-min-2026-05-10",
                        "outcome": "Spurs",
                        "size": 5.0,
                    }
                ]
            },
            "open_orders": {
                "orders": [
                    {
                        "token_id": "token-sas",
                        "side": "SELL",
                        "status": "LIVE",
                        "size": 5.0,
                        "price": 0.65,
                    }
                ]
            },
        },
        execute=True,
        live_money=True,
        integrity_ready=True,
        source="pytest",
        min_size=5.0,
        target_delta_cents=5.0,
        enabled=True,
    )

    assert result["submitted_orders"] == []
    assert result["covered_positions"] == [{"token_id": "token-sas", "position_size": 5.0, "open_sell_size": 5.0}]
    assert calls == []


def test_event_tick_counts_local_pending_buy_intents_before_direct_mirror_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    plan = {
        "market_id": "market-1",
        "active_strategies": [
            {
                "strategy_id": "grid-sas-1",
                "side": "Spurs",
                "entry_rules": {
                    "outcome_id": "outcome-sas",
                    "token_id": "token-sas",
                    "side": "buy",
                    "price": 0.22,
                    "size": 5,
                },
            }
        ],
    }

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"api_root": api_root, "method": method, "path": path, "payload": payload, "kwargs": kwargs})
        if path == "/v1/events/nba-sas-min-2026-05-10/agent-context":
            return {"ok": True, "current_strategy_plan": plan, "direct_open_order_count": 0, "direct_open_position_count": 0}
        if path == "/v1/nba/games":
            return {"ok": True, "items": []}
        if path == "/v1/sync/polymarket/orderbook":
            return {"ok": True}
        if path == "/v1/outcomes/outcome-sas/orderbook/latest":
            return {
                "ok": True,
                "snapshot": {
                    "best_bid": 0.21,
                    "best_ask": 0.22,
                    "spread": 0.01,
                    "captured_at": "2026-05-10T22:00:00+00:00",
                },
            }
        if path == "/v1/portfolio/orders":
            return {
                "ok": True,
                "items": [
                    {
                        "order_id": "local-order-1",
                        "external_order_id": "0xsubmitted",
                        "client_order_id": None,
                        "event_slug": "nba-sas-min-2026-05-10",
                        "market_id": "market-1",
                        "outcome_id": "outcome-sas",
                        "side": "buy",
                        "status": "submitted",
                        "size": 5,
                        "limit_price": 0.22,
                        "metadata_json": {
                            "strategy_id": "grid-sas-1",
                            "strategy_family": "resistance_band_rebound_grid",
                            "signal_id": "signal-1",
                        },
                    }
                ],
            }
        if path == "/v1/events/nba-sas-min-2026-05-10/strategy-plan/evaluate":
            return {"ok": True, "intent_count": 0, "blocked_count": 1}
        return {"ok": True}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._run_event_tick(
        api_root="http://test",
        session_date="2026-05-10",
        event_id="nba-sas-min-2026-05-10",
        account_id="account-1",
        source="pytest",
        execute=False,
        live_money=False,
        max_intents=2,
        orderbook_sample_count=1,
        orderbook_sample_interval_sec=0.0,
        integrity_ready=True,
        integrity_snapshot={
            "direct_clob": {
                "open_order_count": 0,
                "open_orders": {"orders": []},
                "open_positions": {"positions": []},
            }
        },
        min_size=5.0,
        min_buy_notional_usd=1.0,
        share_precision=3,
        auto_protect_manual_positions=True,
        manual_target_delta_cents=5.0,
    )

    evaluate_calls = [call for call in calls if call["path"] == "/v1/events/nba-sas-min-2026-05-10/strategy-plan/evaluate"]
    assert result["portfolio_state"]["pending_intents"] == 1
    assert result["portfolio_state"]["pending_buy_intents"] == 1
    assert result["portfolio_state"]["pending_intent_orders"][0]["strategy_id"] == "grid-sas-1"
    assert len(evaluate_calls) == 1
    portfolio_state = evaluate_calls[0]["payload"]["portfolio_state"]
    assert portfolio_state["open_orders"] == 0
    assert portfolio_state["open_positions"] == 0
    assert portfolio_state["pending_intents"] == 1
