from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from crypto_options_app.workers.feed_worker import FeedWorkerConfig


def polymarket_price_stream_config() -> FeedWorkerConfig:
    return FeedWorkerConfig(
        service_name="crypto_options_app",
        module_id="polymarket_prices",
        data_type="polymarket_event_prices",
        provider="polymarket_clob",
        transport="websocket",
        max_concurrency=1,
        stale_after_seconds=30,
        metadata={"writes": ["polymarket_price_ticks", "polymarket_order_books", "polymarket_trade_prints"]},
    )


def polymarket_price_rest_fallback_config(*, max_concurrency: int = 3) -> FeedWorkerConfig:
    return FeedWorkerConfig(
        service_name="crypto_options_app",
        module_id="polymarket_prices_rest_fallback",
        data_type="polymarket_event_prices",
        provider="polymarket_clob",
        transport="rest",
        max_concurrency=max_concurrency,
        stale_after_seconds=60,
        metadata={"fallback_for": "polymarket_prices"},
    )


def normalize_polymarket_price_tick(
    raw: dict[str, Any],
    *,
    system_received_at_utc: datetime,
    system_inserted_at_utc: datetime,
    previous_mid_price: float | None = None,
) -> dict[str, Any]:
    chart_time = _parse_datetime(raw.get("chart_timestamp_utc") or raw.get("timestamp") or system_received_at_utc)
    best_bid = _optional_float(raw.get("best_bid"))
    best_ask = _optional_float(raw.get("best_ask"))
    mid_price = _optional_float(raw.get("mid_price"))
    if mid_price is None and best_bid is not None and best_ask is not None:
        mid_price = (best_bid + best_ask) / 2.0
    spread = None if best_bid is None or best_ask is None else best_ask - best_bid
    token_id = str(raw["token_id"])
    event_token_key = raw.get("event_token_key")
    price_delta = None if previous_mid_price is None or mid_price is None else mid_price - previous_mid_price
    payload = {
        "event_token_key": event_token_key,
        "event_key": raw.get("event_key"),
        "token_id": token_id,
        "event_slug": raw.get("event_slug"),
        "outcome": raw.get("outcome"),
        "chart_timestamp_utc": chart_time.isoformat(),
        "system_received_at_utc": system_received_at_utc.astimezone(UTC).isoformat(),
        "system_inserted_at_utc": system_inserted_at_utc.astimezone(UTC).isoformat(),
        "source_latency_ms": (system_received_at_utc.astimezone(UTC) - chart_time).total_seconds() * 1000.0,
        "insert_latency_ms": (system_inserted_at_utc.astimezone(UTC) - system_received_at_utc.astimezone(UTC)).total_seconds() * 1000.0,
        "mid_price": mid_price,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "depth_top3_bid_size": _optional_float(raw.get("depth_top3_bid_size")),
        "depth_top3_ask_size": _optional_float(raw.get("depth_top3_ask_size")),
        "trade_price": _optional_float(raw.get("trade_price")),
        "trade_size": _optional_float(raw.get("trade_size")),
        "source_json": json.dumps({**raw, "price_delta": price_delta}, sort_keys=True, default=str),
    }
    payload["price_tick_key"] = raw.get("price_tick_key") or _stable_key("polymarket_price", token_id, payload["system_received_at_utc"])
    return payload


def insert_polymarket_price_tick(conn: Any, tick: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO polymarket_price_ticks(
            price_tick_key, event_token_key, event_key, token_id, event_slug, outcome,
            chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
            source_latency_ms, insert_latency_ms, mid_price, best_bid, best_ask,
            spread, depth_top3_bid_size, depth_top3_ask_size, trade_price, trade_size, source_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(price_tick_key) DO NOTHING
        """,
        (
            tick["price_tick_key"],
            tick.get("event_token_key"),
            tick.get("event_key"),
            tick["token_id"],
            tick.get("event_slug"),
            tick.get("outcome"),
            tick.get("chart_timestamp_utc"),
            tick["system_received_at_utc"],
            tick["system_inserted_at_utc"],
            tick.get("source_latency_ms"),
            tick.get("insert_latency_ms"),
            tick.get("mid_price"),
            tick.get("best_bid"),
            tick.get("best_ask"),
            tick.get("spread"),
            tick.get("depth_top3_bid_size"),
            tick.get("depth_top3_ask_size"),
            tick.get("trade_price"),
            tick.get("trade_size"),
            tick.get("source_json", "{}"),
        ),
    )


def normalize_order_book_levels(
    raw_book: dict[str, Any],
    *,
    order_book_key: str,
    event_token_key: str | None,
    token_id: str,
    observed_at_utc: datetime,
    inserted_at_utc: datetime,
    max_depth: int = 10,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for side in ("bid", "ask"):
        levels = raw_book.get(f"{side}s") or raw_book.get(side) or []
        for index, level in enumerate(levels[:max(0, max_depth)]):
            price = _level_float(level, "price")
            size = _level_float(level, "size")
            if price is None or size is None:
                continue
            rows.append(
                {
                    "order_book_level_key": _stable_key(
                        "order_book_level",
                        order_book_key,
                        side,
                        index,
                        price,
                        size,
                    ),
                    "order_book_key": order_book_key,
                    "event_token_key": event_token_key,
                    "token_id": token_id,
                    "observed_at_utc": observed_at_utc.astimezone(UTC).isoformat(),
                    "side": side,
                    "price": price,
                    "size": size,
                    "level_index": index,
                    "source_json": json.dumps(level, sort_keys=True, default=str),
                    "inserted_at_utc": inserted_at_utc.astimezone(UTC).isoformat(),
                }
            )
    return rows


def insert_polymarket_order_book_levels(conn: Any, levels: list[dict[str, Any]]) -> None:
    conn.executemany(
        """
        INSERT INTO polymarket_order_book_levels(
            order_book_level_key, order_book_key, event_token_key, token_id,
            observed_at_utc, side, price, size, level_index, source_json, inserted_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(order_book_level_key) DO NOTHING
        """,
        [
            (
                level["order_book_level_key"],
                level.get("order_book_key"),
                level.get("event_token_key"),
                level["token_id"],
                level["observed_at_utc"],
                level["side"],
                level["price"],
                level["size"],
                level["level_index"],
                level.get("source_json", "{}"),
                level["inserted_at_utc"],
            )
            for level in levels
        ],
    )


def build_updown_pair_snapshot(
    *,
    event_key: str,
    bucket_timestamp_utc: datetime,
    up_tick: dict[str, Any],
    down_tick: dict[str, Any],
    event_slug: str | None = None,
    symbol: str | None = None,
) -> dict[str, Any]:
    bucket = bucket_timestamp_utc.astimezone(UTC).isoformat()
    up_token_id = str(up_tick.get("token_id") or "")
    down_token_id = str(down_tick.get("token_id") or "")
    source_latency_values = [
        value
        for value in (
            _optional_float(up_tick.get("source_latency_ms")),
            _optional_float(down_tick.get("source_latency_ms")),
        )
        if value is not None
    ]
    return {
        "pair_snapshot_key": _stable_key("updown_pair", event_key, bucket, up_token_id, down_token_id),
        "event_key": event_key,
        "event_slug": event_slug or up_tick.get("event_slug") or down_tick.get("event_slug"),
        "symbol": (symbol or up_tick.get("symbol") or down_tick.get("symbol") or "").upper() or None,
        "bucket_timestamp_utc": bucket,
        "up_event_token_key": up_tick.get("event_token_key"),
        "down_event_token_key": down_tick.get("event_token_key"),
        "up_token_id": up_token_id,
        "down_token_id": down_token_id,
        "up_best_bid": _optional_float(up_tick.get("best_bid")),
        "up_best_ask": _optional_float(up_tick.get("best_ask")),
        "up_mid_price": _optional_float(up_tick.get("mid_price")),
        "down_best_bid": _optional_float(down_tick.get("best_bid")),
        "down_best_ask": _optional_float(down_tick.get("best_ask")),
        "down_mid_price": _optional_float(down_tick.get("mid_price")),
        "up_depth_top3_bid_size": _optional_float(up_tick.get("depth_top3_bid_size")),
        "up_depth_top3_ask_size": _optional_float(up_tick.get("depth_top3_ask_size")),
        "down_depth_top3_bid_size": _optional_float(down_tick.get("depth_top3_bid_size")),
        "down_depth_top3_ask_size": _optional_float(down_tick.get("depth_top3_ask_size")),
        "source_latency_ms": max(source_latency_values) if source_latency_values else None,
        "source_json": json.dumps({"up_tick": up_tick, "down_tick": down_tick}, sort_keys=True, default=str),
        "inserted_at_utc": datetime.now(UTC).isoformat(),
    }


def insert_updown_pair_snapshot(conn: Any, snapshot: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO polymarket_updown_pair_snapshots(
            pair_snapshot_key, event_key, event_slug, symbol, bucket_timestamp_utc,
            up_event_token_key, down_event_token_key, up_token_id, down_token_id,
            up_best_bid, up_best_ask, up_mid_price, down_best_bid, down_best_ask, down_mid_price,
            up_depth_top3_bid_size, up_depth_top3_ask_size, down_depth_top3_bid_size, down_depth_top3_ask_size,
            source_latency_ms, source_json, inserted_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(pair_snapshot_key) DO NOTHING
        """,
        (
            snapshot["pair_snapshot_key"],
            snapshot["event_key"],
            snapshot.get("event_slug"),
            snapshot.get("symbol"),
            snapshot["bucket_timestamp_utc"],
            snapshot.get("up_event_token_key"),
            snapshot.get("down_event_token_key"),
            snapshot.get("up_token_id"),
            snapshot.get("down_token_id"),
            snapshot.get("up_best_bid"),
            snapshot.get("up_best_ask"),
            snapshot.get("up_mid_price"),
            snapshot.get("down_best_bid"),
            snapshot.get("down_best_ask"),
            snapshot.get("down_mid_price"),
            snapshot.get("up_depth_top3_bid_size"),
            snapshot.get("up_depth_top3_ask_size"),
            snapshot.get("down_depth_top3_bid_size"),
            snapshot.get("down_depth_top3_ask_size"),
            snapshot.get("source_latency_ms"),
            snapshot.get("source_json", "{}"),
            snapshot["inserted_at_utc"],
        ),
    )


def normalize_polymarket_trade_print(
    raw: dict[str, Any],
    *,
    event_token_key: str | None = None,
    inserted_at_utc: datetime | None = None,
) -> dict[str, Any]:
    token_id = str(
        raw.get("token_id")
        or raw.get("asset_id")
        or raw.get("asset")
        or raw.get("market")
        or ""
    )
    if not token_id:
        raise ValueError("trade_print_missing_token_id")
    trade_time = _parse_datetime(
        raw.get("trade_at_utc")
        or raw.get("created_at")
        or raw.get("createdAt")
        or raw.get("timestamp")
        or raw.get("time")
        or datetime.now(UTC)
    )
    inserted_at = (inserted_at_utc or datetime.now(UTC)).astimezone(UTC)
    price = _optional_float(raw.get("price") or raw.get("trade_price"))
    size = _optional_float(raw.get("size") or raw.get("trade_size") or raw.get("shares"))
    side = raw.get("side") or raw.get("taker_side") or raw.get("type")
    trade_id = raw.get("id") or raw.get("trade_id") or raw.get("transactionHash") or raw.get("transaction_hash")
    return {
        "trade_print_key": raw.get("trade_print_key") or _stable_key(
            "polymarket_trade_print",
            token_id,
            trade_time.isoformat(),
            price,
            size,
            side,
            trade_id,
        ),
        "event_token_key": event_token_key or raw.get("event_token_key"),
        "token_id": token_id,
        "trade_at_utc": trade_time.isoformat(),
        "price": price,
        "size": size,
        "side": None if side is None else str(side).upper(),
        "source_json": json.dumps(raw, sort_keys=True, default=str),
        "inserted_at_utc": inserted_at.isoformat(),
    }


def insert_polymarket_trade_print(conn: Any, trade: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO polymarket_trade_prints(
            trade_print_key, event_token_key, token_id, trade_at_utc,
            price, size, side, source_json, inserted_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(trade_print_key) DO NOTHING
        """,
        (
            trade["trade_print_key"],
            trade.get("event_token_key"),
            trade["token_id"],
            trade["trade_at_utc"],
            trade.get("price"),
            trade.get("size"),
            trade.get("side"),
            trade.get("source_json", "{}"),
            trade["inserted_at_utc"],
        ),
    )


def insert_polymarket_trade_prints(conn: Any, trades: list[dict[str, Any]]) -> None:
    for trade in trades:
        insert_polymarket_trade_print(conn, trade)


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(float(value), tz=UTC)
    else:
        text = str(value)
        if text.isdigit():
            parsed = datetime.fromtimestamp(float(text), tz=UTC)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _level_float(level: Any, key: str) -> float | None:
    if isinstance(level, dict):
        raw = level.get(key)
    elif isinstance(level, (list, tuple)) and key == "price" and level:
        raw = level[0]
    elif isinstance(level, (list, tuple)) and key == "size" and len(level) > 1:
        raw = level[1]
    else:
        raw = None
    return _optional_float(raw)


def _stable_key(*parts: Any) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
