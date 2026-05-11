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
    assert candidate.active_strategies[0].entry_rules["entry_disabled"] is True
    assert candidate.active_strategies[0].shadow_flags["shadow_only"] is True
    assert candidate.portfolio_reconciliation[0]["action"] == "adopt"
    assert len(order_calls) == 1
    assert len(intervention_calls) == 1
    order_payload = order_calls[0]["payload"]
    assert order_payload["side"] == "sell"
    assert order_payload["market_id"] == "market-1"
    assert order_payload["outcome_id"] == "outcome-sas"
    assert order_payload["limit_price"] == 0.65
    assert order_payload["size"] == 5.0
    assert order_payload["metadata_json"]["reaction_type"] == "operator_intervention_target"
    assert order_payload["metadata_json"]["no_new_entry_until_revision"] is True
    assert order_payload["metadata_json"]["revision_request"]["position_management_only"] is True
    intervention_payload = intervention_calls[0]["payload"]
    assert intervention_payload["external_order_ids"] == ["0xabc"]
    assert intervention_payload["target_status"] == "target_order_submitted"
    assert intervention_payload["stop_status"] == "not_configured_review_required"
    assert intervention_payload["hedge_status"] == "opposite_side_disabled_without_profit_lock"
    assert intervention_payload["metadata"]["reaction"]["no_new_entry"] is True
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
    candidate = StrategyPlan.model_validate(result["candidate_strategy_plan"])
    assert candidate.active_strategies[0].exit_rules["target_required"] is False
    assert candidate.portfolio_reconciliation[0]["open_sell_size"] == 5.0
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


def test_player_status_shocks_from_live_state_tags_watched_sub_out_pytest() -> None:
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

    assert len(shocks) == 1
    assert shocks[0]["tags"] == ["sub_out_star"]
    assert shocks[0]["requires_strategy_plan_revision"] is True


def test_event_tick_passes_player_status_shocks_to_strategy_evaluation_pytest(monkeypatch) -> None:
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
    )

    evaluate_calls = [call for call in calls if call["path"] == "/v1/events/nba-sas-min-2026-05-10/strategy-plan/evaluate"]
    assert result["market_state"]["player_status_shock_count"] == 1
    assert result["market_state"]["player_status_shocks"][0]["tags"] == [
        "ejection",
        "flagrant_type_2",
        "status_conflict",
        "feed_status_conflict",
    ]
    assert evaluate_calls[0]["payload"]["market_state"]["player_status_shock_count"] == 1


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
