from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import pandas as pd

from app.modules.agentic.contracts import LLMRuntimeTrace, StrategyPlan
from codex_tool import run_live_strategy_tick as live_tick


def test_scoreboard_state_treats_frozen_clock_as_stale_pytest() -> None:
    state = live_tick._scoreboard_state(
        "Cavaliers",
        game={
            "home_team_name": "Pistons",
            "home_team_slug": "DET",
            "away_team_name": "Cavaliers",
            "away_team_slug": "CLE",
            "game_status": 2,
            "period": 1,
            "game_clock": "PT02M55.00S",
        },
        live_state={
            "latest_snapshot": {
                "captured_at": "2026-05-14T00:35:32+00:00",
                "period": 1,
                "game_clock": "PT02M55.00S",
                "home_score": 19,
                "away_score": 22,
            },
            "live_snapshots": [
                {
                    "captured_at": "2026-05-14T00:35:32+00:00",
                    "period": 1,
                    "game_clock": "PT02M55.00S",
                    "home_score": 19,
                    "away_score": 22,
                },
                {
                    "captured_at": "2026-05-14T00:34:52+00:00",
                    "period": 1,
                    "game_clock": "PT02M55.00S",
                    "home_score": 19,
                    "away_score": 21,
                },
                {
                    "captured_at": "2026-05-14T00:34:26+00:00",
                    "period": 1,
                    "game_clock": "PT02M55.00S",
                    "home_score": 19,
                    "away_score": 21,
                },
                {
                    "captured_at": "2026-05-14T00:33:30+00:00",
                    "period": 1,
                    "game_clock": "PT03M04.00S",
                    "home_score": 17,
                    "away_score": 21,
                },
            ],
        },
    )

    assert state["score_gap"] == 3
    assert state["scoreboard_stall_seconds"] == 66.0
    assert state["scoreboard_age_seconds"] >= 66.0


def test_scoreboard_state_does_not_mark_timeout_pause_as_stale_pytest() -> None:
    now = datetime.now(timezone.utc)
    captured_at = now.isoformat()
    one_minute_ago = (now - timedelta(seconds=60)).isoformat()
    two_minutes_ago = (now - timedelta(seconds=120)).isoformat()
    timeout_at = (now - timedelta(seconds=248)).isoformat()

    state = live_tick._scoreboard_state(
        "Cavaliers",
        game={
            "home_team_name": "Pistons",
            "home_team_slug": "DET",
            "away_team_name": "Cavaliers",
            "away_team_slug": "CLE",
            "game_status": 2,
            "period": 3,
            "game_clock": "PT06M55.00S",
        },
        live_state={
            "latest_snapshot": {
                "captured_at": captured_at,
                "period": 3,
                "clock": "PT06M55.00S",
                "home_score": 66,
                "away_score": 64,
            },
            "live_snapshots": [
                {
                    "captured_at": captured_at,
                    "period": 3,
                    "clock": "PT06M55.00S",
                    "home_score": 66,
                    "away_score": 64,
                },
                {
                    "captured_at": one_minute_ago,
                    "period": 3,
                    "clock": "PT06M55.00S",
                    "home_score": 66,
                    "away_score": 64,
                },
                {
                    "captured_at": two_minutes_ago,
                    "period": 3,
                    "clock": "PT06M55.00S",
                    "home_score": 66,
                    "away_score": 64,
                },
            ],
            "recent_play_by_play": [
                {
                    "period": 3,
                    "clock": "PT06M55.00S",
                    "description": "DET Timeout",
                    "payload_json": {
                        "actionType": "timeout",
                        "description": "DET Timeout",
                        "timeActual": timeout_at,
                    },
                }
            ],
        },
    )

    assert state["score_gap"] == -2
    assert state["scoreboard_stall_seconds"] == 120.0
    assert state["scoreboard_stall_suppressed_reason"] == "dead_ball_timeout"
    assert state["scoreboard_age_seconds"] < 10


@pytest.mark.parametrize(
    ("outcome_label", "home_name", "away_name", "home_slug", "away_slug", "home_score", "away_score", "expected_gap"),
    [
        ("Atlanta", "Phoenix Mercury", "Atlanta Dream", "PHX", "ATL", 80, 82, 2),
        ("Dallas", "Dallas Wings", "New York Liberty", "DAL", "NYL", 91, 76, 15),
        ("Seattle", "Washington Mystics", "Seattle Storm", "WAS", "SEA", 14, 7, -7),
        ("Seattle", "Washington Mystics", "Seattle Storm", "WAS", "SEA", 24, 26, 2),
    ],
)
def test_scoreboard_state_derives_wnba_score_gap_from_team_aliases_pytest(
    outcome_label: str,
    home_name: str,
    away_name: str,
    home_slug: str,
    away_slug: str,
    home_score: int,
    away_score: int,
    expected_gap: int,
) -> None:
    state = live_tick._scoreboard_state(
        outcome_label,
        game={
            "home_team_name": home_name,
            "home_team_slug": home_slug,
            "away_team_name": away_name,
            "away_team_slug": away_slug,
            "game_status": 2,
            "period": 1,
            "game_clock": "PT06M56.00S",
        },
        live_state={
            "latest_snapshot": {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "period": 1,
                "game_clock": "PT06M56.00S",
                "home_score": home_score,
                "away_score": away_score,
            },
        },
    )

    assert state["score_gap"] == expected_gap
    assert state["scoreboard_age_seconds"] < 10


def test_outcome_label_from_strategy_ignores_trade_side_pytest() -> None:
    assert live_tick._outcome_label_from_strategy({"side": "buy"}, {"side": "buy"}) is None
    assert (
        live_tick._outcome_label_from_strategy(
            {"side": "buy", "outcome_label": "Cavaliers"},
            {"side": "buy"},
        )
        == "Cavaliers"
    )


def test_wnba_slug_aliases_cover_current_live_window_teams_pytest() -> None:
    assert live_tick._wnba_slug_alias("CON") == "con"
    assert live_tick._wnba_slug_alias("CONN") == "con"
    assert live_tick._wnba_slug_alias("Connecticut Sun") == "con"
    assert live_tick._wnba_slug_alias("Atlanta Dream") == "atl"
    assert live_tick._wnba_slug_alias("Phoenix Mercury") == "phx"
    assert live_tick._wnba_slug_alias("New York Liberty") == "nyl"
    assert live_tick._wnba_slug_alias("Seattle Storm") == "sea"
    assert live_tick._wnba_slug_alias("LV") == "lva"
    assert live_tick._wnba_slug_alias("Las Vegas Aces") == "lva"
    assert live_tick._wnba_slug_alias("LA") == "las"
    assert live_tick._wnba_slug_alias("Los Angeles Sparks") == "las"
    assert live_tick._wnba_slug_alias("SEA") == "sea"
    assert live_tick._wnba_slug_alias("WAS") == "wsh"
    assert live_tick._wnba_slug_alias("Washington Mystics") == "wsh"
    assert live_tick._wnba_slug_alias("DAL") == "dal"


def test_resolve_wnba_game_matches_connecticut_conn_slug_to_con_tricode_pytest(monkeypatch) -> None:
    from app.data.nodes.wnba.live import live_stats

    monkeypatch.setattr(
        live_stats,
        "fetch_todays_scoreboard_df",
        lambda: pd.DataFrame(
            [
                {
                    "game_id": "1022600048",
                    "game_status": 2,
                    "game_status_text": "Q1 6:34",
                    "period": 1,
                    "game_clock": "PT06M34.00S",
                    "game_date": "2026-05-25",
                    "home_team_tricode": "GSV",
                    "away_team_tricode": "CON",
                    "home_score": 12,
                    "away_score": 5,
                }
            ]
        ),
    )

    result = live_tick._resolve_wnba_game_from_cdn(
        "wnba-conn-gsv-2026-05-25",
        ("conn", "gsv", "2026-05-25"),
        session_date="2026-05-25",
    )

    assert result is not None
    assert result["resolved"] is True
    assert result["game_id"] == "1022600048"
    assert result["resolution_source"] == "wnba_cdn_scoreboard_event_slug"


def test_resolve_wnba_game_falls_back_to_schedule_for_las_vegas_slug_pytest(monkeypatch) -> None:
    from app.data.nodes.wnba.live import live_stats
    from app.data.nodes.wnba.schedule import season_schedule

    monkeypatch.setattr(live_stats, "fetch_todays_scoreboard_df", lambda: pd.DataFrame())
    monkeypatch.setattr(
        season_schedule,
        "fetch_season_schedule_df",
        lambda season="2026": pd.DataFrame(
            [
                {
                    "game_id": "1022600054",
                    "game_status": 1,
                    "game_status_text": "8:00 pm ET",
                    "period": None,
                    "game_clock": "",
                    "game_date": "2026-05-28",
                    "home_team_tricode": "DAL",
                    "away_team_tricode": "LVA",
                    "home_score": 0,
                    "away_score": 0,
                }
            ]
        ),
    )

    result = live_tick._resolve_wnba_game_from_cdn(
        "wnba-las-dal-2026-05-28",
        ("las", "dal", "2026-05-28"),
        session_date="2026-05-28",
    )

    assert result is not None
    assert result["resolved"] is True
    assert result["game_id"] == "1022600054"
    assert result["resolution_source"] == "wnba_cdn_schedule_event_slug"


def test_sync_and_fetch_live_state_routes_wnba_to_wnba_endpoints_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"api_root": api_root, "method": method, "path": path, "payload": payload, "kwargs": kwargs})
        if path == "/v1/wnba/games/1022600044/live":
            return {"game_id": "1022600044", "latest_snapshot": {"period": 2, "home_score": 32, "away_score": 24}}
        return {"ok": True}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._sync_and_fetch_live_state(
        api_root="http://test",
        game={"league": "wnba", "game_id": "1022600044"},
    )

    assert result["game_id"] == "1022600044"
    assert [call["path"] for call in calls] == [
        "/v1/sync/wnba/live/1022600044",
        "/v1/wnba/games/1022600044/live",
    ]
    assert calls[0]["payload"] == {
        "include_live_snapshots": True,
        "include_boxscore": True,
        "include_play_by_play": True,
    }


def test_sync_and_fetch_live_state_keeps_nba_on_nba_endpoints_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"api_root": api_root, "method": method, "path": path, "payload": payload, "kwargs": kwargs})
        if path == "/v1/nba/games/0042500314/live":
            return {"game_id": "0042500314", "latest_snapshot": {"period": 1, "home_score": 0, "away_score": 0}}
        return {"ok": True}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._sync_and_fetch_live_state(
        api_root="http://test",
        game={"league": "nba", "game_id": "0042500314"},
    )

    assert result["game_id"] == "0042500314"
    assert [call["path"] for call in calls] == [
        "/v1/sync/nba/live/0042500314",
        "/v1/nba/games/0042500314/live",
    ]


def test_run_event_tick_includes_normalized_live_snapshot_pytest(monkeypatch) -> None:
    evaluate_payloads: list[dict[str, Any]] = []

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        if path == "/v1/events/wnba-wsh-sea-2026-05-24/agent-context":
            return {
                "current_strategy_plan": {
                    "market_id": "market-1",
                    "active_strategies": [
                        {
                            "strategy_id": "seattle-grid",
                            "family": "price_stability_micro_grid",
                            "entry_rules": {
                                "outcome_id": "seattle",
                                "token_id": "token-sea",
                                "outcome_label": "Seattle",
                                "side": "buy",
                            },
                        }
                    ],
                }
            }
        if path == "/v1/events/wnba-wsh-sea-2026-05-24/strategy-plan/evaluate":
            evaluate_payloads.append(payload or {})
            return {"ok": True, "sleeve_states": []}
        raise AssertionError(path)

    class FakeTrace:
        trigger_count = 0
        triggers: list[Any] = []

        def model_dump(self, mode: str = "python") -> dict[str, Any]:
            return {"trace_id": "trace-1", "event_id": "wnba-wsh-sea-2026-05-24", "mode": mode}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)
    monkeypatch.setattr(
        live_tick,
        "_resolve_game",
        lambda *args, **kwargs: {
            "event_id": "wnba-wsh-sea-2026-05-24",
            "league": "wnba",
            "game_id": "1022600046",
            "game_status_text": "Live",
            "period": 1,
            "game_clock": "PT06M56.00S",
            "home_team_name": "Seattle Storm",
            "home_team_slug": "SEA",
            "home_score": 7,
            "away_team_name": "Washington Mystics",
            "away_team_slug": "WSH",
            "away_score": 14,
            "updated_at": "2026-05-25T06:40:00Z",
        },
    )
    monkeypatch.setattr(
        live_tick,
        "_sync_and_fetch_live_state",
        lambda *args, **kwargs: {
            "latest_snapshot": {
                "captured_at": "2026-05-25T06:40:05Z",
                "period": 1,
                "clock": "PT06M55.00S",
                "home_score": 7,
                "away_score": 14,
            },
            "recent_play_by_play": [{"timeActual": "2026-05-25T06:40:04Z"}],
        },
    )
    monkeypatch.setattr(
        live_tick,
        "_fetch_direct_orderbook_latest",
        lambda **kwargs: {
            "ok": True,
            "levels_count": 2,
            "snapshot": {
                "token_id": "token-sea",
                "best_bid": 0.31,
                "best_ask": 0.33,
                "spread": 0.02,
                "bid_depth": 5.0,
                "ask_depth": 6.0,
                "captured_at": "2026-05-25T06:40:06Z",
            },
        },
    )
    monkeypatch.setattr(live_tick, "_persist_orderbook_watch_ticks", lambda **kwargs: {"ok": True, "tick_count": 1})
    monkeypatch.setattr(live_tick, "_mirror_direct_open_orders_for_tick", lambda **kwargs: {"ok": True, "status": "applied"})
    monkeypatch.setattr(
        live_tick,
        "_pending_intent_summary",
        lambda **kwargs: {
            "ok": True,
            "source": "pytest",
            "pending_intent_count": 0,
            "pending_buy_intent_count": 0,
            "orders": [],
            "event_start_expired_order_count": 0,
            "event_start_expired_orders": [],
        },
    )
    monkeypatch.setattr(
        live_tick,
        "_known_portfolio_order_external_ids",
        lambda **kwargs: {"ok": True, "external_order_ids": [], "known_order_count": 0},
    )
    monkeypatch.setattr(live_tick, "_persist_direct_trade_watch_observations", lambda **kwargs: {"ok": True, "trade_count": 0})
    monkeypatch.setattr(
        live_tick,
        "_auto_protect_direct_positions",
        lambda **kwargs: {"candidate_strategy_plan_submission": {"submitted": False}, "submitted_orders": []},
    )
    monkeypatch.setattr(live_tick, "build_llm_runtime_trace", lambda **kwargs: FakeTrace())
    monkeypatch.setattr(
        live_tick,
        "_llm_runtime_status_summary",
        lambda *args, **kwargs: {"status": "detected_only", "trigger_count": 0},
    )

    result = live_tick._run_event_tick(
        api_root="http://test",
        session_date="2026-05-25",
        event_id="wnba-wsh-sea-2026-05-24",
        account_id="account-1",
        source="pytest",
        execute=False,
        live_money=False,
        max_intents=0,
        orderbook_sample_count=1,
        orderbook_sample_interval_sec=0.0,
        integrity_ready=False,
        integrity_snapshot={},
        strategy_plan_gate={"status": "ready", "current_plan_count": 1, "current_plans": [{"market_id": "market-1"}]},
        live_strategy_worker_status={"status": "stopped", "worker_thread_alive": False},
        evidence_paths=["local/shared/artifacts/ops/live-monitor.json"],
        min_size=5.0,
        min_buy_notional_usd=1.0,
        share_precision=3,
        auto_protect_manual_positions=False,
        manual_target_delta_cents=5.0,
    )

    snapshot = result["normalized_live_snapshot"]
    assert snapshot["schema_version"] == "normalized_live_snapshot_v1"
    assert snapshot["event_id"] == "wnba-wsh-sea-2026-05-24"
    assert snapshot["league"] == "wnba"
    assert snapshot["game"]["period"] == 1
    assert snapshot["game"]["clock"] == "PT06M55.00S"
    assert snapshot["clob"][0]["best_bid"] == 0.31
    assert snapshot["runtime"]["worker_status"] == "stopped"
    assert snapshot["execution_boundary"] == "evidence_only"
    assert snapshot == evaluate_payloads[0]["market_state"]["normalized_live_snapshot"]
    paired = result["market_state"]["paired_microcycle"]
    assert paired["schema_version"] == "sports_live_paired_microcycle_evidence_v1"
    assert paired["execution_boundary"] == "evidence_only"
    assert paired["cycle_count"] == 1
    assert paired["cycles"][0]["sleeve_id"] == "seattle-grid"
    assert paired["cycles"][0]["status"] == "awaiting_buy"
    assert paired == evaluate_payloads[0]["market_state"]["paired_microcycle"]
    assert paired == result["portfolio_state"]["paired_microcycle"]


def test_resolve_game_uses_catalog_link_for_uuid_event_pytest(monkeypatch) -> None:
    event_uuid = "8da3c71c-1926-5f97-8473-7c742c7156b8"
    calls: list[dict[str, Any]] = []

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"method": method, "path": path, "payload": payload, "kwargs": kwargs})
        if path == "/v1/nba/games":
            return {
                "items": [
                    {
                        "game_id": "0042500311",
                        "game_date": "2026-05-18",
                        "home_team_slug": "OKC",
                        "away_team_slug": "SAS",
                    }
                ]
            }
        if path == f"/v1/events/{event_uuid}":
            return {"event_id": event_uuid, "canonical_slug": "nba-sas-okc-2026-05-18"}
        if path == "/v1/events":
            assert kwargs["query"] == {"canonical_slug": "nba-sas-okc-2026-05-18", "limit": 1}
            return {"items": [{"event_id": event_uuid, "linked_nba_game_id": "0042500311"}]}
        raise AssertionError(path)

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._resolve_game("http://test", event_uuid, "2026-05-18")

    assert result["resolved"] is True
    assert result["game_id"] == "0042500311"
    assert result["resolution_source"] == "catalog_linked_nba_game_id"
    assert [call["path"] for call in calls] == ["/v1/nba/games", f"/v1/events/{event_uuid}", "/v1/events"]


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
    assert result["covered_positions"] == [
        {"token_id": "token-sas", "position_size": 5.0, "open_sell_size": 5.0, "coverage_status": "covered"}
    ]
    assert result["position_reactions"] == []
    assert result["revision_requests"] == []
    assert result["candidate_strategy_plan_required"] is False
    assert "candidate_strategy_plan" not in result
    assert calls == []


def test_auto_protect_direct_position_treats_slug_alias_dust_target_as_covered_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"path": path, "payload": payload})
        return {"ok": True}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._auto_protect_direct_positions(
        api_root="http://test",
        account_id="account-1",
        event_id="8da3c71c-1926-5f97-8473-7c742c7156b8",
        plan={
            "market_id": "market-1",
            "event_id": "8da3c71c-1926-5f97-8473-7c742c7156b8",
            "context_summary": {"event_slug": "nba-sas-okc-2026-05-18"},
            "active_strategies": [
                {
                    "strategy_id": "sas-live-post-order-monitor",
                    "family": "operator_order_management",
                    "entry_rules": {"token_id": "token-sas", "outcome_id": "outcome-sas"},
                    "shadow_flags": {"live_order_external_id": "0xbuy"},
                }
            ],
            "portfolio_reconciliation": [{"action": "monitor_live_target_sell_order", "external_order_id": "0xsell"}],
        },
        direct_clob={
            "open_positions": {
                "positions": [
                    {
                        "asset": "token-sas",
                        "avg_price": 0.3099,
                        "event_slug": "nba-sas-okc-2026-05-18",
                        "outcome": "Spurs",
                        "size": 32.258,
                    }
                ]
            },
            "open_orders": {
                "orders": [
                    {
                        "id": "0xsell",
                        "token_id": "token-sas",
                        "event_slug": "nba-sas-okc-2026-05-18",
                        "side": "SELL",
                        "status": "LIVE",
                        "size": 32.25,
                        "price": 0.50,
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
        known_external_order_ids={"0xsell", "0xbuy"},
    )

    assert calls == []
    assert result["order_reactions"] == []
    assert result["position_reactions"] == []
    assert result["revision_requests"] == []
    assert result["candidate_strategy_plan_required"] is False
    assert result["covered_positions"] == [
        {
            "token_id": "token-sas",
            "position_size": 32.258,
            "open_sell_size": 32.25,
            "uncovered_size": pytest.approx(0.008),
            "coverage_status": "covered_except_dust_below_exchange_minimum",
            "minimum_size": 5.0,
        }
    ]


def test_auto_protect_direct_position_replaces_event_start_expired_target_without_llm_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"path": path, "payload": payload})
        return {"ok": True}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._auto_protect_direct_positions(
        api_root="http://test",
        account_id="account-1",
        event_id="8da3c71c-1926-5f97-8473-7c742c7156b8",
        plan={
            "market_id": "market-1",
            "event_id": "8da3c71c-1926-5f97-8473-7c742c7156b8",
            "context_summary": {
                "event_slug": "nba-sas-okc-2026-05-18",
                "game_start_utc": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            },
            "active_strategies": [
                {
                    "strategy_id": "sas-live-post-order-monitor",
                    "family": "operator_order_management",
                    "entry_rules": {"token_id": "token-sas", "outcome_id": "outcome-sas"},
                    "exit_rules": {"target_policy": "micro_grid_scaled", "target_cents": 5},
                }
            ],
            "portfolio_reconciliation": [
                {"action": "monitor_live_target_sell_order", "external_order_id": "0xsell", "token_id": "token-sas"}
            ],
        },
        direct_clob={
            "open_positions": {
                "positions": [
                    {
                        "asset": "token-sas",
                        "avg_price": 0.3099,
                        "event_slug": "nba-sas-okc-2026-05-18",
                        "outcome": "Spurs",
                        "size": 32.258,
                    }
                ]
            },
            "open_orders": {"orders": []},
        },
        execute=False,
        live_money=False,
        integrity_ready=True,
        source="pytest",
        min_size=5.0,
        target_delta_cents=5.0,
        enabled=True,
        known_external_order_ids={"0xsell"},
    )

    assert calls == []
    assert result["recommended_orders"][0]["side"] == "sell"
    assert result["recommended_orders"][0]["size"] == 32.258
    assert result["position_reactions"][0]["action"] == "replace_event_start_expired_target_order"
    assert result["position_reactions"][0]["skip_llm_revision_trigger"] is True
    assert result["position_reactions"][0]["revision_request"]["reason"] == "event_start_target_order_expired"
    assert result["position_reactions"][0]["revision_request"]["missing_plan_target_order_ids"] == ["0xsell"]
    assert result["revision_requests"] == []
    assert result["candidate_strategy_plan_required"] is False


def test_auto_protect_direct_position_targets_strategy_owned_live_entry_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"path": path, "payload": payload})
        if path == "/v1/portfolio/orders":
            return {"ok": True, "status": "submitted", "external_order_id": "0xtarget"}
        if path == "/v1/operator/interventions/reconcile":
            return {"ok": True, "status": "recorded"}
        return {"ok": True}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._auto_protect_direct_positions(
        api_root="http://test",
        account_id="account-1",
        event_id="8da3c71c-1926-5f97-8473-7c742c7156b8",
        plan={
            "market_id": "market-1",
            "event_id": "8da3c71c-1926-5f97-8473-7c742c7156b8",
            "context_summary": {"event_slug": "nba-sas-okc-2026-05-18"},
            "active_strategies": [
                {
                    "strategy_id": "sas-q4-live-test",
                    "family": "price_stability_micro_grid",
                    "entry_rules": {"token_id": "token-sas", "outcome_id": "outcome-sas", "size": 5.0},
                    "exit_rules": {
                        "target_policy": "micro_grid_scaled",
                        "target_return_fraction": 0.08,
                        "minimum_target_cents": 3,
                    },
                }
            ],
        },
        direct_clob={
            "open_positions": {
                "positions": [
                    {
                        "asset": "token-sas",
                        "avg_price": 0.78,
                        "event_slug": "nba-sas-okc-2026-05-18",
                        "outcome": "Spurs",
                        "size": 10.0,
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
    assert result["submitted_orders"] == [{"ok": True, "status": "submitted", "external_order_id": "0xtarget"}]
    assert result["intervention_records"] == [{"ok": True, "status": "recorded"}]
    assert len(order_calls) == 1
    assert len(intervention_calls) == 1
    order_payload = order_calls[0]["payload"]
    assert order_payload["side"] == "sell"
    assert order_payload["limit_price"] == 0.8424
    assert order_payload["size"] == 5.0
    assert order_payload["metadata_json"]["reaction_type"] == "strategy_plan_target"
    assert order_payload["metadata_json"]["target_size"] == 5.0
    assert result["position_reactions"][0]["action"] == "place_strategy_plan_target_order"
    assert result["position_reactions"][0]["revision_request"]["reason"] == "strategy_plan_target_order"
    assert result["position_reactions"][0]["skip_llm_revision_trigger"] is True
    assert result["candidate_strategy_plan_required"] is False


def test_auto_protect_direct_position_targets_excess_manual_size_when_flagged_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"path": path, "payload": payload})
        if path == "/v1/portfolio/orders":
            return {"ok": True, "status": "submitted", "external_order_id": "0xmanualtarget"}
        if path == "/v1/operator/interventions/reconcile":
            return {"ok": True, "status": "recorded"}
        return {"ok": True}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._auto_protect_direct_positions(
        api_root="http://test",
        account_id="account-1",
        event_id="nba-okc-sas-2026-05-24",
        plan={
            "market_id": "market-1",
            "event_id": "nba-okc-sas-2026-05-24",
            "active_strategies": [
                {
                    "strategy_id": "okc-weighted-exit-5c",
                    "family": "operator_weighted_position_exit",
                    "side": "Thunder",
                    "entry_rules": {
                        "token_id": "token-okc",
                        "outcome_id": "outcome-okc",
                        "size": 5.0,
                    },
                    "exit_rules": {
                        "target_required": True,
                        "target_price": 0.05,
                        "cover_excess_uncovered_position": True,
                    },
                }
            ],
        },
        direct_clob={
            "open_positions": {
                "positions": [
                    {
                        "asset": "token-okc",
                        "avg_price": 0.0407,
                        "event_slug": "nba-okc-sas-2026-05-24",
                        "outcome": "Thunder",
                        "size": 176.6666,
                    }
                ]
            },
            "open_orders": {
                "orders": [
                    {"asset": "token-okc", "side": "sell", "status": "open", "size": 5.0, "price": 0.24},
                    {"asset": "token-okc", "side": "sell", "status": "open", "size": 5.0, "price": 0.28},
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

    order_calls = [call for call in calls if call["path"] == "/v1/portfolio/orders"]
    assert len(order_calls) == 1
    order_payload = order_calls[0]["payload"]
    assert order_payload["side"] == "sell"
    assert order_payload["limit_price"] == 0.05
    assert order_payload["size"] == 166.6666
    assert order_payload["metadata_json"]["target_size"] == 166.6666
    assert result["recommended_orders"][0]["size"] == 166.6666
    assert result["submitted_orders"] == [{"ok": True, "status": "submitted", "external_order_id": "0xmanualtarget"}]


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
        "opposite_side_hedge_or_continuation",
        "add_down_same_side_micro_grid",
        "marketable_stop_or_reduce_only_if_virtual_dead_or_garbage_time",
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


def test_event_tick_submits_monitor_plan_when_current_plan_missing_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"method": method, "path": path, "payload": payload, "kwargs": kwargs})
        if path == "/v1/events/nba-sas-okc-2026-05-24/agent-context":
            return {"ok": True, "current_strategy_plan": None, "direct_open_order_count": 0, "direct_open_position_count": 0}
        if path == "/v1/events/nba-sas-okc-2026-05-24/strategy-plan":
            return {"ok": True, "status": "stored", "strategy_count": len(payload["active_strategies"])}
        if path == "/v1/nba/games":
            return {"ok": True, "items": []}
        if path == "/v1/portfolio/orders":
            return {"ok": True, "items": []}
        if path == "/v1/events/nba-sas-okc-2026-05-24/strategy-plan/evaluate":
            assert payload["market_state"]["sampled_outcomes"] == []
            assert payload["market_state"]["game"]["reason"] == "game_not_found"
            assert payload["market_state"]["pregame_prior"]["status"] == "missing"
            assert payload["market_state"]["pregame_prior"]["reason_codes"] == ["optional_prior_missing"]
            assert payload["market_state"]["pregame_prior"]["liveness_blocking"] is False
            assert payload["market_state"]["pregame_prior"]["live_disabled"] is False
            return {
                "ok": True,
                "intent_count": 0,
                "blocked_count": 1,
                "sleeve_states": [{"status": "blocked", "reason": "monitor_only_fallback"}],
            }
        return {"ok": True}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._run_event_tick(
        api_root="http://test",
        session_date="2026-05-24",
        event_id="nba-sas-okc-2026-05-24",
        account_id="account-1",
        source="pytest",
        execute=False,
        live_money=False,
        max_intents=2,
        orderbook_sample_count=1,
        orderbook_sample_interval_sec=0.0,
        integrity_ready=True,
        integrity_snapshot={"direct_clob": {"open_orders": {"orders": []}, "open_positions": {"positions": []}}},
        min_size=5.0,
        min_buy_notional_usd=1.0,
        share_precision=3,
        auto_protect_manual_positions=True,
        manual_target_delta_cents=5.0,
        submit_candidate_strategy_plan=True,
    )

    plan_submit_index = next(i for i, call in enumerate(calls) if call["path"] == "/v1/events/nba-sas-okc-2026-05-24/strategy-plan")
    evaluate_index = next(i for i, call in enumerate(calls) if call["path"] == "/v1/events/nba-sas-okc-2026-05-24/strategy-plan/evaluate")
    submitted_plan = calls[plan_submit_index]["payload"]
    StrategyPlan.model_validate(submitted_plan)
    assert plan_submit_index < evaluate_index
    assert submitted_plan["plan_owner"] == "system"
    assert submitted_plan["context_summary"]["degraded_missing_pregame_plan"] is True
    assert submitted_plan["active_strategies"][0]["family"] == "degraded_missing_plan_live_monitor"
    assert submitted_plan["active_strategies"][0]["max_positions"] == 0
    assert submitted_plan["active_strategies"][0]["entry_rules"]["entry_disabled"] is True
    assert submitted_plan["active_strategies"][0]["shadow_flags"]["must_not_place_orders"] is True
    assert result["operator_reaction"]["missing_current_strategy_plan_fallback"] is True
    assert result["operator_reaction"]["candidate_strategy_plan_submission"]["submitted"] is True
    assert result["degraded_missing_plan_fallback"]["candidate_strategy_plan_submission"]["submitted"] is True
    assert result["ok"] is True


def test_event_tick_missing_plan_still_blocks_without_candidate_submit_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"method": method, "path": path, "payload": payload, "kwargs": kwargs})
        if path == "/v1/events/nba-sas-okc-2026-05-24/agent-context":
            return {"ok": True, "current_strategy_plan": None, "direct_open_order_count": 0, "direct_open_position_count": 0}
        return {"ok": True}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._run_event_tick(
        api_root="http://test",
        session_date="2026-05-24",
        event_id="nba-sas-okc-2026-05-24",
        account_id="account-1",
        source="pytest",
        execute=False,
        live_money=False,
        max_intents=2,
        orderbook_sample_count=1,
        orderbook_sample_interval_sec=0.0,
        integrity_ready=True,
        integrity_snapshot={},
        min_size=5.0,
        min_buy_notional_usd=1.0,
        share_precision=3,
        auto_protect_manual_positions=True,
        manual_target_delta_cents=5.0,
        submit_candidate_strategy_plan=False,
    )

    assert result["ok"] is False
    assert result["reason"] == "missing_current_strategy_plan"
    assert result["degraded_missing_plan_fallback"]["candidate_strategy_plan_submission"]["reason"] == "review_flag_required"
    assert [call["path"] for call in calls] == ["/v1/events/nba-sas-okc-2026-05-24/agent-context"]


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
    assert result["portfolio_state"]["current_event_inventory_proof"]["pending_intent_count"] == 1
    assert result["market_state"]["current_event_inventory_proof"]["unresolved_inventory_present"] is True
    assert len(evaluate_calls) == 1
    portfolio_state = evaluate_calls[0]["payload"]["portfolio_state"]
    assert portfolio_state["open_orders"] == 0
    assert portfolio_state["open_positions"] == 0
    assert portfolio_state["pending_intents"] == 1
    assert portfolio_state["current_event_inventory_proof"]["pending_intent_count"] == 1


def test_event_scoped_direct_clob_includes_sibling_outcome_inventory_pytest() -> None:
    scoped = live_tick._event_scoped_direct_clob(
        {
            "open_order_count": 2,
            "open_orders": {
                "orders": [
                    {
                        "id": "0xsibling",
                        "market": "condition-game",
                        "token_id": "token-okc",
                        "side": "BUY",
                        "price": 0.15,
                        "size": 20,
                    },
                    {
                        "id": "0xother",
                        "market": "condition-other",
                        "token_id": "token-other",
                        "side": "BUY",
                        "price": 0.2,
                        "size": 20,
                    },
                ]
            },
            "open_positions": {
                "positions": [
                    {
                        "asset": "token-okc",
                        "condition_id": "condition-game",
                        "event_slug": "nba-sas-okc-2026-05-18",
                        "outcome": "Thunder",
                        "size": 5,
                    },
                    {
                        "asset": "token-other",
                        "condition_id": "condition-other",
                        "event_slug": "other-event",
                        "outcome": "Other",
                        "size": 5,
                    },
                ]
            },
            "current_token_trades": {
                "trades": [
                    {
                        "id": "trade-sas",
                        "asset_id": "token-sas",
                        "market": "condition-game",
                        "side": "BUY",
                        "price": 0.76,
                        "size": 5,
                    }
                ]
            },
        },
        {
            "context_summary": {"event_slug": "nba-sas-okc-2026-05-18"},
            "active_strategies": [{"entry_rules": {"token_id": "token-sas"}}],
        },
    )

    assert scoped["event_open_order_count"] == 1
    assert scoped["event_open_position_count"] == 1
    assert scoped["event_condition_ids"] == ["condition-game"]
    assert scoped["event_slugs"] == ["nba-sas-okc-2026-05-18"]
    assert scoped["event_token_ids"] == ["token-okc", "token-sas"]
    assert scoped["open_orders"]["orders"][0]["id"] == "0xsibling"
    assert scoped["open_positions"]["positions"][0]["asset"] == "token-okc"
    assert scoped["current_token_trade_count"] == 1


def test_event_scoped_direct_clob_uses_runtime_outcome_refs_pytest() -> None:
    scoped = live_tick._event_scoped_direct_clob(
        {
            "open_order_count": 2,
            "open_orders": {
                "orders": [
                    {"id": "0xsas", "market": "condition-game", "token_id": "token-sas", "side": "BUY"},
                    {"id": "0xother", "market": "condition-other", "token_id": "token-other", "side": "BUY"},
                ]
            },
            "open_positions": {"positions": []},
        },
        {"context_summary": {"event_slug": "nba-sas-okc-2026-05-18"}, "active_strategies": []},
        outcome_refs={"outcome-sas": {"token_id": "token-sas"}},
    )

    assert scoped["event_open_order_count"] == 1
    assert scoped["open_orders"]["orders"][0]["id"] == "0xsas"
    assert scoped["event_token_ids"] == ["token-sas"]


def test_mirror_direct_open_orders_for_tick_posts_reviewed_capture_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"api_root": api_root, "method": method, "path": path, "payload": payload, "kwargs": kwargs})
        return {
            "ok": True,
            "status": "applied",
            "direct_open_order_mirror": {
                "direct_order_count": 1,
                "eligible_upsert_count": 1,
                "review_required_count": 0,
            },
            "applied": [{"external_order_id": "0xopen"}],
        }

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._mirror_direct_open_orders_for_tick(
        api_root="http://test",
        account_id="account-1",
        source="pytest-user-order-capture",
    )

    assert result["ok"] is True
    assert result["applied_external_order_ids"] == ["0xopen"]
    assert calls == [
        {
            "api_root": "http://test",
            "method": "POST",
            "path": "/v1/portfolio/orders/direct-open-mirror",
            "payload": None,
            "kwargs": {
                "query": {
                    "account_id": "account-1",
                    "dry_run": "false",
                    "reviewed_by": "codex-live-monitor",
                    "reason": "pytest-user-order-capture pre-classification direct CLOB open-order mirror",
                },
                "timeout": 60,
            },
        }
    ]


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


def test_pending_intent_summary_marks_missing_submitted_order_expired_after_event_start_pytest(monkeypatch) -> None:
    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        assert path == "/v1/portfolio/orders"
        return {
            "ok": True,
            "items": [
                {
                    "order_id": "local-order-1",
                    "external_order_id": "0xsubmitted",
                    "event_slug": "nba-sas-okc-2026-05-18",
                    "market_id": "market-1",
                    "outcome_id": "outcome-sas",
                    "side": "buy",
                    "status": "submitted",
                    "size": 5.0,
                    "limit_price": 0.30,
                    "metadata_json": {"strategy_id": "sas-live"},
                }
            ],
        }

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    summary = live_tick._pending_intent_summary(
        api_root="http://test",
        account_id="account-1",
        event_id="8da3c71c-1926-5f97-8473-7c742c7156b8",
        plan={
            "market_id": "market-1",
            "context_summary": {
                "event_slug": "nba-sas-okc-2026-05-18",
                "game_start_utc": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            },
        },
        direct_clob={"open_orders": {"orders": []}, "current_token_trades": {"trades": []}},
    )

    assert summary["pending_intent_count"] == 0
    assert summary["pending_buy_intent_count"] == 0
    assert summary["orders"] == []
    assert summary["event_start_expired_order_count"] == 1
    assert summary["event_start_expired_orders"][0]["external_order_id"] == "0xsubmitted"
    assert summary["event_start_expired_orders"][0]["reason"] == "direct_clob_missing_after_event_start"


def test_pending_intent_summary_expires_local_pending_without_external_after_event_start_pytest(monkeypatch) -> None:
    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        assert path == "/v1/portfolio/orders"
        return {
            "ok": True,
            "items": [
                {
                    "order_id": "local-order-2",
                    "external_order_id": None,
                    "event_slug": "nba-sas-okc-2026-05-18",
                    "market_id": "market-1",
                    "outcome_id": "outcome-sas",
                    "side": "buy",
                    "status": "submitted",
                    "size": 5.0,
                    "limit_price": 0.30,
                    "metadata_json": {"strategy_id": "sas-live"},
                }
            ],
        }

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    summary = live_tick._pending_intent_summary(
        api_root="http://test",
        account_id="account-1",
        event_id="8da3c71c-1926-5f97-8473-7c742c7156b8",
        plan={
            "market_id": "market-1",
            "context_summary": {
                "event_slug": "nba-sas-okc-2026-05-18",
                "game_start_utc": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            },
        },
        direct_clob={"open_orders": {"orders": []}, "current_token_trades": {"trades": []}},
    )

    assert summary["pending_intent_count"] == 0
    assert summary["event_start_expired_order_count"] == 1
    assert summary["event_start_expired_orders"][0]["external_order_id"] is None
    assert summary["event_start_expired_orders"][0]["reason"] == "local_pending_without_external_after_event_start"


def test_pending_intent_summary_expires_after_game_start_without_plan_start_pytest(monkeypatch) -> None:
    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        assert path == "/v1/portfolio/orders"
        return {
            "ok": True,
            "items": [
                {
                    "order_id": "local-order-3",
                    "external_order_id": "0xsubmitted",
                    "event_slug": "nba-sas-okc-2026-05-18",
                    "market_id": "market-1",
                    "outcome_id": "outcome-sas",
                    "side": "buy",
                    "status": "submitted",
                    "size": 5.0,
                    "limit_price": 0.30,
                    "metadata_json": {"strategy_id": "sas-live"},
                }
            ],
        }

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    summary = live_tick._pending_intent_summary(
        api_root="http://test",
        account_id="account-1",
        event_id="8da3c71c-1926-5f97-8473-7c742c7156b8",
        plan={
            "market_id": "market-1",
            "context_summary": {
                "event_slug": "nba-sas-okc-2026-05-18",
            },
        },
        direct_clob={"open_orders": {"orders": []}, "current_token_trades": {"trades": []}},
        game={"game_status": 2, "game_status_text": "Q2"},
    )

    assert summary["pending_intent_count"] == 0
    assert summary["pending_buy_intent_count"] == 0
    assert summary["orders"] == []
    assert summary["event_start_expired_order_count"] == 1
    assert summary["event_start_expired_orders"][0]["external_order_id"] == "0xsubmitted"
    assert summary["event_start_expired_orders"][0]["event_start_utc"]
    assert summary["event_start_expired_orders"][0]["reason"] == "direct_clob_missing_after_event_start"


def test_known_portfolio_order_ids_include_current_strategy_plan_ids_pytest(monkeypatch) -> None:
    def fake_api_json(api_root: str, method: str, path: str, **kwargs):
        assert path == "/v1/portfolio/orders"
        return {"ok": True, "items": []}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    summary = live_tick._known_portfolio_order_external_ids(
        api_root="http://test",
        account_id="account-1",
        event_id="8da3c71c-1926-5f97-8473-7c742c7156b8",
        plan={
            "market_id": "market-1",
            "event_id": "8da3c71c-1926-5f97-8473-7c742c7156b8",
            "context_summary": {"event_slug": "nba-sas-okc-2026-05-18"},
            "active_strategies": [{"shadow_flags": {"live_order_external_id": "0xbuy"}}],
            "portfolio_reconciliation": [{"external_order_id": "0xsell"}],
        },
    )

    assert summary["ok"] is True
    assert summary["source"] == "/v1/portfolio/orders+current_strategy_plan"
    assert summary["external_order_ids"] == ["0xbuy", "0xsell"]
    assert summary["known_order_count"] == 2


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


def test_position_target_price_rounds_low_price_targets_up_to_cent_tick_pytest() -> None:
    target = live_tick._position_target_price_from_plan(
        plan={
            "active_strategies": [
                {
                    "strategy_id": "cle-low-position-management",
                    "family": "position_management_only",
                    "entry_rules": {"token_id": "token-cle"},
                    "exit_rules": {
                        "target_policy": "micro_grid_scaled",
                        "min_target_cents": 1,
                    },
                }
            ]
        },
        token_id="token-cle",
        outcome_id="outcome-cle",
        avg_price=0.0529,
        default_target_delta_cents=5.0,
    )

    assert target["target_price"] == 0.07
    assert target["target_delta_cents"] == pytest.approx(1.71)


def test_position_target_price_allows_subcent_target_tick_when_strategy_requests_it_pytest() -> None:
    target = live_tick._position_target_price_from_plan(
        plan={
            "active_strategies": [
                {
                    "strategy_id": "okc-q4-subpenny-hype-bounce",
                    "family": "ultra_low_underdog_decimal_grid",
                    "entry_rules": {"token_id": "token-okc"},
                    "exit_rules": {
                        "target_policy": "micro_grid_scaled",
                        "min_target_cents": 0.3,
                        "target_return_fraction": 0.50,
                        "min_target_price": 0.001,
                        "target_tick_size": 0.001,
                    },
                }
            ]
        },
        token_id="token-okc",
        outcome_id="outcome-okc",
        avg_price=0.004,
        default_target_delta_cents=5.0,
    )

    assert target["target_price"] == 0.007
    assert target["target_delta_cents"] == pytest.approx(0.3)


def test_position_target_price_can_use_current_price_for_fresh_uncovered_lot_pytest() -> None:
    target = live_tick._position_target_price_from_plan(
        plan={
            "active_strategies": [
                {
                    "strategy_id": "okc-q4-subpenny-hype-bounce",
                    "family": "ultra_low_underdog_decimal_grid",
                    "entry_rules": {"token_id": "token-okc"},
                    "exit_rules": {
                        "target_policy": "micro_grid_scaled",
                        "target_basis": "current_price",
                        "min_target_cents": 0.3,
                        "target_return_fraction": 0.50,
                        "min_target_price": 0.001,
                        "target_tick_size": 0.001,
                    },
                }
            ]
        },
        token_id="token-okc",
        outcome_id="outcome-okc",
        avg_price=0.0065,
        default_target_delta_cents=5.0,
        outcome_state={"price": 0.004},
    )

    assert target["target_price"] == 0.007
    assert target["target_delta_cents"] == pytest.approx(0.05)
    assert target["target_basis_price"] == 0.004


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
            },
            {
                "strategy_id": "cle-favorite-monitor-v1",
                "side": "Cavaliers",
                "sleeve_id": "cle-q1-favorite",
                "sleeve_group": "cle",
                "sleeve_role": "standard_entry",
                "entry_rules": {
                    "outcome_id": "outcome-cle",
                    "token_id": "token-cle",
                    "side": "buy",
                    "price": 0.67,
                    "size": 5,
                },
            },
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
        if path == "/v1/outcomes/outcome-cle/orderbook/latest":
            return {
                "ok": True,
                "snapshot": {
                    "best_bid": 0.66,
                    "best_ask": 0.67,
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
                    },
                    {
                        "sleeve_id": "cle-q1-favorite",
                        "sleeve_group": "cle",
                        "sleeve_role": "standard_entry",
                        "strategy_id": "cle-favorite-monitor-v1",
                        "status": "blocked",
                        "intent_count": 0,
                        "blocker_count": 1,
                        "blocker_reasons": ["score_gap_outside_range"],
                    },
                ],
            }
        return {"ok": True}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)
    persisted_decisions: list[dict[str, Any]] = []

    def fake_write_live_signal_aggregation_decision(decision, **kwargs):
        persisted_decisions.append({"decision": decision.model_dump(mode="json"), "kwargs": kwargs})
        return {
            "status": "stored",
            "path": "local/shared/artifacts/live-signal-aggregation/pytest.json",
        }

    monkeypatch.setattr(live_tick, "write_live_signal_aggregation_decision", fake_write_live_signal_aggregation_decision)

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
        persist_live_signal_aggregation=True,
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
    aggregation = result["live_signal_aggregation"]
    assert aggregation["schema_version"] == "live_worker_aggregation_evidence_v1"
    assert aggregation["signal_count"] == 2
    assert aggregation["sleeve_trigger_binding"]["schema_version"] == "sleeve_trigger_binding_evidence_v1"
    assert aggregation["sleeve_trigger_binding"]["strategy_state_binding_count"] == 2
    assert aggregation["decision"]["decision_type"] == "order_intent_candidate"
    assert aggregation["decision"]["order_intent_candidates"][0]["side"] == "Pistons"
    assert aggregation["decision"]["order_intent_candidates"][0]["sleeve_id"] == "det-q1-underdog"
    assert aggregation["decision"]["order_intent_candidates"][0]["trigger_type"] == "strategy_plan_sleeve_state"
    assert aggregation["decision"]["blocker_artifacts"][0]["detail"]["scope"] == "local_sleeve"
    assert result["market_state"]["sleeve_trigger_binding"]["binding_count"] == 2
    assert aggregation["event_risk_budget"]["event_cap_usd"] == 10.0
    assert aggregation["live_game_context"]["schema_version"] == "live_game_context_evidence_v1"
    assert "game_scenario" in aggregation["live_game_context"]
    assert "ml_confidence_by_sleeve" in aggregation["live_game_context"]
    assert "dynamic_risk_state" in aggregation["live_game_context"]
    assert aggregation["persistence"]["status"] == "stored"
    assert persisted_decisions[0]["kwargs"]["day"] == "2026-05-11"
    portfolio_state = evaluate_calls[0]["payload"]["portfolio_state"]
    assert portfolio_state["open_orders"] == 0
    assert portfolio_state["open_positions"] == 0
    assert portfolio_state["direct_clob_global_open_orders"] == 1


def test_live_signal_aggregation_budget_uses_dynamic_risk_state_pytest() -> None:
    aggregation = live_tick._build_live_signal_aggregation_evidence(
        event_id="wnba-phx-nyl-2026-05-27",
        session_date="2026-05-27",
        source="pytest",
        plan={"active_strategies": []},
        evaluation={},
        market_state={
            "live_game_context": {
                "schema_version": "live_game_context_evidence_v1",
                "dynamic_risk_state": {
                    "realized_event_pnl": 8.0,
                    "realized_day_pnl": 0.0,
                    "open_unrealized_pnl": -1.0,
                },
            }
        },
        portfolio_state={},
        direct_clob={},
        min_size=5.0,
        min_buy_notional_usd=1.0,
        max_buy_notional_usd=10.0,
        persist=False,
    )

    budget = aggregation["event_risk_budget"]
    assert budget["base_event_cap_usd"] == 10.0
    assert budget["realized_event_pnl_usd"] == 8.0
    assert budget["unresolved_loss_exposure_usd"] == 1.0
    assert budget["profit_ratcheted_addon_usd"] == 2.8
    assert budget["event_cap_usd"] == 12.8


def test_event_tick_documents_resolved_residual_without_open_exposure_pytest(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    plan = {
        "market_id": "market-sas-okc",
        "context_summary": {"event_slug": "nba-sas-okc-2026-05-18"},
        "active_strategies": [
            {
                "strategy_id": "thunder-postgame-monitor",
                "side": "Thunder",
                "entry_rules": {
                    "outcome_id": "outcome-thunder",
                    "token_id": "token-thunder",
                    "side": "buy",
                    "price": 0.02,
                    "size": 5,
                },
            }
        ],
    }

    def fake_api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs):
        calls.append({"method": method, "path": path, "payload": payload, "kwargs": kwargs})
        if path == "/v1/events/nba-sas-okc-2026-05-18/agent-context":
            return {
                "ok": True,
                "current_strategy_plan": plan,
                "direct_open_order_count": 0,
                "direct_open_position_count": 1,
            }
        if path == "/v1/nba/games":
            return {"ok": True, "items": []}
        if path == "/v1/sync/polymarket/orderbook":
            return {"ok": True}
        if path == "/v1/outcomes/outcome-thunder/orderbook/latest":
            return {
                "ok": True,
                "snapshot": {
                    "best_bid": None,
                    "best_ask": None,
                    "captured_at": "2026-05-19T10:43:00+00:00",
                },
            }
        if path == "/v1/portfolio/orders":
            return {"ok": True, "items": []}
        if path == "/v1/portfolio/orders/direct-open-mirror":
            return {"ok": True, "status": "applied", "direct_open_order_mirror": {"direct_order_count": 0}}
        if path == "/v1/watchlists/sessions":
            return {"ok": True, "db_persistence": {"watch_session_id": "watch-session-uuid"}}
        if path == "/v1/watchlists/orderbook-ticks":
            return {"ok": True, "tick_count": len(payload["ticks"]), "db_persistence": {"ok": True}}
        if path == "/v1/events/nba-sas-okc-2026-05-18/strategy-plan/evaluate":
            return {"ok": True, "intent_count": 0, "blocked_count": 0, "sleeve_states": []}
        return {"ok": True}

    monkeypatch.setattr(live_tick, "api_json", fake_api_json)

    result = live_tick._run_event_tick(
        api_root="http://test",
        session_date="2026-05-18",
        event_id="nba-sas-okc-2026-05-18",
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
                            "asset": "token-thunder",
                            "condition_id": "condition-sas-okc",
                            "event_slug": "nba-sas-okc-2026-05-18",
                            "outcome": "Thunder",
                            "size": "338.4702",
                            "avg_price": "0.0236",
                            "current_value": "0",
                            "settlement_residual": {
                                "resolved_market": {
                                    "resolved": True,
                                    "condition_id": "condition-sas-okc",
                                    "market_slug": "nba-sas-okc-2026-05-18",
                                    "payouts": {"token-thunder": "0"},
                                },
                                "issue_link": "https://github.com/LucaCGN/janus_cortex/issues/58",
                                "post_redeem_recheck_plan": "post-redeem direct account recheck before clearing",
                            },
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
        call for call in calls if call["path"] == "/v1/events/nba-sas-okc-2026-05-18/strategy-plan/evaluate"
    ]
    portfolio_state = result["portfolio_state"]
    proof = portfolio_state["current_event_inventory_proof"]
    assert portfolio_state["open_positions"] == 0
    assert portfolio_state["raw_event_open_position_count"] == 1
    assert portfolio_state["documented_residual_position_count"] == 1
    assert portfolio_state["blocked_residual_classification_count"] == 0
    assert proof["open_position_count"] == 0
    assert proof["active_open_position_count"] == 0
    assert proof["raw_open_position_count"] == 1
    assert proof["documented_residual_position_count"] == 1
    assert proof["unresolved_inventory_present"] is False
    assert result["operator_reaction"]["position_reactions"] == []
    evaluate_portfolio = evaluate_calls[0]["payload"]["portfolio_state"]
    assert evaluate_portfolio["open_positions"] == 0
    assert evaluate_portfolio["current_event_inventory_proof"]["documented_residual_position_count"] == 1


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
    assert result["market_state"]["llm_runtime_triggers"][0]["selected_model"] == "gpt-5.4-mini"
    assert result["llm_runtime_trace"]["trigger_count"] == 1
    assert result["llm_runtime_trace"]["triggers"][0]["trigger_type"] == "player_status_shock"
    assert result["llm_runtime_trace"]["model_routing"]["selected_model"] == "gpt-5.4-mini"
    assert "frontier_downgraded_operator_minimum_order_policy" in result["llm_runtime_trace"]["model_routing"]["critical_reasons"]
    assert result["llm_runtime_trace"]["status"] == "skipped_unavailable"
    assert result["llm_runtime_trace"]["revision_response"]["status"] == "skipped_unavailable"
    assert result["llm_runtime_trace"]["revision_response"]["skipped_reason"] == "dispatch_disabled"
    assert result["llm_runtime_trace"]["revision_response"]["trace_metadata"]["openai_call_attempted"] is False
    assert result["llm_runtime_trace"]["revision_response"]["trace_metadata"]["order_endpoint_call_allowed"] is False
    assert result["llm_runtime_persistence"]["status"] == "persisted"
    assert result["llm_runtime_status"]["persisted"] is True
    assert result["llm_runtime_status"]["revision_blocker"] == "llm_revision_unavailable"
    assert result["llm_runtime_status"]["live_blocker"] is None
    assert result["llm_runtime_status"]["deterministic_fallback_allowed"] is True
    trace = LLMRuntimeTrace.model_validate(result["llm_runtime_trace"])
    assert (
        live_tick._llm_runtime_live_blocker(trace, plan={"execution_requires_llm_revision": True})
        == "llm_revision_unavailable"
    )
    assert (tmp_path / "llm-runtime" / "2026-05-10").exists()
    assert order_calls == []
    assert evaluate_calls[0]["payload"]["market_state"]["player_status_shock_count"] == 1
    assert evaluate_calls[0]["payload"]["market_state"]["llm_runtime_trigger_count"] == 1
    assert evaluate_calls[0]["payload"]["market_state"]["llm_runtime_status"]["response_status"] == "skipped_unavailable"
    assert evaluate_calls[0]["payload"]["market_state"]["llm_runtime_status"]["revision_blocker"] == "llm_revision_unavailable"
    assert evaluate_calls[0]["payload"]["market_state"]["llm_runtime_status"]["live_blocker"] is None


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
