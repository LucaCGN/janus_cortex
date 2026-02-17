"""Canonical mapping adapter wrappers."""

from app.data.pipelines.canonical.adapters.gamma_nba import adapt_gamma_nba_to_canonical
from app.data.pipelines.canonical.adapters.nba_schedule import attach_nba_schedule_context

__all__ = [
    "adapt_gamma_nba_to_canonical",
    "attach_nba_schedule_context",
]

