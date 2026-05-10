from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.dependencies import get_db_connection
from app.api.main import create_app
from app.api.routers import ops as ops_router
from app.modules.agentic import store as agentic_store


def test_ops_status_uses_repo_local_root_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    monkeypatch.setattr(
        ops_router,
        "get_agentic_database_status",
        lambda: {"ok": True, "schema": "agentic", "tables": []},
    )

    client = TestClient(create_app())
    response = client.get("/v1/ops/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["local_roots"]["shared_root"] == str(local_root / "shared")
    assert payload["database"]["schema"] == "agentic"


def test_strategy_plan_endpoint_stores_and_reads_current_plan_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    monkeypatch.setattr(agentic_store, "try_persist_strategy_plan", lambda plan: {"ok": True})
    client = TestClient(create_app())

    plan_payload = {
        "event_id": "event-123",
        "market_id": "market-123",
        "plan_owner": "janus_internal_llm",
        "context_summary": {"thesis": "test plan"},
        "active_strategies": [
            {
                "strategy_id": "grid-1",
                "family": "resistance_band_rebound_grid",
                "side": "underdog",
                "budget_usd": 5.0,
                "max_positions": 5,
                "entry_rules": {"price_band": [0.05, 0.20]},
                "exit_rules": {"target_cents": 3},
                "stop_rules": {"max_loss_cents": 2},
                "hedge_rules": {},
                "revision_triggers": [{"type": "score_gap"}],
                "shadow_flags": {},
            }
        ],
        "trigger_conditions": [{"type": "orderbook_fresh"}],
        "portfolio_reconciliation": [{"action": "adopt"}],
        "explainability": {"why": "fixture"},
    }

    submit_response = client.post("/v1/events/event-123/strategy-plan", json=plan_payload)
    read_response = client.get("/v1/events/event-123/strategy-plan/current")

    assert submit_response.status_code == 201
    assert submit_response.json()["strategy_count"] == 1
    assert read_response.status_code == 200
    assert read_response.json()["active_strategies"][0]["strategy_id"] == "grid-1"


def test_watchlist_and_ops_cycle_endpoints_record_runtime_artifacts_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    monkeypatch.setattr(ops_router, "try_persist_watchlist_event", lambda event, *, source: {"ok": True})
    client = TestClient(create_app())

    watch_response = client.post(
        "/v1/watchlists/events",
        json={
            "source": "codex",
            "events": [
                {
                    "event_key": "btc-updown-15m-demo",
                    "category": "crypto_options",
                    "title": "BTC up or down demo",
                    "source_urls": ["https://polymarket.com/event/demo"],
                    "passive_only": True,
                }
            ],
        },
    )
    refresh_response = client.post(
        "/v1/ops/data-refresh",
        json={"session_date": "2026-05-09", "event_ids": ["event-1"], "source": "pytest"},
    )

    assert watch_response.status_code == 201
    assert watch_response.json()["event_count"] == 1
    assert refresh_response.status_code == 202
    assert (local_root / "shared" / "artifacts" / "ops" / "2026-05-09").exists()


def test_pregame_plan_endpoint_writes_shared_research_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    client = TestClient(create_app())

    response = client.post(
        "/v1/ops/pregame-plan",
        json={
            "session_date": "2026-05-10",
            "event_ids": ["0042500214"],
            "source": "pytest",
            "notes": "fixture",
            "research_markdown": "Knicks-76ers test thesis.",
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["pregame_file"]["status"] == "stored"
    research_path = local_root / "shared" / "reports" / "daily-live-validation" / "pregame_research_2026-05-10.md"
    assert "Knicks-76ers test thesis." in research_path.read_text(encoding="utf-8")


def test_live_monitor_endpoint_includes_direct_integrity_snapshot_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    fake_connection = object()

    def fake_db_connection():
        yield fake_connection

    monkeypatch.setattr(
        ops_router,
        "build_integrity_snapshot",
        lambda connection, *, account_id=None: {
            "connection_matches": connection is fake_connection,
            "account_id": account_id,
            "ready_for_live_minimum_orders": True,
        },
    )
    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection

    try:
        response = client.post(
            "/v1/ops/live-monitor",
            json={"session_date": "2026-05-10", "account_id": "account-123", "source": "pytest"},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 202
    payload = response.json()
    assert payload["integrity"]["connection_matches"] is True
    assert payload["integrity"]["account_id"] == "account-123"
    assert payload["integrity"]["ready_for_live_minimum_orders"] is True


def test_watch_session_tick_and_trade_endpoints_record_batches_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    monkeypatch.setattr(
        ops_router,
        "try_persist_watch_session",
        lambda payload: {"ok": True, "watch_session_id": payload.watch_session_id or "watch-1"},
    )
    monkeypatch.setattr(ops_router, "try_persist_orderbook_ticks", lambda payload: {"ok": True, "row_count": len(payload.ticks)})
    monkeypatch.setattr(ops_router, "try_persist_market_trades", lambda payload: {"ok": True, "row_count": len(payload.trades)})
    client = TestClient(create_app())

    session_response = client.post(
        "/v1/watchlists/sessions",
        json={"event_key": "event-1", "category": "nba", "cadence_ms": 3000, "reason": "pytest"},
    )
    tick_response = client.post(
        "/v1/watchlists/orderbook-ticks",
        json={
            "source": "pytest",
            "ticks": [
                {
                    "event_key": "event-1",
                    "market_id": "market-1",
                    "token_id": "token-1",
                    "best_bid": 0.19,
                    "best_ask": 0.2,
                }
            ],
        },
    )
    trade_response = client.post(
        "/v1/watchlists/trades",
        json={"source": "pytest", "trades": [{"event_key": "event-1", "price": 0.2, "size": 5}]},
    )

    assert session_response.status_code == 201
    assert session_response.json()["db_persistence"]["watch_session_id"] == "watch-1"
    assert tick_response.status_code == 202
    assert tick_response.json()["tick_count"] == 1
    assert trade_response.status_code == 202
    assert trade_response.json()["trade_count"] == 1
    assert (local_root / "shared" / "artifacts" / "ops").exists()


def test_strategy_plan_evaluate_endpoint_compiles_intents_without_db_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    decision_calls = []
    monkeypatch.setattr(
        ops_router,
        "try_persist_strategy_decisions",
        lambda result, **kwargs: decision_calls.append({"result": result, **kwargs}) or {"ok": True, "row_count": 1},
    )
    client = TestClient(create_app())

    response = client.post(
        "/v1/events/event-123/strategy-plan/evaluate",
        json={
            "dry_run": True,
            "market_state": {"price": 0.2, "orderbook_age_seconds": 1},
            "plan": {
                "event_id": "event-123",
                "market_id": "market-123",
                "active_strategies": [
                    {
                        "strategy_id": "grid-1",
                        "family": "resistance_band_rebound_grid",
                        "side": "underdog",
                        "budget_usd": 2.0,
                        "entry_rules": {
                            "outcome_id": "outcome-1",
                            "token_id": "token-1",
                            "side": "buy",
                            "price": 0.2,
                            "size": 5,
                            "price_band": [0.15, 0.25],
                        },
                    }
                ],
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent_count"] == 1
    assert payload["intents"][0]["strategy_family"] == "resistance_band_rebound_grid"
    assert payload["decision_persistence"] == {"ok": True, "row_count": 1}
    assert decision_calls[0]["source"] == "codex"


def test_strategy_plan_execute_endpoint_hands_intent_to_order_manager_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    client = TestClient(create_app())
    fake_connection = object()
    submitted_orders: list[dict] = []

    def fake_db_connection():
        yield fake_connection

    client.app.dependency_overrides[get_db_connection] = fake_db_connection
    monkeypatch.setattr(
        ops_router,
        "resolve_trading_account",
        lambda connection, *, account_id=None: {"account_id": account_id or "default-account"},
    )
    monkeypatch.setattr(
        ops_router,
        "try_persist_strategy_decisions",
        lambda result, **kwargs: {"ok": True, "row_count": len(result.intents) + len(result.executed_orders)},
    )

    def fake_create_live_order(connection, **kwargs):
        submitted_orders.append({"connection": connection, **kwargs})
        return {"status": "dry_run", "local_order_id": "order-1"}

    monkeypatch.setattr(ops_router, "create_live_order", fake_create_live_order)

    try:
        response = client.post(
            "/v1/events/event-123/strategy-plan/execute",
            json={
                "dry_run": True,
                "execute": True,
                "account_id": "account-123",
                "source": "pytest",
                "market_state": {"price": 0.2, "orderbook_age_seconds": 1},
                "plan": {
                    "event_id": "event-123",
                    "market_id": "market-123",
                    "active_strategies": [
                        {
                            "strategy_id": "grid-1",
                            "family": "resistance_band_rebound_grid",
                            "side": "underdog",
                            "budget_usd": 2.0,
                            "entry_rules": {
                                "outcome_id": "outcome-1",
                                "token_id": "token-1",
                                "side": "buy",
                                "price": 0.2,
                                "size": 5,
                                "price_band": [0.15, 0.25],
                            },
                        }
                    ],
                },
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 202
    payload = response.json()
    assert payload["intent_count"] == 1
    assert payload["decision_persistence"] == {"ok": True, "row_count": 2}
    assert payload["executed_orders"] == [
        {"intent_id": "event-123|grid-1|1", "status": "dry_run", "local_order_id": "order-1"}
    ]
    assert len(submitted_orders) == 1
    submitted = submitted_orders[0]
    assert submitted["connection"] is fake_connection
    assert submitted["account"]["account_id"] == "account-123"
    assert submitted["market_id"] == "market-123"
    assert submitted["outcome_id"] == "outcome-1"
    assert submitted["token_id"] == "token-1"
    assert submitted["side"] == "buy"
    assert submitted["size"] == 5
    assert submitted["price"] == 0.2
    assert submitted["order_type"] == "limit"
    assert submitted["dry_run"] is True
    assert submitted["metadata_json"]["order_policy"] == "strategy_plan_json"
