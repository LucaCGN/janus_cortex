from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


CENTRAL_APP_ROOT = Path("crypto_options_app")
CENTRAL_DATA_ROOT = CENTRAL_APP_ROOT / "data"
CENTRAL_ARTIFACT_ROOT = CENTRAL_APP_ROOT / "artifacts"
CENTRAL_DOCS_ROOT = CENTRAL_APP_ROOT / "docs" / "reference" / "crypto_options"
CENTRAL_DB_PATH = CENTRAL_DATA_ROOT / "crypto_options_data.sqlite"
CENTRAL_ACTIVE_PROFILE_POOL = CENTRAL_DATA_ROOT / "profile-pool" / "active_crypto_profile_pool.txt"
CENTRAL_POSTGRES_HOST = "127.0.0.1"
CENTRAL_POSTGRES_PORT = 55433
CENTRAL_POSTGRES_DB = "crypto_options"
CENTRAL_POSTGRES_USER = "crypto_options"
CENTRAL_POSTGRES_PASSWORD = "crypto_options_dev"
CENTRAL_POSTGRES_URL = (
    f"postgresql://{CENTRAL_POSTGRES_USER}:{CENTRAL_POSTGRES_PASSWORD}"
    f"@{CENTRAL_POSTGRES_HOST}:{CENTRAL_POSTGRES_PORT}/{CENTRAL_POSTGRES_DB}"
)
CENTRAL_REDIS_HOST = "127.0.0.1"
CENTRAL_REDIS_PORT = 56379
CENTRAL_REDIS_URL = f"redis://{CENTRAL_REDIS_HOST}:{CENTRAL_REDIS_PORT}/0"


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CryptoOptionsAppConfig:
    """Runtime configuration for the isolated crypto-options app."""

    app_name: str = "crypto-options-app"
    api_version: str = "0.1.0"
    db_path: Path = CENTRAL_DB_PATH
    artifact_root: Path = CENTRAL_ARTIFACT_ROOT
    active_profile_pool_path: Path = CENTRAL_ACTIVE_PROFILE_POOL
    docs_root: Path = CENTRAL_DOCS_ROOT
    database_backend: str = os.getenv("JANUS_CRYPTO_OPTIONS_DATABASE_BACKEND", "postgres")
    postgres_database_url: str = os.getenv("JANUS_CRYPTO_OPTIONS_POSTGRES_URL", CENTRAL_POSTGRES_URL)
    redis_url: str = os.getenv("JANUS_CRYPTO_OPTIONS_REDIS_URL", CENTRAL_REDIS_URL)
    frontend_origin: str = os.getenv("JANUS_CRYPTO_OPTIONS_FRONTEND_ORIGIN", "http://127.0.0.1:8012")
    live_trading_authorized: bool = False
    orders_allowed: bool = False
    postgres_enabled: bool = _env_flag("JANUS_CRYPTO_OPTIONS_POSTGRES_ENABLED", True)
    redis_enabled: bool = _env_flag("JANUS_CRYPTO_OPTIONS_REDIS_ENABLED", False)


DEFAULT_CONFIG = CryptoOptionsAppConfig()
