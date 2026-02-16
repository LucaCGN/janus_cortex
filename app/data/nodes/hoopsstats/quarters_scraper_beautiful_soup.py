#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
quarters_scraper_beautiful_soup.py
----------------------------------

Deterministic scraper for HoopsStats "By Quarter" pages using requests + BeautifulSoup.

Input URL pattern (example):

    https://www.hoopsstats.com/basketball/fantasy/nba/{team_slug}/team/quarters/{season_code}/{team_id}/{dashboard}

Output JSON shape:

{
  "meta": {
    "team_slug": str | null,
    "season_code": int | null,
    "team_id": int | null,
    "dashboard": int | null,
    "view_type": "team",
    "team_full_name": str | null,   # here we store the TEAM ABBREVIATION (e.g. "OKC")
    "team_nickname": str | null,    # e.g. "Thunder"
    "season_label": str | null      # e.g. "2025-2026"
  },
  "scoring_by_quarter": [
    {
      "row_type": "team" | "opponents" | "times_leading" | "times_trailing" | "draw",
      "row_label": str,
      "q1_points": float | null,
      "q2_points": float | null,
      "q3_points": float | null,
      "q4_points": float | null,
      "q1_games": int | null,
      "q2_games": int | null,
      "q3_games": int | null,
      "q4_games": int | null
    }
  ],
  "quarter_tips": [
    {
      "team_full_name": str,
      "wins": int,
      "losses": int,
      "situation_text": str
    }
  ]
}
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

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
    # Extend as needed; 26 is your current use case.
    26: "2025-2026",
}

NICKNAME_OVERRIDES: Dict[str, str] = {
    "philadelphia-76ers": "76ers",
    "portland-trail-blazers": "Blazers",
}


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class ScoringRow:
    row_type: str
    row_label: str
    q1_points: Optional[float] = None
    q2_points: Optional[float] = None
    q3_points: Optional[float] = None
    q4_points: Optional[float] = None
    q1_games: Optional[int] = None
    q2_games: Optional[int] = None
    q3_games: Optional[int] = None
    q4_games: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "row_type": self.row_type,
            "row_label": self.row_label,
            "q1_points": self.q1_points,
            "q2_points": self.q2_points,
            "q3_points": self.q3_points,
            "q4_points": self.q4_points,
            "q1_games": self.q1_games,
            "q2_games": self.q2_games,
            "q3_games": self.q3_games,
            "q4_games": self.q4_games,
        }


@dataclass
class QuarterTip:
    team_full_name: str
    wins: int
    losses: int
    situation_text: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "team_full_name": self.team_full_name,
            "wins": self.wins,
            "losses": self.losses,
            "situation_text": self.situation_text,
        }


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def fetch_html(url: str) -> str:
    logger.info("Fetching HoopsStats quarters page: %s", url)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; JanusCortexScraper/1.0; +https://github.com/lnoni)"
    }
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.text


def _clean_num(text: str, is_int: bool = False) -> Optional[float]:
    text = text.strip()
    if text in ("", "-", "–", "&nbsp;"):
        return None
    try:
        return int(text) if is_int else float(text)
    except ValueError:
        return None


def parse_scoring_by_quarter(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """
    Parse the "By Quarter" main table.
    Row IDs we expect:
        q1 = team
        q2 = opponents
        q3 = times leading
        q4 = times trailing
        q5 = draw
    """
    row_map = {
        "q1": "team",
        "q2": "opponents",
        "q3": "times_leading",
        "q4": "times_trailing",
        "q5": "draw",
    }

    rows: List[ScoringRow] = []

    for row_id, row_type in row_map.items():
        row = soup.find("tr", id=row_id)
        if not row:
            continue

        cells = row.find_all("td")
        if len(cells) < 5:
            continue

        label = cells[0].get_text(strip=True)

        # Helper closure for this row
        def cell_val(idx: int, is_int: bool = False) -> Optional[float]:
            return _clean_num(cells[idx].get_text(strip=True), is_int=is_int)

        scoring_row = ScoringRow(row_type=row_type, row_label=label)

        if row_type in ("team", "opponents"):
            scoring_row.q1_points = cell_val(1)
            scoring_row.q2_points = cell_val(2)
            scoring_row.q3_points = cell_val(3)
            scoring_row.q4_points = cell_val(4)
        else:
            scoring_row.q1_games = cell_val(1, is_int=True)
            scoring_row.q2_games = cell_val(2, is_int=True)
            scoring_row.q3_games = cell_val(3, is_int=True)
            scoring_row.q4_games = cell_val(4, is_int=True)

        rows.append(scoring_row)

    return [r.to_dict() for r in rows]


def parse_quarter_tips(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """
    Parse the "Tips" section, where each row is something like:

        Oklahoma City Thunder are 20 - 1 if leading after 3rd quarter
    """
    tips: List[QuarterTip] = []

    # This targets the same rows that produced the correct output in your first run
    tip_rows = soup.select("tr[id^=pyid]")

    import re

    for tr in tip_rows:
        text = tr.get_text(" ", strip=True)
        if not text:
            continue

        # Pattern: "<team> are W - L <situation>"
        match = re.search(r"(.*?) are (\d+)\s*-\s*(\d+)\s+(.*)", text)
        if not match:
            continue

        team_name = match.group(1).strip()
        wins = int(match.group(2))
        losses = int(match.group(3))
        situation = match.group(4).strip()

        tips.append(
            QuarterTip(
                team_full_name=team_name,
                wins=wins,
                losses=losses,
                situation_text=situation,
            )
        )

    return [t.to_dict() for t in tips]


def derive_meta_from_url(url: str) -> Dict[str, Any]:
    """
    Derive meta fields purely from the URL, not from the HTML,
    so we are robust to layout / content changes.

    - team_full_name: we intentionally store the TEAM ABBREVIATION here (e.g. "OKC").
    """
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]

    team_slug: Optional[str] = None
    season_code: Optional[int] = None
    team_id: Optional[int] = None
    dashboard: Optional[int] = None

    try:
        # Expected pattern:
        # /basketball/fantasy/nba/{team_slug}/team/quarters/{season_code}/{team_id}/{dashboard}
        nba_idx = parts.index("nba")
        team_slug = parts[nba_idx + 1]

        quarters_idx = parts.index("quarters")
        season_code = int(parts[quarters_idx + 1])
        team_id = int(parts[quarters_idx + 2])
        dashboard = int(parts[quarters_idx + 3])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not parse meta from URL '%s': %s", url, exc)

    # Season label from mapping, if known
    season_label = SEASON_CODE_TO_LABEL.get(season_code) if season_code is not None else None

    # team_full_name: we store the NBA tricode (e.g. "OKC")
    team_abbr = TEAM_SLUG_TO_ABBR.get(team_slug) if team_slug else None

    # Nickname: last token of slug or override
    if team_slug in NICKNAME_OVERRIDES:
        team_nickname = NICKNAME_OVERRIDES[team_slug]
    elif team_slug:
        # e.g. "oklahoma-city-thunder" -> "Thunder"
        last = team_slug.split("-")[-1]
        team_nickname = last.title() if last != "76ers" else "76ers"
    else:
        team_nickname = None

    return {
        "team_slug": team_slug,
        "season_code": season_code,
        "team_id": team_id,
        "dashboard": dashboard,
        "view_type": "team",
        # IMPORTANT: using ABBR here, per your request.
        "team_full_name": team_abbr,
        "team_nickname": team_nickname,
        "season_label": season_label,
    }


def build_result_from_html(url: str, html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    # Focus on statscontent if present; fallback to full soup otherwise
    stats_content = soup.select_one("div.statscontent") or soup

    meta = derive_meta_from_url(url)
    scoring = parse_scoring_by_quarter(stats_content)
    tips = parse_quarter_tips(stats_content)

    return {
        "meta": meta,
        "scoring_by_quarter": scoring,
        "quarter_tips": tips,
    }


# ---------------------------------------------------------------------------
# Public Facade
# ---------------------------------------------------------------------------

def construct_url(team_slug: str, team_id: int, season_code: int = 26, dashboard: int = 1) -> str:
    """
    Construct the HoopsStats URL.
    Example: https://www.hoopsstats.com/basketball/fantasy/nba/oklahoma-city-thunder/team/quarters/26/21/1
    """
    return f"https://www.hoopsstats.com/basketball/fantasy/nba/{team_slug}/team/quarters/{season_code}/{team_id}/{dashboard}"


def scrape_data(team_slug: str, team_id: int, season_code: int = 26) -> Dict[str, Any]:
    """
    High-level function to scrape Team Quarters data (scoring + tips).
    """
    url = construct_url(team_slug, team_id, season_code)
    html = fetch_html(url)
    return build_result_from_html(url, html)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deterministic HoopsStats 'By Quarter' scraper using BeautifulSoup."
    )
    parser.add_argument("--url", required=True, help="HoopsStats 'By Quarter' URL")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("Starting BeautifulSoup quarters scraper")
    html = fetch_html(args.url)
    data = build_result_from_html(args.url, html)

    if args.pretty:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(data, separators=(",", ":"), ensure_ascii=False))


if __name__ == "__main__":
    main()
