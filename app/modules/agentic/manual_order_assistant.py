from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.modules.agentic.contracts import ManualClobOrderAssistantRequest


def build_manual_clob_order_assistant_review(
    payload: ManualClobOrderAssistantRequest,
    *,
    event_id: str,
    matched_outcome: dict[str, Any] | None,
    orderbook: dict[str, Any] | None,
    inventory: dict[str, Any] | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a deterministic safety review for manual/Codex CLOB order intent."""

    now = now_utc or datetime.now(timezone.utc)
    matched = dict(matched_outcome or {})
    book = dict(orderbook or {})
    inventory_snapshot = dict(inventory or {})
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    _validate_event_outcome(payload, event_id=event_id, matched_outcome=matched, blockers=blockers)
    if payload.execute and not str(payload.account_id or "").strip():
        blockers.append({"reason": "account_id_required_for_execute"})
    _validate_order_type_policy(payload, blockers=blockers)
    _validate_price_and_notional(payload, blockers=blockers)
    _validate_orderbook(payload, orderbook=book, now_utc=now, blockers=blockers, warnings=warnings)
    _validate_inventory(payload, inventory=inventory_snapshot, blockers=blockers, warnings=warnings)

    price = payload.limit_price if payload.limit_price is not None else _side_reference_price(payload, book)
    notional = round((float(price or 0.0) * float(payload.size)), 6)
    status = "blocked" if blockers else ("approved_for_execute" if payload.execute else "preview_ready")
    order_payload = {
        "event_id": event_id,
        "account_id": payload.account_id,
        "market_id": payload.market_id,
        "outcome_id": payload.outcome_id,
        "token_id": payload.token_id,
        "side": payload.side,
        "order_type": payload.order_type,
        "limit_price": payload.limit_price,
        "size": payload.size,
        "time_in_force": payload.time_in_force,
        "notional_usd": notional,
        "execute": payload.execute,
    }
    metadata = build_manual_clob_order_metadata(
        payload,
        event_id=event_id,
        order_payload=order_payload,
        status=status,
        blockers=blockers,
        orderbook=book,
        inventory=inventory_snapshot,
    )
    return {
        "schema_version": "manual_clob_order_assistant_review_v1",
        "status": status,
        "event_id": event_id,
        "reviewed_at_utc": now.isoformat(),
        "actor": payload.actor,
        "reason": payload.reason,
        "execute_requested": payload.execute,
        "approved": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "order_payload": order_payload,
        "matched_outcome": matched,
        "orderbook": book,
        "inventory_snapshot": inventory_snapshot,
        "metadata": metadata,
    }


def build_manual_clob_order_metadata(
    payload: ManualClobOrderAssistantRequest,
    *,
    event_id: str,
    order_payload: dict[str, Any],
    status: str,
    blockers: list[dict[str, Any]],
    orderbook: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "manual_clob_order_assistant_metadata_v1",
        "source": "manual_clob_order_assistant",
        "actor": payload.actor,
        "origin_actor": f"{payload.actor}_assisted",
        "reason": payload.reason,
        "event_id": event_id,
        "idempotency_key": payload.idempotency_key,
        "assistant_status": status,
        "execute_requested": payload.execute,
        "order_payload": order_payload,
        "guardrails": {
            "max_price": payload.max_price,
            "max_notional_usd": payload.max_notional_usd,
            "max_spread_cents": payload.max_spread_cents,
            "max_book_age_seconds": payload.max_book_age_seconds,
            "min_depth": payload.min_depth,
            "market_order_exception": bool(payload.allow_market_urgent_profit_capture),
            "urgent_profit_capture_reason": payload.urgent_profit_capture_reason,
        },
        "blockers": blockers,
        "orderbook_snapshot": orderbook,
        "inventory_snapshot": inventory,
        "operator_metadata": payload.metadata,
    }


def _validate_event_outcome(
    payload: ManualClobOrderAssistantRequest,
    *,
    event_id: str,
    matched_outcome: dict[str, Any],
    blockers: list[dict[str, Any]],
) -> None:
    if not matched_outcome:
        blockers.append({"reason": "outcome_mapping_missing", "event_id": event_id, "outcome_id": payload.outcome_id})
        return
    mapped_event_id = str(matched_outcome.get("event_id") or matched_outcome.get("event_key") or "").strip()
    mapped_market_id = str(matched_outcome.get("market_id") or "").strip()
    mapped_outcome_id = str(matched_outcome.get("outcome_id") or "").strip()
    mapped_token_id = str(matched_outcome.get("token_id") or "").strip()
    if mapped_event_id and mapped_event_id != event_id:
        blockers.append({"reason": "wrong_event", "expected_event_id": event_id, "mapped_event_id": mapped_event_id})
    if mapped_market_id and mapped_market_id != payload.market_id:
        blockers.append({"reason": "market_mismatch", "expected_market_id": payload.market_id, "mapped_market_id": mapped_market_id})
    if mapped_outcome_id and mapped_outcome_id != payload.outcome_id:
        blockers.append(
            {"reason": "outcome_mismatch", "expected_outcome_id": payload.outcome_id, "mapped_outcome_id": mapped_outcome_id}
        )
    if mapped_token_id and mapped_token_id != payload.token_id:
        blockers.append({"reason": "token_mismatch", "expected_token_id": payload.token_id, "mapped_token_id": mapped_token_id})


def _validate_order_type_policy(payload: ManualClobOrderAssistantRequest, *, blockers: list[dict[str, Any]]) -> None:
    if payload.order_type != "market":
        return
    if not payload.allow_market_urgent_profit_capture:
        blockers.append({"reason": "market_orders_disabled", "order_type": payload.order_type})
        return
    if payload.side != "sell":
        blockers.append({"reason": "market_order_exception_sell_only", "side": payload.side})


def _validate_price_and_notional(payload: ManualClobOrderAssistantRequest, *, blockers: list[dict[str, Any]]) -> None:
    price = payload.limit_price
    if payload.order_type == "limit":
        if price is None:
            blockers.append({"reason": "limit_price_required"})
            return
        if payload.side == "buy" and payload.max_price is not None and price > payload.max_price + 1e-9:
            blockers.append({"reason": "max_price_exceeded", "limit_price": price, "max_price": payload.max_price})
        notional = float(price) * float(payload.size)
        if notional > payload.max_notional_usd + 1e-9:
            blockers.append(
                {
                    "reason": "max_notional_exceeded",
                    "notional_usd": round(notional, 6),
                    "max_notional_usd": payload.max_notional_usd,
                }
            )


def _validate_orderbook(
    payload: ManualClobOrderAssistantRequest,
    *,
    orderbook: dict[str, Any],
    now_utc: datetime,
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    if not orderbook:
        blockers.append({"reason": "orderbook_missing"})
        return
    book_token_id = str(orderbook.get("token_id") or "").strip()
    if book_token_id and book_token_id != payload.token_id:
        blockers.append({"reason": "orderbook_token_mismatch", "expected_token_id": payload.token_id, "book_token_id": book_token_id})
    age_seconds = _age_seconds(orderbook.get("captured_at_utc") or orderbook.get("captured_at") or orderbook.get("timestamp"), now_utc=now_utc)
    if age_seconds is None:
        blockers.append({"reason": "orderbook_timestamp_missing"})
    elif age_seconds > payload.max_book_age_seconds:
        blockers.append(
            {
                "reason": "stale_orderbook",
                "orderbook_age_seconds": round(age_seconds, 3),
                "max_book_age_seconds": payload.max_book_age_seconds,
            }
        )
    spread_cents = _spread_cents(orderbook)
    if spread_cents is None:
        warnings.append({"reason": "spread_unavailable"})
    elif spread_cents > payload.max_spread_cents + 1e-9:
        blockers.append({"reason": "spread_too_wide", "spread_cents": spread_cents, "max_spread_cents": payload.max_spread_cents})
    if payload.order_type == "market":
        reference_price = _side_reference_price(payload, orderbook)
        if reference_price is None:
            blockers.append({"reason": "market_reference_price_missing"})
        else:
            notional = reference_price * float(payload.size)
            if notional > payload.max_notional_usd + 1e-9:
                blockers.append(
                    {
                        "reason": "max_notional_exceeded",
                        "notional_usd": round(notional, 6),
                        "max_notional_usd": payload.max_notional_usd,
                    }
                )
    if payload.min_depth is not None:
        depth_key = "ask_depth" if payload.side == "buy" else "bid_depth"
        depth = _safe_float(orderbook.get(depth_key))
        if depth is None:
            warnings.append({"reason": "depth_unavailable", "depth_key": depth_key})
        elif depth < payload.min_depth:
            blockers.append({"reason": "insufficient_depth", "depth_key": depth_key, "depth": depth, "min_depth": payload.min_depth})


def _validate_inventory(
    payload: ManualClobOrderAssistantRequest,
    *,
    inventory: dict[str, Any],
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    if not inventory:
        warnings.append({"reason": "inventory_snapshot_missing"})
        return
    if bool(inventory.get("unresolved_inventory_present")):
        blockers.append({"reason": "unresolved_current_event_inventory"})
    if _has_duplicate_pending(payload, inventory.get("pending_intents") or []):
        blockers.append({"reason": "duplicate_pending_intent", "outcome_id": payload.outcome_id, "side": payload.side})
    if _has_duplicate_pending(payload, inventory.get("open_orders") or []):
        blockers.append({"reason": "duplicate_open_order", "outcome_id": payload.outcome_id, "side": payload.side})


def _has_duplicate_pending(payload: ManualClobOrderAssistantRequest, rows: Any) -> bool:
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        outcome_id = str(row.get("outcome_id") or "").strip()
        token_id = str(row.get("token_id") or "").strip()
        side = str(row.get("side") or row.get("order_side") or "").strip().lower()
        status = str(row.get("status") or row.get("lifecycle_status") or "open").strip().lower()
        if status in {"canceled", "cancelled", "filled", "closed", "expired"}:
            continue
        same_outcome = bool(outcome_id and outcome_id == payload.outcome_id) or bool(token_id and token_id == payload.token_id)
        if same_outcome and side == payload.side:
            return True
    return False


def _side_reference_price(payload: ManualClobOrderAssistantRequest, orderbook: dict[str, Any]) -> float | None:
    if payload.side == "buy":
        return _safe_float(orderbook.get("best_ask"))
    return _safe_float(orderbook.get("best_bid"))


def _spread_cents(orderbook: dict[str, Any]) -> float | None:
    spread = _safe_float(orderbook.get("spread_cents"))
    if spread is not None:
        return round(spread, 6)
    best_bid = _safe_float(orderbook.get("best_bid"))
    best_ask = _safe_float(orderbook.get("best_ask"))
    if best_bid is None or best_ask is None:
        return None
    return round((best_ask - best_bid) * 100.0, 6)


def _age_seconds(value: Any, *, now_utc: datetime) -> float | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    return max(0.0, (now_utc - parsed).total_seconds())


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "build_manual_clob_order_assistant_review",
    "build_manual_clob_order_metadata",
]
