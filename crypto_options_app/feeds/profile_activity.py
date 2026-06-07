from __future__ import annotations

from collections.abc import Iterable

from crypto_options_app.workers.feed_worker import FeedWorkerConfig


def profile_activity_feed_config(*, max_concurrency: int = 8, queue_maxsize: int = 512) -> FeedWorkerConfig:
    return FeedWorkerConfig(
        service_name="crypto_options_app",
        module_id="profile_activity",
        data_type="profile_activity",
        provider="polymarket_profiles",
        transport="rest",
        max_concurrency=max_concurrency,
        queue_maxsize=queue_maxsize,
        stale_after_seconds=3600,
        metadata={"writes": ["profiles", "profile_raw_activity", "profile_fetch_runs"]},
    )


def normalize_profile_refs(refs: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        value = ref.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized
