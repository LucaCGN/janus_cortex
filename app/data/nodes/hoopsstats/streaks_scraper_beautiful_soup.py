#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
streaks_scraper_beautiful_soup.py
---------------------------------

Deterministic scraper for HoopsStats "Team Streaks" pages using requests + BeautifulSoup.

Input URL pattern (example):

    https://www.hoopsstats.com/basketball/fantasy/nba/{team_slug}/team/streaks/{season_code}/{team_id}/{dashboard}

Output JSON shape (Pydantic validated):

{
  "meta": { ... },
  "team_streaks": [ ... ]
}
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static mappings
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
}

NICKNAME_OVERRIDES: Dict[str, str] = {
    "philadelphia-76ers": "76ers",
    "portland-trail-blazers": "Blazers",
}

# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class TeamStreak(BaseModel):
    length: int
    category: str
    metric_text: Optional[str]
    raw_text: str

class Meta(BaseModel):
    team_slug: Optional[str]
    season_code: Optional[int]
    team_id: Optional[int]
    dashboard: Optional[int]
    view_type: str = "team"
    team_full_name: Optional[str]
    team_nickname: Optional[str]
    season_label: Optional[str]

class StreaksPayload(BaseModel):
    meta: Meta
    team_streaks: List[TeamStreak]

# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def fetch_html(url: str) -> str:
    logger.info("Fetching HoopsStats streaks page: %s", url)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; JanusCortexScraper/1.0; +https://github.com/lnoni)"
    }
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.text

def parse_team_streaks(soup: BeautifulSoup) -> List[TeamStreak]:
    """
    Parse the "Team Streaks" statscontent table.
    """
    streaks: List[TeamStreak] = []
    
    rows = soup.select("tr[id^=pyid]")
    
    for tr in rows:
        text = tr.get_text(" ", strip=True)
        if not text:
            continue
            
        match = re.search(r"^(\d+)\s+(.*?)(?:\s+with\s+(.*))?$", text)
        if not match:
            logger.debug("Row text did not match strict regex: %s", text)
            continue
            
        length = int(match.group(1))
        middle = match.group(2).strip()
        tail = match.group(3)
        
        if tail:
            category = middle
            metric_text = tail.strip()
        else:
            category = middle
            metric_text = None
            
        streaks.append(TeamStreak(
            length=length,
            category=category,
            metric_text=metric_text,
            raw_text=text
        ))
        
    return streaks

def derive_meta_from_url(url: str) -> Meta:
    """
    Derive meta fields from URL.
    """
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]

    team_slug: Optional[str] = None
    season_code: Optional[int] = None
    team_id: Optional[int] = None
    dashboard: Optional[int] = None

    try:
        if "nba" in parts:
            nba_idx = parts.index("nba")
            team_slug = parts[nba_idx + 1]

        if "streaks" in parts:
            streaks_idx = parts.index("streaks")
            season_code = int(parts[streaks_idx + 1])
            team_id = int(parts[streaks_idx + 2])
            dashboard = int(parts[streaks_idx + 3])
    except Exception as exc:
        logger.warning("Could not parse meta from URL '%s': %s", url, exc)

    season_label = SEASON_CODE_TO_LABEL.get(season_code) if season_code is not None else None
    team_abbr = TEAM_SLUG_TO_ABBR.get(team_slug) if team_slug else None

    if team_slug in NICKNAME_OVERRIDES:
        team_nickname = NICKNAME_OVERRIDES[team_slug]
    elif team_slug:
        last = team_slug.split("-")[-1]
        team_nickname = last.title() if last.lower() != "76ers" else "76ers"
    else:
        team_nickname = None

    return Meta(
        team_slug=team_slug,
        season_code=season_code,
        team_id=team_id,
        dashboard=dashboard,
        view_type="team",
        team_full_name=team_abbr,
        team_nickname=team_nickname,
        season_label=season_label,
    )

def construct_url(team_slug: str, team_id: int, season_code: int = 26, dashboard: int = 1) -> str:
    """
    Construct the HoopsStats Team Streaks URL.
    Example: https://www.hoopsstats.com/basketball/fantasy/nba/oklahoma-city-thunder/team/streaks/26/21/1
    """
    return f"https://www.hoopsstats.com/basketball/fantasy/nba/{team_slug}/team/streaks/{season_code}/{team_id}/{dashboard}"

def scrape_data(team_slug: str, team_id: int, season_code: int = 26) -> StreaksPayload:
    """
    High-level function to scrape Team Streaks data.
    """
    url = construct_url(team_slug, team_id, season_code)
    html = fetch_html(url)
    return build_result_from_html(url, html)

def build_result_from_html(url: str, html: str) -> StreaksPayload:
    soup = BeautifulSoup(html, "html.parser")
    meta = derive_meta_from_url(url)
    streaks = parse_team_streaks(soup)

    return StreaksPayload(
        meta=meta,
        team_streaks=streaks,
    )

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deterministic HoopsStats 'Team Streaks' scraper using BeautifulSoup."
    )
    parser.add_argument("--url", required=True, help="HoopsStats 'Team Streaks' URL")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("Starting BeautifulSoup streaks scraper")
    html = fetch_html(args.url)
    payload = build_result_from_html(args.url, html)

    print(payload.model_dump_json(indent=2 if args.pretty else None))

if __name__ == "__main__":
    main()
