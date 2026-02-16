#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Node: Team recent form (last 5 games) per season.

Endpoint:
    - nba_api.stats.endpoints.TeamGameLogs

Versão refatorada:

1) Usa UMA chamada para TeamGameLogs da temporada toda
   - Evita timeouts e problemas de rate limiting de chamar time a time.

2) Suporta:
   - Filtro por team_ids / team_slugs
   - Pydantic (TeamLast5Request / TeamLast5Stats)
   - Upsert em SQLite (nba_teams_last5)

Output CSV:
    nba_teams_last5_{season_nodash}.csv

Campos (pensando em NBA_TEAMS_TABLE):

    - team_id
    - team_name
    - team_slug
    - season
    - last_update
    - last_5_games_played
    - last_5_games_win_rate
    - last_5_avg_points
    - last_5_avg_turnover
    - last_5_avg_plus_minus (enriquecido)
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from nba_api.stats.endpoints import teamgamelogs
from nba_api.stats.static import teams as static_teams
from pydantic import BaseModel, Field


# -----------------------------------------------------------------------------
# Pydantic models
# -----------------------------------------------------------------------------


class TeamLast5Request(BaseModel):
    """
    Input genérico para métricas dos últimos 5 jogos.
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


class TeamLast5Stats(BaseModel):
    """
    Snapshot de performance recente (últimos 5 jogos).
    """

    team_id: int
    team_name: str
    team_slug: Optional[str]
    season: str
    last_update: datetime

    last_5_games_played: int
    last_5_games_win_rate: float
    last_5_avg_points: Optional[float] = None
    last_5_avg_turnover: Optional[float] = None
    last_5_avg_plus_minus: Optional[float] = None


TeamLast5Request.model_rebuild()
TeamLast5Stats.model_rebuild()


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _team_slug_map() -> Dict[int, str]:
    """Return mapping TEAM_ID -> TEAM_ABBREVIATION using static metadata."""
    mapping: Dict[int, str] = {}
    try:
        for t in static_teams.get_teams() or []:
            try:
                tid = int(t.get("id"))
            except (TypeError, ValueError):
                continue
            mapping[tid] = (t.get("abbreviation") or "").upper()
    except Exception:
        return mapping
    return mapping


# -----------------------------------------------------------------------------
# Core fetch + transformação em DataFrame
# -----------------------------------------------------------------------------


def compute_last5_metrics_df(
    season: str,
    team_ids: Optional[List[int]] = None,
    team_slugs: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Compute last-5 games metrics para todos (ou subset) dos times na temporada.

    Implementação:
      - Uma chamada a TeamGameLogs para a temporada toda
      - Agrupa por TEAM_ID e pega os 5 jogos mais recentes
      - Calcula win_rate, média de PTS, TOV e PLUS_MINUS
    """
    print(f"[teams_last5] Computing last-5 metrics for season={season} ...")

    logs = teamgamelogs.TeamGameLogs(
        season_nullable=season,
        season_type_nullable="Regular Season",
        league_id_nullable="00",
    )
    df_all = logs.get_data_frames()[0]

    if df_all is None or df_all.empty:
        print("[teams_last5] No game logs returned; DataFrame empty.")
        return pd.DataFrame(
            columns=[
                "team_id",
                "team_name",
                "team_slug",
                "season",
                "last_update",
                "last_5_games_played",
                "last_5_games_win_rate",
                "last_5_avg_points",
                "last_5_avg_turnover",
                "last_5_avg_plus_minus",
            ]
        )

    df = df_all.copy()

    # Normaliza TEAM_ID e GAME_DATE
    df["TEAM_ID"] = pd.to_numeric(df["TEAM_ID"], errors="coerce")
    df.dropna(subset=["TEAM_ID"], inplace=True)
    df["TEAM_ID"] = df["TEAM_ID"].astype(int)

    if "GAME_DATE" in df.columns:
        df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], errors="coerce")
        df = df.sort_values(["TEAM_ID", "GAME_DATE"], ascending=[True, False])

    ids_filter_set = {int(x) for x in team_ids} if team_ids else None

    slug_map = _team_slug_map()
    ts = datetime.now(timezone.utc).isoformat()

    out_rows: List[Dict] = []

    # Para filtros por sigla
    slugs_filter_set = {s.upper() for s in team_slugs} if team_slugs else None

    for team_id, grp in df.groupby("TEAM_ID"):
        if ids_filter_set and team_id not in ids_filter_set:
            continue

        # Descobre slug (static_teams primeiro, fallback pelo próprio logs)
        slug = slug_map.get(team_id)
        if slug is None and "TEAM_ABBREVIATION" in grp.columns:
            slug = str(grp["TEAM_ABBREVIATION"].iloc[0]).upper()

        if slugs_filter_set and (slug is None or slug.upper() not in slugs_filter_set):
            continue

        if grp.empty:
            continue

        team_name = str(grp["TEAM_NAME"].iloc[0])

        tail = grp.head(5).copy()
        n_games = len(tail)
        if n_games == 0:
            continue

        wins = (tail["WL"] == "W").sum() if "WL" in tail.columns else 0
        win_rate = wins / float(n_games)

        pts = pd.to_numeric(tail.get("PTS", pd.Series([], dtype=float)), errors="coerce")
        tov = pd.to_numeric(tail.get("TOV", pd.Series([], dtype=float)), errors="coerce")
        plusm = pd.to_numeric(
            tail.get("PLUS_MINUS", pd.Series([], dtype=float)),
            errors="coerce",
        )

        out_rows.append(
            {
                "team_id": int(team_id),
                "team_name": team_name,
                "team_slug": slug,
                "season": season,
                "last_update": ts,
                "last_5_games_played": int(n_games),
                "last_5_games_win_rate": float(round(win_rate, 3)),
                "last_5_avg_points": float(round(pts.mean(), 2)) if not pts.empty else None,
                "last_5_avg_turnover": float(round(tov.mean(), 2)) if not tov.empty else None,
                "last_5_avg_plus_minus": float(round(plusm.mean(), 2))
                if not plusm.empty
                else None,
            }
        )

    if not out_rows:
        print("[teams_last5] WARNING - no metrics computed (maybe filters too restrictive).")
        return pd.DataFrame(
            columns=[
                "team_id",
                "team_name",
                "team_slug",
                "season",
                "last_update",
                "last_5_games_played",
                "last_5_games_win_rate",
                "last_5_avg_points",
                "last_5_avg_turnover",
                "last_5_avg_plus_minus",
            ]
        )

    out_df = pd.DataFrame(out_rows).sort_values("team_id").reset_index(drop=True)
    return out_df


# Compat: função antiga
def compute_last5_metrics(season: str) -> pd.DataFrame:
    """
    Wrapper legacy para compatibilidade.

    Equivalente a:
        compute_last5_metrics_df(season=season)
    """
    return compute_last5_metrics_df(season=season)


# -----------------------------------------------------------------------------
# Pydantic wrapper
# -----------------------------------------------------------------------------


def compute_last5_metrics_models(req: TeamLast5Request) -> List[TeamLast5Stats]:
    df = compute_last5_metrics_df(
        season=req.season,
        team_ids=req.team_ids,
        team_slugs=req.team_slugs,
    )
    if df.empty:
        return []

    records = df.where(pd.notnull(df), None).to_dict(orient="records")
    return [TeamLast5Stats(**row) for row in records]


# -----------------------------------------------------------------------------
# SQLite upsert
# -----------------------------------------------------------------------------


def upsert_teams_last5_into_sqlite(
    df: pd.DataFrame,
    db_path: str = "app/data/databases/nba.db",
    table_name: str = "nba_teams_last5",
) -> None:
    """
    Upsert em SQLite usando (team_id, season) como chave primária.
    Converte datetime/pd.Timestamp para string ISO.
    """
    import sqlite3

    if df.empty:
        print("[teams_last5] Nothing to upsert into SQLite (empty DataFrame).")
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
        print(f"[teams_last5] Upserted {len(values)} rows into {db_path} (table={table_name}).")
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Node: compute last-5 games metrics (win rate, PTS, TOV, PLUS_MINUS) para times."
    )
    parser.add_argument(
        "--season",
        default="2024-25",
        help="Season in 'YYYY-YY' format.",
    )
    parser.add_argument(
        "--team-id",
        type=int,
        action="append",
        help="Optional TEAM_ID filter (pode ser passado múltiplas vezes).",
    )
    parser.add_argument(
        "--team-slug",
        type=str,
        action="append",
        help="Optional TEAM_ABBREVIATION filter (ex.: --team-slug MIA --team-slug DAL).",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Optional output directory. Default: this script's directory.",
    )
    parser.add_argument(
        "--sqlite",
        action="store_true",
        help="Se setado, também faz upsert em app/data/databases/nba.db.",
    )
    parser.add_argument(
        "--sqlite-table",
        type=str,
        default="nba_teams_last5",
        help="Nome da tabela SQLite quando --sqlite é usado.",
    )
    args = parser.parse_args()

    season = args.season
    out_dir = Path(args.out_dir) if args.out_dir else Path(__file__).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)

    df = compute_last5_metrics_df(
        season=season,
        team_ids=args.team_id,
        team_slugs=args.team_slug,
    )

    fname = f"nba_teams_last5_{season.replace('-', '')}.csv"
    out_path = out_dir / fname
    df.to_csv(out_path, index=False)

    print(f"[teams_last5] Saved {len(df)} rows to: {out_path}")

    if args.sqlite and not df.empty:
        db_path = "app/data/databases/nba.db"
        upsert_teams_last5_into_sqlite(df, db_path=db_path, table_name=args.sqlite_table)


if __name__ == "__main__":
    main()
