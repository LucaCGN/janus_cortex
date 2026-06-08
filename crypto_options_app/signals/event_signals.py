from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class EventContext:
    context_key: str
    event_key: str
    event_token_key: str | None
    symbol: str
    side: str
    event_threshold_price: float
    computed_at_utc: datetime
    event_end_time_utc: datetime
    underlying_price: float
    target_delta_abs: float
    target_delta_signed_for_side: float
    time_remaining_seconds: float
    indicator_summary: dict[str, Any] = field(default_factory=dict)
    blocker_summary: dict[str, Any] = field(default_factory=dict)


def build_event_context(
    *,
    event_key: str,
    symbol: str,
    side: str,
    event_threshold_price: float,
    underlying_price: float,
    event_end_time_utc: datetime,
    computed_at_utc: datetime,
    event_token_key: str | None = None,
    indicator_summary: dict[str, Any] | None = None,
    blocker_summary: dict[str, Any] | None = None,
) -> EventContext:
    normalized_side = side.strip().lower()
    signed = underlying_price - event_threshold_price if normalized_side in {"up", "yes"} else event_threshold_price - underlying_price
    context_key = _stable_key("event_context", event_key, event_token_key, side, computed_at_utc.isoformat())
    return EventContext(
        context_key=context_key,
        event_key=event_key,
        event_token_key=event_token_key,
        symbol=symbol.upper(),
        side=side,
        event_threshold_price=event_threshold_price,
        computed_at_utc=computed_at_utc.astimezone(UTC),
        event_end_time_utc=event_end_time_utc.astimezone(UTC),
        underlying_price=underlying_price,
        target_delta_abs=abs(underlying_price - event_threshold_price),
        target_delta_signed_for_side=signed,
        time_remaining_seconds=max(0.0, (event_end_time_utc.astimezone(UTC) - computed_at_utc.astimezone(UTC)).total_seconds()),
        indicator_summary=indicator_summary or {},
        blocker_summary=blocker_summary or {},
    )


def insert_event_context(conn: Any, context: EventContext) -> None:
    conn.execute(
        """
        INSERT INTO event_indicator_context(
            context_key, event_key, event_token_key, symbol, side, event_threshold_price,
            computed_at_utc, underlying_price, target_delta_abs, target_delta_signed_for_side,
            indicator_summary_json, blocker_summary_json, inserted_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(context_key) DO NOTHING
        """,
        (
            context.context_key,
            context.event_key,
            context.event_token_key,
            context.symbol,
            context.side,
            context.event_threshold_price,
            context.computed_at_utc.isoformat(),
            context.underlying_price,
            context.target_delta_abs,
            context.target_delta_signed_for_side,
            json.dumps(context.indicator_summary, sort_keys=True, default=str),
            json.dumps({**context.blocker_summary, "time_remaining_seconds": context.time_remaining_seconds}, sort_keys=True, default=str),
            datetime.now(UTC).isoformat(),
        ),
    )


def _stable_key(*parts: Any) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
