import requests
import pandas as pd
import logging
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any

logger = logging.getLogger(__name__)

CDN_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"

def fetch_season_schedule_df(season: str = "2025-26") -> pd.DataFrame:
    """
    Fetches the full season schedule from the NBA CDN.
    Returns a DataFrame with all games (past and future).
    """
    logger.info(f"Fetching season schedule from {CDN_URL}...")
    try:
        r = requests.get(CDN_URL, timeout=10)
        if r.status_code != 200:
            logger.error(f"Failed to fetch schedule: {r.status_code}")
            return pd.DataFrame()
        
        data = r.json()
        league_schedule = data.get('leagueSchedule', {})
        game_dates = league_schedule.get('gameDates', [])
        
        rows = []
        for gd in game_dates:
            game_date_str = gd.get('gameDate') # Format: "10/02/2025 00:00:00"
            # Parse date to YYYY-MM-DD
            try:
                dt = datetime.strptime(game_date_str, "%m/%d/%Y %H:%M:%S")
                date_iso = dt.strftime("%Y-%m-%d")
            except:
                date_iso = game_date_str
            
            games = gd.get('games', [])
            for game in games:
                rows.append({
                    'game_id': game.get('gameId'),
                    'game_date': date_iso,
                    'game_start_time': game.get('gameDateTimeUTC'), # New field
                    'game_status': game.get('gameStatus'), # 1=Scheduled, 2=Live, 3=Final
                    'game_status_text': game.get('gameStatusText'),
                    'home_team_id': game.get('homeTeam', {}).get('teamId'),
                    'home_team_slug': game.get('homeTeam', {}).get('teamTricode'), # Using Tricode as slug
                    'home_team_name': game.get('homeTeam', {}).get('teamName'),
                    'home_team_city': game.get('homeTeam', {}).get('teamCity'),
                    'home_score': game.get('homeTeam', {}).get('score'),
                    'away_team_id': game.get('awayTeam', {}).get('teamId'),
                    'away_team_slug': game.get('awayTeam', {}).get('teamTricode'),
                    'away_team_name': game.get('awayTeam', {}).get('teamName'),
                    'away_team_city': game.get('awayTeam', {}).get('teamCity'),
                    'away_score': game.get('awayTeam', {}).get('score'),
                    'updated_at': datetime.now().isoformat()
                })
        
        df = pd.DataFrame(rows)
        logger.info(f"Fetched {len(df)} games from schedule.")
        return df
        
    except Exception as e:
        logger.error(f"Error fetching schedule: {e}")
        return pd.DataFrame()

def parse_polymarket_slug(slug: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Parses a Polymarket slug (e.g., 'nba-hou-dal-2025-12-06')
    Returns (away_slug, home_slug, date_iso)
    """
    try:
        parts = slug.split("-")
        if len(parts) < 5 or parts[0].lower() != "nba":
            return None, None, None
        
        away = parts[1].upper()
        home = parts[2].upper()
        year, month, day = parts[-3:]
        date_iso = f"{year}-{month}-{day}"
        return away, home, date_iso
    except:
        return None, None, None

def match_polymarket_slug_to_game(slug: str, schedule_df: pd.DataFrame) -> Optional[str]:
    """
    Matches a Polymarket slug to an NBA Game ID using the schedule DataFrame.
    """
    away, home, date = parse_polymarket_slug(slug)
    if not away or not home or not date:
        return None
    
    # Filter by date
    # Note: Polymarket date might be slightly off due to timezone, but usually matches local date.
    # We might need to check adjacent days if strict match fails, but let's start with strict.
    
    day_games = schedule_df[schedule_df['game_date'] == date]
    if day_games.empty:
        # Try finding games where date is +/- 1 day? 
        # For now, strict match.
        return None
    
    # Filter by teams
    # Polymarket slugs use Tricodes usually (LAL, BOS, etc.)
    # But sometimes they might differ (e.g. PHX vs PHO?).
    # Let's assume standard Tricodes first.
    
    match = day_games[
        ((day_games['home_team_slug'] == home) & (day_games['away_team_slug'] == away)) |
        ((day_games['home_team_slug'] == away) & (day_games['away_team_slug'] == home)) # Should not happen but safety
    ]
    
    if not match.empty:
        return match.iloc[0]['game_id']
    
    return None
