from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


OUTCOME_UP = "up"
OUTCOME_DOWN = "down"


@dataclass(frozen=True)
class ProfileOrder:
    profile_key: str
    event_key: str
    outcome: str
    order_side: str
    price: float
    shares: float
    activity_at_utc: str | None = None
    raw_activity_key: str | None = None


@dataclass(frozen=True)
class SideInventory:
    outcome: str
    buy_count: int = 0
    sell_count: int = 0
    buy_shares: float = 0.0
    sell_shares: float = 0.0
    cost_basis_usd: float = 0.0
    sell_proceeds_usd: float = 0.0
    weighted_avg_price: float | None = None
    open_shares: float = 0.0
    open_cost_basis_usd: float = 0.0
    price_buckets: frozenset[float] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ProfileEventReconstruction:
    reconstruction_key: str
    profile_key: str
    event_key: str
    up: SideInventory
    down: SideInventory
    buy_count: int
    sell_count: int
    realized_pnl_usd: float
    settlement_pnl_usd: float | None
    event_pnl_usd: float | None
    event_effective_win: bool | None
    side_skew: float
    hedge_balance: float
    price_band_coverage: float
    turnover: float
    reconstruction_quality: float
    source_order_keys: tuple[str, ...]
    resolved_outcome: str | None = None


def reconstruct_profile_event(
    *,
    profile_key: str,
    event_key: str,
    orders: list[ProfileOrder],
    resolved_outcome: str | None = None,
) -> ProfileEventReconstruction:
    normalized_orders = [
        order
        for order in orders
        if order.profile_key == profile_key and order.event_key == event_key and order.price >= 0 and order.shares > 0
    ]
    up = _build_side_inventory(normalized_orders, OUTCOME_UP)
    down = _build_side_inventory(normalized_orders, OUTCOME_DOWN)
    buy_count = up.buy_count + down.buy_count
    sell_count = up.sell_count + down.sell_count
    realized_pnl = _realized_pnl(up) + _realized_pnl(down)
    settlement_pnl = _settlement_pnl(up, down, resolved_outcome)
    event_pnl = realized_pnl + settlement_pnl if settlement_pnl is not None else realized_pnl if sell_count else None
    event_effective_win = None if event_pnl is None else event_pnl > 0
    total_open_shares = up.open_shares + down.open_shares
    side_skew = 0.0 if total_open_shares == 0 else abs(up.open_shares - down.open_shares) / total_open_shares
    hedge_balance = 1.0 - side_skew if total_open_shares else 0.0
    price_band_coverage = _price_band_coverage(up.price_buckets.union(down.price_buckets), buy_count)
    buy_notional = up.cost_basis_usd + down.cost_basis_usd
    sell_notional = up.sell_proceeds_usd + down.sell_proceeds_usd
    turnover = sell_notional / buy_notional if buy_notional > 0 else 0.0
    reconstruction_quality = _reconstruction_quality(normalized_orders, up, down)
    source_order_keys = tuple(order.raw_activity_key or f"{order.order_side}:{order.outcome}:{order.price}:{order.shares}" for order in normalized_orders)
    return ProfileEventReconstruction(
        reconstruction_key=f"{profile_key}:{event_key}",
        profile_key=profile_key,
        event_key=event_key,
        up=up,
        down=down,
        buy_count=buy_count,
        sell_count=sell_count,
        realized_pnl_usd=round(realized_pnl, 6),
        settlement_pnl_usd=None if settlement_pnl is None else round(settlement_pnl, 6),
        event_pnl_usd=None if event_pnl is None else round(event_pnl, 6),
        event_effective_win=event_effective_win,
        side_skew=round(side_skew, 6),
        hedge_balance=round(hedge_balance, 6),
        price_band_coverage=round(price_band_coverage, 6),
        turnover=round(turnover, 6),
        reconstruction_quality=round(reconstruction_quality, 6),
        source_order_keys=source_order_keys,
        resolved_outcome=_normalize_outcome(resolved_outcome) if resolved_outcome else None,
    )


def order_from_mapping(row: dict[str, Any]) -> ProfileOrder:
    return ProfileOrder(
        profile_key=str(row["profile_key"]),
        event_key=str(row["event_key"]),
        outcome=str(row.get("outcome") or row.get("outcome_side") or ""),
        order_side=str(row.get("order_side") or ""),
        price=float(row.get("price") or 0.0),
        shares=float(row.get("shares") or 0.0),
        activity_at_utc=row.get("activity_at_utc"),
        raw_activity_key=row.get("raw_activity_key"),
    )


def _build_side_inventory(orders: list[ProfileOrder], outcome: str) -> SideInventory:
    buy_count = sell_count = 0
    buy_shares = sell_shares = 0.0
    cost_basis = sell_proceeds = 0.0
    buckets: set[float] = set()
    for order in orders:
        if _normalize_outcome(order.outcome) != outcome:
            continue
        side = order.order_side.upper()
        if side == "BUY":
            buy_count += 1
            buy_shares += order.shares
            cost_basis += order.price * order.shares
            buckets.add(_price_bucket(order.price))
        elif side == "SELL":
            sell_count += 1
            sell_shares += order.shares
            sell_proceeds += order.price * order.shares
    weighted_avg = cost_basis / buy_shares if buy_shares > 0 else None
    open_shares = max(buy_shares - sell_shares, 0.0)
    open_cost = open_shares * weighted_avg if weighted_avg is not None else 0.0
    return SideInventory(
        outcome=outcome,
        buy_count=buy_count,
        sell_count=sell_count,
        buy_shares=buy_shares,
        sell_shares=sell_shares,
        cost_basis_usd=round(cost_basis, 6),
        sell_proceeds_usd=round(sell_proceeds, 6),
        weighted_avg_price=None if weighted_avg is None else round(weighted_avg, 6),
        open_shares=round(open_shares, 6),
        open_cost_basis_usd=round(open_cost, 6),
        price_buckets=frozenset(buckets),
    )


def _realized_pnl(side: SideInventory) -> float:
    if side.sell_shares <= 0 or side.weighted_avg_price is None:
        return 0.0
    matched_shares = min(side.sell_shares, side.buy_shares)
    matched_cost = matched_shares * side.weighted_avg_price
    return side.sell_proceeds_usd - matched_cost


def _settlement_pnl(up: SideInventory, down: SideInventory, resolved_outcome: str | None) -> float | None:
    if not resolved_outcome:
        return None
    normalized = _normalize_outcome(resolved_outcome)
    winning = up if normalized == OUTCOME_UP else down if normalized == OUTCOME_DOWN else None
    losing = down if winning is up else up if winning is down else None
    if winning is None or losing is None:
        return None
    return winning.open_shares - winning.open_cost_basis_usd - losing.open_cost_basis_usd


def _price_band_coverage(buckets: set[float] | frozenset[float], buy_count: int) -> float:
    if buy_count <= 0:
        return 0.0
    return min(1.0, len(buckets) / 5.0)


def _reconstruction_quality(orders: list[ProfileOrder], up: SideInventory, down: SideInventory) -> float:
    if not orders:
        return 0.0
    complete_rows = sum(1 for order in orders if order.outcome and order.order_side and order.price >= 0 and order.shares > 0)
    row_quality = complete_rows / len(orders)
    identity_quality = 1.0 if up.buy_count + down.buy_count > 0 else 0.4
    return max(0.0, min(1.0, row_quality * identity_quality))


def _price_bucket(price: float) -> float:
    return round(int(price / 0.05) * 0.05, 2)


def _normalize_outcome(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"yes", "up"}:
        return OUTCOME_UP
    if normalized in {"no", "down"}:
        return OUTCOME_DOWN
    return normalized
