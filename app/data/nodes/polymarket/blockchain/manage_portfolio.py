#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
manage_portfolio.py
-------------------

Node Polymarket — Gerenciamento de Portfólio.

Modos atuais:
- Leitura via Data-API (/positions, /closed-positions).
- Escrita via CLOB (ordens), usando py-clob-client.

Integração com .env esperado (exemplo):

    # --- POLYMARKET CREDENTIALS / CONFIG ---
    PK=0x...                                  # private key do signer
    CLOB_API_KEY=...
    CLOB_SECRET=...
    CLOB_PASS_PHRASE=...
    CLOB_API_URL="https://clob.polymarket.com"
    CHAIN_ID=137

    # --- ENDEREÇOS ONCHAIN (para Data-API + CLOB) ---
    # Proxy / wallet principal (onde ficam USDC + posições):
    POLYMARKET_PROXY_WALLET=0x7d2F...-1763866468400

    # (opcional) endereço "limpo" sem sufixo:
    POLYMARKET_PRIMARY_WALLET=0x7d2F...

    # --- Config extra para CLOB (proxy wallet) ---
    # 0 = EOA puro, 1 = email/Magic, 2 = browser proxy
    POLY_SIGNATURE_TYPE=1

    # (opcional) se quiser forçar manualmente o funder (senão usamos wallet_address)
    # POLY_FUNDER_ADDRESS=0x7d2F...

Notas importantes:
- Data-API (/positions, /closed-positions) espera `user` = endereço 0x + 40 hex.
- Em contas com proxy wallet, esse endereço é o "wallet address" do perfil
  (ex.: https://polymarket.com/@0x7d2F...-1763866468400).
- Para o CLOB:
    - key = PK do signer (EOA / Magic)
    - funder = endereço que realmente tem os fundos (proxy wallet).
    - signature_type define o tipo de assinatura (ver docs do py-clob-client).
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, List, Optional

import pandas as pd
import requests
from pydantic import BaseModel, Field
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs
from py_clob_client.constants import POLYGON

logger = logging.getLogger(__name__)

DATA_API_BASE_URL = "https://data-api.polymarket.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_base_address(raw: Optional[str]) -> Optional[str]:
    """
    Extrai endereço base 0x + 40 hex de uma string env, ex.:

        "0x1234...abcd-1763866468400" -> "0x1234...abcd"

    Se não bater esse formato mínimo, retorna None.
    """
    if not raw:
        return None
    val = raw.strip().strip('"').strip("'")
    if val.startswith("0x") and len(val) >= 42:
        return val[:42]
    return None


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class PolymarketCredentials(BaseModel):
    """
    Credenciais para Data-API + CLOB.

    - wallet_address: endereço base (0x + 40 hex) usado em `user` da Data-API.
    - proxy_wallet_raw / primary_wallet_raw: valores brutos vindos do .env.
    - private_key / api_key / secret / passphrase: credenciais do CLOB.
    - clob_host: endpoint do CLOB.
    - chain_id: id da chain (Polygon).
    - funder_address: endereço que realmente tem os fundos (proxy wallet).
    - signature_type: tipo de assinatura (ver docs py-clob-client).
    """

    wallet_address: str = Field(
        ...,
        description="Endereço 0x-prefixed (40 hex) usado em `user` na Data-API.",
    )

    proxy_wallet_raw: Optional[str] = Field(
        default=None,
        description="Valor bruto de POLYMARKET_PROXY_WALLET (pode ter sufixo).",
    )
    primary_wallet_raw: Optional[str] = Field(
        default=None,
        description="Valor bruto de POLYMARKET_PRIMARY_WALLET (pode ter sufixo).",
    )

    private_key: Optional[str] = Field(
        default=None,
        description="PK ou POLIMARKET_PRIVATE_KEY (CLOB trading).",
    )
    api_key: Optional[str] = Field(
        default=None,
        description="CLOB_API_KEY ou POLIMARKET_API_KEY (CLOB trading).",
    )
    secret: Optional[str] = Field(
        default=None,
        description="CLOB_SECRET ou POLIMARKET_SECRET (CLOB trading).",
    )
    passphrase: Optional[str] = Field(
        default=None,
        description="CLOB_PASS_PHRASE ou POLIMARKET_PASSPHRASE (CLOB trading).",
    )

    clob_host: Optional[str] = Field(
        default=None,
        description="CLOB_API_URL ou POLYMARKET_HOST (ex.: https://clob.polymarket.com).",
    )
    chain_id: Optional[int] = Field(
        default=None,
        description="CHAIN_ID (ex.: 137 para Polygon).",
    )

    funder_address: Optional[str] = Field(
        default=None,
        description=(
            "Endereço que realmente possui os fundos no CLOB. "
            "Em contas com proxy wallet, é o endereço do proxy (perfil)."
        ),
    )
    signature_type: int = Field(
        default=1,
        description=(
            "Tipo de assinatura para o CLOB (py-clob-client): "
            "0=EOA puro, 1=email/Magic, 2=browser proxy."
        ),
    )

    @classmethod
    def from_env(cls) -> "PolymarketCredentials":
        """
        Carrega credenciais do .env.

        - wallet_address é extraído de POLYMARKET_PROXY_WALLET ou POLYMARKET_PRIMARY_WALLET.
        - funder_address, se não definido, cai no próprio wallet_address.
        - signature_type vem de POLY_SIGNATURE_TYPE / POLYMARKET_SIGNATURE_TYPE (default=1).
        """
        # --- chaves CLOB ---
        pk = os.getenv("PK") or os.getenv("POLIMARKET_PRIVATE_KEY")
        api_key = os.getenv("CLOB_API_KEY") or os.getenv("POLIMARKET_API_KEY")
        secret = os.getenv("CLOB_SECRET") or os.getenv("POLIMARKET_SECRET")
        passphrase = os.getenv("CLOB_PASS_PHRASE") or os.getenv("POLIMARKET_PASSPHRASE")
        host = os.getenv("CLOB_API_URL") or os.getenv("POLYMARKET_HOST") or "https://clob.polymarket.com"

        # CHAIN_ID: se não vier, usamos Polygon
        chain_id = int(os.getenv("CHAIN_ID", POLYGON))

        # --- endereços base (proxy / primary) ---
        proxy = os.getenv("POLYMARKET_PROXY_WALLET")
        primary = os.getenv("POLYMARKET_PRIMARY_WALLET")

        wallet_address: Optional[str] = None
        if proxy:
            wallet_address = _extract_base_address(proxy)
        elif primary:
            wallet_address = _extract_base_address(primary)

        if not wallet_address:
            wallet_address = ""

        # --- signature_type / funder ---
        sig_env = (
            os.getenv("POLY_SIGNATURE_TYPE")
            or os.getenv("POLYMARKET_SIGNATURE_TYPE")
            or "1"
        )
        try:
            signature_type = int(sig_env)
        except ValueError:
            signature_type = 1

        funder = (
            os.getenv("POLY_FUNDER_ADDRESS")
            or os.getenv("POLYMARKET_FUNDER")
            or wallet_address
        )

        return cls(
            wallet_address=wallet_address,
            proxy_wallet_raw=proxy,
            primary_wallet_raw=primary,
            private_key=pk,
            api_key=api_key,
            secret=secret,
            passphrase=passphrase,
            clob_host=host,
            chain_id=chain_id,
            funder_address=funder,
            signature_type=signature_type,
        )


from app.data.nodes.polymarket.gamma.gamma_client import PolymarketDataClient, OpenPosition, ClosedPosition

@dataclass
class OpenOrder:
    """Representação de uma ordem aberta (Limit Order)."""

    id: str
    market: str
    asset_id: str
    side: str
    size: float
    price: float
    filled_size: float
    status: str
    created_at: int
    token_id: str

# ---------------------------------------------------------------------------
# Core fetchers (Data-API)
# ---------------------------------------------------------------------------

def view_open_positions(
    creds: PolymarketCredentials,
    *,
    event_slug: Optional[str] = None,
    min_size: float = 1.0,
) -> List[OpenPosition]:
    """
    Busca posições abertas de um usuário via /positions, com filtro opcional por eventSlug.
    """
    client = PolymarketDataClient()
    raw_positions = client.get_positions(
        user=creds.wallet_address,
        size_threshold=min_size,
        limit=500
    )

    positions: List[OpenPosition] = []
    for raw in raw_positions:
        if event_slug is not None and raw.get("eventSlug") != event_slug:
            continue
        try:
            pos = OpenPosition(
                proxy_wallet=str(raw.get("proxyWallet")),
                asset=str(raw.get("asset")),
                condition_id=str(raw.get("conditionId")),
                size=float(raw.get("size", 0) or 0.0),
                avg_price=float(raw.get("avgPrice", 0) or 0.0),
                initial_value=float(raw.get("initialValue", 0) or 0.0),
                current_value=float(raw.get("currentValue", 0) or 0.0),
                cash_pnl=float(raw.get("cashPnl", 0) or 0.0),
                percent_pnl=float(raw.get("percentPnl", 0) or 0.0),
                title=str(raw.get("title")),
                slug=str(raw.get("slug")),
                event_slug=str(raw.get("eventSlug")),
                outcome=str(raw.get("outcome")),
                outcome_index=int(raw.get("outcomeIndex", 0) or 0),
                end_date=str(raw.get("endDate")),
            )
            positions.append(pos)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[manage_portfolio][view_open_positions] Falha ao parsear posição: %r (%s)",
                raw,
                exc,
            )

    return positions


def open_positions_to_df(positions: List[OpenPosition]) -> pd.DataFrame:
    """Converte lista de OpenPosition em DataFrame."""
    return pd.DataFrame([asdict(p) for p in positions])


def view_closed_positions(
    creds: PolymarketCredentials,
    *,
    event_slug: Optional[str] = None,
) -> List[ClosedPosition]:
    """
    Busca posições fechadas via /closed-positions (Data-API).
    """
    client = PolymarketDataClient()
    raw_positions = client.get_closed_positions(
        user=creds.wallet_address,
        limit=500
    )

    positions: List[ClosedPosition] = []
    for raw in raw_positions:
        if event_slug is not None and raw.get("eventSlug") != event_slug:
            continue
        try:
            pos = ClosedPosition(
                proxy_wallet=str(raw.get("proxyWallet")),
                asset=str(raw.get("asset")),
                condition_id=str(raw.get("conditionId")),
                size=float(raw.get("size", 0) or 0.0),
                avg_price=float(raw.get("avgPrice", 0) or 0.0),
                realized_pnl=float(raw.get("realizedPnl", 0) or 0.0),
                title=str(raw.get("title")),
                slug=str(raw.get("slug")),
                event_slug=str(raw.get("eventSlug")),
                outcome=str(raw.get("outcome")),
                end_date=str(raw.get("endDate")),
            )
            positions.append(pos)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[manage_portfolio][view_closed_positions] Falha ao parsear posição: %r (%s)",
                raw,
                exc,
            )

    return positions


def closed_positions_to_df(positions: List[ClosedPosition]) -> pd.DataFrame:
    return pd.DataFrame([asdict(p) for p in positions])


def view_orders(creds: PolymarketCredentials, *, open_only: bool = True) -> List[OpenOrder]:
    """
    Busca ordens via CLOB API (py-clob-client).

    Requer Private Key e usa proxy wallet como funder (modo proxy/email).
    """
    if not creds.private_key:
        logger.warning("[view_orders] Private Key ausente. Não é possível buscar ordens no CLOB.")
        return []

    try:
        client = ClobClient(
            host=creds.clob_host or "https://clob.polymarket.com",
            key=creds.private_key,
            chain_id=creds.chain_id or POLYGON,
            signature_type=creds.signature_type,
            funder=creds.funder_address or creds.wallet_address,
        )

        # Configura API Creds se disponíveis
        if creds.api_key and creds.secret and creds.passphrase:
            try:
                api_creds = ApiCreds(
                    api_key=creds.api_key,
                    api_secret=creds.secret,
                    api_passphrase=creds.passphrase,
                )
                client.set_api_creds(api_creds)
            except Exception as e:  # noqa: BLE001
                logger.warning("[view_orders] Falha ao setar API Creds: %s", e)

        resp = client.get_orders()
        logger.info("[view_orders] Raw CLOB response: %s", resp)

        orders: List[OpenOrder] = []

        raw_list = resp if isinstance(resp, list) else resp.get("data", [])

        for raw in raw_list:
            try:
                o = OpenOrder(
                    id=str(raw.get("orderID") or raw.get("id")),
                    market=str(raw.get("market", "")),
                    asset_id=str(raw.get("asset_id", "")),
                    side=str(raw.get("side")),
                    size=float(raw.get("size", 0) or 0.0),
                    price=float(raw.get("price", 0) or 0.0),
                    filled_size=float(raw.get("filledSize") or raw.get("filled_size") or 0.0),
                    status=str(raw.get("status", "OPEN")),
                    created_at=int(raw.get("timestamp") or raw.get("created_at") or 0),
                    token_id=str(raw.get("asset_id") or raw.get("token_id") or ""),
                )
                if open_only and o.status not in ("OPEN", "PARTIALLY_FILLED"):
                    continue
                orders.append(o)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Falha ao parsear ordem CLOB: %s | Raw: %s", exc, raw)

        return orders

    except Exception as e:  # noqa: BLE001
        logger.error("[view_orders] Erro ao buscar ordens no CLOB: %s", e)
        return []


# ---------------------------------------------------------------------------
# Escrita (ordens) via CLOB
# ---------------------------------------------------------------------------


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class PlaceOrderRequest(BaseModel):
    """
    Modelo para envio de ordens ao CLOB.
    """

    market_id: str
    token_id: str
    side: OrderSide
    size: float
    price: float
    order_type: OrderType = OrderType.LIMIT


class PlaceOrderResult(BaseModel):
    """Resultado de uma ordem."""

    success: bool
    raw: Any


def place_new_order(creds: PolymarketCredentials, request: PlaceOrderRequest) -> PlaceOrderResult:
    """
    Envia uma ordem para o CLOB, usando proxy wallet como funder (se definido).

    - Usa credenciais L1 (PK) para assinar.
    - Usa credenciais L2 (API keys) para autenticar a chamada.
    """
    if not creds.private_key:
        return PlaceOrderResult(success=False, raw="Private Key ausente")

    try:
        client = ClobClient(
            host=creds.clob_host or "https://clob.polymarket.com",
            key=creds.private_key,
            chain_id=creds.chain_id or POLYGON,
            signature_type=creds.signature_type,
            funder=creds.funder_address or creds.wallet_address,
        )

        if creds.api_key and creds.secret and creds.passphrase:
            try:
                api_creds = ApiCreds(
                    api_key=creds.api_key,
                    api_secret=creds.secret,
                    api_passphrase=creds.passphrase,
                )
                client.set_api_creds(api_creds)
            except Exception as e:  # noqa: BLE001
                logger.warning("[place_new_order] Falha ao setar API Creds: %s", e)

        order_args = OrderArgs(
            price=request.price,
            size=request.size,
            side=request.side.value,  # BUY/SELL
            token_id=request.token_id,
        )

        logger.info(
            "[place_new_order] Assinando ordem: %s | funder=%s | signature_type=%s",
            order_args,
            creds.funder_address or creds.wallet_address,
            creds.signature_type,
        )
        signed_order = client.create_order(order_args)

        logger.info("[place_new_order] Enviando ordem assinada (POST)...")
        resp = client.post_order(signed_order)

        logger.info("[place_new_order] Sucesso: %s", resp)
        return PlaceOrderResult(success=True, raw=resp)

    except Exception as e:  # noqa: BLE001
        logger.error("[place_new_order] Erro ao enviar ordem: %s", e)
        return PlaceOrderResult(success=False, raw=str(e))


def cancel_order(creds: PolymarketCredentials, order_id: str) -> PlaceOrderResult:
    """
    Cancela uma ordem existente no CLOB.
    """
    if not creds.private_key:
        return PlaceOrderResult(success=False, raw="Private Key ausente")

    try:
        client = ClobClient(
            host=creds.clob_host or "https://clob.polymarket.com",
            key=creds.private_key,
            chain_id=creds.chain_id or POLYGON,
            signature_type=creds.signature_type,
            funder=creds.funder_address or creds.wallet_address,
        )

        if creds.api_key and creds.secret and creds.passphrase:
            try:
                api_creds = ApiCreds(
                    api_key=creds.api_key,
                    api_secret=creds.secret,
                    api_passphrase=creds.passphrase,
                )
                client.set_api_creds(api_creds)
            except Exception as e:  # noqa: BLE001
                logger.warning("[cancel_order] Falha ao setar API Creds: %s", e)

        logger.info("[cancel_order] Cancelando ordem %s...", order_id)
        resp = client.cancel(order_id)
        logger.info("[cancel_order] Sucesso: %s", resp)
        
        return PlaceOrderResult(success=True, raw=resp)

    except Exception as e:
        logger.error("[cancel_order] Erro ao cancelar ordem: %s", e)
        return PlaceOrderResult(success=False, raw=str(e))


@dataclass
class Trade:
    """Representação de um trade executado (History)."""
    id: str
    market: str
    asset_id: str
    side: str
    size: float
    price: float
    timestamp: int
    taker_order_id: str
    maker_order_id: str


def view_trades(creds: PolymarketCredentials) -> List[Trade]:
    """
    Busca histórico de trades via CLOB API (get_trades).
    """
    if not creds.private_key:
        logger.warning("[view_trades] Private Key ausente")
        return []

    try:
        client = ClobClient(
            host=creds.clob_host or "https://clob.polymarket.com",
            key=creds.private_key,
            chain_id=creds.chain_id or POLYGON,
            signature_type=creds.signature_type,
            funder=creds.funder_address or creds.wallet_address,
        )
        if creds.api_key and creds.secret and creds.passphrase:
             try:
                 from py_clob_client.clob_types import ApiCreds
                 client.set_api_creds(ApiCreds(creds.api_key, creds.secret, creds.passphrase))
             except:
                 pass

        resp = client.get_trades()
        # logger.info("[view_trades] Found %d trades", len(resp))
        
        trades = []
        for raw in resp:
            try:
                t = Trade(
                    id=str(raw.get("id") or raw.get("hash") or ""),
                    market=str(raw.get("market") or ""),
                    asset_id=str(raw.get("asset_id") or ""),
                    side=str(raw.get("side")),
                    size=float(raw.get("size") or 0.0),
                    price=float(raw.get("price") or 0.0),
                    timestamp=int(raw.get("timestamp") or 0),
                    taker_order_id=str(raw.get("taker_order_id") or ""),
                    maker_order_id=str(raw.get("maker_order_id") or "")
                )
                trades.append(t)
            except Exception as e:
                logger.warning(f"Error parsing trade: {e}")
                
        return trades

    except Exception as e:
        logger.error(f"[view_trades] Error: {e}")
        return []

