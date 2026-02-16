import asyncio
import json
import os
import sys
import argparse
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse
from dataclasses import asdict

from pydantic import BaseModel, Field

# Check if crawl4ai is available
try:
    from crawl4ai import AsyncWebCrawler
    from crawl4ai.extraction_strategy import LLMExtractionStrategy
except ImportError:
    # This is expected in environments where crawl4ai is not installed
    # We allow the script to exist but it will fail at runtime if run.
    pass

class Meta(BaseModel):
    source_url: str
    vs_team_id: Optional[int] = None
    vs_team_slug: Optional[str] = None
    lineup_section: Optional[str] = None

class PositionalMatchup(BaseModel):
    position: str = Field(..., description="Position name, e.g. 'Point-Guard'")
    team_player_name: str = Field(..., description="Name of the player from the primary team")
    team_player_url: Optional[str] = Field(None, description="URL for the team player")
    opponent_player_name: str = Field(..., description="Name of the opponent player")
    opponent_player_url: Optional[str] = Field(None, description="URL for the opponent player")

class TeamMatchupStat(BaseModel):
    stat_name: str = Field(..., description="Name of the statistic, e.g. 'Efficiency recap'")
    team_avg: float = Field(..., description="Average value for the primary team")
    team_rank: int = Field(..., description="Rank of the primary team")
    opponent_avg: float = Field(..., description="Average value for the opponent team")
    opponent_rank: int = Field(..., description="Rank of the opponent team")
    tips_to_watch_out_text: Optional[str] = Field(None, description="Text from the 'Tips to watch out' column")

class MatchupsPayload(BaseModel):
    positional_matchups: List[PositionalMatchup]
    team_matchup_stats: List[TeamMatchupStat]

LLM_INSTRUCTION = """You are scraping a HoopsStats Matchups page.
Extract two main sections:

1. "positional_matchups": Look for the list of matchups by position.
   - Example line: "Point-Guard Matchup : Ajay Mitchell vs Collin Gillespie"
   - Extract the position "Point-Guard".
   - Extract team_player_name "Ajay Mitchell" and opponent_player_name "Collin Gillespie".
   - If individual URLs are present, extract them. If it's a single comparison URL, put it in team_player_url.

2. "team_matchup_stats": Look for the comparison table between the two teams.
   - Headers might look like: {Team} | Stat | {Opponent} | Tips to watch out
   - Extract each row.
   - Fields: stat_name, team_avg, team_rank, opponent_avg, opponent_rank, tips_to_watch_out_text.
   - Ensure numeric fields are parsed as numbers.
"""

def derive_meta_from_url(url: str) -> Meta:
    parsed = urlparse(url)
    path_parts = parsed.path.strip('/').split('/')
    
    lineup_section = "unknown"
    vs_team_id = None
    
    if len(path_parts) >= 6:
        if path_parts[4] in ["team", "backcourt", "frontcourt", "starters", "bench", "in-the-paint", "out-of-paint"]:
            lineup_section = path_parts[4]
            
    last_seg = path_parts[-1]
    if '-' in last_seg:
        try:
            parts = last_seg.split('-')
            if len(parts) >= 2:
                vs_team_id = int(parts[1])
        except ValueError:
            pass
            
    return Meta(
        source_url=url,
        vs_team_id=vs_team_id,
        lineup_section=lineup_section
    )

async def crawl_and_extract(url: str):
    if "crawl4ai" not in sys.modules:
        print("Error: crawl4ai module not found.")
        sys.exit(1)
        
    api_token = os.getenv('OPENAI_API_KEY')
    if not api_token:
        print("Error: OPENAI_API_KEY environment variable not set.")
        sys.exit(1)

    extraction_strategy = LLMExtractionStrategy(
        provider="openai/gpt-4o-mini",
        api_token=api_token,
        schema=MatchupsPayload.model_json_schema(),
        extraction_type="schema",
        instruction=LLM_INSTRUCTION,
        chunk_token_threshold=1000,
        overlap_rate=0.0,
        apply_chunking=True,
        input_format="markdown",
        verbose=True
    )

    async with AsyncWebCrawler(verbose=True) as crawler:
        result = await crawler.arun(
            url=url,
            extraction_strategy=extraction_strategy,
            bypass_cache=True,
        )

        if not result.success:
            print("Crawl failed:", result.error_message)
            return None

        data = json.loads(result.extracted_content)
        return data

def main():
    parser = argparse.ArgumentParser(description="LLM Scraper for HoopsStats Matchups")
    parser.add_argument("url", help="Target URL")
    parser.add_argument("--output", help="Output JSON file")
    args = parser.parse_args()

    meta = derive_meta_from_url(args.url)
    
    # Run async crawl
    raw_data = asyncio.run(crawl_and_extract(args.url))
    
    if raw_data:
        # Combine meta and extracted data
        final_output = {
            "meta": meta.model_dump(),
            "positional_matchups": raw_data.get("positional_matchups", []),
            "team_matchup_stats": raw_data.get("team_matchup_stats", [])
        }
        
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(final_output, f, indent=2)
            print(f"Output saved to {args.output}")
        else:
            print(json.dumps(final_output, indent=2))
    else:
        print("Extraction returned no data.")

if __name__ == "__main__":
    main()
