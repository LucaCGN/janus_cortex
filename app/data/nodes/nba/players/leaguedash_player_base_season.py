#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Node: NBA player base stats (LeagueDashPlayerStats, measure_type=Base).

Usos:

1) Pipeline diária (cron/job)
   - Buscar estatísticas "base" por jogador na temporada.
   - Opcionalmente fazer upsert em SQLite.

2) Integração com CrewAI / tools
   - PlayerBaseRequest / PlayerBaseStats (Pydantic)
   - fetch_player_base_models é a entrypoint "tool-friendly".

3) Integração com FastAPI
   - Leitura direta da tabela SQLite populada por este node.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd
from nba_api.stats.endpoints import leaguedashplayerstats
from nba_api.stats.static import teams as teams_static
from pydantic import BaseModel, Field


# -----------------------------------------------------------------------------
# Helpers (team_slug mapping)
# -----------------------------------------------------------------------------

def _build_team_slug_map() -> Dict[int, str]:
    """
    Constrói um mapa {TEAM_ID -> TEAM_ABBREVIATION}, usando nba_api.stats.static.teams.
    """
    data = teams_static.get_teams()
    return {int(t["id"]): str(t["abbreviation"]) for t in data}


TEAM_ID_TO_SLUG: Dict[int, str] = _build_team_slug_map()


def _normalize_team_slug(team_id: int) -> str:
    """
    Retorna o team_slug (ex: 'MIA', 'DAL') para um dado TEAM_ID.
    """
    return TEAM_ID_TO_SLUG.get(int(team_id), "UNK")


def _filter_by_team_ids_and_slugs(
    df: pd.DataFrame,
    team_ids: Optional[Sequence[int]] = None,
    team_slugs: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """
    Filtro auxiliar por lista de TEAM_IDs e/ou team_slugs.
    """
    out = df
    if team_ids:
        out = out[out["TEAM_ID"].isin(list(team_ids))]
    if team_slugs:
        slugs_upper = [s.upper() for s in team_slugs]
        out = out[out["TEAM_ABBREVIATION"].isin(slugs_upper)]
    return out.reset_index(drop=True)


# -----------------------------------------------------------------------------
# Core fetch + transformação em DataFrame
# -----------------------------------------------------------------------------

def fetch_player_base_df(
    season: str,
    player_ids: Optional[Sequence[int]] = None,
    team_slugs: Optional[Sequence[str]] = None,
    sleep_seconds: float = 0.5,  # mantido por compatibilidade
) -> pd.DataFrame:
    """
    Node: estatísticas base (por jogo) para todos os jogadores da temporada
    (ou filtrado por time/jogadores) via LeagueDashPlayerStats.

    Parameters
    ----------
    season:
        Ex: "2024-25"
    player_ids:
        Lista opcional de PLAYER_IDs para filtrar.
    team_slugs:
        Lista opcional de abreviações de time, ex: ["MIA", "DAL"].
    sleep_seconds:
        Intervalo para evitar rate-limit (não utilizado diretamente aqui).
    """
    print(f"[players_base] Fetching LeagueDashPlayerStats Base for season={season} ...")

    endpoint = leaguedashplayerstats.LeagueDashPlayerStats(
        season=season,
        season_type_all_star="Regular Season",
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Base",
    )
    raw = endpoint.get_data_frames()[0]

    # Filtro inicial por team_slugs + player_ids
    if team_slugs or player_ids:
        raw = _filter_by_team_ids_and_slugs(
            raw,
            team_ids=None,
            team_slugs=team_slugs,
        )
        if player_ids:
            raw = raw[raw["PLAYER_ID"].isin(list(player_ids))]

    now_utc = datetime.now(timezone.utc).isoformat()

    def _get_col(name: str) -> pd.Series:
        return raw[name] if name in raw.columns else pd.NA

    df = pd.DataFrame(
        {
            "player_nba_id": raw["PLAYER_ID"].astype("int64"),
            "player_name": raw["PLAYER_NAME"].astype(str),
            "team_id": raw["TEAM_ID"].astype("int64"),
            "team_slug": raw["TEAM_ABBREVIATION"].astype(str),
            "season": season,
            "last_update": now_utc,
            "games_played": pd.to_numeric(_get_col("GP"), errors="coerce"),
            "wins": pd.to_numeric(_get_col("W"), errors="coerce"),
            "losses": pd.to_numeric(_get_col("L"), errors="coerce"),
            "win_pct": pd.to_numeric(_get_col("W_PCT"), errors="coerce"),
            "age": pd.to_numeric(_get_col("AGE"), errors="coerce"),
            "avg_minutes": pd.to_numeric(_get_col("MIN"), errors="coerce"),
            "avg_points": pd.to_numeric(_get_col("PTS"), errors="coerce"),
            "avg_assist": pd.to_numeric(_get_col("AST"), errors="coerce"),
            "avg_steals": pd.to_numeric(_get_col("STL"), errors="coerce"),
            "avg_blocks": pd.to_numeric(_get_col("BLK"), errors="coerce"),
            "avg_turnover": pd.to_numeric(_get_col("TOV"), errors="coerce"),
            "avg_rebounds": pd.to_numeric(_get_col("REB"), errors="coerce"),
            "off_reb": pd.to_numeric(_get_col("OREB"), errors="coerce"),
            "def_reb": pd.to_numeric(_get_col("DREB"), errors="coerce"),
            "fg_made": pd.to_numeric(_get_col("FGM"), errors="coerce"),
            "fg_attempted": pd.to_numeric(_get_col("FGA"), errors="coerce"),
            "fg_pct": pd.to_numeric(_get_col("FG_PCT"), errors="coerce"),
            "fg3_made": pd.to_numeric(_get_col("FG3M"), errors="coerce"),
            "fg3_attempted": pd.to_numeric(_get_col("FG3A"), errors="coerce"),
            "fg3_pct": pd.to_numeric(_get_col("FG3_PCT"), errors="coerce"),
            "ft_made": pd.to_numeric(_get_col("FTM"), errors="coerce"),
            "ft_attempted": pd.to_numeric(_get_col("FTA"), errors="coerce"),
            "ft_pct": pd.to_numeric(_get_col("FT_PCT"), errors="coerce"),
            "pf": pd.to_numeric(_get_col("PF"), errors="coerce"),
            "pf_drawn": pd.to_numeric(_get_col("PFD"), errors="coerce"),
            "plus_minus": pd.to_numeric(_get_col("PLUS_MINUS"), errors="coerce"),
            "nba_fantasy_pts": pd.to_numeric(_get_col("NBA_FANTASY_PTS"), errors="coerce"),
            "double_doubles": pd.to_numeric(_get_col("DD2"), errors="coerce"),
            "triple_doubles": pd.to_numeric(_get_col("TD3"), errors="coerce"),
        }
    )

    # Normaliza team_slug via nosso mapa (caso a API mude algo)
    df["team_slug"] = df["team_id"].map(_normalize_team_slug)

    return df.reset_index(drop=True)


# -----------------------------------------------------------------------------
# Pydantic models (para CrewAI / tools)
# -----------------------------------------------------------------------------

class PlayerBaseRequest(BaseModel):
    """
    Request para tools/agents:
    - season (obrigatório)
    - player_ids / team_slugs (opcionais)
    """
    season: str = Field(..., description="Ex: '2024-25'")
    player_ids: Optional[List[int]] = Field(
        default=None,
        description="Lista opcional de PLAYER_IDs para filtrar.",
    )
    team_slugs: Optional[List[str]] = Field(
        default=None,
        description="Lista opcional de abreviações de times (ex: ['MIA', 'DAL']).",
    )


class PlayerBaseStats(BaseModel):
    """
    Modelo "linha" do DataFrame base, útil para tools.
    """
    player_nba_id: int
    player_name: str
    team_id: int
    team_slug: str
    season: str
    last_update: str

    games_played: Optional[float] = None
    wins: Optional[float] = None
    losses: Optional[float] = None
    win_pct: Optional[float] = None
    age: Optional[float] = None
    avg_minutes: Optional[float] = None
    avg_points: Optional[float] = None
    avg_assist: Optional[float] = None
    avg_steals: Optional[float] = None
    avg_blocks: Optional[float] = None
    avg_turnover: Optional[float] = None
    avg_rebounds: Optional[float] = None
    off_reb: Optional[float] = None
    def_reb: Optional[float] = None
    fg_made: Optional[float] = None
    fg_attempted: Optional[float] = None
    fg_pct: Optional[float] = None
    fg3_made: Optional[float] = None
    fg3_attempted: Optional[float] = None
    fg3_pct: Optional[float] = None
    ft_made: Optional[float] = None
    ft_attempted: Optional[float] = None
    ft_pct: Optional[float] = None
    pf: Optional[float] = None
    pf_drawn: Optional[float] = None
    plus_minus: Optional[float] = None
    nba_fantasy_pts: Optional[float] = None
    double_doubles: Optional[float] = None
    triple_doubles: Optional[float] = None


def fetch_player_base_models(req: PlayerBaseRequest) -> List[PlayerBaseStats]:
    """
    Entry point "tool-friendly":
    - recebe PlayerBaseRequest
    - devolve lista de PlayerBaseStats
    """
    df = fetch_player_base_df(
        season=req.season,
        player_ids=req.player_ids,
        team_slugs=req.team_slugs,
    )
    records: List[PlayerBaseStats] = []
    for row in df.to_dict(orient="records"):
        records.append(PlayerBaseStats(**row))
    return records


# -----------------------------------------------------------------------------
# Upsert em SQLite
# -----------------------------------------------------------------------------

def upsert_players_base_into_sqlite(
    df: pd.DataFrame,
    db_path: str = "app/data/databases/nba.db",
    table_name: str = "nba_players_base",
) -> None:
    """
    Upsert simples em SQLite, usando append (chave composta pode ser tratada fora).
    """
    if df.empty:
        print("[players_base][upsert] DataFrame vazio, nada a fazer.")
        return

    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_file))
    try:
        df.to_sql(table_name, conn, if_exists="append", index=False)
    finally:
        conn.close()

    print(
        f"[players_base][upsert] Inseridas {len(df)} linhas em "
        f"{db_path} (tabela={table_name})."
    )


# -----------------------------------------------------------------------------
# Pydantic model rebuild (necessário com __future__.annotations)
# -----------------------------------------------------------------------------

PlayerBaseRequest.model_rebuild()
PlayerBaseStats.model_rebuild()


# -----------------------------------------------------------------------------
# CLI de debug (opcional)
# -----------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Node NBA Player Base — LeagueDashPlayerStats(Base)."
    )
    parser.add_argument(
        "--season",
        type=str,
        default="2024-25",
        help="Temporada no formato 'YYYY-YY' (ex: 2024-25).",
    )
    parser.add_argument(
        "--team-slugs",
        type=str,
        default="",
        help="Lista de times separados por vírgula (ex: 'MIA,DAL').",
    )
    args = parser.parse_args()

    team_slugs: Optional[List[str]] = None
    if args.team_slugs:
        team_slugs = [s.strip().upper() for s in args.team_slugs.split(",") if s.strip()]

    df = fetch_player_base_df(season=args.season, team_slugs=team_slugs)
    print(df.head().to_string(index=False))


if __name__ == "__main__":
    _cli()
