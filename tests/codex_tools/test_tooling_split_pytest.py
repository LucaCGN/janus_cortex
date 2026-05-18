from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from codex_tool import _client as legacy_client
from codex_tools.janus import client as janus_client
from codex_tools.polymarket import (
    PolymarketExecutionGateSnapshot,
    PolymarketFallbackIntent,
    build_fallback_decision,
    write_fallback_decision_ledger,
)


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


def test_janus_client_namespace_preserves_legacy_api_client() -> None:
    assert janus_client.DEFAULT_API_ROOT == legacy_client.DEFAULT_API_ROOT
    assert janus_client.api_json is legacy_client.api_json
    assert janus_client.base_parser is legacy_client.base_parser


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
