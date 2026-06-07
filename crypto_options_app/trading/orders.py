from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from crypto_options_app.trading.intents import ExecutionIntent


ORDER_STATES = {
    "created",
    "submitted",
    "accepted",
    "rejected",
    "partially_filled",
    "filled",
    "unfilled",
    "expired",
    "cancelled",
    "submit_error",
    "reconciliation_pending",
    "reconciliation_failed",
}
TERMINAL_ORDER_STATES = {"rejected", "filled", "unfilled", "expired", "cancelled", "submit_error", "reconciliation_failed"}


@dataclass(frozen=True)
class OrderState:
    order_key: str
    intent_key: str
    strategy_id: str
    event_token_key: str
    side: str
    order_type: str
    requested_shares: float
    limit_price: float | None
    status: str
    exchange_order_id: str | None = None
    submitted_at_utc: datetime | None = None
    updated_at_utc: datetime | None = None
    reason: str | None = None
    source_payload: dict[str, Any] | None = None


def create_order_from_intent(intent: ExecutionIntent, *, exchange_order_id: str | None = None) -> OrderState:
    return OrderState(
        order_key=f"order:{intent.intent_key}",
        intent_key=intent.intent_key,
        strategy_id=intent.strategy_id,
        event_token_key=intent.event_token_key,
        side=intent.side,
        order_type=intent.order_type,
        requested_shares=intent.shares,
        limit_price=intent.limit_price,
        status="created",
        exchange_order_id=exchange_order_id,
        updated_at_utc=datetime.now(UTC),
    )


def transition_order(order: OrderState, *, status: str, reason: str | None = None, source_payload: dict[str, Any] | None = None) -> OrderState:
    if status not in ORDER_STATES:
        raise ValueError(f"unsupported order status: {status}")
    submitted_at = order.submitted_at_utc
    if status == "submitted" and submitted_at is None:
        submitted_at = datetime.now(UTC)
    return replace(
        order,
        status=status,
        submitted_at_utc=submitted_at,
        updated_at_utc=datetime.now(UTC),
        reason=reason,
        source_payload=source_payload if source_payload is not None else order.source_payload,
    )


def order_counts_as_trade(order: OrderState) -> bool:
    return order.status in {"partially_filled", "filled"}
