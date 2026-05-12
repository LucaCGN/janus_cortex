from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


POLYMARKET_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB_BASE_URL = "https://clob.polymarket.com"
WNBA_TAG_ID = "100254"

POLYMARKET_PUBLIC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}


def _fetch_json(url: str) -> Any:
    request = Request(url, headers=POLYMARKET_PUBLIC_HEADERS)
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed public provider URLs.
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None or value == "":
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _safe_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    if "+" not in raw and raw.count(":") >= 2 and " " in raw:
        raw = raw.replace(" ", "T") + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def fetch_closed_wnba_moneyline_events(
    *,
    limit: int = 50,
    offset: int = 0,
    tag_id: str = WNBA_TAG_ID,
) -> list[dict[str, Any]]:
    query = urlencode(
        {
            "limit": max(1, int(limit)),
            "offset": max(0, int(offset)),
            "closed": "true",
            "tag_id": tag_id,
            "order": "endDate",
            "ascending": "false",
        }
    )
    payload = _fetch_json(f"{POLYMARKET_GAMMA_BASE_URL}/events?{query}")
    return payload if isinstance(payload, list) else []


def normalize_wnba_moneyline_events(events: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for event in events:
        event_slug = str(event.get("slug") or "")
        markets = event.get("markets") if isinstance(event.get("markets"), list) else []
        for market in markets:
            if str(market.get("sportsMarketType") or "").lower() != "moneyline":
                continue
            outcomes = [str(value) for value in _json_list(market.get("outcomes"))]
            token_ids = [str(value) for value in _json_list(market.get("clobTokenIds"))]
            outcome_prices = _json_list(market.get("outcomePrices"))
            for index, outcome in enumerate(outcomes):
                token_id = token_ids[index] if index < len(token_ids) else None
                if not token_id:
                    continue
                rows.append(
                    {
                        "event_id": str(event.get("id") or ""),
                        "event_slug": event_slug,
                        "event_title": event.get("title"),
                        "event_date": event.get("eventDate"),
                        "event_start_time": event.get("startTime") or event.get("startDate"),
                        "event_closed": bool(event.get("closed")),
                        "event_ended": bool(event.get("ended")),
                        "event_score": event.get("score"),
                        "event_game_id": event.get("gameId"),
                        "market_id": str(market.get("id") or ""),
                        "condition_id": market.get("conditionId"),
                        "market_slug": market.get("slug"),
                        "sports_market_type": market.get("sportsMarketType"),
                        "game_start_time": market.get("gameStartTime") or event.get("startTime"),
                        "closed_time": market.get("closedTime") or event.get("closedTime"),
                        "outcome": outcome,
                        "outcome_index": index,
                        "token_id": token_id,
                        "resolved_price": outcome_prices[index] if index < len(outcome_prices) else None,
                        "volume": market.get("volumeNum") or market.get("volume"),
                        "raw_event_json": event,
                        "raw_market_json": market,
                    }
                )
    return pd.DataFrame(rows)


def fetch_token_price_history(
    token_id: str,
    *,
    interval: str = "max",
    fidelity: int = 1,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {"market": str(token_id), "fidelity": max(1, int(fidelity))}
    if start_ts is not None or end_ts is not None:
        if start_ts is not None:
            query["startTs"] = int(start_ts)
        if end_ts is not None:
            query["endTs"] = int(end_ts)
    else:
        query["interval"] = interval
    return _fetch_json(f"{POLYMARKET_CLOB_BASE_URL}/prices-history?{urlencode(query)}")


def normalize_token_price_history(
    payload: dict[str, Any],
    *,
    token_id: str,
    game_id: str | None = None,
    team_side: str | None = None,
    team_tricode: str | None = None,
    outcome: str | None = None,
    event_slug: str | None = None,
    market_id: str | None = None,
    source: str = "polymarket_clob_prices_history",
) -> pd.DataFrame:
    history = payload.get("history") if isinstance(payload, dict) else []
    rows: list[dict[str, Any]] = []
    for item in history if isinstance(history, list) else []:
        timestamp = item.get("t") if isinstance(item, dict) else None
        price = item.get("p") if isinstance(item, dict) else None
        if timestamp is None or price is None:
            continue
        try:
            price_at = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
            numeric_price = float(price)
        except (TypeError, ValueError, OSError):
            continue
        rows.append(
            {
                "game_id": game_id,
                "team_side": team_side,
                "team_tricode": team_tricode,
                "event_slug": event_slug,
                "market_id": market_id,
                "token_id": str(token_id),
                "outcome": outcome,
                "captured_at": price_at,
                "mid_price": numeric_price,
                "team_price": numeric_price,
                "best_bid": None,
                "best_ask": None,
                "spread": None,
                "source": source,
                "raw_json": item,
            }
        )
    return pd.DataFrame(rows)


def event_time_bounds(row: pd.Series | dict[str, Any], *, pregame_hours: int = 12, postgame_hours: int = 2) -> tuple[int | None, int | None]:
    series = row if isinstance(row, pd.Series) else pd.Series(row)
    start = _safe_dt(series.get("game_start_time") or series.get("event_start_time"))
    closed = _safe_dt(series.get("closed_time"))
    if start is None:
        return None, None
    start_ts = int(start.timestamp()) - max(0, int(pregame_hours)) * 3600
    end_ts = int((closed or start).timestamp()) + max(0, int(postgame_hours)) * 3600
    return start_ts, end_ts


__all__ = [
    "WNBA_TAG_ID",
    "event_time_bounds",
    "fetch_closed_wnba_moneyline_events",
    "fetch_token_price_history",
    "normalize_token_price_history",
    "normalize_wnba_moneyline_events",
]
