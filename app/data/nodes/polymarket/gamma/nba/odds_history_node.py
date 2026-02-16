#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
odds_history_node.py
--------------------

Historical odds acquisition for NBA moneyline outcomes.

Primary source:
- CLOB prices history endpoint (`/prices-history`) by outcome token id.

Fallback source:
- Current snapshot price from Gamma moneyline payload when historical endpoint
  returns no points for a token/outcome.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
import sqlite3
from pydantic import BaseModel

from app.data.nodes.polymarket.gamma.gamma_client import GammaClient, get_default_client
from app.data.nodes.polymarket.gamma.nba.markets_moneyline_node import (
    NBAMoneylineMarketsRequest,
    fetch_nba_moneyline_df,
)

logger = logging.getLogger(__name__)

CLOB_BASE_URL = "https://clob.polymarket.com"


class NBAOddsHistoryRequest(BaseModel):
    """Request contract for historical odds acquisition."""

    tag_id: Optional[int] = None
    tag_slug: Optional[str] = "nba"
    only_open: bool = False
    page_size: int = 100
    max_pages: Optional[int] = 5
    start_date_min: Optional[datetime] = None
    start_date_max: Optional[datetime] = None
    use_events_fallback: bool = True

    # CLOB history query controls
    interval: str = "1m"  # known values include: 1m, 1h, 6h, 1d, 1w
    fidelity: int = 10
    max_outcomes: Optional[int] = 100
    request_timeout_sec: float = 12.0
    retries: int = 2
    retry_backoff_sec: float = 0.4

    # When CLOB history has no data, optionally keep a single current sample.
    allow_snapshot_fallback: bool = True
    snapshot_ts: Optional[datetime] = None


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_unix(dt: datetime) -> int:
    return int(_ensure_utc(dt).timestamp())


def _parse_history_payload(payload: Any) -> List[Dict[str, Any]]:
    """
    Parse CLOB /prices-history payload.

    Expected shape:
    {"history":[{"t": <unix>, "p": <float>}, ...]}
    """
    if not isinstance(payload, dict):
        return []

    history = payload.get("history")
    if not isinstance(history, list):
        return []

    points: List[Dict[str, Any]] = []
    for row in history:
        if not isinstance(row, dict):
            continue

        ts_raw = row.get("t", row.get("timestamp"))
        price_raw = row.get("p", row.get("price"))
        if ts_raw is None or price_raw is None:
            continue

        try:
            ts_unix = int(float(ts_raw))
            price = float(price_raw)
        except (TypeError, ValueError):
            continue

        points.append(
            {
                "ts": datetime.fromtimestamp(ts_unix, tz=timezone.utc),
                "price": price,
                "raw": row,
            }
        )

    points.sort(key=lambda x: x["ts"])
    return points


def _build_history_queries(req: NBAOddsHistoryRequest) -> List[Dict[str, Any]]:
    """
    Build ordered query shapes.
    Prefer explicit start/end windows when provided, then fallback to interval.
    """
    queries: List[Dict[str, Any]] = []

    if req.start_date_min is not None and req.start_date_max is not None:
        start_unix = _to_unix(req.start_date_min)
        end_unix = _to_unix(req.start_date_max)
        if end_unix >= start_unix:
            queries.append(
                {
                    "startTs": start_unix,
                    "endTs": end_unix,
                    "fidelity": req.fidelity,
                }
            )

    # Interval query is a reliable fallback on CLOB.
    queries.append(
        {
            "interval": req.interval,
            "fidelity": req.fidelity,
        }
    )

    # Deduplicate identical query shapes.
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for q in queries:
        key = json.dumps(q, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out


def fetch_clob_prices_history(
    token_id: str,
    req: NBAOddsHistoryRequest,
    session: Any = requests,
) -> List[Dict[str, Any]]:
    """
    Fetch history points from CLOB for one token/outcome.
    """
    if not token_id:
        return []

    queries = _build_history_queries(req)
    url = f"{CLOB_BASE_URL}/prices-history"

    for q in queries:
        params = {"market": token_id, **q}
        for attempt in range(req.retries + 1):
            try:
                resp = session.get(url, params=params, timeout=req.request_timeout_sec)
                if resp.status_code == 200:
                    points = _parse_history_payload(resp.json())
                    if points:
                        return points

                    # Empty history is not a transport failure; try next query shape.
                    logger.info(
                        "fetch_clob_prices_history: empty history token=%s params=%s",
                        token_id[:18],
                        params,
                    )
                    break

                # 4xx/5xx response, but still inspect details for logs.
                body = ""
                try:
                    body = resp.text[:220]
                except Exception:  # noqa: BLE001
                    body = "<unreadable>"

                logger.warning(
                    "fetch_clob_prices_history: non-200 token=%s status=%s params=%s body=%s",
                    token_id[:18],
                    resp.status_code,
                    params,
                    body,
                )

                # Non-transient client-side validation errors should not be retried.
                if resp.status_code < 500 and resp.status_code != 429:
                    break
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "fetch_clob_prices_history: request error token=%s params=%s attempt=%d error=%r",
                    token_id[:18],
                    params,
                    attempt + 1,
                    exc,
                )

            if attempt < req.retries:
                time.sleep(req.retry_backoff_sec * (2**attempt))

    return []


def _moneyline_req_from_history_req(req: NBAOddsHistoryRequest) -> NBAMoneylineMarketsRequest:
    return NBAMoneylineMarketsRequest(
        tag_id=req.tag_id,
        tag_slug=req.tag_slug,
        only_open=req.only_open,
        page_size=req.page_size,
        max_pages=req.max_pages,
        start_date_min=req.start_date_min,
        start_date_max=req.start_date_max,
        use_events_fallback=req.use_events_fallback,
    )


def fetch_nba_odds_history_df(
    req: NBAOddsHistoryRequest | None = None,
    client: GammaClient | None = None,
    session: Any = requests,
) -> pd.DataFrame:
    if req is None:
        req = NBAOddsHistoryRequest()

    client = client or get_default_client()
    moneyline_df = fetch_nba_moneyline_df(req=_moneyline_req_from_history_req(req), client=client)
    if moneyline_df.empty:
        return pd.DataFrame()

    if "token_id" not in moneyline_df.columns:
        return pd.DataFrame()

    candidates = moneyline_df[moneyline_df["token_id"].notna()].copy()
    if candidates.empty:
        return pd.DataFrame()

    # Stable order for deterministic results/tests.
    sort_cols = [c for c in ["event_slug", "market_id", "outcome"] if c in candidates.columns]
    if sort_cols:
        candidates = candidates.sort_values(by=sort_cols, na_position="last")
    candidates = candidates.reset_index(drop=True)

    if req.max_outcomes is not None and req.max_outcomes > 0:
        candidates = candidates.head(req.max_outcomes)

    rows: List[Dict[str, Any]] = []
    fallback_ts = _ensure_utc(req.snapshot_ts) if req.snapshot_ts is not None else datetime.now(timezone.utc)

    for _, item in candidates.iterrows():
        token_id = str(item.get("token_id") or "").strip()
        if not token_id:
            continue

        points = fetch_clob_prices_history(token_id=token_id, req=req, session=session)
        if points:
            for point in points:
                rows.append(
                    {
                        "event_id": item.get("event_id"),
                        "event_slug": item.get("event_slug"),
                        "event_title": item.get("event_title"),
                        "market_id": item.get("market_id"),
                        "market_slug": item.get("market_slug"),
                        "market_type": item.get("market_type"),
                        "outcome": item.get("outcome"),
                        "team_id": item.get("team_id"),
                        "team_abbr": item.get("team_abbr"),
                        "team_name": item.get("team_name"),
                        "token_id": token_id,
                        "game_start_time": item.get("game_start_time"),
                        "ts": point["ts"],
                        "price": point["price"],
                        "source": "clob_prices_history",
                        "ingestion_source": item.get("ingestion_source"),
                        "raw": point.get("raw"),
                    }
                )
            continue

        if req.allow_snapshot_fallback:
            snapshot_price = item.get("last_price")
            try:
                snapshot_price_f = float(snapshot_price)
            except (TypeError, ValueError):
                snapshot_price_f = None

            if snapshot_price_f is not None:
                rows.append(
                    {
                        "event_id": item.get("event_id"),
                        "event_slug": item.get("event_slug"),
                        "event_title": item.get("event_title"),
                        "market_id": item.get("market_id"),
                        "market_slug": item.get("market_slug"),
                        "market_type": item.get("market_type"),
                        "outcome": item.get("outcome"),
                        "team_id": item.get("team_id"),
                        "team_abbr": item.get("team_abbr"),
                        "team_name": item.get("team_name"),
                        "token_id": token_id,
                        "game_start_time": item.get("game_start_time"),
                        "ts": fallback_ts,
                        "price": snapshot_price_f,
                        "source": "snapshot_fallback",
                        "ingestion_source": item.get("ingestion_source"),
                        "raw": None,
                    }
                )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # Protect integrity against duplicate rows from retries/overlapping points.
    df = df.sort_values(by=["token_id", "ts", "source"]).drop_duplicates(
        subset=["token_id", "ts", "source"], keep="last"
    )
    return df.reset_index(drop=True)


def upsert_nba_odds_history_to_sqlite(
    df: pd.DataFrame,
    sqlite_path: str | Path,
    table_name: str = "polymarket_nba_odds_history_ticks",
) -> None:
    if df.empty:
        logger.warning("upsert_nba_odds_history_to_sqlite: DataFrame vazio, nada a inserir.")
        return

    sqlite_path = Path(sqlite_path)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    df2 = df.copy()
    if "raw" in df2.columns:
        df2["raw"] = df2["raw"].apply(
            lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else None
        )

    with sqlite3.connect(sqlite_path) as conn:
        df2.to_sql(table_name, conn, if_exists="append", index=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    req = NBAOddsHistoryRequest()
    out = fetch_nba_odds_history_df(req=req)
    print("NBA odds history shape:", out.shape)
    print(out.head())
