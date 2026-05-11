from __future__ import annotations

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
    plan_payload["active_strategies"].append(
        {
            **plan_payload["active_strategies"][0],
            "strategy_id": "grid-2",
            "entry_rules": {
                **plan_payload["active_strategies"][0]["entry_rules"],
                "outcome_id": "outcome-2",
                "token_id": "token-2",
            },
        }
    )

    try:
        submit_response = client.post("/v1/events/event-123/strategy-plan", json=plan_payload)
        response = client.post(
            "/v1/ops/live-monitor",
            json={"session_date": "2026-05-11", "event_ids": ["event-123"], "account_id": "account-123", "source": "pytest"},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert submit_response.status_code == 201
    assert response.status_code == 202
    payload = response.json()
    assert payload["integrity"]["direct_trade_token_ids"] == ["token-1", "token-2"]


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
            "direct_open_order_external_ids": [],
            "direct_open_order_count": 0,
            "direct_open_position_count": 0,
            "direct_trade_rows": [{"id": "direct-trade-1"}],
            "direct_evidence": {
                "enabled": True,
                "ok": True,
                "error": None,
                "open_order_count": 0,
                "open_position_count": 0,
                "trade_count": 1,
                "trades": [{"id": "direct-trade-1"}],
            },
        }

    def fake_fetch_rows(connection, **kwargs):
        assert connection is fake_connection
        fetch_calls.append(kwargs)
        return [{"order_id": "order-1"}]

    def fake_lifecycle_report(rows, **kwargs):
        assert rows == [{"order_id": "order-1"}]
        assert kwargs["direct_trade_rows"] == [{"id": "direct-trade-1"}]
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
    assert fetch_calls[0]["account_id"] == "56964015-5935-5035-bdab-b056c9277146"
    assert fetch_calls[0]["event_slug"] == "nba-sas-min-2026-05-10"
    attribution = payload["portfolio_pnl_attribution"]
    assert attribution["status"] == "ready"
    assert attribution["direct_evidence"]["trade_count"] == 1
    assert attribution["items"][0]["pnl_attribution"]["known_cashflow_usd"] == 0.8
    assert "portfolio_pnl_attribution" in Path(payload["path"]).read_text(encoding="utf-8")


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
    assert intent["size"] == 5.556
    assert intent["metadata"]["sizing_policy"]["source"] == "operator_policy"
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
