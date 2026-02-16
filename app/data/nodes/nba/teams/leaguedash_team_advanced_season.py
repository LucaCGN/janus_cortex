#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Node: NBA team season advanced stats (per-game).

Endpoint:
    - nba_api.stats.endpoints.LeagueDashTeamStats
      measure_type_detailed_defense="Advanced", per_mode_detailed="PerGame"

Usos:
    1) Pipeline diária (cron) -> CSV + SQLite
    2) Tool para CrewAI / function calling (TeamAdvancedRequest / TeamAdvancedStats)
    3) Endpoint FastAPI

Campos principais:

    Identificação:
        - team_id, team_name, team_slug, season, last_update

    Básicos:
        - games_played, wins, losses, win_pct

    Advanced:
        - pace
        - off_rating
        - def_rating
        - net_rating

    Enriquecidos (se disponíveis na API):
        - min
        - ast_pct        (AST_PCT)
        - ast_tov        (AST_TOV)
        - ast_ratio      (AST_RATIO)
        - oreb_pct       (OREB_PCT)
        - dreb_pct       (DREB_PCT)
        - reb_pct        (REB_PCT)
        - tm_tov_pct     (TM_TOV_PCT)
        - efg_pct        (EFG_PCT)
        - ts_pct         (TS_PCT)
        - pie            (PIE)
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from nba_api.stats.endpoints import leaguedashteamstats
from nba_api.stats.static import teams as static_teams
from pydantic import BaseModel, Field


# -----------------------------------------------------------------------------
# Pydantic models
# -----------------------------------------------------------------------------


class TeamAdvancedRequest(BaseModel):
    """
    Input genérico para stats advanced de times.
    """

    season: str = Field(..., description="Temporada no formato 'YYYY-YY', ex.: '2024-25'.")
    team_ids: Optional[List[int]] = Field(
        default=None,
        description="Lista opcional de TEAM_ID para filtrar.",
    )
    team_slugs: Optional[List[str]] = Field(
        default=None,
        description="Lista opcional de TEAM_ABBREVIATION (ex.: ['MIA', 'DAL']).",
    )


class TeamAdvancedStats(BaseModel):
    """
    Snapshot advanced da temporada por time.
    """

    team_id: int
    team_name: str
    team_slug: Optional[str]
    season: str
    last_update: datetime

    games_played: Optional[float] = None
    wins: Optional[float] = None
    losses: Optional[float] = None
    win_pct: Optional[float] = None

    pace: float
    off_rating: float
    def_rating: float
    net_rating: float

    min: Optional[float] = None

    ast_pct: Optional[float] = None
    ast_tov: Optional[float] = None
    ast_ratio: Optional[float] = None

    oreb_pct: Optional[float] = None
    dreb_pct: Optional[float] = None
    reb_pct: Optional[float] = None

    tm_tov_pct: Optional[float] = None

    efg_pct: Optional[float] = None
    ts_pct: Optional[float] = None

    pie: Optional[float] = None


TeamAdvancedRequest.model_rebuild()
TeamAdvancedStats.model_rebuild()


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _team_slug_map() -> Dict[int, str]:
    """Mapeia TEAM_ID -> TEAM_ABBREVIATION (slug) usando nba_api.stats.static.teams."""
    mapping: Dict[int, str] = {}
    try:
        for t in static_teams.get_teams() or []:
            try:
                tid = int(t.get("id"))
            except (TypeError, ValueError):
                continue
            slug = (t.get("abbreviation") or "").upper()
            mapping[tid] = slug
    except Exception:
        return mapping
    return mapping


def _add_numeric(
    df_raw: pd.DataFrame,
    df_out: pd.DataFrame,
    src_col: str,
    dst_col: str,
    required: bool = False,
) -> None:
    """
    Copia coluna numérica df_raw[src_col] -> df_out[dst_col].

    - Se required=True e a coluna não existir, lança RuntimeError.
    - Se não existir e não for required, preenche com NaN/None.
    """
    if src_col not in df_raw.columns:
        if required:
            raise RuntimeError(f"[teams_advanced] Missing required column '{src_col}' from API.")
        df_out[dst_col] = pd.NA
        return

    df_out[dst_col] = pd.to_numeric(df_raw[src_col], errors="coerce")


# -----------------------------------------------------------------------------
# Core fetch + transformação em DataFrame
# -----------------------------------------------------------------------------


def fetch_team_advanced_df(
    season: str,
    team_ids: Optional[List[int]] = None,
    team_slugs: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Node: advanced da temporada por time (LeagueDashTeamStats, measure_type='Advanced', PerGame).
    """
    print(f"[teams_advanced] Fetching LeagueDashTeamStats Advanced for season={season} ...")
    resp = leaguedashteamstats.LeagueDashTeamStats(
        season=season,
        season_type_all_star="Regular Season",
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Advanced",
    )
    raw = resp.get_data_frames()[0]
    df = raw.copy()

    # Filtro por TEAM_ID no frame bruto
    if team_ids:
        ids_set = {int(x) for x in team_ids}
        df["TEAM_ID"] = pd.to_numeric(df["TEAM_ID"], errors="coerce")
        df = df[df["TEAM_ID"].isin(ids_set)]

    if df.empty:
        print("[teams_advanced] WARNING - filtros resultaram em DataFrame vazio (fase bruta).")
        return pd.DataFrame(columns=list(TeamAdvancedStats.model_fields.keys()))

    required_cols = [
        "TEAM_ID",
        "TEAM_NAME",
        "PACE",
        "OFF_RATING",
        "DEF_RATING",
        "NET_RATING",
    ]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise RuntimeError(f"[teams_advanced] Missing expected columns: {missing}")

    df["TEAM_ID"] = pd.to_numeric(df["TEAM_ID"], errors="coerce").astype("Int64")

    slug_map = _team_slug_map()
    ts = datetime.now(timezone.utc).isoformat()

    out = pd.DataFrame()
    out["team_id"] = df["TEAM_ID"].astype(int)
    out["team_name"] = df["TEAM_NAME"].astype(str)
    out["team_slug"] = out["team_id"].map(slug_map)
    out["season"] = season
    out["last_update"] = ts

    # Básicos
    _add_numeric(df, out, "GP", "games_played", required=False)
    _add_numeric(df, out, "W", "wins", required=False)
    _add_numeric(df, out, "L", "losses", required=False)
    _add_numeric(df, out, "W_PCT", "win_pct", required=False)

    _add_numeric(df, out, "PACE", "pace", required=True)
    _add_numeric(df, out, "OFF_RATING", "off_rating", required=True)
    _add_numeric(df, out, "DEF_RATING", "def_rating", required=True)
    _add_numeric(df, out, "NET_RATING", "net_rating", required=True)

    _add_numeric(df, out, "MIN", "min", required=False)

    # Percentuais avançados
    _add_numeric(df, out, "AST_PCT", "ast_pct", required=False)
    _add_numeric(df, out, "AST_TOV", "ast_tov", required=False)
    _add_numeric(df, out, "AST_RATIO", "ast_ratio", required=False)

    _add_numeric(df, out, "OREB_PCT", "oreb_pct", required=False)
    _add_numeric(df, out, "DREB_PCT", "dreb_pct", required=False)
    _add_numeric(df, out, "REB_PCT", "reb_pct", required=False)

    _add_numeric(df, out, "TM_TOV_PCT", "tm_tov_pct", required=False)

    _add_numeric(df, out, "EFG_PCT", "efg_pct", required=False)
    _add_numeric(df, out, "TS_PCT", "ts_pct", required=False)

    _add_numeric(df, out, "PIE", "pie", required=False)

    # 🔎 Filtro por team_slugs agora no DataFrame normalizado
    if team_slugs:
        slugs = {s.upper() for s in team_slugs}
        out = out[out["team_slug"].astype(str).str.upper().isin(slugs)]

    out.sort_values("team_id", inplace=True)
    out.reset_index(drop=True, inplace=True)
    return out


# -----------------------------------------------------------------------------
# Pydantic wrapper
# -----------------------------------------------------------------------------


def fetch_team_advanced_models(req: TeamAdvancedRequest) -> List[TeamAdvancedStats]:
    """
    Versão Pydantic-friendly de fetch_team_advanced_df.
    """
    df = fetch_team_advanced_df(
        season=req.season,
        team_ids=req.team_ids,
        team_slugs=req.team_slugs,
    )
    if df.empty:
        return []

    records = df.where(pd.notnull(df), None).to_dict(orient="records")
    return [TeamAdvancedStats(**row) for row in records]


# -----------------------------------------------------------------------------
# SQLite upsert
# -----------------------------------------------------------------------------


def upsert_teams_advanced_into_sqlite(
    df: pd.DataFrame,
    db_path: str = "app/data/databases/nba.db",
    table_name: str = "nba_teams_advanced",
) -> None:
    """
    Upsert em SQLite usando (team_id, season) como chave primária.
    Converte datetime/pd.Timestamp para string ISO.
    """
    import sqlite3

    if df.empty:
        print("[teams_advanced] Nothing to upsert into SQLite (empty DataFrame).")
        return

    db_path_obj = Path(db_path)
    db_path_obj.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path_obj))
    try:
        cur = conn.cursor()
        cols = list(df.columns)

        schema_parts = [
            "team_id INTEGER NOT NULL",
            "season TEXT NOT NULL",
        ]

        for col, dtype in df.dtypes.items():
            if col in ("team_id", "season"):
                continue
            if pd.api.types.is_integer_dtype(dtype):
                coltype = "INTEGER"
            elif pd.api.types.is_float_dtype(dtype):
                coltype = "REAL"
            else:
                coltype = "TEXT"
            schema_parts.append(f"{col} {coltype}")

        schema_sql = ",\n    ".join(schema_parts + ["PRIMARY KEY (team_id, season)"])
        cur.execute(f"CREATE TABLE IF NOT EXISTS {table_name} (\n    {schema_sql}\n);")

        placeholders = ",".join(["?"] * len(cols))
        insert_sql = f"INSERT OR REPLACE INTO {table_name} ({','.join(cols)}) VALUES ({placeholders})"

        df_clean = df.where(pd.notnull(df), None)

        values = []
        for _, row in df_clean.iterrows():
            row_vals = []
            for c in cols:
                v = row[c]
                if isinstance(v, pd.Timestamp):
                    v = v.to_pydatetime().isoformat()
                elif isinstance(v, datetime):
                    v = v.isoformat()
                row_vals.append(v)
            values.append(tuple(row_vals))

        cur.executemany(insert_sql, values)
        conn.commit()
        print(f"[teams_advanced] Upserted {len(values)} rows into {db_path} (table={table_name}).")
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Node: NBA team advanced stats per season (LeagueDashTeamStats Advanced, PerGame)."
    )
    parser.add_argument(
        "--season",
        type=str,
        default="2024-25",
        help="Season string, e.g. '2024-25'.",
    )
    parser.add_argument(
        "--team-id",
        type=int,
        action="append",
        help="Optional TEAM_ID filter (can be passed multiple times).",
    )
    parser.add_argument(
        "--team-slug",
        type=str,
        action="append",
        help="Optional TEAM_ABBREVIATION filter (ex: --team-slug MIA --team-slug DAL).",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=None,
        help="Output directory (default: this script's directory).",
    )
    parser.add_argument(
        "--sqlite",
        action="store_true",
        help="If set, also upsert result into app/data/databases/nba.db.",
    )
    parser.add_argument(
        "--sqlite-table",
        type=str,
        default="nba_teams_advanced",
        help="SQLite table name when --sqlite is used.",
    )

    args = parser.parse_args()

    season = args.season
    out_dir = Path(args.out_dir) if args.out_dir else Path(__file__).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)

    df = fetch_team_advanced_df(
        season=season,
        team_ids=args.team_id,
        team_slugs=args.team_slug,
    )
    if df.empty:
        print("[teams_advanced] No rows returned, nothing to save.")
        return

    out_path = out_dir / f"nba_teams_advanced_{season.replace('-', '')}.csv"
    df.to_csv(out_path, index=False)
    print(f"[teams_advanced] Saved {len(df)} rows to: {out_path}")

    if args.sqlite:
        db_path = "app/data/databases/nba.db"
        upsert_teams_advanced_into_sqlite(df, db_path=db_path, table_name=args.sqlite_table)


if __name__ == "__main__":
    main()
