#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tips_scraper_beautiful_soup.py
------------------------------

Deterministic scraper for HoopsStats "Team Tips" pages using requests + BeautifulSoup.

Input URL pattern (example):

    https://www.hoopsstats.com/basketball/fantasy/nba/{team_slug}/team/tips/{season_code}/{team_id}/{dashboard}

Output JSON shape (Pydantic validated):

{
  "meta": { ... },
  "team_tips": [ ... ]
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

class TeamTip(BaseModel):
    condition_prefix: str
    metric_text: str
    team_full_name: str
    wins: int
    losses: int

class Meta(BaseModel):
    team_slug: Optional[str]
    season_code: Optional[int]
    team_id: Optional[int]
    dashboard: Optional[int]
    view_type: str = "team"
    team_full_name: Optional[str]
    team_nickname: Optional[str]
    season_label: Optional[str]

class TipsPayload(BaseModel):
    meta: Meta
    team_tips: List[TeamTip]

# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def fetch_html(url: str) -> str:
    logger.info("Fetching HoopsStats tips page: %s", url)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; JanusCortexScraper/1.0; +https://github.com/lnoni)"
    }
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.text

def parse_team_tips(soup: BeautifulSoup) -> List[TeamTip]:
    """
    Parse the "Team Tips" statscontent table.
    """
    tips: List[TeamTip] = []
    
    rows = soup.select("tr[id^=pyid]")
    
    for tr in rows:
        text = tr.get_text(" ", strip=True)
        if not text:
            continue
            
        match = re.search(r"^When Team\s+(.*?),[\s\xa0]+(.*?)\s+are\s+(\d+)\s*[-\u2013]\s*(\d+)", text)
        if not match:
            logger.debug("Row text did not match strict regex: %s", text)
            continue
            
        metric_text = match.group(1).strip()
        team_full_name = match.group(2).strip()
        wins = int(match.group(3))
        losses = int(match.group(4))
        
        condition_prefix = f"When Team {metric_text}"
        
        tips.append(TeamTip(
            condition_prefix=condition_prefix,
            metric_text=metric_text,
            team_full_name=team_full_name,
            wins=wins,
            losses=losses
        ))
        
    return tips

def derive_meta_from_url(url: str) -> Meta:
    """
    Derive meta fields purely from the URL.
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

        if "tips" in parts:
            tips_idx = parts.index("tips")
            season_code = int(parts[tips_idx + 1])
            team_id = int(parts[tips_idx + 2])
            dashboard = int(parts[tips_idx + 3])
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
    Construct the HoopsStats Team Tips URL.
    Example: https://www.hoopsstats.com/basketball/fantasy/nba/oklahoma-city-thunder/team/tips/26/21/1
    """
    return f"https://www.hoopsstats.com/basketball/fantasy/nba/{team_slug}/team/tips/{season_code}/{team_id}/{dashboard}"

def scrape_data(team_slug: str, team_id: int, season_code: int = 26) -> TipsPayload:
    """
    High-level function to scrape Team Tips data.
    """
    url = construct_url(team_slug, team_id, season_code)
    html = fetch_html(url)
    return build_result_from_html(url, html)

def build_result_from_html(url: str, html: str) -> TipsPayload:
    soup = BeautifulSoup(html, "html.parser")
    
    meta = derive_meta_from_url(url)
    tips = parse_team_tips(soup)

    return TipsPayload(
        meta=meta,
        team_tips=tips,
    )

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deterministic HoopsStats 'Team Tips' scraper using BeautifulSoup."
    )
    parser.add_argument("--url", required=True, help="HoopsStats 'Team Tips' URL")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("Starting BeautifulSoup tips scraper")
    html = fetch_html(args.url)
    payload = build_result_from_html(args.url, html)

    # Output using model_dump_json
    print(payload.model_dump_json(indent=2 if args.pretty else None))

if __name__ == "__main__":
    main()
