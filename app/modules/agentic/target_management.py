from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


TargetActor = Literal["janus", "operator", "unknown"]
TargetStatus = Literal["no_position", "target_covered", "target_missing", "target_stale"]


class TargetManagedLot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token_id: str = Field(min_length=1)
    shares: float = Field(gt=0.0)
    price: float = Field(ge=0.0, le=1.0)
    actor: TargetActor = "unknown"
    external_order_id: str | None = None
    external_trade_id: str | None = None
    source: str = "direct_clob_trade"


class TargetCoverageOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token_id: str = Field(min_length=1)
    size: float = Field(gt=0.0)
    price: float = Field(ge=0.0, le=1.0)
    external_order_id: str | None = None
    status: str | None = None


class SleeveTargetEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sleeve_id: str = Field(min_length=1)
    sleeve_role: str | None = None
    strategy_id: str | None = None
    token_id: str = Field(min_length=1)
    outcome_id: str | None = None
    outcome_label: str | None = None
    allocated_shares: float = Field(default=0.0, ge=0.0)
    weighted_basis_price: float | None = Field(default=None, ge=0.0, le=1.0)
    lot_actor_counts: dict[str, int] = Field(default_factory=dict)
    target_price: float | None = Field(default=None, ge=0.0, le=1.0)
    target_coverage_shares: float = Field(default=0.0, ge=0.0)
    target_order_ids: list[str] = Field(default_factory=list)
    target_status: TargetStatus
    replacement_recommended: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    lots: list[TargetManagedLot] = Field(default_factory=list)
    target_orders: list[TargetCoverageOrder] = Field(default_factory=list)


class TargetManagementEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "sports_live_target_management_evidence_v1"
    event_id: str = Field(min_length=1)
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sleeve_count: int = Field(default=0, ge=0)
    target_covered_count: int = Field(default=0, ge=0)
    target_missing_count: int = Field(default=0, ge=0)
    target_stale_count: int = Field(default=0, ge=0)
    replacement_recommendation_count: int = Field(default=0, ge=0)
    execution_boundary: Literal["evidence_only"] = "evidence_only"
    sleeves: list[SleeveTargetEvidence] = Field(default_factory=list)


def build_target_management_evidence(
    *,
    event_id: str,
    plan: dict[str, Any],
    direct_clob: dict[str, Any],
    known_external_order_ids: set[str] | None = None,
    min_size: float = 5.0,
    default_target_delta_cents: float = 5.0,
    stale_target_tolerance_cents: float = 0.5,
) -> TargetManagementEvidence:
    strategies = _strategy_targets(plan)
    positions = _positions_by_token(direct_clob)
    orders = _sell_orders_by_token(direct_clob)
    lots = _lots_by_token(
        direct_clob,
        positions=positions,
        known_external_order_ids=known_external_order_ids,
    )
    sleeve_rows: list[SleeveTargetEvidence] = []
    strategy_tokens = {row["token_id"] for row in strategies}

    for token_id in sorted(strategy_tokens):
        token_strategies = [row for row in strategies if row["token_id"] == token_id]
        token_lots = list(lots.get(token_id) or [])
        token_orders = list(orders.get(token_id) or [])
        for strategy in token_strategies:
            sleeve_lots = _take_lots(token_lots, strategy["entry_size"])
            sleeve_orders = _take_orders(token_orders, _sum_lot_shares(sleeve_lots))
            sleeve_rows.append(
                _build_sleeve_evidence(
                    strategy=strategy,
                    lots=sleeve_lots,
                    orders=sleeve_orders,
                    min_size=min_size,
                    default_target_delta_cents=default_target_delta_cents,
                    stale_target_tolerance_cents=stale_target_tolerance_cents,
                )
            )

        excess_lots = [lot for lot in token_lots if lot.shares > 1e-9]
        if excess_lots:
            sleeve_orders = _take_orders(token_orders, _sum_lot_shares(excess_lots))
            sleeve_rows.append(
                _build_sleeve_evidence(
                    strategy={
                        "sleeve_id": f"{token_id}-unassigned-direct-clob-excess",
                        "sleeve_role": "unassigned_excess",
                        "strategy_id": None,
                        "token_id": token_id,
                        "outcome_id": None,
                        "outcome_label": None,
                        "entry_size": _sum_lot_shares(excess_lots),
                        "exit_rules": {},
                    },
                    lots=excess_lots,
                    orders=sleeve_orders,
                    min_size=min_size,
                    default_target_delta_cents=default_target_delta_cents,
                    stale_target_tolerance_cents=stale_target_tolerance_cents,
                )
            )

    for token_id, token_position in sorted(positions.items()):
        if token_id in strategy_tokens:
            continue
        position_lot = TargetManagedLot(
            token_id=token_id,
            shares=token_position["shares"],
            price=token_position["price"],
            actor="unknown",
            source="direct_clob_position",
        )
        sleeve_rows.append(
            _build_sleeve_evidence(
                strategy={
                    "sleeve_id": f"{token_id}-unmapped-direct-clob-position",
                    "sleeve_role": "unmapped_position",
                    "strategy_id": None,
                    "token_id": token_id,
                    "outcome_id": None,
                    "outcome_label": token_position.get("outcome_label"),
                    "entry_size": token_position["shares"],
                    "exit_rules": {},
                },
                lots=[position_lot],
                orders=list(orders.get(token_id) or []),
                min_size=min_size,
                default_target_delta_cents=default_target_delta_cents,
                stale_target_tolerance_cents=stale_target_tolerance_cents,
            )
        )

    return TargetManagementEvidence(
        event_id=event_id,
        sleeve_count=len(sleeve_rows),
        target_covered_count=sum(1 for row in sleeve_rows if row.target_status == "target_covered"),
        target_missing_count=sum(1 for row in sleeve_rows if row.target_status == "target_missing"),
        target_stale_count=sum(1 for row in sleeve_rows if row.target_status == "target_stale"),
        replacement_recommendation_count=sum(1 for row in sleeve_rows if row.replacement_recommended),
        sleeves=sleeve_rows,
    )


def _strategy_targets(plan: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for strategy in plan.get("active_strategies") or []:
        if not isinstance(strategy, dict):
            continue
        entry_rules = strategy.get("entry_rules") if isinstance(strategy.get("entry_rules"), dict) else {}
        token_id = str(entry_rules.get("token_id") or entry_rules.get("asset_id") or "").strip()
        if not token_id:
            continue
        strategy_id = str(strategy.get("strategy_id") or "").strip() or token_id
        rows.append(
            {
                "sleeve_id": str(strategy.get("sleeve_id") or entry_rules.get("sleeve_id") or strategy_id).strip()
                or strategy_id,
                "sleeve_role": str(strategy.get("sleeve_role") or entry_rules.get("sleeve_role") or "").strip() or None,
                "strategy_id": strategy_id,
                "token_id": token_id,
                "outcome_id": str(entry_rules.get("outcome_id") or "").strip() or None,
                "outcome_label": str(strategy.get("side") or entry_rules.get("outcome_label") or "").strip() or None,
                "entry_size": _float(entry_rules.get("size") or entry_rules.get("shares")) or 0.0,
                "exit_rules": strategy.get("exit_rules") if isinstance(strategy.get("exit_rules"), dict) else {},
            }
        )
    return rows


def _positions_by_token(direct_clob: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for position in ((direct_clob.get("open_positions") or {}).get("positions") or []):
        if not isinstance(position, dict):
            continue
        token_id = str(position.get("asset") or position.get("asset_id") or position.get("token_id") or "").strip()
        size = _float(position.get("size")) or 0.0
        price = _float(position.get("avg_price") or position.get("average_price"))
        if price is None:
            current_value = _float(position.get("current_value"))
            price = current_value / size if current_value is not None and size > 0 else None
        if token_id and size > 0.0 and price is not None:
            rows[token_id] = {
                "shares": size,
                "price": price,
                "outcome_label": position.get("outcome") or position.get("outcome_label"),
            }
    return rows


def _sell_orders_by_token(direct_clob: dict[str, Any]) -> dict[str, list[TargetCoverageOrder]]:
    rows: dict[str, list[TargetCoverageOrder]] = {}
    for order in ((direct_clob.get("open_orders") or {}).get("orders") or []):
        if not isinstance(order, dict):
            continue
        side = str(order.get("side") or "").strip().lower()
        token_id = str(order.get("asset") or order.get("asset_id") or order.get("token_id") or "").strip()
        size = _float(order.get("size") or order.get("original_size") or order.get("remaining_size")) or 0.0
        price = _float(order.get("price") or order.get("limit_price"))
        if side != "sell" or not token_id or size <= 0.0 or price is None:
            continue
        rows.setdefault(token_id, []).append(
            TargetCoverageOrder(
                token_id=token_id,
                size=size,
                price=price,
                external_order_id=_external_order_id(order),
                status=str(order.get("status") or "").strip() or None,
            )
        )
    return rows


def _lots_by_token(
    direct_clob: dict[str, Any],
    *,
    positions: dict[str, dict[str, Any]],
    known_external_order_ids: set[str] | None,
) -> dict[str, list[TargetManagedLot]]:
    rows: dict[str, list[TargetManagedLot]] = {}
    for trade in _direct_trades(direct_clob, known_external_order_ids=known_external_order_ids):
        token_id = str(trade.get("asset") or trade.get("asset_id") or trade.get("token_id") or "").strip()
        side = str(trade.get("side") or trade.get("taker_side") or "").strip().lower()
        size = _float(trade.get("size") or trade.get("matched_amount") or trade.get("shares")) or 0.0
        price = _float(trade.get("price"))
        if not token_id or side not in {"buy", "long"} or size <= 0.0 or price is None:
            continue
        rows.setdefault(token_id, []).append(
            TargetManagedLot(
                token_id=token_id,
                shares=size,
                price=price,
                actor=_trade_actor(trade, known_external_order_ids=known_external_order_ids),
                external_order_id=_trade_order_id(trade),
                external_trade_id=_external_trade_id(trade),
            )
        )
    _cap_lots_to_open_positions(rows, positions)
    for token_id, position in positions.items():
        known_size = _sum_lot_shares(rows.get(token_id) or [])
        remainder = max(0.0, position["shares"] - known_size)
        if remainder > 1e-9:
            rows.setdefault(token_id, []).append(
                TargetManagedLot(
                    token_id=token_id,
                    shares=round(remainder, 6),
                    price=position["price"],
                    actor="unknown",
                    source="direct_clob_position_remainder",
                )
            )
    return rows


def _cap_lots_to_open_positions(
    rows: dict[str, list[TargetManagedLot]],
    positions: dict[str, dict[str, Any]],
) -> None:
    for token_id, position in positions.items():
        lots = rows.get(token_id) or []
        position_shares = _float(position.get("shares")) or 0.0
        if not lots or position_shares <= 0.0 or _sum_lot_shares(lots) <= position_shares + 1e-9:
            continue
        remaining = position_shares
        capped: list[TargetManagedLot] = []
        for lot in lots:
            if remaining <= 1e-9:
                break
            take = min(lot.shares, remaining)
            if take <= 1e-9:
                continue
            capped.append(lot.model_copy(update={"shares": round(take, 6)}))
            remaining -= take
        rows[token_id] = capped


def _build_sleeve_evidence(
    *,
    strategy: dict[str, Any],
    lots: list[TargetManagedLot],
    orders: list[TargetCoverageOrder],
    min_size: float,
    default_target_delta_cents: float,
    stale_target_tolerance_cents: float,
) -> SleeveTargetEvidence:
    allocated_shares = _sum_lot_shares(lots)
    basis = _weighted_basis(lots)
    target_price = _target_price(basis, strategy.get("exit_rules") or {}, default_target_delta_cents)
    coverage = round(sum(order.size for order in orders), 6)
    order_ids = [order.external_order_id for order in orders if order.external_order_id]
    status: TargetStatus
    reasons: list[str] = []
    replacement = False
    if allocated_shares <= 1e-9:
        status = "no_position"
        reasons.append("no_direct_position_for_sleeve")
    elif coverage <= 1e-9:
        status = "target_missing"
        replacement = allocated_shares >= min_size
        reasons.append("target_order_missing")
    elif target_price is not None and any(order.price + stale_target_tolerance_cents / 100.0 < target_price for order in orders):
        status = "target_stale"
        replacement = True
        reasons.append("target_price_below_current_lot_basis_policy")
    elif coverage + 1e-9 < allocated_shares and allocated_shares - coverage >= min_size:
        status = "target_missing"
        replacement = True
        reasons.append("target_coverage_shortfall")
    else:
        status = "target_covered"
        reasons.append("target_coverage_current")
    if replacement:
        reasons.append("replace_or_place_target_order_recommended")
    return SleeveTargetEvidence(
        sleeve_id=str(strategy["sleeve_id"]),
        sleeve_role=strategy.get("sleeve_role"),
        strategy_id=strategy.get("strategy_id"),
        token_id=str(strategy["token_id"]),
        outcome_id=strategy.get("outcome_id"),
        outcome_label=strategy.get("outcome_label"),
        allocated_shares=round(allocated_shares, 6),
        weighted_basis_price=basis,
        lot_actor_counts=_actor_counts(lots),
        target_price=target_price,
        target_coverage_shares=coverage,
        target_order_ids=order_ids,
        target_status=status,
        replacement_recommended=replacement,
        reason_codes=_unique(reasons),
        lots=[lot for lot in lots if lot.shares > 1e-9],
        target_orders=orders,
    )


def _take_lots(lots: list[TargetManagedLot], target_size: float) -> list[TargetManagedLot]:
    if target_size <= 0.0:
        target_size = sum(lot.shares for lot in lots)
    remaining = target_size
    taken: list[TargetManagedLot] = []
    for index, lot in enumerate(list(lots)):
        if remaining <= 1e-9:
            break
        take = min(lot.shares, remaining)
        if take <= 1e-9:
            continue
        taken.append(lot.model_copy(update={"shares": round(take, 6)}))
        lots[index] = lot.model_copy(update={"shares": round(lot.shares - take, 6)})
        remaining -= take
    return taken


def _take_orders(orders: list[TargetCoverageOrder], target_size: float) -> list[TargetCoverageOrder]:
    remaining = target_size
    taken: list[TargetCoverageOrder] = []
    for index, order in enumerate(list(orders)):
        if remaining <= 1e-9:
            break
        take = min(order.size, remaining)
        if take <= 1e-9:
            continue
        taken.append(order.model_copy(update={"size": round(take, 6)}))
        orders[index] = order.model_copy(update={"size": round(order.size - take, 6)})
        remaining -= take
    return taken


def _target_price(basis: float | None, exit_rules: dict[str, Any], default_target_delta_cents: float) -> float | None:
    explicit = _float(exit_rules.get("target_price"))
    if explicit is not None:
        return _normalize_price(explicit, exit_rules)
    if basis is None:
        return None
    min_target_cents = _float(
        exit_rules.get("min_target_cents")
        or exit_rules.get("minimum_target_cents")
        or exit_rules.get("target_floor_cents")
    )
    target_return = _float(
        exit_rules.get("target_return_fraction")
        or exit_rules.get("target_gain_fraction")
        or exit_rules.get("target_return")
    )
    target_return_percent = _float(exit_rules.get("target_return_percent") or exit_rules.get("target_gain_percent"))
    if target_return is None and target_return_percent is not None:
        target_return = target_return_percent / 100.0
    move = max((min_target_cents or default_target_delta_cents) / 100.0, basis * (target_return or 0.0))
    return _normalize_price(basis + move, exit_rules)


def _normalize_price(price: float, rules: dict[str, Any]) -> float:
    floor = _float(
        rules.get("min_target_price")
        or rules.get("target_min_price")
        or rules.get("target_floor_price")
        or rules.get("minimum_target_price")
    )
    tick = _float(
        rules.get("target_tick_size")
        or rules.get("tick_size")
        or rules.get("price_tick_size")
        or rules.get("min_price_increment")
    )
    bounded = min(0.95, max(floor or 0.01, price))
    if tick is not None and tick > 0.0:
        decimals = max(0, min(6, len(f"{tick:.8f}".rstrip("0").split(".")[-1])))
        return round(math.ceil((bounded - 1e-12) / tick) * tick, decimals)
    if bounded < 0.10:
        return round(math.ceil((bounded - 1e-12) * 100.0) / 100.0, 2)
    return round(bounded, 4)


_ACCOUNT_TRADE_SECTION_KEYS = (
    "account_trades",
    "event_account_trades",
    "direct_account_trades",
    "direct_trades",
    "trades",
)


def _direct_trades(
    direct_clob: dict[str, Any],
    *,
    known_external_order_ids: set[str] | None,
) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    for key in _ACCOUNT_TRADE_SECTION_KEYS:
        value = direct_clob.get(key)
        if isinstance(value, dict):
            candidates.extend(value.get("trades") or value.get("items") or [])
        elif isinstance(value, list):
            candidates.extend(value)

    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        stable_key = _trade_stable_key(row)
        if stable_key in seen:
            continue
        seen.add(stable_key)
        output.append(row)
    return output


def _trade_actor(trade: dict[str, Any], *, known_external_order_ids: set[str] | None) -> TargetActor:
    actor = str(trade.get("actor") or trade.get("source_actor") or trade.get("owner") or "").strip().lower()
    if actor in {"janus", "operator"}:
        return actor  # type: ignore[return-value]
    order_id = _trade_order_id(trade)
    if order_id and known_external_order_ids is not None:
        return "janus" if order_id.lower() in {item.lower() for item in known_external_order_ids} else "operator"
    return "unknown"


def _trade_order_id(trade: dict[str, Any]) -> str | None:
    for key in ("taker_order_id", "maker_order_id", "order_id", "orderID", "orderId", "external_order_id"):
        value = str(trade.get(key) or "").strip()
        if value:
            return value
    return None


def _external_order_id(order: dict[str, Any]) -> str | None:
    for key in ("id", "orderID", "orderId", "order_id", "external_order_id"):
        value = str(order.get(key) or "").strip()
        if value:
            return value
    return None


def _external_trade_id(trade: dict[str, Any]) -> str | None:
    for key in ("id", "trade_id", "external_trade_id", "hash", "tx_hash", "transaction_hash"):
        value = str(trade.get(key) or "").strip()
        if value:
            return value
    return None


def _trade_stable_key(trade: dict[str, Any]) -> str:
    trade_id = _external_trade_id(trade)
    if trade_id:
        return f"id:{trade_id}"
    order_id = _trade_order_id(trade) or ""
    token_id = str(trade.get("asset") or trade.get("asset_id") or trade.get("token_id") or "").strip()
    side = str(trade.get("side") or trade.get("taker_side") or "").strip().lower()
    size = str(trade.get("size") or trade.get("matched_amount") or trade.get("shares") or "").strip()
    price = str(trade.get("price") or "").strip()
    timestamp = str(trade.get("timestamp_utc") or trade.get("created_at_utc") or trade.get("created_at") or "").strip()
    return f"trade:{order_id}:{token_id}:{side}:{size}:{price}:{timestamp}"


def _sum_lot_shares(lots: list[TargetManagedLot]) -> float:
    return round(sum(lot.shares for lot in lots if lot.shares > 0.0), 6)


def _weighted_basis(lots: list[TargetManagedLot]) -> float | None:
    shares = sum(lot.shares for lot in lots)
    if shares <= 0.0:
        return None
    return round(sum(lot.shares * lot.price for lot in lots) / shares, 6)


def _actor_counts(lots: list[TargetManagedLot]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for lot in lots:
        counts[lot.actor] = counts.get(lot.actor, 0) + 1
    return counts


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
    return output


__all__ = [
    "SleeveTargetEvidence",
    "TargetCoverageOrder",
    "TargetManagedLot",
    "TargetManagementEvidence",
    "build_target_management_evidence",
]
