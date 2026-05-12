from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests


BALLDONTLIE_WNBA_BASE_URL = "https://api.balldontlie.io/wnba/v1"
BALLDONTLIE_WNBA_DOCS_URL = "https://wnba.balldontlie.io/"
PAID_PBP_TIERS = {"all-star", "all_star", "goat", "all-access", "all_access"}
PAID_STATS_TIERS = {"goat", "all-access", "all_access"}


@dataclass(frozen=True)
class BalldontlieWnbaConfig:
    api_key: str | None
    tier: str | None
    base_url: str = BALLDONTLIE_WNBA_BASE_URL

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    @property
    def normalized_tier(self) -> str:
        return str(self.tier or "").strip().lower()

    @property
    def has_play_by_play_tier(self) -> bool:
        return self.normalized_tier in PAID_PBP_TIERS

    @property
    def has_stats_tier(self) -> bool:
        return self.normalized_tier in PAID_STATS_TIERS


def get_balldontlie_wnba_config() -> BalldontlieWnbaConfig:
    api_key = (
        os.getenv("WNBA_BALLDONTLIE_API_KEY")
        or os.getenv("BALLDONTLIE_WNBA_API_KEY")
        or os.getenv("BALLDONTLIE_API_KEY")
    )
    tier = (
        os.getenv("WNBA_BALLDONTLIE_TIER")
        or os.getenv("BALLDONTLIE_WNBA_TIER")
        or os.getenv("BALLDONTLIE_TIER")
    )
    return BalldontlieWnbaConfig(api_key=api_key, tier=tier)


def describe_historical_backfill_readiness(
    *,
    season: str,
    require_play_by_play: bool = True,
    require_team_player_stats: bool = True,
    config: BalldontlieWnbaConfig | None = None,
) -> dict[str, Any]:
    config = config or get_balldontlie_wnba_config()
    blockers: list[dict[str, Any]] = []
    if not config.has_api_key:
        blockers.append(
            {
                "code": "missing_balldontlie_wnba_api_key",
                "requirement": "Set WNBA_BALLDONTLIE_API_KEY or BALLDONTLIE_WNBA_API_KEY.",
                "source": "balldontlie_wnba",
            }
        )
    if require_play_by_play and not config.has_play_by_play_tier:
        blockers.append(
            {
                "code": "missing_balldontlie_wnba_play_by_play_tier",
                "requirement": "Configure WNBA balldontlie tier ALL-STAR, GOAT, or ALL-ACCESS for /plays.",
                "source": "balldontlie_wnba",
            }
        )
    if require_team_player_stats and not config.has_stats_tier:
        blockers.append(
            {
                "code": "missing_balldontlie_wnba_stats_tier",
                "requirement": "Configure WNBA balldontlie tier GOAT or ALL-ACCESS for player/team stats.",
                "source": "balldontlie_wnba",
            }
        )
    return {
        "season": str(season),
        "source": "balldontlie_wnba",
        "base_url": config.base_url,
        "docs_url": BALLDONTLIE_WNBA_DOCS_URL,
        "status": "ready" if not blockers else "blocked",
        "api_key_configured": config.has_api_key,
        "tier_configured": bool(config.tier),
        "tier": config.tier,
        "blockers": blockers,
    }


class BalldontlieWnbaClient:
    def __init__(self, config: BalldontlieWnbaConfig | None = None, timeout_sec: int = 20):
        self.config = config or get_balldontlie_wnba_config()
        self.timeout_sec = timeout_sec
        if not self.config.has_api_key:
            raise ValueError("balldontlie WNBA API key is not configured")

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        clean_path = "/" + path.strip("/")
        response = requests.get(
            f"{self.config.base_url}{clean_path}",
            headers={"Authorization": str(self.config.api_key)},
            params=params or {},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def get_games(self, *, season: int, per_page: int = 100) -> dict[str, Any]:
        return self.get("/games", params={"seasons[]": season, "per_page": per_page})

    def get_plays(self, *, game_id: int) -> dict[str, Any]:
        if not self.config.has_play_by_play_tier:
            raise ValueError("balldontlie WNBA play-by-play requires ALL-STAR, GOAT, or ALL-ACCESS tier")
        return self.get("/plays", params={"game_id": game_id})
