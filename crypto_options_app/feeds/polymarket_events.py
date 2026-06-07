from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from crypto_options_app.workers.feed_worker import FeedWorkerConfig


def event_universe_feed_config(*, max_concurrency: int = 4) -> FeedWorkerConfig:
    return FeedWorkerConfig(
        service_name="crypto_options_app",
        module_id="event_universe",
        data_type="event_universe",
        provider="polymarket_gamma_clob",
        transport="rest",
        max_concurrency=max_concurrency,
        stale_after_seconds=120,
        metadata={"captures_future_events": True, "writes": ["events", "event_tokens", "event_outcomes"]},
    )


def filter_discoverable_events(
    events: Iterable[Mapping[str, Any]],
    *,
    now_utc: datetime,
    recently_ended_window_seconds: int = 600,
    include_future: bool = True,
) -> list[Mapping[str, Any]]:
    selected: list[Mapping[str, Any]] = []
    recently_ended_cutoff = now_utc - timedelta(seconds=recently_ended_window_seconds)
    for event in events:
        start_at = _parse_optional_datetime(event.get("event_start_time_utc") or event.get("start_time_utc"))
        end_at = _parse_optional_datetime(event.get("event_end_time_utc") or event.get("end_time_utc"))
        is_future = start_at is not None and start_at > now_utc
        is_current = (start_at is None or start_at <= now_utc) and (end_at is None or end_at >= now_utc)
        is_recently_ended = end_at is not None and recently_ended_cutoff <= end_at < now_utc
        if is_current or is_recently_ended or (include_future and is_future):
            selected.append(event)
    return selected


def _parse_optional_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
