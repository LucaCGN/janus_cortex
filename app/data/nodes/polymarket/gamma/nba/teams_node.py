#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
teams_node.py
-------------

Node Gamma → NBA Teams.

- Tenta primeiro GET /sports/list-teams?sport=nba
- Caso vazio/não disponível, faz fallback para GET /teams?league=NBA
- Retorna DataFrame com times.
- Upsert serializa 'raw' como JSON.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import sqlite3
from pydantic import BaseModel

from app.data.nodes.polymarket.gamma.gamma_client import GammaClient, get_default_client

logger = logging.getLogger(__name__)


class NBATeamsRequest(BaseModel):
    """Parâmetros para fetch de times NBA pela Gamma."""
    league: str = "NBA"
    page_size: int = 100
    max_pages: Optional[int] = 5


class NBATeam(BaseModel):
    """Representação simplificada de um time NBA na Gamma."""
    team_id: str
    name: str
    abbreviation: Optional[str] = None
    league: Optional[str] = None
    logo: Optional[str] = None
    alias: Optional[str] = None
    record: Optional[str] = None
    city: Optional[str] = None
    raw: Dict[str, Any]


def fetch_nba_teams_raw(
    req: NBATeamsRequest | None = None,
    client: GammaClient | None = None,
) -> List[Dict[str, Any]]:
    if req is None:
        req = NBATeamsRequest()
    client = client or get_default_client()

    # 1) tenta /sports/list-teams?sport=nba
    data = client.get_sports_teams(sport="nba")
    if data:
        logger.info("fetch_nba_teams_raw: usando /sports/list-teams?sport=nba (%d itens).", len(data))
        return data

    # 2) fallback: /teams?league=NBA com paginação
    params: Dict[str, Any] = {"league": req.league.upper()}
    items: List[Dict[str, Any]] = []
    for team in client.paginate("/teams", params=params, limit=req.page_size, max_pages=req.max_pages):
        items.append(team)
    logger.info("fetch_nba_teams_raw: fallback /teams?league=NBA -> %d itens.", len(items))
    return items


def _parse_team_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mapeia o dict cru da Gamma para colunas normalizadas.
    Os campos podem variar entre /sports/list-teams e /teams.
    """
    return {
        "team_id": str(raw.get("id") or raw.get("teamId") or raw.get("team_id")),
        "name": raw.get("name") or raw.get("teamName") or raw.get("displayName"),
        "abbreviation": raw.get("abbreviation") or raw.get("abbr"),
        "league": raw.get("league") or raw.get("sport") or raw.get("leagueName"),
        "logo": raw.get("logo") or raw.get("image") or raw.get("icon"),
        "alias": raw.get("alias"),
        "record": raw.get("record"),
        "city": raw.get("city"),
        "raw": raw,
    }


def fetch_nba_teams_df(
    req: NBATeamsRequest | None = None,
    client: GammaClient | None = None,
) -> pd.DataFrame:
    """Retorna todos os times NBA em um DataFrame normalizado."""
    raw = fetch_nba_teams_raw(req=req, client=client)
    if not raw:
        return pd.DataFrame()
    rows = [_parse_team_row(t) for t in raw]
    df = pd.DataFrame(rows)
    return df


def fetch_nba_teams_models(
    req: NBATeamsRequest | None = None,
    client: GammaClient | None = None,
) -> List[NBATeam]:
    df = fetch_nba_teams_df(req=req, client=client)
    models: List[NBATeam] = []
    for _, row in df.iterrows():
        data = row.to_dict()
        raw = data.pop("raw", {})
        models.append(NBATeam(**data, raw=raw))
    return models


def upsert_nba_teams_to_sqlite(
    df: pd.DataFrame,
    sqlite_path: str | Path,
    table_name: str = "polymarket_nba_teams",
) -> None:
    """
    Upsert simples em SQLite usando pandas.to_sql.
    Serializa 'raw' para JSON string antes do to_sql.
    """
    if df.empty:
        logger.warning("upsert_nba_teams_to_sqlite: DataFrame vazio, nada a inserir.")
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
    req = NBATeamsRequest()
    df = fetch_nba_teams_df(req=req)
    print("NBA Teams shape:", df.shape)
    print(df.head())
