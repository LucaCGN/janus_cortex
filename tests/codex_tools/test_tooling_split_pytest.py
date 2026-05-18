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
    read_account_snapshot,
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


class _Creds:
    def __init__(self, *, wallet_address: str = "", private_key: str | None = None) -> None:
        self.wallet_address = wallet_address
        self.funder_address = wallet_address
        self.private_key = private_key


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
