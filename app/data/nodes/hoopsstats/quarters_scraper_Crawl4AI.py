#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
quarters_scraper_Crawl4AI.py
----------------------------

Scraper LLM-powered para o endpoint "By Quarter" do HoopsStats,
usando Crawl4AI + LLMExtractionStrategy como fallback inteligente
para HTML legados.

Saída: JSON com estrutura:
{
  "meta": {...},                 # montado deterministicamente via URL + HTML
  "scoring_by_quarter": [...],   # extraído via LLM (schema)
  "quarter_tips": [...]          # extraído via LLM (schema)
}

Requisitos de ambiente:
- OPENAI_API_KEY  -> usado com o modelo 'openai/gpt-4o-mini'

Uso:

    python app/data/nodes/hoopsstats/quarters_scraper_Crawl4AI.py \
        --url "https://www.hoopsstats.com/basketball/fantasy/nba/oklahoma-city-thunder/team/quarters/26/21/1" \
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
# Constantes / Config
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

# Instrução para o LLMExtractionStrategy
LLM_INSTRUCTION = """
You are given the cleaned Markdown content of a HoopsStats 'By Quarter' page
for one NBA team in one season.

From this content, extract ONLY the following structured data:

1) scoring_by_quarter:
   - This is the main "Scoring by Quarter" table.
   - It has exactly 5 logical rows:
        - Team (the team itself)
        - Opponents
        - Times Leading
        - Times Trailing
        - Draw
   - Each row must be represented as an object with:
        - row_type: one of ["team", "opponents", "times_leading", "times_trailing", "draw"]
        - row_label: the textual label in the table (e.g., "Thunder", "Opponents", "Times Leading", etc.)
        - q1_points, q2_points, q3_points, q4_points:
             * For "team" and "opponents": decimal (float) points per quarter.
             * For the other row types: MUST be null.
        - q1_games, q2_games, q3_games, q4_games:
             * For "times_leading", "times_trailing" and "draw": integer counts per quarter.
             * For "team" and "opponents": MUST be null.

2) quarter_tips:
   - This is the list of textual "Tips" below the quarter table, each one usually in a row.
   - Each tip is a sentence like:
        "<Team Name> are W - L if leading after 3rd quarter"
        "<Team Name> are 9 - 0 at Home, if leading at Halftime"
        ...
   - For each such tip, extract:
        - team_full_name: full team name as appears in the text (e.g., "Oklahoma City Thunder").
        - wins: integer W
        - losses: integer L
        - situation_text: all the remaining text after "W - L", e.g. "if leading after 3rd quarter",
          "at Home, if trailing after 1st quarter", etc.

Return a JSON object with exactly two top-level keys:
{
  "scoring_by_quarter": [...],
  "quarter_tips": [...]
}

Do NOT include any other keys. Do NOT include "meta".
Ensure the JSON strictly follows the provided JSON schema.
"""

# ---------------------------------------------------------------------------
# Pydantic models para validação do payload LLM
# ---------------------------------------------------------------------------


class ScoringRow(BaseModel):
    row_type: str
    row_label: str
    q1_points: Optional[float]
    q2_points: Optional[float]
    q3_points: Optional[float]
    q4_points: Optional[float]
    q1_games: Optional[int]
    q2_games: Optional[int]
    q3_games: Optional[int]
    q4_games: Optional[int]


class QuarterTip(BaseModel):
    team_full_name: str
    wins: int
    losses: int
    situation_text: str


class QuarterStatsPayload(BaseModel):
    scoring_by_quarter: List[ScoringRow]
    quarter_tips: List[QuarterTip]


# ---------------------------------------------------------------------------
# Funções para meta (determinístico)
# ---------------------------------------------------------------------------


def fetch_html(url: str, timeout: int = 15) -> str:
    logger.info("Fetching HoopsStats quarters page (for meta enrichment): %s", url)
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def parse_url_meta(url: str) -> Dict[str, Any]:
    """
    URL esperada:
    https://www.hoopsstats.com/basketball/fantasy/nba/{team_slug}/team/quarters/{season_code}/{team_id}/{dashboard}
    """
    parts = url.rstrip("/").split("/")
    if len(parts) < 12:
        raise ValueError(f"URL inesperada para HoopsStats quarters: {url}")

    team_slug = parts[6]
    season_code = int(parts[9])
    team_id = int(parts[10])
    dashboard = int(parts[11])

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

    # Convenção: team_full_name = sigla (ex.: "OKC"), como no scraper BeautifulSoup
    team_full_name = team_abbrev

    meta: Dict[str, Any] = {
        **base_meta,
        "team_full_name": team_full_name,
        "team_nickname": team_nickname,
        "season_label": season_label,
    }
    return meta


# ---------------------------------------------------------------------------
# Execução do Crawl4AI + LLMExtractionStrategy
# ---------------------------------------------------------------------------


async def run_llm_extraction(url: str, llm_config: LLMConfig) -> Optional[QuarterStatsPayload]:
    """
    Executa o Crawl4AI com LLMExtractionStrategy para extrair:
    - scoring_by_quarter
    - quarter_tips
    conforme o esquema QuarterStatsPayload.
    """
    browser_config = BrowserConfig(verbose=False, headless=True)

    extraction_strategy = LLMExtractionStrategy(
        llm_config=llm_config,
        schema=QuarterStatsPayload.model_json_schema(),
        extraction_type="schema",
        instruction=LLM_INSTRUCTION,
    )

    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        word_count_threshold=10,
        extraction_strategy=extraction_strategy,
        css_selector=".statscontent",
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=url, config=run_config)

    raw_content = result.extracted_content
    if not raw_content:
        logger.error("LLM extraction returned empty content")
        return None

    # Tentar fazer o parse do JSON retornado pelo LLM
    try:
        raw = json.loads(raw_content)
    except Exception:
        logger.exception(
            "Falha ao fazer json.loads do conteúdo extraído pelo LLM (primeiros 500 chars): %s",
            raw_content[:500],
        )
        return None

    # Em alguns casos o Crawl4AI/LiteLLM devolve uma LISTA com um único objeto
    # Ex.: [{"scoring_by_quarter": [...], "quarter_tips": [...]}]
    if isinstance(raw, list):
        logger.warning(
            "LLM extraction retornou uma lista; tentando localizar o primeiro objeto com as chaves esperadas..."
        )
        chosen: Optional[Dict[str, Any]] = None
        for item in raw:
            if isinstance(item, dict) and "scoring_by_quarter" in item and "quarter_tips" in item:
                chosen = item
                break
        if chosen is None:
            logger.error(
                "Nenhum item da lista possui as chaves esperadas 'scoring_by_quarter' e 'quarter_tips'. "
                "Payload bruto: %s",
                raw,
            )
            return None
        raw = chosen

    if not isinstance(raw, dict):
        logger.error(
            "Payload do LLM não é um objeto JSON após normalização (tipo=%s, valor=%s)",
            type(raw),
            raw,
        )
        return None

    try:
        payload = QuarterStatsPayload.model_validate(raw)
    except ValidationError as e:
        logger.error("Validation error para payload do LLM: %s", e)
        return None

    return payload


async def build_result_llm(url: str) -> Dict[str, Any]:
    """
    Orquestra:
    - Download do HTML para construir meta (determinístico)
    - Chamada do LLM (Crawl4AI) para scoring_by_quarter + quarter_tips
    """
    api_token = os.getenv("OPENAI_API_KEY")
    if not api_token:
        raise RuntimeError("OPENAI_API_KEY não definido; necessário para usar Crawl4AI com LLM.")

    html = fetch_html(url)
    meta = extract_meta(url, html)

    # Hardcode do modelo: openai/gpt-4o-mini
    llm_config = LLMConfig(
        provider="openai/gpt-4o-mini",
        api_token=api_token,
    )

    logger.info("Running Crawl4AI LLM extraction with provider='openai/gpt-4o-mini'...")
    payload = await run_llm_extraction(url, llm_config)
    if payload is None:
        raise RuntimeError("LLM extraction falhou ao montar QuarterStatsPayload.")

    return {
        "meta": meta,
        "scoring_by_quarter": [row.model_dump() for row in payload.scoring_by_quarter],
        "quarter_tips": [tip.model_dump() for tip in payload.quarter_tips],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="HoopsStats Quarters scraper (Crawl4AI + LLM fallback)",
    )
    parser.add_argument(
        "--url",
        required=True,
        help="URL completa do endpoint HoopsStats 'By Quarter'",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Imprimir JSON com indentação",
    )
    args = parser.parse_args()

    logger.info("Starting Crawl4AI quarters scraper")

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
