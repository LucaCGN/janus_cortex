from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from crypto_options_app.trading.orders import OrderState


@dataclass(frozen=True)
class FillState:
    fill_key: str
    order_key: str
    intent_key: str
    strategy_id: str
    event_token_key: str
    side: str
    filled_shares: float
    fill_price: float
    filled_at_utc: datetime
    reconciled: bool = False


def record_fill(
    order: OrderState,
    *,
    filled_shares: float,
    fill_price: float,
    filled_at_utc: datetime | None = None,
    reconciled: bool = False,
) -> FillState:
    if filled_shares <= 0:
        raise ValueError("filled_shares must be positive")
    if fill_price < 0:
        raise ValueError("fill_price cannot be negative")
    return FillState(
        fill_key=f"fill:{order.order_key}:{filled_shares}:{fill_price}",
        order_key=order.order_key,
        intent_key=order.intent_key,
        strategy_id=order.strategy_id,
        event_token_key=order.event_token_key,
        side=order.side,
        filled_shares=filled_shares,
        fill_price=fill_price,
        filled_at_utc=(filled_at_utc or datetime.now(UTC)).astimezone(UTC),
        reconciled=reconciled,
    )
