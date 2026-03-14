from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Any
from uuid import UUID

from fastapi import HTTPException


@dataclass(frozen=True)
class OrderRiskLimits:
    max_order_size: float
    min_limit_price: float
    max_limit_price: float
    max_notional: float
    max_ops_per_minute: int


def load_order_risk_limits() -> OrderRiskLimits:
    return OrderRiskLimits(
        max_order_size=float(os.getenv("JANUS_MAX_ORDER_SIZE", "10000")),
        min_limit_price=float(os.getenv("JANUS_MIN_LIMIT_PRICE", "0.001")),
        max_limit_price=float(os.getenv("JANUS_MAX_LIMIT_PRICE", "0.999")),
        max_notional=float(os.getenv("JANUS_MAX_ORDER_NOTIONAL", "5000")),
        max_ops_per_minute=int(os.getenv("JANUS_ORDER_OPS_PER_MIN", "30")),
    )


def enforce_order_risk_limits(
    *,
    size: float | None,
    limit_price: float | None,
    order_type: str,
    limits: OrderRiskLimits | None = None,
) -> None:
    config = limits or load_order_risk_limits()
    normalized_order_type = order_type.strip().lower()

    if size is None or size <= 0:
        raise HTTPException(status_code=422, detail="size must be > 0")
    if size > config.max_order_size:
        raise HTTPException(
            status_code=422,
            detail=f"size exceeds max_order_size={config.max_order_size}",
        )

    if normalized_order_type == "limit":
        if limit_price is None:
            raise HTTPException(status_code=422, detail="limit_price is required for limit orders")
        if limit_price < config.min_limit_price or limit_price > config.max_limit_price:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"limit_price must be between {config.min_limit_price} "
                    f"and {config.max_limit_price}"
                ),
            )
        if limit_price * size > config.max_notional:
            raise HTTPException(
                status_code=422,
                detail=f"order notional exceeds max_notional={config.max_notional}",
            )
    elif normalized_order_type == "market":
        if size > config.max_notional:
            raise HTTPException(
                status_code=422,
                detail=f"market size exceeds max_notional={config.max_notional}",
            )
    else:
        raise HTTPException(status_code=422, detail=f"unsupported order_type={order_type}")


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._lock = Lock()
        self._entries: dict[str, deque[float]] = {}

    def check(self, *, key: str, max_ops: int, window_sec: int = 60) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            queue = self._entries.setdefault(key, deque())
            floor = now - float(window_sec)
            while queue and queue[0] < floor:
                queue.popleft()
            allowed = len(queue) < max_ops
            if allowed:
                queue.append(now)
            return {
                "allowed": allowed,
                "count_in_window": len(queue),
                "window_sec": window_sec,
                "max_ops": max_ops,
            }


_ORDER_RATE_LIMITER = InMemoryRateLimiter()


def enforce_order_rate_limit(*, account_id: UUID, action: str, max_ops_per_minute: int) -> None:
    key = f"{account_id}:{action}"
    verdict = _ORDER_RATE_LIMITER.check(key=key, max_ops=max_ops_per_minute, window_sec=60)
    if not bool(verdict["allowed"]):
        raise HTTPException(
            status_code=429,
            detail=(
                f"rate limit exceeded for account action {action}; "
                f"max_ops_per_minute={max_ops_per_minute}"
            ),
        )
