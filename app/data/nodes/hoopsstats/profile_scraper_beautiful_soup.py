#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
profile_scraper_beautiful_soup.py
---------------------------------

Deterministic scraper for HoopsStats "Team Profile" pages using requests + BeautifulSoup.

Input URL pattern:
    https://www.hoopsstats.com/basketball/fantasy/nba/{team_slug}/team/profile/{season}/{team_1}
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mappings
# ---------------------------------------------------------------------------

TEAM_SLUG_TO_ABBR: Dict[str, str] = {
    "atlanta-hawks": "ATL",
    "boston-celtics": "BOS",
    "brooklyn-nets": "BKN",
    "charlotte-hornets": "CHA",
    "chicago-bulls": "CHI",
    "cleveland-cavaliers": "CLE",
    "dallas-mavericks": "DAL",
    "denver-nuggets": "DEN",
    "detroit-pistons": "DET",
    "golden-state-warriors": "GSW",
    "houston-rockets": "HOU",
    "indiana-pacers": "IND",
    "la-clippers": "LAC",
    "los-angeles-clippers": "LAC",
    "los-angeles-lakers": "LAL",
    "memphis-grizzlies": "MEM",
    "miami-heat": "MIA",
    "milwaukee-bucks": "MIL",
    "minnesota-timberwolves": "MIN",
    "new-orleans-pelicans": "NOP",
    "new-york-knicks": "NYK",
    "oklahoma-city-thunder": "OKC",
    "orlando-magic": "ORL",
    "philadelphia-76ers": "PHI",
    "phoenix-suns": "PHX",
    "portland-trail-blazers": "POR",
    "sacramento-kings": "SAC",
    "san-antonio-spurs": "SAS",
    "toronto-raptors": "TOR",
    "utah-jazz": "UTA",
    "washington-wizards": "WAS",
}

SEASON_CODE_TO_LABEL: Dict[int, str] = {
    26: "2025-2026",
    25: "2024-2025",
}

# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class StatRow(BaseModel):
    row_type: str  # "team" | "opponents"
    row_label: str
    games: int
    wins: int
    minutes: float
    points: float
    rebounds: float
    assists: float
    steals: float
    blocks: float
    turnovers: float
    personal_fouls: float
    def_reb: float
    off_reb: float
    fgm_a: str
    fg_pct: float
    tpm_a: str
    tp_pct: float
    ftm_a: str
    ft_pct: float
    eff: float

class MatchupRow(BaseModel):
    date_label: str
    game_label: str
    home_away: str
    opponent_name: str
    result: str
    score_label: str
    team_points: int
    opponent_points: int
    team_eff: int
    opponent_eff: int
    eff_diff: int

class Meta(BaseModel):
    team_slug: Optional[str]
    season_code: Optional[int]
    team_id: Optional[int]
    view_type: str = "team_profile"
    team_full_name: Optional[str] = None
    season_label: Optional[str] = None

class SeasonMatchupSummary(BaseModel):
    won_matchups: int
    lost_matchups: int
    record_all: Dict[str, int]

class ProfilePayload(BaseModel):
    meta: Meta
    team_stats_averages: List[StatRow]
    team_stats_totals: List[StatRow]
    season_matchup_summary: SeasonMatchupSummary
    won_matchups: List[MatchupRow]
    lost_matchups: List[MatchupRow]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_text(text: str) -> str:
    return text.replace("\xa0", " ").strip()

def parse_float(val: str) -> float:
    try:
        return float(val.strip())
    except ValueError:
        return 0.0

def parse_int(val: str) -> int:
    try:
        if val.startswith("+"): val = val[1:]
        return int(val.strip())
    except ValueError:
        return 0

def fetch_html(url: str) -> str:
    logger.info("Fetching: %s", url)
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.text

def derive_meta_from_url(url: str) -> Meta:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    
    team_slug = None
    season_code = None
    team_id = None
    team_full_name = None
    season_label = None

    try:
        if "nba" in parts and "profile" in parts:
            nba_idx = parts.index("nba")
            team_slug = parts[nba_idx + 1]
            profile_idx = parts.index("profile")
            season_code = int(parts[profile_idx + 1])
            team_id = int(parts[profile_idx + 2])
            
            team_full_name = TEAM_SLUG_TO_ABBR.get(team_slug)
            season_label = SEASON_CODE_TO_LABEL.get(season_code, str(season_code))
    except Exception as e:
        logger.warning(f"Meta parse error: {e}")
        
    return Meta(
        team_slug=team_slug,
        season_code=season_code,
        team_id=team_id,
        team_full_name=team_full_name,
        season_label=season_label
    )

# ---------------------------------------------------------------------------
# Parsing Logic
# ---------------------------------------------------------------------------

def parse_stats_table_content(table_rows: List[Tag]) -> List[StatRow]:
    results = []
    
    for tr in table_rows:
        tds = tr.find_all("td")
        if not tds: continue
        if len(tds) < 19: continue
        
        row_label = clean_text(tds[0].get_text())
        row_type = "opponents" if "Opponents" in row_label else "team"
        
        data = StatRow(
            row_type=row_type,
            row_label=row_label,
            games=parse_int(tds[1].get_text()),
            wins=parse_int(tds[2].get_text()),
            minutes=parse_float(tds[3].get_text()),
            points=parse_float(tds[4].get_text()),
            rebounds=parse_float(tds[5].get_text()),
            assists=parse_float(tds[6].get_text()),
            steals=parse_float(tds[7].get_text()),
            blocks=parse_float(tds[8].get_text()),
            turnovers=parse_float(tds[9].get_text()),
            personal_fouls=parse_float(tds[10].get_text()),
            def_reb=parse_float(tds[11].get_text()),
            off_reb=parse_float(tds[12].get_text()),
            fgm_a=clean_text(tds[13].get_text()),
            fg_pct=parse_float(tds[14].get_text()),
            tpm_a=clean_text(tds[15].get_text()),
            tp_pct=parse_float(tds[16].get_text()),
            ftm_a=clean_text(tds[17].get_text()),
            ft_pct=parse_float(tds[18].get_text()),
            eff=parse_float(tds[19].get_text())
        )
        results.append(data)
    return results

def parse_matchups_content(table_rows: List[Tag]) -> List[MatchupRow]:
    results = []
    for tr in table_rows:
        tds = tr.find_all("td")
        if len(tds) < 7: continue
        
        date_label = clean_text(tds[0].get_text())
        game_label = clean_text(tds[1].get_text())
        result = clean_text(tds[2].get_text())
        score_label = clean_text(tds[3].get_text())
        team_eff = parse_int(tds[4].get_text())
        opp_eff = parse_int(tds[5].get_text())
        eff_diff = parse_int(tds[6].get_text())
        
        if game_label.startswith("at "):
            home_away = "away"
            opp_name_raw = game_label[3:]
        elif game_label.startswith("vs "):
            home_away = "home"
            opp_name_raw = game_label[3:]
        else:
            home_away = "unknown"
            opp_name_raw = game_label
            
        opp_name = opp_name_raw.replace("_", " ")

        pts = score_label.split("-")
        if len(pts) == 2:
            t_pts = int(pts[0])
            o_pts = int(pts[1])
        else:
            t_pts = 0
            o_pts = 0

        row = MatchupRow(
            date_label=date_label,
            game_label=game_label,
            home_away=home_away,
            opponent_name=opp_name,
            result=result,
            score_label=score_label,
            team_points=t_pts,
            opponent_points=o_pts,
            team_eff=team_eff,
            opponent_eff=opp_eff,
            eff_diff=eff_diff
        )
        results.append(row)
    return results

def construct_url(team_slug: str, team_id: int, season_code: int = 26) -> str:
    """
    Construct the HoopsStats Team Profile URL.
    Example: https://www.hoopsstats.com/basketball/fantasy/nba/oklahoma-city-thunder/team/profile/26/21
    """
    return f"https://www.hoopsstats.com/basketball/fantasy/nba/{team_slug}/team/profile/{season_code}/{team_id}"

def scrape_data(team_slug: str, team_id: int, season_code: int = 26) -> ProfilePayload:
    """
    High-level function to scrape Team Profile data.
    """
    url = construct_url(team_slug, team_id, season_code)
    html = fetch_html(url)
    meta = derive_meta_from_url(url)
    return process_html(html, meta)

def process_html(html: str, meta: Meta) -> ProfilePayload:
    soup = BeautifulSoup(html, "html.parser")
    
    averages_data = []
    totals_data = []
    
    all_rows = soup.find_all("tr")
    
    stats_rows = []
    won_rows = []
    lost_rows = []
    
    for tr in all_rows:
        rid = tr.get("id", "")
        if rid.startswith("myid"):
            tds = tr.find_all("td")
            if len(tds) >= 19:
                stats_rows.append(tr)
        elif rid.startswith("wmyid"):
            won_rows.append(tr)
        elif rid.startswith("lmyid"):
            lost_rows.append(tr)
            
    # Assuming first 2 stat rows are Averages, next 2 are Totals
    if len(stats_rows) >= 4:
        averages_data = parse_stats_table_content(stats_rows[:2])
        totals_data = parse_stats_table_content(stats_rows[2:4])
    elif len(stats_rows) >= 2:
        averages_data = parse_stats_table_content(stats_rows[:2])
    
    won_matchups = parse_matchups_content(won_rows)
    lost_matchups = parse_matchups_content(lost_rows)
    
    season_matchup_summary = SeasonMatchupSummary(
        won_matchups=len(won_matchups),
        lost_matchups=len(lost_matchups),
        record_all={
            "wins": len(won_matchups),
            "losses": len(lost_matchups)
        }
    )
    
    return ProfilePayload(
        meta=meta,
        team_stats_averages=averages_data,
        team_stats_totals=totals_data,
        season_matchup_summary=season_matchup_summary,
        won_matchups=won_matchups,
        lost_matchups=lost_matchups
    )

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.INFO)
    
    try:
        html = fetch_html(args.url)
        meta = derive_meta_from_url(args.url)
        payload = process_html(html, meta)
        
        print(payload.model_dump_json(indent=2 if args.pretty else None))
            
    except Exception as e:
        logger.error(f"Failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
