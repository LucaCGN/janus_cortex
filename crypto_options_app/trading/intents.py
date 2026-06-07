from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


SUPPORTED_INTENT_ORDER_TYPES = {"market_buy", "limit_buy", "market_sell", "limit_sell"}


@dataclass(frozen=True)
class ExecutionIntent:
    intent_key: str
    candidate_key: str
    strategy_id: str
    run_id: str
    event_key: str
    event_token_key: str
    side: str
    intent_type: str
    order_type: str
    shares: float
    limit_price: float | None
    decision_at_utc: datetime
    source_attribution: tuple[str, ...] = ()
    status: str = "created"
    metadata: dict[str, Any] = field(default_factory=dict)
    orders_allowed: bool = False
    live_trading_authorized: bool = False


class IntentBook:
    def __init__(self) -> None:
        self._intents: dict[str, ExecutionIntent] = {}

    def add(self, intent: ExecutionIntent) -> bool:
        if intent.intent_key in self._intents:
            return False
        self._intents[intent.intent_key] = intent
        return True

    def get(self, intent_key: str) -> ExecutionIntent | None:
        return self._intents.get(intent_key)


def create_execution_intent(
    *,
    candidate_key: str,
    strategy_id: str,
    run_id: str,
    event_key: str,
    event_token_key: str,
    side: str,
    intent_type: str,
    order_type: str,
    shares: float,
    decision_at_utc: datetime,
    limit_price: float | None = None,
    source_attribution: tuple[str, ...] = (),
) -> ExecutionIntent:
    normalized_order_type = order_type.lower()
    if normalized_order_type not in SUPPORTED_INTENT_ORDER_TYPES:
        raise ValueError(f"unsupported order type: {order_type}")
    if normalized_order_type.startswith("limit") and limit_price is None:
        raise ValueError("limit intents require limit_price")
    if shares <= 0:
        raise ValueError("shares must be positive")
    key = stable_intent_key(
        strategy_id,
        run_id,
        event_key,
        event_token_key,
        side,
        intent_type,
        normalized_order_type,
        round(shares, 8),
        None if limit_price is None else round(limit_price, 4),
        decision_at_utc.replace(second=0, microsecond=0).astimezone(UTC).isoformat(),
        "|".join(source_attribution),
    )
    return ExecutionIntent(
        intent_key=key,
        candidate_key=candidate_key,
        strategy_id=strategy_id,
        run_id=run_id,
        event_key=event_key,
        event_token_key=event_token_key,
        side=side.upper(),
        intent_type=intent_type,
        order_type=normalized_order_type,
        shares=shares,
        limit_price=limit_price,
        decision_at_utc=decision_at_utc.astimezone(UTC),
        source_attribution=source_attribution,
    )


def stable_intent_key(*parts: Any) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]
