from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class ManagedPositionState:
    strategy_id: str
    event_token_key: str
    outcome: str
    shares: float
    entry_price: float
    current_bid: float | None
    current_ask: float | None
    opened_at_utc: datetime


@dataclass(frozen=True)
class ManagedIntentPlan:
    intent_type: str
    side: str
    price: float | None
    shares: float
    reason: str


def cashout_rebuy_plan(
    position: ManagedPositionState,
    *,
    take_profit_cents: float = 0.10,
    rebuy_discount_cents: float = 0.08,
    min_rebuy_price: float = 0.01,
) -> tuple[ManagedIntentPlan, ...]:
    if position.current_bid is None or position.shares <= 0:
        return ()
    profit_per_share = position.current_bid - position.entry_price
    if profit_per_share < take_profit_cents:
        return ()
    cashout = ManagedIntentPlan(
        intent_type="cashout",
        side="SELL",
        price=position.current_bid,
        shares=position.shares,
        reason="take_profit_reached",
    )
    rebuy_price = max(min_rebuy_price, position.current_bid - rebuy_discount_cents)
    rebuy = ManagedIntentPlan(
        intent_type="rebuy",
        side="BUY",
        price=round(rebuy_price, 4),
        shares=position.shares,
        reason="reenter_after_cashout_pullback_target",
    )
    return (cashout, rebuy)


def stale_order_review_due(
    *,
    created_at_utc: datetime,
    now_utc: datetime | None = None,
    review_after_seconds: float = 45.0,
) -> bool:
    now = (now_utc or datetime.now(UTC)).astimezone(UTC)
    created = created_at_utc.astimezone(UTC)
    return now - created >= timedelta(seconds=review_after_seconds)


def hedge_rebalance_plan(
    *,
    current_up_cost: float,
    current_down_cost: float,
    target_up_ratio: float,
    max_additional_notional_usd: float,
    current_up_ask: float | None,
    current_down_ask: float | None,
) -> ManagedIntentPlan | None:
    total = max(current_up_cost + current_down_cost, 0.0)
    if total <= 0 or max_additional_notional_usd <= 0:
        return None
    bounded_target = min(1.0, max(0.0, target_up_ratio))
    current_ratio = current_up_cost / total
    if abs(current_ratio - bounded_target) < 0.05:
        return None
    buy_up = current_ratio < bounded_target
    price = current_up_ask if buy_up else current_down_ask
    if price is None or price <= 0:
        return None
    deficit = abs((bounded_target * total) - current_up_cost) if buy_up else abs(((1.0 - bounded_target) * total) - current_down_cost)
    notional = min(max_additional_notional_usd, max(deficit, 1.0))
    return ManagedIntentPlan(
        intent_type="hedge_rebalance",
        side="BUY",
        price=price,
        shares=round(notional / price, 6),
        reason="buy_only_ratio_rebalance",
    )
