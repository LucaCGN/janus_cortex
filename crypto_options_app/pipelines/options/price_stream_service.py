from __future__ import annotations

"""Read-only crypto price stream and indicator service.

The service captures public exchange market data, persists it into the separate
market-data SQLite store, and computes indicator snapshots. It has no trading
authority.
"""

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from crypto_options_app.data_nodes.crypto.candles import BINANCE_SYMBOL_MAP, BinanceKlineCandleProvider
from crypto_options_app.pipelines.options.indicators import build_indicator_snapshots
from crypto_options_app.pipelines.options.market_data_store import (
    default_market_data_store_path,
    load_candles,
    prune_market_data_retention,
    record_price_ingest_run,
    upsert_candles,
    upsert_indicator_snapshots,
    upsert_price_ticks,
)


PRICE_STREAM_SERVICE_SCHEMA_VERSION = "crypto_options_price_stream_service_v1"
BINANCE_MARKET_DATA_WS_BASE_URL = "wss://data-stream.binance.vision/stream?streams="


@dataclass(frozen=True)
class PriceStreamCaptureConfig:
    symbols: tuple[str, ...] = ("BTC", "ETH")
    max_messages: int = 20
    timeout_seconds: float = 20.0
    source: str = "binance_ws_trade"


def build_binance_combined_trade_stream_url(symbols: list[str] | tuple[str, ...]) -> str:
    streams = []
    for symbol in symbols:
        exchange_symbol = BINANCE_SYMBOL_MAP.get(str(symbol).upper(), str(symbol).upper())
        streams.append(f"{exchange_symbol.lower()}@trade")
    return BINANCE_MARKET_DATA_WS_BASE_URL + "/".join(streams)


def parse_binance_trade_message(message: str | bytes | dict[str, Any], *, observed_at_utc: Any | None = None) -> dict[str, Any] | None:
    payload = _decode_message(message)
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    event_type = str(data.get("e") or "")
    if event_type not in {"trade", "aggTrade"}:
        return None
    exchange_symbol = str(data.get("s") or "").upper()
    symbol = _reverse_binance_symbol(exchange_symbol)
    price = _float(data.get("p"))
    if not symbol or price is None:
        return None
    event_ts = data.get("T") or data.get("E")
    return {
        "symbol": symbol,
        "source": "binance_ws_trade" if event_type == "trade" else "binance_ws_agg_trade",
        "observed_at_utc": _text_time(observed_at_utc or _now()),
        "exchange_timestamp_utc": _epoch_ms_to_utc(event_ts),
        "price": price,
        "last_size": _float(data.get("q")),
        "raw_json": data,
    }


async def capture_binance_trade_stream(
    *,
    symbols: list[str] | tuple[str, ...] = ("BTC", "ETH"),
    max_messages: int = 20,
    timeout_seconds: float = 20.0,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Capture public Binance trade stream messages and persist ticks."""

    import websockets

    config = PriceStreamCaptureConfig(
        symbols=tuple(str(symbol).upper() for symbol in symbols),
        max_messages=max(1, int(max_messages)),
        timeout_seconds=float(timeout_seconds),
    )
    started_at = _now()
    run_id = _stable_run_id("binance_ws_trade", config.symbols, started_at)
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    url = build_binance_combined_trade_stream_url(config.symbols)
    try:
        async with websockets.connect(url, ping_interval=20, open_timeout=min(config.timeout_seconds, 20.0)) as websocket:
            while len(rows) < config.max_messages:
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=config.timeout_seconds)
                except asyncio.TimeoutError:
                    break
                row = parse_binance_trade_message(message, observed_at_utc=_now())
                if row:
                    rows.append(row)
    except Exception as exc:  # noqa: BLE001 - caller needs structured failure.
        errors.append({"error_type": type(exc).__name__, "error": str(exc)})

    inserted = upsert_price_ticks(rows, db_path=db_path or default_market_data_store_path(), run_id=run_id) if rows else 0
    status = "complete" if rows and not errors else ("partial" if rows else "failed")
    record_price_ingest_run(
        run_id=run_id,
        started_at_utc=started_at,
        completed_at_utc=_now(),
        status=status,
        source="binance_ws_trade",
        symbols=list(config.symbols),
        rows_observed=len(rows),
        rows_inserted=inserted,
        error_count=len(errors),
        summary={"url": _safe_url(url), "errors": errors, "config": asdict(config)},
        db_path=db_path or default_market_data_store_path(),
    )
    return {
        "schema_version": PRICE_STREAM_SERVICE_SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        "source": "binance_ws_trade",
        "symbols": list(config.symbols),
        "rows_observed": len(rows),
        "rows_inserted": inserted,
        "errors": errors,
        "latest_ticks": rows[-min(len(rows), 5) :],
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def fetch_and_store_binance_klines(
    *,
    symbols: list[str] | tuple[str, ...] = ("BTC", "ETH"),
    interval: str = "1m",
    lookback_minutes: int = 120,
    db_path: str | Path | None = None,
    provider: BinanceKlineCandleProvider | None = None,
) -> dict[str, Any]:
    """Fetch public Binance klines and persist normalized candles."""

    provider = provider or BinanceKlineCandleProvider()
    started_at = _now()
    normalized_symbols = tuple(str(symbol).upper() for symbol in symbols)
    run_id = _stable_run_id("binance_public_klines", normalized_symbols, interval, started_at)
    start = pd.Timestamp.utcnow() - pd.Timedelta(minutes=int(lookback_minutes))
    end = pd.Timestamp.utcnow()
    frames: list[pd.DataFrame] = []
    errors: list[dict[str, Any]] = []
    for symbol in normalized_symbols:
        try:
            frame = provider.fetch_candles(symbol=symbol, start=start, end=end, interval=interval)
            if not frame.empty:
                frames.append(frame)
        except Exception as exc:  # noqa: BLE001 - one symbol should not fail the full fetch.
            errors.append({"symbol": symbol, "error_type": type(exc).__name__, "error": str(exc)})
    rows = []
    if frames:
        combined = pd.concat(frames, ignore_index=True)
        rows = combined.to_dict(orient="records")
    inserted = upsert_candles(rows, db_path=db_path or default_market_data_store_path(), run_id=run_id) if rows else 0
    status = "complete" if rows and not errors else ("partial" if rows else "failed")
    record_price_ingest_run(
        run_id=run_id,
        started_at_utc=started_at,
        completed_at_utc=_now(),
        status=status,
        source="binance_public_klines",
        symbols=list(normalized_symbols),
        rows_observed=len(rows),
        rows_inserted=inserted,
        error_count=len(errors),
        summary={"interval": interval, "lookback_minutes": lookback_minutes, "errors": errors},
        db_path=db_path or default_market_data_store_path(),
    )
    return {
        "schema_version": "crypto_options_kline_fetch_result_v1",
        "run_id": run_id,
        "status": status,
        "symbols": list(normalized_symbols),
        "interval": interval,
        "lookback_minutes": lookback_minutes,
        "rows_observed": len(rows),
        "rows_inserted": inserted,
        "errors": errors,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def compute_and_store_market_indicators(
    *,
    symbols: list[str] | tuple[str, ...] = ("BTC", "ETH"),
    interval: str = "1m",
    lookback_minutes: int = 180,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Compute latest indicator snapshots from stored candles."""

    candle_rows = load_candles(
        symbols=tuple(str(symbol).upper() for symbol in symbols),
        interval=interval,
        lookback_minutes=lookback_minutes,
        limit=max(5000, int(lookback_minutes) * max(len(symbols), 1) + 100),
        db_path=db_path or default_market_data_store_path(),
    )
    if not candle_rows:
        return {
            "schema_version": "crypto_options_indicator_compute_result_v1",
            "status": "no_candles",
            "symbols": [str(symbol).upper() for symbol in symbols],
            "interval": interval,
            "snapshots_inserted": 0,
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    candles = pd.DataFrame(candle_rows)
    snapshots = build_indicator_snapshots(candles, interval=interval, source="local_market_data_store")
    inserted = upsert_indicator_snapshots(snapshots, db_path=db_path or default_market_data_store_path())
    return {
        "schema_version": "crypto_options_indicator_compute_result_v1",
        "status": "complete" if inserted else "empty",
        "symbols": [str(symbol).upper() for symbol in symbols],
        "interval": interval,
        "lookback_minutes": lookback_minutes,
        "candles_loaded": len(candle_rows),
        "snapshots_observed": len(snapshots),
        "snapshots_inserted": inserted,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


async def run_market_data_collection_loop(
    *,
    symbols: list[str] | tuple[str, ...] = ("BTC", "ETH"),
    interval: str = "1m",
    kline_lookback_minutes: int = 180,
    loop_interval_seconds: float = 30.0,
    stream_max_messages: int = 20,
    stream_timeout_seconds: float = 20.0,
    max_iterations: int = 1,
    retention_days: int = 90,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the forward market-data collection loop.

    `max_iterations=0` runs forever and is intended for a scheduler/automation
    process. Tests and smoke checks should pass a positive bounded value.
    """

    iterations: list[dict[str, Any]] = []
    iteration = 0
    while max_iterations == 0 or iteration < int(max_iterations):
        iteration += 1
        stream_result = await capture_binance_trade_stream(
            symbols=symbols,
            max_messages=stream_max_messages,
            timeout_seconds=stream_timeout_seconds,
            db_path=db_path or default_market_data_store_path(),
        )
        kline_result = fetch_and_store_binance_klines(
            symbols=symbols,
            interval=interval,
            lookback_minutes=kline_lookback_minutes,
            db_path=db_path or default_market_data_store_path(),
        )
        indicator_result = compute_and_store_market_indicators(
            symbols=symbols,
            interval=interval,
            lookback_minutes=kline_lookback_minutes,
            db_path=db_path or default_market_data_store_path(),
        )
        prune_result = prune_market_data_retention(retention_days=retention_days, db_path=db_path or default_market_data_store_path())
        iterations.append(
            {
                "iteration": iteration,
                "stream": stream_result,
                "klines": kline_result,
                "indicators": indicator_result,
                "retention": prune_result,
            }
        )
        if max_iterations == 0 or iteration < int(max_iterations):
            await asyncio.sleep(float(loop_interval_seconds))
    return {
        "schema_version": "crypto_options_market_data_collection_loop_result_v1",
        "status": "complete",
        "iterations": iterations,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def _decode_message(message: str | bytes | dict[str, Any]) -> Any:
    if isinstance(message, dict):
        return message
    if isinstance(message, bytes):
        message = message.decode("utf-8")
    try:
        return json.loads(message)
    except (TypeError, json.JSONDecodeError):
        return None


def _reverse_binance_symbol(exchange_symbol: str) -> str:
    for symbol, mapped in BINANCE_SYMBOL_MAP.items():
        if mapped.upper() == exchange_symbol.upper():
            return symbol
    if exchange_symbol.endswith("USDT"):
        return exchange_symbol[:-4]
    return exchange_symbol


def _epoch_ms_to_utc(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text_time(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        parsed = pd.to_datetime(value, utc=True)
        if pd.isna(parsed):
            return None
        return parsed.isoformat()
    except (TypeError, ValueError, OverflowError):
        return str(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_run_id(*parts: Any) -> str:
    import hashlib

    raw = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _safe_url(url: str) -> str:
    return url.split("?streams=", 1)[0] + "?streams=..."


__all__ = [
    "BINANCE_MARKET_DATA_WS_BASE_URL",
    "PRICE_STREAM_SERVICE_SCHEMA_VERSION",
    "PriceStreamCaptureConfig",
    "build_binance_combined_trade_stream_url",
    "capture_binance_trade_stream",
    "compute_and_store_market_indicators",
    "fetch_and_store_binance_klines",
    "parse_binance_trade_message",
    "run_market_data_collection_loop",
]
