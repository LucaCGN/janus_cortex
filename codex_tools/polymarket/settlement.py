"""Resolved-market residual classification and redeem previews.

This module is deliberately inert. It classifies resolved-market account rows
and builds redemption gate previews, but it never prepares, signs, submits, or
broadcasts transactions and never places CLOB orders.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.runtime.local_paths import resolve_shared_root
from codex_tools.polymarket.execution_gate import NO_EXECUTION_STATEMENT
from codex_tools.polymarket.safety import NON_AUTHORITATIVE_TRUTH_SOURCES

RESIDUAL_CLASSIFICATION_SCHEMA_VERSION = "polymarket_residual_classification_v1"
REDEEM_PREVIEW_SCHEMA_VERSION = "polymarket_redeem_preview_v1"
POST_REDEEM_RECONCILIATION_SCHEMA_VERSION = "polymarket_post_redeem_reconciliation_v1"
SETTLEMENT_LEDGER_ENTRY_SCHEMA_VERSION = "polymarket_settlement_ledger_entry_v1"
SETTLEMENT_LEDGER_WRITE_SCHEMA_VERSION = "polymarket_settlement_ledger_write_v1"
SETTLEMENT_LEDGER_FILE_NAME = "settlement_ledger.jsonl"
NO_REDEMPTION_STATEMENT = (
    "No redemption transaction was prepared, signed, submitted, or broadcast. "
    + NO_EXECUTION_STATEMENT
)

REQUIRED_REDEEM_GATE_LABELS = {
    "residual_classified": "documented residual classification from direct truth",
    "fresh_direct_truth": "fresh direct account/CLOB truth",
    "market_resolution_proven": "resolved market, token, outcome, and payout proof",
    "no_event_scoped_open_orders": "no event-scoped open orders or fill ambiguity",
    "wallet_chain_signer_ready": "wallet, chain, signer, gas, and fee readiness",
    "kill_switch_clear": "kill switch clear",
    "ledger_idempotency_available": "settlement ledger idempotency key available",
    "janus_codex_approval": "explicit Janus+Codex redemption approval gate",
    "post_redeem_recheck_plan_present": "post-redeem direct-truth reconciliation plan",
    "non_authoritative_truth_rejected": "screenshots/chat/Obsidian/GitHub/stale mirrors rejected as truth",
}


@dataclass(frozen=True)
class PolymarketResidualClassification:
    schema_version: str
    status: str
    residual_type: str
    live_readiness_blocker: bool
    token_id: str
    condition_id: str
    market_slug: str
    size: str | None
    current_value_usd: str | None
    expected_payout_usd: str | None
    matching_open_order_count: int
    blockers: list[str]
    evidence: dict[str, Any]
    order_preparation_attempted: bool
    order_submission_attempted: bool
    redemption_preparation_attempted: bool
    redemption_submission_attempted: bool
    no_execution_statement: str


@dataclass(frozen=True)
class PolymarketRedeemPreview:
    schema_version: str
    status: str
    dry_run: bool
    redemption_authorized: bool
    residual_classification: dict[str, Any]
    missing_gates: list[str]
    required_gates: dict[str, str]
    expected_payout_usd: str | None
    idempotency_key: str
    ledger_write_required_before_submission: bool
    transaction_preparation_attempted: bool
    transaction_signing_attempted: bool
    transaction_submission_attempted: bool
    transaction_broadcast_attempted: bool
    order_preparation_attempted: bool
    order_submission_attempted: bool
    gate_snapshot: dict[str, Any]
    no_execution_statement: str


@dataclass(frozen=True)
class PolymarketSettlementLedgerWrite:
    schema_version: str
    status: str
    ledger_path: str
    ledger_id: str
    idempotency_key: str
    created: bool
    transaction_preparation_attempted: bool
    transaction_signing_attempted: bool
    transaction_submission_attempted: bool
    transaction_broadcast_attempted: bool
    order_preparation_attempted: bool
    order_submission_attempted: bool
    no_execution_statement: str


@dataclass(frozen=True)
class PolymarketPostRedeemReconciliation:
    schema_version: str
    status: str
    residual_cleared: bool
    live_readiness_blocker: bool
    token_id: str
    condition_id: str
    market_slug: str
    matching_open_order_count: int
    matching_open_position_count: int
    blockers: list[str]
    evidence: dict[str, Any]
    transaction_preparation_attempted: bool
    transaction_signing_attempted: bool
    transaction_submission_attempted: bool
    transaction_broadcast_attempted: bool
    order_preparation_attempted: bool
    order_submission_attempted: bool
    no_execution_statement: str


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite():
        return None
    return result


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _text(item.get(key))
        if value:
            return value
    return ""


def _token_id(item: dict[str, Any]) -> str:
    return _first_text(item, ("token_id", "asset_id", "asset", "outcomeTokenId", "clobTokenId"))


def _condition_id(item: dict[str, Any]) -> str:
    return _first_text(item, ("condition_id", "conditionId", "condition", "market_condition_id"))


def _market_slug(item: dict[str, Any]) -> str:
    return _first_text(item, ("market_slug", "event_slug", "slug", "market"))


def _position_size(position: dict[str, Any]) -> Decimal | None:
    for key in ("size", "quantity", "shares", "balance"):
        value = _decimal(position.get(key))
        if value is not None:
            return value
    return None


def _current_value(position: dict[str, Any]) -> Decimal | None:
    explicit = _decimal(position.get("current_value") or position.get("currentValue") or position.get("value"))
    if explicit is not None:
        return explicit
    current_price = _decimal(position.get("current_price") or position.get("cur_price") or position.get("price"))
    size = _position_size(position)
    if current_price is None or size is None:
        return None
    return current_price * size


def _resolution_payout_per_share(resolution: dict[str, Any], token_id: str) -> Decimal | None:
    explicit = _decimal(resolution.get("payout_per_share") or resolution.get("payout"))
    if explicit is not None:
        return explicit
    payouts = resolution.get("payouts") or resolution.get("token_payouts") or {}
    if isinstance(payouts, dict):
        return _decimal(payouts.get(token_id))
    return None


def _expected_payout(position: dict[str, Any], resolution: dict[str, Any], token_id: str) -> Decimal | None:
    explicit = _decimal(
        resolution.get("expected_payout_usd")
        or resolution.get("expected_proceeds_usd")
        or position.get("expected_payout_usd")
        or position.get("expected_proceeds_usd")
    )
    if explicit is not None:
        return max(Decimal("0"), explicit)
    size = _position_size(position)
    payout_per_share = _resolution_payout_per_share(resolution, token_id)
    if size is None or payout_per_share is None:
        return None
    return max(Decimal("0"), size * payout_per_share)


def _format_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize()
    return format(normalized, "f")


def _open_orders(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return []
    return [item for item in (snapshot.get("open_orders") or []) if isinstance(item, dict)]


def _open_positions(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return []
    return [item for item in (snapshot.get("open_positions") or []) if isinstance(item, dict)]


def _matching_open_orders(
    snapshot: dict[str, Any] | None,
    *,
    token_id: str,
    condition_id: str,
    market_slug: str,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for order in _open_orders(snapshot):
        order_token = _token_id(order)
        order_condition = _condition_id(order)
        order_market = _market_slug(order)
        if token_id and order_token == token_id:
            matches.append(order)
        elif condition_id and order_condition == condition_id:
            matches.append(order)
        elif market_slug and order_market == market_slug:
            matches.append(order)
    return matches


def _matching_open_positions(
    snapshot: dict[str, Any] | None,
    *,
    token_id: str,
    condition_id: str,
    market_slug: str,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for position in _open_positions(snapshot):
        position_token = _token_id(position)
        position_condition = _condition_id(position)
        position_market = _market_slug(position)
        if token_id and position_token == token_id:
            matches.append(position)
        elif condition_id and position_condition == condition_id:
            matches.append(position)
        elif market_slug and position_market == market_slug:
            matches.append(position)
    return matches


def _snapshot_status(snapshot: dict[str, Any] | None) -> str:
    if not isinstance(snapshot, dict):
        return ""
    return _text(snapshot.get("status"))


def _section_status(snapshot: dict[str, Any] | None, section: str) -> str:
    if not isinstance(snapshot, dict):
        return ""
    section_status = snapshot.get("section_status") or {}
    if not isinstance(section_status, dict):
        return ""
    return _text(section_status.get(section))


def _has_documented_owner(
    *,
    issue_link: str | None,
    ledger_link: str | None,
    post_redeem_recheck_plan: str | dict[str, Any] | None,
) -> bool:
    return bool((_text(issue_link) or _text(ledger_link)) and post_redeem_recheck_plan)


def _truth_sources_ok(truth_sources: list[str] | None) -> tuple[bool, list[str]]:
    normalized = sorted({source.strip().lower() for source in truth_sources or [] if source.strip()})
    rejected = [source for source in normalized if source in NON_AUTHORITATIVE_TRUTH_SOURCES]
    return not rejected, rejected


def default_settlement_ledger_root() -> Path:
    return (
        resolve_shared_root()
        / "artifacts"
        / "codex-tools"
        / "polymarket"
        / "settlement-ledger"
    )


def classify_resolved_market_residual(
    position: dict[str, Any],
    resolved_market: dict[str, Any],
    direct_truth_snapshot: dict[str, Any] | None,
    *,
    issue_link: str | None = None,
    ledger_link: str | None = None,
    post_redeem_recheck_plan: str | dict[str, Any] | None = None,
) -> PolymarketResidualClassification:
    """Classify a resolved-market row without treating it as active exposure."""

    blockers: list[str] = []
    token_id = _token_id(position)
    condition_id = _condition_id(resolved_market) or _condition_id(position)
    market_slug = _market_slug(resolved_market) or _market_slug(position)
    size = _position_size(position)
    current_value = _current_value(position)
    expected_payout = _expected_payout(position, resolved_market, token_id)

    if not token_id:
        blockers.append("token_id_missing")
    if not condition_id:
        blockers.append("condition_id_missing")
    if not bool(resolved_market.get("resolved")):
        blockers.append("market_resolution_not_proven")
    if _snapshot_status(direct_truth_snapshot) != "read_only_snapshot":
        blockers.append("direct_truth_snapshot_not_complete")
    if _section_status(direct_truth_snapshot, "open_positions") != "ok":
        blockers.append("direct_open_position_truth_unavailable")
    if _section_status(direct_truth_snapshot, "open_orders") != "ok":
        blockers.append("direct_open_order_truth_unavailable")
    matching_open_orders = _matching_open_orders(
        direct_truth_snapshot,
        token_id=token_id,
        condition_id=condition_id,
        market_slug=market_slug,
    )
    if matching_open_orders:
        blockers.append("event_scoped_open_orders_present")
    if expected_payout is None:
        blockers.append("expected_payout_missing")
    if not _has_documented_owner(
        issue_link=issue_link,
        ledger_link=ledger_link,
        post_redeem_recheck_plan=post_redeem_recheck_plan,
    ):
        blockers.append("residual_issue_or_ledger_link_and_recheck_plan_missing")

    if blockers:
        residual_type = "unknown_settlement_state"
        status = "blocked_residual_classification"
        live_readiness_blocker = True
    elif expected_payout == 0 and (current_value is None or current_value == 0):
        residual_type = "zero_value_residual"
        status = "documented_zero_value_residual"
        live_readiness_blocker = False
    elif expected_payout > 0:
        residual_type = "redeemable_residual"
        status = "documented_redeemable_residual"
        live_readiness_blocker = False
    else:
        residual_type = "unknown_settlement_state"
        status = "blocked_residual_classification"
        live_readiness_blocker = True
        blockers.append("residual_value_not_classifiable")

    return PolymarketResidualClassification(
        schema_version=RESIDUAL_CLASSIFICATION_SCHEMA_VERSION,
        status=status,
        residual_type=residual_type,
        live_readiness_blocker=live_readiness_blocker,
        token_id=token_id,
        condition_id=condition_id,
        market_slug=market_slug,
        size=_format_decimal(size),
        current_value_usd=_format_decimal(current_value),
        expected_payout_usd=_format_decimal(expected_payout),
        matching_open_order_count=len(matching_open_orders),
        blockers=blockers,
        evidence={
            "issue_link": issue_link,
            "ledger_link": ledger_link,
            "post_redeem_recheck_plan_present": bool(post_redeem_recheck_plan),
            "direct_truth_status": _snapshot_status(direct_truth_snapshot),
            "direct_open_position_section_status": _section_status(direct_truth_snapshot, "open_positions"),
            "direct_open_order_section_status": _section_status(direct_truth_snapshot, "open_orders"),
            "matching_open_order_ids": [
                _first_text(order, ("id", "order_id", "external_order_id", "hash"))
                for order in matching_open_orders
            ],
            "resolved_market": dict(resolved_market),
        },
        order_preparation_attempted=False,
        order_submission_attempted=False,
        redemption_preparation_attempted=False,
        redemption_submission_attempted=False,
        no_execution_statement=NO_REDEMPTION_STATEMENT,
    )


def derive_redeem_idempotency_key(classification: PolymarketResidualClassification) -> str:
    canonical = json.dumps(
        {
            "condition_id": classification.condition_id,
            "expected_payout_usd": classification.expected_payout_usd,
            "market_slug": classification.market_slug,
            "residual_type": classification.residual_type,
            "size": classification.size,
            "token_id": classification.token_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "redeem-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def build_redeem_preview(
    position: dict[str, Any],
    resolved_market: dict[str, Any],
    direct_truth_snapshot: dict[str, Any] | None,
    *,
    dry_run: bool = True,
    issue_link: str | None = None,
    ledger_link: str | None = None,
    post_redeem_recheck_plan: str | dict[str, Any] | None = None,
    wallet_ready: bool = False,
    chain_ready: bool = False,
    signer_ready: bool = False,
    gas_fee_ready: bool = False,
    kill_switch_clear: bool = False,
    ledger_available: bool = False,
    janus_codex_approval: bool = False,
    truth_sources: list[str] | None = None,
    now_utc: datetime | str | None = None,
) -> PolymarketRedeemPreview:
    """Build a non-executing redemption preview and gate snapshot."""

    classification = classify_resolved_market_residual(
        position,
        resolved_market,
        direct_truth_snapshot,
        issue_link=issue_link,
        ledger_link=ledger_link,
        post_redeem_recheck_plan=post_redeem_recheck_plan,
    )
    truth_sources_clean, rejected_truth_sources = _truth_sources_ok(truth_sources)
    wallet_chain_ready = bool(wallet_ready and chain_ready and signer_ready and gas_fee_ready)
    idempotency_key = derive_redeem_idempotency_key(classification)
    now_text = _coerce_utc(now_utc).isoformat().replace("+00:00", "Z")

    gate_snapshot = {
        "generated_at_utc": now_text,
        "residual_classified": classification.live_readiness_blocker is False,
        "fresh_direct_truth": _snapshot_status(direct_truth_snapshot) == "read_only_snapshot",
        "market_resolution_proven": bool(resolved_market.get("resolved")),
        "no_event_scoped_open_orders": classification.matching_open_order_count == 0,
        "wallet_chain_signer_ready": wallet_chain_ready,
        "kill_switch_clear": bool(kill_switch_clear),
        "ledger_idempotency_available": bool(ledger_available and idempotency_key),
        "janus_codex_approval": bool(janus_codex_approval),
        "post_redeem_recheck_plan_present": bool(post_redeem_recheck_plan),
        "non_authoritative_truth_rejected": truth_sources_clean,
        "wallet_ready": bool(wallet_ready),
        "chain_ready": bool(chain_ready),
        "signer_ready": bool(signer_ready),
        "gas_fee_ready": bool(gas_fee_ready),
        "truth_sources": sorted({source.strip().lower() for source in truth_sources or [] if source.strip()}),
        "rejected_truth_sources": rejected_truth_sources,
        "transaction_preparation_attempted": False,
        "transaction_signing_attempted": False,
        "transaction_submission_attempted": False,
        "transaction_broadcast_attempted": False,
        "order_preparation_attempted": False,
        "order_submission_attempted": False,
        "no_execution_statement": NO_REDEMPTION_STATEMENT,
    }
    missing_gates = [
        name for name in REQUIRED_REDEEM_GATE_LABELS if not bool(gate_snapshot.get(name))
    ]

    if classification.status == "blocked_residual_classification":
        status = "blocked_residual_classification"
        redemption_authorized = False
    elif classification.residual_type == "zero_value_residual":
        status = "no_redeem_needed_zero_value_residual"
        redemption_authorized = False
        missing_gates = []
    elif missing_gates:
        status = "blocked_missing_redemption_gates"
        redemption_authorized = False
    elif dry_run:
        status = "dry_run_redeem_preview_only"
        redemption_authorized = False
    else:
        status = "ready_for_approved_redemption_path"
        redemption_authorized = True

    return PolymarketRedeemPreview(
        schema_version=REDEEM_PREVIEW_SCHEMA_VERSION,
        status=status,
        dry_run=dry_run,
        redemption_authorized=redemption_authorized,
        residual_classification=asdict(classification),
        missing_gates=missing_gates,
        required_gates=REQUIRED_REDEEM_GATE_LABELS.copy(),
        expected_payout_usd=classification.expected_payout_usd,
        idempotency_key=idempotency_key,
        ledger_write_required_before_submission=True,
        transaction_preparation_attempted=False,
        transaction_signing_attempted=False,
        transaction_submission_attempted=False,
        transaction_broadcast_attempted=False,
        order_preparation_attempted=False,
        order_submission_attempted=False,
        gate_snapshot=gate_snapshot,
        no_execution_statement=NO_REDEMPTION_STATEMENT,
    )


def _dataclass_or_mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    return {}


def _redemption_evidence_id(redemption_evidence: dict[str, Any] | None) -> str:
    if not isinstance(redemption_evidence, dict):
        return ""
    return _first_text(
        redemption_evidence,
        (
            "transaction_hash",
            "tx_hash",
            "redeem_transaction_hash",
            "external_redeem_id",
            "settlement_tx_hash",
        ),
    )


def build_post_redeem_reconciliation(
    redeem_preview: PolymarketRedeemPreview | dict[str, Any],
    direct_truth_snapshot: dict[str, Any] | None,
    *,
    settlement_ledger_write: PolymarketSettlementLedgerWrite | dict[str, Any] | None = None,
    redemption_evidence: dict[str, Any] | None = None,
    now_utc: datetime | str | None = None,
) -> PolymarketPostRedeemReconciliation:
    """Evaluate post-redeem direct-truth state without doing redemption work."""

    preview = _dataclass_or_mapping(redeem_preview)
    ledger_write = _dataclass_or_mapping(settlement_ledger_write)
    classification = preview.get("residual_classification")
    if not isinstance(classification, dict):
        classification = {}

    token_id = _text(classification.get("token_id"))
    condition_id = _text(classification.get("condition_id"))
    market_slug = _text(classification.get("market_slug"))
    idempotency_key = _text(preview.get("idempotency_key"))
    expected_ledger_id = f"settlement-{idempotency_key}" if idempotency_key else ""
    matching_open_orders = _matching_open_orders(
        direct_truth_snapshot,
        token_id=token_id,
        condition_id=condition_id,
        market_slug=market_slug,
    )
    matching_open_positions = _matching_open_positions(
        direct_truth_snapshot,
        token_id=token_id,
        condition_id=condition_id,
        market_slug=market_slug,
    )

    blockers: list[str] = []
    if classification.get("status") == "blocked_residual_classification":
        blockers.append("redeem_preview_residual_classification_blocked")
    if classification.get("residual_type") != "redeemable_residual":
        blockers.append("redeem_preview_not_redeemable_residual")
    if bool(preview.get("missing_gates")):
        blockers.append("redeem_preview_missing_gates")
    if not idempotency_key:
        blockers.append("redeem_preview_idempotency_key_missing")
    if _snapshot_status(direct_truth_snapshot) != "read_only_snapshot":
        blockers.append("direct_truth_snapshot_not_complete")
    if _section_status(direct_truth_snapshot, "open_positions") != "ok":
        blockers.append("direct_open_position_truth_unavailable")
    if _section_status(direct_truth_snapshot, "open_orders") != "ok":
        blockers.append("direct_open_order_truth_unavailable")
    if matching_open_orders:
        blockers.append("event_scoped_open_orders_present")
    if matching_open_positions:
        blockers.append("redeemed_position_still_present")
    if not ledger_write:
        blockers.append("settlement_ledger_prewrite_missing")
    else:
        ledger_status = _text(ledger_write.get("status"))
        ledger_id = _text(ledger_write.get("ledger_id"))
        ledger_idempotency_key = _text(ledger_write.get("idempotency_key"))
        if ledger_status not in {"written", "already_recorded"}:
            blockers.append("settlement_ledger_prewrite_not_recorded")
        if expected_ledger_id and ledger_id != expected_ledger_id:
            blockers.append("settlement_ledger_id_mismatch")
        if idempotency_key and ledger_idempotency_key != idempotency_key:
            blockers.append("settlement_ledger_idempotency_key_mismatch")
    if not _redemption_evidence_id(redemption_evidence):
        blockers.append("redemption_execution_evidence_missing")

    if not blockers:
        status = "post_redeem_reconciled_flat"
        residual_cleared = True
        live_readiness_blocker = False
    elif "redeemed_position_still_present" in blockers:
        status = "post_redeem_recheck_position_still_present"
        residual_cleared = False
        live_readiness_blocker = True
    elif "event_scoped_open_orders_present" in blockers:
        status = "post_redeem_recheck_open_order_present"
        residual_cleared = False
        live_readiness_blocker = True
    else:
        status = "blocked_post_redeem_reconciliation"
        residual_cleared = False
        live_readiness_blocker = True

    return PolymarketPostRedeemReconciliation(
        schema_version=POST_REDEEM_RECONCILIATION_SCHEMA_VERSION,
        status=status,
        residual_cleared=residual_cleared,
        live_readiness_blocker=live_readiness_blocker,
        token_id=token_id,
        condition_id=condition_id,
        market_slug=market_slug,
        matching_open_order_count=len(matching_open_orders),
        matching_open_position_count=len(matching_open_positions),
        blockers=blockers,
        evidence={
            "checked_at_utc": _coerce_utc(now_utc).isoformat().replace("+00:00", "Z"),
            "redeem_preview_status": preview.get("status"),
            "redeem_preview_idempotency_key": idempotency_key,
            "settlement_ledger_id": ledger_write.get("ledger_id") if ledger_write else None,
            "redemption_evidence_present": bool(_redemption_evidence_id(redemption_evidence)),
            "redemption_evidence": dict(redemption_evidence or {}),
            "direct_truth_status": _snapshot_status(direct_truth_snapshot),
            "direct_open_position_section_status": _section_status(direct_truth_snapshot, "open_positions"),
            "direct_open_order_section_status": _section_status(direct_truth_snapshot, "open_orders"),
            "matching_open_order_ids": [
                _first_text(order, ("id", "order_id", "external_order_id", "hash"))
                for order in matching_open_orders
            ],
            "matching_open_position_ids": [
                _first_text(position, ("id", "position_id", "token_id", "asset_id", "asset"))
                for position in matching_open_positions
            ],
        },
        transaction_preparation_attempted=False,
        transaction_signing_attempted=False,
        transaction_submission_attempted=False,
        transaction_broadcast_attempted=False,
        order_preparation_attempted=False,
        order_submission_attempted=False,
        no_execution_statement=NO_REDEMPTION_STATEMENT,
    )


def classify_documented_residual_positions(
    *,
    open_orders: list[dict[str, Any]],
    open_positions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Split direct open positions into active exposure and documented residuals."""

    direct_truth_snapshot = {
        "status": "read_only_snapshot",
        "section_status": {"open_orders": "ok", "open_positions": "ok"},
        "open_orders": open_orders,
        "open_positions": open_positions,
    }
    active_open_positions: list[dict[str, Any]] = []
    documented_residual_positions: list[dict[str, Any]] = []
    blocked_residual_classifications: list[dict[str, Any]] = []
    for position in open_positions:
        residual_metadata = extract_settlement_residual_metadata(position)
        if not residual_metadata:
            active_open_positions.append(position)
            continue
        classification = classify_resolved_market_residual(
            position,
            residual_metadata["resolved_market"],
            direct_truth_snapshot,
            issue_link=residual_metadata.get("issue_link"),
            ledger_link=residual_metadata.get("ledger_link"),
            post_redeem_recheck_plan=residual_metadata.get("post_redeem_recheck_plan"),
        )
        classification_payload = asdict(classification)
        if classification.live_readiness_blocker:
            blocked_residual_classifications.append(
                {"position": position, "classification": classification_payload}
            )
            active_open_positions.append(position)
        else:
            documented_residual_positions.append(
                {"position": position, "classification": classification_payload}
            )
    return {
        "active_open_positions": active_open_positions,
        "documented_residual_positions": documented_residual_positions,
        "blocked_residual_classifications": blocked_residual_classifications,
    }


def extract_settlement_residual_metadata(position: dict[str, Any]) -> dict[str, Any] | None:
    residual = position.get("settlement_residual")
    residual_metadata = residual if isinstance(residual, dict) else {}
    resolved_market = residual_metadata.get("resolved_market")
    if not isinstance(resolved_market, dict):
        resolved_market = position.get("resolved_market")
    if not isinstance(resolved_market, dict):
        resolved_market = _resolved_market_from_position(position, residual_metadata)
    if not isinstance(resolved_market, dict):
        return None

    issue_link = (
        residual_metadata.get("issue_link")
        or residual_metadata.get("github_issue")
        or position.get("settlement_issue_link")
        or position.get("issue_link")
    )
    ledger_link = (
        residual_metadata.get("ledger_link")
        or residual_metadata.get("settlement_ledger_link")
        or position.get("settlement_ledger_link")
        or position.get("ledger_link")
    )
    post_redeem_recheck_plan = (
        residual_metadata.get("post_redeem_recheck_plan")
        or residual_metadata.get("recheck_plan")
        or position.get("post_redeem_recheck_plan")
        or position.get("settlement_recheck_plan")
    )
    return {
        "resolved_market": resolved_market,
        "issue_link": issue_link,
        "ledger_link": ledger_link,
        "post_redeem_recheck_plan": post_redeem_recheck_plan,
    }


def _resolved_market_from_position(position: dict[str, Any], residual_metadata: dict[str, Any]) -> dict[str, Any] | None:
    if not bool(
        residual_metadata.get("market_resolved")
        or residual_metadata.get("resolved")
        or position.get("market_resolved")
        or position.get("resolved")
    ):
        return None
    token_id = _token_id(position)
    payout = residual_metadata.get("payout_per_share")
    if payout is None:
        payout = position.get("payout_per_share")
    payouts = residual_metadata.get("payouts") or position.get("payouts")
    if not isinstance(payouts, dict) and token_id and payout is not None:
        payouts = {token_id: payout}
    resolved_market = {
        "resolved": True,
        "condition_id": residual_metadata.get("condition_id") or position.get("condition_id") or position.get("market"),
        "market_slug": residual_metadata.get("market_slug") or position.get("market_slug") or position.get("event_slug"),
        "winning_token_id": residual_metadata.get("winning_token_id") or position.get("winning_token_id"),
    }
    if isinstance(payouts, dict):
        resolved_market["payouts"] = payouts
    expected_payout = residual_metadata.get("expected_payout_usd") or position.get("expected_payout_usd")
    if expected_payout is not None:
        resolved_market["expected_payout_usd"] = expected_payout
    return resolved_market


def _settlement_ledger_id(preview: PolymarketRedeemPreview) -> str:
    return "settlement-" + preview.idempotency_key


def _existing_settlement_ledger_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()

    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        ledger_id = entry.get("ledger_id")
        idempotency_key = entry.get("idempotency_key")
        if isinstance(ledger_id, str):
            keys.add(ledger_id)
        if isinstance(idempotency_key, str):
            keys.add(idempotency_key)
    return keys


def build_settlement_ledger_entry(
    preview: PolymarketRedeemPreview,
    *,
    written_at_utc: datetime | str | None = None,
    source_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an inert settlement ledger entry for a redeem preview."""

    timestamp = _coerce_utc(written_at_utc).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": SETTLEMENT_LEDGER_ENTRY_SCHEMA_VERSION,
        "written_at_utc": timestamp,
        "entry_type": "redeem_preview_prewrite",
        "ledger_id": _settlement_ledger_id(preview),
        "idempotency_key": preview.idempotency_key,
        "status": "prewrite_recorded",
        "redeem_preview_status": preview.status,
        "redemption_authorized": preview.redemption_authorized,
        "dry_run": preview.dry_run,
        "expected_payout_usd": preview.expected_payout_usd,
        "ledger_write_required_before_submission": preview.ledger_write_required_before_submission,
        "source_evidence": dict(source_evidence or {}),
        "transaction_preparation_attempted": False,
        "transaction_signing_attempted": False,
        "transaction_submission_attempted": False,
        "transaction_broadcast_attempted": False,
        "order_preparation_attempted": False,
        "order_submission_attempted": False,
        "no_execution_statement": NO_REDEMPTION_STATEMENT,
        "redeem_preview": asdict(preview),
    }


def write_settlement_ledger_prewrite(
    preview: PolymarketRedeemPreview,
    *,
    ledger_root: Path | None = None,
    written_at_utc: datetime | str | None = None,
    source_evidence: dict[str, Any] | None = None,
) -> PolymarketSettlementLedgerWrite:
    """Persist a pre-redeem ledger row without preparing any transaction."""

    timestamp = _coerce_utc(written_at_utc)
    root = ledger_root if ledger_root is not None else default_settlement_ledger_root()
    ledger_dir = Path(root) / timestamp.date().isoformat()
    ledger_path = ledger_dir / SETTLEMENT_LEDGER_FILE_NAME
    ledger_id = _settlement_ledger_id(preview)

    ledger_dir.mkdir(parents=True, exist_ok=True)
    existing_keys = _existing_settlement_ledger_keys(ledger_path)
    if ledger_id in existing_keys or preview.idempotency_key in existing_keys:
        return PolymarketSettlementLedgerWrite(
            schema_version=SETTLEMENT_LEDGER_WRITE_SCHEMA_VERSION,
            status="already_recorded",
            ledger_path=str(ledger_path),
            ledger_id=ledger_id,
            idempotency_key=preview.idempotency_key,
            created=False,
            transaction_preparation_attempted=False,
            transaction_signing_attempted=False,
            transaction_submission_attempted=False,
            transaction_broadcast_attempted=False,
            order_preparation_attempted=False,
            order_submission_attempted=False,
            no_execution_statement=NO_REDEMPTION_STATEMENT,
        )

    entry = build_settlement_ledger_entry(
        preview,
        written_at_utc=timestamp,
        source_evidence=source_evidence,
    )
    with ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")))
        handle.write("\n")

    return PolymarketSettlementLedgerWrite(
        schema_version=SETTLEMENT_LEDGER_WRITE_SCHEMA_VERSION,
        status="written",
        ledger_path=str(ledger_path),
        ledger_id=ledger_id,
        idempotency_key=preview.idempotency_key,
        created=True,
        transaction_preparation_attempted=False,
        transaction_signing_attempted=False,
        transaction_submission_attempted=False,
        transaction_broadcast_attempted=False,
        order_preparation_attempted=False,
        order_submission_attempted=False,
        no_execution_statement=NO_REDEMPTION_STATEMENT,
    )


def _coerce_utc(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


__all__ = [
    "NO_REDEMPTION_STATEMENT",
    "POST_REDEEM_RECONCILIATION_SCHEMA_VERSION",
    "REDEEM_PREVIEW_SCHEMA_VERSION",
    "REQUIRED_REDEEM_GATE_LABELS",
    "RESIDUAL_CLASSIFICATION_SCHEMA_VERSION",
    "SETTLEMENT_LEDGER_ENTRY_SCHEMA_VERSION",
    "SETTLEMENT_LEDGER_FILE_NAME",
    "SETTLEMENT_LEDGER_WRITE_SCHEMA_VERSION",
    "PolymarketPostRedeemReconciliation",
    "PolymarketRedeemPreview",
    "PolymarketResidualClassification",
    "PolymarketSettlementLedgerWrite",
    "build_post_redeem_reconciliation",
    "build_settlement_ledger_entry",
    "build_redeem_preview",
    "classify_documented_residual_positions",
    "classify_resolved_market_residual",
    "default_settlement_ledger_root",
    "derive_redeem_idempotency_key",
    "extract_settlement_residual_metadata",
    "write_settlement_ledger_prewrite",
]
