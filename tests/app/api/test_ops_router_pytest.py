from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.dependencies import get_db_connection
from app.api.main import create_app
from app.api.routers import ops as ops_router
from app.modules.agentic import store as agentic_store


def _strategy_plan_payload(*, event_id: str = "event-123", market_id: str = "market-123") -> dict:
    return {
        "event_id": event_id,
        "market_id": market_id,
        "plan_owner": "janus_internal_llm",
        "context_summary": {"thesis": "test plan"},
        "active_strategies": [
            {
                "strategy_id": "grid-1",
                "family": "resistance_band_rebound_grid",
                "side": "underdog",
                "budget_usd": 5.0,
                "max_positions": 5,
                "entry_rules": {
                    "outcome_id": "outcome-1",
                    "token_id": "token-1",
                    "side": "buy",
                    "price": 0.2,
                    "size": 5,
                    "price_band": [0.15, 0.25],
                },
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

    plan_payload = _strategy_plan_payload()

    submit_response = client.post("/v1/events/event-123/strategy-plan", json=plan_payload)
    read_response = client.get("/v1/events/event-123/strategy-plan/current")

    assert submit_response.status_code == 201
    assert submit_response.json()["strategy_count"] == 1
    assert read_response.status_code == 200
    assert read_response.json()["active_strategies"][0]["strategy_id"] == "grid-1"


def test_agent_context_resolves_catalog_uuid_to_slug_current_plan_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    monkeypatch.setattr(agentic_store, "try_persist_strategy_plan", lambda plan: {"ok": True})
    event_uuid = "6121380b-9b9e-511e-a225-505cfe5ca152"
    event_slug = "nba-okc-lal-2026-05-11"
    monkeypatch.setattr(
        agentic_store,
        "resolve_catalog_event_strategy_plan_aliases",
        lambda event_id: [event_slug] if event_id == event_uuid else [],
    )
    client = TestClient(create_app())
    plan_payload = _strategy_plan_payload(event_id=event_slug, market_id="market-123")

    submit_response = client.post(
        "/v1/ops/pregame-plan",
        json={
            "session_date": "2026-05-13",
            "event_ids": [event_slug],
            "source": "pytest",
            "strategy_plans": [plan_payload],
        },
    )
    context_response = client.get(
        f"/v1/events/{event_uuid}/agent-context",
        params={"session_date": "2026-05-13"},
    )
    current_response = client.get(
        f"/v1/events/{event_uuid}/strategy-plan/current",
        params={"session_date": "2026-05-13"},
    )

    assert submit_response.status_code == 202
    assert context_response.status_code == 200
    context_payload = context_response.json()
    assert context_payload["event_id"] == event_uuid
    assert context_payload["strategy_plan_lookup_event_ids"] == [event_uuid, event_slug]
    assert context_payload["resolved_strategy_plan_event_id"] == event_slug
    assert context_payload["current_strategy_plan"]["event_id"] == event_slug
    assert current_response.status_code == 200
    assert current_response.json()["event_id"] == event_slug


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


def test_pregame_plan_endpoint_persists_strategy_plans_and_reports_gate_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    monkeypatch.setattr(agentic_store, "try_persist_strategy_plan", lambda plan: {"ok": True})
    client = TestClient(create_app())

    response = client.post(
        "/v1/ops/pregame-plan",
        json={
            "session_date": "2026-05-10",
            "event_ids": ["event-123"],
            "source": "pytest",
            "strategy_plans": [_strategy_plan_payload()],
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["strategy_plan_gate"]["status"] == "ready"
    assert payload["strategy_plan_gate"]["missing_event_ids"] == []
    assert payload["strategy_plan_records"][0]["strategy_count"] == 1
    current_path = (
        local_root
        / "shared"
        / "artifacts"
        / "strategy-plans"
        / "2026-05-10"
        / "event-123"
        / "current.json"
    )
    assert current_path.exists()


def test_live_monitor_endpoint_includes_direct_integrity_snapshot_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    fake_connection = object()

    def fake_db_connection():
        yield fake_connection

    monkeypatch.setattr(
        ops_router,
        "build_integrity_snapshot",
        lambda connection, *, account_id=None, direct_trade_token_ids=None: {
            "connection_matches": connection is fake_connection,
            "account_id": account_id,
            "direct_trade_token_ids": direct_trade_token_ids or [],
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
    assert payload["integrity"]["direct_trade_token_ids"] == []
    assert payload["integrity"]["ready_for_live_minimum_orders"] is True
    assert payload["live_monitor_readiness"]["status"] == "not_required"
    assert payload["live_monitor_readiness"]["gate"] == "YELLOW"
    assert payload["live_monitor_readiness"]["ready_for_live_execution"] is False


def test_live_monitor_endpoint_passes_current_plan_tokens_to_integrity_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    fake_connection = object()
    captured: dict[str, object] = {}

    def fake_db_connection():
        yield fake_connection

    monkeypatch.setattr(agentic_store, "try_persist_strategy_plan", lambda plan: {"ok": True})
    monkeypatch.setattr(
        ops_router,
        "build_integrity_snapshot",
        lambda connection, *, account_id=None, direct_trade_token_ids=None: captured.setdefault(
            "integrity",
            {
                "connection_matches": connection is fake_connection,
                "account_id": account_id,
                "direct_trade_token_ids": direct_trade_token_ids or [],
                "ready_for_live_minimum_orders": True,
            },
        ),
    )

    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection
    plan_payload = _strategy_plan_payload(event_id="event-123", market_id="market-123")
    plan_payload["active_strategies"][0]["sleeve_id"] = "underdog-grid"
    plan_payload["active_strategies"][0]["sleeve_group"] = "underdog"
    plan_payload["active_strategies"][0]["sleeve_role"] = "standard_entry"
    plan_payload["active_strategies"].append(
        {
            **plan_payload["active_strategies"][0],
            "strategy_id": "grid-2",
            "sleeve_id": "favorite-grid",
            "sleeve_group": "favorite",
            "sleeve_role": "reviewed_q4_clutch",
            "entry_rules": {
                **plan_payload["active_strategies"][0]["entry_rules"],
                "outcome_id": "outcome-2",
                "token_id": "token-2",
            },
        }
    )

    try:
        submit_response = client.post(
            "/v1/ops/pregame-plan",
            json={
                "session_date": "2026-05-11",
                "event_ids": ["event-123"],
                "source": "pytest",
                "strategy_plans": [plan_payload],
            },
        )
        response = client.post(
            "/v1/ops/live-monitor",
            json={"session_date": "2026-05-11", "event_ids": ["event-123"], "account_id": "account-123", "source": "pytest"},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert submit_response.status_code == 202
    assert response.status_code == 202
    payload = response.json()
    assert payload["integrity"]["direct_trade_token_ids"] == ["token-1", "token-2"]
    assert payload["live_strategy_worker_status"]["status"] == "blocked"
    assert payload["live_strategy_worker_status"]["blocker_reason"] == "live_strategy_worker_not_running"
    assert payload["live_strategy_worker_status"]["expected_event_ids"] == ["event-123"]
    assert payload["live_monitor_readiness"]["status"] == "blocked"
    assert payload["live_monitor_readiness"]["gate"] == "RED"
    assert payload["live_monitor_readiness"]["blocker_reasons"] == ["live_strategy_worker_not_running"]
    assert payload["live_monitor_readiness"]["ready_for_live_execution"] is False
    current_plan = payload["strategy_plan_gate"]["current_plans"][0]
    assert current_plan["sleeve_count"] == 2
    assert [
        (sleeve["sleeve_id"], sleeve["sleeve_group"], sleeve["sleeve_role"])
        for sleeve in current_plan["sleeves"]
    ] == [
        ("underdog-grid", "underdog", "standard_entry"),
        ("favorite-grid", "favorite", "reviewed_q4_clutch"),
    ]


def test_live_monitor_endpoint_exposes_current_event_inventory_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    fake_connection = object()

    def fake_db_connection():
        yield fake_connection

    monkeypatch.setattr(agentic_store, "try_persist_strategy_plan", lambda plan: {"ok": True})
    monkeypatch.setattr(
        ops_router,
        "build_integrity_snapshot",
        lambda connection, *, account_id=None, direct_trade_token_ids=None: {
            "ready_for_live_minimum_orders": True,
            "direct_clob": {
                "ok": True,
                "open_order_count": 2,
                "open_orders": {
                    "ok": True,
                    "orders": [
                        {
                            "id": "0xevent",
                            "market": "condition-123",
                            "token_id": "token-1",
                            "side": "BUY",
                            "price": 0.28,
                            "size": 20,
                        },
                        {
                            "id": "0xsibling",
                            "market": "condition-123",
                            "token_id": "token-sibling",
                            "side": "SELL",
                            "price": 0.33,
                            "size": 27.27,
                        },
                        {
                            "id": "0xother",
                            "market": "condition-other",
                            "token_id": "token-other",
                            "side": "BUY",
                            "price": 0.2,
                            "size": 20,
                        },
                    ],
                },
                "open_positions": {
                    "ok": True,
                    "positions": [
                        {
                            "asset": "token-sibling",
                            "condition_id": "condition-123",
                            "event_slug": "event-123-slug",
                            "outcome": "Other side",
                            "size": 27.2744,
                        },
                        {
                            "asset": "token-other",
                            "condition_id": "condition-other",
                            "event_slug": "other-event",
                            "outcome": "Other event",
                            "size": 5,
                        },
                    ],
                },
                "current_token_trades": {
                    "ok": True,
                    "trades": [
                        {
                            "id": "trade-1",
                            "asset_id": "token-1",
                            "market": "condition-123",
                            "side": "BUY",
                            "price": 0.31,
                            "size": 32.25,
                        }
                    ],
                },
            },
        },
    )

    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection
    plan_payload = _strategy_plan_payload(event_id="event-123", market_id="market-123")
    plan_payload["context_summary"]["event_slug"] = "event-123-slug"

    try:
        submit_response = client.post(
            "/v1/ops/pregame-plan",
            json={
                "session_date": "2026-05-11",
                "event_ids": ["event-123"],
                "source": "pytest",
                "strategy_plans": [plan_payload],
            },
        )
        response = client.post(
            "/v1/ops/live-monitor",
            json={"session_date": "2026-05-11", "event_ids": ["event-123"], "account_id": "account-123", "source": "pytest"},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert submit_response.status_code == 202
    assert response.status_code == 202
    inventory = response.json()["current_event_inventory"]
    assert inventory["schema_version"] == "live_monitor_current_event_inventory_v1"
    assert inventory["open_order_count"] == 2
    assert inventory["open_position_count"] == 1
    assert inventory["trade_count"] == 1
    assert inventory["unresolved_inventory_present"] is True
    item = inventory["items"][0]
    assert item["event_id"] == "event-123"
    assert item["token_ids"] == ["token-1"]
    assert item["condition_ids"] == ["condition-123"]
    assert item["event_slugs"] == ["event-123-slug"]
    assert [order["id"] for order in item["open_orders"]] == ["0xevent", "0xsibling"]
    assert item["open_positions"][0]["asset"] == "token-sibling"
    assert item["trades"][0]["id"] == "trade-1"


def test_live_monitor_inventory_documents_resolved_residual_without_blocking_pytest(monkeypatch) -> None:
    monkeypatch.setattr(
        ops_router,
        "load_current_strategy_plan_for_event",
        lambda event_id, *, day=None: (
            {
                "context_summary": {"event_slug": "nba-sas-okc-2026-05-18"},
                "active_strategies": [{"entry_rules": {"token_id": "token-spurs"}}],
                "portfolio_reconciliation": [{"token_id": "token-thunder"}],
            },
            event_id,
            [event_id],
        ),
    )

    inventory = ops_router._build_live_monitor_current_event_inventory(
        integrity={
            "direct_clob": {
                "ok": True,
                "open_order_count": 0,
                "open_orders": {"ok": True, "orders": []},
                "open_positions": {
                    "ok": True,
                    "positions": [
                        {
                            "asset": "token-thunder",
                            "condition_id": "condition-okc",
                            "event_slug": "nba-sas-okc-2026-05-18",
                            "outcome": "Thunder",
                            "size": "338.4702",
                            "current_value": "0",
                            "settlement_residual": {
                                "resolved_market": {
                                    "resolved": True,
                                    "condition_id": "condition-okc",
                                    "market_slug": "nba-sas-okc-2026-05-18",
                                    "winning_token_id": "token-spurs",
                                    "payouts": {"token-spurs": "1", "token-thunder": "0"},
                                },
                                "issue_link": "https://github.com/LucaCGN/janus_cortex/issues/58",
                                "post_redeem_recheck_plan": "Recheck direct account and Janus settlement ledger before closure.",
                            },
                        }
                    ],
                },
                "current_token_trades": {"ok": True, "trades": []},
            }
        },
        event_ids=["nba-sas-okc-2026-05-18"],
        day="2026-05-18",
    )

    assert inventory["open_position_count"] == 1
    assert inventory["active_open_position_count"] == 0
    assert inventory["documented_residual_position_count"] == 1
    assert inventory["unresolved_inventory_present"] is False
    item = inventory["items"][0]
    assert item["open_position_count"] == 1
    assert item["active_open_position_count"] == 0
    assert item["documented_residual_position_count"] == 1
    assert item["unresolved_inventory_present"] is False
    residual = item["documented_residual_positions"][0]
    assert residual["classification"]["residual_type"] == "zero_value_residual"
    assert residual["classification"]["live_readiness_blocker"] is False
    assert item["blocked_residual_classifications"] == []


def test_live_monitor_endpoint_returns_compact_microstructure_context_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    fake_connection = object()

    def fake_db_connection():
        yield fake_connection

    monkeypatch.setattr(agentic_store, "try_persist_strategy_plan", lambda plan: {"ok": True})
    monkeypatch.setattr(
        ops_router,
        "build_integrity_snapshot",
        lambda connection, *, account_id=None, direct_trade_token_ids=None: {
            "connection_matches": connection is fake_connection,
            "direct_trade_token_ids": direct_trade_token_ids or [],
            "ready_for_live_minimum_orders": True,
        },
    )
    monkeypatch.setattr(
        ops_router,
        "_build_event_review_runtime_evidence",
        lambda connection, *, event_id: {
            "schema_version": "event_review_runtime_evidence_v1",
            "event_id": event_id,
            "status": "ready",
            "errors": [],
            "orderbook_ticks": [
                {
                    "captured_at": "2026-05-10T20:00:00+00:00",
                    "outcome_id": "outcome-1",
                    "spread": 0.01,
                    "mid_price": 0.45,
                    "raw_json": {"trace": {"latest_state": {"period": 2, "clock": "06:00"}}},
                },
                {
                    "captured_at": "2026-05-10T20:00:00+00:00",
                    "outcome_id": "outcome-2",
                    "spread": 0.01,
                    "mid_price": 0.55,
                    "raw_json": {"trace": {"latest_state": {"period": 2, "clock": "06:00"}}},
                },
                {
                    "captured_at": "2026-05-10T20:01:00+00:00",
                    "outcome_id": "outcome-1",
                    "spread": 0.01,
                    "mid_price": 0.52,
                    "raw_json": {"trace": {"latest_state": {"period": 2, "clock": "05:00"}}},
                },
                {
                    "captured_at": "2026-05-10T20:01:00+00:00",
                    "outcome_id": "outcome-2",
                    "spread": 0.01,
                    "mid_price": 0.48,
                    "raw_json": {"trace": {"latest_state": {"period": 2, "clock": "05:00"}}},
                },
            ],
            "orderbook_window_summary": {"tick_count": 4},
        },
    )

    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection
    plan_payload = _strategy_plan_payload(event_id="event-123", market_id="market-123")

    try:
        submit_response = client.post(
            "/v1/ops/pregame-plan",
            json={
                "session_date": "2026-05-11",
                "event_ids": ["event-123"],
                "source": "pytest",
                "strategy_plans": [plan_payload],
            },
        )
        response = client.post(
            "/v1/ops/live-monitor",
            json={"session_date": "2026-05-11", "event_ids": ["event-123"], "source": "pytest"},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert submit_response.status_code == 202
    assert response.status_code == 202
    microstructure = response.json()["live_microstructure_context"]
    assert microstructure["schema_version"] == "live_monitor_microstructure_context_v1"
    assert microstructure["status"] == "recorded"
    assert microstructure["event_count"] == 1
    item = microstructure["items"][0]
    assert item["schema_version"] == "live_monitor_event_microstructure_context_v1"
    assert item["event_id"] == "event-123"
    assert item["tick_count"] == 4
    assert item["favorite_underdog_inversion_count"] == 1
    assert item["period_context_status"] == "recorded"
    assert item["period_summaries"][0]["period_key"] == "period_2"
    assert item["screenshot_dependency"] is False
    assert item["trading_authority"] == "review_evidence_only"


def test_live_monitor_discovers_current_plan_events_when_event_ids_omitted_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    fake_connection = object()

    def fake_db_connection():
        yield fake_connection

    monkeypatch.setattr(agentic_store, "try_persist_strategy_plan", lambda plan: {"ok": True})
    monkeypatch.setattr(
        ops_router,
        "build_integrity_snapshot",
        lambda connection, *, account_id=None, direct_trade_token_ids=None: {
            "connection_matches": connection is fake_connection,
            "direct_trade_token_ids": direct_trade_token_ids or [],
            "ready_for_live_minimum_orders": True,
        },
    )
    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection
    plan_payload = _strategy_plan_payload(event_id="event-123", market_id="market-123")
    plan_payload["generated_at_utc"] = "2026-05-13T09:00:00Z"
    plan_payload["valid_until_utc"] = "2999-01-01T00:00:00Z"

    try:
        submit_response = client.post(
            "/v1/ops/pregame-plan",
            json={
                "session_date": "2026-05-13",
                "event_ids": ["event-123"],
                "source": "pytest",
                "strategy_plans": [plan_payload],
            },
        )
        response = client.post(
            "/v1/ops/live-monitor",
            json={"session_date": "2026-05-13", "source": "pytest"},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert submit_response.status_code == 202
    assert response.status_code == 202
    payload = response.json()
    assert payload["requested_event_ids"] == []
    assert payload["resolved_event_ids"] == ["event-123"]
    assert payload["integrity"]["direct_trade_token_ids"] == ["token-1"]
    assert payload["strategy_plan_gate"]["status"] == "ready"
    assert payload["strategy_plan_gate"]["current_plans"][0]["event_id"] == "event-123"
    assert payload["live_strategy_worker_status"]["expected_event_ids"] == ["event-123"]
    assert payload["live_monitor_readiness"]["gate"] == "RED"
    assert payload["live_monitor_readiness"]["blocker_reasons"] == ["live_strategy_worker_not_running"]


def test_live_monitor_resolves_explicit_catalog_uuid_to_slug_current_plan_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    fake_connection = object()
    event_uuid = "6121380b-9b9e-511e-a225-505cfe5ca152"
    event_slug = "nba-okc-lal-2026-05-11"

    def fake_db_connection():
        yield fake_connection

    monkeypatch.setattr(agentic_store, "try_persist_strategy_plan", lambda plan: {"ok": True})
    monkeypatch.setattr(
        agentic_store,
        "resolve_catalog_event_strategy_plan_aliases",
        lambda event_id: [event_slug] if event_id == event_uuid else [],
    )
    monkeypatch.setattr(
        ops_router,
        "build_integrity_snapshot",
        lambda connection, *, account_id=None, direct_trade_token_ids=None: {
            "connection_matches": connection is fake_connection,
            "direct_trade_token_ids": direct_trade_token_ids or [],
            "ready_for_live_minimum_orders": True,
        },
    )
    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection
    plan_payload = _strategy_plan_payload(event_id=event_slug, market_id="market-123")

    try:
        submit_response = client.post(
            "/v1/ops/pregame-plan",
            json={
                "session_date": "2026-05-13",
                "event_ids": [event_slug],
                "source": "pytest",
                "strategy_plans": [plan_payload],
            },
        )
        response = client.post(
            "/v1/ops/live-monitor",
            json={"session_date": "2026-05-13", "event_ids": [event_uuid], "source": "pytest"},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert submit_response.status_code == 202
    assert response.status_code == 202
    payload = response.json()
    assert payload["requested_event_ids"] == [event_uuid]
    assert payload["resolved_event_ids"] == [event_slug]
    assert payload["integrity"]["direct_trade_token_ids"] == ["token-1"]
    assert payload["strategy_plan_gate"]["status"] == "ready"
    assert payload["strategy_plan_gate"]["missing_event_ids"] == []
    assert payload["strategy_plan_gate"]["current_plans"][0]["event_id"] == event_slug
    assert payload["live_strategy_worker_status"]["expected_event_ids"] == [event_slug]


def test_integrity_check_resolves_explicit_catalog_uuid_to_slug_current_plan_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    fake_connection = object()
    event_uuid = "6121380b-9b9e-511e-a225-505cfe5ca152"
    event_slug = "nba-okc-lal-2026-05-11"

    def fake_db_connection():
        yield fake_connection

    monkeypatch.setattr(agentic_store, "try_persist_strategy_plan", lambda plan: {"ok": True})
    monkeypatch.setattr(
        agentic_store,
        "resolve_catalog_event_strategy_plan_aliases",
        lambda event_id: [event_slug] if event_id == event_uuid else [],
    )
    monkeypatch.setattr(
        ops_router,
        "build_integrity_snapshot",
        lambda connection, *, account_id=None, direct_trade_token_ids=None: {
            "connection_matches": connection is fake_connection,
            "account_id": account_id,
            "direct_trade_token_ids": direct_trade_token_ids or [],
            "ready_for_live_minimum_orders": True,
        },
    )
    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection
    plan_payload = _strategy_plan_payload(event_id=event_slug, market_id="market-123")

    try:
        submit_response = client.post(
            "/v1/ops/pregame-plan",
            json={
                "session_date": "2026-05-13",
                "event_ids": [event_slug],
                "source": "pytest",
                "strategy_plans": [plan_payload],
            },
        )
        response = client.post(
            "/v1/ops/integrity-check",
            json={
                "session_date": "2026-05-13",
                "event_ids": [event_uuid],
                "account_id": "account-123",
                "source": "pytest",
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert submit_response.status_code == 202
    assert response.status_code == 202
    payload = response.json()
    assert payload["requested_event_ids"] == [event_uuid]
    assert payload["resolved_event_ids"] == [event_slug]
    assert payload["integrity"]["account_id"] == "account-123"
    assert payload["integrity"]["direct_trade_token_ids"] == ["token-1"]

    artifact_payload = json.loads(Path(payload["path"]).read_text(encoding="utf-8"))
    assert artifact_payload["requested_event_ids"] == [event_uuid]
    assert artifact_payload["resolved_event_ids"] == [event_slug]
    assert artifact_payload["integrity"]["direct_trade_token_ids"] == ["token-1"]


def test_live_monitor_readiness_blocks_ready_worker_without_execution_evidence_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    fake_connection = object()

    def fake_db_connection():
        yield fake_connection

    monkeypatch.setattr(agentic_store, "try_persist_strategy_plan", lambda plan: {"ok": True})
    monkeypatch.setattr(
        ops_router,
        "build_integrity_snapshot",
        lambda connection, *, account_id=None, direct_trade_token_ids=None: {
            "ready_for_live_minimum_orders": True,
            "direct_trade_token_ids": direct_trade_token_ids or [],
        },
    )
    monkeypatch.setattr(
        ops_router,
        "build_live_strategy_worker_readiness",
        lambda *, session_date, event_ids, strategy_plan_gate: {
            "schema_version": "live_strategy_worker_monitor_v1",
            "status": "ready",
            "blocker_reason": None,
            "worker_required": True,
            "ready_for_live_execution": True,
            "health_only_not_executor": True,
            "session_date": session_date,
            "expected_event_ids": ["event-123"],
            "worker_thread_alive": True,
            "heartbeat_present": True,
            "heartbeat_fresh": True,
            "heartbeat_age_seconds": 5,
            "heartbeat_max_age_seconds": 90,
        },
    )
    monkeypatch.setattr(
        ops_router,
        "_fetch_live_execution_evidence_counts",
        lambda connection, *, event_id: {
            "watch_session_count": 1,
            "latest_watch_session_started_at": "2026-05-13T00:00:00+00:00",
            "latest_watch_session_ended_at": None,
            "orderbook_tick_count": 0,
            "latest_orderbook_tick_at": None,
            "strategy_decision_count": 0,
            "latest_strategy_decision_at": None,
        },
    )

    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection

    try:
        submit_response = client.post(
            "/v1/ops/pregame-plan",
            json={
                "session_date": "2026-05-13",
                "event_ids": ["event-123"],
                "source": "pytest",
                "strategy_plans": [_strategy_plan_payload(event_id="event-123")],
            },
        )
        response = client.post(
            "/v1/ops/live-monitor",
            json={"session_date": "2026-05-13", "event_ids": ["event-123"], "source": "pytest"},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert submit_response.status_code == 202
    assert response.status_code == 202
    payload = response.json()
    assert payload["live_execution_evidence"]["status"] == "blocked"
    assert payload["live_execution_evidence"]["gate"] == "RED"
    assert payload["live_execution_evidence"]["blocker_reasons"] == [
        "live_orderbook_tick_missing",
        "live_strategy_decision_missing",
    ]
    assert payload["live_monitor_readiness"]["status"] == "blocked"
    assert payload["live_monitor_readiness"]["gate"] == "RED"
    assert payload["live_monitor_readiness"]["blocker_reasons"] == [
        "live_orderbook_tick_missing",
        "live_strategy_decision_missing",
    ]


def test_live_monitor_endpoint_reports_missing_strategy_plan_gate_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    fake_connection = object()

    def fake_db_connection():
        yield fake_connection

    monkeypatch.setattr(
        ops_router,
        "build_integrity_snapshot",
        lambda connection, *, account_id=None, direct_trade_token_ids=None: {
            "ready_for_live_minimum_orders": True,
            "direct_trade_token_ids": direct_trade_token_ids or [],
        },
    )
    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection

    try:
        response = client.post(
            "/v1/ops/live-monitor",
            json={"session_date": "2026-05-10", "event_ids": ["event-missing"], "source": "pytest"},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 202
    payload = response.json()
    assert payload["strategy_plan_gate"]["status"] == "blocked"
    assert payload["strategy_plan_gate"]["missing_event_ids"] == ["event-missing"]
    assert payload["strategy_plan_gate"]["ready_for_strategy_evaluation"] is False
    assert payload["live_strategy_worker_status"]["status"] == "not_required"
    assert payload["live_strategy_worker_status"]["worker_required"] is False
    assert payload["live_strategy_worker_status"]["health_only_not_executor"] is True
    assert payload["live_monitor_readiness"]["status"] == "blocked"
    assert payload["live_monitor_readiness"]["gate"] == "RED"
    assert payload["live_monitor_readiness"]["blocker_reasons"] == ["missing_current_strategy_plan"]


def test_live_strategy_worker_control_endpoints_delegate_to_service_worker_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    calls: list[tuple[str, dict | None]] = []

    class FakeWorker:
        def status(self):
            calls.append(("status", None))
            return {"status": "stopped", "worker_thread_alive": False}

        def run_once(self, overrides):
            calls.append(("run_once", overrides))
            return {"ok": True, "status": "completed", "event_ids": overrides.get("event_ids")}

        def start(self, overrides):
            calls.append(("start", overrides))
            return {"status": "running", "start_status": "started", "config": overrides}

        def stop(self):
            calls.append(("stop", None))
            return {"status": "stopped", "stop_status": "stopped"}

    fake_worker = FakeWorker()
    monkeypatch.setattr(ops_router, "get_live_strategy_worker", lambda: fake_worker)
    client = TestClient(create_app())

    status_response = client.get("/v1/ops/live-strategy-worker/status")
    tick_response = client.post(
        "/v1/ops/live-strategy-worker/tick",
        json={"session_date": "2026-05-13", "event_ids": ["event-1"], "account_id": "account-1"},
    )
    start_response = client.post(
        "/v1/ops/live-strategy-worker/start",
        json={
            "session_date": "2026-05-13",
            "event_ids": ["event-1"],
            "account_id": "account-1",
            "execute": True,
            "live_money": True,
        },
    )
    stop_response = client.post("/v1/ops/live-strategy-worker/stop")

    assert status_response.status_code == 200
    assert tick_response.status_code == 202
    assert tick_response.json()["event_ids"] == ["event-1"]
    assert start_response.status_code == 202
    assert start_response.json()["start_status"] == "started"
    assert stop_response.status_code == 202
    assert calls == [
        ("status", None),
        ("run_once", {"session_date": "2026-05-13", "event_ids": ["event-1"], "account_id": "account-1", "source": "janus-live-strategy-worker"}),
        (
            "start",
            {
                "session_date": "2026-05-13",
                "event_ids": ["event-1"],
                "account_id": "account-1",
                "source": "janus-live-strategy-worker",
                "execute": True,
                "live_money": True,
            },
        ),
        ("stop", None),
    ]


def test_live_monitor_endpoint_exposes_latest_llm_runtime_status_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    fake_connection = object()
    artifact_dir = local_root / "shared" / "artifacts" / "llm-runtime" / "2026-05-10"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "trace.json").write_text(
        json.dumps(
            {
                "schema_version": "llm_runtime_trace_artifact_v1",
                "event_id": "event-123",
                "trace_id": "trace-1",
                "status": "skipped_unavailable",
                "response_status": "skipped_unavailable",
                "trigger_count": 1,
                "trigger_types": ["quarter_end"],
                "selected_model": "gpt-5.4-mini",
                "model_routing_decision": {"selected_model": "gpt-5.4-mini", "selected_tier": "mini"},
                "response": {"status": "skipped_unavailable", "skipped_reason": "dispatch_disabled"},
                "persisted_at_utc": "2026-05-10T20:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    def fake_db_connection():
        yield fake_connection

    monkeypatch.setattr(
        ops_router,
        "build_integrity_snapshot",
        lambda connection, *, account_id=None, direct_trade_token_ids=None: {
            "ready_for_live_minimum_orders": True,
            "direct_trade_token_ids": direct_trade_token_ids or [],
        },
    )
    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection

    try:
        response = client.post(
            "/v1/ops/live-monitor",
            json={"session_date": "2026-05-10", "event_ids": ["event-123"], "source": "pytest"},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 202
    payload = response.json()
    assert payload["llm_runtime_status"]["status"] == "recorded"
    assert payload["llm_runtime_status"]["safety_controls"]["status"] == "ready"
    assert "trigger_hash_dedup" in payload["llm_runtime_status"]["safety_controls"]["implemented_controls"]
    assert payload["llm_runtime_status"]["items"][0]["event_id"] == "event-123"
    assert payload["llm_runtime_status"]["items"][0]["response_status"] == "skipped_unavailable"
    assert payload["llm_runtime_status"]["items"][0]["skipped_reason"] == "dispatch_disabled"
    assert payload["llm_runtime_status"]["items"][0]["adoption_status"] == "not_adoptable"
    assert (
        payload["llm_runtime_status"]["items"][0]["llm_revision_adoption"]["adoption_endpoint"]
        == "/v1/events/event-123/llm-revision/adopt"
    )


def test_live_monitor_endpoint_marks_recorded_llm_revision_adoptable_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    fake_connection = object()
    artifact_dir = local_root / "shared" / "artifacts" / "llm-runtime" / "2026-05-10"
    artifact_dir.mkdir(parents=True)
    artifact_path = artifact_dir / "trace.json"
    artifact_path.write_text(
        json.dumps(
            {
                "schema_version": "llm_runtime_trace_artifact_v1",
                "event_id": "event-123",
                "trace_id": "trace-1",
                "status": "response_recorded",
                "response_status": "response_recorded",
                "trigger_count": 1,
                "trigger_types": ["manual_operator_position"],
                "selected_model": "gpt-5.5",
                "model_routing_decision": {"selected_model": "gpt-5.5", "selected_tier": "frontier"},
                "response": {
                    "request_id": "request-1",
                    "status": "response_recorded",
                    "selected_model": "gpt-5.5",
                    "revised_strategy_plan": {"event_id": "event-123"},
                    "reconciliation_actions": [],
                    "blocked_actions": [],
                    "confidence": 0.81,
                    "skipped_reason": None,
                },
                "persisted_at_utc": "2026-05-10T20:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    def fake_db_connection():
        yield fake_connection

    monkeypatch.setattr(
        ops_router,
        "build_integrity_snapshot",
        lambda connection, *, account_id=None, direct_trade_token_ids=None: {
            "ready_for_live_minimum_orders": True,
            "direct_trade_token_ids": direct_trade_token_ids or [],
        },
    )
    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection

    try:
        response = client.post(
            "/v1/ops/live-monitor",
            json={"session_date": "2026-05-10", "event_ids": ["event-123"], "source": "pytest"},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 202
    item = response.json()["llm_runtime_status"]["items"][0]
    assert item["adoption_status"] == "adoptable_review_required"
    assert item["llm_revision_adoption"]["trace_artifact_path"] == str(artifact_path.resolve())
    assert item["llm_revision_adoption"]["order_endpoint_call_allowed"] is False


def test_event_review_bundle_endpoint_aggregates_review_sources_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    fake_connection = object()
    artifact_dir = local_root / "shared" / "artifacts" / "llm-runtime" / "2026-05-10"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "trace.json").write_text(
        json.dumps(
            {
                "schema_version": "llm_runtime_trace_artifact_v1",
                "event_id": "event-123",
                "trace_id": "trace-1",
                "status": "response_recorded",
                "response_status": "response_recorded",
                "trigger_count": 1,
                "trigger_types": ["quarter_end"],
                "selected_model": "gpt-5.4-mini",
                "model_routing_decision": {"selected_model": "gpt-5.4-mini", "selected_tier": "mini"},
                "response": {
                    "status": "response_recorded",
                    "selected_model": "gpt-5.4-mini",
                    "trace_metadata": {"usage": {"input_tokens": 10, "output_tokens": 5}},
                },
                "persisted_at_utc": "2026-05-10T20:01:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    runtime_evidence = {
        "schema_version": "event_review_runtime_evidence_v1",
        "event_id": "event-123",
        "status": "ready",
        "errors": [],
        "market_event": {"event_key": "event-123", "title": "Test event"},
        "market_outcomes": [{"outcome_id": "outcome-1", "label": "Home"}, {"outcome_id": "outcome-2", "label": "Away"}],
        "watch_sessions": [{"watch_session_id": "watch-1", "started_at": "2026-05-10T20:00:00+00:00"}],
        "orderbook_ticks": [
            {
                "captured_at": "2026-05-10T20:00:30+00:00",
                "outcome_id": "outcome-1",
                "spread": 0.01,
                "mid_price": 0.48,
                "raw_json": {"trace": {"latest_state": {"period": 1, "clock": "08:00", "clock_seconds_remaining": 480}}},
            },
            {
                "captured_at": "2026-05-10T20:00:30+00:00",
                "outcome_id": "outcome-2",
                "spread": 0.01,
                "mid_price": 0.52,
                "raw_json": {"trace": {"latest_state": {"period": 1, "clock": "08:00", "clock_seconds_remaining": 480}}},
            },
            {
                "captured_at": "2026-05-10T20:01:00+00:00",
                "outcome_id": "outcome-1",
                "spread": 0.01,
                "mid_price": 0.52,
                "raw_json": {"trace": {"latest_state": {"period": 1, "clock": "07:30", "clock_seconds_remaining": 450}}},
            },
            {
                "captured_at": "2026-05-10T20:01:00+00:00",
                "outcome_id": "outcome-2",
                "spread": 0.01,
                "mid_price": 0.48,
                "raw_json": {"trace": {"latest_state": {"period": 1, "clock": "07:30", "clock_seconds_remaining": 450}}},
            },
            {
                "captured_at": "2026-05-10T20:01:30+00:00",
                "outcome_id": "outcome-1",
                "spread": 0.01,
                "mid_price": 0.49,
                "raw_json": {"trace": {"latest_state": {"period": 2, "clock": "11:30", "clock_seconds_remaining": 690}}},
            },
            {
                "captured_at": "2026-05-10T20:01:30+00:00",
                "outcome_id": "outcome-2",
                "spread": 0.01,
                "mid_price": 0.51,
                "raw_json": {"trace": {"latest_state": {"period": 2, "clock": "11:30", "clock_seconds_remaining": 690}}},
            },
            {
                "captured_at": "2026-05-10T20:02:00+00:00",
                "outcome_id": "outcome-1",
                "spread": 0.01,
                "mid_price": 0.54,
                "raw_json": {"trace": {"latest_state": {"period": 2, "clock": "11:00", "clock_seconds_remaining": 660}}},
            },
            {
                "captured_at": "2026-05-10T20:02:00+00:00",
                "outcome_id": "outcome-2",
                "spread": 0.01,
                "mid_price": 0.46,
                "raw_json": {"trace": {"latest_state": {"period": 2, "clock": "11:00", "clock_seconds_remaining": 660}}},
            },
        ],
        "market_trades": [{"trade_time": "2026-05-10T20:02:00+00:00", "side": "BUY", "price": 0.51, "size": 5}],
        "strategy_decisions": [
            {
                "strategy_decision_id": "decision-1",
                "decided_at": "2026-05-10T20:00:45+00:00",
                "strategy_id": "grid-1",
                "decision_type": "order_intent",
            }
        ],
        "operator_interventions": [],
        "replay_sessions": [],
        "orderbook_window_summary": {
            "tick_count": 1,
            "first_captured_at": "2026-05-10T20:00:30+00:00",
            "last_captured_at": "2026-05-10T20:00:30+00:00",
        },
    }
    monkeypatch.setattr(ops_router, "_build_event_review_runtime_evidence", lambda connection, *, event_id: runtime_evidence)
    monkeypatch.setattr(
        ops_router,
        "_build_postgame_live_evidence",
        lambda connection, *, event_ids, day: {
            "schema_version": "postgame_live_evidence_v1",
            "status": "live_evidence_present",
            "gate": "GREEN",
            "event_count": len(event_ids),
            "items": [{"event_id": event_ids[0], "status": "live_evidence_present", "blockers": [], "warnings": []}],
        },
    )
    captured_pnl_attribution_days: list[str | None] = []

    def fake_postgame_portfolio_pnl_attribution(connection, payload, *, event_ids, day):
        captured_pnl_attribution_days.append(day)
        return {
            "status": "ready",
            "source": "pytest",
            "event_count": len(event_ids),
            "items": [{"event_id": event_ids[0], "pnl_attribution": {"pnl_attribution_ready": True}}],
        }

    monkeypatch.setattr(
        ops_router,
        "_build_postgame_portfolio_pnl_attribution",
        fake_postgame_portfolio_pnl_attribution,
    )

    def fake_db_connection():
        yield fake_connection

    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection
    client.post(
        "/v1/ops/pregame-plan",
        json={
            "session_date": "2026-05-10",
            "event_ids": ["event-123"],
            "source": "pytest",
            "strategy_plans": [_strategy_plan_payload()],
        },
    )

    try:
        response = client.get(
            "/v1/events/event-123/review-bundle",
            params={"session_date": "2026-05-10", "account_id": "account-1"},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "event_review_bundle_v1"
    assert payload["event_id"] == "event-123"
    assert payload["strategy_plan_versions"]["current_exists"] is True
    assert payload["runtime_evidence"]["market_event"]["title"] == "Test event"
    assert payload["llm_runtime_status"]["status"] == "recorded"
    assert payload["portfolio_pnl_attribution"]["status"] == "ready"
    assert captured_pnl_attribution_days == ["2026-05-10"]
    microstructure = payload["market_microstructure"]
    assert microstructure["favorite_underdog_inversion_count"] == 3
    assert microstructure["price_inversion_point_count"] == 4
    assert microstructure["oscillation_band_count"] == 4
    assert microstructure["grid_opportunity_count"] == 6
    assert microstructure["trend_profile"] == "jagged_oscillation"
    assert microstructure["outcome_summaries"]["outcome-1"]["spike_count"] == 3
    assert microstructure["outcome_summaries"]["outcome-2"]["grid_opportunity_count"] == 3
    assert microstructure["period_context_status"] == "recorded"
    assert microstructure["period_summary_count"] == 2
    assert microstructure["period_summaries"]["period_1"]["first_clock"] == "08:00"
    assert microstructure["period_summaries"]["period_2"]["last_clock"] == "11:00"
    assert microstructure["period_summaries"]["period_2"]["grid_opportunity_count"] == 2
    timeline = payload["decision_timeline"]
    assert timeline["entry_count"] >= 5
    assert timeline["kind_counts"]["order_intent"] == 1
    assert timeline["kind_counts"]["llm_runtime_trace"] == 1
    assert payload["missed_opportunities"]["schema_version"] == "event_missed_opportunity_candidates_v1"
    assert payload["token_cost_timeline"]["entry_count"] == 1
    assert payload["timeline_slices"]["schema_version"] == "event_timeline_slices_v1"
    assert payload["postgame_tooling_status"]["screenshot_dependency"] is False


def test_event_review_microstructure_classifies_smooth_and_noisy_profiles_pytest() -> None:
    def tick(timestamp: str, outcome_id: str, mid_price: float) -> dict:
        return {
            "captured_at": timestamp,
            "outcome_id": outcome_id,
            "spread": 0.01,
            "mid_price": mid_price,
            "raw_json": {"trace": {"latest_state": {"period": 4, "clock": "04:00", "clock_seconds_remaining": 240}}},
        }

    smooth = ops_router._build_event_review_microstructure_summary(
        {
            "orderbook_ticks": [
                tick("2026-05-10T20:00:00+00:00", "favorite", 0.55),
                tick("2026-05-10T20:00:00+00:00", "underdog", 0.45),
                tick("2026-05-10T20:01:00+00:00", "favorite", 0.65),
                tick("2026-05-10T20:01:00+00:00", "underdog", 0.35),
                tick("2026-05-10T20:02:00+00:00", "favorite", 0.75),
                tick("2026-05-10T20:02:00+00:00", "underdog", 0.25),
                tick("2026-05-10T20:03:00+00:00", "favorite", 0.88),
                tick("2026-05-10T20:03:00+00:00", "underdog", 0.12),
            ],
            "orderbook_window_summary": {},
        }
    )

    assert smooth["trend_profile"] == "smooth_trend"
    assert smooth["period_context_status"] == "recorded"
    assert smooth["period_summaries"]["period_4"]["trend_profile"] == "smooth_trend"
    assert smooth["outcome_summaries"]["favorite"]["oscillation_band_count"] == 0

    noisy = ops_router._build_event_review_microstructure_summary(
        {
            "orderbook_ticks": [
                tick("2026-05-10T20:00:00+00:00", "favorite", 0.85),
                tick("2026-05-10T20:00:00+00:00", "underdog", 0.15),
                tick("2026-05-10T20:01:00+00:00", "favorite", 0.70),
                tick("2026-05-10T20:01:00+00:00", "underdog", 0.30),
                tick("2026-05-10T20:02:00+00:00", "favorite", 0.76),
                tick("2026-05-10T20:02:00+00:00", "underdog", 0.24),
                tick("2026-05-10T20:03:00+00:00", "favorite", 0.55),
                tick("2026-05-10T20:03:00+00:00", "underdog", 0.45),
                tick("2026-05-10T20:04:00+00:00", "favorite", 0.60),
                tick("2026-05-10T20:04:00+00:00", "underdog", 0.40),
                tick("2026-05-10T20:05:00+00:00", "favorite", 0.32),
                tick("2026-05-10T20:05:00+00:00", "underdog", 0.68),
            ],
            "orderbook_window_summary": {},
        }
    )

    assert noisy["trend_profile"] == "jagged_oscillation"
    assert noisy["favorite_underdog_inversion_count"] == 1
    assert noisy["period_summaries"]["period_4"]["oscillation_band_count"] >= 6


def test_event_review_microstructure_spread_adjusts_thresholds_pytest() -> None:
    summary = ops_router._build_event_review_microstructure_summary(
        {
            "orderbook_ticks": [
                {
                    "captured_at": "2026-05-18T20:00:00+00:00",
                    "outcome_id": "wide",
                    "spread": 0.08,
                    "mid_price": 0.50,
                },
                {
                    "captured_at": "2026-05-18T20:01:00+00:00",
                    "outcome_id": "wide",
                    "spread": 0.08,
                    "mid_price": 0.53,
                },
                {
                    "captured_at": "2026-05-18T20:02:00+00:00",
                    "outcome_id": "wide",
                    "spread": 0.08,
                    "mid_price": 0.50,
                },
            ],
            "orderbook_window_summary": {},
        }
    )

    assert summary["threshold_calibration_status"] == "spread_adjusted"
    assert summary["trading_authority_status"] == "review_only_thresholds_pending_backtest"
    thresholds = summary["outcome_summaries"]["wide"]["threshold_calibration"]
    assert thresholds["observed_median_spread"] == 0.08
    assert thresholds["grid_move_threshold"] == 0.08
    assert thresholds["spike_move_threshold"] == 0.12
    assert thresholds["direction_noise_floor"] == 0.08
    assert summary["outcome_summaries"]["wide"]["grid_opportunity_count"] == 0
    assert summary["outcome_summaries"]["wide"]["spike_count"] == 0
    assert summary["outcome_summaries"]["wide"]["oscillation_band_count"] == 0


def test_event_review_microstructure_aligns_ticks_to_persisted_pbp_context_pytest() -> None:
    summary = ops_router._build_event_review_microstructure_summary(
        {
            "orderbook_ticks": [
                {
                    "captured_at": "2026-05-18T20:02:00+00:00",
                    "outcome_id": "favorite",
                    "spread": 0.01,
                    "mid_price": 0.58,
                    "raw_json": {},
                },
                {
                    "captured_at": "2026-05-18T20:03:00+00:00",
                    "outcome_id": "favorite",
                    "spread": 0.01,
                    "mid_price": 0.62,
                    "raw_json": {},
                },
            ],
            "play_by_play_context": [
                {
                    "league": "wnba",
                    "game_id": "1022600029",
                    "event_index": 42,
                    "time_actual": "2026-05-18T20:02:20+00:00",
                    "period": 3,
                    "clock": "06:44",
                    "description": "made jump shot",
                },
                {
                    "league": "wnba",
                    "game_id": "1022600029",
                    "event_index": 43,
                    "time_actual": "2026-05-18T20:02:50+00:00",
                    "period": 3,
                    "clock": "06:14",
                    "description": "defensive rebound",
                },
            ],
            "orderbook_window_summary": {},
        }
    )

    assert summary["play_by_play_context_status"] == "recorded"
    assert summary["play_by_play_event_count"] == 2
    assert summary["pbp_alignment_status"] == "recorded"
    assert summary["pbp_aligned_tick_count"] == 2
    assert summary["period_context_status"] == "recorded"
    period_summary = summary["period_summaries"]["period_3"]
    assert period_summary["context_sources"] == ["play_by_play_context.wnba"]
    assert period_summary["first_clock"] == "06:44"
    assert period_summary["last_clock"] == "06:14"
    assert period_summary["grid_opportunity_count"] == 1


def test_manual_order_assistant_endpoint_records_preview_and_never_raw_exchange_pytest(monkeypatch) -> None:
    monkeypatch.setattr(
        ops_router,
        "_fetch_manual_order_assistant_outcome_mapping",
        lambda *args, **kwargs: {
            "event_id": "event-123",
            "market_id": "market-123",
            "outcome_id": "outcome-123",
            "token_id": "token-123",
        },
    )
    monkeypatch.setattr(
        ops_router,
        "_fetch_manual_order_assistant_orderbook",
        lambda *args, **kwargs: {
            "event_id": "event-123",
            "market_id": "market-123",
            "outcome_id": "outcome-123",
            "token_id": "token-123",
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "best_bid": 0.001,
            "best_ask": 0.002,
            "spread_cents": 0.1,
            "bid_depth": 100,
            "ask_depth": 100,
        },
    )
    monkeypatch.setattr(
        ops_router,
        "_fetch_manual_order_assistant_inventory",
        lambda *args, **kwargs: {
            "open_orders": [],
            "pending_intents": [],
            "unresolved_inventory_present": False,
        },
    )
    monkeypatch.setattr(ops_router, "_write_manual_order_assistant_artifact", lambda **kwargs: {"status": "stored", "path": "artifact.json"})
    monkeypatch.setattr(ops_router, "try_persist_operator_intervention", lambda payload: {"status": "stored"})
    monkeypatch.setattr(
        ops_router,
        "create_live_order",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("preview must not place orders")),
    )

    class FakeConnection:
        pass

    def fake_db_connection():
        yield FakeConnection()

    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection
    try:
        response = client.post(
            "/v1/events/event-123/manual-order-assistant",
            json={
                "account_id": "account-1",
                "market_id": "market-123",
                "outcome_id": "outcome-123",
                "token_id": "token-123",
                "side": "buy",
                "order_type": "limit",
                "limit_price": 0.001,
                "size": 100,
                "max_price": 0.001,
                "max_notional_usd": 0.1,
                "actor": "codex",
                "reason": "low-price tail preview from realized profit",
                "execute": False,
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "preview_ready"
    assert payload["approved"] is True
    assert payload["raw_exchange_order_allowed"] is False
    assert payload["executed_order"] is None


def test_manual_order_assistant_execute_market_exception_requires_review_metadata_pytest(monkeypatch) -> None:
    monkeypatch.setattr(
        ops_router,
        "_fetch_manual_order_assistant_outcome_mapping",
        lambda *args, **kwargs: {
            "event_id": "event-123",
            "market_id": "market-123",
            "outcome_id": "outcome-123",
            "token_id": "token-123",
        },
    )
    monkeypatch.setattr(
        ops_router,
        "_fetch_manual_order_assistant_orderbook",
        lambda *args, **kwargs: {
            "event_id": "event-123",
            "market_id": "market-123",
            "outcome_id": "outcome-123",
            "token_id": "token-123",
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "best_bid": 0.44,
            "best_ask": 0.45,
            "spread_cents": 1.0,
            "bid_depth": 100,
            "ask_depth": 100,
        },
    )
    monkeypatch.setattr(
        ops_router,
        "_fetch_manual_order_assistant_inventory",
        lambda *args, **kwargs: {
            "open_orders": [],
            "pending_intents": [],
            "unresolved_inventory_present": False,
        },
    )
    monkeypatch.setattr(ops_router, "_write_manual_order_assistant_artifact", lambda **kwargs: {"status": "stored", "path": "artifact.json"})
    monkeypatch.setattr(ops_router, "try_persist_operator_intervention", lambda payload: {"status": "stored"})
    monkeypatch.setattr(
        ops_router,
        "create_live_order",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("blocked market exception must not place orders")),
    )

    class FakeConnection:
        pass

    def fake_db_connection():
        yield FakeConnection()

    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection
    try:
        response = client.post(
            "/v1/events/event-123/manual-order-assistant",
            json={
                "account_id": "account-1",
                "market_id": "market-123",
                "outcome_id": "outcome-123",
                "token_id": "token-123",
                "side": "sell",
                "order_type": "market",
                "size": 5,
                "max_notional_usd": 3.0,
                "actor": "codex",
                "reason": "urgent profit spike review",
                "execute": True,
                "allow_market_urgent_profit_capture": True,
                "urgent_profit_capture_reason": "profit spike likely to mean revert",
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["approved"] is False
    assert payload["executed_order"] is None
    assert {item["reason"] for item in payload["blockers"]} == {
        "market_order_max_slippage_required",
        "market_order_operator_review_required",
    }


def test_llm_revision_adoption_requires_review_and_writes_current_plan_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    persist_calls = []
    monkeypatch.setattr(agentic_store, "try_persist_strategy_plan", lambda plan: persist_calls.append(plan) or {"ok": True})
    monkeypatch.setattr(
        ops_router,
        "create_live_order",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LLM adoption must not call order endpoints")),
    )
    client = TestClient(create_app())
    current_plan = _strategy_plan_payload(event_id="event-123", market_id="market-123")
    revised_plan = _strategy_plan_payload(event_id="event-123", market_id="market-123")
    revised_plan["active_strategies"][0]["strategy_id"] = "grid-revised"
    revised_plan["active_strategies"][0]["entry_rules"]["price"] = 0.22

    pregame_response = client.post(
        "/v1/ops/pregame-plan",
        json={
            "session_date": "2026-05-12",
            "event_ids": ["event-123"],
            "source": "pytest",
            "strategy_plans": [current_plan],
        },
    )
    adoption_response = client.post(
        "/v1/events/event-123/llm-revision/adopt",
        json={
            "session_date": "2026-05-12",
            "source": "pytest",
            "reviewed_by": "pytest-reviewer",
            "review_reason": "mocked valid LLM revision",
            "apply_current": True,
            "response": {
                "request_id": "llm-request-1",
                "status": "response_recorded",
                "selected_model": "gpt-5.5",
                "revised_strategy_plan": revised_plan,
                "reconciliation_actions": [{"action": "revise_plan"}],
                "blocked_actions": [],
                "confidence": 0.82,
                "skipped_reason": None,
                "trace_metadata": {"usage": {"input_tokens": 10, "output_tokens": 20}},
            },
        },
    )
    read_response = client.get("/v1/events/event-123/strategy-plan/current?session_date=2026-05-12")

    assert pregame_response.status_code == 202
    assert adoption_response.status_code == 202
    payload = adoption_response.json()
    assert payload["status"] == "adopted_current"
    assert payload["order_endpoint_call_allowed"] is False
    assert payload["plan_diff"]["added_strategy_ids"] == ["grid-revised"]
    assert payload["plan_diff"]["removed_strategy_ids"] == ["grid-1"]
    assert payload["strategy_plan_record"]["status"] == "stored"
    assert Path(payload["adoption_artifact"]["path"]).exists()
    assert read_response.status_code == 200
    current = read_response.json()
    assert current["active_strategies"][0]["strategy_id"] == "grid-revised"
    adoption = current["explainability"]["llm_revision_adoption"]
    assert adoption["reviewed_by"] == "pytest-reviewer"
    assert adoption["order_endpoint_call_allowed"] is False
    assert len(persist_calls) == 2


def test_codex_fallback_strategy_plan_adoption_still_uses_safety_gates_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    persist_plan_calls = []
    decision_calls = []
    order_calls = []
    monkeypatch.setattr(
        agentic_store,
        "try_persist_strategy_plan",
        lambda plan: persist_plan_calls.append(plan) or {"ok": True},
    )
    monkeypatch.setattr(
        ops_router,
        "try_persist_strategy_decisions",
        lambda result, **kwargs: decision_calls.append({"result": result, **kwargs}) or {"ok": True, "row_count": 1},
    )
    monkeypatch.setattr(
        ops_router,
        "create_live_order",
        lambda *args, **kwargs: order_calls.append({"args": args, "kwargs": kwargs}) or {"ok": False},
    )
    client = TestClient(create_app())
    codex_plan = _strategy_plan_payload(event_id="event-123", market_id="market-123")
    codex_plan["plan_owner"] = "codex_agent"
    codex_plan["context_summary"] = {
        "codex_fallback_state": {
            "reason_code": "llm_event_budget_exceeded",
            "must_use_janus_validators": True,
        }
    }
    codex_plan["active_strategies"][0]["strategy_id"] = "codex-fallback-grid"
    codex_plan["active_strategies"][0]["entry_rules"].update(
        {
            "max_orderbook_age_seconds": 90,
            "max_scoreboard_age_seconds": 90,
            "max_spread_cents": 2,
            "max_abs_score_gap": 10,
            "max_open_positions": 1,
            "allow_ultra_low_underdog": True,
        }
    )

    adoption_response = client.post(
        "/v1/events/event-123/llm-revision/adopt",
        json={
            "session_date": "2026-05-12",
            "source": "codex-fallback-validation",
            "reviewed_by": "pytest-reviewer",
            "review_reason": "budget blocked internal LLM; reviewed Codex StrategyPlanJSON fallback",
            "apply_current": True,
            "response": {
                "request_id": "codex-fallback-request-1",
                "status": "response_recorded",
                "selected_model": "codex-fallback-reviewed",
                "revised_strategy_plan": codex_plan,
                "reconciliation_actions": [{"action": "revise_plan"}],
                "blocked_actions": [{"action": "raw_order", "reason": "validators_required"}],
                "confidence": 0.66,
                "skipped_reason": None,
                "trace_metadata": {
                    "codex_strategy_required": True,
                    "reason_code": "llm_event_budget_exceeded",
                    "order_endpoint_call_allowed": False,
                },
            },
        },
    )
    blocked_response = client.post(
        "/v1/events/event-123/strategy-plan/evaluate",
        json={
            "dry_run": True,
            "session_date": "2026-05-12",
            "source": "codex-fallback-validation",
            "market_state": {"price": 0.2},
            "portfolio_state": {"open_positions": 0, "open_orders": 0},
        },
    )
    valid_response = client.post(
        "/v1/events/event-123/strategy-plan/evaluate",
        json={
            "dry_run": True,
            "session_date": "2026-05-12",
            "source": "codex-fallback-validation",
            "market_state": {
                "price": 0.2,
                "orderbook_age_seconds": 1,
                "scoreboard_age_seconds": 1,
                "spread": 0.01,
                "score_gap": 4,
            },
            "portfolio_state": {"open_positions": 0, "open_orders": 0},
        },
    )
    current_response = client.get("/v1/events/event-123/strategy-plan/current?session_date=2026-05-12")

    assert adoption_response.status_code == 202
    assert adoption_response.json()["order_endpoint_call_allowed"] is False
    assert blocked_response.status_code == 200
    blocked_payload = blocked_response.json()
    assert blocked_payload["intent_count"] == 0
    assert blocked_payload["blockers"][0]["reason"] == "orderbook_freshness_required"
    assert valid_response.status_code == 200
    valid_payload = valid_response.json()
    assert valid_payload["intent_count"] == 1
    assert valid_payload["intents"][0]["dry_run"] is True
    assert current_response.status_code == 200
    current_plan = current_response.json()
    assert current_plan["plan_owner"] == "codex_agent"
    assert current_plan["explainability"]["llm_revision_adoption"]["order_endpoint_call_allowed"] is False
    assert current_plan["explainability"]["llm_revision_adoption"]["trace_metadata"]["codex_strategy_required"] is True
    assert len(persist_plan_calls) == 1
    assert [call["source"] for call in decision_calls] == ["codex-fallback-validation", "codex-fallback-validation"]
    assert order_calls == []


def test_llm_revision_adoption_stamps_quarter_end_review_marker_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    persist_calls = []
    monkeypatch.setattr(agentic_store, "try_persist_strategy_plan", lambda plan: persist_calls.append(plan) or {"ok": True})
    client = TestClient(create_app())
    current_plan = _strategy_plan_payload(event_id="event-123", market_id="market-123")
    revised_plan = _strategy_plan_payload(event_id="event-123", market_id="market-123")
    revised_plan["active_strategies"][0]["strategy_id"] = "halftime-watch"
    trace_path = local_root / "shared" / "artifacts" / "llm-runtime" / "2026-05-12" / "trace.json"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text(
        json.dumps(
            {
                "schema_version": "llm_runtime_trace_artifact_v1",
                "event_id": "event-123",
                "trace_id": "trace-quarter-end",
                "trigger_types": ["quarter_end"],
                "trigger_list": [
                    {
                        "trigger_type": "quarter_end",
                        "evidence": {"period": 2, "clock": "PT00M00.00S"},
                    }
                ],
                "selected_model": "gpt-5.4-mini",
                "persisted_at_utc": "2026-05-12T01:00:00Z",
                "response": {
                    "request_id": "llm-request-quarter-end",
                    "status": "response_recorded",
                    "selected_model": "gpt-5.4-mini",
                    "revised_strategy_plan": revised_plan,
                    "reconciliation_actions": [{"action": "revise_plan"}],
                    "blocked_actions": [],
                    "confidence": 0.9,
                    "skipped_reason": None,
                },
            }
        ),
        encoding="utf-8",
    )

    pregame_response = client.post(
        "/v1/ops/pregame-plan",
        json={
            "session_date": "2026-05-12",
            "event_ids": ["event-123"],
            "source": "pytest",
            "strategy_plans": [current_plan],
        },
    )
    adoption_response = client.post(
        "/v1/events/event-123/llm-revision/adopt",
        json={
            "session_date": "2026-05-12",
            "source": "pytest",
            "reviewed_by": "pytest-reviewer",
            "review_reason": "quarter end review",
            "apply_current": True,
            "trace_artifact_path": str(trace_path),
        },
    )
    read_response = client.get("/v1/events/event-123/strategy-plan/current?session_date=2026-05-12")

    assert pregame_response.status_code == 202
    assert adoption_response.status_code == 202
    assert read_response.status_code == 200
    current = read_response.json()
    explainability = current["explainability"]
    assert current["active_strategies"][0]["strategy_id"] == "halftime-watch"
    assert explainability["q2_quarter_end_reviewed_utc"]
    assert "llm-request-quarter-end" in explainability["q2_quarter_end_reviewed"]
    assert explainability["llm_revision_adoption"]["trace_metadata"]["trigger_types"] == ["quarter_end"]
    assert len(persist_calls) == 2


def test_llm_revision_adoption_stamps_passive_plan_trigger_marker_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    persist_calls = []
    monkeypatch.setattr(agentic_store, "try_persist_strategy_plan", lambda plan: persist_calls.append(plan) or {"ok": True})
    client = TestClient(create_app())
    current_plan = _strategy_plan_payload(event_id="event-123", market_id="market-123")
    current_plan["active_strategies"][0]["revision_triggers"] = [{"type": "fresh_q3_state_after_halftime"}]
    revised_plan = _strategy_plan_payload(event_id="event-123", market_id="market-123")
    revised_plan["active_strategies"][0]["strategy_id"] = "q3-review"
    trace_path = local_root / "shared" / "artifacts" / "llm-runtime" / "2026-05-12" / "trace-passive.json"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text(
        json.dumps(
            {
                "schema_version": "llm_runtime_trace_artifact_v1",
                "event_id": "event-123",
                "trace_id": "trace-passive",
                "trigger_types": ["strategy_plan_revision_trigger"],
                "trigger_list": [
                    {
                        "trigger_type": "strategy_plan_revision_trigger",
                        "evidence": {
                            "trigger": {
                                "type": "fresh_q3_state_after_halftime",
                                "strategy_id": "halftime-watch",
                            },
                            "period": 3,
                            "clock": "PT09M05.00S",
                        },
                    }
                ],
                "selected_model": "gpt-5.4-mini",
                "persisted_at_utc": "2026-05-12T01:00:00Z",
                "response": {
                    "request_id": "llm-request-passive",
                    "status": "response_recorded",
                    "selected_model": "gpt-5.4-mini",
                    "revised_strategy_plan": revised_plan,
                    "reconciliation_actions": [{"action": "revise_plan"}],
                    "blocked_actions": [],
                    "confidence": 0.88,
                    "skipped_reason": None,
                },
            }
        ),
        encoding="utf-8",
    )

    pregame_response = client.post(
        "/v1/ops/pregame-plan",
        json={
            "session_date": "2026-05-12",
            "event_ids": ["event-123"],
            "source": "pytest",
            "strategy_plans": [current_plan],
        },
    )
    adoption_response = client.post(
        "/v1/events/event-123/llm-revision/adopt",
        json={
            "session_date": "2026-05-12",
            "source": "pytest",
            "reviewed_by": "pytest-reviewer",
            "review_reason": "fresh q3 state review",
            "apply_current": True,
            "trace_artifact_path": str(trace_path),
        },
    )
    read_response = client.get("/v1/events/event-123/strategy-plan/current?session_date=2026-05-12")

    assert pregame_response.status_code == 202
    assert adoption_response.status_code == 202
    assert read_response.status_code == 200
    explainability = read_response.json()["explainability"]
    assert explainability["fresh_q3_state_after_halftime_reviewed_utc"]
    assert "llm-request-passive" in explainability["fresh_q3_state_after_halftime_reviewed"]
    assert len(persist_calls) == 2


def test_llm_revision_adoption_records_conservative_actions_without_plan_replace_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    monkeypatch.setattr(
        ops_router,
        "create_live_order",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("conservative adoption must not place orders")),
    )
    client = TestClient(create_app())

    response = client.post(
        "/v1/events/event-123/llm-revision/adopt",
        json={
            "session_date": "2026-05-12",
            "source": "pytest",
            "reviewed_by": "pytest-reviewer",
            "review_reason": "record pause and target only",
            "apply_current": True,
            "response": {
                "request_id": "llm-request-actions",
                "status": "response_recorded",
                "selected_model": "gpt-5.4-mini",
                "revised_strategy_plan": None,
                "reconciliation_actions": [
                    {"action": "pause", "reason": "feed stale"},
                    {"action": "position_management_only", "reason": "manual position detected"},
                ],
                "blocked_actions": [],
                "confidence": 0.7,
                "skipped_reason": None,
            },
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "conservative_actions_recorded"
    assert payload["apply_current"] is False
    assert payload["order_endpoint_call_allowed"] is False
    assert payload["post_adoption_proof"]["raw_order_placed"] is False
    assert Path(payload["adoption_artifact"]["path"]).exists()


def test_llm_revision_adoption_skipped_response_fails_closed_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    client = TestClient(create_app())

    response = client.post(
        "/v1/events/event-123/llm-revision/adopt",
        json={
            "session_date": "2026-05-12",
            "source": "pytest",
            "reviewed_by": "pytest-reviewer",
            "review_reason": "should fail",
            "response": {
                "request_id": "llm-request-1",
                "status": "skipped_unavailable",
                "selected_model": "gpt-5.5",
                "revised_strategy_plan": None,
                "reconciliation_actions": [],
                "blocked_actions": [],
                "skipped_reason": "dispatch_disabled",
            },
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "llm_revision_not_adoptable"


def test_postgame_review_autoloads_plan_events_and_pnl_attribution_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    fake_connection = object()
    fetch_calls = []

    def fake_db_connection():
        yield fake_connection

    def fake_direct_context(connection, **kwargs):
        assert connection is fake_connection
        assert kwargs["account_id"] == "56964015-5935-5035-bdab-b056c9277146"
        assert kwargs["include_direct_clob_evidence"] is True
        return {
            "direct_open_order_external_ids": ["global-order-1"],
            "direct_open_order_count": 1,
            "direct_open_position_count": 1,
            "direct_trade_rows": [
                {"id": "direct-trade-1", "token_id": "token-1"},
                {"id": "global-trade-1", "token_id": "other-token"},
            ],
            "direct_evidence": {
                "enabled": True,
                "ok": True,
                "error": None,
                "open_order_count": 1,
                "open_position_count": 1,
                "trade_count": 2,
                "open_orders": [{"id": "global-order-1", "token_id": "other-token"}],
                "open_positions": [{"asset_id": "other-token", "size": "5"}],
                "trades": [
                    {"id": "direct-trade-1", "token_id": "token-1"},
                    {"id": "global-trade-1", "token_id": "other-token"},
                ],
            },
        }

    def fake_fetch_rows(connection, **kwargs):
        assert connection is fake_connection
        fetch_calls.append(kwargs)
        return [{"order_id": "order-1"}]

    def fake_lifecycle_report(rows, **kwargs):
        assert rows == [{"order_id": "order-1"}]
        assert kwargs["direct_open_order_external_ids"] == []
        assert kwargs["direct_open_order_count"] == 0
        assert kwargs["direct_open_position_count"] == 0
        assert kwargs["direct_trade_rows"] == [{"id": "direct-trade-1", "token_id": "token-1"}]
        return {"order_count": 1, "pnl_attribution_ready": True, "items": []}

    monkeypatch.setattr(agentic_store, "try_persist_strategy_plan", lambda plan: {"ok": True})
    monkeypatch.setattr(ops_router, "_resolve_order_lifecycle_direct_context", fake_direct_context)
    monkeypatch.setattr(ops_router, "_fetch_order_lifecycle_reconciliation_rows", fake_fetch_rows)
    monkeypatch.setattr(ops_router, "build_order_lifecycle_reconciliation_report", fake_lifecycle_report)
    monkeypatch.setattr(
        ops_router,
        "build_portfolio_pnl_attribution_report",
        lambda report: {
            "pnl_attribution_ready": True,
            "known_cashflow_usd": Decimal("0.80"),
            "residual_status": "balanced",
            "buckets": [{"actor_label": "janus_strategy", "known_cashflow_usd": Decimal("0.80")}],
        },
    )
    monkeypatch.setattr(
        ops_router,
        "_fetch_postgame_live_evidence_counts",
        lambda connection, *, event_id: {
            "watch_session_count": 1,
            "orderbook_tick_count": 12,
            "market_trade_count": 1,
            "strategy_decision_count": 4,
            "order_intent_count": 1,
            "executed_order_count": 0,
            "replay_session_count": 1,
            "first_strategy_decision_at": "2026-05-10T23:00:00+00:00",
            "last_strategy_decision_at": "2026-05-10T23:30:00+00:00",
            "first_orderbook_tick_at": "2026-05-10T23:00:00+00:00",
            "last_orderbook_tick_at": "2026-05-10T23:30:00+00:00",
        },
    )
    monkeypatch.setattr(
        ops_router,
        "_read_live_worker_tick_summary",
        lambda *, day, event_id: {
            "status": "recorded",
            "tick_count": 2,
            "heartbeat_present": True,
            "heartbeat_event_match": True,
            "heartbeat_event_ids": [event_id],
        },
    )
    monkeypatch.setattr(
        ops_router,
        "_read_postgame_replay_tick_stream_summary",
        lambda *, day, event_id: {
            "schema_version": "postgame_replay_tick_stream_summary_v1",
            "status": "recorded",
            "event_id": event_id,
            "path": "local/shared/artifacts/live-strategy-worker/2026-05-10/ticks.jsonl",
            "source_confidence": "runtime_artifact",
            "tick_count": 2,
            "intent_count": 1,
            "executed_order_count": 0,
            "order_intent_candidate_count": 1,
            "decision_type_counts": {"candidate": 2},
            "blocker_reason_counts": {"scoreboard_freshness_required": 1},
            "sleeves": {
                "grid-1": {
                    "sleeve_id": "grid-1",
                    "strategy_id": "grid-1",
                    "sleeve_role": "grid_scalp",
                    "sleeve_side": "Knicks",
                    "strategy_family": "price_stability_micro_grid",
                    "tick_count": 2,
                    "intent_count": 1,
                    "blocker_count": 1,
                    "blocker_reasons": ["scoreboard_freshness_required"],
                }
            },
        },
    )

    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection
    try:
        pregame_response = client.post(
            "/v1/ops/pregame-plan",
            json={
                "session_date": "2026-05-10",
                "source": "pytest",
                "strategy_plans": [_strategy_plan_payload(event_id="nba-sas-min-2026-05-10")],
            },
        )
        response = client.post(
            "/v1/ops/postgame-review",
            json={
                "session_date": "2026-05-10",
                "account_id": "56964015-5935-5035-bdab-b056c9277146",
                "source": "pytest",
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert pregame_response.status_code == 202
    assert response.status_code == 202
    payload = response.json()
    assert payload["reviewed_event_ids"] == ["nba-sas-min-2026-05-10"]
    assert payload["strategy_plan_gate"]["status"] == "ready"
    assert payload["postgame_live_evidence"]["status"] == "live_evidence_present"
    assert payload["postgame_live_evidence"]["gate"] == "GREEN"
    assert payload["postgame_live_evidence"]["items"][0]["counts"]["orderbook_tick_count"] == 12
    assert fetch_calls[0]["account_id"] == "56964015-5935-5035-bdab-b056c9277146"
    assert fetch_calls[0]["event_slug"] == "nba-sas-min-2026-05-10"
    attribution = payload["portfolio_pnl_attribution"]
    assert attribution["status"] == "ready"
    assert attribution["direct_evidence"]["trade_count"] == 2
    assert attribution["items"][0]["direct_event_scope"]["status"] == "scoped"
    assert attribution["items"][0]["direct_event_scope"]["open_order_count"] == 0
    assert attribution["items"][0]["direct_event_scope"]["open_position_count"] == 0
    assert attribution["items"][0]["direct_event_scope"]["trade_count"] == 1
    assert attribution["items"][0]["pnl_attribution"]["known_cashflow_usd"] == 0.8
    evaluation = payload["postgame_evaluation"]
    assert evaluation["schema_version"] == "postgame_evaluation_v1"
    assert evaluation["market_tape_policy"]["account_pnl_eligible"] is False
    assert "account_pnl" in evaluation["market_tape_policy"]["blocked_uses"]
    realized = evaluation["realized_live"]
    assert realized["public_market_tape_excluded_from_account_pnl"] is True
    assert realized["items"][0]["account_pnl"]["known_cashflow_usd"] == 0.8
    assert realized["items"][0]["market_tape"]["account_pnl_eligible"] is False
    assert realized["items"][0]["market_tape"]["event_scoped_trade_count"] == 1
    assert evaluation["replay_input"]["same_tick_stream_for_all_modes"] is True
    assert evaluation["replay_input"]["events"]["nba-sas-min-2026-05-10"]["tick_count"] == 2
    assert evaluation["replay_modes"]["sleeve_isolated"]["status"] == "input_ready"
    assert evaluation["replay_modes"]["sleeve_isolated"]["sleeve_count"] == 1
    assert evaluation["replay_modes"]["aggregate_replay"]["status"] == "input_ready"
    assert evaluation["replay_modes"]["aggregate_replay"]["aggregate"]["order_intent_candidate_count"] == 1
    assert evaluation["replay_modes"]["leave_one_out"]["status"] == "input_ready"
    assert evaluation["replay_modes"]["leave_one_out"]["excluded_sleeve_count"] == 1
    assert "portfolio_pnl_attribution" in Path(payload["path"]).read_text(encoding="utf-8")
    assert "postgame_evaluation" in Path(payload["path"]).read_text(encoding="utf-8")


def test_postgame_evaluation_keeps_market_tape_out_of_account_pnl_pytest() -> None:
    evaluation = ops_router._build_postgame_evaluation(
        day=None,
        reviewed_event_ids=["wnba-conn-gsv-2026-05-25"],
        strategy_plan_gate={"status": "ready", "ready": True},
        postgame_live_evidence={"status": "live_evidence_present"},
        portfolio_pnl_attribution={
            "status": "ready",
            "items": [
                {
                    "ok": True,
                    "event_id": "wnba-conn-gsv-2026-05-25",
                    "event_slug": "wnba-conn-gsv-2026-05-25",
                    "direct_event_scope": {
                        "schema_version": "postgame_direct_event_scope_v1",
                        "status": "scoped",
                        "scoped": True,
                        "trade_count": 999,
                    },
                    "reconciliation": {
                        "order_count": 2,
                        "linked_trade_count": 2,
                        "unknown_lifecycle_count": 0,
                    },
                    "pnl_attribution": {
                        "known_cashflow_usd": Decimal("2.15"),
                        "known_fee_usd": Decimal("0"),
                        "residual_status": "not_supplied",
                        "direct_final_flat": True,
                        "pnl_attribution_ready": True,
                        "buckets": [
                            {
                                "actor_label": "janus_strategy",
                                "known_cashflow_usd": Decimal("2.15"),
                            }
                        ],
                    },
                }
            ],
        },
    )

    realized_item = evaluation["realized_live"]["items"][0]
    assert realized_item["account_pnl"]["known_cashflow_usd"] == 2.15
    assert realized_item["market_tape"]["event_scoped_trade_count"] == 999
    assert realized_item["market_tape"]["account_pnl_eligible"] is False
    assert evaluation["market_tape_policy"]["blocked_uses"] == [
        "account_pnl",
        "realized_return",
        "all_account_performance",
    ]


def test_postgame_replay_tick_stream_summary_reads_same_event_stream_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    tick_root = local_root / "shared" / "artifacts" / "live-strategy-worker" / "2026-05-25"
    tick_root.mkdir(parents=True)
    tick = {
        "started_at_utc": "2026-05-25T22:00:00Z",
        "finished_at_utc": "2026-05-25T22:00:01Z",
        "stdout": {
            "events": [
                {"event_id": "other-event"},
                {
                    "event_id": "wnba-conn-gsv-2026-05-25",
                    "live_execution": {
                        "intent_count": 2,
                        "executed_orders": [{"order_id": "order-1"}],
                        "blockers": [{"reason": "scoreboard_freshness_required"}],
                        "sleeve_states": [
                            {
                                "sleeve_id": "gsv-grid",
                                "strategy_id": "gsv-grid",
                                "sleeve_role": "grid_scalp",
                                "sleeve_side": "Golden State Valkyries",
                                "strategy_family": "price_stability_micro_grid",
                                "intent_count": 2,
                                "blocker_count": 1,
                                "blocker_reasons": ["scoreboard_freshness_required"],
                            }
                        ],
                    },
                    "live_signal_aggregation": {
                        "decision": {
                            "decision_type": "candidate",
                            "order_intent_candidates": [{"intent_id": "intent-1"}],
                        }
                    },
                },
            ]
        },
    }
    (tick_root / "ticks.jsonl").write_text(json.dumps(tick) + "\n", encoding="utf-8")

    summary = ops_router._read_postgame_replay_tick_stream_summary(
        day="2026-05-25",
        event_id="wnba-conn-gsv-2026-05-25",
    )

    assert summary["status"] == "recorded"
    assert summary["tick_count"] == 1
    assert summary["intent_count"] == 2
    assert summary["executed_order_count"] == 1
    assert summary["order_intent_candidate_count"] == 1
    assert summary["decision_type_counts"] == {"candidate": 1}
    assert summary["blocker_reason_counts"] == {"scoreboard_freshness_required": 1}
    assert summary["sleeves"]["gsv-grid"]["intent_count"] == 2
    assert summary["sleeves"]["gsv-grid"]["blocker_reasons"] == ["scoreboard_freshness_required"]


def test_postgame_review_flags_not_actually_live_tested_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    fake_connection = object()

    def fake_db_connection():
        yield fake_connection

    monkeypatch.setattr(
        ops_router,
        "_fetch_postgame_live_evidence_counts",
        lambda connection, *, event_id: {
            "watch_session_count": 1,
            "orderbook_tick_count": 4,
            "market_trade_count": 0,
            "strategy_decision_count": 2,
            "order_intent_count": 0,
            "executed_order_count": 0,
            "replay_session_count": 0,
            "first_strategy_decision_at": "2026-05-13T00:55:44+00:00",
            "last_strategy_decision_at": "2026-05-13T02:58:09+00:00",
            "first_orderbook_tick_at": "2026-05-13T00:55:43+00:00",
            "last_orderbook_tick_at": "2026-05-13T02:58:09+00:00",
        },
    )
    monkeypatch.setattr(
        ops_router,
        "_read_live_worker_tick_summary",
        lambda *, day, event_id: {
            "status": "missing",
            "tick_count": 0,
            "heartbeat_present": False,
            "heartbeat_event_match": False,
            "heartbeat_event_ids": [],
        },
    )
    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection

    try:
        response = client.post(
            "/v1/ops/postgame-review",
            json={
                "session_date": "2026-05-12",
                "event_ids": ["nba-min-sas-2026-05-12"],
                "source": "pytest",
            },
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 202
    evidence = response.json()["postgame_live_evidence"]
    assert evidence["status"] == "not_actually_live_tested"
    assert evidence["gate"] == "RED"
    assert evidence["blocker_reasons"] == ["insufficient_orderbook_ticks", "insufficient_strategy_decisions"]
    item = evidence["items"][0]
    assert item["status"] == "not_actually_live_tested"
    assert item["warnings"] == [
        {"reason": "market_trade_stream_missing", "market_trade_count": 0},
        {"reason": "replay_session_missing", "replay_session_count": 0},
        {"reason": "live_worker_tick_evidence_missing", "worker_tick_count": 0},
    ]


def test_read_live_worker_tick_summary_uses_live_strategy_worker_artifact_root_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    event_id = "nba-min-sas-2026-05-13"
    root = local_root / "shared" / "artifacts" / "live-strategy-worker" / "2026-05-13"
    root.mkdir(parents=True)
    (root / "ticks.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_ids": [event_id],
                        "started_at_utc": "2026-05-13T00:55:43Z",
                        "finished_at_utc": "2026-05-13T00:55:44Z",
                    }
                ),
                json.dumps(
                    {
                        "event_ids": ["nba-other-2026-05-13"],
                        "started_at_utc": "2026-05-13T01:55:43Z",
                        "finished_at_utc": "2026-05-13T01:55:44Z",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    (root / "heartbeat.json").write_text(json.dumps({"event_ids": [event_id]}), encoding="utf-8")

    summary = ops_router._read_live_worker_tick_summary(day="2026-05-13", event_id=event_id)

    assert summary["status"] == "recorded"
    assert summary["tick_count"] == 1
    assert summary["latest_tick_at_utc"] == "2026-05-13T00:55:44Z"
    assert summary["heartbeat_present"] is True
    assert summary["heartbeat_event_match"] is True
    assert summary["heartbeat_event_ids"] == [event_id]


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


def test_replay_from_watch_session_returns_source_summary_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    replay_calls = []

    def fake_persist_replay(payload, *, output_root=None):
        replay_calls.append({"payload": payload, "output_root": output_root})
        return {
            "ok": True,
            "replay_session_id": "replay-1",
            "watch_session_id": "watch-1",
            "watch_session_key": payload.watch_session_id,
            "event_key": payload.event_key,
            "source_tick_count": 4,
            "source_trade_count": 1,
            "latency_summary": {"tick_cadence": {"max_gap_seconds": 3.0}},
        }

    monkeypatch.setattr(ops_router, "try_persist_replay_request", fake_persist_replay)
    client = TestClient(create_app())

    response = client.post(
        "/v1/replay/from-watch-session",
        json={"watch_session_id": "watch-nba-event", "event_key": "nba-event", "notes": "pytest"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["db_persistence"]["source_tick_count"] == 4
    assert payload["db_persistence"]["source_trade_count"] == 1
    assert payload["db_persistence"]["latency_summary"]["tick_cadence"]["max_gap_seconds"] == 3.0
    assert replay_calls[0]["payload"].watch_session_id == "watch-nba-event"
    assert replay_calls[0]["output_root"] == payload["path"]


def test_operator_intervention_reconcile_accepts_adoption_metadata_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    persist_calls = []

    def fake_persist(payload):
        persist_calls.append(payload)
        return {
            "ok": True,
            "metadata_status": "metadata_complete",
            "metadata_required": True,
            "metadata_complete": True,
            "missing_metadata_fields": [],
            "adoption_class": "manual_only",
        }

    monkeypatch.setattr(ops_router, "try_persist_operator_intervention", fake_persist)
    monkeypatch.setattr(ops_router, "get_agentic_database_status", lambda: {"ok": True, "schema": "agentic"})
    client = TestClient(create_app())

    response = client.post(
        "/v1/operator/interventions/reconcile",
        json={
            "account_id": "account-1",
            "event_id": "event-lal-okc",
            "market_id": "market-1",
            "action": "adopt",
            "external_order_ids": ["order-1"],
            "external_trade_ids": ["trade-1"],
            "manual_reason": "manual_only_ultra_low_ladder",
            "target_status": "filled_target_sell",
            "stop_status": "not_applicable_manual_watch",
            "hedge_status": "not_applicable",
            "protective_order_status": "manual_exit_completed",
            "expected_close_path": "target_sell_or_manual_flatten",
            "final_pnl_usd": -0.3,
            "metadata": {"source": "postgame_2026-05-09"},
            "notes": "pytest adoption metadata",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["db_persistence"]["metadata_status"] == "metadata_complete"
    assert body["database"]["ok"] is True
    assert persist_calls[0].external_trade_ids == ["trade-1"]
    assert persist_calls[0].expected_close_path == "target_sell_or_manual_flatten"
    assert "manual_only_ultra_low_ladder" in Path(body["path"]).read_text(encoding="utf-8")


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


def test_strategy_plan_evaluate_endpoint_blocks_without_current_plan_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    client = TestClient(create_app())

    response = client.post(
        "/v1/events/event-missing/strategy-plan/evaluate",
        json={"dry_run": True, "session_date": "2026-05-10", "market_state": {"price": 0.2}},
    )

    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "strategy_plan_required"


def test_strategy_plan_evaluate_endpoint_loads_current_plan_for_session_date_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    monkeypatch.setattr(agentic_store, "try_persist_strategy_plan", lambda plan: {"ok": True})
    monkeypatch.setattr(
        ops_router,
        "try_persist_strategy_decisions",
        lambda result, **kwargs: {"ok": True, "row_count": 1},
    )
    client = TestClient(create_app())

    pregame_response = client.post(
        "/v1/ops/pregame-plan",
        json={
            "session_date": "2026-05-10",
            "event_ids": ["event-123"],
            "source": "pytest",
            "strategy_plans": [_strategy_plan_payload()],
        },
    )
    evaluate_response = client.post(
        "/v1/events/event-123/strategy-plan/evaluate",
        json={"dry_run": True, "session_date": "2026-05-10", "market_state": {"price": 0.3}},
    )

    assert pregame_response.status_code == 202
    assert evaluate_response.status_code == 200
    payload = evaluate_response.json()
    assert payload["intent_count"] == 0
    assert payload["blockers"][0]["reason"] == "price_band_not_met"


def test_strategy_plan_evaluate_requires_declared_live_gate_state_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    monkeypatch.setattr(
        ops_router,
        "try_persist_strategy_decisions",
        lambda result, **kwargs: {"ok": True, "row_count": 1},
    )
    client = TestClient(create_app())

    response = client.post(
        "/v1/events/event-123/strategy-plan/evaluate",
        json={
            "dry_run": True,
            "market_state": {},
            "portfolio_state": {},
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
                            "max_orderbook_age_seconds": 90,
                            "max_scoreboard_age_seconds": 90,
                            "max_spread_cents": 2,
                            "max_abs_score_gap": 10,
                            "max_open_positions": 2,
                            "allow_ultra_low_underdog": True,
                        },
                        "exit_rules": {"target_cents": 4},
                        "stop_rules": {"max_loss_cents": 2},
                    }
                ],
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent_count"] == 0
    assert payload["blockers"][0]["reason"] == "orderbook_freshness_required"


def test_strategy_plan_evaluate_uses_outcome_specific_market_state_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    monkeypatch.setattr(
        ops_router,
        "try_persist_strategy_decisions",
        lambda result, **kwargs: {"ok": True, "row_count": 1},
    )
    client = TestClient(create_app())

    response = client.post(
        "/v1/events/event-123/strategy-plan/evaluate",
        json={
            "dry_run": True,
            "market_state": {
                "outcome_states": {
                    "outcome-1": {
                        "price": 0.2,
                        "orderbook_age_seconds": 1,
                        "scoreboard_age_seconds": 1,
                        "spread": 0.01,
                        "score_gap": 4,
                    }
                }
            },
            "portfolio_state": {"open_positions": 0},
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
                            "max_orderbook_age_seconds": 90,
                            "max_scoreboard_age_seconds": 90,
                            "max_spread_cents": 2,
                            "max_abs_score_gap": 10,
                            "max_open_positions": 2,
                            "allow_ultra_low_underdog": True,
                        },
                        "exit_rules": {"target_cents": 4},
                        "stop_rules": {"max_loss_cents": 2},
                    }
                ],
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent_count"] == 1
    assert payload["intents"][0]["outcome_id"] == "outcome-1"


def test_strategy_plan_evaluate_operator_sizing_overrides_llm_size_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    monkeypatch.setattr(
        ops_router,
        "try_persist_strategy_decisions",
        lambda result, **kwargs: {"ok": True, "row_count": 1},
    )
    client = TestClient(create_app())

    response = client.post(
        "/v1/events/event-123/strategy-plan/evaluate",
        json={
            "dry_run": True,
            "market_state": {
                "outcome_states": {
                    "outcome-1": {
                        "price": 0.18,
                        "orderbook_age_seconds": 1,
                        "scoreboard_age_seconds": 1,
                        "spread": 0.01,
                        "score_gap": 4,
                    }
                }
            },
            "portfolio_state": {
                "open_positions": 0,
                "operator_sizing_policy": {
                    "mode": "operator_minimum_order",
                    "min_size": 5,
                    "min_buy_notional_usd": 1.0,
                    "share_precision": 3,
                },
            },
            "plan": {
                "event_id": "event-123",
                "market_id": "market-123",
                "active_strategies": [
                    {
                        "strategy_id": "grid-1",
                        "family": "resistance_band_rebound_grid",
                        "side": "underdog",
                        "budget_usd": 0.25,
                        "entry_rules": {
                            "outcome_id": "outcome-1",
                            "token_id": "token-1",
                            "side": "buy",
                            "price": 0.18,
                            "price_band": [0.15, 0.25],
                            "max_orderbook_age_seconds": 90,
                            "max_scoreboard_age_seconds": 90,
                            "max_spread_cents": 2,
                            "max_abs_score_gap": 10,
                            "max_open_positions": 2,
                            "allow_ultra_low_underdog": True,
                        },
                        "exit_rules": {"target_cents": 4},
                        "stop_rules": {"max_loss_cents": 2},
                    }
                ],
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent_count"] == 1
    intent = payload["intents"][0]
    assert intent["size"] == 5.612
    assert intent["metadata"]["sizing_policy"]["source"] == "operator_policy"
    assert intent["metadata"]["sizing_policy"]["effective_min_buy_notional_usd"] == 1.01
    assert intent["metadata"]["sizing_policy"]["llm_requested_size"] is None
    assert intent["metadata"]["sizing_policy"]["llm_strategy_budget_usd"] == 0.25


def test_strategy_plan_evaluate_counts_open_orders_as_unresolved_exposure_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    monkeypatch.setattr(
        ops_router,
        "try_persist_strategy_decisions",
        lambda result, **kwargs: {"ok": True, "row_count": 1},
    )
    client = TestClient(create_app())

    plan = _strategy_plan_payload()
    plan["active_strategies"][0]["max_positions"] = 1
    plan["active_strategies"][0]["entry_rules"].update(
        {
            "max_open_positions": 2,
            "max_orderbook_age_seconds": 90,
            "max_scoreboard_age_seconds": 90,
            "max_spread_cents": 2,
            "max_abs_score_gap": 10,
        }
    )

    response = client.post(
        "/v1/events/event-123/strategy-plan/evaluate",
        json={
            "dry_run": True,
            "market_state": {
                "outcome_states": {
                    "outcome-1": {
                        "price": 0.2,
                        "orderbook_age_seconds": 1,
                        "scoreboard_age_seconds": 1,
                        "spread": 0.01,
                        "score_gap": 2,
                    }
                }
            },
            "portfolio_state": {"open_positions": 0, "open_orders": 1},
            "plan": plan,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent_count"] == 0
    assert payload["blockers"][0]["reason"] == "position_limit_reached"
    assert payload["blockers"][0]["open_orders"] == 1


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
