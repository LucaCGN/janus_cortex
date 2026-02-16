#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Node: NBA player usage / shot profile stats (LeagueDashPlayerStats, measure_type=Usage).
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
# Helpers
# -----------------------------------------------------------------------------

def _build_team_slug_map() -> Dict[int, str]:
    data = teams_static.get_teams()
    return {int(t["id"]): str(t["abbreviation"]) for t in data}


TEAM_ID_TO_SLUG: Dict[int, str] = _build_team_slug_map()


def _normalize_team_slug(team_id: int) -> str:
    return TEAM_ID_TO_SLUG.get(int(team_id), "UNK")


def _filter_by_team_slugs(
    df: pd.DataFrame,
    team_slugs: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    if not team_slugs:
        return df.reset_index(drop=True)
    slugs_upper = [s.upper() for s in team_slugs]
    out = df[df["TEAM_ABBREVIATION"].isin(slugs_upper)]
    return out.reset_index(drop=True)


# -----------------------------------------------------------------------------
# Core fetch
# -----------------------------------------------------------------------------

def fetch_player_usage_df(
    season: str,
    team_slugs: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """
    Busca LeagueDashPlayerStats (Usage) para todos os jogadores da temporada
    (ou filtrado por team_slugs) e normaliza as colunas.
    """
    print(f"[players_usage] Fetching LeagueDashPlayerStats Usage for season={season} ...")

    endpoint = leaguedashplayerstats.LeagueDashPlayerStats(
        season=season,
        season_type_all_star="Regular Season",
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Usage",
    )
    raw = endpoint.get_data_frames()[0]

    if team_slugs:
        raw = _filter_by_team_slugs(raw, team_slugs=team_slugs)

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
            "pct_fga_2pt": pd.to_numeric(_get_col("PCT_FGA_2PT"), errors="coerce"),
            "pct_fga_3pt": pd.to_numeric(_get_col("PCT_FGA_3PT"), errors="coerce"),
            "pct_pts_2pt": pd.to_numeric(_get_col("PCT_PTS_2PT"), errors="coerce"),
            "pct_pts_3pt": pd.to_numeric(_get_col("PCT_PTS_3PT"), errors="coerce"),
            "pct_pts_ft": pd.to_numeric(_get_col("PCT_PTS_FT"), errors="coerce"),
            "pct_pts_fastbreak": pd.to_numeric(_get_col("PCT_PTS_FB"), errors="coerce"),
            "pct_pts_paint": pd.to_numeric(_get_col("PCT_PTS_PAINT"), errors="coerce"),
            "pct_pts_off_tov": pd.to_numeric(_get_col("PCT_PTS_OFF_TOV"), errors="coerce"),
            "pct_ast_2pm": pd.to_numeric(_get_col("PCT_AST_2PM"), errors="coerce"),
            "pct_uast_2pm": pd.to_numeric(_get_col("PCT_UAST_2PM"), errors="coerce"),
            "pct_ast_3pm": pd.to_numeric(_get_col("PCT_AST_3PM"), errors="coerce"),
            "pct_uast_3pm": pd.to_numeric(_get_col("PCT_UAST_3PM"), errors="coerce"),
        }
    )

    df["team_slug"] = df["team_id"].map(_normalize_team_slug)

    return df.reset_index(drop=True)


# -----------------------------------------------------------------------------
# Pydantic models
# -----------------------------------------------------------------------------

class PlayerUsageRequest(BaseModel):
    season: str = Field(..., description="Ex: '2024-25'")
    team_slugs: Optional[List[str]] = Field(
        default=None,
        description="Times filtrados, ex: ['MIA', 'DAL']",
    )


class PlayerUsageStats(BaseModel):
    player_nba_id: int
    player_name: str
    team_id: int
    team_slug: str
    season: str
    last_update: str

    games_played: Optional[float] = None
    pct_fga_2pt: Optional[float] = None
    pct_fga_3pt: Optional[float] = None
    pct_pts_2pt: Optional[float] = None
    pct_pts_3pt: Optional[float] = None
    pct_pts_ft: Optional[float] = None
    pct_pts_fastbreak: Optional[float] = None
    pct_pts_paint: Optional[float] = None
    pct_pts_off_tov: Optional[float] = None
    pct_ast_2pm: Optional[float] = None
    pct_uast_2pm: Optional[float] = None
    pct_ast_3pm: Optional[float] = None
    pct_uast_3pm: Optional[float] = None


def fetch_player_usage_models(req: PlayerUsageRequest) -> List[PlayerUsageStats]:
    df = fetch_player_usage_df(season=req.season, team_slugs=req.team_slugs)
    records: List[PlayerUsageStats] = []
    for row in df.to_dict(orient="records"):
        records.append(PlayerUsageStats(**row))
    return records


# -----------------------------------------------------------------------------
# Upsert em SQLite
# -----------------------------------------------------------------------------

def upsert_players_usage_into_sqlite(
    df: pd.DataFrame,
    db_path: str = "app/data/databases/nba.db",
    table_name: str = "nba_players_usage",
) -> None:
    if df.empty:
        print("[players_usage][upsert] DataFrame vazio, nada a fazer.")
        return

    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_file))
    try:
        df.to_sql(table_name, conn, if_exists="append", index=False)
    finally:
        conn.close()

    print(
        f"[players_usage][upsert] Inseridas {len(df)} linhas em "
        f"{db_path} (tabela={table_name})."
    )


# -----------------------------------------------------------------------------
# Pydantic model rebuild
# -----------------------------------------------------------------------------

PlayerUsageRequest.model_rebuild()
PlayerUsageStats.model_rebuild()


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Node NBA Player Usage — LeagueDashPlayerStats(Usage)."
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

    df = fetch_player_usage_df(season=args.season, team_slugs=team_slugs)
    print(df.head().to_string(index=False))


if __name__ == "__main__":
    _cli()
