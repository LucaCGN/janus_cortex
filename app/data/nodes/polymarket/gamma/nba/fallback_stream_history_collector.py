#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fallback_stream_history_collector.py
------------------------------------

Append-only fallback stream collector for NBA moneyline odds.

This collector is intended for cases where direct historical endpoints
under-return or are unavailable for some outcomes/tokens.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd
from pydantic import BaseModel

from app.data.nodes.polymarket.gamma.gamma_client import GammaClient, get_default_client
from app.data.nodes.polymarket.gamma.nba.markets_moneyline_node import (
    NBAMoneylineMarketsRequest,
    fetch_nba_moneyline_df,
)
from app.data.nodes.polymarket.gamma.nba.odds_history_node import upsert_nba_odds_history_to_sqlite

logger = logging.getLogger(__name__)


class NBAFallbackStreamRequest(BaseModel):
    """Configuration for fallback stream sampling."""

    tag_id: Optional[int] = None
    tag_slug: Optional[str] = "nba"
    only_open: bool = False
    page_size: int = 100
    max_pages: Optional[int] = 5
    start_date_min: Optional[datetime] = None
    start_date_max: Optional[datetime] = None
    use_events_fallback: bool = True

    sample_count: int = 3
    sample_interval_sec: float = 1.0
    max_outcomes: Optional[int] = 100

    retries_per_sample: int = 1
    retry_backoff_sec: float = 0.5
    continue_on_error: bool = True


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_moneyline_req(req: NBAFallbackStreamRequest) -> NBAMoneylineMarketsRequest:
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


def _fetch_with_retries(
    moneyline_req: NBAMoneylineMarketsRequest,
    client: GammaClient,
    fetcher: Callable[[NBAMoneylineMarketsRequest, GammaClient], pd.DataFrame],
    retries_per_sample: int,
    retry_backoff_sec: float,
    sleep_fn: Callable[[float], None],
) -> pd.DataFrame:
    for attempt in range(retries_per_sample + 1):
        try:
            return fetcher(moneyline_req, client)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "collect_nba_fallback_stream_df: sample fetch failed attempt=%d/%d error=%r",
                attempt + 1,
                retries_per_sample + 1,
                exc,
            )
            if attempt < retries_per_sample:
                sleep_fn(retry_backoff_sec * (2**attempt))
    return pd.DataFrame()


def collect_nba_fallback_stream_df(
    req: NBAFallbackStreamRequest | None = None,
    client: GammaClient | None = None,
    now_fn: Callable[[], datetime] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    fetcher: Callable[[NBAMoneylineMarketsRequest, GammaClient], pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """
    Collect append-only fallback odds samples by repeatedly polling moneyline snapshot data.
    """
    if req is None:
        req = NBAFallbackStreamRequest()

    if req.sample_count <= 0:
        return pd.DataFrame()

    client = client or get_default_client()
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    if fetcher is None:
        def _default_fetcher(mreq: NBAMoneylineMarketsRequest, c: GammaClient) -> pd.DataFrame:
            return fetch_nba_moneyline_df(req=mreq, client=c)

        fetcher = _default_fetcher

    moneyline_req = _to_moneyline_req(req)
    all_batches: list[pd.DataFrame] = []

    for idx in range(req.sample_count):
        sample_no = idx + 1
        sample_ts = _ensure_utc(now_fn())

        df = _fetch_with_retries(
            moneyline_req=moneyline_req,
            client=client,
            fetcher=fetcher,
            retries_per_sample=req.retries_per_sample,
            retry_backoff_sec=req.retry_backoff_sec,
            sleep_fn=sleep_fn,
        )
        if df.empty:
            logger.warning(
                "collect_nba_fallback_stream_df: empty sample sample_no=%d/%d",
                sample_no,
                req.sample_count,
            )
            if not req.continue_on_error:
                raise RuntimeError(f"fallback stream sample {sample_no} returned empty dataset")
        else:
            batch = df.copy()
            sort_cols = [c for c in ["event_slug", "market_id", "outcome"] if c in batch.columns]
            if sort_cols:
                batch = batch.sort_values(by=sort_cols, na_position="last")
            if req.max_outcomes is not None and req.max_outcomes > 0:
                batch = batch.head(req.max_outcomes)

            batch["ts"] = sample_ts
            batch["source"] = "fallback_stream"
            batch["sample_no"] = sample_no
            batch["sample_total"] = req.sample_count
            all_batches.append(batch.reset_index(drop=True))

            logger.info(
                "collect_nba_fallback_stream_df: sampled rows=%d sample_no=%d/%d ts=%s",
                len(batch),
                sample_no,
                req.sample_count,
                sample_ts.isoformat(),
            )

        if idx < req.sample_count - 1 and req.sample_interval_sec > 0:
            sleep_fn(req.sample_interval_sec)

    if not all_batches:
        return pd.DataFrame()

    out = pd.concat(all_batches, ignore_index=True)
    return out.reset_index(drop=True)


def upsert_nba_fallback_stream_to_sqlite(
    df: pd.DataFrame,
    sqlite_path: str | Path,
    table_name: str = "polymarket_nba_odds_history_ticks",
) -> None:
    """
    Persist fallback samples in the same append-only ticks table used by direct history,
    preserving provenance via `source='fallback_stream'`.
    """
    upsert_nba_odds_history_to_sqlite(df=df, sqlite_path=sqlite_path, table_name=table_name)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    req = NBAFallbackStreamRequest(sample_count=2, sample_interval_sec=1.0, max_outcomes=10)
    out = collect_nba_fallback_stream_df(req=req)
    print("fallback stream shape:", out.shape)
    print(out.head())
