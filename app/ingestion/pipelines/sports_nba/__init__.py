"""Sports/NBA ingestion pipeline wrappers."""

from app.data.pipelines.daily.nba.sync_db import main as sync_db_main

__all__ = [
    "sync_db_main",
]

