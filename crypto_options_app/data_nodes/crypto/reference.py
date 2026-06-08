from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


CHAINLINK_BTC_USD_STREAM_URL = "https://data.chain.link/streams/btc-usd"
CHAINLINK_DATA_STREAMS_MAINNET_API_HOST = "https://api.dataengine.chain.link"
DEFAULT_CHAINLINK_PRICE_SCALE = 100_000_000


class CryptoReferenceSourceUnavailable(RuntimeError):
    """Raised when no decoded reference stream source is configured."""


class CryptoReferencePriceProvider(Protocol):
    def fetch_reports(
        self,
        *,
        symbol: str,
        start: Any | None = None,
        end: Any | None = None,
    ) -> pd.DataFrame:
        """Return decoded reference price reports. Implementations must not infer prices from Polymarket odds."""


@dataclass(frozen=True)
class NoConfiguredCryptoReferenceProvider:
    reason: str = "No decoded Chainlink/reference price provider is configured."

    def fetch_reports(
        self,
        *,
        symbol: str,
        start: Any | None = None,
        end: Any | None = None,
    ) -> pd.DataFrame:
        raise CryptoReferenceSourceUnavailable(
            f"{self.reason} symbol={symbol!r}; Polymarket odds cannot supply settlement boundary prices."
        )


@dataclass(frozen=True)
class CsvCryptoReferencePriceProvider:
    path: str | Path

    def fetch_reports(
        self,
        *,
        symbol: str,
        start: Any | None = None,
        end: Any | None = None,
    ) -> pd.DataFrame:
        frame = pd.read_csv(self.path)
        normalized = normalize_reference_price_reports(frame.to_dict(orient="records"))
        if symbol:
            normalized = normalized[normalized["symbol"].astype(str).str.upper() == str(symbol).upper()]
        if start is not None:
            normalized = normalized[normalized["reference_timestamp"] >= pd.to_datetime(start, utc=True)]
        if end is not None:
            normalized = normalized[normalized["reference_timestamp"] <= pd.to_datetime(end, utc=True)]
        return normalized.reset_index(drop=True)


@dataclass(frozen=True)
class ChainlinkDataStreamsRestProvider:
    """Authenticated Chainlink Data Streams REST provider.

    The provider only fetches market-data reports. It does not verify reports onchain
    and does not perform any trading action.
    """

    api_key: str
    api_secret: str
    feed_id_by_symbol: dict[str, str]
    api_host: str = CHAINLINK_DATA_STREAMS_MAINNET_API_HOST
    price_scale: int = DEFAULT_CHAINLINK_PRICE_SCALE

    @classmethod
    def from_env(cls) -> "ChainlinkDataStreamsRestProvider":
        api_key = os.getenv("CHAINLINK_DATA_STREAMS_API_KEY") or os.getenv("CHAINLINK_DATA_STREAMS_UUID")
        api_secret = os.getenv("CHAINLINK_DATA_STREAMS_API_SECRET")
        feed_id = os.getenv("CHAINLINK_BTC_USD_FEED_ID")
        if not api_key or not api_secret or not feed_id:
            raise CryptoReferenceSourceUnavailable(
                "Missing Chainlink Data Streams credentials. Set CHAINLINK_DATA_STREAMS_API_KEY, "
                "CHAINLINK_DATA_STREAMS_API_SECRET, and CHAINLINK_BTC_USD_FEED_ID."
            )
        return cls(
            api_key=api_key,
            api_secret=api_secret,
            feed_id_by_symbol={"BTC": feed_id},
            api_host=os.getenv("CHAINLINK_DATA_STREAMS_API_HOST") or CHAINLINK_DATA_STREAMS_MAINNET_API_HOST,
        )

    def fetch_report_at(self, *, symbol: str, timestamp: int) -> pd.DataFrame:
        feed_id = self.feed_id_by_symbol.get(str(symbol).upper())
        if not feed_id:
            raise CryptoReferenceSourceUnavailable(f"No Chainlink feed id configured for symbol={symbol!r}.")
        path = f"/api/v1/reports?{urlencode({'feedID': feed_id, 'timestamp': int(timestamp)})}"
        payload = self._get_json(path)
        report = payload.get("report") if isinstance(payload, dict) else None
        return normalize_chainlink_rest_reports([report] if isinstance(report, dict) else [], symbol=symbol, price_scale=self.price_scale)

    def fetch_report_page(self, *, symbol: str, start_timestamp: int, limit: int = 100) -> pd.DataFrame:
        feed_id = self.feed_id_by_symbol.get(str(symbol).upper())
        if not feed_id:
            raise CryptoReferenceSourceUnavailable(f"No Chainlink feed id configured for symbol={symbol!r}.")
        path = f"/api/v1/reports/page?{urlencode({'feedID': feed_id, 'startTimestamp': int(start_timestamp), 'limit': int(limit)})}"
        payload = self._get_json(path)
        reports = payload.get("reports") if isinstance(payload, dict) else None
        return normalize_chainlink_rest_reports(reports if isinstance(reports, list) else [], symbol=symbol, price_scale=self.price_scale)

    def fetch_reports(
        self,
        *,
        symbol: str,
        start: Any | None = None,
        end: Any | None = None,
    ) -> pd.DataFrame:
        if start is None:
            raise CryptoReferenceSourceUnavailable("Chainlink page fetch requires a start timestamp.")
        start_ts = int(pd.to_datetime(start, utc=True).timestamp()) if not isinstance(start, (int, float)) else int(start)
        frame = self.fetch_report_page(symbol=symbol, start_timestamp=start_ts)
        if end is not None and not frame.empty:
            end_at = pd.to_datetime(end, utc=True)
            frame = frame[frame["reference_timestamp"] <= end_at]
        return frame.reset_index(drop=True)

    def _get_json(self, path: str) -> dict[str, Any]:
        request = Request(f"{self.api_host}{path}", headers=self._auth_headers("GET", path), method="GET")
        with urlopen(request, timeout=30) as response:  # noqa: S310 - configured official provider URL.
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else {}

    def _auth_headers(self, method: str, full_path: str, body: bytes = b"") -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        body_hash = hashlib.sha256(body).hexdigest()
        string_to_sign = f"{method.upper()} {full_path} {body_hash} {self.api_key} {timestamp}"
        signature = hmac.new(self.api_secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        return {
            "Authorization": self.api_key,
            "X-Authorization-Timestamp": timestamp,
            "X-Authorization-Signature-SHA256": signature,
            "Accept": "application/json",
            "User-Agent": "Janus crypto-options research (read-only)",
        }


def fetch_chainlink_reference_reports_for_events(
    events_df: pd.DataFrame,
    provider: ChainlinkDataStreamsRestProvider,
) -> pd.DataFrame:
    """Fetch boundary reports for each distinct event window."""

    if events_df.empty:
        return normalize_reference_price_reports([])
    frames: list[pd.DataFrame] = []
    key_columns = [column for column in ("event_id", "market_id", "primary_symbol", "window_start_time", "window_end_time") if column in events_df.columns]
    for _, row in events_df[key_columns].drop_duplicates().iterrows():
        symbol = str(row.get("primary_symbol") or "BTC").upper()
        for column in ("window_start_time", "window_end_time"):
            boundary = pd.to_datetime(row.get(column), utc=True, errors="coerce")
            if pd.isna(boundary):
                continue
            frames.append(provider.fetch_report_at(symbol=symbol, timestamp=int(boundary.timestamp())))
    return pd.concat(frames, ignore_index=True).drop_duplicates().reset_index(drop=True) if frames else normalize_reference_price_reports([])


def normalize_reference_price_reports(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Normalize decoded Chainlink/reference stream reports.

    This accepts decoded report rows only. If a provider returns only encoded Chainlink
    `fullReport` bytes, decode them upstream before using this module.
    """

    rows: list[dict[str, Any]] = []
    for record in records:
        symbol = str(_first_value(record, "symbol", "asset", "pair") or "BTC").upper()
        benchmark = _to_float(_first_value(record, "benchmark_price", "benchmarkPrice", "price", "mid", "mid_price"))
        if benchmark is None:
            continue
        query_ts = _parse_time(_first_value(record, "query_timestamp", "ts_query", "timestamp", "requested_at"))
        valid_from = _parse_time(_first_value(record, "valid_from_timestamp", "validFromTimestamp", "valid_from"))
        observations = _parse_time(
            _first_value(record, "observations_timestamp", "observationsTimestamp", "observation_timestamp", "observed_at")
        )
        reference_ts = observations or valid_from or query_ts
        if reference_ts is None:
            continue
        rows.append(
            {
                "symbol": symbol,
                "feed_id": _first_value(record, "feed_id", "feedID", "feedId"),
                "query_timestamp": query_ts,
                "valid_from_timestamp": valid_from,
                "observations_timestamp": observations,
                "reference_timestamp": reference_ts,
                "benchmark_price": benchmark,
                "bid": _to_float(_first_value(record, "bid", "bid_price", "bidPrice")),
                "ask": _to_float(_first_value(record, "ask", "ask_price", "askPrice")),
                "source": _first_value(record, "source") or "decoded_reference_price_report",
                "raw_json": record.get("raw_json") or record,
            }
        )
    columns = [
        "symbol",
        "feed_id",
        "query_timestamp",
        "valid_from_timestamp",
        "observations_timestamp",
        "reference_timestamp",
        "benchmark_price",
        "bid",
        "ask",
        "source",
        "raw_json",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows).sort_values(["symbol", "reference_timestamp"], kind="mergesort").reset_index(drop=True)


def normalize_chainlink_rest_reports(records: list[dict[str, Any]], *, symbol: str = "BTC", price_scale: int = DEFAULT_CHAINLINK_PRICE_SCALE) -> pd.DataFrame:
    """Normalize authenticated Chainlink REST report responses.

    The REST API returns `fullReport`; when it is a crypto v3 ABI report body this
    function decodes price, bid, and ask. If the payload cannot be decoded, the row
    is retained only when a provider has already exposed a decoded price field.
    """

    decoded_records: list[dict[str, Any]] = []
    for report in records:
        if not isinstance(report, dict):
            continue
        decoded = decode_chainlink_crypto_v3_report(report.get("fullReport"), price_scale=price_scale)
        decoded_records.append(
            {
                "symbol": symbol,
                "feed_id": report.get("feedID") or decoded.get("feed_id"),
                "validFromTimestamp": report.get("validFromTimestamp") or decoded.get("valid_from_timestamp"),
                "observationsTimestamp": report.get("observationsTimestamp") or decoded.get("observations_timestamp"),
                "benchmark_price": report.get("benchmark_price") or report.get("price") or decoded.get("price"),
                "bid": report.get("bid") or decoded.get("bid"),
                "ask": report.get("ask") or decoded.get("ask"),
                "source": "chainlink_data_streams_rest",
                "raw_json": report,
            }
        )
    return normalize_reference_price_reports(decoded_records)


def reference_reports_from_candles(candles_df: pd.DataFrame, *, source: str = "exchange_proxy_binance") -> pd.DataFrame:
    """Convert candle closes into proxy reference reports.

    These rows are not canonical settlement labels. They are useful for autonomous
    research/backtesting when the Chainlink source is unavailable.
    """

    if candles_df.empty:
        return normalize_reference_price_reports([])
    rows: list[dict[str, Any]] = []
    candles = candles_df.copy()
    candles["opened_at"] = pd.to_datetime(candles["opened_at"], utc=True, errors="coerce")
    for _, row in candles.dropna(subset=["symbol", "opened_at", "close"]).iterrows():
        rows.append(
            {
                "symbol": str(row["symbol"]).upper(),
                "observations_timestamp": row["opened_at"],
                "benchmark_price": row["close"],
                "bid": None,
                "ask": None,
                "source": source if source else str(row.get("source") or "exchange_proxy_candle_close"),
                "raw_json": {
                    "opened_at": row["opened_at"],
                    "exchange": row.get("exchange"),
                    "interval": row.get("interval"),
                    "source": row.get("source"),
                },
            }
        )
    return normalize_reference_price_reports(rows)


def decode_chainlink_crypto_v3_report(full_report: Any, *, price_scale: int = DEFAULT_CHAINLINK_PRICE_SCALE) -> dict[str, Any]:
    """Decode a crypto v3 Data Streams report body when it is ABI-encoded."""

    raw = _hex_to_bytes(full_report)
    if not raw:
        return {}
    try:
        from eth_abi import decode

        feed_id, valid_from, observations, native_fee, link_fee, expires_at, price, bid, ask = decode(
            ["bytes32", "uint32", "uint32", "uint192", "uint192", "uint32", "int192", "int192", "int192"],
            raw,
        )
    except Exception:
        return {}
    scale = float(price_scale or 1)
    return {
        "feed_id": "0x" + bytes(feed_id).hex(),
        "valid_from_timestamp": int(valid_from),
        "observations_timestamp": int(observations),
        "native_fee": int(native_fee),
        "link_fee": int(link_fee),
        "expires_at": int(expires_at),
        "price": float(price) / scale,
        "bid": float(bid) / scale,
        "ask": float(ask) / scale,
    }


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _hex_to_bytes(value: Any) -> bytes:
    if not value:
        return b""
    if isinstance(value, bytes):
        return value
    raw = str(value)
    if raw.startswith("0x"):
        raw = raw[2:]
    try:
        return bytes.fromhex(raw)
    except ValueError:
        return b""


def _parse_time(value: Any) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    unit = None
    if isinstance(value, (int, float)):
        unit = "ms" if float(value) >= 10_000_000_000 else "s"
    try:
        parsed = pd.to_datetime(value, utc=True, unit=unit)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    return parsed


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "CHAINLINK_BTC_USD_STREAM_URL",
    "CHAINLINK_DATA_STREAMS_MAINNET_API_HOST",
    "CryptoReferencePriceProvider",
    "CryptoReferenceSourceUnavailable",
    "CsvCryptoReferencePriceProvider",
    "ChainlinkDataStreamsRestProvider",
    "NoConfiguredCryptoReferenceProvider",
    "decode_chainlink_crypto_v3_report",
    "fetch_chainlink_reference_reports_for_events",
    "normalize_chainlink_rest_reports",
    "normalize_reference_price_reports",
    "reference_reports_from_candles",
]
