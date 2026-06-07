"""Modular crypto-options application package."""

from crypto_options_app.api.app import create_app
from crypto_options_app.config import CryptoOptionsAppConfig

__all__ = ["CryptoOptionsAppConfig", "create_app"]
