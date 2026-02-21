"""Sports/NBA ingestion pipeline wrappers."""

from app.data.pipelines.daily.nba.sync_db import main as sync_db_main
from app.data.pipelines.daily.nba.sync_postgres import main as sync_postgres_main

__all__ = [
    "sync_db_main",
    "sync_postgres_main",
]
