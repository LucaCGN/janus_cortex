from __future__ import annotations

import json
from typing import Any
from urllib.request import Request, urlopen


WNBA_CDN_BASE_URL = "https://cdn.wnba.com/static/json"
WNBA_SCHEDULE_URL = f"{WNBA_CDN_BASE_URL}/staticData/scheduleLeagueV2.json"
WNBA_TODAYS_SCOREBOARD_URL = f"{WNBA_CDN_BASE_URL}/liveData/scoreboard/todaysScoreboard_10.json"

WNBA_CDN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.wnba.com/",
    "Origin": "https://www.wnba.com",
}


def wnba_boxscore_url(game_id: str) -> str:
    return f"{WNBA_CDN_BASE_URL}/liveData/boxscore/boxscore_{game_id}.json"


def wnba_play_by_play_url(game_id: str) -> str:
    return f"{WNBA_CDN_BASE_URL}/liveData/playbyplay/playbyplay_{game_id}.json"


def fetch_wnba_cdn_json(url: str) -> dict[str, Any]:
    request = Request(url, headers=WNBA_CDN_HEADERS)
    with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed provider URLs.
        payload = response.read().decode("utf-8")
    parsed = json.loads(payload)
    return parsed if isinstance(parsed, dict) else {}
