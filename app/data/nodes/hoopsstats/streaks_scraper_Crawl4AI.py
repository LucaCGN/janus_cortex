#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
streaks_scraper_Crawl4AI.py
---------------------------

LLM-powered scraper for HoopsStats "Team Streaks" endpoint.

Output JSON shape:
{
  "meta": {...},            # Deterministically via URL + HTML
  "team_streaks": [...]     # Extracted via LLM (schema)
}

Environment requirements:
- OPENAI_API_KEY -> used with 'openai/gpt-4o-mini'

Usage:
    python app/data/nodes/hoopsstats/streaks_scraper_Crawl4AI.py \
        --url "..." \
        --pretty
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, ValidationError

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    LLMConfig,
    LLMExtractionStrategy,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / Config
# ---------------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
}

TEAM_ABBREVIATIONS: Dict[str, str] = {
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

# Instruction for LLMExtractionStrategy
LLM_INSTRUCTION = """
You are given the Markdown content of a HoopsStats 'Team Streaks' page.

Extract the list of Team Streaks. Each entry appears as a line like:
"18 consecutive games with higher field-goals percentage"
"15 consecutive wins"

For each streak found:
1. length: The integer number at the start.
2. category: The phrase describing the type of streak (e.g., "consecutive games" or "consecutive wins"). If it says "consecutive games with ...", just put "consecutive games".
3. metric_text: The specific condition if present (e.g. "higher field-goals percentage"). If it is just "consecutive wins", this should be null.
4. raw_text: The full string of the streak.

Return a JSON object with exactly one top-level key: "team_streaks", containing the list.
"""

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TeamStreak(BaseModel):
    length: int
    category: str
    metric_text: Optional[str]
    raw_text: str

class TeamStreaksPayload(BaseModel):
    team_streaks: List[TeamStreak]

# ---------------------------------------------------------------------------
# Meta Utilities
# ---------------------------------------------------------------------------

def fetch_html(url: str, timeout: int = 15) -> str:
    logger.info("Fetching HoopsStats streaks page (for meta): %s", url)
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.text

def parse_url_meta(url: str) -> Dict[str, Any]:
    parts = url.rstrip("/").split("/")
    # .../nba/{slug}/team/streaks/{season}/{id}/{dash}
    try:
        streaks_idx = parts.index("streaks")
        team_slug = parts[streaks_idx - 2]
        season_code = int(parts[streaks_idx + 1])
        team_id = int(parts[streaks_idx + 2])
        dashboard = int(parts[streaks_idx + 3])
    except (ValueError, IndexError):
        try:
             # Fallback
             nba_idx = parts.index("nba")
             team_slug = parts[nba_idx+1]
             # nba(5), slug(6), team(7), streaks(8), seas(9), id(10), dash(11)
             season_code = int(parts[nba_idx+4])
             team_id = int(parts[nba_idx+5])
             dashboard = int(parts[nba_idx+6])
        except:
             raise ValueError(f"Unexpected URL structure: {url}")

    return {
        "team_slug": team_slug,
        "season_code": season_code,
        "team_id": team_id,
        "dashboard": dashboard,
        "view_type": "team",
    }

def slug_to_abbreviation(team_slug: str) -> Optional[str]:
    return TEAM_ABBREVIATIONS.get(team_slug)

def slug_to_nickname(team_slug: str) -> Optional[str]:
    parts = team_slug.split("-")
    if not parts: return None
    return parts[-1].replace("76ers", "76ers").title()

def extract_meta(url: str, html: str) -> Dict[str, Any]:
    base_meta = parse_url_meta(url)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    
    season_label = None
    m = re.search(r"(\d{4}-\d{4})", text)
    if m: season_label = m.group(1)

    team_slug = base_meta["team_slug"]
    team_abbrev = slug_to_abbreviation(team_slug)
    team_nickname = slug_to_nickname(team_slug)
    
    return {
        **base_meta,
        "team_full_name": team_abbrev,
        "team_nickname": team_nickname,
        "season_label": season_label,
    }

# ---------------------------------------------------------------------------
# LLM Execution
# ---------------------------------------------------------------------------

async def run_llm_extraction(url: str, llm_config: LLMConfig) -> Optional[TeamStreaksPayload]:
    browser_config = BrowserConfig(verbose=False, headless=True)

    extraction_strategy = LLMExtractionStrategy(
        llm_config=llm_config,
        schema=TeamStreaksPayload.model_json_schema(),
        extraction_type="schema",
        instruction=LLM_INSTRUCTION,
    )

    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        word_count_threshold=5,
        extraction_strategy=extraction_strategy,
        css_selector=".statscontent",
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=url, config=run_config)

    raw_content = result.extracted_content
    if not raw_content:
        logger.error("LLM extraction returned empty")
        return None

    try:
        raw = json.loads(raw_content)
    except:
        logger.exception("Failed to parse LLM JSON")
        return None
        
    if isinstance(raw, list):
         chosen = None
         for item in raw:
             if isinstance(item, dict) and "team_streaks" in item:
                 chosen = item
                 break
         if chosen:
             raw = chosen
         elif raw and isinstance(raw[0], dict) and "length" in raw[0]:
             raw = {"team_streaks": raw} # list of streaks direct
         else:
             return None

    try:
        payload = TeamStreaksPayload.model_validate(raw)
    except ValidationError as e:
        logger.error("Validation error: %s", e)
        return None

    return payload

async def build_result_llm(url: str) -> Dict[str, Any]:
    api_token = os.getenv("OPENAI_API_KEY")
    if not api_token:
        raise RuntimeError("OPENAI_API_KEY missing.")

    html = fetch_html(url)
    meta = extract_meta(url, html)

    llm_config = LLMConfig(provider="openai/gpt-4o-mini", api_token=api_token)
    
    logger.info("Running Crawl4AI LLM extraction (Streaks)...")
    payload = await run_llm_extraction(url, llm_config)
    if not payload:
        raise RuntimeError("LLM extraction failed.")
        
    return {
        "meta": meta,
        "team_streaks": [s.model_dump() for s in payload.team_streaks]
    }

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="HoopsStats Streaks scraper (Crawl4AI)")
    parser.add_argument("--url", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Crawl4AI streaks scraper")
    
    try:
        data = asyncio.run(build_result_llm(args.url))
    except Exception as e:
        logger.error("Failed: %s", e)
        raise
        
    if args.pretty:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(data, separators=(",", ":"), ensure_ascii=False))

if __name__ == "__main__":
    main()
