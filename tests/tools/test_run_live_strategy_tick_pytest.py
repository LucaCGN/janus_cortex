from __future__ import annotations

from typing import Any

from app.modules.agentic.contracts import StrategyPlan
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
                    "strategy_id": "sas-manual-protect",
                    "family": "operator_position_management",
                    "side": "Spurs",
                    "sleeve_id": "sas-manual-position",
                    "sleeve_group": "manual-adoption",
                    "sleeve_role": "protective-target",
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
    intervention_calls = [call for call in calls if call["path"] == "/v1/operator/interventions/reconcile"]
    assert result["submitted_orders"] == [{"ok": True, "status": "submitted", "external_order_id": "0xabc"}]
    assert result["intervention_records"] == [{"ok": True, "status": "recorded"}]
    assert result["candidate_strategy_plan_required"] is True
    candidate = StrategyPlan.model_validate(result["candidate_strategy_plan"])
    assert candidate.plan_owner == "system"
    assert candidate.context_summary["position_management_only"] is True
    assert candidate.active_strategies[0].family == "operator_position_management"
    assert candidate.active_strategies[0].sleeve_id == "sas-manual-position"
    assert candidate.active_strategies[0].sleeve_group == "manual-adoption"
    assert candidate.active_strategies[0].sleeve_role == "protective-target"
    assert candidate.active_strategies[0].entry_rules["entry_disabled"] is True
    assert candidate.active_strategies[0].shadow_flags["shadow_only"] is True
    assert candidate.portfolio_reconciliation[0]["action"] == "adopt"
    assert candidate.portfolio_reconciliation[0]["sleeve_id"] == "sas-manual-position"
    assert len(order_calls) == 1
    assert len(intervention_calls) == 1
    order_payload = order_calls[0]["payload"]
    assert order_payload["side"] == "sell"
    assert order_payload["market_id"] == "market-1"
    assert order_payload["outcome_id"] == "outcome-sas"
    assert order_payload["limit_price"] == 0.65
    assert order_payload["size"] == 5.0
    assert order_payload["metadata_json"]["reaction_type"] == "operator_intervention_target"
    assert order_payload["metadata_json"]["matched_sleeve_id"] == "sas-manual-position"
    assert order_payload["metadata_json"]["sleeve"] == {
        "sleeve_id": "sas-manual-position",
        "sleeve_group": "manual-adoption",
        "sleeve_role": "protective-target",
    }
    assert order_payload["metadata_json"]["no_new_entry_until_revision"] is True
    assert order_payload["metadata_json"]["revision_request"]["position_management_only"] is True
    assert order_payload["metadata_json"]["revision_request"]["sleeve_id"] == "sas-manual-position"
    intervention_payload = intervention_calls[0]["payload"]
    assert intervention_payload["external_order_ids"] == ["0xabc"]
    assert intervention_payload["target_status"] == "target_order_submitted"
    assert intervention_payload["stop_status"] == "not_configured_review_required"
    assert intervention_payload["hedge_status"] == "opposite_side_disabled_without_profit_lock"
    assert intervention_payload["metadata"]["reaction"]["no_new_entry"] is True
    assert intervention_payload["metadata"]["reaction"]["sleeve_id"] == "sas-manual-position"
    assert intervention_payload["metadata"]["reaction"]["requires_strategy_plan_revision"] is True


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
            "active_strategies": [
                {
                    "strategy_id": "sas-manual-order-watch",
                    "family": "operator_order_management",
                    "sleeve_id": "sas-order-sleeve",
                    "sleeve_group": "manual-adoption",
                    "sleeve_role": "open-order-review",
                    "entry_rules": {"token_id": "token-sas", "outcome_id": "outcome-sas"},
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
    assert result["position_reactions"] == []
    assert result["revision_requests"] == []
    assert result["candidate_strategy_plan_required"] is False
    assert "candidate_strategy_plan" not in result
    assert calls == []


def test_auto_protect_direct_position_emits_adverse_review_when_stop_rules_trip_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"path": path, "payload": payload})
        return {"ok": True}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._auto_protect_direct_positions(
        api_root="http://test",
        account_id="account-1",
        event_id="nba-okc-lal-2026-05-11",
        plan={
            "market_id": "market-1",
            "active_strategies": [
                {
                    "strategy_id": "lal-live-micro-grid",
                    "family": "price_stability_micro_grid",
                    "side": "Lakers",
                    "sleeve_id": "lal-q4-micro-grid",
                    "sleeve_group": "lakers",
                    "sleeve_role": "adverse-review",
                    "entry_rules": {"token_id": "token-lal", "outcome_id": "outcome-lal"},
                    "stop_rules": {
                        "stop_price": 0.13,
                        "max_adverse_cents": 3,
                        "stop_review_if_score_gap_exceeds": 10,
                    },
                }
            ],
        },
        direct_clob={
            "open_positions": {
                "positions": [
                    {
                        "asset": "token-lal",
                        "avg_price": 0.19,
                        "event_slug": "nba-okc-lal-2026-05-11",
                        "outcome": "Lakers",
                        "size": 5.3099,
                    }
                ]
            },
            "open_orders": {
                "orders": [
                    {
                        "token_id": "token-lal",
                        "side": "SELL",
                        "status": "LIVE",
                        "size": 5.3,
                        "price": 0.21,
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
        outcome_states={
            "outcome-lal": {
                "best_bid": 0.10,
                "best_ask": 0.11,
                "score_gap": -8,
                "captured_at_utc": "2026-05-12T03:40:00Z",
            }
        },
    )

    assert calls == []
    assert result["recommended_orders"][0]["reason"] == "uncovered_size_below_minimum"
    assert len(result["adverse_position_reviews"]) == 1
    review = result["adverse_position_reviews"][0]
    assert review["action"] == "position_adverse_move"
    assert review["strategy_id"] == "lal-live-micro-grid"
    assert review["sleeve_id"] == "lal-q4-micro-grid"
    assert review["current_exit_bid"] == 0.10
    assert {rule["rule"] for rule in review["triggered_rules"]} == {"stop_price", "max_adverse_cents"}
    assert review["revision_request"]["decision_options_to_compare"] == [
        "hold_existing_target",
        "cancel_replace_lower_target",
        "marketable_stop_or_reduce",
        "opposite_side_hedge_or_continuation",
        "add_down_same_side_micro_grid",
    ]
    assert review["revision_request"]["sleeve_id"] == "lal-q4-micro-grid"
    assert result["candidate_strategy_plan_required"] is True


def test_position_strategy_from_plan_prefers_current_period_strategy_pytest() -> None:
    plan = {
        "active_strategies": [
            {
                "strategy_id": "lal-q1-q2-lebron-momentum-scalp-v1",
                "entry_rules": {
                    "token_id": "token-lal",
                    "outcome_id": "outcome-lal",
                    "min_period": 1,
                    "max_period": 2,
                },
            },
            {
                "strategy_id": "lal-q3-q4-close-game-micro-grid-v1",
                "entry_rules": {
                    "token_id": "token-lal",
                    "outcome_id": "outcome-lal",
                    "min_period": 3,
                    "max_period": 4,
                },
            },
        ],
    }

    selected = live_tick._position_strategy_from_plan(
        plan=plan,
        token_id="token-lal",
        outcome_id="outcome-lal",
        outcome_state={"period": 4},
    )

    assert selected is not None
    assert selected["strategy_id"] == "lal-q3-q4-close-game-micro-grid-v1"


def test_event_tick_can_submit_reviewed_candidate_strategy_plan_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    plan = {
        "market_id": "market-1",
        "active_strategies": [
            {
                "strategy_id": "sas-favorite-floor-rebound-v2",
                "side": "Spurs",
                "entry_rules": {
                    "outcome_id": "outcome-sas",
                    "token_id": "token-sas",
                    "side": "buy",
                    "price": 0.18,
                    "size": 5,
                },
            }
        ],
    }

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"method": method, "path": path, "payload": payload, "kwargs": kwargs})
        if path == "/v1/events/nba-sas-min-2026-05-10/agent-context":
            return {"ok": True, "current_strategy_plan": plan, "direct_open_order_count": 0, "direct_open_position_count": 1}
        if path == "/v1/nba/games":
            return {"ok": True, "items": []}
        if path == "/v1/sync/polymarket/orderbook":
            return {"ok": True}
        if path == "/v1/outcomes/outcome-sas/orderbook/latest":
            return {
                "ok": True,
                "snapshot": {
                    "best_bid": 0.4,
                    "best_ask": 0.41,
                    "spread": 0.01,
                    "captured_at": "2026-05-11T01:16:00+00:00",
                },
            }
        if path == "/v1/portfolio/orders":
            return {"ok": True, "items": []}
        if path == "/v1/events/nba-sas-min-2026-05-10/strategy-plan":
            return {"ok": True, "status": "stored", "strategy_count": len(payload["active_strategies"])}
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
            }
        },
        min_size=5.0,
        min_buy_notional_usd=1.0,
        share_precision=3,
        auto_protect_manual_positions=True,
        manual_target_delta_cents=5.0,
        submit_candidate_strategy_plan=True,
    )

    plan_submit_index = next(i for i, call in enumerate(calls) if call["path"] == "/v1/events/nba-sas-min-2026-05-10/strategy-plan")
    evaluate_index = next(i for i, call in enumerate(calls) if call["path"] == "/v1/events/nba-sas-min-2026-05-10/strategy-plan/evaluate")
    submitted_plan = calls[plan_submit_index]["payload"]
    StrategyPlan.model_validate(submitted_plan)
    assert plan_submit_index < evaluate_index
    assert submitted_plan["active_strategies"][0]["family"] == "operator_position_management"
    assert submitted_plan["active_strategies"][0]["entry_rules"]["entry_disabled"] is True
    assert result["operator_reaction"]["candidate_strategy_plan_submission"]["submitted"] is True
    assert result["ok"] is True


def test_auto_protect_direct_order_reacts_to_unknown_open_order_pytest(monkeypatch) -> None:
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
            "active_strategies": [
                {
                    "strategy_id": "sas-manual-order-watch",
                    "family": "operator_order_management",
                    "sleeve_id": "sas-order-sleeve",
                    "sleeve_group": "manual-adoption",
                    "sleeve_role": "open-order-review",
                    "entry_rules": {"token_id": "token-sas", "outcome_id": "outcome-sas"},
                }
            ],
        },
        direct_clob={
            "open_positions": {"positions": []},
            "open_orders": {
                "orders": [
                    {
                        "id": "0xmanual",
                        "token_id": "token-sas",
                        "side": "BUY",
                        "status": "LIVE",
                        "size": 10.0,
                        "price": 0.18,
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
        known_external_order_ids=set(),
    )

    assert calls == []
    assert result["order_reactions"][0]["action"] == "adopt_operator_open_order"
    assert result["order_reactions"][0]["direct_order_id"] == "0xmanual"
    assert result["order_reactions"][0]["sleeve_id"] == "sas-order-sleeve"
    assert result["revision_requests"][0]["reason"] == "unknown_direct_clob_order_detected"
    assert result["revision_requests"][0]["sleeve_id"] == "sas-order-sleeve"
    candidate = StrategyPlan.model_validate(result["candidate_strategy_plan"])
    assert candidate.context_summary["unknown_direct_order_count"] == 1
    assert candidate.active_strategies[0].family == "operator_order_management"
    assert candidate.active_strategies[0].sleeve_id == "sas-order-sleeve"
    assert candidate.portfolio_reconciliation[0]["action"] == "adopt_open_order"
    assert candidate.portfolio_reconciliation[0]["sleeve_id"] == "sas-order-sleeve"


def test_auto_protect_direct_trade_reacts_to_unknown_fill_pytest(monkeypatch) -> None:
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
            "active_strategies": [
                {
                    "strategy_id": "sas-manual-trade-watch",
                    "family": "operator_trade_management",
                    "sleeve_id": "sas-trade-sleeve",
                    "sleeve_group": "manual-adoption",
                    "sleeve_role": "trade-review",
                    "entry_rules": {"token_id": "token-sas", "outcome_id": "outcome-sas"},
                }
            ],
        },
        direct_clob={
            "open_positions": {"positions": []},
            "open_orders": {"orders": []},
            "direct_trades": {
                "trades": [
                    {
                        "id": "clob-trade-1",
                        "asset_id": "token-sas",
                        "side": "BUY",
                        "price": 0.60,
                        "size": 5.0,
                        "taker_order_id": "0xmanual-buy",
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
        known_external_order_ids=set(),
    )

    assert calls == []
    assert result["trade_reactions"][0]["action"] == "adopt_operator_trade"
    assert result["trade_reactions"][0]["direct_trade_id"] == "clob-trade-1"
    assert result["trade_reactions"][0]["direct_order_ids"] == ["0xmanual-buy"]
    assert result["trade_reactions"][0]["estimated_cashflow_usd"] == -3.0
    assert result["trade_reactions"][0]["sleeve_id"] == "sas-trade-sleeve"
    assert result["revision_requests"][0]["reason"] == "unknown_direct_clob_trade_detected"
    assert result["revision_requests"][0]["sleeve_id"] == "sas-trade-sleeve"
    candidate = StrategyPlan.model_validate(result["candidate_strategy_plan"])
    assert candidate.context_summary["unknown_direct_trade_count"] == 1
    assert candidate.context_summary["trade_management_only"] is True
    assert candidate.active_strategies[0].family == "operator_trade_management"
    assert candidate.active_strategies[0].sleeve_id == "sas-trade-sleeve"
    assert candidate.active_strategies[0].exit_rules["final_pnl_review_required"] is True
    assert candidate.portfolio_reconciliation[0]["action"] == "adopt_trade_fill"
    assert candidate.portfolio_reconciliation[0]["sleeve_id"] == "sas-trade-sleeve"


def test_auto_protect_direct_trade_ignores_public_market_trade_rows_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"path": path, "payload": payload})
        return {"ok": True}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._auto_protect_direct_positions(
        api_root="http://test",
        account_id="account-1",
        event_id="nba-okc-lal-2026-05-11",
        plan={
            "market_id": "market-1",
            "active_strategies": [{"entry_rules": {"token_id": "token-lal", "outcome_id": "outcome-lal"}}],
        },
        direct_clob={
            "open_positions": {"positions": []},
            "open_orders": {"orders": []},
            "current_token_trades": {
                "trades": [
                    {
                        "id": "public-market-trade",
                        "asset_id": "token-lal",
                        "side": "BUY",
                        "price": 0.21,
                        "size": 3072.0,
                        "taker_order_id": "0xpublic",
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
        known_external_order_ids=set(),
    )

    assert calls == []
    assert result["direct_trade_count"] == 0
    assert result["trade_reactions"] == []
    assert result["revision_requests"] == []
    assert result["candidate_strategy_plan_required"] is False


def test_persist_direct_trade_watch_observations_records_plan_token_trades_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"path": path, "payload": payload})
        return {"ok": True, "trade_count": len(payload["trades"]), "db_persistence": {"ok": True, "row_count": len(payload["trades"])}}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._persist_direct_trade_watch_observations(
        api_root="http://test",
        event_id="nba-sas-min-2026-05-10",
        plan={
            "market_id": "market-1",
            "active_strategies": [{"entry_rules": {"token_id": "token-sas", "outcome_id": "outcome-sas"}}],
        },
        direct_clob={
            "current_token_trades": {
                "trades": [
                    {
                        "id": "clob-trade-1",
                        "asset_id": "token-sas",
                        "side": "SELL",
                        "price": 0.65,
                        "size": 5.0,
                        "timestamp": 1778452800,
                        "maker_order_id": "0xtarget",
                    },
                    {
                        "id": "unrelated-trade",
                        "asset_id": "token-other",
                        "side": "BUY",
                        "price": 0.1,
                        "size": 5.0,
                    },
                ]
            }
        },
        source="pytest",
    )

    assert result["ok"] is True
    assert result["trade_count"] == 1
    assert len(calls) == 1
    assert calls[0]["path"] == "/v1/watchlists/trades"
    assert calls[0]["payload"]["source"] == "pytest:direct_clob_trade_observation"
    trade = calls[0]["payload"]["trades"][0]
    assert trade["event_key"] == "nba-sas-min-2026-05-10"
    assert trade["market_id"] == "market-1"
    assert trade["outcome_id"] == "outcome-sas"
    assert trade["token_id"] == "token-sas"
    assert trade["external_trade_id"] == "clob-trade-1"
    assert trade["side"] == "sell"
    assert trade["price"] == 0.65
    assert trade["size"] == 5.0
    assert trade["raw"]["direct_order_ids"] == ["0xtarget"]


def test_direct_trade_watch_observation_ignores_zero_timestamp_latency_pytest() -> None:
    observation = live_tick._direct_trade_watch_observation(
        event_id="nba-okc-lal-2026-05-11",
        trade={
            "id": "clob-trade-1",
            "asset_id": "token-lal",
            "side": "BUY",
            "price": 0.19,
            "size": 5.31,
            "timestamp": 0,
            "taker_order_id": "0xbuy",
        },
        outcome_lookup={"token-lal": {"market_id": "market-1", "outcome_id": "outcome-lal"}},
        source="pytest",
    )

    assert observation is not None
    assert observation["source_latency_ms"] is None
    assert not str(observation["trade_time_utc"]).startswith("1970-")


def test_direct_trade_watch_observation_omits_historical_latency_over_db_limit_pytest() -> None:
    observation = live_tick._direct_trade_watch_observation(
        event_id="nba-okc-lal-2026-05-11",
        trade={
            "id": "clob-trade-old",
            "asset_id": "token-lal",
            "side": "BUY",
            "price": 0.19,
            "size": 5.31,
            "timestamp": 1,
            "taker_order_id": "0xbuy",
        },
        outcome_lookup={"token-lal": {"market_id": "market-1", "outcome_id": "outcome-lal"}},
        source="pytest",
    )

    assert observation is not None
    assert observation["source_latency_ms"] is None
    assert str(observation["trade_time_utc"]).startswith("1970-")


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


def test_pending_intent_summary_ignores_local_order_filled_in_direct_clob_pytest(monkeypatch) -> None:
    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        assert path == "/v1/portfolio/orders"
        return {
            "ok": True,
            "items": [
                {
                    "order_id": "local-order-1",
                    "external_order_id": "0xsubmitted",
                    "event_slug": "nba-okc-lal-2026-05-11",
                    "market_id": "market-1",
                    "outcome_id": "outcome-lal",
                    "side": "buy",
                    "status": "submitted",
                    "size": 5.316,
                    "limit_price": 0.19,
                    "metadata_json": {"strategy_id": "lal-live-micro-grid"},
                }
            ],
        }

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    summary = live_tick._pending_intent_summary(
        api_root="http://test",
        account_id="account-1",
        event_id="nba-okc-lal-2026-05-11",
        plan={"market_id": "market-1"},
        direct_clob={
            "current_token_trades": {
                "trades": [
                    {
                        "id": "trade-1",
                        "asset_id": "token-lal",
                        "side": "BUY",
                        "price": 0.19,
                        "size": 5.31,
                        "timestamp": 0,
                        "taker_order_id": "0xsubmitted",
                    }
                ]
            }
        },
    )

    assert summary["pending_intent_count"] == 0
    assert summary["pending_buy_intent_count"] == 0
    assert summary["orders"] == []


def test_position_target_price_uses_scaled_micro_grid_policy_pytest() -> None:
    target = live_tick._position_target_price_from_plan(
        plan={
            "active_strategies": [
                {
                    "strategy_id": "det-micro-grid",
                    "family": "underdog_micro_grid_reprice",
                    "sleeve_id": "det-q1-micro-grid",
                    "sleeve_group": "det",
                    "sleeve_role": "scaled-target",
                    "entry_rules": {
                        "token_id": "token-det",
                        "outcome_id": "outcome-det",
                    },
                    "exit_rules": {
                        "target_policy": "micro_grid_scaled",
                        "min_target_cents": 1,
                        "target_return_fraction": 0.10,
                    },
                }
            ]
        },
        token_id="token-det",
        outcome_id="outcome-det",
        avg_price=0.20,
        default_target_delta_cents=5.0,
    )

    assert target["target_price"] == 0.22
    assert target["target_delta_cents"] == 2.0
    assert target["target_policy"] == "micro_grid_scaled"
    assert target["strategy_id"] == "det-micro-grid"
    assert target["sleeve_id"] == "det-q1-micro-grid"


def test_position_target_price_prefers_live_strategy_over_shadow_match_pytest() -> None:
    target = live_tick._position_target_price_from_plan(
        plan={
            "active_strategies": [
                {
                    "strategy_id": "lal-shadow-liftoff",
                    "family": "underdog_liftoff",
                    "entry_rules": {"token_id": "token-lal", "outcome_id": "outcome-lal"},
                    "exit_rules": {"target_price": 0.24},
                    "shadow_flags": {"shadow_only": True},
                },
                {
                    "strategy_id": "lal-live-micro-grid",
                    "family": "price_stability_micro_grid",
                    "entry_rules": {"token_id": "token-lal", "outcome_id": "outcome-lal"},
                    "exit_rules": {
                        "target_policy": "micro_grid_scaled",
                        "min_target_cents": 1,
                        "target_return_fraction": 0.10,
                    },
                    "shadow_flags": {},
                },
            ]
        },
        token_id="token-lal",
        outcome_id="outcome-lal",
        avg_price=0.19,
        default_target_delta_cents=5.0,
    )

    assert target["target_price"] == 0.209
    assert target["target_policy"] == "micro_grid_scaled"
    assert target["strategy_id"] == "lal-live-micro-grid"


def test_position_target_price_prefers_current_period_strategy_pytest() -> None:
    target = live_tick._position_target_price_from_plan(
        plan={
            "active_strategies": [
                {
                    "strategy_id": "okc-favorite-floor-rebound-v1",
                    "family": "favorite_floor_rebound",
                    "entry_rules": {"token_id": "token-okc", "outcome_id": "outcome-okc"},
                    "exit_rules": {"target_price": 0.56},
                },
                {
                    "strategy_id": "okc-q3-q4-close-game-continuation-grid-v1",
                    "family": "favorite_continuation_micro_grid",
                    "entry_rules": {
                        "token_id": "token-okc",
                        "outcome_id": "outcome-okc",
                        "min_period": 3,
                        "max_period": 4,
                    },
                    "exit_rules": {
                        "target_policy": "micro_grid_scaled",
                        "min_target_cents": 1,
                        "target_return_fraction": 0.03,
                    },
                },
            ]
        },
        token_id="token-okc",
        outcome_id="outcome-okc",
        avg_price=0.76,
        default_target_delta_cents=5.0,
        outcome_state={"period": 4},
    )

    assert target["target_price"] == 0.7828
    assert target["target_policy"] == "micro_grid_scaled"
    assert target["strategy_id"] == "okc-q3-q4-close-game-continuation-grid-v1"


def test_position_target_price_uses_one_cent_floor_for_low_prices_pytest() -> None:
    target = live_tick._position_target_price_from_plan(
        plan={
            "active_strategies": [
                {
                    "strategy_id": "det-low-grid",
                    "family": "underdog_micro_grid_reprice",
                    "entry_rules": {"token_id": "token-det"},
                    "exit_rules": {
                        "target_policy": "micro_grid_scaled",
                        "min_target_cents": 1,
                        "target_return_fraction": 0.10,
                    },
                }
            ]
        },
        token_id="token-det",
        outcome_id="outcome-det",
        avg_price=0.05,
        default_target_delta_cents=5.0,
    )

    assert target["target_price"] == 0.06
    assert target["target_delta_cents"] == 1.0


def test_event_tick_scopes_direct_clob_exposure_to_plan_tokens_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    plan = {
        "market_id": "market-det-cle",
        "active_strategies": [
            {
                "strategy_id": "det-underdog-range-scalp-v1",
                "side": "Pistons",
                "sleeve_id": "det-q1-underdog",
                "sleeve_group": "det",
                "sleeve_role": "standard_entry",
                "entry_rules": {
                    "outcome_id": "outcome-det",
                    "token_id": "token-det",
                    "side": "buy",
                    "price": 0.31,
                    "size": 5,
                },
            }
        ],
    }

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"method": method, "path": path, "payload": payload, "kwargs": kwargs})
        if path == "/v1/events/nba-det-cle-2026-05-11/agent-context":
            return {
                "ok": True,
                "current_strategy_plan": plan,
                "direct_open_order_count": 1,
                "direct_open_position_count": 1,
            }
        if path == "/v1/nba/games":
            return {"ok": True, "items": []}
        if path == "/v1/sync/polymarket/orderbook":
            return {"ok": True}
        if path == "/v1/outcomes/outcome-det/orderbook/latest":
            return {
                "ok": True,
                "snapshot": {
                    "best_bid": 0.32,
                    "best_ask": 0.33,
                    "spread": 0.01,
                    "captured_at": "2026-05-12T01:40:00+00:00",
                },
            }
        if path == "/v1/portfolio/orders":
            return {"ok": True, "items": []}
        if path == "/v1/watchlists/sessions":
            return {"ok": True, "db_persistence": {"watch_session_id": "watch-session-uuid"}}
        if path == "/v1/watchlists/orderbook-ticks":
            return {"ok": True, "tick_count": len(payload["ticks"]), "db_persistence": {"ok": True}}
        if path == "/v1/events/nba-det-cle-2026-05-11/strategy-plan/evaluate":
            return {
                "ok": True,
                "intent_count": 1,
                "blocked_count": 0,
                "sleeve_states": [
                    {
                        "sleeve_id": "det-q1-underdog",
                        "sleeve_group": "det",
                        "sleeve_role": "standard_entry",
                        "strategy_id": "det-underdog-range-scalp-v1",
                        "status": "intent_created",
                        "intent_count": 1,
                        "blocker_count": 0,
                        "blocker_reasons": [],
                    }
                ],
            }
        return {"ok": True}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._run_event_tick(
        api_root="http://test",
        session_date="2026-05-11",
        event_id="nba-det-cle-2026-05-11",
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
                "open_order_count": 1,
                "open_orders": {
                    "orders": [
                        {
                            "id": "order-unrelated",
                            "token_id": "token-nba-finals",
                            "side": "SELL",
                            "status": "LIVE",
                            "size": 59.99,
                            "price": 0.03,
                        }
                    ]
                },
                "open_positions": {
                    "positions": [
                        {
                            "asset": "token-nba-finals",
                            "event_slug": "2026-nba-champion",
                            "outcome": "Yes",
                            "size": 59.99,
                            "avg_price": 0.018,
                        }
                    ]
                },
                "current_token_trades": {"trades": [], "trade_count": 0},
            }
        },
        min_size=5.0,
        min_buy_notional_usd=1.0,
        share_precision=3,
        auto_protect_manual_positions=True,
        manual_target_delta_cents=5.0,
    )

    evaluate_calls = [
        call for call in calls if call["path"] == "/v1/events/nba-det-cle-2026-05-11/strategy-plan/evaluate"
    ]
    assert result["portfolio_state"]["open_orders"] == 0
    assert result["portfolio_state"]["open_positions"] == 0
    assert result["portfolio_state"]["direct_clob_global_open_orders"] == 1
    assert result["portfolio_state"]["direct_clob_global_open_positions"] == 1
    assert result["strategy_sleeve_status"]["status"] == "recorded"
    assert result["strategy_sleeve_status"]["intent_sleeve_count"] == 1
    assert result["sleeve_states"][0]["sleeve_id"] == "det-q1-underdog"
    portfolio_state = evaluate_calls[0]["payload"]["portfolio_state"]
    assert portfolio_state["open_orders"] == 0
    assert portfolio_state["open_positions"] == 0
    assert portfolio_state["direct_clob_global_open_orders"] == 1


def test_player_status_shocks_from_live_state_tags_ejection_and_conflict_pytest() -> None:
    shocks = live_tick._player_status_shocks_from_live_state(
        {
            "game_id": "0042500234",
            "latest_snapshot": {
                "payload_json": {
                    "players": [
                        {
                            "playerName": "Victor Wembanyama",
                            "status": "ACTIVE",
                        }
                    ]
                }
            },
            "recent_play_by_play": [
                {
                    "game_id": "0042500234",
                    "event_index": 179,
                    "action_id": 1790,
                    "period": 2,
                    "clock": "PT08M39.00S",
                    "description": "Victor Wembanyama assessed Flagrant Foul Type 2 and ejected",
                    "home_score": 36,
                    "away_score": 34,
                    "payload_json": {
                        "playerName": "Victor Wembanyama",
                        "teamTricode": "SAS",
                        "actionType": "foul",
                        "subType": "flagrant type 2",
                    },
                }
            ],
        },
        plan={
            "active_strategies": [
                {
                    "entry_rules": {
                        "requires_wembanyama_available": True,
                    }
                }
            ]
        },
        game={"game_id": "0042500234"},
    )

    assert len(shocks) == 1
    assert shocks[0]["player_name"] == "Victor Wembanyama"
    assert shocks[0]["event_index"] == 179
    assert shocks[0]["tags"] == ["ejection", "flagrant_type_2", "status_conflict", "feed_status_conflict"]
    assert shocks[0]["watched_player"] is True
    assert shocks[0]["role_weight"] == 1.0
    assert shocks[0]["requires_strategy_plan_revision"] is True


def test_player_status_shocks_from_live_state_ignores_routine_watched_sub_out_pytest() -> None:
    shocks = live_tick._player_status_shocks_from_live_state(
        {
            "game_id": "0042500234",
            "recent_play_by_play": [
                {
                    "event_index": 180,
                    "period": 2,
                    "clock": "PT08M39.00S",
                    "description": "Substitution out: Victor Wembanyama",
                    "payload_json": {"playerName": "Victor Wembanyama"},
                }
            ],
        },
        plan={"active_strategies": [{"entry_rules": {"requires_wembanyama_available": True}}]},
        game={"game_id": "0042500234"},
    )

    assert shocks == []


def test_event_tick_passes_player_status_shocks_to_strategy_evaluation_pytest(monkeypatch, tmp_path) -> None:
    calls: list[dict[str, Any]] = []
    plan = {
        "market_id": "market-1",
        "active_strategies": [
            {
                "strategy_id": "sas-favorite-floor-rebound-v2",
                "side": "Spurs",
                "entry_rules": {
                    "outcome_id": "outcome-sas",
                    "token_id": "token-sas",
                    "side": "buy",
                    "price": 0.18,
                    "size": 6,
                    "requires_wembanyama_available": True,
                },
            }
        ],
    }

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"api_root": api_root, "method": method, "path": path, "payload": payload, "kwargs": kwargs})
        if path == "/v1/events/nba-sas-min-2026-05-10/agent-context":
            return {"ok": True, "current_strategy_plan": plan, "direct_open_order_count": 0, "direct_open_position_count": 0}
        if path == "/v1/nba/games":
            return {
                "ok": True,
                "items": [
                    {
                        "game_id": "0042500234",
                        "game_date": "2026-05-10",
                        "home_team_slug": "MIN",
                        "away_team_slug": "SAS",
                        "home_team_name": "Timberwolves",
                        "away_team_name": "Spurs",
                    }
                ],
            }
        if path == "/v1/sync/nba/live/0042500234":
            return {"ok": True}
        if path == "/v1/nba/games/0042500234/live":
            return {
                "game_id": "0042500234",
                "latest_snapshot": {
                    "payload_json": {"players": [{"playerName": "Victor Wembanyama", "status": "ACTIVE"}]}
                },
                "recent_play_by_play": [
                    {
                        "event_index": 179,
                        "period": 2,
                        "clock": "PT08M39.00S",
                        "description": "Victor Wembanyama Flagrant Foul Type 2, ejected",
                        "payload_json": {"playerName": "Victor Wembanyama", "teamTricode": "SAS"},
                    }
                ],
            }
        if path == "/v1/sync/polymarket/orderbook":
            return {"ok": True}
        if path == "/v1/outcomes/outcome-sas/orderbook/latest":
            return {
                "ok": True,
                "snapshot": {
                    "best_bid": 0.17,
                    "best_ask": 0.18,
                    "spread": 0.01,
                    "captured_at": "2026-05-10T22:00:00+00:00",
                },
            }
        if path == "/v1/portfolio/orders":
            return {"ok": True, "items": []}
        if path == "/v1/watchlists/sessions":
            return {"ok": True, "db_persistence": {"watch_session_id": "watch-session-uuid"}}
        if path == "/v1/watchlists/orderbook-ticks":
            return {"ok": True, "tick_count": len(payload["ticks"]), "db_persistence": {"ok": True}}
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
        llm_runtime_artifact_root=str(tmp_path / "llm-runtime"),
        persist_llm_runtime_trace=True,
    )

    evaluate_calls = [call for call in calls if call["path"] == "/v1/events/nba-sas-min-2026-05-10/strategy-plan/evaluate"]
    order_calls = [call for call in calls if call["path"] == "/v1/portfolio/orders" and call["method"] == "POST"]
    assert result["market_state"]["player_status_shock_count"] == 1
    assert result["market_state"]["player_status_shocks"][0]["tags"] == [
        "ejection",
        "flagrant_type_2",
        "status_conflict",
        "feed_status_conflict",
    ]
    assert result["market_state"]["llm_runtime_trigger_count"] == 1
    assert result["market_state"]["llm_runtime_triggers"][0]["trigger_type"] == "player_status_shock"
    assert result["market_state"]["llm_runtime_triggers"][0]["selected_model"] == "gpt-5.5"
    assert result["llm_runtime_trace"]["trigger_count"] == 1
    assert result["llm_runtime_trace"]["triggers"][0]["trigger_type"] == "player_status_shock"
    assert result["llm_runtime_trace"]["model_routing"]["selected_model"] == "gpt-5.5"
    assert result["llm_runtime_trace"]["status"] == "skipped_unavailable"
    assert result["llm_runtime_trace"]["revision_response"]["status"] == "skipped_unavailable"
    assert result["llm_runtime_trace"]["revision_response"]["skipped_reason"] == "dispatch_disabled"
    assert result["llm_runtime_trace"]["revision_response"]["trace_metadata"]["openai_call_attempted"] is False
    assert result["llm_runtime_trace"]["revision_response"]["trace_metadata"]["order_endpoint_call_allowed"] is False
    assert result["llm_runtime_persistence"]["status"] == "persisted"
    assert result["llm_runtime_status"]["persisted"] is True
    assert result["llm_runtime_status"]["live_blocker"] == "llm_revision_unavailable"
    assert (tmp_path / "llm-runtime" / "2026-05-10").exists()
    assert order_calls == []
    assert evaluate_calls[0]["payload"]["market_state"]["player_status_shock_count"] == 1
    assert evaluate_calls[0]["payload"]["market_state"]["llm_runtime_trigger_count"] == 1
    assert evaluate_calls[0]["payload"]["market_state"]["llm_runtime_status"]["response_status"] == "skipped_unavailable"


def test_persist_orderbook_watch_ticks_records_sampled_outcomes_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"api_root": api_root, "method": method, "path": path, "payload": payload, "kwargs": kwargs})
        if path == "/v1/watchlists/sessions":
            return {
                "ok": True,
                "status": "stored",
                "db_persistence": {
                    "ok": True,
                    "watch_session_id": "watch-session-uuid",
                    "watch_session_key": payload["watch_session_id"],
                },
            }
        if path == "/v1/watchlists/orderbook-ticks":
            return {"ok": True, "status": "stored", "tick_count": len(payload["ticks"]), "db_persistence": {"ok": True, "row_count": 2}}
        return {"ok": True}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._persist_orderbook_watch_ticks(
        api_root="http://test",
        event_id="nba-sas-min-2026-05-10",
        plan={
            "market_id": "market-1",
            "active_strategies": [
                {"entry_rules": {"outcome_id": "outcome-sas", "token_id": "token-sas"}},
                {"entry_rules": {"outcome_id": "outcome-min", "token_id": "token-min"}},
            ],
        },
        orderbooks={
            "outcome-sas": {
                "snapshot": {
                    "orderbook_snapshot_id": "snapshot-sas",
                    "best_bid": 0.4,
                    "best_ask": 0.41,
                    "spread": 0.01,
                    "captured_at": "2026-05-11T01:16:00+00:00",
                    "bid_depth": 25,
                    "ask_depth": 30,
                },
                "bids": [{"price": 0.4, "size": 25}],
                "asks": [{"price": 0.41, "size": 30}],
                "levels_count": 2,
            },
            "outcome-min": {
                "snapshot": {
                    "orderbook_snapshot_id": "snapshot-min",
                    "best_bid": 0.59,
                    "best_ask": 0.6,
                    "captured_at": "2026-05-11T01:16:00+00:00",
                },
                "bids": [],
                "asks": [],
                "levels_count": 0,
            },
        },
        source="pytest-live-tick",
        game={"game_id": "0042500234"},
        cadence_ms=500,
    )

    session_calls = [call for call in calls if call["path"] == "/v1/watchlists/sessions"]
    tick_calls = [call for call in calls if call["path"] == "/v1/watchlists/orderbook-ticks"]
    assert result["ok"] is True
    assert result["watch_session_key"] == "watch-nba-sas-min-2026-05-10"
    assert result["tick_count"] == 2
    assert len(session_calls) == 1
    assert session_calls[0]["payload"]["watch_session_id"] == "watch-nba-sas-min-2026-05-10"
    assert session_calls[0]["payload"]["event_key"] == "nba-sas-min-2026-05-10"
    assert len(tick_calls) == 1
    ticks = tick_calls[0]["payload"]["ticks"]
    assert [tick["outcome_id"] for tick in ticks] == ["outcome-sas", "outcome-min"]
    assert ticks[0]["event_key"] == "nba-sas-min-2026-05-10"
    assert ticks[0]["market_id"] == "market-1"
    assert ticks[0]["token_id"] == "token-sas"
    assert ticks[0]["levels"]["bids"] == [{"price": 0.4, "size": 25}]
    assert ticks[0]["raw"]["watch_session_key"] == "watch-nba-sas-min-2026-05-10"
    assert ticks[0]["raw"]["watch_session_id"] == "watch-session-uuid"
