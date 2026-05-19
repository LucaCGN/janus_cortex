from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from codex_tool import _client as legacy_client
import codex_tool.build_replay_from_watch_session as legacy_replay_cli
import codex_tool.export_event_context as legacy_context_cli
import codex_tool.export_event_review_bundle as legacy_review_cli
import codex_tool.evaluate_strategy_plan as legacy_evaluate_cli
import codex_tool.janus_status as legacy_status_cli
import codex_tool.live_strategy_worker_status as legacy_worker_status_cli
import codex_tool.record_market_trade as legacy_market_trade_cli
import codex_tool.record_orderbook_tick as legacy_orderbook_tick_cli
import codex_tool.reconcile_orders as legacy_reconcile_orders_cli
import codex_tool.reconcile_trades as legacy_reconcile_trades_cli
import codex_tool.run_live_strategy_tick as legacy_live_strategy_tick_cli
import codex_tool.run_live_strategy_worker_tick as legacy_worker_tick_cli
import codex_tool.start_watch_session as legacy_watch_session_cli
import codex_tool.start_live_strategy_worker as legacy_worker_start_cli
import codex_tool.stop_live_strategy_worker as legacy_worker_stop_cli
import codex_tool.submit_pregame_research as legacy_pregame_cli
import codex_tool.submit_strategy_plan as legacy_submit_cli
import codex_tool.adopt_llm_revision as legacy_adopt_cli
import codex_tool.watch_market as legacy_watch_cli
import codex_tools.janus as janus_namespace
from codex_tools.janus import client as janus_client
from codex_tools.janus import events as janus_events
from codex_tools.janus import live_strategy_tick as janus_live_strategy_tick
from codex_tools.janus import ops as janus_ops
from codex_tools.janus import reconciliation as janus_reconciliation
from codex_tools.janus import status as janus_status
from codex_tools.janus import strategy as janus_strategy
from codex_tools.janus import watchlists as janus_watchlists
from codex_tools.janus import worker as janus_worker
from codex_tools.polymarket import (
    GRID_SERVICE_SCHEMA_VERSION,
    POST_REDEEM_RECONCILIATION_SCHEMA_VERSION,
    PREVIEW_SCHEMA_VERSION,
    REDEEM_PREVIEW_SCHEMA_VERSION,
    RESIDUAL_CLASSIFICATION_SCHEMA_VERSION,
    SETTLEMENT_LEDGER_ENTRY_SCHEMA_VERSION,
    SETTLEMENT_LEDGER_WRITE_SCHEMA_VERSION,
    PolymarketExecutionGateSnapshot,
    PolymarketFallbackIntent,
    build_post_redeem_reconciliation,
    build_settlement_ledger_entry,
    build_fallback_preview,
    build_grid_service_preview,
    build_polymarket_safety_gate_snapshot,
    build_redeem_preview,
    classify_resolved_market_residual,
    evaluate_direct_truth_freshness,
    evaluate_kill_switch,
    evaluate_minimum_order_policy,
    evaluate_risk_budget,
    read_account_snapshot,
    build_fallback_decision,
    write_fallback_decision_ledger,
    write_settlement_ledger_prewrite,
)
from codex_tools.polymarket.cli import main as polymarket_cli_main


def _intent(*, dry_run: bool = True) -> PolymarketFallbackIntent:
    return PolymarketFallbackIntent(
        action="open_position",
        account_id="account-1",
        market_slug="test-market",
        token_id="token-yes",
        side="BUY",
        price="0.42",
        size="5",
        reason="unit test",
        dry_run=dry_run,
    )


def _satisfied_gate() -> PolymarketExecutionGateSnapshot:
    return PolymarketExecutionGateSnapshot(
        direct_truth_fresh=True,
        janus_degraded_or_direct_path_selected=True,
        risk_budget_selected=True,
        minimum_order_policy_passed=True,
        target_stop_rebuy_policy_present=True,
        kill_switch_clear=True,
        ledger_idempotency_available=True,
        reconciliation_plan_present=True,
        explicit_execution_approval=True,
        market_token_resolved=True,
        non_authoritative_truth_rejected=True,
    )


class _Creds:
    def __init__(self, *, wallet_address: str = "", private_key: str | None = None) -> None:
        self.wallet_address = wallet_address
        self.funder_address = wallet_address
        self.private_key = private_key


def _target_stop_rebuy_policy() -> dict[str, object]:
    return {
        "policy_name": "existing-position-target-maintenance-v1",
        "target_policy": "place_limit_sell_target_after_review",
        "target_price": 0.6,
        "stop_policy": "review-only stop; no autonomous stop order",
        "rebuy_policy": "no autonomous rebuy",
        "reason": "Unit-test proof for target maintenance.",
    }


def test_janus_client_namespace_preserves_legacy_api_client() -> None:
    assert janus_client.DEFAULT_API_ROOT == legacy_client.DEFAULT_API_ROOT
    assert janus_client.api_json is legacy_client.api_json
    assert janus_client.base_parser is legacy_client.base_parser


def test_janus_ops_namespace_wraps_cycle_endpoints(monkeypatch) -> None:
    calls: list[tuple[str, str, str, dict[str, object]]] = []

    def _api_json(api_root: str, method: str, path: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((api_root, method, path, payload))
        return {"ok": True, "path": path}

    monkeypatch.setattr(janus_ops, "api_json", _api_json)

    response = janus_ops.run_ops_cycle(
        "http://janus.local",
        janus_ops.INTEGRITY_CHECK_PATH,
        {"event_id": "event-1", "dry_run": True},
    )

    assert response == {"ok": True, "path": "/v1/ops/integrity-check"}
    assert calls == [
        (
            "http://janus.local",
            "POST",
            "/v1/ops/integrity-check",
            {"event_id": "event-1", "dry_run": True},
        )
    ]


def test_janus_ops_cycle_parser_preserves_legacy_cycle_args() -> None:
    parser = janus_ops.build_cycle_parser("cycle")
    args = parser.parse_args(["--event-id", "event-1", "--source", "pytest"])

    assert janus_ops.build_cycle_payload(args) == {
        "session_date": None,
        "event_ids": ["event-1"],
        "run_id": None,
        "account_id": None,
        "source": "pytest",
        "notes": None,
        "execute": False,
    }


def test_janus_status_namespace_wraps_status_endpoint(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []

    def _api_json(api_root: str, method: str, path: str) -> dict[str, object]:
        calls.append((api_root, method, path))
        return {"ok": True, "path": path}

    monkeypatch.setattr(janus_status, "api_json", _api_json)

    response = janus_status.get_status("http://janus.local")

    assert response == {"ok": True, "path": "/v1/ops/status"}
    assert calls == [("http://janus.local", "GET", "/v1/ops/status")]


def test_legacy_janus_status_cli_delegates_to_target_namespace() -> None:
    assert legacy_status_cli.STATUS_PATH == janus_status.STATUS_PATH
    assert legacy_status_cli.main_for_status is janus_status.main_for_status


def test_janus_worker_namespace_wraps_status_start_stop_and_tick(monkeypatch) -> None:
    calls: list[tuple[str, str, str, dict[str, object] | None]] = []

    def _api_json(
        api_root: str,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        calls.append((api_root, method, path, payload))
        return {"ok": True, "path": path}

    monkeypatch.setattr(janus_worker, "api_json", _api_json)

    status = janus_worker.get_live_strategy_worker_status("http://janus.local")
    started = janus_worker.start_live_strategy_worker(
        "http://janus.local",
        {"event_ids": ["event-1"], "execute": False},
    )
    stopped = janus_worker.stop_live_strategy_worker("http://janus.local")
    ticked = janus_worker.run_live_strategy_worker_tick(
        "http://janus.local",
        {"event_ids": ["event-1"], "execute": False},
    )

    assert status == {"ok": True, "path": "/v1/ops/live-strategy-worker/status"}
    assert started == {"ok": True, "path": "/v1/ops/live-strategy-worker/start"}
    assert stopped == {"ok": True, "path": "/v1/ops/live-strategy-worker/stop"}
    assert ticked == {"ok": True, "path": "/v1/ops/live-strategy-worker/tick"}
    assert calls == [
        ("http://janus.local", "GET", "/v1/ops/live-strategy-worker/status", None),
        (
            "http://janus.local",
            "POST",
            "/v1/ops/live-strategy-worker/start",
            {"event_ids": ["event-1"], "execute": False},
        ),
        ("http://janus.local", "POST", "/v1/ops/live-strategy-worker/stop", {}),
        (
            "http://janus.local",
            "POST",
            "/v1/ops/live-strategy-worker/tick",
            {"event_ids": ["event-1"], "execute": False},
        ),
    ]


def test_janus_worker_start_parser_preserves_legacy_start_payload_shape() -> None:
    parser = janus_worker.build_live_strategy_worker_start_parser("start")
    args = parser.parse_args(
        [
            "--session-date",
            "2026-05-18",
            "--event-id",
            "event-1",
            "--event-id",
            "event-2",
            "--account-id",
            "account-1",
            "--source",
            "pytest",
            "--interval-seconds",
            "15",
            "--execute",
            "--max-intents",
            "1",
            "--no-auto-protect-manual-positions",
        ]
    )

    assert janus_worker.build_live_strategy_worker_start_payload(args) == {
        "session_date": "2026-05-18",
        "event_ids": ["event-1", "event-2"],
        "account_id": "account-1",
        "source": "pytest",
        "interval_seconds": 15.0,
        "execute": True,
        "live_money": False,
        "enable_llm_dispatch": False,
        "submit_candidate_strategy_plan": False,
        "max_intents": 1,
        "auto_protect_manual_positions": False,
    }


def test_janus_worker_tick_parser_preserves_legacy_tick_payload_shape() -> None:
    parser = janus_worker.build_live_strategy_worker_tick_parser("tick")
    args = parser.parse_args(
        [
            "--session-date",
            "2026-05-18",
            "--event-id",
            "event-1",
            "--account-id",
            "account-1",
            "--source",
            "pytest",
            "--execute",
            "--live-money",
            "--max-intents",
            "1",
            "--timeout-seconds",
            "30",
            "--no-auto-protect-manual-positions",
        ]
    )

    assert janus_worker.build_live_strategy_worker_tick_payload(args) == {
        "session_date": "2026-05-18",
        "event_ids": ["event-1"],
        "account_id": "account-1",
        "source": "pytest",
        "execute": True,
        "live_money": True,
        "enable_llm_dispatch": False,
        "submit_candidate_strategy_plan": False,
        "max_intents": 1,
        "timeout_seconds": 30.0,
        "auto_protect_manual_positions": False,
    }


def test_legacy_worker_clis_delegate_to_target_namespace() -> None:
    assert legacy_worker_status_cli.LIVE_STRATEGY_WORKER_STATUS_PATH == janus_worker.LIVE_STRATEGY_WORKER_STATUS_PATH
    assert (
        legacy_worker_status_cli.main_for_live_strategy_worker_status
        is janus_worker.main_for_live_strategy_worker_status
    )
    assert legacy_worker_start_cli.LIVE_STRATEGY_WORKER_START_PATH == janus_worker.LIVE_STRATEGY_WORKER_START_PATH
    assert (
        legacy_worker_start_cli.build_live_strategy_worker_start_payload
        is janus_worker.build_live_strategy_worker_start_payload
    )
    assert legacy_worker_start_cli.main_for_live_strategy_worker_start is janus_worker.main_for_live_strategy_worker_start
    assert legacy_worker_stop_cli.LIVE_STRATEGY_WORKER_STOP_PATH == janus_worker.LIVE_STRATEGY_WORKER_STOP_PATH
    assert legacy_worker_stop_cli.main_for_live_strategy_worker_stop is janus_worker.main_for_live_strategy_worker_stop
    assert legacy_worker_tick_cli.LIVE_STRATEGY_WORKER_TICK_PATH == janus_worker.LIVE_STRATEGY_WORKER_TICK_PATH
    assert (
        legacy_worker_tick_cli.build_live_strategy_worker_tick_payload
        is janus_worker.build_live_strategy_worker_tick_payload
    )
    assert legacy_worker_tick_cli.main_for_live_strategy_worker_tick is janus_worker.main_for_live_strategy_worker_tick


def test_janus_namespace_exports_worker_tick_helpers() -> None:
    assert janus_namespace.LIVE_STRATEGY_WORKER_TICK_PATH == janus_worker.LIVE_STRATEGY_WORKER_TICK_PATH
    assert janus_namespace.build_live_strategy_worker_tick_parser is janus_worker.build_live_strategy_worker_tick_parser
    assert janus_namespace.build_live_strategy_worker_tick_payload is janus_worker.build_live_strategy_worker_tick_payload
    assert janus_namespace.run_live_strategy_worker_tick is janus_worker.run_live_strategy_worker_tick
    assert janus_namespace.main_for_live_strategy_worker_tick is janus_worker.main_for_live_strategy_worker_tick
    assert "LIVE_STRATEGY_WORKER_TICK_PATH" in janus_namespace.__all__
    assert "main_for_live_strategy_worker_tick" in janus_namespace.__all__


def test_janus_live_strategy_tick_bridge_preserves_legacy_cli_shape() -> None:
    parser = janus_live_strategy_tick.build_live_strategy_tick_parser("live tick")
    args = parser.parse_args(
        [
            "--session-date",
            "2026-05-18",
            "--event-id",
            "event-1",
            "--account-id",
            "account-1",
            "--source",
            "pytest",
            "--execute",
            "--max-intents",
            "1",
            "--no-auto-protect-manual-positions",
        ]
    )

    assert janus_live_strategy_tick.build_live_strategy_tick_kwargs(args) == {
        "api_root": legacy_client.DEFAULT_API_ROOT,
        "session_date": "2026-05-18",
        "event_ids": ["event-1"],
        "account_id": "account-1",
        "source": "pytest",
        "execute": True,
        "live_money": False,
        "max_intents": 1,
        "orderbook_sample_count": 2,
        "orderbook_sample_interval_sec": 0.5,
        "min_size": 5.0,
        "min_buy_notional_usd": 1.0,
        "share_precision": 3,
        "auto_protect_manual_positions": False,
        "manual_target_delta_cents": 5.0,
        "submit_candidate_strategy_plan": False,
        "enable_llm_dispatch": False,
        "llm_runtime_artifact_root": None,
    }


def test_janus_live_strategy_tick_bridge_documents_execution_sensitive_boundary() -> None:
    status = janus_live_strategy_tick.describe_live_strategy_tick_compatibility()

    assert status["state"] == janus_live_strategy_tick.LIVE_STRATEGY_TICK_COMPATIBILITY_STATE
    assert status["legacy_module"] == "codex_tool.run_live_strategy_tick"
    assert status["target_namespace"] == "codex_tools.janus.live_strategy_tick"
    assert "--execute" in status["execution_sensitive_flags"]
    assert "--live-money" in status["execution_sensitive_flags"]
    assert "--auto-protect-manual-positions" in status["execution_sensitive_flags"]
    assert "higher-risk reviewed slice" in status["migration_note"]


def test_janus_live_strategy_tick_bridge_runs_legacy_orchestration_lazily(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def _run_tick(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {"ok": True, "source": kwargs["source"]}

    monkeypatch.setattr(janus_live_strategy_tick, "load_legacy_run_tick", lambda: _run_tick)

    response = janus_live_strategy_tick.run_legacy_live_strategy_tick(
        api_root="http://janus.local",
        session_date="2026-05-18",
        event_ids=["event-1"],
        account_id="account-1",
        source="pytest",
        execute=False,
        live_money=False,
    )

    assert response == {"ok": True, "source": "pytest"}
    assert calls == [
        {
            "api_root": "http://janus.local",
            "session_date": "2026-05-18",
            "event_ids": ["event-1"],
            "account_id": "account-1",
            "source": "pytest",
            "execute": False,
            "live_money": False,
        }
    ]


def test_legacy_live_strategy_tick_cli_delegates_to_target_namespace(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(legacy_live_strategy_tick_cli, "main_for_live_strategy_tick", calls.append)

    legacy_live_strategy_tick_cli.main()

    assert calls == ["Run one quote-aware StrategyPlanJSON tick with shadow and optional live execution."]


def test_janus_strategy_namespace_wraps_plan_submit_and_evaluate(monkeypatch) -> None:
    calls: list[tuple[str, str, str, dict[str, object]]] = []

    def _api_json(api_root: str, method: str, path: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((api_root, method, path, payload))
        return {"ok": True, "path": path}

    monkeypatch.setattr(janus_strategy, "api_json", _api_json)

    submitted = janus_strategy.submit_strategy_plan("http://janus.local", "event-1", {"plan": "v1"})
    evaluated = janus_strategy.evaluate_strategy_plan(
        "http://janus.local",
        "event-1",
        {"dry_run": True},
    )
    execute_preview = janus_strategy.evaluate_strategy_plan(
        "http://janus.local",
        "event-1",
        {"dry_run": True, "execute": True},
        execute=True,
    )

    assert submitted == {"ok": True, "path": "/v1/events/event-1/strategy-plan"}
    assert evaluated == {"ok": True, "path": "/v1/events/event-1/strategy-plan/evaluate"}
    assert execute_preview == {"ok": True, "path": "/v1/events/event-1/strategy-plan/execute"}
    assert calls == [
        ("http://janus.local", "POST", "/v1/events/event-1/strategy-plan", {"plan": "v1"}),
        ("http://janus.local", "POST", "/v1/events/event-1/strategy-plan/evaluate", {"dry_run": True}),
        (
            "http://janus.local",
            "POST",
            "/v1/events/event-1/strategy-plan/execute",
            {"dry_run": True, "execute": True},
        ),
    ]


def test_janus_strategy_namespace_preserves_eval_and_research_payloads(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    market_path = tmp_path / "market.json"
    research_path = tmp_path / "research.md"
    plan_path.write_text(json.dumps({"event_id": "event-1"}), encoding="utf-8-sig")
    market_path.write_text(json.dumps({"price": 0.42}), encoding="utf-8-sig")
    research_path.write_text("research", encoding="utf-8-sig")

    eval_args = janus_strategy.build_strategy_plan_evaluate_parser("eval").parse_args(
        [
            "--event-id",
            "event-1",
            "--plan-path",
            str(plan_path),
            "--market-state-path",
            str(market_path),
            "--account-id",
            "account-1",
            "--execute",
            "--live-money",
            "--max-intents",
            "2",
        ]
    )
    research_args = janus_strategy.build_pregame_research_parser("research").parse_args(
        ["--event-id", "event-1", "--source", "pytest", "--research-path", str(research_path)]
    )

    assert janus_strategy.build_strategy_plan_evaluate_payload(eval_args) == {
        "plan": {"event_id": "event-1"},
        "account_id": "account-1",
        "dry_run": False,
        "execute": True,
        "market_state": {"price": 0.42},
        "portfolio_state": {},
        "source": "codex",
        "max_intents": 2,
    }
    assert janus_strategy.build_pregame_research_payload(research_args) == {
        "session_date": None,
        "event_ids": ["event-1"],
        "run_id": None,
        "account_id": None,
        "source": "pytest",
        "notes": None,
        "execute": False,
        "research_path": str(research_path),
        "research_markdown": "research",
    }


def test_janus_strategy_namespace_preserves_llm_revision_payload(tmp_path: Path) -> None:
    response_path = tmp_path / "response.json"
    response_path.write_text(json.dumps({"request_id": "request-1"}), encoding="utf-8-sig")
    args = janus_strategy.build_llm_revision_adoption_parser("adopt").parse_args(
        [
            "--event-id",
            "event-1",
            "--session-date",
            "2026-05-18",
            "--reviewed-by",
            "reviewer",
            "--review-reason",
            "valid response",
            "--notes",
            "candidate",
            "--response-path",
            str(response_path),
            "--apply-current",
        ]
    )

    assert janus_strategy.build_llm_revision_adoption_payload(args) == {
        "session_date": "2026-05-18",
        "source": "codex-llm-revision-adoption",
        "reviewed_by": "reviewer",
        "review_reason": "valid response",
        "apply_current": True,
        "notes": "candidate",
        "response": {"request_id": "request-1"},
    }


def test_legacy_strategy_clis_delegate_to_target_namespace() -> None:
    assert legacy_submit_cli.STRATEGY_PLAN_PATH_TEMPLATE == janus_strategy.STRATEGY_PLAN_PATH_TEMPLATE
    assert legacy_submit_cli.submit_strategy_plan is janus_strategy.submit_strategy_plan
    assert legacy_submit_cli.main_for_strategy_plan_submit is janus_strategy.main_for_strategy_plan_submit
    assert legacy_evaluate_cli.STRATEGY_PLAN_EVALUATE_PATH_TEMPLATE == (
        janus_strategy.STRATEGY_PLAN_EVALUATE_PATH_TEMPLATE
    )
    assert legacy_evaluate_cli.STRATEGY_PLAN_EXECUTE_PATH_TEMPLATE == janus_strategy.STRATEGY_PLAN_EXECUTE_PATH_TEMPLATE
    assert legacy_evaluate_cli.evaluate_strategy_plan is janus_strategy.evaluate_strategy_plan
    assert legacy_evaluate_cli.main_for_strategy_plan_evaluate is janus_strategy.main_for_strategy_plan_evaluate
    assert legacy_adopt_cli.LLM_REVISION_ADOPT_PATH_TEMPLATE == janus_strategy.LLM_REVISION_ADOPT_PATH_TEMPLATE
    assert legacy_adopt_cli.adopt_llm_revision is janus_strategy.adopt_llm_revision
    assert legacy_adopt_cli._build_payload is janus_strategy.build_llm_revision_adoption_payload
    assert legacy_adopt_cli.main_for_llm_revision_adoption is janus_strategy.main_for_llm_revision_adoption
    assert legacy_pregame_cli.PREGAME_PLAN_PATH == janus_strategy.PREGAME_PLAN_PATH
    assert legacy_pregame_cli.submit_pregame_research is janus_strategy.submit_pregame_research
    assert legacy_pregame_cli.main_for_pregame_research is janus_strategy.main_for_pregame_research


def test_janus_events_namespace_wraps_context_and_review_endpoints(monkeypatch) -> None:
    calls: list[tuple[str, str, str, object, dict[str, object]]] = []

    def _api_json(
        api_root: str,
        method: str,
        path: str,
        payload: object = None,
        *,
        query: dict[str, object] | None = None,
    ) -> dict[str, object]:
        calls.append((api_root, method, path, payload, query or {}))
        return {"ok": True, "path": path}

    monkeypatch.setattr(janus_events, "api_json", _api_json)

    context = janus_events.get_event_context(
        "http://janus.local",
        "event-1",
        session_date="2026-05-18",
    )
    review = janus_events.get_event_review_bundle(
        "http://janus.local",
        "event-2",
        session_date="2026-05-17",
        account_id="account-1",
    )

    assert context == {"ok": True, "path": "/v1/events/event-1/agent-context"}
    assert review == {"ok": True, "path": "/v1/events/event-2/review-bundle"}
    assert calls == [
        (
            "http://janus.local",
            "GET",
            "/v1/events/event-1/agent-context",
            None,
            {"session_date": "2026-05-18"},
        ),
        (
            "http://janus.local",
            "GET",
            "/v1/events/event-2/review-bundle",
            None,
            {"session_date": "2026-05-17", "account_id": "account-1"},
        ),
    ]


def test_janus_events_namespace_preserves_replay_and_watch_payloads() -> None:
    replay_args = janus_events.build_replay_from_watch_session_parser("replay").parse_args(
        [
            "--watch-session-id",
            "watch-1",
            "--event-key",
            "event-key-1",
            "--output-name",
            "replay-name",
            "--notes",
            "unit test",
        ]
    )
    watch_args = janus_events.build_watch_market_parser("watch").parse_args(
        [
            "--event-key",
            "event-key-1",
            "--title",
            "Event Title",
            "--category",
            "geopolitics",
            "--source-url",
            "https://example.com/a",
            "--source-url",
            "https://example.com/b",
            "--market-id",
            "market-1",
            "--notes",
            "watch note",
            "--active",
            "--source",
            "pytest",
        ]
    )

    assert janus_events.build_replay_from_watch_session_payload(replay_args) == {
        "watch_session_id": "watch-1",
        "event_key": "event-key-1",
        "output_name": "replay-name",
        "notes": "unit test",
    }
    assert janus_events.build_watchlist_payload(watch_args) == {
        "source": "pytest",
        "events": [
            {
                "event_key": "event-key-1",
                "category": "geopolitics",
                "title": "Event Title",
                "source_urls": ["https://example.com/a", "https://example.com/b"],
                "market_id": "market-1",
                "notes": "watch note",
                "passive_only": False,
            }
        ],
    }


def test_legacy_event_review_clis_delegate_to_target_namespace() -> None:
    assert legacy_context_cli.EVENT_AGENT_CONTEXT_PATH_TEMPLATE == janus_events.EVENT_AGENT_CONTEXT_PATH_TEMPLATE
    assert legacy_context_cli.main_for_event_context is janus_events.main_for_event_context
    assert legacy_review_cli.EVENT_REVIEW_BUNDLE_PATH_TEMPLATE == janus_events.EVENT_REVIEW_BUNDLE_PATH_TEMPLATE
    assert legacy_review_cli.main_for_event_review_bundle is janus_events.main_for_event_review_bundle
    assert legacy_replay_cli.REPLAY_FROM_WATCH_SESSION_PATH == janus_events.REPLAY_FROM_WATCH_SESSION_PATH
    assert legacy_replay_cli.main_for_replay_from_watch_session is janus_events.main_for_replay_from_watch_session
    assert legacy_watch_cli.WATCHLIST_EVENTS_PATH == janus_events.WATCHLIST_EVENTS_PATH
    assert legacy_watch_cli.main_for_watch_market is janus_events.main_for_watch_market


def test_janus_watchlists_namespace_wraps_session_ticks_and_trades(monkeypatch) -> None:
    calls: list[tuple[str, str, str, dict[str, object]]] = []

    def _api_json(api_root: str, method: str, path: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((api_root, method, path, payload))
        return {"ok": True, "path": path}

    monkeypatch.setattr(janus_watchlists, "api_json", _api_json)

    session = janus_watchlists.start_watch_session(
        "http://janus.local",
        {"event_key": "event-key-1", "passive_only": True},
    )
    ticks = janus_watchlists.record_orderbook_ticks(
        "http://janus.local",
        {"source": "pytest", "ticks": [{"event_key": "event-key-1"}]},
    )
    trades = janus_watchlists.record_market_trades(
        "http://janus.local",
        {"source": "pytest", "trades": [{"event_key": "event-key-1"}]},
    )

    assert session == {"ok": True, "path": "/v1/watchlists/sessions"}
    assert ticks == {"ok": True, "path": "/v1/watchlists/orderbook-ticks"}
    assert trades == {"ok": True, "path": "/v1/watchlists/trades"}
    assert calls == [
        (
            "http://janus.local",
            "POST",
            "/v1/watchlists/sessions",
            {"event_key": "event-key-1", "passive_only": True},
        ),
        (
            "http://janus.local",
            "POST",
            "/v1/watchlists/orderbook-ticks",
            {"source": "pytest", "ticks": [{"event_key": "event-key-1"}]},
        ),
        (
            "http://janus.local",
            "POST",
            "/v1/watchlists/trades",
            {"source": "pytest", "trades": [{"event_key": "event-key-1"}]},
        ),
    ]


def test_janus_watchlists_namespace_preserves_legacy_payload_shapes() -> None:
    session_args = janus_watchlists.build_watch_session_parser("session").parse_args(
        [
            "--event-key",
            "event-key-1",
            "--category",
            "geopolitics",
            "--active-trading",
            "--metadata-json",
            '{"source":"unit-test"}',
        ]
    )
    tick_args = janus_watchlists.build_orderbook_tick_parser("tick").parse_args(
        [
            "--event-key",
            "event-key-1",
            "--best-bid",
            "0.41",
            "--best-ask",
            "0.43",
            "--source",
            "pytest",
        ]
    )
    trade_args = janus_watchlists.build_market_trade_parser("trade").parse_args(
        [
            "--trades-json",
            '{"event_key":"event-key-1","price":0.42}',
            "--source",
            "pytest",
        ]
    )

    assert janus_watchlists.build_watch_session_payload(session_args) == {
        "watch_session_id": None,
        "event_key": "event-key-1",
        "category": "geopolitics",
        "passive_only": False,
        "cadence_ms": None,
        "reason": None,
        "metadata": {"source": "unit-test"},
    }
    assert janus_watchlists.build_orderbook_tick_payload(tick_args) == {
        "source": "pytest",
        "ticks": [
            {
                "event_key": "event-key-1",
                "market_id": None,
                "outcome_id": None,
                "token_id": None,
                "best_bid": 0.41,
                "best_ask": 0.43,
                "spread": 0.02,
                "mid_price": 0.42,
                "bid_depth": None,
                "ask_depth": None,
                "source_latency_ms": None,
                "ingest_latency_ms": None,
            }
        ],
    }
    assert janus_watchlists.build_market_trade_payload(trade_args) == {
        "source": "pytest",
        "trades": [{"event_key": "event-key-1", "price": 0.42}],
    }


def test_legacy_watchlist_capture_clis_delegate_to_target_namespace() -> None:
    assert legacy_watch_session_cli.WATCHLIST_SESSIONS_PATH == janus_watchlists.WATCHLIST_SESSIONS_PATH
    assert legacy_watch_session_cli.build_watch_session_payload is janus_watchlists.build_watch_session_payload
    assert legacy_watch_session_cli.start_watch_session is janus_watchlists.start_watch_session
    assert legacy_watch_session_cli.main_for_watch_session is janus_watchlists.main_for_watch_session
    assert legacy_orderbook_tick_cli.WATCHLIST_ORDERBOOK_TICKS_PATH == janus_watchlists.WATCHLIST_ORDERBOOK_TICKS_PATH
    assert legacy_orderbook_tick_cli.build_orderbook_tick_payload is janus_watchlists.build_orderbook_tick_payload
    assert legacy_orderbook_tick_cli.record_orderbook_ticks is janus_watchlists.record_orderbook_ticks
    assert legacy_orderbook_tick_cli.main_for_orderbook_tick_record is janus_watchlists.main_for_orderbook_tick_record
    assert legacy_market_trade_cli.WATCHLIST_TRADES_PATH == janus_watchlists.WATCHLIST_TRADES_PATH
    assert legacy_market_trade_cli.build_market_trade_payload is janus_watchlists.build_market_trade_payload
    assert legacy_market_trade_cli.record_market_trades is janus_watchlists.record_market_trades
    assert legacy_market_trade_cli.main_for_market_trade_record is janus_watchlists.main_for_market_trade_record


def test_janus_reconciliation_namespace_wraps_order_and_trade_endpoints(monkeypatch) -> None:
    calls: list[tuple[str, str, str, object, dict[str, object]]] = []

    def _api_json(
        api_root: str,
        method: str,
        path: str,
        payload: object = None,
        *,
        query: dict[str, object] | None = None,
    ) -> dict[str, object]:
        calls.append((api_root, method, path, payload, query or {}))
        return {"ok": True, "path": path}

    monkeypatch.setattr(janus_reconciliation, "api_json", _api_json)

    order = janus_reconciliation.reconcile_operator_interventions(
        "http://janus.local",
        {"account_id": "account-1", "action": "scan"},
    )
    trades = janus_reconciliation.get_trade_reconciliation(
        "http://janus.local",
        {"account_id": "account-1", "limit": 100},
    )

    assert order == {"ok": True, "path": "/v1/operator/interventions/reconcile"}
    assert trades == {"ok": True, "path": "/v1/portfolio/trades/reconciliation"}
    assert calls == [
        (
            "http://janus.local",
            "POST",
            "/v1/operator/interventions/reconcile",
            {"account_id": "account-1", "action": "scan"},
            {},
        ),
        (
            "http://janus.local",
            "GET",
            "/v1/portfolio/trades/reconciliation",
            None,
            {"account_id": "account-1", "limit": 100},
        ),
    ]


def test_janus_reconciliation_namespace_preserves_legacy_payload_shapes() -> None:
    order_args = janus_reconciliation.build_order_reconciliation_parser("orders").parse_args(
        [
            "--account-id",
            "account-1",
            "--event-id",
            "event-1",
            "--market-id",
            "market-1",
            "--action",
            "adopt",
            "--external-order-id",
            "order-1",
            "--external-trade-id",
            "trade-1",
            "--strategy-family",
            "manual-tail",
            "--manual-reason",
            "operator test",
            "--target-status",
            "covered",
            "--stop-status",
            "missing",
            "--hedge-status",
            "none",
            "--protective-order-status",
            "target_live",
            "--expected-close-path",
            "target",
            "--final-pnl-usd",
            "1.25",
            "--metadata-json",
            '{"source":"unit-test"}',
            "--notes",
            "reviewed",
        ]
    )
    trade_args = janus_reconciliation.build_trade_reconciliation_parser("trades").parse_args(
        [
            "--account-id",
            "account-1",
            "--market-id",
            "market-1",
            "--outcome-id",
            "outcome-1",
            "--event-slug",
            "event-slug-1",
            "--start-time",
            "2026-05-18T00:00:00Z",
            "--end-time",
            "2026-05-18T01:00:00Z",
            "--limit",
            "250",
        ]
    )

    assert janus_reconciliation.build_order_reconciliation_payload(order_args) == {
        "account_id": "account-1",
        "event_id": "event-1",
        "market_id": "market-1",
        "action": "adopt",
        "external_order_ids": ["order-1"],
        "external_trade_ids": ["trade-1"],
        "strategy_family": "manual-tail",
        "manual_reason": "operator test",
        "target_status": "covered",
        "stop_status": "missing",
        "hedge_status": "none",
        "protective_order_status": "target_live",
        "expected_close_path": "target",
        "final_pnl_usd": 1.25,
        "metadata": {"source": "unit-test"},
        "notes": "reviewed",
    }
    assert janus_reconciliation.build_trade_reconciliation_query(trade_args) == {
        "account_id": "account-1",
        "market_id": "market-1",
        "outcome_id": "outcome-1",
        "event_slug": "event-slug-1",
        "start_time": "2026-05-18T00:00:00Z",
        "end_time": "2026-05-18T01:00:00Z",
        "limit": 250,
    }


def test_legacy_reconciliation_clis_delegate_to_target_namespace() -> None:
    assert legacy_reconcile_orders_cli.OPERATOR_INTERVENTION_RECONCILE_PATH == (
        janus_reconciliation.OPERATOR_INTERVENTION_RECONCILE_PATH
    )
    assert legacy_reconcile_orders_cli.build_order_reconciliation_payload is (
        janus_reconciliation.build_order_reconciliation_payload
    )
    assert legacy_reconcile_orders_cli.reconcile_operator_interventions is (
        janus_reconciliation.reconcile_operator_interventions
    )
    assert legacy_reconcile_orders_cli.main_for_order_reconciliation is (
        janus_reconciliation.main_for_order_reconciliation
    )
    assert legacy_reconcile_trades_cli.TRADE_RECONCILIATION_PATH == janus_reconciliation.TRADE_RECONCILIATION_PATH
    assert legacy_reconcile_trades_cli.build_trade_reconciliation_query is (
        janus_reconciliation.build_trade_reconciliation_query
    )
    assert legacy_reconcile_trades_cli.get_trade_reconciliation is janus_reconciliation.get_trade_reconciliation
    assert legacy_reconcile_trades_cli.main_for_trade_reconciliation is (
        janus_reconciliation.main_for_trade_reconciliation
    )


def test_polymarket_fallback_blocks_when_gates_are_missing() -> None:
    decision = build_fallback_decision(_intent(), PolymarketExecutionGateSnapshot())

    assert decision.status == "blocked_missing_execution_gates"
    assert decision.execution_authorized is False
    assert decision.order_submission_attempted is False
    assert "direct_truth_fresh" in decision.missing_gates
    assert "explicit_execution_approval" in decision.missing_gates
    assert "No order was placed" in decision.no_execution_statement


def test_explicit_approval_alone_does_not_authorize_execution() -> None:
    decision = build_fallback_decision(
        _intent(dry_run=False),
        PolymarketExecutionGateSnapshot(explicit_execution_approval=True),
    )

    assert decision.status == "blocked_missing_execution_gates"
    assert decision.execution_authorized is False
    assert "direct_truth_fresh" in decision.missing_gates
    assert "risk_budget_selected" in decision.missing_gates


def test_satisfied_gates_still_default_to_dry_run_preview() -> None:
    decision = build_fallback_decision(_intent(), _satisfied_gate())

    assert decision.status == "dry_run_preview_only"
    assert decision.execution_authorized is False
    assert decision.order_submission_attempted is False
    assert decision.missing_gates == []


def test_non_dry_run_only_marks_ready_without_submitting() -> None:
    decision = build_fallback_decision(_intent(dry_run=False), _satisfied_gate())

    assert decision.status == "ready_for_approved_execution_path"
    assert decision.execution_authorized is True
    assert decision.order_submission_attempted is False
    assert decision.ledger_write_required_before_submission is True


def test_idempotency_and_ledger_id_are_stable_for_same_intent() -> None:
    intent = _intent()
    first = build_fallback_decision(intent, PolymarketExecutionGateSnapshot())
    second = build_fallback_decision(intent, PolymarketExecutionGateSnapshot())

    assert first.idempotency_key == second.idempotency_key
    assert first.ledger_id == second.ledger_id
    assert asdict(intent)["idempotency_key"] is None


def test_fallback_decision_ledger_writes_blocked_decision_once(tmp_path: Path) -> None:
    written_at = datetime(2026, 5, 18, 14, 5, 0, tzinfo=UTC)
    decision = build_fallback_decision(_intent(), PolymarketExecutionGateSnapshot())

    first = write_fallback_decision_ledger(
        decision,
        ledger_root=tmp_path,
        written_at_utc=written_at,
    )
    second = write_fallback_decision_ledger(
        decision,
        ledger_root=tmp_path,
        written_at_utc=written_at,
    )

    assert first.status == "written"
    assert first.created is True
    assert second.status == "already_recorded"
    assert second.created is False

    ledger_path = tmp_path / "2026-05-18" / "fallback_decisions.jsonl"
    rows = ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1

    entry = json.loads(rows[0])
    assert entry["schema_version"] == "polymarket_fallback_ledger_entry_v1"
    assert entry["ledger_id"] == decision.ledger_id
    assert entry["decision"]["status"] == "blocked_missing_execution_gates"
    assert entry["decision"]["order_submission_attempted"] is False
    assert "No order was placed" in entry["no_execution_statement"]


def test_fallback_decision_ledger_records_preview_without_execution(tmp_path: Path) -> None:
    decision = build_fallback_decision(_intent(), _satisfied_gate())

    result = write_fallback_decision_ledger(
        decision,
        ledger_root=tmp_path,
        written_at_utc=datetime(2026, 5, 18, 14, 6, 0, tzinfo=UTC),
    )

    entry = json.loads(Path(result.ledger_path).read_text(encoding="utf-8").splitlines()[0])
    assert entry["decision"]["status"] == "dry_run_preview_only"
    assert entry["decision"]["execution_authorized"] is False
    assert entry["decision"]["order_submission_attempted"] is False


def test_account_snapshot_blocks_without_account_credentials() -> None:
    def _raise_if_called(*args: object, **kwargs: object) -> list[object]:
        raise AssertionError("reader should not be called without credentials")

    snapshot = read_account_snapshot(
        creds=_Creds(),
        position_reader=_raise_if_called,
        order_reader=_raise_if_called,
        trade_reader=_raise_if_called,
    )

    assert snapshot.status == "blocked_missing_account_credentials"
    assert snapshot.section_status["open_positions"] == "blocked_missing_wallet_address"
    assert snapshot.section_status["open_orders"] == "blocked_missing_clob_credentials"
    assert snapshot.section_status["trades"] == "blocked_missing_clob_credentials"
    assert snapshot.order_preparation_attempted is False
    assert snapshot.order_submission_attempted is False
    assert "No order was placed" in snapshot.no_execution_statement


def test_account_snapshot_reads_direct_state_without_execution() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def _positions(creds: _Creds, **kwargs: object) -> list[dict[str, object]]:
        calls.append(("positions", kwargs))
        return [{"asset": "token-yes", "size": 5.0}]

    def _orders(creds: _Creds, **kwargs: object) -> list[dict[str, object]]:
        calls.append(("orders", kwargs))
        return [{"id": "0xopen", "asset_id": "token-yes", "side": "SELL"}]

    def _trades(creds: _Creds) -> list[dict[str, object]]:
        calls.append(("trades", {}))
        return [{"id": "trade-1", "asset_id": "token-yes", "side": "BUY"}]

    snapshot = read_account_snapshot(
        creds=_Creds(wallet_address="0xabc", private_key="0xpk"),
        account_id="account-1",
        event_slug="test-event",
        min_position_size=0.0,
        position_reader=_positions,
        order_reader=_orders,
        trade_reader=_trades,
        read_at_utc=datetime(2026, 5, 18, 14, 7, 0, tzinfo=UTC),
    )

    assert snapshot.status == "read_only_snapshot"
    assert snapshot.account_id == "account-1"
    assert snapshot.read_at_utc == "2026-05-18T14:07:00Z"
    assert snapshot.open_order_count == 1
    assert snapshot.open_position_count == 1
    assert snapshot.trade_count == 1
    assert snapshot.section_status == {
        "open_positions": "ok",
        "open_orders": "ok",
        "trades": "ok",
    }
    assert calls == [
        ("positions", {"event_slug": "test-event", "min_size": 0.0}),
        ("orders", {"open_only": True}),
        ("trades", {}),
    ]
    assert snapshot.order_submission_attempted is False


def test_account_snapshot_is_partial_without_clob_credentials() -> None:
    snapshot = read_account_snapshot(
        creds=_Creds(wallet_address="0xabc"),
        position_reader=lambda *args, **kwargs: [{"asset": "token-yes"}],
    )

    assert snapshot.status == "read_only_snapshot_partial"
    assert snapshot.open_position_count == 1
    assert snapshot.open_order_count == 0
    assert snapshot.trade_count == 0
    assert snapshot.section_status["open_orders"] == "blocked_missing_clob_credentials"
    assert snapshot.section_status["trades"] == "blocked_missing_clob_credentials"


def _fresh_snapshot() -> dict[str, object]:
    return {
        "status": "read_only_snapshot",
        "read_at_utc": "2026-05-18T14:20:00Z",
        "section_status": {
            "open_positions": "ok",
            "open_orders": "ok",
            "trades": "ok",
        },
        "open_order_count": 1,
        "open_position_count": 1,
        "trade_count": 1,
    }


def _settlement_snapshot(
    *,
    open_positions: list[dict[str, object]] | None = None,
    open_orders: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "status": "read_only_snapshot",
        "read_at_utc": "2026-05-19T12:00:00Z",
        "section_status": {
            "open_positions": "ok",
            "open_orders": "ok",
            "trades": "ok",
        },
        "open_positions": open_positions or [],
        "open_orders": open_orders or [],
        "trades": [],
        "open_order_count": len(open_orders or []),
        "open_position_count": len(open_positions or []),
        "trade_count": 0,
    }


def _losing_residual_position() -> dict[str, object]:
    return {
        "market_slug": "nba-sas-okc-2026-05-18",
        "condition_id": "condition-okc",
        "token_id": "token-thunder",
        "size": "338.4702",
        "current_value": "0",
    }


def _redeemable_residual_position() -> dict[str, object]:
    return {
        "market_slug": "nba-sas-okc-2026-05-18",
        "condition_id": "condition-okc",
        "token_id": "token-spurs",
        "size": "7.5",
        "current_value": "7.5",
    }


def test_direct_truth_freshness_requires_complete_recent_snapshot() -> None:
    stale = evaluate_direct_truth_freshness(
        _fresh_snapshot(),
        now_utc=datetime(2026, 5, 18, 14, 35, 1, tzinfo=UTC),
        max_age_seconds=300.0,
    )
    partial = evaluate_direct_truth_freshness(
        {
            **_fresh_snapshot(),
            "status": "read_only_snapshot_partial",
            "section_status": {"open_positions": "ok", "open_orders": "blocked_missing_clob_credentials"},
        },
        now_utc=datetime(2026, 5, 18, 14, 20, 30, tzinfo=UTC),
    )

    assert stale.passed is False
    assert "direct_truth_snapshot_stale" in stale.blockers
    assert partial.passed is False
    assert "direct_truth_snapshot_not_complete" in partial.blockers
    assert "direct_truth_section_open_orders_blocked_missing_clob_credentials" in partial.blockers


def test_risk_budget_minimum_order_and_kill_switch_checks_block_missing_inputs() -> None:
    risk = evaluate_risk_budget(proposed_notional_usd=2.1)
    minimum = evaluate_minimum_order_policy(
        PolymarketFallbackIntent(
            action="open_position",
            account_id="account-1",
            market_slug="test-market",
            token_id="token-yes",
            side="BUY",
            price="0.10",
            size="4",
            reason="unit test",
        )
    )
    kill_switch = evaluate_kill_switch(kill_switch_clear=False)

    assert risk.passed is False
    assert "risk_budget_not_selected" in risk.blockers
    assert "risk_budget_limit_missing" in risk.blockers
    assert minimum.passed is False
    assert "minimum_order_size_not_met" in minimum.blockers
    assert "minimum_buy_notional_not_met" in minimum.blockers
    assert kill_switch.passed is False
    assert "kill_switch_not_clear" in kill_switch.blockers


def test_direct_fallback_gate_requires_target_stop_rebuy_policy() -> None:
    intent = _intent(dry_run=True)
    gate = build_polymarket_safety_gate_snapshot(
        intent,
        direct_truth_snapshot=_fresh_snapshot(),
        now_utc=datetime(2026, 5, 18, 14, 20, 30, tzinfo=UTC),
        risk_budget_name="global-portfolio-test",
        risk_budget_max_notional_usd=10.0,
        kill_switch_clear=True,
        kill_switch_source="unit-test",
        janus_degraded_or_direct_path_selected=True,
        ledger_available=True,
        reconciliation_plan={"target": "Janus action ledger"},
        explicit_execution_approval=True,
        truth_sources=["direct_clob", "janus_api"],
    )

    assert gate.target_stop_rebuy_policy_present is False
    assert "target_stop_rebuy_policy_present" in gate.missing_gates()
    assert gate.evidence["target_stop_rebuy_policy"]["passed"] is False


def test_safety_gate_snapshot_blocks_without_integrated_gate_inputs() -> None:
    intent = _intent(dry_run=False)
    gate = build_polymarket_safety_gate_snapshot(
        intent,
        direct_truth_snapshot=_fresh_snapshot(),
        now_utc=datetime(2026, 5, 18, 14, 20, 30, tzinfo=UTC),
        risk_budget_name="global-portfolio-test",
        risk_budget_max_notional_usd=10.0,
        kill_switch_clear=False,
        target_stop_rebuy_policy=_target_stop_rebuy_policy(),
        janus_degraded_or_direct_path_selected=True,
        ledger_available=False,
        reconciliation_plan="reconcile back to Janus action ledger",
        explicit_execution_approval=True,
        truth_sources=["direct_clob", "screenshot"],
    )
    decision = build_fallback_decision(intent, gate)

    assert gate.direct_truth_fresh is True
    assert gate.risk_budget_selected is True
    assert gate.minimum_order_policy_passed is True
    assert gate.kill_switch_clear is False
    assert gate.ledger_idempotency_available is False
    assert gate.non_authoritative_truth_rejected is False
    assert "kill_switch_clear" in gate.missing_gates()
    assert "ledger_idempotency_available" in gate.missing_gates()
    assert "non_authoritative_truth_rejected" in gate.missing_gates()
    assert gate.evidence["order_preparation_attempted"] is False
    assert gate.evidence["order_submission_attempted"] is False
    assert gate.evidence["rejected_truth_sources"] == ["screenshot"]
    assert decision.status == "blocked_missing_execution_gates"
    assert decision.order_preparation_attempted is False
    assert decision.order_submission_attempted is False


def test_safety_gate_snapshot_can_feed_preview_only_decision_when_all_gates_pass() -> None:
    intent = _intent(dry_run=True)
    gate = build_polymarket_safety_gate_snapshot(
        intent,
        direct_truth_snapshot=_fresh_snapshot(),
        now_utc=datetime(2026, 5, 18, 14, 20, 30, tzinfo=UTC),
        risk_budget_name="global-portfolio-test",
        risk_budget_max_notional_usd=10.0,
        kill_switch_clear=True,
        kill_switch_source="unit-test",
        target_stop_rebuy_policy=_target_stop_rebuy_policy(),
        janus_degraded_or_direct_path_selected=True,
        ledger_available=True,
        reconciliation_plan={"target": "Janus action ledger"},
        explicit_execution_approval=True,
        truth_sources=["direct_clob", "janus_api"],
    )
    decision = build_fallback_decision(intent, gate)

    assert gate.execution_gates_satisfied() is True
    assert gate.evidence["direct_truth"]["passed"] is True
    assert gate.evidence["risk_budget"]["passed"] is True
    assert gate.evidence["minimum_order_policy"]["passed"] is True
    assert gate.evidence["kill_switch"]["passed"] is True
    assert decision.status == "dry_run_preview_only"
    assert decision.execution_authorized is False
    assert decision.order_preparation_attempted is False
    assert decision.order_submission_attempted is False


def test_fallback_preview_service_entrypoint_blocks_without_execution() -> None:
    preview = build_fallback_preview(
        _intent(dry_run=False),
        direct_truth_snapshot=_fresh_snapshot(),
        now_utc=datetime(2026, 5, 18, 14, 20, 30, tzinfo=UTC),
        risk_budget_name="global-portfolio-test",
        risk_budget_max_notional_usd=10.0,
        kill_switch_clear=True,
        kill_switch_source="unit-test",
        target_stop_rebuy_policy=_target_stop_rebuy_policy(),
        janus_degraded_or_direct_path_selected=True,
        ledger_available=True,
        reconciliation_plan="reconcile back to Janus action ledger",
        explicit_execution_approval=False,
        truth_sources=["direct_clob"],
    )

    assert preview.schema_version == PREVIEW_SCHEMA_VERSION
    assert preview.status == "blocked_missing_execution_gates"
    assert preview.decision["order_preparation_attempted"] is False
    assert preview.decision["order_submission_attempted"] is False
    assert preview.ledger_write is None
    assert preview.order_preparation_attempted is False
    assert preview.order_submission_attempted is False
    assert preview.gate_snapshot["evidence"]["order_submission_attempted"] is False


def test_fallback_preview_can_write_preview_ledger_without_execution(tmp_path: Path) -> None:
    preview = build_fallback_preview(
        _intent(dry_run=True),
        direct_truth_snapshot=_fresh_snapshot(),
        now_utc=datetime(2026, 5, 18, 14, 20, 30, tzinfo=UTC),
        risk_budget_name="global-portfolio-test",
        risk_budget_max_notional_usd=10.0,
        kill_switch_clear=True,
        kill_switch_source="unit-test",
        target_stop_rebuy_policy=_target_stop_rebuy_policy(),
        janus_degraded_or_direct_path_selected=True,
        ledger_available=True,
        reconciliation_plan={"target": "Janus action ledger"},
        explicit_execution_approval=True,
        truth_sources=["direct_clob", "janus_api"],
        write_ledger=True,
        ledger_root=tmp_path,
        written_at_utc=datetime(2026, 5, 18, 14, 33, 0, tzinfo=UTC),
    )

    assert preview.status == "dry_run_preview_only"
    assert preview.decision["execution_authorized"] is False
    assert preview.ledger_write is not None
    assert preview.ledger_write["status"] == "written"
    assert Path(preview.ledger_write["ledger_path"]).exists()
    assert preview.order_preparation_attempted is False
    assert preview.order_submission_attempted is False


def test_polymarket_cli_preview_fallback_outputs_blocked_json(capsys) -> None:
    exit_code = polymarket_cli_main(
        [
            "preview-fallback",
            "--action",
            "open_position",
            "--account-id",
            "account-1",
            "--market-slug",
            "test-market",
            "--token-id",
            "token-yes",
            "--side",
            "BUY",
            "--price",
            "0.42",
            "--size",
            "5",
            "--reason",
            "unit test",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == PREVIEW_SCHEMA_VERSION
    assert payload["decision"]["status"] == "blocked_missing_execution_gates"
    assert payload["decision"]["order_preparation_attempted"] is False
    assert payload["decision"]["order_submission_attempted"] is False
    assert payload["order_preparation_attempted"] is False
    assert payload["order_submission_attempted"] is False


def test_resolved_losing_position_classifies_as_zero_value_residual() -> None:
    position = _losing_residual_position()
    classification = classify_resolved_market_residual(
        position,
        {
            "resolved": True,
            "condition_id": "condition-okc",
            "market_slug": "nba-sas-okc-2026-05-18",
            "winning_token_id": "token-spurs",
            "payouts": {"token-thunder": "0", "token-spurs": "1"},
        },
        _settlement_snapshot(open_positions=[position]),
        issue_link="https://github.com/LucaCGN/janus_cortex/issues/58",
        post_redeem_recheck_plan="Recheck direct account and Janus settlement ledger after operator review.",
    )

    assert classification.schema_version == RESIDUAL_CLASSIFICATION_SCHEMA_VERSION
    assert classification.status == "documented_zero_value_residual"
    assert classification.residual_type == "zero_value_residual"
    assert classification.live_readiness_blocker is False
    assert classification.expected_payout_usd == "0"
    assert classification.matching_open_order_count == 0
    assert classification.order_preparation_attempted is False
    assert classification.order_submission_attempted is False
    assert classification.redemption_preparation_attempted is False
    assert classification.redemption_submission_attempted is False


def test_resolved_residual_blocks_when_event_open_order_remains() -> None:
    position = _losing_residual_position()
    classification = classify_resolved_market_residual(
        position,
        {
            "resolved": True,
            "condition_id": "condition-okc",
            "market_slug": "nba-sas-okc-2026-05-18",
            "payouts": {"token-thunder": "0"},
        },
        _settlement_snapshot(
            open_positions=[position],
            open_orders=[
                {
                    "id": "0xopen",
                    "condition_id": "condition-okc",
                    "token_id": "token-thunder",
                    "side": "SELL",
                }
            ],
        ),
        issue_link="https://github.com/LucaCGN/janus_cortex/issues/58",
        post_redeem_recheck_plan="Recheck after the open order is reconciled.",
    )

    assert classification.status == "blocked_residual_classification"
    assert classification.residual_type == "unknown_settlement_state"
    assert classification.live_readiness_blocker is True
    assert classification.matching_open_order_count == 1
    assert "event_scoped_open_orders_present" in classification.blockers


def test_redeem_preview_blocks_missing_gates_without_transaction_attempts() -> None:
    position = {
        "market_slug": "nba-sas-okc-2026-05-18",
        "condition_id": "condition-okc",
        "token_id": "token-spurs",
        "size": "7.5",
        "current_value": "7.5",
    }
    preview = build_redeem_preview(
        position,
        {
            "resolved": True,
            "condition_id": "condition-okc",
            "market_slug": "nba-sas-okc-2026-05-18",
            "winning_token_id": "token-spurs",
            "payouts": {"token-spurs": "1"},
        },
        _settlement_snapshot(open_positions=[position]),
        issue_link="https://github.com/LucaCGN/janus_cortex/issues/58",
        post_redeem_recheck_plan="Recheck direct account and Janus ledger after redeem.",
        truth_sources=["direct_clob", "github"],
        now_utc=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC),
    )

    assert preview.schema_version == REDEEM_PREVIEW_SCHEMA_VERSION
    assert preview.status == "blocked_missing_redemption_gates"
    assert preview.redemption_authorized is False
    assert "wallet_chain_signer_ready" in preview.missing_gates
    assert "kill_switch_clear" in preview.missing_gates
    assert "ledger_idempotency_available" in preview.missing_gates
    assert "janus_codex_approval" in preview.missing_gates
    assert "non_authoritative_truth_rejected" in preview.missing_gates
    assert preview.transaction_preparation_attempted is False
    assert preview.transaction_signing_attempted is False
    assert preview.transaction_submission_attempted is False
    assert preview.transaction_broadcast_attempted is False
    assert preview.order_preparation_attempted is False
    assert preview.order_submission_attempted is False


def test_redeem_preview_all_gates_stays_dry_run_without_broadcast() -> None:
    position = {
        "market_slug": "nba-sas-okc-2026-05-18",
        "condition_id": "condition-okc",
        "token_id": "token-spurs",
        "size": "7.5",
        "current_value": "7.5",
    }
    preview = build_redeem_preview(
        position,
        {
            "resolved": True,
            "condition_id": "condition-okc",
            "market_slug": "nba-sas-okc-2026-05-18",
            "winning_token_id": "token-spurs",
            "payouts": {"token-spurs": "1"},
        },
        _settlement_snapshot(open_positions=[position]),
        issue_link="https://github.com/LucaCGN/janus_cortex/issues/58",
        post_redeem_recheck_plan="Recheck direct account and Janus ledger after redeem.",
        wallet_ready=True,
        chain_ready=True,
        signer_ready=True,
        gas_fee_ready=True,
        kill_switch_clear=True,
        ledger_available=True,
        janus_codex_approval=True,
        truth_sources=["direct_clob", "janus_api"],
        now_utc=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC),
    )

    assert preview.status == "dry_run_redeem_preview_only"
    assert preview.redemption_authorized is False
    assert preview.missing_gates == []
    assert preview.expected_payout_usd == "7.5"
    assert preview.idempotency_key.startswith("redeem-")
    assert preview.transaction_preparation_attempted is False
    assert preview.transaction_signing_attempted is False
    assert preview.transaction_submission_attempted is False
    assert preview.transaction_broadcast_attempted is False


def test_settlement_ledger_prewrite_records_redeem_preview_without_execution(tmp_path: Path) -> None:
    position = {
        "market_slug": "nba-sas-okc-2026-05-18",
        "condition_id": "condition-okc",
        "token_id": "token-spurs",
        "size": "7.5",
        "current_value": "7.5",
    }
    preview = build_redeem_preview(
        position,
        {
            "resolved": True,
            "condition_id": "condition-okc",
            "market_slug": "nba-sas-okc-2026-05-18",
            "winning_token_id": "token-spurs",
            "payouts": {"token-spurs": "1"},
        },
        _settlement_snapshot(open_positions=[position]),
        issue_link="https://github.com/LucaCGN/janus_cortex/issues/58",
        post_redeem_recheck_plan="Recheck direct account and Janus ledger after redeem.",
        wallet_ready=True,
        chain_ready=True,
        signer_ready=True,
        gas_fee_ready=True,
        kill_switch_clear=True,
        ledger_available=True,
        janus_codex_approval=True,
        truth_sources=["direct_clob", "janus_api"],
        now_utc=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC),
    )
    entry = build_settlement_ledger_entry(
        preview,
        written_at_utc=datetime(2026, 5, 19, 12, 1, 0, tzinfo=UTC),
        source_evidence={"artifact": "unit-test"},
    )
    first_write = write_settlement_ledger_prewrite(
        preview,
        ledger_root=tmp_path,
        written_at_utc=datetime(2026, 5, 19, 12, 1, 0, tzinfo=UTC),
        source_evidence={"artifact": "unit-test"},
    )
    duplicate_write = write_settlement_ledger_prewrite(
        preview,
        ledger_root=tmp_path,
        written_at_utc=datetime(2026, 5, 19, 12, 1, 0, tzinfo=UTC),
    )

    assert entry["schema_version"] == SETTLEMENT_LEDGER_ENTRY_SCHEMA_VERSION
    assert entry["redeem_preview_status"] == "dry_run_redeem_preview_only"
    assert entry["transaction_preparation_attempted"] is False
    assert entry["transaction_submission_attempted"] is False
    assert entry["order_preparation_attempted"] is False
    assert entry["order_submission_attempted"] is False
    assert first_write.schema_version == SETTLEMENT_LEDGER_WRITE_SCHEMA_VERSION
    assert first_write.status == "written"
    assert first_write.created is True
    assert first_write.transaction_preparation_attempted is False
    assert first_write.transaction_submission_attempted is False
    assert first_write.order_preparation_attempted is False
    assert first_write.order_submission_attempted is False
    assert duplicate_write.status == "already_recorded"
    assert duplicate_write.created is False
    ledger_rows = Path(first_write.ledger_path).read_text(encoding="utf-8").splitlines()
    assert len(ledger_rows) == 1
    ledger_payload = json.loads(ledger_rows[0])
    assert ledger_payload["schema_version"] == SETTLEMENT_LEDGER_ENTRY_SCHEMA_VERSION
    assert ledger_payload["idempotency_key"] == preview.idempotency_key
    assert ledger_payload["source_evidence"] == {"artifact": "unit-test"}
    assert ledger_payload["redeem_preview"]["transaction_broadcast_attempted"] is False


def test_post_redeem_reconciliation_marks_flat_only_with_ledger_and_redeem_evidence(tmp_path: Path) -> None:
    position = _redeemable_residual_position()
    preview = build_redeem_preview(
        position,
        {
            "resolved": True,
            "condition_id": "condition-okc",
            "market_slug": "nba-sas-okc-2026-05-18",
            "winning_token_id": "token-spurs",
            "payouts": {"token-spurs": "1"},
        },
        _settlement_snapshot(open_positions=[position]),
        issue_link="https://github.com/LucaCGN/janus_cortex/issues/58",
        post_redeem_recheck_plan="Recheck direct account and Janus ledger after redeem.",
        wallet_ready=True,
        chain_ready=True,
        signer_ready=True,
        gas_fee_ready=True,
        kill_switch_clear=True,
        ledger_available=True,
        janus_codex_approval=True,
        truth_sources=["direct_clob", "janus_api"],
        now_utc=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC),
    )
    ledger_write = write_settlement_ledger_prewrite(
        preview,
        ledger_root=tmp_path,
        written_at_utc=datetime(2026, 5, 19, 12, 1, 0, tzinfo=UTC),
    )

    reconciliation = build_post_redeem_reconciliation(
        preview,
        _settlement_snapshot(open_positions=[]),
        settlement_ledger_write=ledger_write,
        redemption_evidence={
            "transaction_hash": "0xredeem",
            "source": "operator-approved-janus-path",
        },
        now_utc=datetime(2026, 5, 19, 12, 5, 0, tzinfo=UTC),
    )
    missing_evidence = build_post_redeem_reconciliation(
        preview,
        _settlement_snapshot(open_positions=[]),
        settlement_ledger_write=ledger_write,
        now_utc=datetime(2026, 5, 19, 12, 6, 0, tzinfo=UTC),
    )

    assert reconciliation.schema_version == POST_REDEEM_RECONCILIATION_SCHEMA_VERSION
    assert reconciliation.status == "post_redeem_reconciled_flat"
    assert reconciliation.residual_cleared is True
    assert reconciliation.live_readiness_blocker is False
    assert reconciliation.matching_open_position_count == 0
    assert reconciliation.matching_open_order_count == 0
    assert reconciliation.transaction_preparation_attempted is False
    assert reconciliation.transaction_signing_attempted is False
    assert reconciliation.transaction_submission_attempted is False
    assert reconciliation.transaction_broadcast_attempted is False
    assert reconciliation.order_submission_attempted is False
    assert missing_evidence.status == "blocked_post_redeem_reconciliation"
    assert missing_evidence.residual_cleared is False
    assert "redemption_execution_evidence_missing" in missing_evidence.blockers


def test_post_redeem_reconciliation_blocks_when_redeemed_position_remains(tmp_path: Path) -> None:
    position = _redeemable_residual_position()
    preview = build_redeem_preview(
        position,
        {
            "resolved": True,
            "condition_id": "condition-okc",
            "market_slug": "nba-sas-okc-2026-05-18",
            "winning_token_id": "token-spurs",
            "payouts": {"token-spurs": "1"},
        },
        _settlement_snapshot(open_positions=[position]),
        issue_link="https://github.com/LucaCGN/janus_cortex/issues/58",
        post_redeem_recheck_plan="Recheck direct account and Janus ledger after redeem.",
        wallet_ready=True,
        chain_ready=True,
        signer_ready=True,
        gas_fee_ready=True,
        kill_switch_clear=True,
        ledger_available=True,
        janus_codex_approval=True,
        truth_sources=["direct_clob", "janus_api"],
        now_utc=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC),
    )
    ledger_write = write_settlement_ledger_prewrite(
        preview,
        ledger_root=tmp_path,
        written_at_utc=datetime(2026, 5, 19, 12, 1, 0, tzinfo=UTC),
    )
    reconciliation = build_post_redeem_reconciliation(
        preview,
        _settlement_snapshot(open_positions=[position]),
        settlement_ledger_write=ledger_write,
        redemption_evidence={"transaction_hash": "0xredeem"},
        now_utc=datetime(2026, 5, 19, 12, 5, 0, tzinfo=UTC),
    )

    assert reconciliation.status == "post_redeem_recheck_position_still_present"
    assert reconciliation.live_readiness_blocker is True
    assert reconciliation.residual_cleared is False
    assert reconciliation.matching_open_position_count == 1
    assert "redeemed_position_still_present" in reconciliation.blockers
    assert reconciliation.transaction_submission_attempted is False
    assert reconciliation.order_submission_attempted is False


def test_polymarket_cli_preview_redeem_outputs_zero_value_residual(capsys, tmp_path: Path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(_settlement_snapshot(open_positions=[_losing_residual_position()])),
        encoding="utf-8",
    )

    exit_code = polymarket_cli_main(
        [
            "preview-redeem",
            "--direct-truth-json",
            str(snapshot_path),
            "--position-token-id",
            "token-thunder",
            "--market-resolved",
            "--condition-id",
            "condition-okc",
            "--market-slug",
            "nba-sas-okc-2026-05-18",
            "--winning-token-id",
            "token-spurs",
            "--expected-payout-usd",
            "0",
            "--issue-link",
            "https://github.com/LucaCGN/janus_cortex/issues/58",
            "--post-redeem-recheck-plan",
            "Keep linked under issue #58 and recheck before settlement closure.",
            "--now-utc",
            "2026-05-19T12:00:00Z",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == REDEEM_PREVIEW_SCHEMA_VERSION
    assert payload["status"] == "no_redeem_needed_zero_value_residual"
    assert payload["residual_classification"]["residual_type"] == "zero_value_residual"
    assert payload["transaction_preparation_attempted"] is False
    assert payload["transaction_signing_attempted"] is False
    assert payload["transaction_submission_attempted"] is False
    assert payload["transaction_broadcast_attempted"] is False


def test_polymarket_cli_preview_redeem_can_write_settlement_ledger(capsys, tmp_path: Path) -> None:
    position = {
        "market_slug": "nba-sas-okc-2026-05-18",
        "condition_id": "condition-okc",
        "token_id": "token-spurs",
        "size": "7.5",
        "current_value": "7.5",
    }
    snapshot_path = tmp_path / "snapshot.json"
    ledger_root = tmp_path / "settlement-ledger"
    snapshot_path.write_text(
        json.dumps(_settlement_snapshot(open_positions=[position])),
        encoding="utf-8",
    )

    exit_code = polymarket_cli_main(
        [
            "preview-redeem",
            "--direct-truth-json",
            str(snapshot_path),
            "--position-token-id",
            "token-spurs",
            "--market-resolved",
            "--condition-id",
            "condition-okc",
            "--market-slug",
            "nba-sas-okc-2026-05-18",
            "--winning-token-id",
            "token-spurs",
            "--expected-payout-usd",
            "7.5",
            "--issue-link",
            "https://github.com/LucaCGN/janus_cortex/issues/58",
            "--post-redeem-recheck-plan",
            "Recheck direct account and Janus ledger after redeem.",
            "--wallet-ready",
            "--chain-ready",
            "--signer-ready",
            "--gas-fee-ready",
            "--kill-switch-clear",
            "--ledger-available",
            "--janus-codex-approval",
            "--truth-source",
            "direct_clob",
            "--truth-source",
            "janus_api",
            "--now-utc",
            "2026-05-19T12:00:00Z",
            "--write-settlement-ledger",
            "--settlement-ledger-root",
            str(ledger_root),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dry_run_redeem_preview_only"
    assert payload["settlement_ledger_write"]["status"] == "written"
    assert Path(payload["settlement_ledger_write"]["ledger_path"]).exists()
    assert payload["settlement_ledger_write"]["transaction_preparation_attempted"] is False
    assert payload["settlement_ledger_write"]["transaction_submission_attempted"] is False
    assert payload["transaction_broadcast_attempted"] is False
    assert payload["order_submission_attempted"] is False


def test_polymarket_cli_reconcile_redeem_reports_flat_without_execution(capsys, tmp_path: Path) -> None:
    position = _redeemable_residual_position()
    preview = build_redeem_preview(
        position,
        {
            "resolved": True,
            "condition_id": "condition-okc",
            "market_slug": "nba-sas-okc-2026-05-18",
            "winning_token_id": "token-spurs",
            "payouts": {"token-spurs": "1"},
        },
        _settlement_snapshot(open_positions=[position]),
        issue_link="https://github.com/LucaCGN/janus_cortex/issues/58",
        post_redeem_recheck_plan="Recheck direct account and Janus ledger after redeem.",
        wallet_ready=True,
        chain_ready=True,
        signer_ready=True,
        gas_fee_ready=True,
        kill_switch_clear=True,
        ledger_available=True,
        janus_codex_approval=True,
        truth_sources=["direct_clob", "janus_api"],
        now_utc=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC),
    )
    ledger_write = write_settlement_ledger_prewrite(
        preview,
        ledger_root=tmp_path / "settlement-ledger",
        written_at_utc=datetime(2026, 5, 19, 12, 1, 0, tzinfo=UTC),
    )
    preview_path = tmp_path / "preview.json"
    direct_truth_path = tmp_path / "direct-truth.json"
    ledger_write_path = tmp_path / "ledger-write.json"
    preview_path.write_text(json.dumps(asdict(preview)), encoding="utf-8")
    direct_truth_path.write_text(json.dumps(_settlement_snapshot(open_positions=[])), encoding="utf-8")
    ledger_write_path.write_text(json.dumps(asdict(ledger_write)), encoding="utf-8")

    exit_code = polymarket_cli_main(
        [
            "reconcile-redeem",
            "--redeem-preview-json",
            str(preview_path),
            "--direct-truth-json",
            str(direct_truth_path),
            "--settlement-ledger-write-json",
            str(ledger_write_path),
            "--redemption-tx-hash",
            "0xredeem",
            "--redemption-source",
            "operator-approved-janus-path",
            "--now-utc",
            "2026-05-19T12:05:00Z",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == POST_REDEEM_RECONCILIATION_SCHEMA_VERSION
    assert payload["status"] == "post_redeem_reconciled_flat"
    assert payload["residual_cleared"] is True
    assert payload["live_readiness_blocker"] is False
    assert payload["transaction_preparation_attempted"] is False
    assert payload["transaction_broadcast_attempted"] is False
    assert payload["order_submission_attempted"] is False


def test_polymarket_grid_service_preview_finds_global_and_other_basketball_candidates() -> None:
    preview = build_grid_service_preview(
        {
            "status": "read_only_snapshot",
            "open_positions": [
                {
                    "title": "Will aliens be confirmed in 2026?",
                    "market_slug": "aliens-confirmed-2026",
                    "token_id": "alien-yes",
                    "size": "76.93",
                    "average_price": "0.10",
                    "current_price": "0.15",
                },
                {
                    "title": "EuroLeague: Madrid vs Olympiacos",
                    "market_slug": "euroleague-madrid-oly-2026-05-20",
                    "token_id": "euro-underdog",
                    "size": "12",
                    "average_price": "0.42",
                    "current_value": "4.68",
                },
                {
                    "title": "Spurs vs. Thunder",
                    "market_slug": "nba-sas-okc-2026-05-18",
                    "token_id": "covered-nba",
                    "size": "20",
                    "average_price": "0.30",
                    "current_price": "0.43",
                },
            ],
            "open_orders": [{"token_id": "alien-yes", "price": "0.16"}],
        },
        now_utc=datetime(2026, 5, 19, 4, 0, 0, tzinfo=UTC),
        min_abs_pnl_percent="5",
    )

    assert preview.schema_version == GRID_SERVICE_SCHEMA_VERSION
    assert preview.status == "candidate_review_required"
    assert preview.service_spawn_authorized is False
    assert preview.order_preparation_attempted is False
    assert preview.order_submission_attempted is False
    assert {candidate["category"] for candidate in preview.candidates} == {"science_aliens", "other_basketball"}
    assert [candidate["token_id"] for candidate in preview.candidates] == ["alien-yes", "euro-underdog"]
    assert preview.candidates[0]["existing_open_order_count"] == 1
    assert preview.candidates[1]["current_price"] == "0.39"
    assert any(item["reason"] == "covered_basketball_managed_by_janus" for item in preview.skipped)


def test_polymarket_cli_preview_grid_service_outputs_inert_plan(tmp_path, capsys) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "status": "read_only_snapshot",
                "open_positions": [
                    {
                        "title": "Will an AI model pass benchmark X?",
                        "market_slug": "ai-model-benchmark-x",
                        "token_id": "ai-yes",
                        "size": "10",
                        "average_price": "0.40",
                        "current_price": "0.43",
                    }
                ],
                "open_orders": [],
            }
        ),
        encoding="utf-8",
    )

    exit_code = polymarket_cli_main(
        [
            "preview-grid-service",
            "--direct-truth-json",
            str(snapshot_path),
            "--now-utc",
            "2026-05-19T04:00:00Z",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == GRID_SERVICE_SCHEMA_VERSION
    assert payload["status"] == "candidate_review_required"
    assert payload["service_spawn_authorized"] is False
    assert payload["order_preparation_attempted"] is False
    assert payload["order_submission_attempted"] is False
