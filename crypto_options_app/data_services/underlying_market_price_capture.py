from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.config import CENTRAL_DB_PATH
from crypto_options_app.db.connection import connect
from crypto_options_app.db.postgres_connection import should_use_postgres_runtime
from crypto_options_app.db.schema import create_schema, initialize_schema
from crypto_options_app.feeds.underlying_prices import (
    insert_underlying_candle,
    insert_underlying_tick,
    normalize_underlying_candle,
    normalize_underlying_tick,
)
from crypto_options_app.workers.feed_worker import write_watermark


COINBASE_PRODUCT_IDS = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
}
LIVE_FLAG_NAMES = (
    "JANUS_CRYPTO_OPTIONS_LIVE_EXECUTE",
    "JANUS_CRYPTO_OPTIONS_LIVE_APPROVED",
    "JANUS_CRYPTO_OPTIONS_ACK_LIVE_RISK",
)


@dataclass(frozen=True)
class UnderlyingMarketPriceConfig:
    db_path: Path = CENTRAL_DB_PATH
    symbols: tuple[str, ...] = ("BTC", "ETH")
    timeout_seconds: int = 8
    candle_granularity_seconds: int = 60
    candle_limit: int = 3
    target_refresh_seconds: int = 30
    module_id: str = "underlying_market_prices"


@dataclass(frozen=True)
class UnderlyingMarketPriceSummary:
    generated_at_utc: str
    status: str
    db_path: str
    tick_rows_inserted: int
    candle_rows_inserted: int
    readiness_rows_inserted: int
    blockers: tuple[str, ...] = ()
    state: dict[str, Any] = field(default_factory=dict)
    orders_allowed: bool = False
    live_trading_authorized: bool = False


def capture_underlying_market_prices_once(
    *,
    config: UnderlyingMarketPriceConfig,
) -> UnderlyingMarketPriceSummary:
    _reject_live_env_flags()
    if not should_use_postgres_runtime(config.db_path):
        initialize_schema(config.db_path)
    generated_at = datetime.now(UTC)
    inserted_ticks = 0
    inserted_candles = 0
    readiness_rows = 0
    blockers: list[str] = []
    observed_symbols: list[str] = []

    with connect(config.db_path) as conn:
        if not getattr(conn, "is_postgres", False):
            create_schema(conn)
        for symbol in tuple(dict.fromkeys(symbol.upper() for symbol in config.symbols)):
            product_id = COINBASE_PRODUCT_IDS.get(symbol)
            if product_id is None:
                blockers.append(f"unsupported_symbol:{symbol}")
                continue
            try:
                tick = _fetch_coinbase_tick(symbol=symbol, product_id=product_id, timeout_seconds=config.timeout_seconds)
                insert_underlying_tick(conn, normalize_underlying_tick(tick, inserted_at_utc=generated_at))
                inserted_ticks += int(conn.execute("SELECT changes() AS c").fetchone()["c"])
                observed_symbols.append(symbol)
            except Exception as exc:  # noqa: BLE001 - data service reports provider failures.
                blockers.append(f"coinbase_tick:{symbol}:{type(exc).__name__}:{exc}")
            try:
                for candle in _fetch_coinbase_candles(
                    symbol=symbol,
                    product_id=product_id,
                    granularity_seconds=config.candle_granularity_seconds,
                    limit=config.candle_limit,
                    timeout_seconds=config.timeout_seconds,
                ):
                    insert_underlying_candle(conn, normalize_underlying_candle(candle, inserted_at_utc=generated_at))
                    inserted_candles += int(conn.execute("SELECT changes() AS c").fetchone()["c"])
            except Exception as exc:  # noqa: BLE001
                blockers.append(f"coinbase_candles:{symbol}:{type(exc).__name__}:{exc}")
        readiness_rows = _write_readiness(
            conn,
            config=config,
            generated_at_utc=generated_at,
            observed_symbols=tuple(observed_symbols),
            blockers=tuple(blockers),
            tick_rows=inserted_ticks,
            candle_rows=inserted_candles,
        )
        status = "failed" if blockers and not observed_symbols else "degraded" if blockers else "healthy"
        write_watermark(
            conn,
            service_name="crypto_options_app",
            module_id=config.module_id,
            status=status,
            last_run_at_utc=generated_at.isoformat(),
            rows_observed=len(observed_symbols),
            rows_inserted=inserted_ticks + inserted_candles + readiness_rows,
            error_count=len(blockers),
            source="coinbase_exchange_rest",
            state={
                "symbols": list(config.symbols),
                "observed_symbols": observed_symbols,
                "tick_rows_inserted": inserted_ticks,
                "candle_rows_inserted": inserted_candles,
                "readiness_rows_inserted": readiness_rows,
                "blockers": blockers[:20],
                "orders_allowed": False,
                "live_trading_authorized": False,
            },
        )

    return UnderlyingMarketPriceSummary(
        generated_at_utc=generated_at.isoformat(),
        status=status,
        db_path=str(config.db_path),
        tick_rows_inserted=inserted_ticks,
        candle_rows_inserted=inserted_candles,
        readiness_rows_inserted=readiness_rows,
        blockers=tuple(blockers),
        state={"observed_symbols": observed_symbols, "source": "coinbase_exchange_rest"},
    )


def _fetch_coinbase_tick(*, symbol: str, product_id: str, timeout_seconds: int) -> dict[str, Any]:
    url = f"https://api.exchange.coinbase.com/products/{product_id}/ticker"
    started = time.monotonic()
    payload = _get_json(url, timeout_seconds=timeout_seconds)
    observed = payload.get("time") or datetime.now(UTC).isoformat()
    return {
        "symbol": symbol,
        "source": "coinbase_exchange_rest",
        "observed_at_utc": observed,
        "exchange_timestamp_utc": observed,
        "price": payload["price"],
        "bid": payload.get("bid"),
        "ask": payload.get("ask"),
        "source_latency_ms": int((time.monotonic() - started) * 1000),
        "raw": payload,
    }


def _fetch_coinbase_candles(
    *,
    symbol: str,
    product_id: str,
    granularity_seconds: int,
    limit: int,
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    url = f"https://api.exchange.coinbase.com/products/{product_id}/candles?granularity={granularity_seconds}"
    rows = _get_json(url, timeout_seconds=timeout_seconds)
    candles: list[dict[str, Any]] = []
    for row in list(rows)[: max(1, limit)]:
        opened_at = datetime.fromtimestamp(int(row[0]), tz=UTC)
        candles.append(
            {
                "symbol": symbol,
                "exchange": "coinbase_exchange",
                "interval": f"{granularity_seconds}s",
                "opened_at_utc": opened_at.isoformat(),
                "closed_at_utc": datetime.fromtimestamp(int(row[0]) + granularity_seconds, tz=UTC).isoformat(),
                "open": float(row[3]),
                "high": float(row[2]),
                "low": float(row[1]),
                "close": float(row[4]),
                "volume": float(row[5]) if len(row) > 5 else None,
            }
        )
    return candles


def _write_readiness(
    conn: Any,
    *,
    config: UnderlyingMarketPriceConfig,
    generated_at_utc: datetime,
    observed_symbols: tuple[str, ...],
    blockers: tuple[str, ...],
    tick_rows: int,
    candle_rows: int,
) -> int:
    rows = 0
    for symbol in tuple(dict.fromkeys(config.symbols)):
        symbol = symbol.upper()
        symbol_blockers = list(blockers)
        if symbol not in observed_symbols:
            symbol_blockers.append(f"missing_underlying_market_price:{symbol}")
        status = "ready" if not symbol_blockers else "degraded"
        payload = {
            "source": "coinbase_exchange_rest",
            "required_payloads": ["underlying_price_ticks", "underlying_candles"],
            "tick_rows_inserted": tick_rows,
            "candle_rows_inserted": candle_rows,
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
        readiness_key = _stable_key("block_a_market", symbol, generated_at_utc.isoformat())
        conn.execute(
            """
            INSERT INTO data_signal_readiness_snapshots(
                readiness_key, data_block, module_id, symbol, generated_at_utc,
                target_refresh_seconds, status, latest_source_at_utc,
                source_age_seconds, payload_json, blockers_json, inserted_at_utc
            )
            VALUES (?, 'A', 'underlying_market_prices', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(readiness_key) DO NOTHING
            """,
            (
                readiness_key,
                symbol,
                generated_at_utc.isoformat(),
                config.target_refresh_seconds,
                status,
                generated_at_utc.isoformat(),
                0.0,
                json.dumps(payload, sort_keys=True, default=str),
                json.dumps(symbol_blockers, sort_keys=True),
                datetime.now(UTC).isoformat(),
            ),
        )
        rows += int(conn.execute("SELECT changes() AS c").fetchone()["c"])
    return rows


def _get_json(url: str, *, timeout_seconds: int) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "janus-crypto-options/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _stable_key(*parts: Any) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _reject_live_env_flags() -> None:
    import os

    enabled = [name for name in LIVE_FLAG_NAMES if os.getenv(name) in {"1", "true", "TRUE", "yes", "YES"}]
    if enabled:
        raise RuntimeError(f"underlying market data service cannot run with live trading flags enabled: {enabled}")
