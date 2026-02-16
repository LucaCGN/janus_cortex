#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
profile_scraper_Crawl4AI.py
---------------------------

LLM-powered scraper for HoopsStats "Team Profile" page.

Environment requirements:
- OPENAI_API_KEY
- crawl4ai

"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, ValidationError

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    LLMConfig,
    LLMExtractionStrategy,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class StatRow(BaseModel):
    row_type: str = Field(..., description="Either 'team' or 'opponents'")
    row_label: str = Field(..., description="The label in the first column, e.g. 'Oklahoma City' or 'Opponents'")
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
    home_away: str = Field(..., description="'home' or 'away' derived from vs/at")
    opponent_name: str
    result: str = Field(..., description="'W' or 'L'")
    score_label: str
    team_points: int
    opponent_points: int
    team_eff: int
    opponent_eff: int
    eff_diff: int

class ProfilePayload(BaseModel):
    team_stats_averages: List[StatRow]
    team_stats_totals: List[StatRow]
    won_matchups: List[MatchupRow]
    lost_matchups: List[MatchupRow]

# ---------------------------------------------------------------------------
# LLM Instruction
# ---------------------------------------------------------------------------

LLM_INSTRUCTION = """
You are scraping a HoopsStats Team Profile page.
Extract the following sections:
1. "team_stats_averages": From the 'Averages' table (Header: Team | G | W | Min ...). Rows usually 'TeamName' and 'Opponents'.
2. "team_stats_totals": From the 'Totals' table. Same columns.
3. "won_matchups": From 'Won Matchups' table. Columns: Date, Game, W/L, Score, Eff, Oppeff, Diffeff.
   - Parse 'Game' to determine 'home_away' ('at ' -> away, 'vs ' -> home) and 'opponent_name'.
   - Parse 'Score' (e.g. 131-101) to get 'team_points' and 'opponent_points'.
4. "lost_matchups": From 'Lost Matchups' table.

Ensure numeric fields are parsed correctly (strings like '44.1-88.8' stay strings).
"""

# ---------------------------------------------------------------------------
# Meta Utilities
# ---------------------------------------------------------------------------

def retrieve_meta_deterministic(url: str) -> Dict[str, Any]:
    # Simple URL parse fallback
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    meta = {}
    try:
        if "nba" in parts and "profile" in parts:
            nba_idx = parts.index("nba")
            meta["team_slug"] = parts[nba_idx + 1]
            prof_idx = parts.index("profile")
            meta["season_code"] = int(parts[prof_idx + 1])
            meta["team_id"] = int(parts[prof_idx + 2])
    except:
        pass
    return meta

# ---------------------------------------------------------------------------
# Main Logic
# ---------------------------------------------------------------------------

async def run_llm_extraction(url: str, api_token: str) -> Optional[ProfilePayload]:
    llm_config = LLMConfig(provider="openai/gpt-4o-mini", api_token=api_token)
    
    extraction_strategy = LLMExtractionStrategy(
        llm_config=llm_config,
        schema=ProfilePayload.model_json_schema(),
        extraction_type="schema",
        instruction=LLM_INSTRUCTION,
    )
    
    # CSS selector to target main content areas or just body? 
    # The page structure is table-heavy. Body is safe.
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        extraction_strategy=extraction_strategy,
    )
    
    async with AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False)) as crawler:
        result = await crawler.arun(url=url, config=run_config)
        
    if not result.extracted_content:
        return None
        
    try:
        data = json.loads(result.extracted_content)
        # Handle potential list wrap
        if isinstance(data, list) and len(data) > 0:
            data = data[0]
        return ProfilePayload.model_validate(data)
    except Exception as e:
        logger.error(f"LLM Parse Error: {e}")
        return None

async def build_result(url: str) -> Dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY")

    meta = retrieve_meta_deterministic(url)
    
    payload = await run_llm_extraction(url, api_key)
    if not payload:
        raise RuntimeError("Failed to extract data via LLM")
        
    # Calculate summary from extracted lists
    w_count = len(payload.won_matchups)
    l_count = len(payload.lost_matchups)
    summary = {
        "won_matchups": w_count,
        "lost_matchups": l_count,
        "record_all": {"wins": w_count, "losses": l_count}
    }
    
    return {
        "meta": meta,
        "team_stats_averages": [r.model_dump() for r in payload.team_stats_averages],
        "team_stats_totals": [r.model_dump() for r in payload.team_stats_totals],
        "season_matchup_summary": summary,
        "won_matchups": [r.model_dump() for r in payload.won_matchups],
        "lost_matchups": [r.model_dump() for r in payload.lost_matchups]
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    
    try:
        data = asyncio.run(build_result(args.url))
        if args.pretty:
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
    except Exception as e:
        logger.error(str(e))

if __name__ == "__main__":
    main()
