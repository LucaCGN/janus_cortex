#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sports_metadata.py
------------------

Node Gamma → Sports Metadata (NBA).

Responsável por:
- Buscar metadados dos esportes na Gamma (/sports)
- Encontrar a entrada específica para NBA
- Extrair tag_ids que serão usados para filtrar events/markets
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.data.nodes.polymarket.gamma.gamma_client import GammaClient, get_default_client


class NBASportMetadataRequest(BaseModel):
    """Request minimalista – mantida como node parametrizável se virar tool."""
    sport: str = "nba"


class NBASportMetadata(BaseModel):
    """Metadados relevantes para NBA via Gamma /sports."""
    sport: str
    image: Optional[str] = None
    resolution: Optional[str] = None
    ordering: Optional[str] = None
    tag_ids: List[int] = Field(default_factory=list)
    raw: Dict[str, Any]


def _parse_tag_ids(raw_tags: Any) -> List[int]:
    """
    Gamma costuma expor `tags` em SportMetadata como string separada por vírgulas
    (ex.: "123,456") ou lista de strings/ints. Fazemos parsing defensivamente.
    """
    if raw_tags is None:
        return []

    if isinstance(raw_tags, str):
        parts = [p.strip() for p in raw_tags.split(",") if p.strip()]
    elif isinstance(raw_tags, (list, tuple)):
        parts = []
        for item in raw_tags:
            if item is None:
                continue
            parts.append(str(item).strip())
    else:
        return []

    tag_ids: List[int] = []
    for p in parts:
        try:
            tag_ids.append(int(p))
        except (TypeError, ValueError):
            continue
    return tag_ids


def fetch_nba_sport_metadata(
    req: NBASportMetadataRequest | None = None,
    client: GammaClient | None = None,
) -> NBASportMetadata:
    """
    Busca /sports na Gamma e retorna a entrada da NBA.
    """
    if req is None:
        req = NBASportMetadataRequest()

    client = client or get_default_client()
    sports = client.get_sports()

    target = None
    for s in sports:
        sport_label = str(s.get("sport") or s.get("label") or "").lower()
        if sport_label == req.sport.lower():
            target = s
            break

    if not target:
        raise ValueError(f"NBA sport metadata not found for sport={req.sport!r}")

    tag_ids = _parse_tag_ids(target.get("tags"))
    resolution_val = target.get("resolutionSource") or target.get("resolution")

    return NBASportMetadata(
        sport=req.sport.lower(),
        image=target.get("image"),
        resolution=resolution_val,
        ordering=str(target.get("ordering")) if target.get("ordering") is not None else None,
        tag_ids=tag_ids,
        raw=target,
    )


def get_nba_tag_ids(
    client: GammaClient | None = None,
    include_root: bool = False,
) -> List[int]:
    """
    Convenience – retorna lista de tag_ids para NBA.

    Heurística:
    - Se include_root=False, removemos tag_id 1 (tipicamente "root" global).
    - Se isso esvaziar a lista, devolvemos todos os tag_ids originais.
    """
    meta = fetch_nba_sport_metadata(client=client)
    if not meta.tag_ids:
        return []

    if include_root:
        return list(meta.tag_ids)

    filtered = [t for t in meta.tag_ids if t != 1]
    return filtered or list(meta.tag_ids)


if __name__ == "__main__":
    from pprint import pprint

    meta = fetch_nba_sport_metadata()
    print("NBA Sport Metadata:")
    pprint(meta.model_dump())
    print("NBA tag_ids (sem root):", get_nba_tag_ids())
