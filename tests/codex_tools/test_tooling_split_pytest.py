from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from codex_tool import _client as legacy_client
import codex_tool.build_replay_from_watch_session as legacy_replay_cli
import codex_tool.export_event_context as legacy_context_cli
import codex_tool.export_event_review_bundle as legacy_review_cli
import codex_tool.janus_status as legacy_status_cli
import codex_tool.live_strategy_worker_status as legacy_worker_status_cli
import codex_tool.start_live_strategy_worker as legacy_worker_start_cli
import codex_tool.stop_live_strategy_worker as legacy_worker_stop_cli
import codex_tool.watch_market as legacy_watch_cli
from codex_tools.janus import client as janus_client
from codex_tools.janus import events as janus_events
from codex_tools.janus import ops as janus_ops
from codex_tools.janus import status as janus_status
from codex_tools.janus import worker as janus_worker
from codex_tools.polymarket import (
    PREVIEW_SCHEMA_VERSION,
    PolymarketExecutionGateSnapshot,
    PolymarketFallbackIntent,
    build_fallback_preview,
    build_polymarket_safety_gate_snapshot,
    evaluate_direct_truth_freshness,
    evaluate_kill_switch,
    evaluate_minimum_order_policy,
    evaluate_risk_budget,
    read_account_snapshot,
    build_fallback_decision,
    write_fallback_decision_ledger,
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


def test_janus_worker_namespace_wraps_status_start_and_stop(monkeypatch) -> None:
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

    assert status == {"ok": True, "path": "/v1/ops/live-strategy-worker/status"}
    assert started == {"ok": True, "path": "/v1/ops/live-strategy-worker/start"}
    assert stopped == {"ok": True, "path": "/v1/ops/live-strategy-worker/stop"}
    assert calls == [
        ("http://janus.local", "GET", "/v1/ops/live-strategy-worker/status", None),
        (
            "http://janus.local",
            "POST",
            "/v1/ops/live-strategy-worker/start",
            {"event_ids": ["event-1"], "execute": False},
        ),
        ("http://janus.local", "POST", "/v1/ops/live-strategy-worker/stop", {}),
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


def test_safety_gate_snapshot_blocks_without_integrated_gate_inputs() -> None:
    intent = _intent(dry_run=False)
    gate = build_polymarket_safety_gate_snapshot(
        intent,
        direct_truth_snapshot=_fresh_snapshot(),
        now_utc=datetime(2026, 5, 18, 14, 20, 30, tzinfo=UTC),
        risk_budget_name="global-portfolio-test",
        risk_budget_max_notional_usd=10.0,
        kill_switch_clear=False,
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
