#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tips_scraper_Crawl4AI.py
------------------------

LLM-powered scraper for HoopsStats "Team Tips" endpoint,
using Crawl4AI + LLMExtractionStrategy as intelligent fallback.

Output JSON shape:
{
  "meta": {...},            # Deterministically via URL + HTML
  "team_tips": [...]        # Extracted via LLM (schema)
}

Environment requirements:
- OPENAI_API_KEY -> used with 'openai/gpt-4o-mini'

Usage:
    python app/data/nodes/hoopsstats/tips_scraper_Crawl4AI.py \
        --url "https://www.hoopsstats.com/basketball/fantasy/nba/oklahoma-city-thunder/team/tips/26/21/1" \
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
You are given the Markdown content of a HoopsStats 'Team Tips' page.

Extract the list of Team Tips. Each tip usually appears as a sentence like:
"When Team has higher efficiency recap, Oklahoma City Thunder are 23 - 0"

For each tip found, extract:
1. condition_prefix: The full text starting with "When Team " and ending before strict team name match if possible, or just repeat the full clause e.g. "When Team has higher efficiency recap".
2. metric_text: The specific metric description within that prefix, e.g. "higher efficiency recap".
3. team_full_name: The team name mentioned in the sentence (e.g. "Oklahoma City Thunder").
4. wins: The number of wins (first number after "are").
5. losses: The number of losses (second number after "are").

Return a JSON object with exactly one top-level key: "team_tips", containing the list of tip objects.
Ensure the output matches the JSON schema provided.
"""

# ---------------------------------------------------------------------------
# Pydantic models for LLM payload validation
# ---------------------------------------------------------------------------

class TeamTip(BaseModel):
    condition_prefix: str
    metric_text: str
    team_full_name: str
    wins: int
    losses: int

class TeamTipsPayload(BaseModel):
    team_tips: List[TeamTip]

# ---------------------------------------------------------------------------
# Functions for meta (deterministic)
# ---------------------------------------------------------------------------

def fetch_html(url: str, timeout: int = 15) -> str:
    logger.info("Fetching HoopsStats tips page (for meta enrichment): %s", url)
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.text

def parse_url_meta(url: str) -> Dict[str, Any]:
    """
    Expected URL: .../nba/{team_slug}/team/tips/{season_code}/{team_id}/{dashboard}
    """
    parts = url.rstrip("/").split("/")
    # Basic validation of path length
    # https: / / www.hoopsstats.com / basketball / fantasy / nba / {slug} / team / tips / {season} / {id} / {dash}
    # 0      1   2                    3            4         5     6        7      8      9          10     11
    if len(parts) < 12:
         # Be lenient if there are extra segments or fewer, but try to find 'tips'
         pass

    try:
        tips_idx = parts.index("tips")
        team_slug = parts[tips_idx - 2] # typically 2 steps back: .../{slug}/team/tips
        season_code = int(parts[tips_idx + 1])
        team_id = int(parts[tips_idx + 2])
        dashboard = int(parts[tips_idx + 3])
    except (ValueError, IndexError):
        # Fallback simplistic parsing if 'tips' keyword not found or position differs
        # This mirrors the logic in quarters scraper which looks for 'quarters' or 'nba'
        try:
            nba_idx = parts.index("nba")
            team_slug = parts[nba_idx+1]
            season_code = int(parts[nba_idx+4]) # /team/tips/{season} -> +1+1+1 = +3? No.
            # nba/{slug}/team/tips/{season}/{id}/{dash}
            # nba=5, slug=6, team=7, tips=8, season=9, id=10, dash=11
            season_code = int(parts[nba_idx+4])
            team_id = int(parts[nba_idx+5])
            dashboard = int(parts[nba_idx+6])
        except:
             raise ValueError(f"Unexpected URL structure for HoopsStats tips: {url}")

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
    if not parts:
        return None
    return parts[-1].replace("76ers", "76ers").title()

def extract_meta(url: str, html: str) -> Dict[str, Any]:
    base_meta = parse_url_meta(url)

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    season_label = None
    m = re.search(r"(\d{4}-\d{4})", text)
    if m:
        season_label = m.group(1)

    team_slug = base_meta["team_slug"]
    team_abbrev = slug_to_abbreviation(team_slug)
    team_nickname = slug_to_nickname(team_slug)

    team_full_name = team_abbrev

    meta: Dict[str, Any] = {
        **base_meta,
        "team_full_name": team_full_name,
        "team_nickname": team_nickname,
        "season_label": season_label,
    }
    return meta

# ---------------------------------------------------------------------------
# Execute Crawl4AI + LLMExtractionStrategy
# ---------------------------------------------------------------------------

async def run_llm_extraction(url: str, llm_config: LLMConfig) -> Optional[TeamTipsPayload]:
    browser_config = BrowserConfig(verbose=False, headless=True)

    extraction_strategy = LLMExtractionStrategy(
        llm_config=llm_config,
        schema=TeamTipsPayload.model_json_schema(),
        extraction_type="schema",
        instruction=LLM_INSTRUCTION,
    )

    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        word_count_threshold=10,
        extraction_strategy=extraction_strategy,
        css_selector=".statscontent", # Focus on the stats tables
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=url, config=run_config)

    raw_content = result.extracted_content
    if not raw_content:
        logger.error("LLM extraction returned empty content")
        return None

    try:
        raw = json.loads(raw_content)
    except Exception:
        logger.exception("Failed to parse LLM extracted JSON")
        return None

    if isinstance(raw, list):
        # Unwrap list if necessary
        chosen = None
        for item in raw:
            if isinstance(item, dict) and "team_tips" in item:
                chosen = item
                break
        if chosen is None:
             # If just a list of tips?
             # Check if list content looks like tips
             if raw and isinstance(raw[0], dict) and "wins" in raw[0]:
                 raw = {"team_tips": raw}
             else:
                 logger.error("List returned but no valid 'team_tips' wrapper found.")
                 return None
        else:
            raw = chosen

    if not isinstance(raw, dict):
        logger.error("LLM payload is not a dict: %s", type(raw))
        return None

    try:
        payload = TeamTipsPayload.model_validate(raw)
    except ValidationError as e:
        logger.error("Validation error for LLM payload: %s", e)
        return None

    return payload

async def build_result_llm(url: str) -> Dict[str, Any]:
    api_token = os.getenv("OPENAI_API_KEY")
    if not api_token:
        raise RuntimeError("OPENAI_API_KEY not defined; needed for Crawl4AI LLM.")

    html = fetch_html(url)
    meta = extract_meta(url, html)

    llm_config = LLMConfig(
        provider="openai/gpt-4o-mini",
        api_token=api_token,
    )

    logger.info("Running Crawl4AI LLM extraction (Team Tips)...")
    payload = await run_llm_extraction(url, llm_config)
    if payload is None:
        raise RuntimeError("LLM extraction failed to assemble TeamTipsPayload.")

    return {
        "meta": meta,
        "team_tips": [tip.model_dump() for tip in payload.team_tips],
    }

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="HoopsStats Team Tips scraper (Crawl4AI + LLM)",
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Full URL of HoopsStats 'Team Tips' endpoint",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output",
    )
    args = parser.parse_args()

    logger.info("Starting Crawl4AI tips scraper")

    try:
        data = asyncio.run(build_result_llm(args.url))
    except Exception as exc:
        logger.error("Crawl4AI scraper failed: %s", exc)
        raise

    if args.pretty:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(data, separators=(",", ":"), ensure_ascii=False))

if __name__ == "__main__":
    main()
