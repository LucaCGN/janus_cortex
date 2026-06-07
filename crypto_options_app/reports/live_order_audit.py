from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any


AUDIT_SCHEMA_VERSION = "crypto_options_live_order_integrity_audit_v1"


@dataclass(frozen=True)
class LiveOrderAuditResult:
    status: str
    recorded_successful_buy_count: int
    exchange_buy_count: int
    strong_match_count: int
    weak_match_count: int
    unmatched_recorded_count: int
    unmatched_exchange_buy_count: int
    unexpected_exchange_sell_count: int
    blockers: tuple[str, ...]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "status": self.status,
            "recorded_successful_buy_count": self.recorded_successful_buy_count,
            "exchange_buy_count": self.exchange_buy_count,
            "strong_match_count": self.strong_match_count,
            "weak_match_count": self.weak_match_count,
            "unmatched_recorded_count": self.unmatched_recorded_count,
            "unmatched_exchange_buy_count": self.unmatched_exchange_buy_count,
            "unexpected_exchange_sell_count": self.unexpected_exchange_sell_count,
            "blockers": list(self.blockers),
            "summary": self.summary,
        }


def audit_validation_orders_against_exchange(
    *,
    validation_rows: list[dict[str, Any]],
    exchange_trades: list[dict[str, Any]],
) -> LiveOrderAuditResult:
    recorded_rows = [_normalize_validation_row(row) for row in validation_rows]
    recorded_successes = [
        row
        for row in recorded_rows
        if row["side"] == "BUY"
        and row["status"] in {"executed", "live_structural_executed"}
        and not row["blockers"]
        and _is_filled_recorded_buy(row)
    ]
    exchange_buys_all = [_normalize_trade(row) for row in exchange_trades if _normalized_side(row.get("side")) == "BUY"]
    exchange_sells_all = [_normalize_trade(row) for row in exchange_trades if _normalized_side(row.get("side")) == "SELL"]
    exchange_buys = _scope_exchange_trades(exchange_buys_all, recorded_rows)
    exchange_sells = _scope_exchange_trades(exchange_sells_all, recorded_rows)

    strong_matches: list[dict[str, Any]] = []
    weak_matches: list[dict[str, Any]] = []
    fill_mismatches: list[dict[str, Any]] = []
    used_exchange_indexes: set[int] = set()
    for recorded in recorded_successes:
        exact_index = _find_exact_order_id_match(recorded, exchange_buys, used_exchange_indexes)
        if exact_index is not None:
            used_exchange_indexes.add(exact_index)
            if _compatible_fill(recorded, exchange_buys[exact_index]):
                strong_matches.append({"recorded": recorded, "exchange": exchange_buys[exact_index]})
            else:
                fill_mismatches.append({"recorded": recorded, "exchange": exchange_buys[exact_index]})
            continue
        weak_index = _find_weak_match(recorded, exchange_buys, used_exchange_indexes)
        if weak_index is not None:
            used_exchange_indexes.add(weak_index)
            weak_matches.append({"recorded": recorded, "exchange": exchange_buys[weak_index]})

    matched_recorded_count = len(strong_matches) + len(weak_matches) + len(fill_mismatches)
    unmatched_recorded = max(0, len(recorded_successes) - matched_recorded_count)
    unmatched_exchange = max(0, len(exchange_buys) - len(used_exchange_indexes))
    blockers: list[str] = []
    if any(_missing_strong_identity(row) for row in recorded_successes):
        blockers.append("recorded_rows_missing_exchange_or_token_identity")
    if weak_matches:
        blockers.append("recorded_orders_matched_without_exchange_order_id")
    if fill_mismatches:
        blockers.append("recorded_exchange_fill_mismatch")
    if unmatched_recorded:
        blockers.append("recorded_orders_without_exchange_match")
    if unmatched_exchange:
        blockers.append("exchange_buy_trades_without_recorded_match")
    status = "matched" if not blockers else "weak_match_with_blockers" if matched_recorded_count else "mismatch"
    return LiveOrderAuditResult(
        status=status,
        recorded_successful_buy_count=len(recorded_successes),
        exchange_buy_count=len(exchange_buys),
        strong_match_count=len(strong_matches),
        weak_match_count=len(weak_matches),
        unmatched_recorded_count=unmatched_recorded,
        unmatched_exchange_buy_count=unmatched_exchange,
        unexpected_exchange_sell_count=len(exchange_sells),
        blockers=tuple(blockers),
        summary={
            "recorded_status_counts": dict(Counter(row["status"] for row in recorded_rows)),
            "recorded_unfilled_buy_count": len(
                [
                    row
                    for row in recorded_rows
                    if row["side"] == "BUY"
                    and row["status"] in {"executed", "live_structural_executed", "live_structural_unfilled"}
                    and not row["blockers"]
                    and not _is_filled_recorded_buy(row)
                ]
            ),
            "exchange_side_counts": dict(Counter(row["side"] for row in [_normalize_trade(item) for item in exchange_trades])),
            "scoped_exchange_side_counts": {
                "BUY": len(exchange_buys),
                "SELL": len(exchange_sells),
            },
            "strong_matches": strong_matches,
            "weak_matches": weak_matches,
            "fill_mismatches": fill_mismatches,
            "external_exchange_sells_in_validation_scope": exchange_sells,
        },
    )


def _normalize_validation_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy_id": row.get("strategy_id"),
        "status": str(row.get("status") or ""),
        "blockers": [str(blocker) for blocker in row.get("blockers") or []],
        "side": _normalized_side(row.get("side") or "BUY"),
        "event_key": _text(row.get("event_key")),
        "event_token_key": _text(row.get("event_token_key")),
        "token_id": _text(row.get("token_id")),
        "event_slug": _text(row.get("event_slug")),
        "outcome": _text(row.get("outcome")),
        "order_key": _text(row.get("order_key")),
        "exchange_order_id": _text(row.get("exchange_order_id")),
        "order_status": _text(row.get("order_status")),
        "filled_shares": _optional_float(row.get("filled_shares")) or _filled_shares_from_position_key(row.get("position_key")),
        "fill_price": _optional_float(row.get("fill_price")) or _fill_price_from_position_key(row.get("position_key")),
    }


def _normalize_trade(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_id": _text(row.get("id")),
        "exchange_order_id": _text(row.get("taker_order_id") or row.get("order_id") or row.get("orderID")),
        "market": _text(row.get("market")),
        "token_id": _text(row.get("asset_id") or row.get("token_id")),
        "side": _normalized_side(row.get("side")),
        "outcome": _text(row.get("outcome")),
        "price": _optional_float(row.get("price")),
        "size": _optional_float(row.get("size")),
        "match_time": _text(row.get("match_time")),
        "transaction_hash": _text(row.get("transaction_hash")),
    }


def _find_exact_order_id_match(recorded: dict[str, Any], exchange_buys: list[dict[str, Any]], used_indexes: set[int]) -> int | None:
    exchange_order_id = recorded.get("exchange_order_id")
    if not _is_clob_order_id(exchange_order_id):
        return None
    for index, trade in enumerate(exchange_buys):
        if index in used_indexes:
            continue
        if exchange_order_id and exchange_order_id == trade.get("exchange_order_id"):
            return index
    return None


def _find_weak_match(recorded: dict[str, Any], exchange_buys: list[dict[str, Any]], used_indexes: set[int]) -> int | None:
    token_id = recorded.get("token_id")
    if not token_id:
        return None
    for index, trade in enumerate(exchange_buys):
        if index in used_indexes:
            continue
        if token_id != trade.get("token_id"):
            continue
        if recorded.get("outcome") and trade.get("outcome") and recorded["outcome"] != trade["outcome"]:
            continue
        if _compatible_fill(recorded, trade):
            return index
    return None


def _compatible_fill(recorded: dict[str, Any], trade: dict[str, Any], *, dust: float = 1e-6) -> bool:
    recorded_shares = recorded.get("filled_shares")
    recorded_price = recorded.get("fill_price")
    if recorded_shares is None or recorded_price is None:
        return False
    if recorded_shares is not None and trade.get("size") is not None and abs(float(recorded_shares) - float(trade["size"])) > dust:
        return False
    if recorded_price is not None and trade.get("price") is not None and abs(float(recorded_price) - float(trade["price"])) > 0.011:
        return False
    return True


def _missing_strong_identity(row: dict[str, Any]) -> bool:
    return not row.get("exchange_order_id") and not row.get("token_id")


def _is_filled_recorded_buy(row: dict[str, Any]) -> bool:
    order_status = str(row.get("order_status") or "").strip().lower()
    if order_status in {"unfilled", "rejected", "failed", "cancelled", "canceled", "blocked"}:
        return False
    shares = row.get("filled_shares")
    price = row.get("fill_price")
    try:
        return shares is not None and price is not None and float(shares) > 0 and float(price) > 0
    except (TypeError, ValueError):
        return False


def _scope_exchange_trades(exchange_trades: list[dict[str, Any]], recorded_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clob_order_ids = {
        str(row.get("exchange_order_id"))
        for row in recorded_rows
        if _is_clob_order_id(row.get("exchange_order_id"))
    }
    weak_scope_token_ids = {
        str(row.get("token_id"))
        for row in recorded_rows
        if row.get("token_id") and not _is_clob_order_id(row.get("exchange_order_id"))
    }
    if not clob_order_ids and not weak_scope_token_ids:
        return []
    scoped: list[dict[str, Any]] = []
    for trade in exchange_trades:
        if trade.get("exchange_order_id") in clob_order_ids or trade.get("token_id") in weak_scope_token_ids:
            scoped.append(trade)
    return scoped


def _is_clob_order_id(value: Any) -> bool:
    text = _text(value)
    return bool(text and text.startswith("0x") and len(text) >= 10)


def _filled_shares_from_position_key(value: Any) -> float | None:
    parts = str(value or "").split(":")
    if len(parts) < 2:
        return None
    return _optional_float(parts[-2])


def _fill_price_from_position_key(value: Any) -> float | None:
    parts = str(value or "").split(":")
    if len(parts) < 2:
        return None
    return _optional_float(parts[-1])


def _normalized_side(value: Any) -> str:
    side = str(value or "").strip().upper()
    return side if side in {"BUY", "SELL"} else "UNKNOWN"


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
