"""Gamma API connector wrappers."""

from app.data.nodes.polymarket.gamma.gamma_client import GammaClient, get_default_client

__all__ = [
    "GammaClient",
    "get_default_client",
]

