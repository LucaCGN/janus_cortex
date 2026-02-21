"""Cross-domain ingestion pipeline wrappers."""

from app.data.pipelines.daily.cross_domain.sync_mappings import main as sync_mappings_main

__all__ = [
    "sync_mappings_main",
]
