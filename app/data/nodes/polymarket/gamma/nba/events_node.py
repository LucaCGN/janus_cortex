#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
events_node.py
--------------

Node Gamma → NBA Events.

- Usa tags específicas da NBA para buscar eventos.
- Normaliza em um DataFrame para futura tabela events_nba.

Events incluem:
- Jogos normais (GAME)
- Prêmios (ex.: "Rookie of the Year") → tipo "AWARD"
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import sqlite3
from pydantic import BaseModel, Field

from app.data.nodes.polymarket.gamma.gamma_client import GammaClient, get_default_client
from app.data.nodes.polymarket.gamma.nba.sports_metadata import (
    NBASportMetadataRequest,
    fetch_nba_sport_metadata,
    get_nba_tag_ids,
)

logger = logging.getLogger(__name__)


class NBAEventsRequest(BaseModel):
    """Parâmetros genéricos para buscar eventos da NBA via Gamma."""
    tag_id: Optional[int] = None
    tag_slug: Optional[str] = None # Added field
    only_open: bool = True
    page_size: int = 100
    max_pages: Optional[int] = 5
    start_date_min: Optional[datetime] = None
    start_date_max: Optional[datetime] = None
    query_window_days: Optional[int] = 30
    only_game_or_award: bool = True  # pós-filtro defensivo


class NBAEvent(BaseModel):
    """Representação normalizada de um evento da NBA na Gamma."""
    event_id: str = Field(alias="id")
    slug: str
    title: str

    category: Optional[str] = None
    subcategory: Optional[str] = None
    event_type: str = Field(default="OTHER", description="GAME, AWARD ou OTHER")

    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    closed: bool = False
    enable_orderbook: Optional[bool] = None
    volume: Optional[float] = None
    liquidity: Optional[float] = None

    raw: Dict[str, Any]


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value)
        except Exception:  # noqa: BLE001
            return None
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        if v.endswith("Z"):
            v = v.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(v)
        except Exception:  # noqa: BLE001
            return None
    return None


def _normalize_gamma_bound(dt: datetime, end_of_day: bool = False) -> datetime:
    """
    Gamma date filters are more reliable with UTC day boundaries.
    We normalize query params broadly, then apply exact filtering client-side.
    """
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


def _event_has_nba_marker(raw: Dict[str, Any], nba_tag_ids: Optional[List[int]] = None) -> bool:
    slug = str(raw.get("slug") or "").lower()
    title = str(raw.get("title") or "").lower()

    if slug.startswith("nba-"):
        return True
    if title.startswith("nba ") or " nba " in f" {title} ":
        return True

    tags = raw.get("tags") or raw.get("tagIds") or raw.get("tag_ids")
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, dict):
                if str(tag.get("slug") or "").lower() == "nba":
                    return True
                if str(tag.get("label") or "").strip().lower() == "nba":
                    return True

    parsed_ids = _extract_tags(raw)
    if nba_tag_ids and any(t in parsed_ids for t in nba_tag_ids):
        return True

    return False


def _infer_event_type(e: Dict[str, Any]) -> str:
    title = str(e.get("title") or "").lower()
    slug = str(e.get("slug") or "").lower()

    if "rookie-of-the-year" in slug or "rookie of the year" in title:
        return "AWARD"
    if "most valuable player" in title or "mvp" in slug:
        return "AWARD"

    if " vs " in title or " vs. " in title or " @ " in title or " at " in title:
        return "GAME"

    return "OTHER"


def _extract_tags(raw: Dict[str, Any]) -> List[int]:
    """
    Extrai tags de um evento (se existirem) em formato List[int].
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
            if isinstance(item, dict):
                # Se for objeto (ex: {'id': '123', ...}), pegamos o id
                val = item.get("id")
                if val is not None:
                    parts.append(str(val).strip())
            else:
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


def _parse_event_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    start_time = (
        raw.get("startTime") or raw.get("startDate") or raw.get("eventStartTime") or raw.get("closesAt")
    )
    end_time = raw.get("endTime") or raw.get("eventEndTime")

    category = raw.get("category") or raw.get("categoryLabel")
    subcategory = raw.get("subcategory") or raw.get("subcategoryLabel")

    volume = raw.get("volume") or raw.get("volumeTotal") or raw.get("totalVolume")
    liquidity = raw.get("liquidity") or raw.get("totalLiquidity")

    event_type = _infer_event_type(raw)
    tags = _extract_tags(raw)

    return {
        "event_id": str(raw.get("id")),
        "slug": raw.get("slug"),
        "title": raw.get("title"),
        "category": category,
        "subcategory": subcategory,
        "event_type": event_type,
        "start_time": _parse_dt(start_time),
        "end_time": _parse_dt(end_time),
        "closed": bool(raw.get("closed", False)),
        "enable_orderbook": raw.get("enableOrderBook"),
        "volume": float(volume) if isinstance(volume, (int, float, str)) and str(volume) not in ("", "None") else None,
        "liquidity": float(liquidity) if isinstance(liquidity, (int, float, str)) and str(liquidity) not in ("", "None") else None,
        "tags": tags,
        "raw": raw,
    }


def fetch_nba_events_raw(
    req: NBAEventsRequest | None = None,
    client: GammaClient | None = None,
) -> List[Dict[str, Any]]:
    if req is None:
        req = NBAEventsRequest()

    client = client or get_default_client()

    # Decide strategies (Gamma is sensitive to exact parameter names and tag behavior).
    fallback_query_strategies: List[Dict[str, Any]] = []
    if req.tag_slug:
        query_strategies: List[Dict[str, Any]] = [{"tag_slug": req.tag_slug}]
    elif req.tag_id is not None:
        query_strategies = [{"tag_id": req.tag_id}]
    else:
        nba_tags = get_nba_tag_ids(client=client, include_root=False)
        if not nba_tags:
            meta = fetch_nba_sport_metadata(NBASportMetadataRequest(), client=client)
            nba_tags = meta.tag_ids or []

        query_strategies = [{"tag_slug": "nba"}]
        fallback_query_strategies = [{"tag_id": tid} for tid in nba_tags if tid is not None]

    windows = _iter_query_windows(req.start_date_min, req.start_date_max, req.query_window_days)

    items_by_id: Dict[str, Dict[str, Any]] = {}

    def _execute_strategies(strategies: List[Dict[str, Any]]) -> None:
        for strategy in strategies:
            for w_start, w_end in windows:
                params: Dict[str, Any] = dict(strategy)
                if req.only_open:
                    params["closed"] = "false"

                # NOTE: Gamma expects snake_case query params here.
                if w_start is not None:
                    params["start_date_min"] = _normalize_gamma_bound(w_start).isoformat()
                if w_end is not None:
                    params["start_date_max"] = _normalize_gamma_bound(w_end, end_of_day=True).isoformat()

                params["order"] = "startDate"
                params["ascending"] = "true"

                try:
                    iterator = client.paginate(
                        "/events",
                        params=params,
                        limit=req.page_size,
                        max_pages=req.max_pages,
                    )
                    for ev in iterator:
                        start_time_val = ev.get("startDate") or ev.get("startTime")
                        if req.start_date_min or req.start_date_max:
                            dt = _parse_dt(start_time_val)
                            if dt:
                                min_dt = req.start_date_min
                                max_dt = req.start_date_max

                                if dt.tzinfo is None and (
                                    (min_dt is not None and min_dt.tzinfo is not None)
                                    or (max_dt is not None and max_dt.tzinfo is not None)
                                ):
                                    dt = dt.replace(tzinfo=timezone.utc)

                                if min_dt is not None and min_dt.tzinfo is None and dt.tzinfo is not None:
                                    min_dt = min_dt.replace(tzinfo=timezone.utc)
                                if max_dt is not None and max_dt.tzinfo is None and dt.tzinfo is not None:
                                    max_dt = max_dt.replace(tzinfo=timezone.utc)

                                if min_dt and dt < min_dt:
                                    continue
                                if max_dt and dt > max_dt:
                                    continue

                        ev_id = str(ev.get("id"))
                        items_by_id[ev_id] = ev
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "fetch_nba_events_raw strategy failed params=%s error=%r",
                        params,
                        exc,
                    )
                    continue

    _execute_strategies(query_strategies)
    if not items_by_id and fallback_query_strategies:
        _execute_strategies(fallback_query_strategies)

    # Defensive NBA guardrail: even with tag filters, some responses can bleed.
    if req.tag_id is None and req.tag_slug is None:
        nba_tags = get_nba_tag_ids(client=client, include_root=False)
        before = len(items_by_id)
        items_by_id = {
            ev_id: ev
            for ev_id, ev in items_by_id.items()
            if _event_has_nba_marker(ev, nba_tag_ids=list(nba_tags))
        }
        if len(items_by_id) != before:
            logger.info(
                "fetch_nba_events_raw: nba marker guardrail filtered %d -> %d events",
                before,
                len(items_by_id),
            )

    logger.info(
        "fetch_nba_events_raw: obtidos %d eventos (antes de qualquer pós-filtro).",
        len(items_by_id),
    )
    return list(items_by_id.values())


def fetch_nba_events_df(
    req: NBAEventsRequest | None = None,
    client: GammaClient | None = None,
) -> pd.DataFrame:
    if req is None:
        req = NBAEventsRequest()

    raw = fetch_nba_events_raw(req=req, client=client)
    if not raw:
        return pd.DataFrame()

    rows = [_parse_event_row(e) for e in raw]
    df = pd.DataFrame(rows)

    # Pós-filtro por tags NBA, se tivermos tags em alguma linha
    nba_tags = get_nba_tag_ids(client=client, include_root=False)
    
    # If a specific tag was requested, add it to valid tags list
    # If a specific tag was requested, use ONLY that tag for filtering to be strict
    # Otherwise use the default known NBA tags list
    if req.tag_id is not None:
         valid_tags = [req.tag_id]
    else:
         valid_tags = list(nba_tags)
         
    # If using tag_slug, we assume API filtering worked (since we can't easily check 'nba' via IDs without metadata lookup)
    if req.tag_slug is not None:
         # Skip post-filtering for tags, as we trust the API or assume all returned events are relevant
         pass
    elif not df.empty and valid_tags and "tags" in df.columns:
        mask_has_tags = df["tags"].apply(lambda v: bool(v))
        if mask_has_tags.any():
            # Check if any of the event's tags match our VALID tags list
            mask_nba = df["tags"].apply(lambda v: any(t in v for t in valid_tags))
            before = len(df)
            df = df[mask_nba].reset_index(drop=True)
            logger.info(
                "fetch_nba_events_df: filtrados por tags NBA (%s): %d -> %d eventos",
                valid_tags,
                before,
                len(df),
            )

    # Pós-filtro por tipo (GAME / AWARD) se solicitado
    if req.only_game_or_award and not df.empty and "event_type" in df.columns:
        before = len(df)
        df = df[df["event_type"].isin(["GAME", "AWARD"])].reset_index(drop=True)
        logger.info(
            "fetch_nba_events_df: filtrados por event_type (GAME/AWARD): %d -> %d eventos",
            before,
            len(df),
        )

    return df


def fetch_nba_events_models(
    req: NBAEventsRequest | None = None,
    client: GammaClient | None = None,
) -> List[NBAEvent]:
    df = fetch_nba_events_df(req=req, client=client)
    models: List[NBAEvent] = []
    for _, row in df.iterrows():
        data = row.to_dict()
        raw = data.pop("raw", {})
        # tags/event_id já estão mapeados corretamente
        models.append(NBAEvent(id=data.pop("event_id"), raw=raw, **data))
    return models


def upsert_nba_events_to_sqlite(
    df: pd.DataFrame,
    sqlite_path: str | Path,
    table_name: str = "polymarket_nba_events",
) -> None:
    """
    Upsert simples em SQLite para a tabela de eventos da Polymarket (NBA).

    - Se o DataFrame estiver vazio, apenas loga/retorna.
    - Se a tabela ainda não existir, cria com o schema atual (todas as colunas).
    - Se a tabela já existir, faz append apenas das colunas que já existem
      na tabela (ignora colunas novas, ex.: 'tags', se o schema for antigo).
    """
    if df.empty:
        # Mantemos stderr só pra ficar visível em testes integrados.
        print(
            "upsert_nba_events_to_sqlite: DataFrame vazio, nada a inserir.",
            file=sys.stderr,
        )
        return

    sqlite_path = Path(sqlite_path)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    df2 = df.copy()

    # Se 'raw' estiver presente, garantimos que vai como JSON string.
    if "raw" in df2.columns:
        df2["raw"] = df2["raw"].apply(
            lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else x
        )

    with sqlite3.connect(sqlite_path) as conn:
        # Verifica se a tabela já existe
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        exists = cur.fetchone() is not None

        if not exists:
            # Primeira vez: criamos a tabela com o schema completo atual
            df2.to_sql(table_name, conn, if_exists="append", index=False)
            return

        # Tabela já existe: lemos as colunas existentes
        existing_cols = {
            row[1] for row in conn.execute(f"PRAGMA table_info('{table_name}')")
        }

        # Mantém apenas colunas em comum entre o DF e a tabela
        common_cols = [c for c in df2.columns if c in existing_cols]

        if not common_cols:
            # Caso extremo: schema totalmente incompatível.
            # Em ambiente de dev/teste faz mais sentido recriar a tabela.
            df2.to_sql(table_name, conn, if_exists="replace", index=False)
            return

        # Append apenas das colunas que a tabela conhece (evita erro com 'tags')
        df2[common_cols].to_sql(table_name, conn, if_exists="append", index=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    req = NBAEventsRequest()
    df = fetch_nba_events_df(req=req)
    print("NBA Events shape:", df.shape)
    print(df.head())
