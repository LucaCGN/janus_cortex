#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
player_quarter_splits_byperiod.py
---------------------------------
Node: NBA player quarter splits (Q1..Q4) usando LeagueDashPlayerStats(Base, period=N).

- Monta um DataFrame com métricas por período (PTS e PLUS_MINUS por jogo).
- Robusto a trocas/time_id ausente entre períodos (merge outer), usando dtype Int64 (nullable).
- Fornece modelos Pydantic e função de upsert em SQLite para testes.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Sequence, Dict

import pandas as pd
import numpy as np

# A importação do nba_api fica aqui; o arquivo é "drop-in" e deve rodar no seu ambiente.
from nba_api.stats.endpoints import leaguedashplayerstats  # type: ignore

# ============================
# Helpers de normalização
# ============================

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _coalesce(*vals):
    for v in vals:
        if pd.notna(v):
            return v
    return pd.NA

# ============================
# Fetch por período (Q1..Q4)
# ============================

def _fetch_period_df(season: str, period: int, season_type: str = "Regular Season") -> pd.DataFrame:
    """
    Busca estatísticas base por PERÍODO (1..4) e retorna colunas padronizadas:
    [player_nba_id, player_name, team_id, team_slug, Q{period}_PTS, Q{period}_PLUS_MINUS]
    """
    resp = leaguedashplayerstats.LeagueDashPlayerStats(
        season=season,
        per_mode_detailed="PerGame",
        measure_type_detailed_defense="Base",
        period=period,
        season_type_all_star=season_type,
        timeout=30
    )
    raw = resp.get_data_frames()[0]

    df = raw[[
        "PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "TEAM_ABBREVIATION", "PTS", "PLUS_MINUS"
    ]].copy()

    df = df.rename(columns={
        "PLAYER_ID": "player_nba_id",
        "PLAYER_NAME": "player_name",
        "TEAM_ID": "team_id",
        "TEAM_ABBREVIATION": "team_slug",
        "PTS": f"Q{period}_PTS",
        "PLUS_MINUS": f"Q{period}_PLUS_MINUS",
    })
    # dtypes tolerantes: team_id pode faltar em algum período
    df["player_nba_id"] = pd.to_numeric(df["player_nba_id"], errors="coerce").astype("Int64")
    df["team_id"] = pd.to_numeric(df["team_id"], errors="coerce").astype("Int64")
    df["player_name"] = df["player_name"].astype(str)

    return df

# ============================
# Público: DataFrame combinado
# ============================

def fetch_player_quarter_splits_df(
    season: str,
    team_slugs: Sequence[str] | None = None,
    season_type: str = "Regular Season",
) -> pd.DataFrame:
    """
    Retorna um DataFrame com colunas:
        ['player_nba_id','player_name','team_id','team_slug','season','last_update',
         'Q1_PTS','Q1_PLUS_MINUS','Q2_PTS','Q2_PLUS_MINUS','Q3_PTS','Q3_PLUS_MINUS','Q4_PTS','Q4_PLUS_MINUS']
    Filtra por `team_slugs` se fornecido.
    """
    # Coleta Q1..Q4
    qdfs: Dict[int, pd.DataFrame] = {}
    for p in (1, 2, 3, 4):
        qdfs[p] = _fetch_period_df(season=season, period=p, season_type=season_type)
        # Para evitar colisões em merge, renomeia colunas de id/slug por período
        qdfs[p] = qdfs[p].rename(columns={"team_id": f"team_id_p{p}", "team_slug": f"team_slug_p{p}"})

    # Merge "outer" por player (id+name) para manter quem aparece só em alguns períodos
    out = qdfs[1].merge(qdfs[2], on=["player_nba_id", "player_name"], how="outer")\
                 .merge(qdfs[3], on=["player_nba_id", "player_name"], how="outer")\
                 .merge(qdfs[4], on=["player_nba_id", "player_name"], how="outer")

    # Coalesce de team_id e team_slug (pega o primeiro não nulo em Q1..Q4)
    out["team_id"] = out.apply(lambda r: _coalesce(r.get("team_id_p1"), r.get("team_id_p2"),
                                                   r.get("team_id_p3"), r.get("team_id_p4")), axis=1).astype("Int64")
    out["team_slug"] = out.apply(lambda r: _coalesce(r.get("team_slug_p1"), r.get("team_slug_p2"),
                                                     r.get("team_slug_p3"), r.get("team_slug_p4")), axis=1)

    # Drop colunas de suporte
    out = out.drop(columns=[c for c in out.columns if c.startswith("team_id_p") or c.startswith("team_slug_p")])

    # Metadados
    out["season"] = season
    out["last_update"] = _utcnow_iso()

    # Filtro por times (se pedido)
    if team_slugs:
        want = {s.upper() for s in team_slugs}
        out = out[out["team_slug"].astype(str).str.upper().isin(want)]

    # Ordenação consistente
    out = out.sort_values(["team_slug", "player_name"], na_position="last", kind="mergesort").reset_index(drop=True)

    # Preenche 'team_slug' ausente como "UNK" para evitar crashes em consumidores (opcional)
    out["team_slug"] = out["team_slug"].fillna("UNK")

    return out

# ============================
# Pydantic models (v2)
# ============================

try:
    from pydantic import BaseModel, Field
except Exception:  # fallback mínimo se pydantic não estiver presente no ambiente
    class BaseModel:  # type: ignore
        pass
    def Field(default=None, **kwargs):  # type: ignore
        return default

class PlayerQuarterSplitsRequest(BaseModel):
    season: str = Field(..., description="Ex.: '2024-25'")
    team_slugs: list[str] | None = Field(default=None, description="Filtros por times (tri-codes).")
    season_type: str = Field(default="Regular Season")

class PlayerQuarterSplitsStats(BaseModel):
    player_nba_id: int | None = None
    player_name: str
    team_id: int | None = None
    team_slug: str | None = None
    season: str
    last_update: str
    Q1_PTS: float | None = None
    Q1_PLUS_MINUS: float | None = None
    Q2_PTS: float | None = None
    Q2_PLUS_MINUS: float | None = None
    Q3_PTS: float | None = None
    Q3_PLUS_MINUS: float | None = None
    Q4_PTS: float | None = None
    Q4_PLUS_MINUS: float | None = None

def fetch_player_quarter_splits_models(req: PlayerQuarterSplitsRequest) -> List[PlayerQuarterSplitsStats]:
    df = fetch_player_quarter_splits_df(
        season=req.season,
        team_slugs=req.team_slugs,
        season_type=req.season_type,
    )
    # Converte dtypes pandas -> tipos python padrão para Pydantic
    df = df.replace({pd.NA: None, np.nan: None})
    records = df.to_dict(orient="records")
    return [PlayerQuarterSplitsStats(**rec) for rec in records]

# ============================
# Upsert em SQLite
# ============================

def upsert_players_quarter_splits_into_sqlite(
    df: pd.DataFrame,
    db_path: str,
    table_name: str = "nba_players_quarter_splits",
) -> None:
    """
    Upsert simples por (player_nba_id, season). Cria tabela se não existir.
    """
    import sqlite3

    # Normaliza nulos para None (sqlite entende)
    clean = df.replace({pd.NA: None, np.nan: None}).copy()

    cols_metrics = [
        "Q1_PTS", "Q1_PLUS_MINUS", "Q2_PTS", "Q2_PLUS_MINUS",
        "Q3_PTS", "Q3_PLUS_MINUS", "Q4_PTS", "Q4_PLUS_MINUS",
    ]

    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                player_nba_id INTEGER,
                player_name TEXT,
                team_id INTEGER,
                team_slug TEXT,
                season TEXT NOT NULL,
                last_update TEXT,
                Q1_PTS REAL, Q1_PLUS_MINUS REAL,
                Q2_PTS REAL, Q2_PLUS_MINUS REAL,
                Q3_PTS REAL, Q3_PLUS_MINUS REAL,
                Q4_PTS REAL, Q4_PLUS_MINUS REAL,
                PRIMARY KEY (player_nba_id, season)
            )
        """)
        # UPSERT
        placeholders = ",".join("?" for _ in range(6 + len(cols_metrics)))
        assignments = ",".join([f"{c}=excluded.{c}" for c in ["player_name","team_id","team_slug","last_update"] + cols_metrics])
        sql = f"""
            INSERT INTO {table_name} (
                player_nba_id, player_name, team_id, team_slug, season, last_update,
                {", ".join(cols_metrics)}
            ) VALUES ({placeholders})
            ON CONFLICT(player_nba_id, season) DO UPDATE SET
                {assignments}
        """

        rows = []
        for _, r in clean.iterrows():
            rows.append([
                int(r["player_nba_id"]) if r["player_nba_id"] is not None else None,
                r["player_name"],
                int(r["team_id"]) if r["team_id"] is not None else None,
                r["team_slug"],
                r["season"],
                r["last_update"],
                *(r.get(c, None) for c in cols_metrics)
            ])
        cur.executemany(sql, rows)
        conn.commit()

# ============================
# CLI rápido para debug
# ============================

def _cli() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Quarter splits por período (Q1..Q4).")
    parser.add_argument("--season", default="2024-25")
    parser.add_argument("--team_slugs", default=None, help="Ex.: 'MIA,DAL'")
    args = parser.parse_args()

    filt = None
    if args.team_slugs:
        filt = [s.strip().upper() for s in args.team_slugs.split(",") if s.strip()]

    df = fetch_player_quarter_splits_df(season=args.season, team_slugs=filt)
    print(df.head(20).to_string(index=False))

if __name__ == "__main__":
    _cli()
