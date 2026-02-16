#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gamma_client.py
---------------

Cliente básico para a Gamma API da Polymarket (eventos, mercados, esportes, tags, times).

Uso:
    from app.data.nodes.polymarket.gamma.gamma_client import GammaClient, get_default_client

    client = get_default_client()
    sports = client.get_sports()
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import requests

logger = logging.getLogger(__name__)

GAMMA_BASE_URL = os.getenv("POLYMARKET_GAMMA_URL", "https://gamma-api.polymarket.com")
GAMMA_TIMEOUT = float(os.getenv("POLYMARKET_GAMMA_TIMEOUT", "10.0"))


@dataclass
class GammaClient:
    """HTTP client simples para a Gamma API."""

    base_url: str = GAMMA_BASE_URL
    timeout: float = GAMMA_TIMEOUT

    # ------------------------------------------------------------------ #
    # Internals                                                          #
    # ------------------------------------------------------------------ #

    def _build_url(self, path: str) -> str:
        return self.base_url.rstrip("/") + "/" + path.lstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = self._build_url(path)
        logger.debug("Gamma %s %s params=%r", method, url, params)
        resp = requests.request(method, url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------ #
    # Endpoints diretos (página única)                                  #
    # ------------------------------------------------------------------ #

    def get_health(self) -> Dict[str, Any]:
        """
        Nem toda instância expõe /health; use com cautela.
        """
        return self._request("GET", "/health")

    def get_sports(self) -> List[Dict[str, Any]]:
        """
        Retorna lista de esportes da Gamma (tipicamente com campos:
        id, sport, image, resolution, ordering, tags, ...).
        """
        return self._request("GET", "/sports")

    def get_sports_teams(self, sport: str = "nba") -> List[Dict[str, Any]]:
        """
        Tenta o endpoint específico de times por esporte.
        Alguns deployments expõem: /sports/list-teams?sport=<sport>
        """
        try:
            return self._request("GET", "/sports/list-teams", params={"sport": sport})
        except requests.HTTPError as exc:
            logger.debug("GET /sports/list-teams falhou: %r", exc)
            return []

    def get_teams(self, **params: Any) -> List[Dict[str, Any]]:
        """
        Fallback genérico para times:
            GET /teams?league=NBA  (por exemplo)
        """
        return self._request("GET", "/teams", params=params or None)

    def get_events(self, **params: Any) -> List[Dict[str, Any]]:
        return self._request("GET", "/events", params=params or None)

    def get_markets(self, **params: Any) -> List[Dict[str, Any]]:
        return self._request("GET", "/markets", params=params or None)

    # ------------------------------------------------------------------ #
    # Paginação simples (limit/offset)                                  #
    # ------------------------------------------------------------------ #

    def paginate(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        limit: int = 100,
        max_pages: Optional[int] = None,
    ) -> Iterable[Dict[str, Any]]:
        """
        Iterador genérico para endpoints paginados da Gamma.

        Muitos endpoints usam pattern:
            GET /resource?limit=<N>&offset=<M>

        Esta função abstrai esse padrão de forma simples.
        """
        params = dict(params or {})
        offset = int(params.pop("offset", 0))
        page = 0

        while True:
            page_params = dict(params)
            page_params["limit"] = limit
            page_params["offset"] = offset

            data = self._request("GET", path, params=page_params)
            if not data:
                break

            if isinstance(data, dict) and "data" in data:
                items = data.get("data") or []
            else:
                items = data

            if not items:
                break

            for item in items:
                yield item

            page += 1
            if len(items) < limit:
                break
            if max_pages is not None and page >= max_pages:
                break

            offset += limit


_default_client: Optional[GammaClient] = None


def get_default_client() -> GammaClient:
    """Retorna um singleton simples de GammaClient."""
    global _default_client
    if _default_client is None:
        _default_client = GammaClient()
    return _default_client



@dataclass
class OpenPosition:
    """Representação simplificada de uma posição aberta na Polymarket."""
    proxy_wallet: str
    asset: str
    condition_id: str
    size: float
    avg_price: float
    initial_value: float
    current_value: float
    cash_pnl: float
    percent_pnl: float
    title: str
    slug: str
    event_slug: str
    outcome: str
    outcome_index: int
    end_date: str

@dataclass
class ClosedPosition:
    """Representação simplificada de uma posição fechada na Polymarket."""
    proxy_wallet: str
    asset: str
    condition_id: str
    size: float
    avg_price: float
    realized_pnl: float
    title: str
    slug: str
    event_slug: str
    outcome: str
    end_date: str

@dataclass
class PolymarketDataClient:
    """
    Cliente para a Polymarket Data API (https://data-api.polymarket.com).
    Focado em dados do usuário (posições, trades, atividade).
    """
    base_url: str = "https://data-api.polymarket.com"
    timeout: float = 15.0

    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        logger.debug("DataAPI %s %s params=%r", method, url, params)
        resp = requests.request(method, url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_positions(
        self,
        user: str,
        size_threshold: float = 1.0,
        limit: int = 100,
        sort_by: str = "TOKENS",
        sort_direction: str = "DESC"
    ) -> List[Dict[str, Any]]:
        """
        GET /positions
        """
        params = {
            "user": user,
            "sizeThreshold": size_threshold,
            "limit": limit,
            "sortBy": sort_by,
            "sortDirection": sort_direction,
        }
        return self._request("GET", "/positions", params=params)

    def get_closed_positions(
        self,
        user: str,
        limit: int = 100,
        sort_by: str = "REALIZEDPNL",
        sort_direction: str = "DESC"
    ) -> List[Dict[str, Any]]:
        """
        GET /closed-positions
        """
        params = {
            "user": user,
            "limit": limit,
            "sortBy": sort_by,
            "sortDirection": sort_direction,
        }
        return self._request("GET", "/closed-positions", params=params)

    def get_trades(
        self,
        user: str,
        limit: int = 100,
        taker_only: bool = True
    ) -> List[Dict[str, Any]]:
        """
        GET /trades
        """
        params = {
            "user": user,
            "limit": limit,
            "takerOnly": str(taker_only).lower(),
        }
        return self._request("GET", "/trades", params=params)

    def get_orders(
        self,
        user: str,
        limit: int = 100,
        status: str = "OPEN" # OPEN, CANCELED, FILLED
    ) -> List[Dict[str, Any]]:
        """
        GET /orders
        Note: Data API might return all orders, we filter by status often handled by API params.
        """
        params = {
            "user": user,
            "limit": limit,
            "status": status
        }
        # Try /orders endpoint which is standard in Data API
        return self._request("GET", "/orders", params=params)


    def get_activity(
        self,
        user: str,
        limit: int = 100,
        sort_by: str = "TIMESTAMP",
        sort_direction: str = "DESC"
    ) -> List[Dict[str, Any]]:
        """
        GET /activity
        """
        params = {
            "user": user,
            "limit": limit,
            "sortBy": sort_by,
            "sortDirection": sort_direction,
        }
        return self._request("GET", "/activity", params=params)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    client = get_default_client()
    try:
        health = client.get_health()
        print("Gamma health:", health)
    except Exception as exc:  # noqa: BLE001
        print("Gamma /health indisponível:", repr(exc))

    sports = client.get_sports()
    print(f"Sports count: {len(sports)}")
