from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

BINANCE_PUBLIC_MARKET_DATA_BASE_URL = "https://data-api.binance.vision"
BINANCE_PUBLIC_HEADERS = {
    "User-Agent": "Janus crypto-options research (read-only)",
    "Accept": "application/json,text/plain,*/*",
}
BINANCE_SYMBOL_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
}


class CryptoCandleSourceUnavailable(RuntimeError):
    """Raised when no exchange-backed underlying candle source is configured."""


class CryptoCandleProvider(Protocol):
    def fetch_candles(
        self,
        *,
        symbol: str,
        start: Any | None = None,
        end: Any | None = None,
        interval: str = "1m",
    ) -> pd.DataFrame:
        """Return exchange-backed candles. Implementations must not infer candles from Polymarket odds."""


@dataclass(frozen=True)
class NoConfiguredCryptoCandleProvider:
    """Explicit failing provider used until Janus is wired to a real exchange candle source."""

    reason: str = "No exchange-backed crypto candle provider is configured."

    def fetch_candles(
        self,
        *,
        symbol: str,
        start: Any | None = None,
        end: Any | None = None,
        interval: str = "1m",
    ) -> pd.DataFrame:
        raise CryptoCandleSourceUnavailable(
            f"{self.reason} symbol={symbol!r} interval={interval!r}; Polymarket odds are not an underlying price source."
        )


@dataclass(frozen=True)
class CsvCryptoCandleProvider:
    """Small local-file provider for tests and offline replay packs."""

    path: str | Path

    def fetch_candles(
        self,
        *,
        symbol: str,
        start: Any | None = None,
        end: Any | None = None,
        interval: str = "1m",
    ) -> pd.DataFrame:
        frame = pd.read_csv(self.path)
        normalized = normalize_candle_records(frame.to_dict(orient="records"))
        if symbol:
            normalized = normalized[normalized["symbol"].astype(str).str.upper() == str(symbol).upper()]
        if interval and "interval" in normalized.columns:
            normalized = normalized[normalized["interval"].astype(str) == str(interval)]
        if start is not None:
            normalized = normalized[normalized["opened_at"] >= pd.to_datetime(start, utc=True)]
        if end is not None:
            normalized = normalized[normalized["opened_at"] <= pd.to_datetime(end, utc=True)]
        return normalized.reset_index(drop=True)


@dataclass(frozen=True)
class BinanceKlineCandleProvider:
    """Public Binance market-data kline provider for proxy research features."""

    base_url: str = BINANCE_PUBLIC_MARKET_DATA_BASE_URL
    symbol_map: dict[str, str] | None = None

    def fetch_candles(
        self,
        *,
        symbol: str,
        start: Any | None = None,
        end: Any | None = None,
        interval: str = "1m",
    ) -> pd.DataFrame:
        exchange_symbol = (self.symbol_map or BINANCE_SYMBOL_MAP).get(str(symbol).upper(), str(symbol).upper())
        query: dict[str, Any] = {"symbol": exchange_symbol, "interval": _binance_interval(interval), "limit": 1000}
        if start is not None:
            query["startTime"] = _to_epoch_ms(start)
        if end is not None:
            query["endTime"] = _to_epoch_ms(end)
        request = Request(f"{self.base_url}/api/v3/klines?{urlencode(query)}", headers=BINANCE_PUBLIC_HEADERS)
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed public market-data URL.
            import json

            payload = json.loads(response.read().decode("utf-8"))
        records = []
        for row in payload if isinstance(payload, list) else []:
            if not isinstance(row, list) or len(row) < 6:
                continue
            records.append(
                {
                    "symbol": str(symbol).upper(),
                    "exchange": "binance",
                    "interval": interval,
                    "opened_at": pd.to_datetime(int(row[0]), utc=True, unit="ms"),
                    "open": row[1],
                    "high": row[2],
                    "low": row[3],
                    "close": row[4],
                    "volume": row[5],
                    "source": "binance_public_klines",
                    "raw_json": {"exchange_symbol": exchange_symbol, "row": row},
                }
            )
        return normalize_candle_records(records)


def fetch_binance_candles_for_events(
    events_df: pd.DataFrame,
    *,
    interval: str = "1m",
    lookback_minutes: int = 60,
    lookahead_minutes: int = 5,
    provider: BinanceKlineCandleProvider | None = None,
) -> pd.DataFrame:
    """Fetch public Binance candles covering loaded event windows."""

    provider = provider or BinanceKlineCandleProvider()
    if events_df.empty or "primary_symbol" not in events_df.columns:
        return normalize_candle_records([])
    frames: list[pd.DataFrame] = []
    for symbol, group in events_df.dropna(subset=["primary_symbol"]).groupby("primary_symbol"):
        start_at = _min_time(group, "window_start_time")
        end_at = _max_time(group, "window_end_time")
        if start_at is None or end_at is None:
            continue
        start = start_at - pd.Timedelta(minutes=int(lookback_minutes))
        end = end_at + pd.Timedelta(minutes=int(lookahead_minutes))
        frames.append(provider.fetch_candles(symbol=str(symbol).upper(), start=start, end=end, interval=interval))
    return pd.concat(frames, ignore_index=True) if frames else normalize_candle_records([])


def normalize_candle_records(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Normalize exchange candle rows into the crypto-options candle schema."""

    rows: list[dict[str, Any]] = []
    for record in records:
        opened_at = _first_value(record, "opened_at", "timestamp", "time", "start_time")
        symbol = str(_first_value(record, "symbol", "asset", "pair") or "").upper()
        if not opened_at or not symbol:
            continue
        try:
            parsed_at = pd.to_datetime(opened_at, utc=True)
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "symbol": symbol,
                "exchange": _first_value(record, "exchange", "venue") or "unknown",
                "interval": str(_first_value(record, "interval", "timeframe") or "1m"),
                "opened_at": parsed_at,
                "open": _to_float(_first_value(record, "open", "o")),
                "high": _to_float(_first_value(record, "high", "h")),
                "low": _to_float(_first_value(record, "low", "l")),
                "close": _to_float(_first_value(record, "close", "c", "price")),
                "volume": _to_float(_first_value(record, "volume", "v"), default=0.0),
                "source": _first_value(record, "source") or "exchange_candle_csv",
                "raw_json": record.get("raw_json") or record,
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(
            columns=["symbol", "exchange", "interval", "opened_at", "open", "high", "low", "close", "volume", "source", "raw_json"]
        )
    return frame.sort_values(["symbol", "opened_at"], kind="mergesort").reset_index(drop=True)


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _to_float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_epoch_ms(value: Any) -> int:
    return int(pd.to_datetime(value, utc=True).timestamp() * 1000)


def _binance_interval(interval: str) -> str:
    normalized = str(interval or "1m").strip().lower()
    aliases = {"60s": "1m", "1min": "1m", "minute": "1m"}
    return aliases.get(normalized, normalized)


def _min_time(frame: pd.DataFrame, column: str) -> pd.Timestamp | None:
    if column not in frame.columns:
        return None
    values = pd.to_datetime(frame[column], utc=True, errors="coerce").dropna()
    return values.min() if not values.empty else None


def _max_time(frame: pd.DataFrame, column: str) -> pd.Timestamp | None:
    if column not in frame.columns:
        return None
    values = pd.to_datetime(frame[column], utc=True, errors="coerce").dropna()
    return values.max() if not values.empty else None


__all__ = [
    "BINANCE_PUBLIC_MARKET_DATA_BASE_URL",
    "BinanceKlineCandleProvider",
    "CryptoCandleProvider",
    "CryptoCandleSourceUnavailable",
    "CsvCryptoCandleProvider",
    "NoConfiguredCryptoCandleProvider",
    "fetch_binance_candles_for_events",
    "normalize_candle_records",
]
