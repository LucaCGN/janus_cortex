#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
live_stats.py
-------------

Node NBA — Live Boxscore (team + players) para um jogo específico.
Versão atualizada para usar `nba_api.live.nba.endpoints` (CDN) em vez de `stats.nba.com`.

Funções principais:

- resolve_game_id(input_str) -> str
    Resolve um input (slug ou game_id) para um GAME_ID oficial.

- LiveStatsRequest (Pydantic)
    Model de entrada com game_id.

- fetch_live_team_stats_df(request) -> pd.DataFrame
    Boxscore por time.

- fetch_live_player_boxscore_df(request) -> pd.DataFrame
    Boxscore por jogador.

- fetch_game_summary_df(request) -> pd.DataFrame
    Resumo do jogo (Line Score).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional, Set, Tuple, List, Dict, Any

import pandas as pd
from dateutil import parser
# Novos endpoints LIVE (mais estáveis/rápidos que stats.nba.com)
from nba_api.live.nba.endpoints import boxscore, scoreboard
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_polymarket_nba_slug(slug: str) -> Tuple[str, str, str]:
    """
    Converte slug 'nba-hou-dal-2025-12-06' em:
        (AWAY_TRICODE, HOME_TRICODE, YYYY-MM-DD)
    """
    parts = slug.split("-")
    if len(parts) < 5:
        raise ValueError(f"Slug inválido: {slug!r}")

    prefix = parts[0]
    if prefix.lower() != "nba":
        raise ValueError(f"Slug não é NBA: {slug!r}")

    away = parts[1].upper()
    home = parts[2].upper()
    year, month, day = parts[-3:]

    date_str = f"{year}-{month}-{day}"
    return away, home, date_str


def resolve_game_id(input_str: str) -> str:
    """
    Resolve um input para GAME_ID oficial da NBA.

    Aceita:
    - GAME_ID numérico (ex: '0022500363') -> retorna direto.
    - Slug Polymarket (ex: 'nba-hou-dal-2025-12-06') -> resolve via scoreboard (live).
    """
    # 1. Se for numérico (ex: '0022500363'), assume que já é GAME_ID
    if input_str.isdigit() and len(input_str) > 5:
        logger.info("[live_stats][resolve_game_id] Input parece ser GAME_ID: %s", input_str)
        return input_str

    # 2. Tenta tratar como slug
    slug = input_str
    try:
        away, home, iso_date = parse_polymarket_nba_slug(slug)
    except ValueError:
        raise ValueError(f"Input inválido (não é GAME_ID nem slug conhecido): {input_str!r}")

    # O endpoint live scoreboard não aceita data como parâmetro direto na inicialização da mesma forma
    # Ele retorna o dia de "hoje" por padrão, mas podemos tentar navegar se a lib suportar.
    # A lib nba_api.live.nba.endpoints.scoreboard.ScoreBoard() não aceita data.
    # Ela pega o "Today's Score Board".
    # SE o jogo for hoje, vai funcionar. Se for passado, pode falhar.
    # Para jogos passados, teríamos que usar o stats endpoint (que está bloqueado) ou outra fonte.
    # ASSUMINDO que o uso principal é LIVE (jogos de hoje).
    
    logger.info("[live_stats][resolve_game_id] Buscando jogo de HOJE para %s vs %s", away, home)

    try:
        sb = scoreboard.ScoreBoard()
        games = sb.games.get_dict()
    except Exception as e:
        logger.error("[live_stats] Erro ao buscar ScoreBoard: %s", e)
        raise ValueError(f"Erro ao buscar ScoreBoard live: {e}")

    target_set: Set[str] = {away, home}

    for game in games:
        # Estrutura: game['awayTeam']['teamTricode'], game['homeTeam']['teamTricode']
        g_away = game.get('awayTeam', {}).get('teamTricode', '')
        g_home = game.get('homeTeam', {}).get('teamTricode', '')
        
        current_teams = {g_away, g_home}
        
        if current_teams == target_set:
            game_id = game['gameId']
            logger.info(
                "[live_stats][resolve_game_id] Match encontrado: GAME_ID=%s, teams=%s",
                game_id,
                current_teams,
            )
            return str(game_id)

    # Fallback: Se não achou no board de hoje, talvez a data do slug não seja hoje.
    # Como não temos acesso fácil ao histórico via live endpoints, lançamos erro.
    raise ValueError(
        f"Nenhum jogo encontrado no ScoreBoard de HOJE para slug={slug!r} "
        f"(away={away}, home={home}). Verifique se a data do slug corresponde a hoje."
    )


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------


class LiveStatsRequest(BaseModel):
    """Parâmetros para buscar boxscore live/tradicional."""

    game_id: str = Field(..., description="GAME_ID oficial da NBA, ex: '0022500315'.")
    include_players: bool = Field(
        True,
        description="Se True, também retorna DataFrame com boxscore de jogadores.",
    )
    as_of: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp aproximado da coleta (UTC).",
    )


@dataclass
class LiveTeamStats:
    """Linha de estatísticas agregadas por time em um boxscore."""

    game_id: str
    team_id: int
    team_abbreviation: str
    team_city: str
    team_name: str
    
    # Stats podem vir como int ou float
    minutes: float # Adicionado para compatibilidade
    points: float
    rebounds: float # total
    assists: float
    steals: float
    blocks: float
    turnovers: float
    fgm: float
    fga: float
    fg3m: float
    fg3a: float
    ftm: float
    fta: float
    plus_minus: float # calculado ou do boxscore


@dataclass
class LivePlayerBoxScore:
    """Linha de boxscore por jogador."""

    game_id: str
    player_id: int
    player_name: str
    team_id: int
    team_abbreviation: str
    minutes: str # Vem como string "PT32M33.00S" ou similar
    points: float
    rebounds: float
    assists: float
    steals: float
    blocks: float
    turnovers: float
    fgm: float
    fga: float
    fg3m: float
    fg3a: float
    ftm: float
    fta: float
    plus_minus: float


# ---------------------------------------------------------------------------
# Core fetchers
# ---------------------------------------------------------------------------

def _parse_team_stats(game_id: str, team_data: Dict[str, Any]) -> Optional[LiveTeamStats]:
    """Helper para converter dict do live endpoint para dataclass LiveTeamStats."""
    if not team_data:
        return None
    
    stats = team_data.get('statistics', {})
    
    return LiveTeamStats(
        game_id=game_id,
        team_id=int(team_data.get('teamId', 0)),
        team_abbreviation=str(team_data.get('teamTricode', '')),
        team_city=str(team_data.get('teamCity', '')),
        team_name=str(team_data.get('teamName', '')),
        points=float(stats.get('points', 0)),
        rebounds=float(stats.get('reboundsTotal', 0)),
        assists=float(stats.get('assists', 0)),
        steals=float(stats.get('steals', 0)),
        blocks=float(stats.get('blocks', 0)),
        turnovers=float(stats.get('turnovers', 0)),
        fgm=float(stats.get('fieldGoalsMade', 0)),
        fga=float(stats.get('fieldGoalsAttempted', 0)),
        fg3m=float(stats.get('threePointersMade', 0)),
        fg3a=float(stats.get('threePointersAttempted', 0)),
        ftm=float(stats.get('freeThrowsMade', 0)),
        fta=float(stats.get('freeThrowsAttempted', 0)),
        plus_minus=float(stats.get('plusMinusPoints', 0)),
        minutes=0.0 # Team minutes usually 240, not explicitly in this stats block often
    )

def fetch_live_team_stats_df(request: LiveStatsRequest) -> pd.DataFrame:
    """
    Busca boxscore (team level) via nba_api.live.nba.endpoints.boxscore.
    """
    logger.info("[live_stats][fetch_live_team_stats_df] game_id=%s", request.game_id)
    try:
        box = boxscore.BoxScore(request.game_id)
        data = box.game.get_dict() # dict com 'homeTeam', 'awayTeam'
    except Exception as e:
        logger.error("[live_stats] Erro ao buscar BoxScore: %s", e)
        return pd.DataFrame()

    rows = []
    
    # Home Team
    home_stats = _parse_team_stats(request.game_id, data.get('homeTeam', {}))
    if home_stats:
        rows.append(asdict(home_stats))
        
    # Away Team
    away_stats = _parse_team_stats(request.game_id, data.get('awayTeam', {}))
    if away_stats:
        rows.append(asdict(away_stats))
        
    return pd.DataFrame(rows)


def fetch_live_player_boxscore_df(request: LiveStatsRequest) -> pd.DataFrame:
    """
    Busca boxscore (player level) via nba_api.live.nba.endpoints.boxscore.
    """
    logger.info("[live_stats][fetch_live_player_boxscore_df] game_id=%s", request.game_id)
    try:
        box = boxscore.BoxScore(request.game_id)
        data = box.game.get_dict()
    except Exception as e:
        logger.error("[live_stats] Erro ao buscar BoxScore: %s", e)
        return pd.DataFrame()

    rows = []
    
    # Processa Home e Away
    for side in ['homeTeam', 'awayTeam']:
        team_data = data.get(side, {})
        team_id = int(team_data.get('teamId', 0))
        team_abbr = str(team_data.get('teamTricode', ''))
        
        players = team_data.get('players', [])
        for p in players:
            # Só processa quem jogou ou está ativo?
            # O endpoint retorna todos. Vamos pegar stats.
            stats = p.get('statistics', {})
            
            # Se não jogou, stats pode estar zerado ou incompleto.
            # O campo 'played' = '1' indica que entrou em quadra.
            
            row = LivePlayerBoxScore(
                game_id=request.game_id,
                player_id=int(p.get('personId', 0)),
                player_name=str(p.get('name', 'Unknown')),
                team_id=team_id,
                team_abbreviation=team_abbr,
                minutes=str(stats.get('minutes', 'PT00M00.00S')),
                points=float(stats.get('points', 0)),
                rebounds=float(stats.get('reboundsTotal', 0)),
                assists=float(stats.get('assists', 0)),
                steals=float(stats.get('steals', 0)),
                blocks=float(stats.get('blocks', 0)),
                turnovers=float(stats.get('turnovers', 0)),
                fgm=float(stats.get('fieldGoalsMade', 0)),
                fga=float(stats.get('fieldGoalsAttempted', 0)),
                fg3m=float(stats.get('threePointersMade', 0)),
                fg3a=float(stats.get('threePointersAttempted', 0)),
                ftm=float(stats.get('freeThrowsMade', 0)),
                fta=float(stats.get('freeThrowsAttempted', 0)),
                plus_minus=float(stats.get('plusMinusPoints', 0)),
            )
            rows.append(asdict(row))

    return pd.DataFrame(rows)


def fetch_game_summary_df(request: LiveStatsRequest) -> pd.DataFrame:
    """
    Busca resumo do jogo (Line Score) via nba_api.live.nba.endpoints.boxscore.
    Retorna DataFrame com LineScore (pontos por quarto).
    """
    logger.info("[live_stats][fetch_game_summary_df] game_id=%s", request.game_id)
    try:
        box = boxscore.BoxScore(request.game_id)
        data = box.game.get_dict()
    except Exception as e:
        logger.error("[live_stats] Erro ao buscar BoxScore para summary: %s", e)
        return pd.DataFrame()

    # Monta um DF simples com 2 linhas (Home/Away) e colunas de periods
    rows = []
    
    for side in ['homeTeam', 'awayTeam']:
        team_data = data.get(side, {})
        periods = team_data.get('periods', [])
        
        row = {
            "GAME_ID": request.game_id,
            "TEAM_ID": team_data.get('teamId'),
            "TEAM_ABBREVIATION": team_data.get('teamTricode'),
            "TEAM_NAME": team_data.get('teamName'),
            "PTS_TOTAL": team_data.get('score')
        }
        
        # Adiciona colunas dinâmicas por período
        for p in periods:
            p_num = p.get('period')
            p_score = p.get('score')
            row[f"PTS_QTR{p_num}"] = p_score
            
        rows.append(row)
        
    return pd.DataFrame(rows)


def fetch_live_scoreboard(game_id: str) -> Dict[str, Any]:
    """
    Fetches basic scoreboard info (Clock, Period, Scores) for a specific game.
    """
    try:
        box = boxscore.BoxScore(game_id)
        data = box.game.get_dict()
        return {
            "game_id": game_id,
            "game_status": data.get('gameStatus'),
            "game_status_text": data.get('gameStatusText'),
            "period": data.get('period'),
            "game_clock": data.get('gameClock'),
            "home_score": data.get('homeTeam', {}).get('score'),
            "visitor_score": data.get('awayTeam', {}).get('score'),
            "home_tri": data.get('homeTeam', {}).get('teamTricode'),
            "visitor_tri": data.get('awayTeam', {}).get('teamTricode'),
            "home_team_slug": data.get('homeTeam', {}).get('teamName'), # Fallback to Name if no detailed slug
            "away_team_slug": data.get('awayTeam', {}).get('teamName'),
        }
    except Exception as e:
        logger.error(f"Error fetching live scoreboard: {e}")
        return {}

def fetch_todays_scoreboard() -> List[Dict[str, Any]]:
    """
    Fetches the ScoreBoard for ALL games happening today.
    Returns reference list for updating DB statuses.
    
    [UPDATED] Handles Date Rollover: If local time < 06:00, also fetches YESTERDAY's games
    using `scoreboardv2` (stats endpoint) because `live.ScoreBoard` defaults to 'today'.
    """
    results = []
    
    # 1. Fetch "Today" (Standard Live Endpoint)
    try:
        sb = scoreboard.ScoreBoard()
        games = sb.games.get_dict()
        for g in games:
             results.append({
                 "game_id": g.get('gameId'),
                 "game_status": g.get('gameStatus'),
                 "game_status_text": g.get('gameStatusText'),
                 "period": g.get('period'),
                 "clock": g.get('gameClock'),
                 "home_team": g.get('homeTeam', {}).get('teamName'),
                 "away_team": g.get('awayTeam', {}).get('teamName'),
                 "home_score": g.get('homeTeam', {}).get('score'),
                 "away_score": g.get('awayTeam', {}).get('score'),
             })
    except Exception as e:
        logger.error(f"Error fetching today's live scoreboard: {e}")

    # 2. Date Rollover Check (< 6 AM) -> Fetch Yesterday via Stats Endpoint
    # Used to catch late night games when server date has rolled over
    if datetime.now().hour < 6:
        try:
            from nba_api.stats.endpoints import scoreboardv2
            from datetime import timedelta
            
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            logger.info(f"[live_stats] Early morning detected. Fetching stats for yesterday: {yesterday}")
            
            board = scoreboardv2.ScoreboardV2(game_date=yesterday)
            # GameHeader contains IDs and Status
            # LineScore contains Scores
            headers = board.game_header.get_dict()['data']
            linescores = board.line_score.get_dict()['data']
            
            # Map Stats data to our dict format
            # Stats API returns list of lists, need to map indices
            # Or use get_normalized_dict() if available, but get_dict() is safer
            
            # Helper to find index in Headers
            h_cols = board.game_header.get_dict()['headers']
            idx_gid = h_cols.index('GAME_ID')
            idx_stat = h_cols.index('GAME_STATUS_ID') # 3=Final?
            idx_stat_txt = h_cols.index('GAME_STATUS_TEXT')
            idx_home_id = h_cols.index('HOME_TEAM_ID')
            idx_away_id = h_cols.index('VISITOR_TEAM_ID')
            
            # Helper for scores (LineScore has 2 rows per game)
            l_cols = board.line_score.get_dict()['headers']
            l_idx_gid = l_cols.index('GAME_ID')
            l_idx_pts = l_cols.index('PTS')
            l_idx_team = l_cols.index('TEAM_ID')
            
            scores_map = {} # GameID -> {HomePts, AwayPts}
            for row in linescores:
                gid = row[l_idx_gid]
                tid = row[l_idx_team]
                pts = row[l_idx_pts]
                if gid not in scores_map: scores_map[gid] = {}
                scores_map[gid][tid] = pts # Temp store by TeamID

            for row in headers:
                gid = row[idx_gid]
                
                # Deduplicate: If we already have this GameID from Live, skip
                if any(x['game_id'] == gid for x in results):
                    continue
                
                # Get Scores
                hid = row[idx_home_id]
                aid = row[idx_away_id]
                h_pts = scores_map.get(gid, {}).get(hid, 0)
                a_pts = scores_map.get(gid, {}).get(aid, 0)

                results.append({
                    "game_id": gid,
                    "game_status": int(row[idx_stat]),
                    "game_status_text": row[idx_stat_txt],
                    "period": 0, # Stats endpoint doesn't give live period easily in Header
                    "clock": "", 
                    "home_team": "", # Names not in Header (only IDs), can be looked up if needed or left empty
                    "away_team": "",
                    "home_score": h_pts,
                    "away_score": a_pts,
                    "source": "stats_v2" # Debug marker
                })
                
        except Exception as e:
            logger.error(f"Error fetching yesterday's stats scoreboard: {e}")

    return results
