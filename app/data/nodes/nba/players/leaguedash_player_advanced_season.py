#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Node: NBA player advanced stats (LeagueDashPlayerStats, measure_type=Advanced).
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd
from nba_api.stats.endpoints import leaguedashplayerstats
from nba_api.stats.static import teams as teams_static
from pydantic import BaseModel, Field


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _build_team_slug_map() -> Dict[int, str]:
    """
    Constrói um mapa {TEAM_ID -> TEAM_ABBREVIATION}, usando nba_api.stats.static.teams.
    """
    data = teams_static.get_teams()
    return {int(t["id"]): str(t["abbreviation"]) for t in data}


TEAM_ID_TO_SLUG: Dict[int, str] = _build_team_slug_map()


def _normalize_team_slug(team_id: int) -> str:
    return TEAM_ID_TO_SLUG.get(int(team_id), "UNK")


def _filter_by_team_ids_and_slugs(
    df: pd.DataFrame,
    team_ids: Optional[Sequence[int]] = None,
    team_slugs: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    out = df
    if team_ids:
        out = out[out["TEAM_ID"].isin(list(team_ids))]
    if team_slugs:
        slugs_upper = [s.upper() for s in team_slugs]
        out = out[out["TEAM_ABBREVIATION"].isin(slugs_upper)]
    return out.reset_index(drop=True)


# -----------------------------------------------------------------------------
# Core fetch + transformação
# -----------------------------------------------------------------------------

def fetch_player_advanced_df(
    season: str,
    player_ids: Optional[Sequence[int]] = None,
    team_slugs: Optional[Sequence[str]] = None,
    sleep_seconds: float = 0.5,
) -> pd.DataFrame:
    """
    Advanced stats por jogador (por jogo) na temporada.
    """
    print(f"[players_advanced] Fetching LeagueDashPlayerStats Advanced for season={season} ...")

    endpoint = leaguedashplayerstats.LeagueDashPlayerStats(
        season=season,
        season_type_all_star="Regular Season",
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Advanced",
    )
    raw = endpoint.get_data_frames()[0]

    if player_ids or team_slugs:
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
            "min": pd.to_numeric(_get_col("MIN"), errors="coerce"),
            "usage_pct": pd.to_numeric(_get_col("USG_PCT"), errors="coerce"),
            "ts_pct": pd.to_numeric(_get_col("TS_PCT"), errors="coerce"),
            "efg_pct": pd.to_numeric(_get_col("EFG_PCT"), errors="coerce"),
            "off_rating": pd.to_numeric(_get_col("OFF_RATING"), errors="coerce"),
            "def_rating": pd.to_numeric(_get_col("DEF_RATING"), errors="coerce"),
            "net_rating": pd.to_numeric(_get_col("NET_RATING"), errors="coerce"),
            "ast_pct": pd.to_numeric(_get_col("AST_PCT"), errors="coerce"),
            "reb_pct": pd.to_numeric(_get_col("REB_PCT"), errors="coerce"),
            "oreb_pct": pd.to_numeric(_get_col("OREB_PCT"), errors="coerce"),
            "dreb_pct": pd.to_numeric(_get_col("DREB_PCT"), errors="coerce"),
            # TOV_PCT pode não vir em todas as seasons -> usamos opcional
            "tov_pct": pd.to_numeric(_get_col("TOV_PCT"), errors="coerce"),
            "pie": pd.to_numeric(_get_col("PIE"), errors="coerce"),
        }
    )

    df["team_slug"] = df["team_id"].map(_normalize_team_slug)

    return df.reset_index(drop=True)


# -----------------------------------------------------------------------------
# Pydantic models
# -----------------------------------------------------------------------------

class PlayerAdvancedRequest(BaseModel):
    season: str = Field(..., description="Ex: '2024-25'")
    player_ids: Optional[List[int]] = Field(
        default=None,
        description="Lista opcional de PLAYER_IDs para filtrar.",
    )
    team_slugs: Optional[List[str]] = Field(
        default=None,
        description="Lista opcional de abreviações de times (ex: ['MIA', 'DAL']).",
    )


class PlayerAdvancedStats(BaseModel):
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
    min: Optional[float] = None
    usage_pct: Optional[float] = None
    ts_pct: Optional[float] = None
    efg_pct: Optional[float] = None
    off_rating: Optional[float] = None
    def_rating: Optional[float] = None
    net_rating: Optional[float] = None
    ast_pct: Optional[float] = None
    reb_pct: Optional[float] = None
    oreb_pct: Optional[float] = None
    dreb_pct: Optional[float] = None
    tov_pct: Optional[float] = None
    pie: Optional[float] = None


def fetch_player_advanced_models(req: PlayerAdvancedRequest) -> List[PlayerAdvancedStats]:
    df = fetch_player_advanced_df(
        season=req.season,
        player_ids=req.player_ids,
        team_slugs=req.team_slugs,
    )
    records: List[PlayerAdvancedStats] = []
    for row in df.to_dict(orient="records"):
        records.append(PlayerAdvancedStats(**row))
    return records


# -----------------------------------------------------------------------------
# Upsert em SQLite
# -----------------------------------------------------------------------------

def upsert_players_advanced_into_sqlite(
    df: pd.DataFrame,
    db_path: str = "app/data/databases/nba.db",
    table_name: str = "nba_players_advanced",
) -> None:
    if df.empty:
        print("[players_advanced][upsert] DataFrame vazio, nada a fazer.")
        return

    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_file))
    try:
        df.to_sql(table_name, conn, if_exists="append", index=False)
    finally:
        conn.close()

    print(
        f"[players_advanced][upsert] Inseridas {len(df)} linhas em "
        f"{db_path} (tabela={table_name})."
    )


# -----------------------------------------------------------------------------
# Pydantic model rebuild
# -----------------------------------------------------------------------------

PlayerAdvancedRequest.model_rebuild()
PlayerAdvancedStats.model_rebuild()


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Node NBA Player Advanced — LeagueDashPlayerStats(Advanced)."
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

    df = fetch_player_advanced_df(season=args.season, team_slugs=team_slugs)
    print(df.head().to_string(index=False))


if __name__ == "__main__":
    _cli()
