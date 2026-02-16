import requests
from bs4 import BeautifulSoup
import argparse
import json
import re
import sys
import logging
from typing import List, Dict, Optional, Any
from urllib.parse import urlparse
from pydantic import BaseModel, Field

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PositionalMatchup(BaseModel):
    position: str
    team_player_name: str
    team_player_url: Optional[str]
    opponent_player_name: str
    opponent_player_url: Optional[str]

class TeamMatchupStat(BaseModel):
    stat_name: str
    team_avg: float
    team_rank: int
    opponent_avg: float
    opponent_rank: int
    tips_to_watch_out_text: Optional[str]

class Meta(BaseModel):
    source_url: str
    vs_team_id: Optional[int] = None
    vs_team_slug: Optional[str] = None
    lineup_section: Optional[str] = None # e.g. "backcourt"

class MatchupsPayload(BaseModel):
    meta: Meta
    positional_matchups: List[PositionalMatchup]
    team_matchup_stats: List[TeamMatchupStat]

def clean_text(text: str) -> str:
    """Clean whitespace from text."""
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()

def parse_float(text: str) -> float:
    """Parse float from string, handling non-numeric gracefully."""
    try:
        # Remove any non-numeric chars except period and minus
        clean = re.sub(r'[^\d.-]', '', text)
        return float(clean)
    except ValueError:
        return 0.0

def parse_int(text: str) -> int:
    """Parse int from string."""
    try:
        clean = re.sub(r'[^\d-]', '', text)
        return int(clean)
    except ValueError:
        return 0

def fetch_html(url: str) -> str:
    """Fetch HTML content from URL."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logger.error(f"Error fetching URL {url}: {e}")
        sys.exit(1)

def derive_meta_from_url(url: str) -> Meta:
    """Derive metadata from the URL structure."""
    parsed = urlparse(url)
    path_parts = parsed.path.strip('/').split('/')
    
    lineup_section = "unknown"
    vs_team_id = None
    
    if len(path_parts) >= 6:
        if path_parts[4] in ["team", "backcourt", "frontcourt", "starters", "bench", "in-the-paint", "out-of-paint"]:
            lineup_section = path_parts[4]
            
    # Try to get vs_team_id from the last segment
    last_seg = path_parts[-1] # "1-24"
    if '-' in last_seg:
        try:
            parts = last_seg.split('-')
            if len(parts) >= 2:
                vs_team_id = int(parts[1])
        except ValueError:
            pass
            
def construct_url(team_slug: str, team_id: int, opponent_id: int, season_code: int = 25, dashboard: int = 1) -> str:
    """
    Construct the HoopsStats Matchups URL.
    Example: https://www.hoopsstats.com/basketball/fantasy/nba/oklahoma-city-thunder/team/matchups/25/21/1-24
    Note: Matchups URL uses season 25 for 2024-25 in example? 
    Wait, user example: .../matchups/25/21/1-24 (25=Last Season?).
    Let's check sync_db.py current season logic. It uses 2025-26.
    The example URL used 25/21/1-24. 25 might be 2024-25. 26 is 2025-26.
    We should use the requested season_code.
    """
    return f"https://www.hoopsstats.com/basketball/fantasy/nba/{team_slug}/team/matchups/{season_code}/{team_id}/{dashboard}-{opponent_id}"

def scrape_data(team_slug: str, team_id: int, opponent_id: int, season_code: int = 25) -> MatchupsPayload:
    """
    High-level function to scrape Matchups data.
    """
    url = construct_url(team_slug, team_id, opponent_id, season_code)
    html = fetch_html(url)
    
    # We need to construct meta first or parse it from URL.
    # scrape_data calls should ideally return fully populated payloads.
    # Our main() does detailed logic with soup.
    
    soup = BeautifulSoup(html, "html.parser")
    meta = derive_meta_from_url(url)
    
    # Logic from main() to refine meta
    headline = soup.select_one("table.headline")
    if headline:
        text = clean_text(headline.get_text())
        if "Matchup :" in text and " vs " in text:
            teams_part = text.split("Matchup :")[1]
            parts = teams_part.split(" vs ")
            if len(parts) == 2:
                meta.vs_team_slug = parts[1].strip().lower().replace(" ", "-")
                
    positional_matchups = parse_positional_matchups(soup)
    team_matchup_stats = parse_team_matchup_stats(soup)
    
    return MatchupsPayload(
        meta=meta,
        positional_matchups=positional_matchups,
        team_matchup_stats=team_matchup_stats
    )

def parse_positional_matchups(soup: BeautifulSoup) -> List[PositionalMatchup]:
    results = []
    rows = soup.select("tr[id^=kmyid]")
    
    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
            
        label_text = clean_text(tds[0].get_text())
        position = label_text.replace(" Matchup :", "").strip()
        
        anchors = tds[1].find_all("a")
        
        team_player_name = "Unknown"
        opp_player_name = "Unknown"
        team_player_url = None
        opp_player_url = None
        
        if len(anchors) == 2:
            team_player_name = clean_text(anchors[0].get_text())
            team_player_url = anchors[0].get('href')
            
            opp_player_name = clean_text(anchors[1].get_text())
            opp_player_url = anchors[1].get('href')
            
        elif len(anchors) == 1:
            full_text = clean_text(anchors[0].get_text())
            if " vs " in full_text:
                parts = full_text.split(" vs ")
                if len(parts) == 2:
                    team_player_name = parts[0].strip()
                    opp_player_name = parts[1].strip()
            
        else:
            full_text = clean_text(tds[1].get_text())
            if " vs " in full_text:
                parts = full_text.split(" vs ")
                if len(parts) == 2:
                    team_player_name = parts[0].strip()
                    opp_player_name = parts[1].strip()

        results.append(PositionalMatchup(
            position=position,
            team_player_name=team_player_name,
            team_player_url=team_player_url,
            opponent_player_name=opp_player_name,
            opponent_player_url=opp_player_url
        ))
        
    return results

def parse_team_matchup_stats(soup: BeautifulSoup) -> List[TeamMatchupStat]:
    results = []
    rows = soup.select("tr[id^=hmyid]")
    
    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
            
        team_avg = parse_float(tds[0].get_text())
        team_rank = parse_int(tds[1].get_text())
        stat_name = clean_text(tds[2].get_text())
        opp_rank = parse_int(tds[3].get_text())
        opp_avg = parse_float(tds[4].get_text())
        
        tips_text = clean_text(tds[5].get_text())
        if tips_text == "-" or not tips_text:
            tips_text = None
            
        results.append(TeamMatchupStat(
            stat_name=stat_name,
            team_avg=team_avg,
            team_rank=team_rank,
            opponent_avg=opp_avg,
            opponent_rank=opp_rank,
            tips_to_watch_out_text=tips_text
        ))
        
    return results

def main():
    parser = argparse.ArgumentParser(description="Scrape HoopsStats Matchups Endpoint")
    parser.add_argument("--url", required=True, help="Target URL to scrape")
    parser.add_argument("--output", help="Output JSON file path (optional)")
    parser.add_argument("--pretty", action="store_true", help="Pretty print JSON")
    
    args = parser.parse_args()
    
    html = fetch_html(args.url)
    soup = BeautifulSoup(html, "html.parser")
    
    meta = derive_meta_from_url(args.url)
    
    headline = soup.select_one("table.headline")
    if headline:
        text = clean_text(headline.get_text())
        if "Matchup :" in text and " vs " in text:
            teams_part = text.split("Matchup :")[1]
            parts = teams_part.split(" vs ")
            if len(parts) == 2:
                meta.vs_team_slug = parts[1].strip().lower().replace(" ", "-")
    
    positional_matchups = parse_positional_matchups(soup)
    team_matchup_stats = parse_team_matchup_stats(soup)
    
    payload = MatchupsPayload(
        meta=meta,
        positional_matchups=positional_matchups,
        team_matchup_stats=team_matchup_stats
    )
    
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(payload.model_dump_json(indent=2))
        logger.info(f"Output written to {args.output}")
    else:
        print(payload.model_dump_json(indent=2 if args.pretty else None))

if __name__ == "__main__":
    main()
