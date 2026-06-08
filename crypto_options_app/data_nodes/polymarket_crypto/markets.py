from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


POLYMARKET_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
POLYMARKET_PUBLIC_HEADERS = {
    "User-Agent": "Janus crypto-options research (read-only)",
    "Accept": "application/json,text/plain,*/*",
}

SUPPORTED_SYMBOLS = ("BTC", "ETH", "SOL", "XRP")

MARKET_TYPE_PRIORITY = (
    {
        "event_type": "short_interval_up_down",
        "priority": 1,
        "reason": "Five-minute recurring direction markets are the first concrete #47 target and require reference-stream timing.",
    },
    {
        "event_type": "hourly_daily_up_down",
        "priority": 2,
        "reason": "Recurring cadence and binary settlement make these the cleanest first replay target.",
    },
    {
        "event_type": "above_below_price_at_time",
        "priority": 3,
        "reason": "Threshold and close timestamp are explicit enough for exchange-candle labels.",
    },
    {
        "event_type": "range_target",
        "priority": 4,
        "reason": "Useful after threshold parsing exists, but requires multi-outcome settlement handling.",
    },
    {
        "event_type": "hit_by_date",
        "priority": 5,
        "reason": "Needs path-dependent exchange high/low labeling and more careful lookahead controls.",
    },
    {
        "event_type": "other_recurring_crypto_contract",
        "priority": 6,
        "reason": "Keep discoverable, but do not backtest until condition text is parsed.",
    },
)


def fetch_gamma_crypto_events(*, limit: int = 100, offset: int = 0, closed: bool | None = None) -> list[dict[str, Any]]:
    """Fetch public Gamma events likely to include crypto contracts.

    This is read-only discovery only. It does not touch CLOB credentials, orders, balances, or portfolio state.
    """

    query: dict[str, Any] = {
        "limit": max(1, int(limit)),
        "offset": max(0, int(offset)),
        "q": "crypto bitcoin ethereum solana xrp up down above below",
    }
    if closed is not None:
        query["closed"] = str(bool(closed)).lower()
    request = Request(f"{POLYMARKET_GAMMA_BASE_URL}/events?{urlencode(query)}", headers=POLYMARKET_PUBLIC_HEADERS)
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed public provider URL.
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, list) else []


def fetch_gamma_event_by_slug(slug: str) -> dict[str, Any]:
    """Fetch one public Gamma event by slug."""

    request = Request(f"{POLYMARKET_GAMMA_BASE_URL}/events/slug/{slug}", headers=POLYMARKET_PUBLIC_HEADERS)
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed public provider URL.
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def fetch_recurring_updown_events_by_slug_range(
    *,
    symbol: str = "BTC",
    cadence_minutes: int = 5,
    start: Any,
    end: Any,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch recurring up/down events by generated slug range.

    Returns `(events, attempts)` where attempts records any missing slug without
    failing the whole historical replay run.
    """

    start_value = parse_utc(start) or _parse_utc_any(start)
    end_value = parse_utc(end) or _parse_utc_any(end)
    start_at = _floor_to_cadence(start_value, cadence_minutes)
    end_at = _floor_to_cadence(end_value, cadence_minutes)
    if start_at is None or end_at is None or start_at > end_at:
        return [], [{"status": "blocked", "blocker": "invalid_slug_range", "start": str(start), "end": str(end)}]
    events: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    current = start_at
    fetched = 0
    while current <= end_at:
        if limit is not None and fetched >= int(limit):
            break
        slug = f"{str(symbol).lower()}-updown-{int(cadence_minutes)}m-{int(current.timestamp())}"
        try:
            event = fetch_gamma_event_by_slug(slug)
        except Exception as exc:  # noqa: BLE001 - per-slug discovery failures should be recorded.
            attempts.append({"slug": slug, "status": "missing", "blocker": f"{type(exc).__name__}:{exc}"})
            current += timedelta(minutes=int(cadence_minutes))
            fetched += 1
            continue
        if event:
            events.append(event)
            attempts.append({"slug": slug, "status": "fetched", "event_id": event.get("id")})
        else:
            attempts.append({"slug": slug, "status": "missing", "blocker": "empty_gamma_response"})
        current += timedelta(minutes=int(cadence_minutes))
        fetched += 1
    return events, attempts


def normalize_polymarket_crypto_events(events: list[dict[str, Any]]) -> pd.DataFrame:
    """Flatten Gamma event/market/outcome payloads into crypto-options outcome rows."""

    rows: list[dict[str, Any]] = []
    for event in events:
        event_title = str(event.get("title") or event.get("question") or "")
        event_slug = str(event.get("slug") or "")
        event_text = " ".join([event_title, event_slug, str(event.get("description") or "")])
        markets = event.get("markets") if isinstance(event.get("markets"), list) else []
        for market in markets:
            market_title = str(market.get("question") or market.get("title") or event_title)
            market_slug = str(market.get("slug") or "")
            market_text = " ".join([event_text, market_title, market_slug, str(market.get("description") or "")])
            slug_window = parse_recurring_updown_slug(event_slug or market_slug)
            event_type = classify_crypto_market_type(market_text)
            symbols = _extract_symbols(market_text)
            if slug_window and not symbols:
                symbols = [slug_window["symbol"]]
            if slug_window:
                event_type = "short_interval_up_down"
            if event_type == "non_crypto" and not symbols:
                continue
            outcomes = _json_list(market.get("outcomes"))
            token_ids = _json_list(market.get("clobTokenIds"))
            outcome_prices = _json_list(market.get("outcomePrices"))
            condition_text = str(market.get("description") or event.get("description") or market_title)
            threshold = _extract_threshold(condition_text)
            resolution = _extract_resolution_source(condition_text)
            for index, outcome in enumerate(outcomes or [""]):
                token_id = str(token_ids[index]) if index < len(token_ids) else None
                rows.append(
                    {
                        "event_id": str(event.get("id") or ""),
                        "event_slug": event_slug,
                        "event_title": event_title,
                        "market_id": str(market.get("id") or ""),
                        "condition_id": market.get("conditionId"),
                        "market_slug": market_slug,
                        "market_title": market_title,
                        "event_type": event_type,
                        "symbols": symbols,
                        "primary_symbol": symbols[0] if symbols else None,
                        "outcome": str(outcome),
                        "outcome_index": index,
                        "token_id": token_id,
                        "outcome_price": _to_float(outcome_prices[index]) if index < len(outcome_prices) else None,
                        "start_time": _first_value(market, event, "startTime", "startDate", "gameStartTime")
                        or (slug_window or {}).get("window_start_time"),
                        "end_time": _first_value(market, event, "endDate", "endTime", "closedTime")
                        or (slug_window or {}).get("window_end_time"),
                        "window_start_time": (slug_window or {}).get("window_start_time"),
                        "window_end_time": (slug_window or {}).get("window_end_time"),
                        "cadence_seconds": (slug_window or {}).get("cadence_seconds"),
                        "closed_time": market.get("closedTime") or event.get("closedTime"),
                        "settlement_threshold": threshold,
                        "condition_text": condition_text,
                        "resolution_source": resolution["resolution_source"],
                        "resolution_source_url": resolution["resolution_source_url"],
                        "closed": bool(market.get("closed") or event.get("closed")),
                        "ended": bool(market.get("ended") or event.get("ended")),
                        "volume": _to_float(market.get("volumeNum") or market.get("volume")),
                        "liquidity": _to_float(market.get("liquidityNum") or market.get("liquidity")),
                        "raw_event_json": event,
                        "raw_market_json": market,
                    }
                )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "event_id",
                "event_slug",
                "market_id",
                "market_slug",
                "event_type",
                "primary_symbol",
                "outcome",
                "token_id",
                "settlement_threshold",
                "condition_text",
                "cadence_seconds",
                "window_start_time",
                "window_end_time",
                "resolution_source",
                "resolution_source_url",
            ]
        )
    return frame.reset_index(drop=True)


def classify_crypto_market_type(text: str) -> str:
    normalized = str(text or "").lower()
    if not any(symbol.lower() in normalized or name in normalized for symbol, name in _SYMBOL_NAMES.items()):
        return "non_crypto"
    if re.search(r"\b(up|down|updown)\b", normalized) and re.search(
        r"\b(\d+\s*m|5m|5-minute|five-minute|minute|minutes)\b",
        normalized,
    ):
        return "short_interval_up_down"
    if re.search(r"\b(up|down)\b", normalized) and re.search(r"\b(hour|hourly|day|daily|today|tomorrow)\b", normalized):
        return "hourly_daily_up_down"
    if re.search(r"\b(above|below|over|under)\b", normalized) and re.search(r"\$?\d[\d,]*(?:\.\d+)?", normalized):
        return "above_below_price_at_time"
    if re.search(r"\b(between|range|target)\b", normalized):
        return "range_target"
    if re.search(r"\b(hit|touch|reach|by)\b", normalized) and re.search(r"\b(date|week|month|year|before)\b", normalized):
        return "hit_by_date"
    return "other_recurring_crypto_contract"


def market_type_priority() -> tuple[dict[str, Any], ...]:
    return MARKET_TYPE_PRIORITY


def parse_recurring_updown_slug(slug: str) -> dict[str, Any] | None:
    """Parse slugs like btc-updown-5m-1779999300 into a UTC replay window."""

    match = re.search(r"\b(?P<asset>btc|eth|sol|xrp)-updown-(?P<minutes>\d+)m-(?P<start_ts>\d{10})\b", str(slug or "").lower())
    if not match:
        return None
    minutes = int(match.group("minutes"))
    start_at = datetime.fromtimestamp(int(match.group("start_ts")), tz=timezone.utc)
    end_at = start_at + timedelta(minutes=minutes)
    return {
        "symbol": match.group("asset").upper(),
        "cadence_seconds": minutes * 60,
        "window_start_time": start_at.isoformat(),
        "window_end_time": end_at.isoformat(),
    }


_SYMBOL_NAMES = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "xrp",
}


def _extract_symbols(text: str) -> list[str]:
    normalized = str(text or "").lower()
    symbols = []
    for symbol, name in _SYMBOL_NAMES.items():
        if symbol.lower() in normalized or name in normalized:
            symbols.append(symbol)
    return symbols


def _extract_threshold(text: str) -> float | None:
    matches = re.findall(r"\$?\s*([0-9][0-9,]*(?:\.\d+)?)", str(text or ""))
    if not matches:
        return None
    values: list[float] = []
    for match in matches:
        try:
            values.append(float(match.replace(",", "")))
        except ValueError:
            continue
    if not values:
        return None
    # Ignore years and small date fragments when a price-like value is present.
    price_like = [value for value in values if value >= 10]
    return max(price_like or values)


def _extract_resolution_source(text: str) -> dict[str, str | None]:
    raw = str(text or "")
    lowered = raw.lower()
    urls = re.findall(r"https?://[^\s)]+", raw)
    source_url = urls[0] if urls else None
    if "chainlink" in lowered and "btc" in lowered and "usd" in lowered:
        return {"resolution_source": "chainlink_btc_usd_data_stream", "resolution_source_url": source_url}
    if "chainlink" in lowered:
        return {"resolution_source": "chainlink_data_stream", "resolution_source_url": source_url}
    if source_url:
        return {"resolution_source": "external_reference_url", "resolution_source_url": source_url}
    return {"resolution_source": None, "resolution_source_url": None}


def _first_value(market: dict[str, Any], event: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = market.get(key)
        if value not in (None, ""):
            return value
        value = event.get(key)
        if value not in (None, ""):
            return value
    return None


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


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


def parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_utc_any(value: Any) -> datetime | None:
    try:
        parsed = pd.to_datetime(value, utc=True)
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _floor_to_cadence(value: datetime | None, cadence_minutes: int) -> datetime | None:
    if value is None:
        return None
    cadence_seconds = int(cadence_minutes) * 60
    floored = int(value.timestamp()) - (int(value.timestamp()) % cadence_seconds)
    return datetime.fromtimestamp(floored, tz=timezone.utc)


__all__ = [
    "MARKET_TYPE_PRIORITY",
    "SUPPORTED_SYMBOLS",
    "classify_crypto_market_type",
    "fetch_gamma_event_by_slug",
    "fetch_gamma_crypto_events",
    "fetch_recurring_updown_events_by_slug_range",
    "market_type_priority",
    "normalize_polymarket_crypto_events",
    "parse_recurring_updown_slug",
]
