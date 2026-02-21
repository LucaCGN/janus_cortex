"""Prediction-market/Polymarket ingestion pipeline wrappers."""

from app.data.pipelines.daily.polymarket.sync_events import main as sync_events_main
from app.data.pipelines.daily.polymarket.sync_markets import main as sync_markets_main
from app.data.pipelines.daily.polymarket.sync_portfolio import main as sync_portfolio_main
from app.data.pipelines.daily.polymarket.backfill_retry import main as backfill_retry_main

__all__ = [
    "backfill_retry_main",
    "sync_events_main",
    "sync_markets_main",
    "sync_portfolio_main",
]
