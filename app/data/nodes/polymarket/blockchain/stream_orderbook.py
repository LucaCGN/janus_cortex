#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stream_orderbook.py
-------------------

Node for fetching/streaming CLOB orderbook depth.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.exceptions import PolyApiException

from app.data.nodes.polymarket.blockchain.manage_portfolio import PolymarketCredentials

logger = logging.getLogger(__name__)
_ORDERBOOK_WARNING_LOG_COOLDOWN_SEC = 300.0
_ORDERBOOK_WARNING_LOG_STATE: dict[str, float] = {}


@dataclass
class OrderbookLevel:
    price: float
    size: float


@dataclass
class OrderbookSnapshot:
    market_id: Optional[str] = None
    token_id: str = ""
    bids: List[OrderbookLevel] = field(default_factory=list)
    asks: List[OrderbookLevel] = field(default_factory=list)
    min_order_size: float | None = None
    tick_size: float | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class OrderbookStreamConfig:
    market_id: Optional[str] = None
    token_id: str = ""
    poll_interval_seconds: float = 2.0
    max_iterations: Optional[int] = 1
    continue_on_error: bool = True


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_levels(raw_levels: Any) -> List[OrderbookLevel]:
    if not isinstance(raw_levels, list):
        return []

    out: List[OrderbookLevel] = []
    for item in raw_levels:
        if isinstance(item, dict):
            price = _to_float(item.get("price"))
            size = _to_float(item.get("size"))
        else:
            price = _to_float(getattr(item, "price", None))
            size = _to_float(getattr(item, "size", None))

        if price <= 0 or size <= 0:
            continue
        out.append(OrderbookLevel(price=price, size=size))
    return out


def _raw_orderbook_value(raw: Any, key: str, default: Any = None) -> Any:
    if isinstance(raw, dict):
        return raw.get(key, default)
    return getattr(raw, key, default)


def _build_client(creds: PolymarketCredentials, client_factory: Any = ClobClient) -> Any:
    return client_factory(
        host=creds.clob_host or "https://clob.polymarket.com",
        key=creds.private_key,
        chain_id=creds.chain_id or 137,
    )


def _is_missing_orderbook_exception(exc: Exception) -> bool:
    if not isinstance(exc, PolyApiException):
        return False
    message = str(getattr(exc, "error_msg", exc) or "").lower()
    return int(getattr(exc, "status_code", 0) or 0) == 404 and "no orderbook exists" in message


def _log_missing_orderbook_once(*, token_id: str, market_id: Optional[str], exc: Exception) -> None:
    now = time.monotonic()
    cache_key = str(token_id or "")
    last_logged_at = _ORDERBOOK_WARNING_LOG_STATE.get(cache_key)
    if last_logged_at is None or (now - last_logged_at) >= _ORDERBOOK_WARNING_LOG_COOLDOWN_SEC:
        _ORDERBOOK_WARNING_LOG_STATE[cache_key] = now
        logger.warning(
            "fetch_orderbook: missing orderbook token_id=%s market_id=%s; treating as unavailable quote and retrying later: %s",
            token_id,
            market_id,
            exc,
        )
        return
    logger.debug("fetch_orderbook: missing orderbook suppressed token_id=%s market_id=%s error=%r", token_id, market_id, exc)


def fetch_orderbook(
    creds: PolymarketCredentials,
    token_id: str,
    market_id: Optional[str] = None,
    client_factory: Any = ClobClient,
) -> OrderbookSnapshot:
    """
    Fetches the current orderbook for a given token_id.
    """
    if not token_id:
        return OrderbookSnapshot(market_id=market_id, token_id="", bids=[], asks=[])

    try:
        client = _build_client(creds, client_factory=client_factory)
        raw = client.get_order_book(token_id)

        bids = _extract_levels(_raw_orderbook_value(raw, "bids", []))
        asks = _extract_levels(_raw_orderbook_value(raw, "asks", []))

        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)

        return OrderbookSnapshot(
            market_id=market_id,
            token_id=token_id,
            bids=bids,
            asks=asks,
            min_order_size=_to_float(_raw_orderbook_value(raw, "min_order_size"), default=0.0) or None,
            tick_size=_to_float(_raw_orderbook_value(raw, "tick_size"), default=0.0) or None,
        )
    except Exception as exc:  # noqa: BLE001
        if _is_missing_orderbook_exception(exc):
            _log_missing_orderbook_once(token_id=token_id, market_id=market_id, exc=exc)
            return OrderbookSnapshot(market_id=market_id, token_id=token_id, bids=[], asks=[])
        logger.error("fetch_orderbook: failed token_id=%s error=%r", token_id, exc)
        raise


def stream_orderbook(
    config: OrderbookStreamConfig,
    callback: Optional[Callable[[OrderbookSnapshot], None]] = None,
    creds: Optional[PolymarketCredentials] = None,
    client_factory: Any = ClobClient,
) -> List[OrderbookSnapshot]:
    """
    Polls orderbook snapshots and optionally emits each snapshot to callback.
    """
    if not config.token_id:
        raise ValueError("stream_orderbook: config.token_id is required")

    if creds is None:
        creds = PolymarketCredentials(
            wallet_address="",
            private_key=None,
            clob_host="https://clob.polymarket.com",
            chain_id=137,
        )

    snapshots: List[OrderbookSnapshot] = []
    iteration = 0

    while True:
        if config.max_iterations is not None and iteration >= config.max_iterations:
            break

        try:
            snap = fetch_orderbook(
                creds=creds,
                token_id=config.token_id,
                market_id=config.market_id,
                client_factory=client_factory,
            )
            snapshots.append(snap)
            if callback is not None:
                callback(snap)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "stream_orderbook: iteration_failed token_id=%s iteration=%d error=%r",
                config.token_id,
                iteration + 1,
                exc,
            )
            if not config.continue_on_error:
                raise

        iteration += 1
        if config.max_iterations is not None and iteration >= config.max_iterations:
            break
        if config.poll_interval_seconds > 0:
            time.sleep(config.poll_interval_seconds)

    return snapshots
