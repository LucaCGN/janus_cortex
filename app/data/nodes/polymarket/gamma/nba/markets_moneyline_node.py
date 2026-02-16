#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
markets_moneyline_node.py
-------------------------

Node Gamma → NBA Moneyline Markets.

- Busca mercados de moneyline para eventos da NBA.
- "Explode" 1 linha por outcome (ex.: UTA, NYK).
- Expõe token_id (quando disponível) para uso posterior no CLOB/blockchain.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import sqlite3
from pydantic import BaseModel

from app.data.nodes.polymarket.gamma.gamma_client import GammaClient, get_default_client
from app.data.nodes.polymarket.gamma.nba.teams_node import NBATeamsRequest, fetch_nba_teams_df
from app.data.nodes.polymarket.gamma.nba.sports_metadata import get_nba_tag_ids

logger = logging.getLogger(__name__)


class NBAMoneylineMarketsRequest(BaseModel):
    """Parâmetros para buscar mercados de moneyline NBA."""
    tag_id: Optional[int] = None
    tag_slug: Optional[str] = None
    only_open: bool = True
    page_size: int = 100
    max_pages: Optional[int] = 5
    start_date_min: Optional[datetime] = None
    start_date_max: Optional[datetime] = None
    query_window_days: Optional[int] = None
    use_events_fallback: bool = True


class NBAMoneylineOutcome(BaseModel):
    """Uma linha por outcome de moneyline para um evento/mercado."""
    event_id: Optional[str] = None
    event_slug: Optional[str] = None
    event_title: Optional[str] = None
    market_id: str
    market_slug: Optional[str] = None
    market_type: Optional[str] = None

    outcome: str
    team_id: Optional[str] = None
    team_abbr: Optional[str] = None
    team_name: Optional[str] = None

    last_price: Optional[float] = None
    implied_prob: Optional[float] = None

    token_id: Optional[str] = None

    game_start_time: Optional[datetime] = None
    closed: bool = False
    enable_orderbook: Optional[bool] = None
    volume: Optional[float] = None
    liquidity: Optional[float] = None
    ingestion_source: Optional[str] = None

    raw: Dict[str, Any]


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        v = str(value)
        if v.endswith("Z"):
            v = v.replace("Z", "+00:00")
        return datetime.fromisoformat(v)
    except Exception:  # noqa: BLE001
        return None


def _normalize_gamma_bound(dt: datetime, end_of_day: bool = False) -> datetime:
    if dt.tzinfo is None:
        base = dt.replace(tzinfo=timezone.utc)
    else:
        base = dt.astimezone(timezone.utc)

    if end_of_day:
        return base.replace(hour=23, minute=59, second=59, microsecond=999999)
    return base.replace(hour=0, minute=0, second=0, microsecond=0)


def _iter_query_windows(
    start_dt: Optional[datetime],
    end_dt: Optional[datetime],
    window_days: Optional[int],
) -> List[tuple[Optional[datetime], Optional[datetime]]]:
    if start_dt is None or end_dt is None or window_days is None or window_days <= 0:
        return [(start_dt, end_dt)]

    start_norm = _normalize_gamma_bound(start_dt)
    end_norm = _normalize_gamma_bound(end_dt, end_of_day=True)
    if end_norm < start_norm:
        return [(start_dt, end_dt)]

    windows: List[tuple[datetime, datetime]] = []
    cursor = start_norm
    while cursor <= end_norm:
        window_end = min(cursor + timedelta(days=window_days) - timedelta(microseconds=1), end_norm)
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(microseconds=1)
    return windows


def _parse_price(raw_price: Any) -> Optional[float]:
    if raw_price is None:
        return None
    try:
        p = float(raw_price)
    except (TypeError, ValueError):
        return None
    return p / 100.0 if p > 1.0 else p


def _safe_json_loads(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:  # noqa: BLE001
            return value
    return value


def _extract_primary_event(raw: Dict[str, Any]) -> Dict[str, Any]:
    events = raw.get("events")
    if isinstance(events, list) and events and isinstance(events[0], dict):
        return events[0]
    return {}


def _extract_event_tags(raw: Dict[str, Any]) -> List[int]:
    ev = _extract_primary_event(raw)
    if not ev:
        return []
    candidate = ev.get("tags")
    if not isinstance(candidate, list):
        return []

    out: List[int] = []
    for item in candidate:
        if isinstance(item, dict):
            val = item.get("id")
            try:
                out.append(int(val))
            except (TypeError, ValueError):
                continue
        else:
            try:
                out.append(int(item))
            except (TypeError, ValueError):
                continue
    return out


def _market_has_nba_marker(raw: Dict[str, Any], nba_tag_ids: List[int]) -> bool:
    if any(t in _extract_market_tags(raw) for t in nba_tag_ids):
        return True
    if any(t in _extract_event_tags(raw) for t in nba_tag_ids):
        return True

    ev = _extract_primary_event(raw)
    event_slug = str(ev.get("slug") or raw.get("eventSlug") or "").lower()
    event_title = str(ev.get("title") or "").lower()
    if event_slug.startswith("nba-"):
        return True
    if " nba " in f" {event_title} " or event_title.startswith("nba "):
        return True

    event_tags = ev.get("tags")
    if isinstance(event_tags, list):
        for tag in event_tags:
            if isinstance(tag, dict):
                if str(tag.get("slug") or "").lower() == "nba":
                    return True
                if str(tag.get("label") or "").strip().lower() == "nba":
                    return True

    return False


def _is_moneyline_market(raw: Dict[str, Any]) -> bool:
    market_type = str(raw.get("sportsMarketType") or "").strip().lower()
    if market_type == "moneyline":
        return True
    if market_type and market_type != "moneyline":
        return False
    question = str(raw.get("question") or "").lower()
    return "moneyline" in question


def _extract_market_event_start(raw: Dict[str, Any]) -> Optional[datetime]:
    ev = _extract_primary_event(raw)
    return _parse_dt(
        ev.get("startDate")
        or raw.get("gameStartTime")
        or raw.get("startDate")
        or raw.get("startTime")
    )


def _extract_event_date_from_slug(raw: Dict[str, Any]) -> Optional[datetime]:
    ev = _extract_primary_event(raw)
    slug = str(ev.get("slug") or raw.get("eventSlug") or "").lower()
    if not slug.startswith("nba-"):
        return None

    parts = slug.split("-")
    if len(parts) < 6:
        return None

    year, month, day = parts[-3:]
    if len(year) != 4 or len(month) != 2 or len(day) != 2:
        return None

    try:
        return datetime(
            year=int(year),
            month=int(month),
            day=int(day),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def _extract_market_reference_time(raw: Dict[str, Any]) -> Optional[datetime]:
    slug_dt = _extract_event_date_from_slug(raw)
    if slug_dt is not None:
        return slug_dt
    return _extract_market_event_start(raw)


def _match_team_for_outcome(
    outcome: str,
    teams_df: Optional[pd.DataFrame],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Best-effort: tenta bater abbr (NYK/UTA) e depois o nome.
    """
    if teams_df is None or teams_df.empty:
        return None, None, None

    out = outcome.strip().upper()
    mask_abbr = teams_df["abbreviation"].astype(str).str.upper() == out
    if mask_abbr.any():
        row = teams_df[mask_abbr].iloc[0]
        return str(row["team_id"]), str(row["abbreviation"]), str(row["name"])

    mask_name = teams_df["name"].astype(str).str.upper() == out
    if mask_name.any():
        row = teams_df[mask_name].iloc[0]
        return str(row["team_id"]), str(row["abbreviation"]), str(row["name"])

    return None, None, None


def _extract_market_tags(raw: Dict[str, Any]) -> List[int]:
    """
    Extrai tags de um market (se existirem) em formato List[int].
    Tentativas: raw['tags'], raw['tagIds'], raw['tag_ids'].
    """
    candidate = raw.get("tags") or raw.get("tagIds") or raw.get("tag_ids")
    if candidate is None:
        return []

    if isinstance(candidate, str):
        parts = [p.strip() for p in candidate.split(",") if p.strip()]
    elif isinstance(candidate, (list, tuple)):
        parts = []
        for item in candidate:
            if item is None:
                continue
            parts.append(str(item).strip())
    else:
        return []

    out: List[int] = []
    for p in parts:
        try:
            out.append(int(p))
        except (TypeError, ValueError):
            continue
    return out


def _parse_market_outcomes(
    market: Dict[str, Any],
    teams_df: Optional[pd.DataFrame],
) -> List[Dict[str, Any]]:
    market_id = market.get("id") or market.get("marketId") or market.get("market_id")
    market_slug = market.get("slug") or market.get("marketSlug") or market.get("market_slug")
    market_type = market.get("sportsMarketType")
    event_payload = _extract_primary_event(market)

    outcomes = _safe_json_loads(market.get("outcomes") or [])
    if not isinstance(outcomes, list):
        outcomes = []
    outcome_prices = _safe_json_loads(market.get("outcomePrices"))
    clob_token_ids = _safe_json_loads(market.get("clobTokenIds") or market.get("clobTokenIDs"))

    def _get_price_for(ix: int, outcome_label: str) -> Optional[float]:
        if isinstance(outcome_prices, dict):
            if outcome_label in outcome_prices:
                return _parse_price(outcome_prices[outcome_label])
            if str(ix) in outcome_prices:
                return _parse_price(outcome_prices[str(ix)])
        elif isinstance(outcome_prices, (list, tuple)) and ix < len(outcome_prices):
            return _parse_price(outcome_prices[ix])
        return None

    def _get_token_for(ix: int, outcome_label: str) -> Optional[str]:
        if isinstance(clob_token_ids, dict):
            if outcome_label in clob_token_ids:
                return str(clob_token_ids[outcome_label])
            if str(ix) in clob_token_ids:
                return str(clob_token_ids[str(ix)])
        elif isinstance(clob_token_ids, (list, tuple)) and ix < len(clob_token_ids):
            return str(clob_token_ids[ix])
        return None

    game_start_dt = _extract_market_event_start(market)
    volume = market.get("volume") or market.get("volumeTotal") or market.get("totalVolume")
    liquidity = market.get("liquidity") or market.get("totalLiquidity")
    event_id = (
        market.get("eventId")
        or market.get("eventID")
        or market.get("event_id")
        or event_payload.get("id")
    )
    event_slug = market.get("eventSlug") or event_payload.get("slug")
    event_title = event_payload.get("title")
    ingestion_source = market.get("_ingestion_source", "markets_endpoint")

    rows: List[Dict[str, Any]] = []
    for idx, outcome_label in enumerate(outcomes):
        outcome_str = str(outcome_label)
        price = _get_price_for(idx, outcome_str)
        token_id = _get_token_for(idx, outcome_str)
        team_id, team_abbr, team_name = _match_team_for_outcome(outcome_str, teams_df)

        rows.append(
            {
                "event_id": str(event_id) if event_id is not None else None,
                "event_slug": event_slug,
                "event_title": event_title,
                "market_id": str(market_id),
                "market_slug": market_slug,
                "market_type": market_type,
                "outcome": outcome_str,
                "team_id": team_id,
                "team_abbr": team_abbr,
                "team_name": team_name,
                "last_price": price,
                "implied_prob": price,
                "token_id": token_id,
                "game_start_time": game_start_dt,
                "closed": bool(market.get("closed", False)),
                "enable_orderbook": market.get("enableOrderBook"),
                "volume": float(volume) if isinstance(volume, (int, float, str)) and str(volume) not in ("", "None") else None,
                "liquidity": float(liquidity) if isinstance(liquidity, (int, float, str)) and str(liquidity) not in ("", "None") else None,
                "ingestion_source": ingestion_source,
                "raw": market,
            }
        )
    return rows


def fetch_raw_moneyline_markets(
    req: NBAMoneylineMarketsRequest | None = None,
    client: GammaClient | None = None,
) -> List[Dict[str, Any]]:
    if req is None:
        req = NBAMoneylineMarketsRequest()

    client = client or get_default_client()

    if req.tag_id is not None:
        query_strategies: List[Dict[str, Any]] = [{"tag_id": req.tag_id}]
    else:
        nba_tags = get_nba_tag_ids(client=client, include_root=False)
        # `100639` is usually a broad "games" tag; keep NBA-specific tags first.
        prioritized_tags = [tid for tid in nba_tags if tid != 100639] or list(nba_tags)
        query_strategies = [{"tag_id": tid} for tid in prioritized_tags] if prioritized_tags else [{}]
        if req.tag_slug:
            query_strategies.insert(0, {"tag_slug": req.tag_slug})

    windows = _iter_query_windows(req.start_date_min, req.start_date_max, req.query_window_days)

    markets_by_id: Dict[str, Dict[str, Any]] = {}
    def _run_strategy(shape: Dict[str, Any]) -> None:
        for strategy in query_strategies:
            for w_start, w_end in windows:
                params: Dict[str, Any] = dict(strategy)
                params.update(shape)

                if req.only_open:
                    params["closed"] = "false"
                if w_start is not None:
                    params["start_date_min"] = _normalize_gamma_bound(w_start).isoformat()
                if w_end is not None:
                    params["start_date_max"] = _normalize_gamma_bound(w_end, end_of_day=True).isoformat()

                params["order"] = "startDate"
                params["ascending"] = "true"

                try:
                    iterator = client.paginate(
                        "/markets",
                        params=params,
                        limit=req.page_size,
                        max_pages=req.max_pages,
                    )
                    for m in iterator:
                        m_id = str(m.get("id") or m.get("marketId") or m.get("market_id"))
                        if not m_id or m_id == "None":
                            continue
                        if "_ingestion_source" not in m:
                            m["_ingestion_source"] = "markets_endpoint"
                        markets_by_id[m_id] = m
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "fetch_raw_moneyline_markets strategy failed params=%s error=%r",
                        params,
                        exc,
                    )
                    continue

    # Primary strategy (correct snake_case params).
    _run_strategy({"sports_market_type": "moneyline"})
    # Legacy fallback only if primary returned nothing.
    if not markets_by_id:
        _run_strategy({"sportsMarketType": "moneyline"})

    logger.info(
        "fetch_raw_moneyline_markets: obtidos %d markets (antes de qualquer pós-filtro).",
        len(markets_by_id),
    )
    return list(markets_by_id.values())


def fetch_raw_moneyline_markets_from_events(
    req: NBAMoneylineMarketsRequest,
    client: GammaClient,
) -> List[Dict[str, Any]]:
    """
    Fallback path: use /events payload (which includes nested markets) when /markets
    query shape is unstable.
    """
    from app.data.nodes.polymarket.gamma.nba.events_node import NBAEventsRequest, fetch_nba_events_raw

    events_req = NBAEventsRequest(
        tag_id=req.tag_id,
        tag_slug=req.tag_slug or "nba",
        only_open=req.only_open,
        page_size=req.page_size,
        max_pages=req.max_pages,
        start_date_min=req.start_date_min,
        start_date_max=req.start_date_max,
        query_window_days=req.query_window_days,
        only_game_or_award=False,
    )
    events_raw = fetch_nba_events_raw(req=events_req, client=client)
    if not events_raw:
        return []

    markets_by_id: Dict[str, Dict[str, Any]] = {}
    for ev in events_raw:
        ev_market_list = ev.get("markets")
        if not isinstance(ev_market_list, list):
            continue

        ev_summary = {
            "id": ev.get("id"),
            "slug": ev.get("slug"),
            "title": ev.get("title"),
            "startDate": ev.get("startDate") or ev.get("startTime"),
            "endDate": ev.get("endDate"),
            "tags": ev.get("tags"),
        }
        for m in ev_market_list:
            if not isinstance(m, dict):
                continue
            m2 = dict(m)
            if "events" not in m2 or not m2.get("events"):
                m2["events"] = [ev_summary]
            if m2.get("eventId") is None:
                m2["eventId"] = ev.get("id")
            if m2.get("eventSlug") is None:
                m2["eventSlug"] = ev.get("slug")
            m2["_ingestion_source"] = "events_fallback"

            m_id = str(m2.get("id") or m2.get("marketId") or m2.get("market_id"))
            if not m_id or m_id == "None":
                continue
            markets_by_id[m_id] = m2

    logger.info(
        "fetch_raw_moneyline_markets_from_events: obtidos %d markets do fallback de events.",
        len(markets_by_id),
    )
    return list(markets_by_id.values())


def moneyline_markets_to_df(
    markets_raw: List[Dict[str, Any]],
    teams_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for m in markets_raw:
        if not m:
            continue
        rows.extend(_parse_market_outcomes(m, teams_df=teams_df))
    return pd.DataFrame(rows)


def fetch_nba_moneyline_df(
    req: NBAMoneylineMarketsRequest | None = None,
    client: GammaClient | None = None,
) -> pd.DataFrame:
    if req is None:
        req = NBAMoneylineMarketsRequest()

    client = client or get_default_client()
    teams_df = fetch_nba_teams_df(NBATeamsRequest(), client=client)
    nba_tags = list(get_nba_tag_ids(client=client, include_root=False))

    markets_raw = fetch_raw_moneyline_markets(req=req, client=client)
    filtered_raw = [
        m for m in markets_raw
        if _market_has_nba_marker(m, nba_tag_ids=nba_tags) and _is_moneyline_market(m)
    ]

    # /markets can be unstable depending on query shape; fallback to nested markets in /events.
    if req.use_events_fallback and not filtered_raw:
        logger.warning("fetch_nba_moneyline_df: /markets returned no NBA moneyline rows, using events fallback.")
        fallback_raw = fetch_raw_moneyline_markets_from_events(req=req, client=client)
        filtered_raw = [
            m for m in fallback_raw
            if _market_has_nba_marker(m, nba_tag_ids=nba_tags) and _is_moneyline_market(m)
        ]

    if req.start_date_min is not None or req.start_date_max is not None:
        min_dt = req.start_date_min
        max_dt = req.start_date_max
        if min_dt is not None and min_dt.tzinfo is None:
            min_dt = min_dt.replace(tzinfo=timezone.utc)
        if max_dt is not None and max_dt.tzinfo is None:
            max_dt = max_dt.replace(tzinfo=timezone.utc)

        date_filtered: List[Dict[str, Any]] = []
        for m in filtered_raw:
            dt = _extract_market_reference_time(m)
            if dt is None:
                continue
            if dt.tzinfo is None and ((min_dt and min_dt.tzinfo) or (max_dt and max_dt.tzinfo)):
                dt = dt.replace(tzinfo=timezone.utc)
            if min_dt is not None and dt < min_dt:
                continue
            if max_dt is not None and dt > max_dt:
                continue
            date_filtered.append(m)
        filtered_raw = date_filtered

    if not filtered_raw:
        return pd.DataFrame()

    df = moneyline_markets_to_df(filtered_raw, teams_df=teams_df)
    return df


def fetch_nba_moneyline_models(
    req: NBAMoneylineMarketsRequest | None = None,
    client: GammaClient | None = None,
) -> List[NBAMoneylineOutcome]:
    df = fetch_nba_moneyline_df(req=req, client=client)
    models: List[NBAMoneylineOutcome] = []
    for _, row in df.iterrows():
        data = row.to_dict()
        raw = data.pop("raw", {})
        models.append(NBAMoneylineOutcome(raw=raw, **data))
    return models


def upsert_nba_moneyline_to_sqlite(
    df: pd.DataFrame,
    sqlite_path: str | Path,
    table_name: str = "polymarket_nba_moneyline",
) -> None:
    """
    Serializa 'raw' para JSON string antes do to_sql.
    """
    if df.empty:
        logger.warning("upsert_nba_moneyline_to_sqlite: DataFrame vazio, nada a inserir.")
        return

    sqlite_path = Path(sqlite_path)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    df2 = df.copy()
    if "raw" in df2.columns:
        df2["raw"] = df2["raw"].apply(lambda x: json.dumps(x or {}, ensure_ascii=False))

    with sqlite3.connect(sqlite_path) as conn:
        df2.to_sql(table_name, conn, if_exists="append", index=False)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    req = NBAMoneylineMarketsRequest()
    df = fetch_nba_moneyline_df(req=req)
    print("NBA Moneyline shape:", df.shape)
    print(df.head())
