#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Node: NBA team quarter splits (Q1..Q4) via LeagueDashTeamStats(Base, PerGame, period=1..4).

Usos:

1) Pipeline diária (cron/job)
   - Rodar via CLI para gerar CSV por temporada
   - Opcionalmente fazer upsert em SQLite (nba.db)

2) Integração com CrewAI / function calling
   - Usar TeamQuarterSplitsRequest / TeamQuarterSplitsStats (Pydantic)
   - fetch_quarter_splits_models é a entrypoint "tool-friendly"

3) Integração com FastAPI
   - Body: TeamQuarterSplitsRequest
   - Retorno: List[TeamQuarterSplitsStats]

Campos principais:

    Identificação:
        - team_id, team_name, team_slug, season, last_update

    Splits por quarto:
        - Q1_PTS, Q1_PLUS_MINUS
        - Q2_PTS, Q2_PLUS_MINUS
        - Q3_PTS, Q3_PLUS_MINUS
        - Q4_PTS, Q4_PLUS_MINUS
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


class TeamQuarterSplitsRequest(BaseModel):
    """
    Input genérico para splits por quarto.

    Exemplos:
        - Todos os times:
            {"season": "2024-25"}

        - Apenas alguns times por ID:
            {"season": "2024-25", "team_ids": [1610612748, 1610612742]}

        - Apenas alguns times por sigla:
            {"season": "2024-25", "team_slugs": ["MIA", "DAL"]}
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
    sleep_seconds: float = Field(
        default=0.5,
        ge=0.0,
        description="Delay entre chamadas ao endpoint por período (para respeitar rate limits).",
    )


class TeamQuarterSplitsStats(BaseModel):
    """
    Snapshot de splits por quarto (Q1..Q4) para um time em uma temporada.
    """

    team_id: int
    team_name: str
    team_slug: Optional[str]
    season: str
    last_update: datetime

    Q1_PTS: Optional[float] = None
    Q1_PLUS_MINUS: Optional[float] = None
    Q2_PTS: Optional[float] = None
    Q2_PLUS_MINUS: Optional[float] = None
    Q3_PTS: Optional[float] = None
    Q3_PLUS_MINUS: Optional[float] = None
    Q4_PTS: Optional[float] = None
    Q4_PLUS_MINUS: Optional[float] = None


TeamQuarterSplitsRequest.model_rebuild()
TeamQuarterSplitsStats.model_rebuild()


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


# -----------------------------------------------------------------------------
# Core fetch + transformação em DataFrame
# -----------------------------------------------------------------------------


def fetch_quarter_splits_df(
    season: str,
    team_ids: Optional[List[int]] = None,
    team_slugs: Optional[List[str]] = None,
    sleep_seconds: float = 0.5,
) -> pd.DataFrame:
    """
    Node: splits por quarto (Q1..Q4) via LeagueDashTeamStats(Base, PerGame, period=1..4).

    Retorna DataFrame já normalizado na forma de TeamQuarterSplitsStats.
    """
    import time

    slug_map = _team_slug_map()
    quarter_frames: List[pd.DataFrame] = []

    ids_filter_set = {int(x) for x in team_ids} if team_ids else None

    for period in (1, 2, 3, 4):
        print(
            f"[teams_quarters] Fetching LeagueDashTeamStats(Base, period={period}) "
            f"for season={season} ..."
        )
        try:
            resp = leaguedashteamstats.LeagueDashTeamStats(
                season=season,
                season_type_all_star="Regular Season",
                per_mode_detailed="PerGame",
                measure_type_detailed_defense="Base",
                period=period,
            )
            raw = resp.get_data_frames()[0]
        except Exception as e:
            print(f"[teams_quarters] Error fetching period={period}: {e}")
            continue

        if raw is None or raw.empty:
            print(f"[teams_quarters] Empty frame for period={period}")
            continue

        required_cols = ["TEAM_ID", "TEAM_NAME", "PTS", "PLUS_MINUS"]
        missing = [c for c in required_cols if c not in raw.columns]
        if missing:
            print(
                f"[teams_quarters] Period {period} missing cols {missing}; "
                "skipping this period."
            )
            continue

        df = raw.copy()
        df["TEAM_ID"] = pd.to_numeric(df["TEAM_ID"], errors="coerce")
        df.dropna(subset=["TEAM_ID"], inplace=True)
        df["TEAM_ID"] = df["TEAM_ID"].astype(int)

        # Filtro por TEAM_ID (se fornecido)
        if ids_filter_set:
            df = df[df["TEAM_ID"].isin(ids_filter_set)]

        if df.empty:
            print(f"[teams_quarters] No data for period={period} after filters.")
            continue

        df = df.drop_duplicates(subset=["TEAM_ID"])

        df = df[["TEAM_ID", "TEAM_NAME", "PTS", "PLUS_MINUS"]].copy()
        df.rename(
            columns={
                "PTS": f"Q{period}_PTS",
                "PLUS_MINUS": f"Q{period}_PLUS_MINUS",
            },
            inplace=True,
        )

        quarter_frames.append(df)

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    if not quarter_frames:
        print("[teams_quarters] No quarter data frames collected; returning empty DataFrame.")
        return pd.DataFrame(
            columns=[
                "team_id",
                "team_name",
                "team_slug",
                "season",
                "last_update",
                "Q1_PTS",
                "Q1_PLUS_MINUS",
                "Q2_PTS",
                "Q2_PLUS_MINUS",
                "Q3_PTS",
                "Q3_PLUS_MINUS",
                "Q4_PTS",
                "Q4_PLUS_MINUS",
            ]
        )

    merged = quarter_frames[0]
    for qdf in quarter_frames[1:]:
        merged = pd.merge(
            merged,
            qdf.drop(columns=["TEAM_NAME"]),
            on="TEAM_ID",
            how="outer",
        )

    ts = datetime.now(timezone.utc).isoformat()

    merged["team_id"] = merged["TEAM_ID"].astype(int)
    merged["team_name"] = merged["TEAM_NAME"].astype(str)
    merged["team_slug"] = merged["team_id"].map(slug_map)
    merged["season"] = season
    merged["last_update"] = ts

    # Garante colunas Q1..Q4 mesmo se algum período falhou
    for period in (1, 2, 3, 4):
        for suffix in ("PTS", "PLUS_MINUS"):
            col = f"Q{period}_{suffix}"
            if col not in merged.columns:
                merged[col] = pd.NA

    cols = [
        "team_id",
        "team_name",
        "team_slug",
        "season",
        "last_update",
        "Q1_PTS",
        "Q1_PLUS_MINUS",
        "Q2_PTS",
        "Q2_PLUS_MINUS",
        "Q3_PTS",
        "Q3_PLUS_MINUS",
        "Q4_PTS",
        "Q4_PLUS_MINUS",
    ]
    merged = merged[cols].sort_values("team_id").reset_index(drop=True)

    # Filtro por team_slugs no DF normalizado
    if team_slugs:
        slugs = {s.upper() for s in team_slugs}
        merged = merged[merged["team_slug"].astype(str).str.upper().isin(slugs)].reset_index(
            drop=True
        )

    return merged


# Compatibilidade com versão antiga (assinatura antiga)
def fetch_quarter_splits(season: str, sleep_seconds: float = 0.5) -> pd.DataFrame:
    """
    Wrapper legacy: mantém compatibilidade com código antigo.

    Equivalente a:
        fetch_quarter_splits_df(season=season, sleep_seconds=sleep_seconds)
    """
    return fetch_quarter_splits_df(season=season, sleep_seconds=sleep_seconds)


# -----------------------------------------------------------------------------
# Pydantic wrapper (CrewAI / FastAPI)
# -----------------------------------------------------------------------------


def fetch_quarter_splits_models(req: TeamQuarterSplitsRequest) -> List[TeamQuarterSplitsStats]:
    df = fetch_quarter_splits_df(
        season=req.season,
        team_ids=req.team_ids,
        team_slugs=req.team_slugs,
        sleep_seconds=req.sleep_seconds,
    )
    if df.empty:
        return []

    records = df.where(pd.notnull(df), None).to_dict(orient="records")
    return [TeamQuarterSplitsStats(**row) for row in records]


# -----------------------------------------------------------------------------
# SQLite upsert
# -----------------------------------------------------------------------------


def upsert_teams_quarters_into_sqlite(
    df: pd.DataFrame,
    db_path: str = "app/data/databases/nba.db",
    table_name: str = "nba_teams_quarter_splits",
) -> None:
    """
    Upsert em SQLite usando (team_id, season) como chave primária.
    Converte datetime/pd.Timestamp para string ISO.
    """
    import sqlite3

    if df.empty:
        print("[teams_quarters] Nothing to upsert into SQLite (empty DataFrame).")
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
        print(
            f"[teams_quarters] Upserted {len(values)} rows into {db_path} "
            f"(table={table_name})."
        )
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Node: NBA team quarter splits (PTS & PLUS_MINUS por quarto) "
            "usando LeagueDashTeamStats(Base, period=1..4)."
        )
    )
    parser.add_argument(
        "--season",
        type=str,
        default="2024-25",
        help="Season string, e.g. '2024-25'",
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
        "--sleep-seconds",
        type=float,
        default=0.5,
        help="Delay entre períodos para respeitar rate limits (default: 0.5s).",
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
        help="Se setado, faz também upsert em app/data/databases/nba.db.",
    )
    parser.add_argument(
        "--sqlite-table",
        type=str,
        default="nba_teams_quarter_splits",
        help="Nome da tabela SQLite quando --sqlite é usado.",
    )

    args = parser.parse_args()

    season = args.season
    out_dir = Path(args.out_dir) if args.out_dir else Path(__file__).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)

    df = fetch_quarter_splits_df(
        season=season,
        team_ids=args.team_id,
        team_slugs=args.team_slug,
        sleep_seconds=args.sleep_seconds,
    )

    out_path = out_dir / f"nba_teams_quarter_splits_{season.replace('-', '')}.csv"

    if df.empty:
        print("[teams_quarters] No rows to save (empty DataFrame).")
    else:
        df.to_csv(out_path, index=False)
        print(f"[teams_quarters] Saved {len(df)} rows to: {out_path}")

    if args.sqlite and not df.empty:
        db_path = "app/data/databases/nba.db"
        upsert_teams_quarters_into_sqlite(df, db_path=db_path, table_name=args.sqlite_table)


if __name__ == "__main__":
    main()
