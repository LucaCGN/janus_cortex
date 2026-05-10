from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.dependencies import get_db_connection
from app.api.main import create_app
from app.api.routers import ops as ops_router


def test_ops_status_uses_repo_local_root_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))

    client = TestClient(create_app())
    response = client.get("/v1/ops/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["local_roots"]["shared_root"] == str(local_root / "shared")


def test_strategy_plan_endpoint_stores_and_reads_current_plan_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
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


def test_strategy_plan_evaluate_endpoint_compiles_intents_without_db_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
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
