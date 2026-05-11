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
