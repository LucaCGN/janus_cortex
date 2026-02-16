#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Node: NBA team season base stats (per-game).

Endpoint:
    - nba_api.stats.endpoints.LeagueDashTeamStats
      measure_type_detailed_defense="Base", per_mode_detailed="PerGame"

Objetivos / usos:

1) Pipeline diária (cron/job)
   - Rodar via CLI para gerar CSV por temporada
   - Opcionalmente fazer upsert em SQLite (nba.db)

2) Integração com CrewAI / function calling
   - Usar TeamBaseRequest / TeamBaseStats (Pydantic)
   - Registrar fetch_team_base_models como ferramenta

3) Integração com FastAPI
   - Body: TeamBaseRequest
   - Retorno: List[TeamBaseStats]

Campos principais:

    Identificação:
        - team_id, team_name, team_slug, season, last_update

    Base:
        - games_played, wins, losses, season_win_rate
        - avg_points, avg_turnovers, avg_plus_minus

    Enriquecidos (quando disponíveis):
        - avg_rebounds, off_reb, def_reb
        - avg_assists, avg_steals, avg_blocks
        - fg_made, fg_attempted, fg_pct
        - fg3_made, fg3_attempted, fg3_pct
        - ft_made, ft_attempted, ft_pct
        - pf
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
# Pydantic models (para CrewAI / FastAPI / tools)
# -----------------------------------------------------------------------------


class TeamBaseRequest(BaseModel):
    """
    Input genérico para buscar stats base de times.

    Exemplos:
        - Todos os times da temporada:
            {"season": "2024-25"}

        - Alguns times por ID:
            {"season": "2024-25", "team_ids": [1610612748, 1610612742]}

        - Alguns times por sigla:
            {"season": "2024-25", "team_slugs": ["MIA", "DAL"]}
    """

    season: str = Field(..., description="Temporada no formato 'YYYY-YY', ex.: '2024-25'.")
    team_ids: Optional[List[int]] = Field(
        default=None,
        description="Lista opcional de TEAM_ID para filtrar (ex.: [1610612748]).",
    )
    team_slugs: Optional[List[str]] = Field(
        default=None,
        description="Lista opcional de siglas (TEAM_ABBREVIATION), ex.: ['MIA', 'DAL'].",
    )


class TeamBaseStats(BaseModel):
    """
    Snapshot base da temporada por time (per-game).
    """

    team_id: int
    team_name: str
    team_slug: Optional[str]
    season: str
    last_update: datetime

    games_played: float
    wins: float
    losses: float
    season_win_rate: float

    avg_points: float
    avg_turnovers: float
    avg_plus_minus: float

    avg_rebounds: Optional[float] = None
    off_reb: Optional[float] = None
    def_reb: Optional[float] = None

    avg_assists: Optional[float] = None
    avg_steals: Optional[float] = None
    avg_blocks: Optional[float] = None

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


TeamBaseRequest.model_rebuild()
TeamBaseStats.model_rebuild()


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
            raise RuntimeError(f"[teams_base] Missing required column '{src_col}' from API.")
        df_out[dst_col] = pd.NA
        return

    df_out[dst_col] = pd.to_numeric(df_raw[src_col], errors="coerce")


# -----------------------------------------------------------------------------
# Core fetch + transformação em DataFrame
# -----------------------------------------------------------------------------


def fetch_team_base_df(
    season: str,
    team_ids: Optional[List[int]] = None,
    team_slugs: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Node: base da temporada por time (LeagueDashTeamStats, measure_type='Base', PerGame).

    Saída (DataFrame já normalizado para TeamBaseStats).
    """
    print(f"[teams_base] Fetching LeagueDashTeamStats Base for season={season} ...")
    resp = leaguedashteamstats.LeagueDashTeamStats(
        season=season,
        season_type_all_star="Regular Season",
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Base",
    )
    raw = resp.get_data_frames()[0]
    df = raw.copy()

    # Filtro por TEAM_ID diretamente no frame bruto (eficiente e seguro)
    if team_ids:
        ids_set = {int(x) for x in team_ids}
        df["TEAM_ID"] = pd.to_numeric(df["TEAM_ID"], errors="coerce")
        df = df[df["TEAM_ID"].isin(ids_set)]

    if df.empty:
        print("[teams_base] WARNING - filtros resultaram em DataFrame vazio (fase bruta).")
        return pd.DataFrame(columns=list(TeamBaseStats.model_fields.keys()))

    required_cols = [
        "TEAM_ID",
        "TEAM_NAME",
        "GP",
        "W",
        "L",
        "W_PCT",
        "PTS",
        "TOV",
        "PLUS_MINUS",
    ]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise RuntimeError(f"[teams_base] Missing expected columns in raw frame: {missing}")

    df["TEAM_ID"] = pd.to_numeric(df["TEAM_ID"], errors="coerce").astype("Int64")

    slug_map = _team_slug_map()
    ts = datetime.now(timezone.utc).isoformat()

    out = pd.DataFrame()
    out["team_id"] = df["TEAM_ID"].astype(int)
    out["team_name"] = df["TEAM_NAME"].astype(str)
    out["team_slug"] = out["team_id"].map(slug_map)
    out["season"] = season
    out["last_update"] = ts

    # Números básicos
    _add_numeric(df, out, "GP", "games_played", required=True)
    _add_numeric(df, out, "W", "wins", required=True)
    _add_numeric(df, out, "L", "losses", required=True)
    _add_numeric(df, out, "W_PCT", "season_win_rate", required=True)
    _add_numeric(df, out, "PTS", "avg_points", required=True)
    _add_numeric(df, out, "TOV", "avg_turnovers", required=True)
    _add_numeric(df, out, "PLUS_MINUS", "avg_plus_minus", required=True)

    # Rebotes
    _add_numeric(df, out, "REB", "avg_rebounds", required=False)
    _add_numeric(df, out, "OREB", "off_reb", required=False)
    _add_numeric(df, out, "DREB", "def_reb", required=False)

    # Outras stats base
    _add_numeric(df, out, "AST", "avg_assists", required=False)
    _add_numeric(df, out, "STL", "avg_steals", required=False)
    _add_numeric(df, out, "BLK", "avg_blocks", required=False)

    # Arremessos
    _add_numeric(df, out, "FGM", "fg_made", required=False)
    _add_numeric(df, out, "FGA", "fg_attempted", required=False)
    _add_numeric(df, out, "FG_PCT", "fg_pct", required=False)

    _add_numeric(df, out, "FG3M", "fg3_made", required=False)
    _add_numeric(df, out, "FG3A", "fg3_attempted", required=False)
    _add_numeric(df, out, "FG3_PCT", "fg3_pct", required=False)

    _add_numeric(df, out, "FTM", "ft_made", required=False)
    _add_numeric(df, out, "FTA", "ft_attempted", required=False)
    _add_numeric(df, out, "FT_PCT", "ft_pct", required=False)

    # Faltas
    _add_numeric(df, out, "PF", "pf", required=False)

    # 🔎 Filtro por team_slugs agora no DataFrame normalizado
    if team_slugs:
        slugs = {s.upper() for s in team_slugs}
        out = out[out["team_slug"].astype(str).str.upper().isin(slugs)]

    out.sort_values("team_id", inplace=True)
    out.reset_index(drop=True, inplace=True)
    return out


# -----------------------------------------------------------------------------
# Conversão para Pydantic (CrewAI / FastAPI)
# -----------------------------------------------------------------------------


def fetch_team_base_models(req: TeamBaseRequest) -> List[TeamBaseStats]:
    """
    Versão Pydantic-friendly de fetch_team_base_df.
    """
    df = fetch_team_base_df(
        season=req.season,
        team_ids=req.team_ids,
        team_slugs=req.team_slugs,
    )
    if df.empty:
        return []

    records = df.where(pd.notnull(df), None).to_dict(orient="records")
    return [TeamBaseStats(**row) for row in records]


# -----------------------------------------------------------------------------
# Integração com SQLite (pipeline diária)
# -----------------------------------------------------------------------------


def upsert_teams_base_into_sqlite(
    df: pd.DataFrame,
    db_path: str = "app/data/databases/nba.db",
    table_name: str = "nba_teams_base",
) -> None:
    """
    Upsert simples em SQLite usando (team_id, season) como chave primária.
    Converte datetime/pd.Timestamp para string ISO antes de inserir.
    """
    import sqlite3

    if df.empty:
        print("[teams_base] Nothing to upsert into SQLite (empty DataFrame).")
        return

    # Garante que o diretório exista
    db_path_obj = Path(db_path)
    db_path_obj.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path_obj))
    try:
        cur = conn.cursor()
        cols = list(df.columns)

        # Cria tabela se não existir
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
                # Normaliza datetime / Timestamp -> string ISO
                if isinstance(v, pd.Timestamp):
                    v = v.to_pydatetime().isoformat()
                elif isinstance(v, datetime):
                    v = v.isoformat()
                row_vals.append(v)
            values.append(tuple(row_vals))

        cur.executemany(insert_sql, values)
        conn.commit()
        print(f"[teams_base] Upserted {len(values)} rows into {db_path} (table={table_name}).")
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Node: NBA team base stats per season (LeagueDashTeamStats Base, PerGame)."
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
        default="nba_teams_base",
        help="SQLite table name when --sqlite is used.",
    )

    args = parser.parse_args()

    season = args.season
    out_dir = Path(args.out_dir) if args.out_dir else Path(__file__).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)

    df = fetch_team_base_df(
        season=season,
        team_ids=args.team_id,
        team_slugs=args.team_slug,
    )
    if df.empty:
        print("[teams_base] No rows returned, nothing to save.")
        return

    out_path = out_dir / f"nba_teams_base_{season.replace('-', '')}.csv"
    df.to_csv(out_path, index=False)
    print(f"[teams_base] Saved {len(df)} rows to: {out_path}")

    if args.sqlite:
        db_path = "app/data/databases/nba.db"
        upsert_teams_base_into_sqlite(df, db_path=db_path, table_name=args.sqlite_table)


if __name__ == "__main__":
    main()
