from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from crypto_options_app.workers.feed_worker import FeedWorkerConfig


def underlying_price_stream_config() -> FeedWorkerConfig:
    return FeedWorkerConfig(
        service_name="crypto_options_app",
        module_id="underlying_prices",
        data_type="underlying_prices",
        provider="crypto_exchange",
        transport="websocket",
        max_concurrency=1,
        stale_after_seconds=15,
        metadata={"writes": ["underlying_price_ticks"]},
    )


def underlying_candle_backfill_config(*, max_concurrency: int = 2) -> FeedWorkerConfig:
    return FeedWorkerConfig(
        service_name="crypto_options_app",
        module_id="underlying_candles",
        data_type="underlying_candles",
        provider="crypto_exchange",
        transport="rest",
        max_concurrency=max_concurrency,
        stale_after_seconds=300,
        metadata={"writes": ["underlying_candles"]},
    )


def normalize_underlying_tick(raw: dict[str, Any], *, inserted_at_utc: datetime) -> dict[str, Any]:
    observed = _parse_datetime(raw.get("observed_at_utc") or raw.get("timestamp") or inserted_at_utc)
    symbol = str(raw["symbol"]).upper()
    payload = {
        "symbol": symbol,
        "source": raw.get("source", "unknown"),
        "observed_at_utc": observed.isoformat(),
        "exchange_timestamp_utc": _iso_or_none(raw.get("exchange_timestamp_utc") or raw.get("exchange_timestamp")),
        "price": float(raw["price"]),
        "bid": _optional_float(raw.get("bid")),
        "ask": _optional_float(raw.get("ask")),
        "source_json": json.dumps(raw, sort_keys=True, default=str),
        "inserted_at_utc": inserted_at_utc.astimezone(UTC).isoformat(),
    }
    payload["tick_key"] = raw.get("tick_key") or _stable_key("underlying_tick", symbol, payload["observed_at_utc"], payload["source"])
    return payload


def normalize_underlying_candle(raw: dict[str, Any], *, inserted_at_utc: datetime) -> dict[str, Any]:
    symbol = str(raw["symbol"]).upper()
    opened = _parse_datetime(raw["opened_at_utc"])
    payload = {
        "symbol": symbol,
        "exchange": raw.get("exchange", "unknown"),
        "interval": raw["interval"],
        "opened_at_utc": opened.isoformat(),
        "closed_at_utc": _iso_or_none(raw.get("closed_at_utc")),
        "open": float(raw["open"]),
        "high": float(raw["high"]),
        "low": float(raw["low"]),
        "close": float(raw["close"]),
        "volume": _optional_float(raw.get("volume")),
        "source_json": json.dumps(raw, sort_keys=True, default=str),
        "inserted_at_utc": inserted_at_utc.astimezone(UTC).isoformat(),
    }
    payload["candle_key"] = raw.get("candle_key") or _stable_key("underlying_candle", symbol, payload["interval"], payload["opened_at_utc"])
    return payload


def insert_underlying_tick(conn: Any, tick: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO underlying_price_ticks(
            tick_key, symbol, source, observed_at_utc, exchange_timestamp_utc,
            price, bid, ask, source_json, inserted_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tick_key) DO NOTHING
        """,
        (
            tick["tick_key"],
            tick["symbol"],
            tick["source"],
            tick["observed_at_utc"],
            tick.get("exchange_timestamp_utc"),
            tick["price"],
            tick.get("bid"),
            tick.get("ask"),
            tick.get("source_json", "{}"),
            tick["inserted_at_utc"],
        ),
    )


def insert_underlying_candle(conn: Any, candle: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO underlying_candles(
            candle_key, symbol, exchange, interval, opened_at_utc, closed_at_utc,
            open, high, low, close, volume, source_json, inserted_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(candle_key) DO NOTHING
        """,
        (
            candle["candle_key"],
            candle["symbol"],
            candle["exchange"],
            candle["interval"],
            candle["opened_at_utc"],
            candle.get("closed_at_utc"),
            candle["open"],
            candle["high"],
            candle["low"],
            candle["close"],
            candle.get("volume"),
            candle.get("source_json", "{}"),
            candle["inserted_at_utc"],
        ),
    )


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return _parse_datetime(value).isoformat()


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _stable_key(*parts: Any) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
