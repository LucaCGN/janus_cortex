from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from crypto_options_app.db.connection import connect, count_rows
from crypto_options_app.db.schema import create_schema
from crypto_options_app.feeds.polymarket_events import filter_discoverable_events
from crypto_options_app.workers.feed_worker import (
    FeedTaskResult,
    FeedWorkerConfig,
    default_feed_configs,
    evaluate_staleness,
    run_bounded_feed_batch,
    stale_time,
    write_watermark,
)


def test_default_feed_configs_cover_required_workers_pytest() -> None:
    configs = default_feed_configs()
    module_ids = {config.module_id for config in configs}

    assert module_ids == {
        "profile_activity",
        "event_universe",
        "polymarket_prices",
        "underlying_prices",
        "indicators",
        "reconstruction",
        "signal_aggregation",
    }
    for config in configs:
        config.validate()
        assert config.read_only is True
    assert {config.transport for config in configs if config.transport == "websocket"} == {"websocket"}


def test_feed_worker_config_validation_keeps_feeds_read_only_pytest() -> None:
    with pytest.raises(ValueError, match="max_concurrency"):
        FeedWorkerConfig(
            service_name="crypto_options_app",
            module_id="bad",
            data_type="bad",
            provider="fixture",
            transport="rest",
            max_concurrency=0,
        ).validate()

    with pytest.raises(ValueError, match="read-only"):
        FeedWorkerConfig(
            service_name="crypto_options_app",
            module_id="bad",
            data_type="bad",
            provider="fixture",
            transport="rest",
            read_only=False,
        ).validate()


def test_bounded_feed_batch_respects_semaphore_and_writes_watermark_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "feeds.sqlite"
    current = 0
    max_seen = 0

    async def handler(item: int) -> FeedTaskResult:
        nonlocal current, max_seen
        current += 1
        max_seen = max(max_seen, current)
        await asyncio.sleep(0.01)
        current -= 1
        return FeedTaskResult(rows_observed=1, rows_inserted=1, state={"item": item})

    config = FeedWorkerConfig(
        service_name="crypto_options_app",
        module_id="fixture_profile_activity",
        data_type="profile_activity",
        provider="fixture",
        transport="rest",
        max_concurrency=2,
    )
    summary = asyncio.run(
        run_bounded_feed_batch(config=config, items=range(5), handler=handler, db_path=db_path)
    )

    assert max_seen <= 2
    assert summary.status == "healthy"
    assert summary.rows_observed == 5
    assert summary.rows_inserted == 5
    assert summary.error_count == 0
    assert summary.orders_allowed is False
    with connect(db_path) as conn:
        assert count_rows(conn, "worker_runs") == 1
        row = conn.execute(
            """
            SELECT status, rows_observed, rows_inserted, error_count, state_json
            FROM data_service_watermarks
            WHERE service_name='crypto_options_app' AND module_id='fixture_profile_activity'
            """
        ).fetchone()
        assert row["status"] == "healthy"
        assert row["rows_observed"] == 5
        assert row["rows_inserted"] == 5
        assert row["error_count"] == 0
        assert "live_trading_authorized" in row["state_json"]


def test_feed_watermarks_support_healthy_stale_degraded_failed_pytest(tmp_path: Path) -> None:
    db_path = tmp_path / "watermarks.sqlite"
    with connect(db_path) as conn:
        create_schema(conn)
        for status in ("healthy", "stale", "degraded", "failed"):
            write_watermark(
                conn,
                service_name="crypto_options_app",
                module_id=f"fixture_{status}",
                status=status,
                rows_observed=1,
                rows_inserted=0 if status == "failed" else 1,
                error_count=1 if status in {"degraded", "failed"} else 0,
                state={"status_under_test": status},
            )

        rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM data_service_watermarks GROUP BY status"
        ).fetchall()
        status_counts = {row["status"]: row["c"] for row in rows}
        assert status_counts == {"healthy": 1, "stale": 1, "degraded": 1, "failed": 1}

        write_watermark(
            conn,
            service_name="crypto_options_app",
            module_id="old_profile_activity",
            status="healthy",
            last_run_at_utc=stale_time(500),
        )
        reports = evaluate_staleness(
            conn,
            [
                FeedWorkerConfig(
                    service_name="crypto_options_app",
                    module_id="old_profile_activity",
                    data_type="profile_activity",
                    provider="fixture",
                    transport="rest",
                    stale_after_seconds=60,
                )
            ],
            now_utc=datetime.now(UTC),
        )
        assert reports[0].status == "stale"
        assert reports[0].age_seconds is not None
        assert reports[0].age_seconds > 60


def test_future_event_discovery_keeps_pre_event_capture_pytest() -> None:
    now = datetime(2026, 6, 3, 1, 0, tzinfo=UTC)
    events = [
        {
            "event_slug": "current",
            "event_start_time_utc": (now - timedelta(minutes=1)).isoformat(),
            "event_end_time_utc": (now + timedelta(minutes=4)).isoformat(),
        },
        {
            "event_slug": "future",
            "event_start_time_utc": (now + timedelta(minutes=3)).isoformat(),
            "event_end_time_utc": (now + timedelta(minutes=8)).isoformat(),
        },
        {
            "event_slug": "recently-ended",
            "event_start_time_utc": (now - timedelta(minutes=8)).isoformat(),
            "event_end_time_utc": (now - timedelta(minutes=2)).isoformat(),
        },
        {
            "event_slug": "old-ended",
            "event_start_time_utc": (now - timedelta(hours=2)).isoformat(),
            "event_end_time_utc": (now - timedelta(hours=1)).isoformat(),
        },
    ]

    selected = filter_discoverable_events(events, now_utc=now, recently_ended_window_seconds=600)

    assert {event["event_slug"] for event in selected} == {"current", "future", "recently-ended"}
