from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.dependencies import get_db_connection
from app.api.main import create_app
from app.api.routers import ops as ops_router
from app.modules.agentic.contracts import OpsCycleRequest
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


def test_live_state_promotes_direct_worker_game_resolution_pytest() -> None:
    event_payload = {
        "event_id": "wnba-sea-dal-2026-06-01",
        "game": {
            "event_id": "wnba-sea-dal-2026-06-01",
            "source": "wnba_cdn_scoreboard",
            "resolution_source": "wnba_cdn_scoreboard_event_slug",
            "resolved": True,
            "game_id": "1022600064",
            "league": "wnba",
            "game_date": "2026-06-01",
            "game_start_time": "2026-06-02T00:00:00+00:00",
            "game_status": 1,
            "game_status_text": "8:00 pm ET",
            "period": 0,
            "game_clock": "",
            "home_team_tricode": "DAL",
            "away_team_tricode": "SEA",
            "home_score": 0,
            "away_score": 0,
        },
    }

    scoreboard = ops_router._live_tick_scoreboard_resolution(event_payload)  # noqa: SLF001
    game_state = ops_router._live_tick_game_state(event_payload)  # noqa: SLF001

    assert scoreboard["status"] == "resolved"
    assert scoreboard["selected_game_id"] == "1022600064"
    assert scoreboard["resolution_source"] == "wnba_cdn_scoreboard_event_slug"
    assert scoreboard["execution_blocking_if_required"] is False
    assert game_state["status"] == "recorded"
    assert game_state["game_status"] == 1
    assert game_state["game_status_text"] == "8:00 pm ET"
    assert game_state["game_start_time"] == "2026-06-02T00:00:00+00:00"
    assert game_state["home_team"] == "DAL"
    assert game_state["away_team"] == "SEA"


def test_wnba_db_context_uses_roster_and_history_before_tip_pytest(monkeypatch) -> None:
    class _Connection:
        def __enter__(self) -> "_Connection":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    def _fetch_one(_connection: object, query: str, _params: tuple[object, ...]) -> dict | None:
        compact = " ".join(query.split())
        if "FROM wnba.wnba_games" in compact:
            return {
                "game_id": "1022600064",
                "game_date": "2026-06-01",
                "game_start_time": datetime(2026, 6, 2, tzinfo=timezone.utc),
                "game_status": 1,
                "game_status_text": "8:00 pm ET",
                "home_team_tricode": "DAL",
                "away_team_tricode": "SEA",
                "home_score": 0,
                "away_score": 0,
                "updated_at": datetime(2026, 6, 1, 16, 22, tzinfo=timezone.utc),
                "source": "wnba_cdn_scoreboard",
            }
        if "FROM wnba.wnba_team_boxscore_snapshots snapshot" in compact:
            return {
                "row_count": 1202,
                "team_count": 2,
                "game_count": 6,
                "latest_captured_at": datetime(2026, 5, 31, 1, 1, tzinfo=timezone.utc),
            }
        if "FROM wnba.wnba_player_boxscore_snapshots snapshot" in compact:
            return {
                "row_count": 17309,
                "player_count": 29,
                "game_count": 6,
                "latest_captured_at": datetime(2026, 5, 31, 1, 1, tzinfo=timezone.utc),
            }
        if "FROM wnba.wnba_team_boxscore_snapshots" in compact:
            return {"row_count": 0, "team_count": 0, "latest_captured_at": None}
        if "FROM wnba.wnba_player_boxscore_snapshots" in compact:
            return {"row_count": 0, "latest_captured_at": None}
        if "FROM wnba.wnba_players" in compact:
            return {"row_count": 29, "latest_updated_at": datetime(2026, 6, 1, 16, tzinfo=timezone.utc)}
        return None

    def _fetch_all(_connection: object, query: str, _params: tuple[object, ...]) -> list[dict]:
        compact = " ".join(query.split())
        if "FROM wnba.wnba_teams" in compact:
            return [
                {
                    "team_id": 1,
                    "team_tricode": "DAL",
                    "team_city": "Dallas",
                    "team_name": "Wings",
                    "updated_at": datetime(2026, 6, 1, 16, tzinfo=timezone.utc),
                    "source": "wnba_cdn",
                },
                {
                    "team_id": 2,
                    "team_tricode": "SEA",
                    "team_city": "Seattle",
                    "team_name": "Storm",
                    "updated_at": datetime(2026, 6, 1, 16, tzinfo=timezone.utc),
                    "source": "wnba_cdn",
                },
            ]
        if "FROM wnba.wnba_players" in compact:
            return [
                {"player_name": "Arike Ogunbowale"},
                {"player_name": "Paige Bueckers"},
                {"player_name": "Ezi Magbegor"},
                {"player_name": "Skylar Diggins"},
            ]
        if "FROM wnba.wnba_player_boxscore_snapshots" in compact:
            return []
        return []

    import app.data.databases.postgres as postgres_module

    monkeypatch.setattr(postgres_module, "managed_connection", lambda: _Connection())
    monkeypatch.setattr(agentic_store, "_db_fetch_one", _fetch_one)
    monkeypatch.setattr(agentic_store, "_db_fetch_all", _fetch_all)

    source = agentic_store._wnba_database_context_source(  # noqa: SLF001
        event_id="wnba-sea-dal-2026-06-01",
        away_code="SEA",
        home_code="DAL",
        event_day="2026-06-01",
        generated_at=datetime(2026, 6, 1, 16, 30, tzinfo=timezone.utc),
    )

    assert source["status"] == "current"
    assert source["included_in_llm_context"] is True
    assert source["stale_blockers"] == []
    assert source["team_stat_snapshot_count"] == 1202
    assert source["player_stat_snapshot_count"] == 17309
    assert source["teams"] == ["Dallas Wings", "Seattle Storm"]
    assert "Paige Bueckers" in source["players"]
    assert "current_game_boxscore_not_available_before_tip" in source["source_caveats"]
    assert "using_roster_and_historical_boxscore_stats_for_pregame_context" in source["source_caveats"]


def test_live_state_event_controls_state_reads_current_artifact_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    control_path = (
        local_root
        / "shared"
        / "artifacts"
        / "event-controls"
        / "2026-06-01"
        / "wnba-sea-dal-2026-06-01"
        / "current.json"
    )
    control_path.parent.mkdir(parents=True)
    control_path.write_text(
        json.dumps(
            {
                "schema_version": "event_control_config_v1",
                "event_id": "wnba-sea-dal-2026-06-01",
                "session_date": "2026-06-01",
                "enabled": True,
                "signal_source_toggles": {"scoreboard": True, "strategy_plan": True},
                "parameters": {
                    "event_cap_usd": 20.0,
                    "max_signal_age_seconds": 90.0,
                    "cooldown_seconds": 90.0,
                    "min_confidence": 0.5,
                    "max_grid_leg_shares": 5.0,
                    "core_hold_shares": 5.0,
                    "rebuy_review_required": True,
                    "allow_inventory_adding": False,
                },
                "stop_with_positions_policy": {
                    "enabled": True,
                    "max_recovery_delay_seconds": 300.0,
                    "backup_restart_required": True,
                    "set_sell_targets_if_restart_blocked": True,
                    "sell_targets_must_use_janus_gates": True,
                    "raw_order_bypass_allowed": False,
                    "policy_action": "restart_or_set_sell_targets",
                    "reason": "pytest stop recovery",
                },
                "updated_at_utc": "2026-06-01T16:12:54Z",
                "updated_by": "codex",
                "source": "pytest",
                "reason": "fixture",
                "evidence_paths": [],
            }
        ),
        encoding="utf-8",
    )

    state = ops_router._build_event_controls_state(  # noqa: SLF001
        "wnba-sea-dal-2026-06-01",
        day="2026-06-01",
    )

    assert state["schema_version"] == "janus_live_event_state_event_controls_state_v1"
    assert state["status"] == "recorded"
    assert state["current_artifact_present"] is True
    assert state["current_artifact_path"] == str(control_path)
    assert state["parameters"]["event_cap_usd"] == 20.0
    assert state["parameters"]["core_hold_shares"] == 5.0
    assert state["parameters"]["allow_inventory_adding"] is False
    assert state["stop_with_positions_policy"]["enabled"] is True
    assert state["stop_with_positions_policy"]["backup_restart_required"] is True
    assert state["stop_with_positions_policy"]["set_sell_targets_if_restart_blocked"] is True
    assert state["stop_with_positions_policy"]["raw_order_bypass_allowed"] is False
    assert state["aggregation_control"]["event_cap_usd"] == 20.0
    assert state["aggregation_control"]["max_signal_age_seconds"] == 90.0
    assert state["order_endpoint_call_allowed"] is False
    assert state["execution_authority"] is False


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
    plan_payload["context_summary"]["db_context_sources"] = [
        {
            "source_id": "wnba_team_stats_snapshot",
            "source_kind": "team_stats_snapshot",
            "status": "current",
            "source_timestamp_utc": "2026-05-13T10:00:00Z",
            "included_in_llm_context": True,
            "team_stat_snapshot_count": 2,
            "player_stat_snapshot_count": 24,
            "teams": ["Oklahoma City Thunder", "Los Angeles Lakers"],
        }
    ]
    prior_path = (
        local_root
        / "shared"
        / "artifacts"
        / "pregame-priors"
        / "2026-05-13"
        / event_slug
        / "current.json"
    )
    prior_path.parent.mkdir(parents=True)
    prior_path.write_text(
        json.dumps(
            {
                "schema_version": "pregame_research_prior_v1",
                "event_id": event_slug,
                "league": "nba",
                "generated_at_utc": "2026-05-13T12:00:00Z",
                "expires_at_utc": "2026-06-14T00:00:00Z",
                "teams": ["Oklahoma City Thunder", "Los Angeles Lakers"],
                "source_caveats": ["fixture"],
            }
        ),
        encoding="utf-8",
    )

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
    db_context_response = client.get(
        f"/v1/events/{event_uuid}/db-stat-context",
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
    db_trace = context_payload["db_stat_context_trace"]
    assert db_trace["schema_version"] == "db_stat_context_trace_v1"
    assert db_trace["included_source_count"] == 2
    assert db_trace["blockers"] == []
    assert [source["source_id"] for source in context_payload["db_context_sources"]] == [
        "wnba_team_stats_snapshot",
        "optional_pregame_prior",
    ]
    assert context_payload["db_context_sources"][0]["team_stat_snapshot_count"] == 2
    assert context_payload["db_context_sources"][1]["source_path"] == str(prior_path)
    assert context_payload["db_context_sources"][1]["included_in_llm_context"] is True
    assert db_context_response.status_code == 200
    db_context_payload = db_context_response.json()
    assert db_context_payload["schema_version"] == "janus_event_db_stat_context_readback_v1"
    assert db_context_payload["schema_contract"]["schema_version"] == "janus_event_db_stat_context_readback_contract_v1"
    assert db_context_payload["schema_contract"]["execution_authority"] is False
    assert db_context_payload["readback_endpoints"]["self"] == (
        f"/v1/events/{event_uuid}/db-stat-context?session_date=2026-05-13"
    )
    assert db_context_payload["readback_endpoints"]["agent_context"] == (
        f"/v1/events/{event_uuid}/agent-context?session_date=2026-05-13"
    )
    assert db_context_payload["resolved_strategy_plan_event_id"] == event_slug
    assert db_context_payload["strategy_plan_lookup_event_ids"] == [event_uuid, event_slug]
    assert db_context_payload["db_stat_context_state"]["schema_version"] == (
        "janus_live_event_state_db_stat_context_state_v1"
    )
    assert db_context_payload["db_stat_context_state"]["llm_context_inclusion_status"] == "included"
    assert db_context_payload["db_stat_context_state"]["included_source_count"] == 2
    assert db_context_payload["db_stat_context_state"]["source_ids"] == [
        "wnba_team_stats_snapshot",
        "optional_pregame_prior",
    ]
    assert db_context_payload["db_stat_context_trace"]["schema_version"] == "db_stat_context_trace_v1"
    assert [source["source_id"] for source in db_context_payload["db_context_sources"]] == [
        "wnba_team_stats_snapshot",
        "optional_pregame_prior",
    ]
    assert db_context_payload["order_endpoint_call_allowed"] is False
    assert db_context_payload["execution_authority"] is False
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


def test_live_monitor_endpoint_persists_janus_live_event_state_pytest(tmp_path, monkeypatch) -> None:
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
                "open_order_count": 0,
                "open_orders": {"ok": True, "orders": []},
                "open_positions": {"ok": True, "positions": []},
                "current_token_trades": {"ok": True, "trades": []},
            },
        },
    )
    monkeypatch.setattr(
        ops_router,
        "build_live_strategy_worker_readiness",
        lambda *, session_date, event_ids, strategy_plan_gate: {
            "status": "blocked",
            "blocker_reason": "live_strategy_worker_not_running",
            "worker_required": True,
            "ready_for_live_execution": False,
            "expected_event_ids": list(event_ids),
            "config_trust": {
                "schema_version": "live_strategy_worker_config_trust_v1",
                "config_trust_status": "fail_closed_stale_or_implicit_config",
                "restart_allowed": False,
                "start_allowed": False,
                "stale_config_reason": "explicit_start_payload_required",
                "effective_event_ids": ["old-event-2026-05-10"],
                "explicit_event_scope_payload": False,
                "explicit_account_payload": False,
                "trusted_env_autostart": False,
                "execute": True,
                "live_money": True,
            },
            "worker_status": {
                "status": "stopped",
                "worker_thread_alive": False,
                "config_trust": {
                    "schema_version": "live_strategy_worker_config_trust_v1",
                    "config_trust_status": "fail_closed_stale_or_implicit_config",
                    "restart_allowed": False,
                    "start_allowed": False,
                    "stale_config_reason": "explicit_start_payload_required",
                    "effective_event_ids": ["old-event-2026-05-10"],
                    "explicit_event_scope_payload": False,
                    "explicit_account_payload": False,
                    "trusted_env_autostart": False,
                    "execute": True,
                    "live_money": True,
                },
            },
        },
    )
    monkeypatch.setattr(
        ops_router,
        "load_latest_llm_runtime_status",
        lambda *, session_date, event_ids: {
            "status": "recorded",
            "session_date": session_date,
            "event_count": len(event_ids),
            "recorded_event_count": len(event_ids),
            "items": [
                {
                    "event_id": "event-123",
                    "status": "recorded",
                    "quarter_revision_review_count": 1,
                    "quarter_revision_reviews": [
                        {
                            "schema_version": "strategy_plan_quarter_revision_review_v1",
                            "quarter_label": "q1_end",
                            "period": 1,
                            "review_status": "revision_recorded",
                            "reviewed_sleeve_count": 2,
                            "revision_decisions": {
                                "kept_strategy_ids": ["underdog-grid"],
                                "added_strategy_ids": [],
                                "removed_strategy_ids": [],
                            },
                            "order_endpoint_call_allowed": False,
                            "execution_authority": False,
                        }
                    ],
                    "quarter_revision_review_artifacts": [
                        {
                            "path": str(
                                local_root
                                / "shared"
                                / "artifacts"
                                / "llm-runtime"
                                / "2026-05-11"
                                / "event-123"
                                / "quarter-review_q1.json"
                            )
                        }
                    ],
                    "llm_runtime_state": {
                        "schema_version": "llm_runtime_state_flags_v1",
                        "status": "recorded",
                    },
                }
            ],
            "safety_controls": {},
        },
    )
    ticks_path = local_root / "shared" / "artifacts" / "live-strategy-worker" / "2026-05-11" / "ticks.jsonl"
    ticks_path.parent.mkdir(parents=True, exist_ok=True)
    ticks_path.write_text(
        json.dumps(
            {
                "started_at_utc": "2026-05-11T20:01:00Z",
                "finished_at_utc": "2026-05-11T20:01:03Z",
                "stdout": {
                    "events": [
                        {
                            "event_id": "event-123",
                            "market_state": {
                                "game": {
                                    "status": "in_progress",
                                    "period": 2,
                                    "game_clock": "04:12",
                                    "home_team": "Aces",
                                    "away_team": "Valkyries",
                                    "home_score": 45,
                                    "away_score": 40,
                                    "score_gap": 5,
                                    "scoreboard_resolution": {
                                        "schema_version": "scoreboard_resolution_diagnostics_v1",
                                        "status": "resolved",
                                        "selected_game_id": "game-123",
                                    },
                                }
                            },
                            "live_signal_aggregation": {
                                "signal_count": 2,
                                "decision": {
                                    "decision_type": "blocked",
                                    "selected_signal_ids": ["sig-1"],
                                    "suppressed_signal_ids": ["sig-2"],
                                    "order_intent_candidates": [
                                        {
                                            "event_id": "event-123",
                                            "signal_type": "buy",
                                            "side": "Aces",
                                            "requested_shares": 5.0,
                                            "requested_notional_usd": 0.75,
                                            "max_price": 0.15,
                                            "minimum_order_policy": {
                                                "schema_version": "polymarket_order_type_minimum_policy_v1",
                                                "order_type": "limit",
                                                "order_type_minimum_rule": "limit_min_shares",
                                                "minimum_rule_status": "passed",
                                                "share_minimum_applies": True,
                                                "notional_minimum_applies": False,
                                                "required_min_size": 5.0,
                                                "required_market_notional_usd": None,
                                            },
                                        }
                                    ],
                                    "blocker_artifacts": [
                                        {
                                            "reason_code": "price_band_not_met",
                                            "detail": {
                                                "scope": "local_sleeve",
                                                "candidate_blocking": False,
                                                "expected_next_action": "preserve_unrelated_order_candidates",
                                            },
                                        },
                                        {
                                            "reason_code": "event_budget_exceeded",
                                            "detail": {
                                                "scope": "event",
                                                "candidate_blocking": True,
                                                "expected_next_action": "block_order_candidate_until_budget_changes",
                                            },
                                        },
                                    ],
                                },
                                "event_risk_budget": {
                                    "event_cap_usd": 18.0,
                                    "base_event_cap_usd": 10.0,
                                    "event_cap_before_profit_addon_usd": 10.0,
                                    "remaining_notional_usd": 7.5,
                                    "used_notional_usd": 10.5,
                                    "budget_status": "within_budget",
                                    "profit_ratcheted_requested_addon_usd": 8.0,
                                    "profit_ratcheted_addon_usd": 8.0,
                                    "profit_ratcheted_blocked_addon_usd": 0.0,
                                    "risk_promotion_evidence": {
                                        "schema_version": "live_tick_risk_promotion_evidence_v1",
                                        "source_confidence": "account_confirmed",
                                        "risk_promotion_allowed": True,
                                        "requested_profit_addon_usd": 8.0,
                                        "allowed_profit_addon_usd": 8.0,
                                        "blocked_profit_addon_usd": 0.0,
                                        "blocker_codes": [],
                                        "profit_addon_policy": {
                                            "schema_version": "account_confirmed_profit_budget_addon_v1",
                                            "mode": "dollar_for_dollar_realized_profit",
                                            "requested_addon_usd": 8.0,
                                            "allowed_addon_usd": 8.0,
                                            "blocked_addon_usd": 0.0,
                                            "source_confidence_required": ["account_confirmed", "db_confirmed"],
                                            "closed_pnl_required": True,
                                            "unrealized_pnl_allowed": False,
                                            "execution_authority": False,
                                        },
                                    },
                                },
                            },
                        }
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    prior_path = (
        local_root
        / "shared"
        / "artifacts"
        / "pregame-priors"
        / "2026-05-11"
        / "event-123"
        / "current.json"
    )
    prior_path.parent.mkdir(parents=True)
    prior_path.write_text(
        json.dumps(
            {
                "schema_version": "pregame_research_prior_v1",
                "event_id": "event-123",
                "generated_at_utc": "2026-05-11T12:00:00Z",
                "expires_at_utc": "2026-06-14T00:00:00Z",
                "teams": ["Aces", "Valkyries"],
                "source_caveats": ["fixture"],
            }
        ),
        encoding="utf-8",
    )
    control_path = (
        local_root
        / "shared"
        / "artifacts"
        / "event-controls"
        / "2026-05-11"
        / "event-123"
        / "current.json"
    )
    control_path.parent.mkdir(parents=True)
    control_path.write_text(
        json.dumps(
            {
                "schema_version": "event_control_config_v1",
                "event_id": "event-123",
                "session_date": "2026-05-11",
                "enabled": True,
                "signal_source_toggles": {"scoreboard": True, "strategy_plan": True},
                "parameters": {"event_cap_usd": 20.0},
                "stop_with_positions_policy": {
                    "enabled": True,
                    "backup_restart_required": True,
                    "set_sell_targets_if_restart_blocked": True,
                    "raw_order_bypass_allowed": False,
                },
                "updated_at_utc": "2026-05-11T12:00:00Z",
                "updated_by": "codex",
                "source": "pytest",
                "reason": "budget cap readback fixture",
                "evidence_paths": [],
            }
        ),
        encoding="utf-8",
    )
    plan_payload = _strategy_plan_payload(event_id="event-123")
    plan_payload["context_summary"]["db_context_sources"] = [
        {
            "source_id": "wnba_team_player_stats_snapshot",
            "source_kind": "team_player_stats_snapshot",
            "status": "current",
            "source_timestamp_utc": "2026-05-11T10:00:00Z",
            "included_in_llm_context": True,
            "team_stat_snapshot_count": 2,
            "player_stat_snapshot_count": 24,
            "teams": ["Aces", "Valkyries"],
        }
    ]

    client = TestClient(create_app())
    client.app.dependency_overrides[get_db_connection] = fake_db_connection

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
        monitor_response = client.post(
            "/v1/ops/live-monitor",
            json={"session_date": "2026-05-11", "event_ids": ["event-123"], "account_id": "account-123", "source": "pytest"},
        )
        state_response = client.get(
            "/v1/events/event-123/live-state",
            params={"session_date": "2026-05-11"},
        )
        history_response = client.get(
            "/v1/events/event-123/live-state/history",
            params={"session_date": "2026-05-11", "limit": 5},
        )
        signal_blockers_response = client.get(
            "/v1/events/event-123/signal-blockers",
            params={"session_date": "2026-05-11"},
        )
        budget_state_response = client.get(
            "/v1/events/event-123/budget-state",
            params={"session_date": "2026-05-11"},
        )
        worker_restart_response = client.get(
            "/v1/events/event-123/worker-restart-readiness",
            params={"session_date": "2026-05-11"},
        )
    finally:
        client.app.dependency_overrides.clear()

    assert submit_response.status_code == 202
    assert monitor_response.status_code == 202
    assert state_response.status_code == 200
    assert history_response.status_code == 200
    assert signal_blockers_response.status_code == 200
    assert budget_state_response.status_code == 200
    assert worker_restart_response.status_code == 200
    payload = monitor_response.json()
    states = payload["janus_live_event_states"]
    assert states[0]["schema_version"] == "janus_live_event_state_v1"
    assert states[0]["schema_contract"]["schema_version"] == "janus_live_event_state_contract_v1"
    assert states[0]["schema_contract"]["execution_authority"] is False
    assert "GET /v1/events/{event_id}/live-state/history" in states[0]["schema_contract"]["durable_outputs"]
    assert "GET /v1/events/{event_id}/worker-restart-readiness" in states[0]["schema_contract"]["durable_outputs"]
    assert "readback_endpoints" in states[0]["schema_contract"]["required_sections"]
    assert "signal_aggregation_state" in states[0]["schema_contract"]["required_sections"]
    assert "db_stat_context_state" in states[0]["schema_contract"]["required_sections"]
    assert "quarter_revision_state" in states[0]["schema_contract"]["required_sections"]
    assert states[0]["event_identity"]["event_id"] == "event-123"
    assert states[0]["readback_endpoints"] == {
        "latest": "/v1/events/event-123/live-state?session_date=2026-05-11",
        "history": "/v1/events/event-123/live-state/history?session_date=2026-05-11",
        "worker_restart_readiness": (
            "/v1/events/event-123/worker-restart-readiness?session_date=2026-05-11"
        ),
    }
    assert states[0]["execution_authority"] is False
    assert states[0]["strategy_plan_state"]["sleeve_count"] == 1
    assert states[0]["direct_clob_account_state"]["summary"]["open_order_count"] == 0
    assert states[0]["direct_clob_account_state"]["summary"]["trusted_trade_count"] == 0
    assert states[0]["blockers"][0]["reason"] == "live_strategy_worker_not_running"
    assert states[0]["worker_restart_safety_state"]["schema_version"] == "janus_live_event_state_worker_restart_safety_v1"
    assert states[0]["worker_restart_safety_state"]["execution_authority"] is False
    assert states[0]["worker_restart_safety_state"]["status"] == "fail_closed_explicit_current_scope_required"
    assert states[0]["worker_restart_safety_state"]["restart_allowed"] is False
    assert states[0]["worker_restart_safety_state"]["safe_to_restart_without_explicit_payload"] is False
    assert states[0]["worker_restart_safety_state"]["event_scope_contains_requested_event"] is False
    assert states[0]["worker_restart_safety_state"]["expected_scope_contains_requested_event"] is True
    db_context_state = states[0]["db_stat_context_state"]
    assert db_context_state["schema_version"] == "janus_live_event_state_db_stat_context_state_v1"
    assert db_context_state["status"] == "recorded"
    assert db_context_state["source_schema"] == "db_stat_context_trace_v1"
    assert db_context_state["llm_context_inclusion_status"] == "included"
    assert db_context_state["source_count"] == 2
    assert db_context_state["included_source_count"] == 2
    assert db_context_state["stale_source_count"] == 0
    assert db_context_state["missing_source_count"] == 0
    assert db_context_state["source_status_counts"] == {"current": 2}
    assert db_context_state["freshness_status_counts"] == {"current": 2}
    assert db_context_state["source_ids"] == ["wnba_team_player_stats_snapshot", "optional_pregame_prior"]
    assert db_context_state["included_source_ids"] == ["wnba_team_player_stats_snapshot", "optional_pregame_prior"]
    assert db_context_state["source_paths"] == [str(prior_path)]
    assert db_context_state["teams"] == ["Aces", "Valkyries"]
    assert db_context_state["execution_authority"] is False
    quarter_revision_state = states[0]["quarter_revision_state"]
    assert quarter_revision_state["schema_version"] == "janus_live_event_state_quarter_revision_state_v1"
    assert quarter_revision_state["schema_contract"]["schema_version"] == (
        "janus_live_event_state_quarter_revision_contract_v1"
    )
    assert quarter_revision_state["schema_contract"]["expected_quarter_labels"] == [
        "q1_end",
        "halftime_q2_end",
        "q3_end",
    ]
    assert "missing_expected_quarter_labels" in quarter_revision_state["schema_contract"]["required_sections"]
    assert quarter_revision_state["status"] == "recorded"
    assert quarter_revision_state["source_schema"] == "strategy_plan_quarter_revision_review_v1"
    assert quarter_revision_state["quarter_revision_review_count"] == 1
    assert quarter_revision_state["quarter_labels"] == ["q1_end"]
    assert quarter_revision_state["missing_expected_quarter_labels"] == ["halftime_q2_end", "q3_end"]
    assert quarter_revision_state["review_status_counts"] == {"revision_recorded": 1}
    assert quarter_revision_state["reviewed_sleeve_count"] == 2
    assert quarter_revision_state["order_endpoint_call_allowed"] is False
    assert quarter_revision_state["execution_authority"] is False
    assert states[0]["latest_live_worker_tick_state"]["status"] == "recorded"
    assert states[0]["scoreboard_resolution"]["selected_game_id"] == "game-123"
    assert states[0]["game_state"]["period"] == 2
    assert states[0]["game_state"]["clock"] == "04:12"
    assert states[0]["signal_aggregation_state"]["schema_version"] == "janus_live_event_state_signal_aggregation_v1"
    assert states[0]["signal_aggregation_state"]["blocker_scope_counts"] == {"event": 1, "local_sleeve": 1}
    assert states[0]["signal_aggregation_state"]["candidate_blocking_blocker_count"] == 1
    assert states[0]["signal_aggregation_state"]["nonblocking_local_blocker_count"] == 1
    minimum_summary = states[0]["signal_aggregation_state"]["minimum_order_policy_summary"]
    assert minimum_summary["schema_version"] == "janus_live_event_state_minimum_order_policy_summary_v1"
    assert minimum_summary["schema_contract"]["schema_version"] == (
        "janus_live_event_state_minimum_order_policy_summary_contract_v1"
    )
    assert minimum_summary["schema_contract"]["source_schema"] == "polymarket_order_type_minimum_policy_v1"
    assert "order_type_minimum_rule_counts" in minimum_summary["schema_contract"]["required_sections"]
    assert minimum_summary["execution_authority"] is False
    assert minimum_summary["policy_count"] == 1
    assert minimum_summary["candidate_policy_count"] == 1
    assert minimum_summary["order_type_minimum_rule_counts"] == {"limit_min_shares": 1}
    assert minimum_summary["minimum_rule_status_counts"] == {"passed": 1}
    assert minimum_summary["order_type_counts"] == {"limit": 1}
    assert minimum_summary["share_minimum_applies_count"] == 1
    assert minimum_summary["notional_minimum_applies_count"] == 0
    assert states[0]["budget_state"]["schema_version"] == "janus_live_event_state_budget_state_v1"
    assert states[0]["budget_state"]["effective_event_cap_usd"] == 18.0
    assert states[0]["budget_state"]["cap_readback_status"] == "effective_cap_adjusted_from_configured_cap"
    assert states[0]["budget_state"]["execution_authority"] is False
    assert states[0]["budget_state"]["profit_ratcheted_addon_usd"] == 8.0
    assert states[0]["budget_state"]["risk_promotion_evidence"]["allowed_profit_addon_usd"] == 8.0
    target_state = states[0]["target_management_state"]
    assert target_state["schema_version"] == "janus_live_event_state_target_management_state_v1"
    assert target_state["status"] == "not_applicable_flat"
    assert target_state["direct_target_coverage"]["schema_version"] == "janus_live_event_state_direct_target_coverage_v1"
    assert target_state["direct_target_coverage"]["active_position_count"] == 0
    assert target_state["direct_target_coverage"]["execution_authority"] is False
    assert states[0]["reduce_stop_state"]["status"] == "not_applicable_flat"

    record = payload["janus_live_event_state_records"]["records"][0]
    latest_path = Path(record["latest_path"])
    history_path = Path(record["history_path"])
    assert latest_path.exists()
    assert history_path.exists()
    persisted = json.loads(latest_path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == "janus_live_event_state_v1"
    assert persisted["schema_contract"]["primary_schema"] == "janus_live_event_state_v1"
    assert persisted["readback_endpoints"]["history"] == (
        "/v1/events/event-123/live-state/history?session_date=2026-05-11"
    )
    assert persisted["readback_endpoints"]["worker_restart_readiness"] == (
        "/v1/events/event-123/worker-restart-readiness?session_date=2026-05-11"
    )
    assert persisted["worker_restart_safety_state"]["restart_allowed"] is False
    assert persisted["db_stat_context_state"]["included_source_count"] == 2
    assert persisted["quarter_revision_state"]["quarter_revision_review_count"] == 1
    assert str(latest_path) in persisted["latest_artifact_paths"]
    assert state_response.json()["event_identity"]["event_id"] == "event-123"
    assert state_response.json()["readback_endpoints"]["latest"] == (
        "/v1/events/event-123/live-state?session_date=2026-05-11"
    )
    assert state_response.json()["readback_endpoints"]["worker_restart_readiness"] == (
        "/v1/events/event-123/worker-restart-readiness?session_date=2026-05-11"
    )
    assert state_response.json()["schema_contract"]["schema_version"] == "janus_live_event_state_contract_v1"
    assert state_response.json()["worker_restart_safety_state"]["source_schema"] == "live_strategy_worker_config_trust_v1"
    assert state_response.json()["db_stat_context_state"]["source_ids"] == [
        "wnba_team_player_stats_snapshot",
        "optional_pregame_prior",
    ]
    assert state_response.json()["quarter_revision_state"]["quarter_labels"] == ["q1_end"]
    history_payload = history_response.json()
    assert history_payload["schema_version"] == "janus_live_event_state_history_readback_v1"
    assert history_payload["schema_contract"]["schema_version"] == (
        "janus_live_event_state_history_readback_contract_v1"
    )
    assert history_payload["schema_contract"]["primary_schema"] == "janus_live_event_state_history_readback_v1"
    assert history_payload["schema_contract"]["source_schema"] == "janus_live_event_state_v1"
    assert history_payload["schema_contract"]["execution_authority"] is False
    assert "records" in history_payload["schema_contract"]["required_sections"]
    assert "scoreboard_resolution" in history_payload["schema_contract"]["record_required_sections"]
    assert history_payload["schema_contract"]["limit_bounds"] == {"min": 1, "max": 500}
    assert history_payload["status"] == "recorded"
    assert history_payload["event_id"] == "event-123"
    assert history_payload["session_date"] == "2026-05-11"
    assert history_payload["requested_limit"] == 5
    assert history_payload["effective_limit"] == 5
    assert history_payload["total_record_count"] == 1
    assert history_payload["returned_record_count"] == 1
    assert history_payload["invalid_line_count"] == 0
    assert history_payload["execution_authority"] is False
    assert history_payload["records"][0]["schema_version"] == "janus_live_event_state_v1"
    assert history_payload["records"][0]["event_identity"]["event_id"] == "event-123"
    assert history_payload["records"][0]["scoreboard_resolution"]["selected_game_id"] == "game-123"
    assert history_payload["records"][0]["signal_aggregation_state"]["blocker_scope_counts"] == {
        "event": 1,
        "local_sleeve": 1,
    }
    assert history_payload["records"][0]["execution_authority"] is False
    signal_blockers_payload = signal_blockers_response.json()
    assert signal_blockers_payload["schema_version"] == "janus_event_signal_blocker_readback_v1"
    assert signal_blockers_payload["schema_contract"]["schema_version"] == (
        "janus_event_signal_blocker_readback_contract_v1"
    )
    assert signal_blockers_payload["schema_contract"]["blocker_row_schema"] == "live_signal_blocker_scope_v1"
    assert signal_blockers_payload["readback_endpoints"]["self"] == (
        "/v1/events/event-123/signal-blockers?session_date=2026-05-11"
    )
    assert signal_blockers_payload["source"] == "janus_live_event_state_latest_json"
    assert signal_blockers_payload["blocker_scope_counts"] == {"event": 1, "local_sleeve": 1}
    assert signal_blockers_payload["candidate_blocking_blocker_count"] == 1
    assert signal_blockers_payload["nonblocking_local_blocker_count"] == 1
    assert signal_blockers_payload["expected_next_action_counts"] == {
        "block_order_candidate_until_budget_changes": 1,
        "preserve_unrelated_order_candidates": 1,
    }
    assert signal_blockers_payload["blocker_scope_rows"] == [
        {
            "schema_version": "live_signal_blocker_scope_v1",
            "reason_code": "price_band_not_met",
            "scope": "local_sleeve",
            "candidate_blocking": False,
            "expected_next_action": "preserve_unrelated_order_candidates",
            "affected_sleeve_ids": [],
            "candidate_sleeve_ids": [],
            "unaffected_sleeve_ids": [],
            "trigger_id": None,
            "trigger_type": None,
            "trigger_source": None,
            "affected_signal_types": [],
        },
        {
            "schema_version": "live_signal_blocker_scope_v1",
            "reason_code": "event_budget_exceeded",
            "scope": "event",
            "candidate_blocking": True,
            "expected_next_action": "block_order_candidate_until_budget_changes",
            "affected_sleeve_ids": [],
            "candidate_sleeve_ids": [],
            "unaffected_sleeve_ids": [],
            "trigger_id": None,
            "trigger_type": None,
            "trigger_source": None,
            "affected_signal_types": [],
        },
    ]
    assert signal_blockers_payload["order_endpoint_call_allowed"] is False
    assert signal_blockers_payload["execution_authority"] is False
    budget_payload = budget_state_response.json()
    assert budget_payload["schema_version"] == "janus_event_budget_readback_v1"
    assert budget_payload["schema_contract"]["schema_version"] == "janus_event_budget_readback_contract_v1"
    assert budget_payload["schema_contract"]["profit_addon_policy_schema"] == (
        "account_confirmed_profit_budget_addon_v1"
    )
    assert budget_payload["readback_endpoints"]["self"] == (
        "/v1/events/event-123/budget-state?session_date=2026-05-11"
    )
    assert budget_payload["source"] == "janus_live_event_state_latest_json"
    assert budget_payload["source_schema"] == "janus_live_event_state_budget_state_v1"
    assert budget_payload["event_cap_before_profit_addon_usd"] == 10.0
    assert budget_payload["event_cap_usd"] == 18.0
    assert budget_payload["remaining_notional_usd"] == 7.5
    assert budget_payload["profit_ratcheted_requested_addon_usd"] == 8.0
    assert budget_payload["profit_ratcheted_addon_usd"] == 8.0
    assert budget_payload["profit_ratcheted_blocked_addon_usd"] == 0.0
    assert budget_payload["risk_promotion_allowed"] is True
    assert budget_payload["risk_promotion_source_confidence"] == "account_confirmed"
    assert budget_payload["risk_promotion_blocker_codes"] == []
    assert budget_payload["profit_addon_policy"]["schema_version"] == "account_confirmed_profit_budget_addon_v1"
    assert budget_payload["profit_addon_policy"]["mode"] == "dollar_for_dollar_realized_profit"
    assert budget_payload["profit_addon_policy"]["execution_authority"] is False
    assert budget_payload["order_endpoint_call_allowed"] is False
    assert budget_payload["execution_authority"] is False
    worker_restart_payload = worker_restart_response.json()
    assert worker_restart_payload["schema_version"] == "janus_event_worker_restart_readiness_v1"
    assert worker_restart_payload["schema_contract"]["schema_version"] == (
        "janus_event_worker_restart_readiness_contract_v1"
    )
    assert worker_restart_payload["schema_contract"]["worker_restart_safety_schema"] == (
        "janus_live_event_state_worker_restart_safety_v1"
    )
    assert worker_restart_payload["status"] == "restart_blocked_explicit_current_payload_required"
    assert worker_restart_payload["source_schema"] == "live_strategy_worker_config_trust_v1"
    assert worker_restart_payload["readback_endpoints"]["self"] == (
        "/v1/events/event-123/worker-restart-readiness?session_date=2026-05-11"
    )
    assert worker_restart_payload["worker_restart_safety_state"]["restart_allowed"] is False
    assert worker_restart_payload["worker_restart_safety_state"]["safe_to_restart_without_explicit_payload"] is False
    assert worker_restart_payload["worker_restart_safety_state"]["event_scope_contains_requested_event"] is False
    assert worker_restart_payload["canonical_worker_restart_safety_state"]["schema_version"] == (
        "janus_live_event_state_worker_restart_safety_v1"
    )
    assert worker_restart_payload["strategy_plan_gate"]["ready_for_strategy_evaluation"] is True
    assert worker_restart_payload["blocker_reasons"] == [
        "effective_worker_scope_missing_requested_event",
        "explicit_start_payload_required",
        "live_money_restart_requires_explicit_account_scope",
        "worker_restart_not_allowed_by_config_trust",
    ]
    assert worker_restart_payload["order_endpoint_call_allowed"] is False
    assert worker_restart_payload["execution_authority"] is False


def test_live_event_state_escalates_adverse_position_codex_review_pytest(monkeypatch) -> None:
    monkeypatch.setattr(
        ops_router,
        "_latest_live_worker_tick_state",
        lambda *, day, event_id: {
            "schema_version": "janus_live_event_state_live_tick_readback_v1",
            "status": "recorded",
            "event_id": event_id,
            "budget_state": {"event_cap_usd": 20.0, "remaining_notional_usd": 12.0},
        },
    )
    monkeypatch.setattr(
        ops_router,
        "_build_event_controls_state",
        lambda event_id, *, day: {
            "schema_version": "janus_live_event_state_event_controls_state_v1",
            "status": "recorded",
            "event_id": event_id,
            "parameters": {"event_cap_usd": 20.0},
            "order_endpoint_call_allowed": False,
            "execution_authority": False,
        },
    )
    monkeypatch.setattr(
        ops_router,
        "_build_db_stat_context_state",
        lambda event_id, *, day: {
            "schema_version": "janus_live_event_state_db_stat_context_state_v1",
            "status": "not_recorded",
            "event_id": event_id,
            "execution_authority": False,
        },
    )

    states = ops_router._build_janus_live_event_states(
        session_date="2026-06-01",
        event_ids=["wnba-min-phx-2026-06-01"],
        ops_status={"status": "ok"},
        integrity={},
        strategy_plan_gate={
            "missing_event_ids": [],
            "current_plans": [
                {
                    "event_id": "wnba-min-phx-2026-06-01",
                    "sleeves": [],
                    "sleeve_count": 0,
                }
            ],
        },
        llm_runtime_status={
            "status": "recorded",
            "items": [
                {
                    "event_id": "wnba-min-phx-2026-06-01",
                    "status": "skipped_unavailable",
                    "response_status": "skipped_unavailable",
                    "adoption_status": "not_adoptable",
                    "trigger_count": 2,
                    "trigger_types": ["position_adverse_move", "manual_operator_position"],
                    "path": "local/shared/artifacts/llm-runtime/2026-06-01/trace.json",
                    "codex_fallback_state": {
                        "status": "codex_strategy_required",
                        "review_required": True,
                        "codex_strategy_required": True,
                        "internal_llm_unavailable": True,
                        "allowed_fallback_actions": [
                            "submit_reviewed_strategy_plan_candidate",
                            "record_no_action_with_reason",
                        ],
                    },
                    "llm_runtime_state": {
                        "schema_version": "llm_runtime_state_flags_v1",
                        "status": "codex_strategy_required",
                        "review_required": True,
                        "codex_strategy_required": True,
                        "internal_llm_unavailable": True,
                    },
                }
            ],
        },
        live_strategy_worker_status={"status": "stopped"},
        live_execution_evidence={},
        live_microstructure_context={"items": []},
        current_event_inventory={
            "status": "recorded",
            "items": [
                {
                    "event_id": "wnba-min-phx-2026-06-01",
                    "status": "recorded",
                    "open_order_count": 2,
                    "open_position_count": 1,
                    "active_open_position_count": 1,
                    "unresolved_inventory_present": True,
                }
            ],
        },
        live_monitor_readiness={"status": "ready", "gate": "GREEN", "blocker_reasons": []},
    )

    state = states[0]
    review_state = state["strategy_review_escalation_state"]
    assert review_state["schema_version"] == "janus_live_event_state_strategy_review_escalation_v1"
    assert review_state["status"] == "critical_review_required"
    assert review_state["critical_review_required"] is True
    assert review_state["critical_trigger_types"] == [
        "position_adverse_move",
        "manual_operator_position",
    ]
    assert review_state["codex_strategy_required"] is True
    assert review_state["order_endpoint_call_allowed"] is False
    assert review_state["execution_authority"] is False
    review_blockers = [
        blocker
        for blocker in state["blockers"]
        if blocker["reason"] == "critical_strategy_review_required"
    ]
    assert review_blockers == [
        {
            "source": "strategy_review_escalation_state",
            "reason": "critical_strategy_review_required",
            "severity": "RED",
            "scope": "event_strategy_review",
            "execution_blocking": True,
            "trigger_types": ["position_adverse_move", "manual_operator_position"],
            "codex_strategy_required": True,
            "expected_next_action": "submit_or_record_janus_gated_strategy_review_before_live_restart",
        }
    ]


def test_quarter_revision_readback_endpoint_exposes_llm_runtime_reviews_pytest(monkeypatch) -> None:
    monkeypatch.setattr(
        ops_router,
        "load_latest_llm_runtime_status",
        lambda *, session_date, event_ids: {
            "status": "recorded",
            "session_date": session_date,
            "event_count": len(event_ids),
            "recorded_event_count": len(event_ids),
            "artifact_root": "local/shared/artifacts/llm-runtime",
            "items": [
                {
                    "event_id": "event-123",
                    "status": "recorded",
                    "path": "local/shared/artifacts/llm-runtime/2026-05-11/trace.json",
                    "quarter_revision_reviews": [
                        {
                            "schema_version": "strategy_plan_quarter_revision_review_v1",
                            "quarter_label": "q1_end",
                            "review_status": "revision_recorded",
                            "reviewed_sleeve_count": 2,
                            "order_endpoint_call_allowed": False,
                            "execution_authority": False,
                        }
                    ],
                    "quarter_revision_review_artifacts": [
                        {
                            "path": "local/shared/artifacts/llm-runtime/2026-05-11/quarter-revision-reviews/event-123/q1.json"
                        }
                    ],
                }
            ],
            "safety_controls": {},
        },
    )
    client = TestClient(create_app())

    response = client.get(
        "/v1/events/event-123/quarter-revisions",
        params={"session_date": "2026-05-11"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "janus_event_quarter_revision_readback_v1"
    assert payload["schema_contract"]["schema_version"] == "janus_event_quarter_revision_readback_contract_v1"
    assert payload["schema_contract"]["execution_authority"] is False
    assert "quarter_revision_state" in payload["schema_contract"]["required_sections"]
    assert payload["readback_endpoints"]["self"] == (
        "/v1/events/event-123/quarter-revisions?session_date=2026-05-11"
    )
    assert payload["readback_endpoints"]["live_state"] == (
        "/v1/events/event-123/live-state?session_date=2026-05-11"
    )
    assert payload["status"] == "recorded"
    assert payload["llm_runtime_status"]["trace_artifact_path"] == (
        "local/shared/artifacts/llm-runtime/2026-05-11/trace.json"
    )
    assert payload["quarter_revision_state"]["schema_contract"]["schema_version"] == (
        "janus_live_event_state_quarter_revision_contract_v1"
    )
    assert payload["quarter_revision_state"]["quarter_revision_review_count"] == 1
    assert payload["quarter_revision_state"]["quarter_labels"] == ["q1_end"]
    assert payload["quarter_revision_state"]["missing_expected_quarter_labels"] == [
        "halftime_q2_end",
        "q3_end",
    ]
    assert payload["quarter_revision_reviews"][0]["quarter_label"] == "q1_end"
    assert payload["artifact_paths"] == [
        "local/shared/artifacts/llm-runtime/2026-05-11/quarter-revision-reviews/event-123/q1.json"
    ]
    assert payload["order_endpoint_call_allowed"] is False
    assert payload["execution_authority"] is False


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
                            "timestamp": int(datetime(2026, 5, 11, 1, 0, tzinfo=timezone.utc).timestamp()),
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


def test_live_monitor_inventory_quarantines_zero_timestamp_trades_pytest(monkeypatch) -> None:
    monkeypatch.setattr(
        ops_router,
        "load_current_strategy_plan_for_event",
        lambda event_id, *, day=None: (
            {
                "context_summary": {
                    "event_slug": "wnba-las-gsv-2026-05-31",
                    "game_start_utc": "2026-05-31T19:30:00Z",
                },
                "active_strategies": [{"entry_rules": {"token_id": "token-gsv"}}],
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
                "open_positions": {"ok": True, "positions": []},
                "current_token_trades": {
                    "ok": True,
                    "trades": [
                        {
                            "id": "oversized-history-row",
                            "asset_id": "token-gsv",
                            "market": "condition-gsv",
                            "side": "BUY",
                            "price": 0.44,
                            "size": 240.0,
                            "timestamp": 0,
                        },
                        {
                            "id": "valid-live-row",
                            "asset_id": "token-gsv",
                            "market": "condition-gsv",
                            "side": "BUY",
                            "price": 0.42,
                            "size": 5.0,
                            "timestamp": int(datetime(2026, 5, 31, 19, 45, tzinfo=timezone.utc).timestamp()),
                        },
                    ],
                },
            }
        },
        event_ids=["wnba-lva-gsv-2026-05-31"],
        day="2026-05-31",
    )

    assert inventory["trade_count"] == 1
    item = inventory["items"][0]
    assert item["trade_count"] == 1
    assert item["trusted_trade_count"] == 1
    assert item["trades"][0]["id"] == "valid-live-row"
    assert item["untrusted_trade_count"] == 1
    assert item["all_observed_trade_count"] == 2
    assert item["untrusted_trades"][0]["id"] == "oversized-history-row"
    assert item["untrusted_trades"][0]["current_account_evidence_reason"] == "direct_trade_timestamp_missing_or_invalid"


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


def test_live_state_direct_target_coverage_reads_open_sell_targets_pytest() -> None:
    coverage = ops_router._direct_target_coverage_state(  # noqa: SLF001
        {
            "event_id": "event-123",
            "active_open_positions": [
                {
                    "asset": "token-dal",
                    "outcome": "Dallas Wings",
                    "size": "5",
                    "current_value": "3.25",
                }
            ],
            "open_orders": [
                {
                    "id": "target-dal-1",
                    "asset_id": "token-dal",
                    "side": "SELL",
                    "price": 0.72,
                    "size": "5",
                }
            ],
        }
    )

    assert coverage["schema_version"] == "janus_live_event_state_direct_target_coverage_v1"
    assert coverage["status"] == "covered"
    assert coverage["active_position_count"] == 1
    assert coverage["covered_position_count"] == 1
    assert coverage["uncovered_position_count"] == 0
    assert coverage["open_sell_order_count"] == 1
    assert coverage["rows"][0]["coverage_status"] == "covered_by_open_sell_order"
    assert coverage["rows"][0]["target_order_external_ids"] == ["target-dal-1"]
    assert coverage["execution_authority"] is False


def test_live_state_direct_target_coverage_flags_missing_sell_targets_pytest() -> None:
    coverage = ops_router._direct_target_coverage_state(  # noqa: SLF001
        {
            "event_id": "event-123",
            "active_open_positions": [
                {
                    "asset": "token-sea",
                    "outcome": "Seattle Storm",
                    "size": "5",
                    "current_value": "1.15",
                }
            ],
            "open_orders": [
                {
                    "id": "unrelated-target",
                    "asset_id": "token-dal",
                    "side": "SELL",
                    "price": 0.74,
                    "size": "5",
                }
            ],
        }
    )

    assert coverage["status"] == "uncovered_positions_present"
    assert coverage["active_position_count"] == 1
    assert coverage["covered_position_count"] == 0
    assert coverage["uncovered_position_count"] == 1
    assert coverage["open_sell_order_count"] == 1
    assert coverage["rows"][0]["coverage_status"] == "missing_open_sell_target"
    assert coverage["rows"][0]["target_order_external_ids"] == []
    assert coverage["execution_authority"] is False


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
            return {"status": "stopped", "worker_thread_alive": False, "config": {}}

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
    monkeypatch.setattr(ops_router, "session_date", lambda value=None: value or "2026-05-13")
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
            "max_buy_notional_usd": None,
        },
    )
    stop_response = client.post("/v1/ops/live-strategy-worker/stop")

    assert status_response.status_code == 200
    assert tick_response.status_code == 202
    assert tick_response.json()["event_ids"] == ["event-1"]
    assert tick_response.json()["tick_request_safety"]["schema_version"] == (
        "live_strategy_worker_tick_request_safety_v1"
    )
    assert tick_response.json()["tick_request_safety"]["tick_allowed"] is True
    assert start_response.status_code == 202
    assert start_response.json()["start_status"] == "started"
    assert stop_response.status_code == 202
    assert calls == [
        ("status", None),
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
                "max_buy_notional_usd": None,
            },
        ),
        ("stop", None),
    ]


def test_live_strategy_worker_tick_blocks_stale_explicit_session_scope_pytest(tmp_path, monkeypatch) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    monkeypatch.setattr(ops_router, "session_date", lambda value=None: value or "2026-06-01")
    calls: list[dict | None] = []

    class FakeWorker:
        def status(self):
            return {
                "status": "stopped",
                "worker_thread_alive": False,
                "config": {
                    "session_date": "2026-05-26",
                    "event_ids": ["nba-sas-okc-2026-05-26"],
                    "execute": False,
                    "live_money": False,
                },
            }

        def run_once(self, overrides):
            calls.append(overrides)
            return {"ok": True, "status": "completed"}

    monkeypatch.setattr(ops_router, "get_live_strategy_worker", lambda: FakeWorker())
    client = TestClient(create_app())

    response = client.post(
        "/v1/ops/live-strategy-worker/tick",
        json={
            "session_date": "2026-05-26",
            "event_ids": ["nba-sas-okc-2026-05-26"],
            "source": "pytest-stale-session",
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["tick_request_safety"]["tick_allowed"] is False
    assert payload["tick_request_safety"]["effective_session_date"] == "2026-05-26"
    assert payload["tick_request_safety"]["current_session_date"] == "2026-06-01"
    assert payload["tick_request_safety"]["event_scope_current"] is False
    assert payload["tick_request_safety"]["blocker_reasons"] == ["manual_tick_stale_event_scope"]
    assert calls == []


def test_live_strategy_worker_empty_tick_payload_fails_closed_instead_of_reusing_scope_pytest(
    tmp_path, monkeypatch
) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    calls: list[dict | None] = []

    class FakeWorker:
        def status(self):
            return {
                "status": "stopped",
                "worker_thread_alive": False,
                "config": {
                    "session_date": "2026-05-13",
                    "event_ids": ["event-valid"],
                    "execute": True,
                    "live_money": True,
                },
            }

        def run_once(self, overrides):
            calls.append(overrides)
            return {"ok": True, "status": "completed", "event_ids_override_present": "event_ids" in overrides}

    monkeypatch.setattr(ops_router, "get_live_strategy_worker", lambda: FakeWorker())
    client = TestClient(create_app())

    response = client.post("/v1/ops/live-strategy-worker/tick", json={})

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["reason"] == "live_strategy_worker_tick_request_not_trusted"
    assert payload["tick_request_safety"]["schema_version"] == "live_strategy_worker_tick_request_safety_v1"
    assert payload["tick_request_safety"]["tick_allowed"] is False
    assert payload["tick_request_safety"]["execution_authority"] is False
    assert payload["tick_request_safety"]["blocker_reasons"] == [
        "manual_live_tick_requires_explicit_account_scope",
        "manual_tick_requires_explicit_event_scope",
        "manual_tick_stale_event_scope",
    ]
    assert calls == []


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
                {
                    "id": "direct-trade-1",
                    "token_id": "token-1",
                    "timestamp": int(datetime(2026, 5, 10, 1, 0, tzinfo=timezone.utc).timestamp()),
                },
                {"id": "timestamp-zero-history-row", "token_id": "token-1", "timestamp": 0, "size": 50},
                {"id": "global-trade-1", "token_id": "other-token"},
            ],
            "direct_evidence": {
                "enabled": True,
                "ok": True,
                "error": None,
                "open_order_count": 1,
                "open_position_count": 1,
                "trade_count": 3,
                "open_orders": [{"id": "global-order-1", "token_id": "other-token"}],
                "open_positions": [{"asset_id": "other-token", "size": "5"}],
                "trades": [
                    {
                        "id": "direct-trade-1",
                        "token_id": "token-1",
                        "timestamp": int(datetime(2026, 5, 10, 1, 0, tzinfo=timezone.utc).timestamp()),
                    },
                    {"id": "timestamp-zero-history-row", "token_id": "token-1", "timestamp": 0, "size": 50},
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
        assert kwargs["direct_trade_rows"] == [
            {
                "id": "direct-trade-1",
                "token_id": "token-1",
                "timestamp": int(datetime(2026, 5, 10, 1, 0, tzinfo=timezone.utc).timestamp()),
            }
        ]
        return {
            "order_count": 1,
            "linked_trade_count": 0,
            "unknown_lifecycle_count": 0,
            "pnl_attribution_ready": True,
            "items": [
                {
                    "order_id": "order-1",
                    "external_order_id": "0xorder1",
                    "side": "buy",
                    "outcome_id": "outcome-1",
                    "token_id": "token-1",
                    "fill_evidence_source": "direct_clob_trades",
                    "effective_fill_size": Decimal("5"),
                    "effective_cashflow_usd": Decimal("-4.75"),
                    "effective_fee_usd": Decimal("0"),
                    "direct_fill_size": Decimal("5"),
                    "direct_cashflow_usd": Decimal("-4.75"),
                    "direct_fee_usd": Decimal("0"),
                    "direct_trade_ids": ["direct-trade-1"],
                    "direct_local_fill_mismatch": False,
                }
            ],
        }

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
        "_read_live_worker_tick_summaries",
        lambda *, day, event_ids: {
            event_id: {
                "status": "recorded",
                "tick_count": 2,
                "heartbeat_present": True,
                "heartbeat_event_match": True,
                "heartbeat_event_ids": [event_id],
            }
            for event_id in event_ids
        },
    )
    replay_summary = lambda *, day, event_id: {
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
    }
    monkeypatch.setattr(
        ops_router,
        "_read_postgame_replay_tick_stream_summary",
        replay_summary,
    )
    monkeypatch.setattr(
        ops_router,
        "_read_postgame_replay_tick_stream_summaries",
        lambda *, day, event_ids: {event_id: replay_summary(day=day, event_id=event_id) for event_id in event_ids},
    )
    live_state_path = ops_router._janus_live_event_state_latest_path(
        "nba-sas-min-2026-05-10",
        day="2026-05-10",
    )
    live_state_path.parent.mkdir(parents=True, exist_ok=True)
    live_state_payload = {
        "schema_version": "janus_live_event_state_v1",
        "generated_at_utc": "2026-05-11T01:05:00Z",
        "event_identity": {"event_id": "nba-sas-min-2026-05-10"},
        "execution_authority": False,
        "latest_live_worker_tick_state": {
            "schema_version": "janus_live_event_state_live_tick_readback_v1",
            "status": "recorded",
            "tick_finished_at_utc": "2026-05-11T01:04:58Z",
        },
        "scoreboard_resolution": {
            "schema_version": "scoreboard_resolution_diagnostics_v1",
            "status": "resolved",
            "selected_game_id": "nba-game-1",
            "parsed_event_date": "2026-05-10",
            "parsed_teams": {"away": "sas", "home": "min"},
            "searched_date_windows": ["2026-05-10", "2026-05-11"],
            "candidate_games": [
                {
                    "game_id": "nba-game-1",
                    "game_date": "2026-05-11",
                    "away_team": "San Antonio Spurs",
                    "home_team": "Minnesota Timberwolves",
                    "match_status": "selected",
                },
                {
                    "game_id": "nba-game-2",
                    "game_date": "2026-05-10",
                    "away_team": "New York Knicks",
                    "home_team": "Philadelphia 76ers",
                    "match_status": "team_mismatch",
                },
            ],
        },
        "game_state": {
            "schema_version": "janus_live_event_state_game_state_v1",
            "status": "recorded",
            "game_status": "final",
            "period": 4,
            "clock": "00:00",
        },
        "db_stat_context_state": {
            "schema_version": "janus_live_event_state_db_stat_context_state_v1",
            "status": "recorded",
            "event_id": "nba-sas-min-2026-05-10",
            "source_schema": "db_stat_context_trace_v1",
            "resolved_strategy_plan_event_id": "nba-sas-min-2026-05-10",
            "llm_context_inclusion_status": "included",
            "source_count": 2,
            "included_source_count": 2,
            "stale_source_count": 0,
            "missing_source_count": 0,
            "source_ids": ["nba_team_player_stats_snapshot", "optional_pregame_prior"],
            "included_source_ids": ["nba_team_player_stats_snapshot", "optional_pregame_prior"],
            "stale_source_ids": [],
            "source_status_counts": {"current": 2},
            "freshness_status_counts": {"current": 2},
            "stale_blocker_counts": {},
            "teams": ["San Antonio Spurs", "Minnesota Timberwolves"],
            "players": [],
            "source_paths": [
                "local/shared/artifacts/pregame-priors/2026-05-10/nba-sas-min-2026-05-10/current.json"
            ],
            "liveness_blocking": False,
            "execution_authority": False,
        },
        "quarter_revision_state": {
            "schema_version": "janus_live_event_state_quarter_revision_state_v1",
            "status": "recorded",
            "event_id": "nba-sas-min-2026-05-10",
            "source_schema": "strategy_plan_quarter_revision_review_v1",
            "quarter_revision_review_count": 1,
            "artifact_count": 1,
            "quarter_labels": ["halftime_q2_end"],
            "missing_expected_quarter_labels": ["q1_end", "q3_end"],
            "review_status_counts": {"revision_recorded": 1},
            "reviewed_sleeve_count": 3,
            "artifact_paths": [
                "local/shared/artifacts/llm-runtime/2026-05-10/nba-sas-min-2026-05-10/quarter-review_halftime.json"
            ],
            "order_endpoint_call_allowed": False,
            "execution_authority": False,
        },
        "signal_aggregation_state": {
            "schema_version": "janus_live_event_state_signal_aggregation_v1",
            "status": "recorded",
            "decision_type": "blocked",
            "signal_count": 3,
            "order_intent_candidate_count": 1,
            "blocker_count": 2,
            "candidate_blocking_blocker_count": 1,
            "nonblocking_local_blocker_count": 1,
            "blocker_reason_counts": {"scoreboard_freshness_required": 1, "price_band_not_met": 1},
            "blocker_scope_counts": {"global_safety": 1, "local_sleeve": 1},
            "expected_next_action_counts": {
                "block_selected_order_candidate": 1,
                "preserve_unrelated_order_candidates": 1,
            },
            "minimum_order_policy_summary": {
                "schema_version": "janus_live_event_state_minimum_order_policy_summary_v1",
                "status": "recorded",
                "policy_count": 2,
                "candidate_policy_count": 1,
                "blocker_policy_count": 1,
                "order_type_minimum_rule_counts": {"limit_min_shares": 1, "market_min_notional": 1},
                "minimum_rule_status_counts": {"blocked": 1, "passed": 1},
                "order_type_counts": {"limit": 1, "market": 1},
                "share_minimum_applies_count": 1,
                "notional_minimum_applies_count": 1,
                "execution_authority": False,
            },
        },
        "budget_state": {
            "schema_version": "janus_live_event_state_budget_state_v1",
            "status": "recorded",
            "budget_status": "within_budget",
            "event_cap_usd": 18.0,
            "remaining_notional_usd": 7.5,
            "profit_ratcheted_requested_addon_usd": 8.0,
            "profit_ratcheted_addon_usd": 8.0,
            "profit_ratcheted_blocked_addon_usd": 0.0,
            "risk_promotion_evidence": {
                "schema_version": "live_tick_risk_promotion_evidence_v1",
                "risk_promotion_allowed": True,
                "source_confidence": "account_confirmed",
                "allowed_profit_addon_usd": 8.0,
            },
        },
    }
    live_state_path.write_text(json.dumps(live_state_payload), encoding="utf-8")
    (live_state_path.parent / "history.jsonl").write_text(
        json.dumps(live_state_payload) + "\n" + json.dumps({**live_state_payload, "generated_at_utc": "2026-05-11T01:06:00Z"}) + "\n",
        encoding="utf-8",
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
    canonical = payload["postgame_canonical_live_event_state"]
    assert canonical["schema_version"] == "postgame_canonical_live_event_state_summary_v1"
    assert canonical["schema_contract"]["schema_version"] == "postgame_canonical_live_event_state_contract_v1"
    assert canonical["schema_contract"]["source_schema"] == "janus_live_event_state_v1"
    assert "aggregate_source_counts" in canonical["schema_contract"]["required_sections"]
    assert "aggregate_reason_counts" in canonical["schema_contract"]["required_sections"]
    assert "aggregate_blocker_scope_counts" in canonical["schema_contract"]["required_sections"]
    assert "aggregate_expected_next_action_counts" in canonical["schema_contract"]["required_sections"]
    assert "aggregate_minimum_order_policy_state" in canonical["schema_contract"]["required_sections"]
    assert "aggregate_db_context_inclusion_status_counts" in canonical["schema_contract"]["required_sections"]
    assert "aggregate_quarter_revision_review_count" in canonical["schema_contract"]["required_sections"]
    assert "readback_endpoints" in canonical["schema_contract"]["item_required_sections"]
    assert "source" in canonical["schema_contract"]["item_required_sections"]
    assert "scoreboard_resolution_state" in canonical["schema_contract"]["item_required_sections"]
    assert "db_stat_context_state" in canonical["schema_contract"]["item_required_sections"]
    assert "quarter_revision_state" in canonical["schema_contract"]["item_required_sections"]
    assert canonical["status"] == "recorded"
    assert canonical["aggregate_source_counts"] == {"janus_live_event_state_latest_json": 1}
    assert canonical["aggregate_reason_counts"] == {}
    assert canonical["aggregate_scoreboard_resolution_status_counts"] == {"resolved": 1}
    assert canonical["aggregate_blocker_scope_counts"] == {"global_safety": 1, "local_sleeve": 1}
    assert canonical["aggregate_blocker_reason_counts"] == {
        "price_band_not_met": 1,
        "scoreboard_freshness_required": 1,
    }
    assert canonical["aggregate_expected_next_action_counts"] == {
        "block_selected_order_candidate": 1,
        "preserve_unrelated_order_candidates": 1,
    }
    assert canonical["aggregate_signal_count"] == 3
    assert canonical["aggregate_order_intent_candidate_count"] == 1
    assert canonical["aggregate_blocker_count"] == 2
    assert canonical["aggregate_candidate_blocking_blocker_count"] == 1
    assert canonical["aggregate_nonblocking_local_blocker_count"] == 1
    assert canonical["aggregate_minimum_order_policy_state"]["schema_version"] == (
        "postgame_canonical_minimum_order_policy_summary_v1"
    )
    assert canonical["aggregate_minimum_order_policy_state"]["schema_contract"]["schema_version"] == (
        "postgame_canonical_minimum_order_policy_summary_contract_v1"
    )
    assert "share_minimum_applies_count" in (
        canonical["aggregate_minimum_order_policy_state"]["schema_contract"]["required_sections"]
    )
    assert canonical["aggregate_minimum_order_policy_state"]["source_schema"] == (
        "janus_live_event_state_minimum_order_policy_summary_v1"
    )
    assert canonical["aggregate_minimum_order_policy_state"]["status"] == "recorded"
    assert canonical["aggregate_minimum_order_policy_state"]["policy_count"] == 2
    assert canonical["aggregate_minimum_order_policy_state"]["candidate_policy_count"] == 1
    assert canonical["aggregate_minimum_order_policy_state"]["blocker_policy_count"] == 1
    assert canonical["aggregate_minimum_order_policy_state"]["order_type_minimum_rule_counts"] == {
        "limit_min_shares": 1,
        "market_min_notional": 1,
    }
    assert canonical["aggregate_minimum_order_policy_state"]["minimum_rule_status_counts"] == {
        "blocked": 1,
        "passed": 1,
    }
    assert canonical["aggregate_minimum_order_policy_state"]["order_type_counts"] == {
        "limit": 1,
        "market": 1,
    }
    assert canonical["aggregate_minimum_order_policy_state"]["share_minimum_applies_count"] == 1
    assert canonical["aggregate_minimum_order_policy_state"]["notional_minimum_applies_count"] == 1
    assert canonical["aggregate_minimum_order_policy_state"]["execution_authority"] is False
    assert canonical["aggregate_budget_state"]["schema_version"] == "postgame_canonical_budget_state_summary_v1"
    assert canonical["aggregate_budget_state"]["source_schema"] == "janus_live_event_state_budget_state_v1"
    assert canonical["aggregate_budget_state"]["execution_authority"] is False
    assert canonical["aggregate_budget_state"]["budget_status_counts"] == {"within_budget": 1}
    assert canonical["aggregate_budget_state"]["event_cap_usd"] == 18.0
    assert canonical["aggregate_budget_state"]["remaining_notional_usd"] == 7.5
    assert canonical["aggregate_budget_state"]["profit_ratcheted_requested_addon_usd"] == 8.0
    assert canonical["aggregate_budget_state"]["profit_ratcheted_addon_usd"] == 8.0
    assert canonical["aggregate_budget_state"]["profit_ratcheted_blocked_addon_usd"] == 0.0
    assert canonical["aggregate_budget_state"]["risk_promotion_allowed_count"] == 1
    assert canonical["aggregate_budget_state"]["risk_promotion_blocked_count"] == 0
    assert canonical["aggregate_budget_state"]["risk_promotion_source_confidence_counts"] == {
        "account_confirmed": 1
    }
    assert canonical["aggregate_db_context_inclusion_status_counts"] == {"included": 1}
    assert canonical["aggregate_db_context_stale_blocker_counts"] == {}
    assert canonical["aggregate_quarter_revision_review_count"] == 1
    assert canonical["aggregate_quarter_revision_review_status_counts"] == {"revision_recorded": 1}
    assert canonical["items"][0]["history_count"] == 2
    assert canonical["items"][0]["source"] == "janus_live_event_state_latest_json"
    assert canonical["items"][0]["readback_endpoints"] == {
        "latest": "/v1/events/nba-sas-min-2026-05-10/live-state?session_date=2026-05-10",
        "history": "/v1/events/nba-sas-min-2026-05-10/live-state/history?session_date=2026-05-10",
        "worker_restart_readiness": (
            "/v1/events/nba-sas-min-2026-05-10/worker-restart-readiness?session_date=2026-05-10"
        ),
    }
    assert canonical["items"][0]["selected_game_id"] == "nba-game-1"
    assert canonical["items"][0]["scoreboard_resolution_state"]["schema_version"] == (
        "postgame_scoreboard_resolution_readback_v1"
    )
    assert canonical["items"][0]["scoreboard_resolution_state"]["source_schema"] == (
        "scoreboard_resolution_diagnostics_v1"
    )
    assert canonical["items"][0]["scoreboard_resolution_state"]["searched_date_windows"] == [
        "2026-05-10",
        "2026-05-11",
    ]
    assert canonical["items"][0]["scoreboard_resolution_state"]["candidate_count"] == 2
    assert canonical["items"][0]["scoreboard_resolution_state"]["candidate_game_ids"] == [
        "nba-game-1",
        "nba-game-2",
    ]
    assert canonical["items"][0]["signal_aggregation_state"]["nonblocking_local_blocker_count"] == 1
    assert canonical["items"][0]["signal_aggregation_state"]["expected_next_action_counts"] == {
        "block_selected_order_candidate": 1,
        "preserve_unrelated_order_candidates": 1,
    }
    assert canonical["items"][0]["signal_aggregation_state"]["minimum_order_policy_summary"]["policy_count"] == 2
    assert canonical["items"][0]["db_stat_context_state"]["schema_version"] == (
        "janus_live_event_state_db_stat_context_state_v1"
    )
    assert canonical["items"][0]["db_stat_context_state"]["llm_context_inclusion_status"] == "included"
    assert canonical["items"][0]["db_stat_context_state"]["included_source_count"] == 2
    assert canonical["items"][0]["db_stat_context_state"]["execution_authority"] is False
    assert canonical["items"][0]["quarter_revision_state"]["schema_version"] == (
        "janus_live_event_state_quarter_revision_state_v1"
    )
    assert canonical["items"][0]["quarter_revision_state"]["schema_contract"]["schema_version"] == (
        "janus_live_event_state_quarter_revision_contract_v1"
    )
    assert canonical["items"][0]["quarter_revision_state"]["quarter_labels"] == ["halftime_q2_end"]
    assert canonical["items"][0]["quarter_revision_state"]["execution_authority"] is False
    assert canonical["items"][0]["budget_state"]["budget_status"] == "within_budget"
    assert canonical["items"][0]["budget_state"]["profit_ratcheted_addon_usd"] == 8.0
    assert canonical["items"][0]["budget_state"]["risk_promotion_evidence"]["source_confidence"] == "account_confirmed"
    assert fetch_calls[0]["account_id"] == "56964015-5935-5035-bdab-b056c9277146"
    assert fetch_calls[0]["event_slug"] == "nba-sas-min-2026-05-10"
    attribution = payload["portfolio_pnl_attribution"]
    assert attribution["status"] == "ready"
    assert attribution["direct_evidence"]["trade_count"] == 3
    trust_summary = attribution["direct_trade_trust_summary"]
    assert trust_summary["schema_version"] == "postgame_direct_trade_trust_summary_v1"
    assert trust_summary["schema_contract"]["schema_version"] == (
        "postgame_direct_trade_trust_summary_contract_v1"
    )
    assert trust_summary["schema_contract"]["source_schema"] == "postgame_direct_event_scope_v1"
    assert "trusted_trade_count" in trust_summary["schema_contract"]["required_sections"]
    assert "untrusted_trade_reason_counts" in trust_summary["schema_contract"]["required_sections"]
    assert trust_summary["status"] == "untrusted_rows_quarantined"
    assert trust_summary["trade_trust_policy"] == "requires_valid_trade_timestamp_for_current_account_evidence"
    assert trust_summary["account_pnl_input_policy"] == "trusted_trades_only"
    assert trust_summary["trusted_trade_count"] == 1
    assert trust_summary["untrusted_trade_count"] == 1
    assert trust_summary["all_observed_trade_count"] == 2
    assert trust_summary["untrusted_trade_reason_counts"] == {
        "direct_trade_timestamp_missing_or_invalid": 1
    }
    assert trust_summary["items"][0]["account_pnl_input_policy"] == "trusted_trades_only"
    assert attribution["items"][0]["direct_event_scope"]["status"] == "scoped"
    assert attribution["items"][0]["direct_event_scope"]["open_order_count"] == 0
    assert attribution["items"][0]["direct_event_scope"]["open_position_count"] == 0
    assert attribution["items"][0]["direct_event_scope"]["trade_count"] == 1
    assert attribution["items"][0]["direct_event_scope"]["trusted_trade_count"] == 1
    assert attribution["items"][0]["direct_event_scope"]["untrusted_trade_count"] == 1
    assert attribution["items"][0]["direct_event_scope"]["all_observed_trade_count"] == 2
    assert attribution["items"][0]["direct_event_scope"]["untrusted_trade_reasons"] == {
        "direct_trade_timestamp_missing_or_invalid": 1
    }
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
    assert realized["items"][0]["market_tape"]["trusted_trade_count"] == 1
    assert realized["items"][0]["market_tape"]["untrusted_trade_count"] == 1
    assert realized["items"][0]["market_tape"]["all_observed_trade_count"] == 2
    assert realized["items"][0]["market_tape"]["account_pnl_input_policy"] == "trusted_trades_only"
    clob_grounding = realized["items"][0]["clob_grounding"]
    assert clob_grounding["status"] == "recorded"
    assert clob_grounding["direct_trade_trust_state"]["schema_version"] == "postgame_direct_trade_trust_state_v1"
    assert clob_grounding["direct_trade_trust_state"]["schema_contract"]["schema_version"] == (
        "postgame_direct_trade_trust_state_contract_v1"
    )
    assert clob_grounding["direct_trade_trust_state"]["schema_contract"]["source_schema"] == (
        "postgame_direct_event_scope_v1"
    )
    assert "market_tape_only_trade_count" in (
        clob_grounding["direct_trade_trust_state"]["schema_contract"]["required_sections"]
    )
    assert clob_grounding["direct_trade_trust_state"]["status"] == "untrusted_rows_quarantined"
    assert clob_grounding["direct_trade_trust_state"]["account_pnl_input_policy"] == "trusted_trades_only"
    assert clob_grounding["direct_trade_trust_state"]["trusted_trade_count"] == 1
    assert clob_grounding["direct_trade_trust_state"]["untrusted_trade_count"] == 1
    assert clob_grounding["direct_trade_trust_state"]["all_observed_trade_count"] == 2
    assert clob_grounding["direct_trade_trust_state"]["untrusted_trade_reasons"] == {
        "direct_trade_timestamp_missing_or_invalid": 1
    }
    assert clob_grounding["direct_trade_trust_state"]["untrusted_trade_ids"] == [
        "timestamp-zero-history-row"
    ]
    assert clob_grounding["direct_event_scope"]["trusted_trade_count"] == 1
    assert clob_grounding["direct_event_scope"]["untrusted_trade_count"] == 1
    assert clob_grounding["direct_event_scope"]["all_observed_trade_count"] == 2
    assert clob_grounding["external_order_ids"] == ["0xorder1"]
    assert clob_grounding["direct_trade_ids"] == ["direct-trade-1"]
    assert clob_grounding["fill_rows"][0]["effective_avg_price"] == 0.95
    ui_comparison = clob_grounding["ui_displayed_price_comparison"]
    assert ui_comparison["status"] == "derived_display_estimates"
    assert ui_comparison["account_pnl_eligible"] is False
    assert ui_comparison["rows"][0]["exact_avg_price"] == 0.95
    assert ui_comparison["rows"][0]["estimated_ui_whole_cent_label"] == "95c"
    assert ui_comparison["rows"][0]["minimum_checks"]["size_meets_exchange_minimum"] is True
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


def test_postgame_canonical_live_event_state_falls_back_to_live_worker_ticks_pytest(
    tmp_path, monkeypatch
) -> None:
    local_root = tmp_path / "local"
    monkeypatch.setenv("JANUS_LOCAL_ROOT", str(local_root))
    event_id = "wnba-lva-gsv-2026-05-31"
    ticks_path = (
        local_root
        / "shared"
        / "artifacts"
        / "live-strategy-worker"
        / "2026-05-31"
        / "ticks.jsonl"
    )
    ticks_path.parent.mkdir(parents=True, exist_ok=True)
    tick_payload = {
        "started_at_utc": "2026-05-31T20:35:00Z",
        "finished_at_utc": "2026-05-31T20:35:02Z",
        "stdout": {
            "events": [
                {
                    "event_id": event_id,
                    "market_state": {
                        "game": {
                            "status": "final",
                            "period": 4,
                            "game_clock": "00:00",
                            "home_team": "Golden State Valkyries",
                            "away_team": "Las Vegas Aces",
                            "home_score": 81,
                            "away_score": 91,
                            "scoreboard_resolution": {
                                "schema_version": "scoreboard_resolution_diagnostics_v1",
                                "status": "resolved",
                                "selected_game_id": "wnba-game-1",
                                "parsed_event_date": "2026-05-31",
                                "parsed_teams": {"away": "lva", "home": "gsv"},
                                "searched_date_windows": ["2026-05-31"],
                                "candidate_games": [
                                    {
                                        "game_id": "wnba-game-1",
                                        "game_date": "2026-05-31",
                                        "away_team": "Las Vegas Aces",
                                        "home_team": "Golden State Valkyries",
                                        "match_status": "selected",
                                    }
                                ],
                            },
                        }
                    },
                    "live_signal_aggregation": {
                        "signal_count": 2,
                        "decision": {
                            "decision_type": "blocked",
                            "selected_signal_ids": [],
                            "suppressed_signal_ids": ["signal-1"],
                            "order_intent_candidates": [],
                            "blocker_artifacts": [
                                {
                                    "reason_code": "game_not_live_no_entry",
                                    "detail": {
                                        "scope": "global_safety",
                                        "candidate_blocking": True,
                                        "expected_next_action": "block_order_candidate_until_game_live",
                                    },
                                }
                            ],
                        },
                        "event_risk_budget": {
                            "event_cap_usd": 20.0,
                            "remaining_notional_usd": 20.0,
                            "budget_status": "within_budget",
                            "profit_ratcheted_requested_addon_usd": 0.0,
                            "profit_ratcheted_addon_usd": 0.0,
                            "profit_ratcheted_blocked_addon_usd": 0.0,
                            "risk_promotion_evidence": {
                                "schema_version": "live_tick_risk_promotion_evidence_v1",
                                "risk_promotion_allowed": False,
                                "source_confidence": "unconfirmed",
                            },
                        },
                    },
                }
            ]
        },
    }
    ticks_path.write_text(json.dumps(tick_payload) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        ops_router,
        "build_event_agent_context",
        lambda requested_event_id, *, day=None: {
            "event_id": requested_event_id,
            "resolved_strategy_plan_event_id": event_id,
            "db_stat_context_trace": {
                "schema_version": "db_stat_context_trace_v1",
                "llm_context_inclusion_status": "included",
                "source_count": 1,
                "included_source_count": 1,
                "stale_source_count": 0,
                "missing_source_count": 0,
                "blockers": [],
                "liveness_blocking": False,
                "db_context_sources": [
                    {
                        "source_id": "wnba_team_stats_snapshot",
                        "status": "ready",
                        "freshness_status": "fresh",
                        "included_in_llm_context": True,
                        "stale": False,
                        "teams": ["Las Vegas Aces", "Golden State Valkyries"],
                        "players": ["A'ja Wilson"],
                        "source_path": (
                            "local/shared/artifacts/team-stats/2026-05-31/"
                            "wnba-lva-gsv-2026-05-31/team_stats.json"
                        ),
                    }
                ],
            },
            "db_context_sources": [
                {
                    "source_id": "wnba_team_stats_snapshot",
                    "status": "ready",
                    "freshness_status": "fresh",
                    "included_in_llm_context": True,
                    "stale": False,
                    "teams": ["Las Vegas Aces", "Golden State Valkyries"],
                    "players": ["A'ja Wilson"],
                    "source_path": (
                        "local/shared/artifacts/team-stats/2026-05-31/"
                        "wnba-lva-gsv-2026-05-31/team_stats.json"
                    ),
                }
            ],
        },
    )
    monkeypatch.setattr(
        ops_router,
        "load_latest_llm_runtime_status",
        lambda *, session_date, event_ids: {
            "status": "recorded",
            "session_date": session_date,
            "items": [
                {
                    "event_id": event_ids[0],
                    "status": "recorded",
                    "quarter_revision_reviews": [
                        {
                            "schema_version": "strategy_plan_quarter_revision_review_v1",
                            "quarter_label": "q1_end",
                            "review_status": "revision_recorded",
                            "reviewed_sleeve_count": 2,
                            "order_endpoint_call_allowed": False,
                            "path": (
                                "local/shared/artifacts/llm-runtime/2026-05-31/"
                                "wnba-lva-gsv-2026-05-31/quarter-review_q1.json"
                            ),
                        }
                    ],
                    "quarter_revision_review_artifacts": [
                        {
                            "path": (
                                "local/shared/artifacts/llm-runtime/2026-05-31/"
                                "wnba-lva-gsv-2026-05-31/quarter-review_q1.json"
                            )
                        }
                    ],
                }
            ],
        },
    )

    canonical = ops_router._build_postgame_canonical_live_event_state_summary(
        day="2026-05-31",
        event_ids=[event_id],
    )

    assert canonical["status"] == "recorded"
    assert canonical["recorded_event_count"] == 1
    assert canonical["missing_event_count"] == 0
    assert canonical["aggregate_source_counts"] == {"live_strategy_worker_tick_history_fallback": 1}
    assert canonical["aggregate_reason_counts"] == {
        "janus_live_event_state_latest_missing_tick_history_used": 1
    }
    assert canonical["aggregate_scoreboard_resolution_status_counts"] == {"resolved": 1}
    assert canonical["aggregate_blocker_scope_counts"] == {"global_safety": 1}
    assert canonical["aggregate_blocker_reason_counts"] == {"game_not_live_no_entry": 1}
    assert canonical["aggregate_expected_next_action_counts"] == {
        "block_order_candidate_until_game_live": 1
    }
    assert canonical["aggregate_signal_count"] == 2
    assert canonical["aggregate_order_intent_candidate_count"] == 0
    assert canonical["aggregate_blocker_count"] == 1
    assert canonical["aggregate_candidate_blocking_blocker_count"] == 1
    assert canonical["aggregate_nonblocking_local_blocker_count"] == 0
    assert canonical["aggregate_budget_state"]["budget_status_counts"] == {"within_budget": 1}
    assert canonical["aggregate_budget_state"]["event_cap_usd"] == 20.0
    assert canonical["aggregate_budget_state"]["remaining_notional_usd"] == 20.0
    assert canonical["aggregate_budget_state"]["risk_promotion_allowed_count"] == 0
    assert canonical["aggregate_budget_state"]["risk_promotion_blocked_count"] == 1
    assert canonical["aggregate_budget_state"]["risk_promotion_source_confidence_counts"] == {
        "unconfirmed": 1
    }
    assert canonical["aggregate_db_context_inclusion_status_counts"] == {"included": 1}
    assert canonical["aggregate_quarter_revision_review_count"] == 1
    assert canonical["aggregate_quarter_revision_review_status_counts"] == {"revision_recorded": 1}
    item = canonical["items"][0]
    assert item["status"] == "recorded"
    assert item["source"] == "live_strategy_worker_tick_history_fallback"
    assert item["source_schema"] == "janus_live_event_state_live_tick_readback_v1"
    assert item["reason"] == "janus_live_event_state_latest_missing_tick_history_used"
    assert item["history_count"] == 0
    assert item["latest_tick_status"] == "recorded"
    assert item["latest_tick_finished_at_utc"] == "2026-05-31T20:35:02Z"
    assert item["selected_game_id"] == "wnba-game-1"
    assert item["scoreboard_resolution_state"]["source_schema"] == "scoreboard_resolution_diagnostics_v1"
    assert item["scoreboard_resolution_state"]["candidate_game_ids"] == ["wnba-game-1"]
    assert item["game_status"] == "final"
    assert item["period"] == 4
    assert item["clock"] == "00:00"
    assert item["db_stat_context_state"]["status"] == "recorded"
    assert item["db_stat_context_state"]["source"] == "agent_context_fallback"
    assert item["db_stat_context_state"]["reason"] == "canonical_live_event_state_db_stat_context_missing"
    assert item["db_stat_context_state"]["llm_context_inclusion_status"] == "included"
    assert item["db_stat_context_state"]["source_count"] == 1
    assert item["db_stat_context_state"]["included_source_count"] == 1
    assert item["db_stat_context_state"]["source_ids"] == ["wnba_team_stats_snapshot"]
    assert item["db_stat_context_state"]["included_source_ids"] == ["wnba_team_stats_snapshot"]
    assert item["db_stat_context_state"]["freshness_status_counts"] == {"fresh": 1}
    assert item["db_stat_context_state"]["teams"] == [
        "Las Vegas Aces",
        "Golden State Valkyries",
    ]
    assert item["db_stat_context_state"]["players"] == ["A'ja Wilson"]
    assert item["db_stat_context_state"]["execution_authority"] is False
    assert item["quarter_revision_state"]["status"] == "recorded"
    assert item["quarter_revision_state"]["schema_contract"]["schema_version"] == (
        "janus_live_event_state_quarter_revision_contract_v1"
    )
    assert item["quarter_revision_state"]["source"] == "llm_runtime_status_fallback"
    assert item["quarter_revision_state"]["reason"] == "canonical_live_event_state_quarter_revision_missing"
    assert item["quarter_revision_state"]["quarter_revision_review_count"] == 1
    assert item["quarter_revision_state"]["quarter_labels"] == ["q1_end"]
    assert item["quarter_revision_state"]["missing_expected_quarter_labels"] == ["halftime_q2_end", "q3_end"]
    assert item["quarter_revision_state"]["review_status_counts"] == {"revision_recorded": 1}
    assert item["quarter_revision_state"]["reviewed_sleeve_count"] == 2
    assert item["quarter_revision_state"]["order_endpoint_call_allowed"] is False
    assert item["execution_authority"] is False


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
                        "items": [
                            {
                                "order_id": "order-1",
                                "external_order_id": "0xorder1",
                                "side": "sell",
                                "outcome_id": "outcome-1",
                                "token_id": "token-1",
                                "fill_evidence_source": "local_and_direct_trades",
                                "effective_fill_size": Decimal("5"),
                                "effective_cashflow_usd": Decimal("4.75"),
                                "effective_fee_usd": Decimal("0"),
                                "direct_fill_size": Decimal("5"),
                                "direct_cashflow_usd": Decimal("4.75"),
                                "direct_fee_usd": Decimal("0"),
                                "direct_trade_ids": ["trade-1"],
                                "direct_local_fill_mismatch": False,
                            }
                        ],
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
    assert realized_item["clob_grounding"]["fill_rows"][0]["effective_avg_price"] == 0.95
    ui_comparison = realized_item["clob_grounding"]["ui_displayed_price_comparison"]
    assert ui_comparison["source_confidence"] == "inferred"
    assert ui_comparison["actual_ui_observation_source_confidence"] == "ui_observed"
    assert ui_comparison["rows"][0]["estimated_ui_one_decimal_cent_label"] == "95c"
    assert ui_comparison["rows"][0]["minimum_checks"]["notional_meets_exchange_buy_minimum"] is True
    assert realized_item["market_tape"]["event_scoped_trade_count"] == 999
    assert realized_item["market_tape"]["account_pnl_eligible"] is False
    assert evaluation["market_tape_policy"]["blocked_uses"] == [
        "account_pnl",
        "realized_return",
        "all_account_performance",
    ]


def test_postgame_portfolio_pnl_can_skip_direct_clob_fetch_pytest(monkeypatch) -> None:
    captured: list[bool] = []

    def fake_direct_context(
        connection,
        *,
        account_id,
        direct_open_order_external_id,
        direct_open_order_count,
        direct_open_position_count,
        include_direct_clob_evidence,
    ):
        captured.append(include_direct_clob_evidence)
        return {
            "direct_open_order_external_ids": [],
            "direct_open_order_count": None,
            "direct_open_position_count": None,
            "direct_trade_rows": [],
            "direct_evidence": {"enabled": include_direct_clob_evidence, "ok": None},
        }

    monkeypatch.setattr(ops_router, "_resolve_order_lifecycle_direct_context", fake_direct_context)
    monkeypatch.setattr(ops_router, "_fetch_order_lifecycle_reconciliation_rows", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        ops_router,
        "build_order_lifecycle_reconciliation_report",
        lambda rows, **kwargs: {"status": "ready", "fill_rows": [], "unresolved_evidence": []},
    )
    monkeypatch.setattr(
        ops_router,
        "build_portfolio_pnl_attribution_report",
        lambda report: {"pnl_attribution_ready": False, "known_cashflow_usd": 0.0},
    )
    payload = OpsCycleRequest(
        account_id="56964015-5935-5035-bdab-b056c9277146",
        include_direct_clob_evidence=False,
    )

    result = ops_router._build_postgame_portfolio_pnl_attribution(
        object(),
        payload,
        event_ids=["wnba-phx-nyl-2026-05-27"],
        day="2026-05-27",
    )

    assert captured == [False]
    assert result["direct_evidence"]["enabled"] is False
    assert result["items"][0]["ok"] is True


def test_postgame_portfolio_pnl_loads_default_account_when_payload_omits_account_id_pytest(monkeypatch) -> None:
    captured_account_ids: list[str] = []

    monkeypatch.setattr(
        ops_router,
        "resolve_trading_account",
        lambda connection, *, account_id=None: {"account_id": "default-account-id"},
    )

    def fake_direct_context(connection, **kwargs):
        captured_account_ids.append(kwargs["account_id"])
        return {
            "direct_open_order_external_ids": [],
            "direct_open_order_count": 0,
            "direct_open_position_count": 0,
            "direct_trade_rows": [],
            "direct_evidence": {"enabled": True, "ok": True},
        }

    monkeypatch.setattr(ops_router, "_resolve_order_lifecycle_direct_context", fake_direct_context)
    monkeypatch.setattr(ops_router, "_fetch_order_lifecycle_reconciliation_rows", lambda *args, **kwargs: [])
    monkeypatch.setattr(ops_router, "_event_scoped_order_lifecycle_direct_context", lambda direct_context, **kwargs: direct_context)
    monkeypatch.setattr(
        ops_router,
        "build_order_lifecycle_reconciliation_report",
        lambda rows, **kwargs: {"status": "ready", "unknown_lifecycle_count": 0},
    )
    monkeypatch.setattr(
        ops_router,
        "build_portfolio_pnl_attribution_report",
        lambda report: {"pnl_attribution_ready": True, "known_cashflow_usd": 0.0},
    )

    result = ops_router._build_postgame_portfolio_pnl_attribution(
        object(),
        OpsCycleRequest(),
        event_ids=["wnba-phx-nyl-2026-05-27"],
        day="2026-05-27",
    )

    assert captured_account_ids == ["default-account-id"]
    assert result["status"] == "ready"
    assert result["account_id"] == "default-account-id"
    assert result["account_resolution"]["status"] == "default_loaded"


def test_postgame_portfolio_pnl_returns_partial_when_one_event_fails_pytest(monkeypatch) -> None:
    monkeypatch.setattr(
        ops_router,
        "_resolve_order_lifecycle_direct_context",
        lambda *args, **kwargs: {
            "direct_open_order_external_ids": [],
            "direct_open_order_count": 0,
            "direct_open_position_count": 0,
            "direct_trade_rows": [],
            "direct_evidence": {"enabled": True, "ok": True},
        },
    )
    monkeypatch.setattr(ops_router, "_event_scoped_order_lifecycle_direct_context", lambda direct_context, **kwargs: direct_context)

    def fake_fetch_rows(connection, **kwargs):
        if kwargs["event_slug"] == "event-fail":
            raise TimeoutError("event reconciliation timed out")
        return [{"order_id": "order-ok"}]

    monkeypatch.setattr(ops_router, "_fetch_order_lifecycle_reconciliation_rows", fake_fetch_rows)
    monkeypatch.setattr(
        ops_router,
        "build_order_lifecycle_reconciliation_report",
        lambda rows, **kwargs: {"status": "ready", "unknown_lifecycle_count": 0},
    )
    monkeypatch.setattr(
        ops_router,
        "build_portfolio_pnl_attribution_report",
        lambda report: {"pnl_attribution_ready": True, "known_cashflow_usd": 0.25},
    )

    result = ops_router._build_postgame_portfolio_pnl_attribution(
        object(),
        OpsCycleRequest(account_id="account-1"),
        event_ids=["event-ok", "event-fail"],
        day="2026-05-27",
    )

    assert result["status"] == "partial"
    assert result["ready_event_count"] == 1
    assert result["error_count"] == 1
    assert result["items"][1]["ok"] is False
    assert result["items"][1]["unresolved_evidence"][0]["reason"] == "event_pnl_attribution_failed"


def test_account_activity_return_report_uses_closed_pnl_not_gross_turnover_pytest() -> None:
    report = ops_router._build_account_activity_return_report_from_rows(
        event_ids=["wnba-atl-por-2026-05-29"],
        activity_rows=[
            {
                "eventSlug": "wnba-atl-por-2026-05-29",
                "type": "TRADE",
                "side": "BUY",
                "outcome": "Atlanta Dream",
                "size": 5,
                "price": 0.95,
                "usdcSize": 4.75,
                "timestamp": 1,
                "transactionHash": "0xbuy-one",
            },
            {
                "eventSlug": "wnba-atl-por-2026-05-29",
                "type": "TRADE",
                "side": "BUY",
                "outcome": "Atlanta Dream",
                "size": 5,
                "price": 0.95,
                "usdcSize": 4.75,
                "timestamp": 1,
                "transactionHash": "0xbuy-one",
            },
            {
                "eventSlug": "wnba-atl-por-2026-05-29",
                "type": "TRADE",
                "side": "SELL",
                "outcome": "Atlanta Dream",
                "size": 5,
                "price": 0.96,
                "usdcSize": 4.8,
                "timestamp": 2,
                "transactionHash": "0xsell-one",
            },
            {
                "eventSlug": "wnba-atl-por-2026-05-29",
                "type": "TRADE",
                "side": "BUY",
                "outcome": "Atlanta Dream",
                "size": 1.63,
                "price": 0.96,
                "usdcSize": 1.5667,
                "timestamp": 3,
                "transactionHash": "0xbuy-two",
            },
            {
                "eventSlug": "wnba-atl-por-2026-05-29",
                "type": "REDEEM",
                "size": 1.63,
                "usdcSize": 1.63,
                "timestamp": 4,
                "transactionHash": "0xredeem",
            },
        ],
        closed_position_rows=[
            {
                "eventSlug": "wnba-atl-por-2026-05-29",
                "asset": "atl-token",
                "conditionId": "condition",
                "outcome": "Atlanta Dream",
                "avgPrice": 0.96,
                "totalBought": 6.63,
                "realizedPnl": 1.4152,
                "timestamp": 5,
            }
        ],
    )

    item = report["items"][0]
    assert item["gross_buy_turnover_usd"] == 6.3167
    assert item["gross_buy_turnover_is_invested_capital"] is False
    assert item["peak_cash_at_risk_usd"] == 4.75
    assert item["actual_pnl_usd"] == 1.4152
    assert item["actual_pnl_source"] == "polymarket_closed_positions"
    assert item["accounting_notes"][0]["reason"] == "closed_position_pnl_overrides_activity_cashflow"
    assert report["total_actual_pnl_usd"] == 1.4152


def test_realized_live_item_prefers_account_return_over_lifecycle_cashflow_pytest() -> None:
    item = ops_router._build_postgame_realized_live_item(
        {
            "event_id": "wnba-atl-por-2026-05-29",
            "event_slug": "wnba-atl-por-2026-05-29",
            "pnl_attribution": {
                "pnl_attribution_ready": True,
                "known_cashflow_usd": -9.53,
                "buckets": [],
            },
            "account_return": {
                "source": "polymarket_account_activity_return_v1",
                "source_confidence": "account_confirmed",
                "actual_pnl_usd": 1.4152,
                "actual_pnl_source": "polymarket_closed_positions",
                "peak_cash_at_risk_usd": 16.98,
                "gross_buy_turnover_usd": 57.78,
                "gross_buy_turnover_is_invested_capital": False,
            },
            "direct_event_scope": {"scoped": True, "status": "scoped"},
            "reconciliation": {},
        }
    )

    assert item["status"] == "ready"
    assert item["account_pnl"]["actual_pnl_usd"] == 1.4152
    assert item["account_pnl"]["known_cashflow_usd"] == -9.53
    assert item["account_pnl"]["lifecycle_cashflow_is_actual_return"] is False
    assert item["account_pnl"]["gross_buy_turnover_is_invested_capital"] is False


def test_postgame_ui_display_comparison_preserves_subcent_clob_truth_pytest() -> None:
    comparison = ops_router._build_ui_displayed_price_comparison(
        [
            {
                "order_id": "order-subcent",
                "side": "buy",
                "token_id": "token-okc",
                "effective_fill_size": 202,
                "effective_cashflow_usd": -1.212,
                "effective_avg_price": 0.006,
            }
        ]
    )

    row = comparison["rows"][0]
    assert comparison["account_pnl_eligible"] is False
    assert row["exact_avg_price"] == 0.006
    assert row["exact_cents"] == 0.6
    assert row["estimated_ui_whole_cent_label"] == "1c"
    assert row["estimated_ui_one_decimal_cent_label"] == "0.6c"
    assert row["rounding_delta_to_whole_cent"] == 0.4
    assert row["minimum_checks"]["effective_notional_usd"] == 1.212


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
                            "order_intent_candidates": [
                                {
                                    "event_id": "wnba-conn-gsv-2026-05-25",
                                    "market_token_id": "token-gsv",
                                    "outcome_id": "outcome-gsv",
                                    "max_price": 0.95,
                                    "requested_shares": 5,
                                    "side": "Golden State Valkyries",
                                    "signal_type": "buy",
                                    "sleeve_id": "gsv-grid",
                                    "sleeve_role": "grid_scalp",
                                    "strategy_family": "price_stability_micro_grid",
                                    "strategy_id": "gsv-grid",
                                    "supporting_signal_ids": ["signal-1"],
                                }
                            ],
                        }
                    },
                    "orderbook_results": {
                        "outcome-gsv": {
                            "best_ask": 0.88,
                            "best_bid": 0.86,
                            "captured_at": "2026-05-25T22:00:00Z",
                        }
                    },
                    "market_state": {
                        "token_states": {
                            "token-gsv": {
                                "best_ask": 0.90,
                                "best_bid": 0.94,
                                "captured_at_utc": "2026-05-25T22:00:01Z",
                            }
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
    assert summary["fill_simulation"]["status"] == "simulated_from_clob_tape"
    assert summary["fill_simulation"]["unique_candidate_count"] == 1
    assert summary["fill_simulation"]["simulated_fill_count"] == 1
    assert summary["fill_simulation"]["simulated_cashflow_usd"] == -4.4
    assert summary["fill_simulation"]["simulated_mark_value_usd"] == 4.7
    assert summary["fill_simulation"]["simulated_pnl_usd"] == 0.3
    assert summary["sleeves"]["gsv-grid"]["fill_simulation"]["simulated_pnl_usd"] == 0.3
    assert summary["missed_window_analysis"]["status"] == "estimated"
    assert summary["missed_window_analysis"]["estimated_missed_value_usd"] == 0.3
    assert summary["missed_window_analysis"]["rows"][0]["reason"] == "missed_exit_extrema_after_candidate"
    assert summary["missed_window_analysis"]["rows"][0]["account_pnl_eligible"] is False


def test_postgame_replay_modes_compute_leave_one_out_marginal_value_pytest() -> None:
    replay_inputs = {
        "game-1": {
            "status": "recorded",
            "tick_count": 3,
            "intent_count": 2,
            "executed_order_count": 0,
            "order_intent_candidate_count": 2,
            "decision_type_counts": {"candidate": 3},
            "blocker_reason_counts": {},
            "fill_simulation": {
                "status": "simulated_from_clob_tape",
                "candidate_count": 2,
                "unique_candidate_count": 2,
                "simulated_fill_count": 2,
                "simulated_cashflow_usd": -9.0,
                "simulated_mark_value_usd": 9.6,
                "simulated_pnl_usd": 0.6,
            },
            "missed_window_analysis": {
                "status": "estimated",
                "estimated_missed_value_usd": 0.4,
                "rows": [{"sleeve_id": "grid", "estimated_missed_value_usd": 0.4}],
            },
            "sleeves": {
                "grid": {
                    "sleeve_id": "grid",
                    "strategy_id": "grid",
                    "sleeve_role": "grid_scalp",
                    "tick_count": 3,
                    "intent_count": 2,
                    "blocker_count": 0,
                    "fill_simulation": {
                        "status": "simulated_from_clob_tape",
                        "candidate_count": 2,
                        "unique_candidate_count": 2,
                        "simulated_fill_count": 2,
                        "simulated_cashflow_usd": -9.0,
                        "simulated_mark_value_usd": 9.6,
                        "simulated_pnl_usd": 0.6,
                    },
                }
            },
        }
    }

    aggregate = ops_router._build_postgame_replay_mode(
        "aggregate_replay",
        "aggregate",
        replay_inputs=replay_inputs,
    )
    leave_one_out = ops_router._build_postgame_replay_mode(
        "leave_one_out",
        "leave one out",
        replay_inputs=replay_inputs,
    )

    assert aggregate["aggregate"]["simulation_status"] == "simulated_from_clob_tape"
    assert aggregate["aggregate"]["simulated_pnl_usd"] == 0.6
    assert aggregate["aggregate"]["missed_window_estimated_value_usd"] == 0.4
    row = leave_one_out["leave_one_out_rows"][0]
    assert row["aggregate_simulated_pnl_usd"] == 0.6
    assert row["aggregate_without_excluded_simulated_pnl_usd"] == 0.0
    assert row["marginal_value_usd"] == 0.6
    assert row["marginal_value_source_confidence"] == "clob_market_tape"


def test_postgame_evaluation_builds_p1_p2_strategy_learning_sections_pytest(monkeypatch) -> None:
    replay_summary = {
        "status": "recorded",
        "source_confidence": "runtime_artifact",
        "tick_count": 3,
        "intent_count": 2,
        "executed_order_count": 0,
        "order_intent_candidate_count": 2,
        "decision_type_counts": {"candidate": 2, "blocked": 1},
        "blocker_reason_counts": {
            "price_band_not_met": 4,
            "scoreboard_freshness_required": 1,
        },
        "fill_simulation": {
            "status": "simulated_from_clob_tape",
            "candidate_count": 2,
            "unique_candidate_count": 2,
            "simulated_fill_count": 2,
            "simulated_cashflow_usd": -9.0,
            "simulated_mark_value_usd": 9.6,
            "simulated_pnl_usd": 0.6,
        },
        "missed_window_analysis": {
            "status": "estimated",
            "estimated_missed_value_usd": 0.4,
            "rows": [{"sleeve_id": "grid", "estimated_missed_value_usd": 0.4}],
            "blocked_sleeve_rows": [
                {
                    "sleeve_id": "blocked-grid",
                    "blocker_count": 4,
                    "blocker_reasons": ["price_band_not_met"],
                    "recorded_range_cents": 2.0,
                }
            ],
        },
        "sleeves": {
            "grid": {
                "sleeve_id": "grid",
                "strategy_id": "grid",
                "sleeve_role": "grid_scalp",
                "sleeve_side": "Spurs",
                "tick_count": 3,
                "intent_count": 2,
                "blocker_count": 0,
                "blocker_reasons": [],
                "fill_simulation": {
                    "status": "simulated_from_clob_tape",
                    "candidate_count": 2,
                    "unique_candidate_count": 2,
                    "simulated_fill_count": 2,
                    "simulated_cashflow_usd": -9.0,
                    "simulated_mark_value_usd": 9.6,
                    "simulated_pnl_usd": 0.6,
                },
            },
            "blocked-grid": {
                "sleeve_id": "blocked-grid",
                "strategy_id": "blocked-grid",
                "sleeve_role": "ultra_low_rebound",
                "sleeve_side": "Thunder",
                "tick_count": 3,
                "intent_count": 0,
                "blocker_count": 4,
                "blocker_reasons": ["price_band_not_met"],
                "fill_simulation": {
                    "status": "no_candidates",
                    "candidate_count": 0,
                    "unique_candidate_count": 0,
                    "simulated_fill_count": 0,
                    "simulated_cashflow_usd": 0.0,
                    "simulated_mark_value_usd": 0.0,
                    "simulated_pnl_usd": 0.0,
                },
            },
        },
    }
    monkeypatch.setattr(
        ops_router,
        "_read_postgame_replay_tick_stream_summary",
        lambda *, day, event_id: replay_summary,
    )
    monkeypatch.setattr(
        ops_router,
        "_read_postgame_replay_tick_stream_summaries",
        lambda *, day, event_ids: {event_id: replay_summary for event_id in event_ids},
    )

    evaluation = ops_router._build_postgame_evaluation(
        day="2026-05-26",
        reviewed_event_ids=["nba-sas-okc-2026-05-26"],
        strategy_plan_gate={"status": "ready", "ready": True},
        postgame_live_evidence={"status": "live_evidence_present"},
        portfolio_pnl_attribution={
            "status": "partial",
            "items": [
                {
                    "ok": True,
                    "event_id": "nba-sas-okc-2026-05-26",
                    "event_slug": "nba-sas-okc-2026-05-26",
                    "direct_event_scope": {"status": "scoped", "scoped": True, "trade_count": 4},
                    "reconciliation": {
                        "order_count": 4,
                        "linked_trade_count": 4,
                        "unknown_lifecycle_count": 1,
                        "unresolved_lifecycle_reason_counts": {
                            "direct_flat_open_order_missing_terminal_status": 1
                        },
                    },
                    "pnl_attribution": {
                        "known_cashflow_usd": Decimal("-6.0"),
                        "unknown_lifecycle_count": 1,
                        "direct_final_flat": False,
                        "residual_status": "open_or_unresolved",
                        "pnl_attribution_ready": False,
                    },
                }
            ],
        },
    )

    comparison = evaluation["mode_comparison"]
    assert comparison["schema_version"] == "postgame_mode_comparison_v1"
    assert [row["mode"] for row in comparison["rows"]] == [
        "realized_live",
        "sleeve_isolated",
        "aggregate_replay",
        "leave_one_out",
    ]
    assert comparison["rows"][0]["known_cashflow_usd"] == -6.0
    assert evaluation["realized_live"]["items"][0]["unresolved_evidence"][0]["unresolved_lifecycle_reasons"] == [
        "direct_flat_open_order_missing_terminal_status"
    ]
    assert evaluation["realized_live"]["items"][0]["lifecycle_summary"]["unresolved_lifecycle_reason_counts"] == {
        "direct_flat_open_order_missing_terminal_status": 1
    }
    assert comparison["rows"][1]["simulated_pnl_usd"] == 0.6
    scoreboard = evaluation["sleeve_scoreboard"]
    assert scoreboard["schema_version"] == "postgame_sleeve_scoreboard_v1"
    assert scoreboard["positive_simulated_sleeve_count"] == 1
    assert scoreboard["blocked_sleeve_count"] == 1
    assert scoreboard["rows"][0]["sleeve_id"] == "grid"
    assert scoreboard["rows"][0]["leave_one_out_marginal_value_usd"] == 0.6
    why_no_trade = evaluation["why_no_trade"]
    assert why_no_trade["aggregate_blocker_scope_counts"]["local_sleeve"] == 4
    assert why_no_trade["aggregate_blocker_scope_counts"]["global_gate"] == 1
    assert why_no_trade["events"][0]["sleeves"][0]["sleeve_id"] == "blocked-grid"
    llm_usage = evaluation["llm_usage_analysis"]
    assert llm_usage["schema_version"] == "postgame_llm_usage_analysis_v1"
    assert any(gap["expected_model"] == "gpt-5.4-nano" for gap in llm_usage["model_role_gaps"])
    blocker_review = evaluation["blocker_efficacy_review"]
    assert blocker_review["schema_version"] == "postgame_blocker_efficacy_review_v1"
    assert any(row["reason"] == "price_band_not_met" for row in blocker_review["rows"])
    promotion = evaluation["strategy_promotion_review"]
    assert promotion["schema_version"] == "postgame_strategy_promotion_review_v1"
    assert promotion["status"] == "blocked_by_unresolved_realized_evidence"
    assert promotion["automation_ready"] is False
    assert promotion["rows"][0]["eligible_for_promotion"] is False
    assert "realized_lifecycle_or_direct_evidence_unresolved" in promotion["rows"][0]["review_reasons"]


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
