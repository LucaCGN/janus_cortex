from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from crypto_options_app.db.connection import connect
from crypto_options_app.db.schema import create_schema


WATERMARK_STATUSES = {"healthy", "warming", "stale", "degraded", "failed"}
TRANSPORT_TYPES = {"rest", "websocket", "compute"}


@dataclass(frozen=True)
class FeedWorkerConfig:
    service_name: str
    module_id: str
    data_type: str
    provider: str
    transport: str
    max_concurrency: int = 4
    queue_maxsize: int = 256
    stale_after_seconds: int = 90
    read_only: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.service_name.strip():
            raise ValueError("service_name is required")
        if not self.module_id.strip():
            raise ValueError("module_id is required")
        if self.transport not in TRANSPORT_TYPES:
            raise ValueError(f"unsupported transport: {self.transport}")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if self.queue_maxsize < 1:
            raise ValueError("queue_maxsize must be at least 1")
        if self.stale_after_seconds < 1:
            raise ValueError("stale_after_seconds must be at least 1")
        if not self.read_only:
            raise ValueError("feed workers must remain read-only")


@dataclass(frozen=True)
class FeedTaskResult:
    rows_observed: int = 0
    rows_inserted: int = 0
    state: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FeedRunSummary:
    worker_run_id: str
    config: FeedWorkerConfig
    started_at_utc: str
    completed_at_utc: str
    status: str
    rows_observed: int
    rows_inserted: int
    error_count: int
    state: Mapping[str, Any]
    orders_allowed: bool = False
    live_trading_authorized: bool = False


@dataclass(frozen=True)
class StalenessReport:
    service_name: str
    module_id: str
    data_type: str
    status: str
    age_seconds: float | None
    stale_after_seconds: int
    last_run_at_utc: str | None


FeedTaskHandler = Callable[[Any], FeedTaskResult | Mapping[str, Any] | Awaitable[FeedTaskResult | Mapping[str, Any]]]


DEFAULT_FEED_CONFIGS: dict[str, FeedWorkerConfig] = {
    "profile_activity": FeedWorkerConfig(
        service_name="crypto_options_app",
        module_id="profile_activity",
        data_type="profile_activity",
        provider="polymarket_profiles",
        transport="rest",
        max_concurrency=8,
        stale_after_seconds=3600,
        metadata={"writes": ["profiles", "profile_raw_activity", "profile_fetch_runs"]},
    ),
    "event_universe": FeedWorkerConfig(
        service_name="crypto_options_app",
        module_id="event_universe",
        data_type="event_universe",
        provider="polymarket_gamma_clob",
        transport="rest",
        max_concurrency=4,
        stale_after_seconds=120,
        metadata={"captures_future_events": True, "writes": ["events", "event_tokens", "event_outcomes"]},
    ),
    "polymarket_prices": FeedWorkerConfig(
        service_name="crypto_options_app",
        module_id="polymarket_prices",
        data_type="polymarket_event_prices",
        provider="polymarket_clob",
        transport="websocket",
        max_concurrency=1,
        stale_after_seconds=30,
        metadata={"rest_fallback_module_id": "polymarket_prices_rest_fallback"},
    ),
    "underlying_prices": FeedWorkerConfig(
        service_name="crypto_options_app",
        module_id="underlying_prices",
        data_type="underlying_prices",
        provider="crypto_exchange",
        transport="websocket",
        max_concurrency=1,
        stale_after_seconds=15,
        metadata={"writes": ["underlying_price_ticks", "underlying_candles"]},
    ),
    "indicators": FeedWorkerConfig(
        service_name="crypto_options_app",
        module_id="indicators",
        data_type="indicator_snapshots",
        provider="canonical_db",
        transport="compute",
        max_concurrency=2,
        stale_after_seconds=60,
        metadata={"writes": ["indicator_snapshots", "event_indicator_context"]},
    ),
    "reconstruction": FeedWorkerConfig(
        service_name="crypto_options_app",
        module_id="reconstruction",
        data_type="profile_event_reconstruction",
        provider="canonical_db",
        transport="compute",
        max_concurrency=4,
        stale_after_seconds=300,
        metadata={"writes": ["profile_event_orders", "profile_event_reconstructions", "profile_event_styles"]},
    ),
    "signal_aggregation": FeedWorkerConfig(
        service_name="crypto_options_app",
        module_id="signal_aggregation",
        data_type="signal_aggregation",
        provider="canonical_db",
        transport="compute",
        max_concurrency=2,
        stale_after_seconds=60,
        metadata={"writes": ["profile_generator_scores"]},
    ),
}


async def run_bounded_feed_batch(
    *,
    config: FeedWorkerConfig,
    items: Iterable[Any],
    handler: FeedTaskHandler,
    db_path: str | Path,
) -> FeedRunSummary:
    config.validate()
    queued_items = list(items)
    if len(queued_items) > config.queue_maxsize:
        raise ValueError("feed item count exceeds queue_maxsize")

    started_at = _now()
    worker_run_id = f"{config.module_id}-{uuid4().hex}"
    semaphore = asyncio.Semaphore(config.max_concurrency)
    errors: list[dict[str, Any]] = []

    async def run_one(item: Any) -> FeedTaskResult:
        async with semaphore:
            try:
                result = handler(item)
                if inspect.isawaitable(result):
                    result = await result
                return _normalize_task_result(result)
            except Exception as exc:  # noqa: BLE001 - feed errors must be persisted by class.
                errors.append({"error_class": exc.__class__.__name__, "message": str(exc), "item": repr(item)})
                return FeedTaskResult(rows_observed=1, rows_inserted=0, state={"failed": True})

    results = await asyncio.gather(*(run_one(item) for item in queued_items))
    rows_observed = sum(result.rows_observed for result in results)
    rows_inserted = sum(result.rows_inserted for result in results)
    completed_at = _now()
    if errors and len(errors) == len(queued_items):
        status = "failed"
    elif errors:
        status = "degraded"
    elif queued_items:
        status = "healthy"
    else:
        status = "warming"
    state = {
        "transport": config.transport,
        "provider": config.provider,
        "items_queued": len(queued_items),
        "errors": errors,
        "task_states": [dict(result.state) for result in results if result.state],
        "orders_allowed": False,
        "live_trading_authorized": False,
    }
    summary = FeedRunSummary(
        worker_run_id=worker_run_id,
        config=config,
        started_at_utc=started_at,
        completed_at_utc=completed_at,
        status=status,
        rows_observed=rows_observed,
        rows_inserted=rows_inserted,
        error_count=len(errors),
        state=state,
    )
    with connect(db_path) as conn:
        create_schema(conn)
        write_worker_run(conn, summary)
        write_watermark(
            conn,
            service_name=config.service_name,
            module_id=config.module_id,
            status=status,
            last_run_at_utc=completed_at,
            rows_observed=rows_observed,
            rows_inserted=rows_inserted,
            error_count=len(errors),
            state=state,
            source=config.provider,
        )
    return summary


def write_worker_run(conn: Any, summary: FeedRunSummary) -> None:
    conn.execute(
        """
        INSERT INTO worker_runs(
            worker_run_id, worker_name, started_at_utc, completed_at_utc, status,
            rows_observed, rows_inserted, error_count, summary_json, inserted_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            summary.worker_run_id,
            summary.config.module_id,
            summary.started_at_utc,
            summary.completed_at_utc,
            summary.status,
            summary.rows_observed,
            summary.rows_inserted,
            summary.error_count,
            json.dumps(summary.state, sort_keys=True, default=str),
            _now(),
        ),
    )


def write_watermark(
    conn: Any,
    *,
    service_name: str,
    module_id: str,
    status: str,
    last_run_at_utc: str | None = None,
    rows_observed: int = 0,
    rows_inserted: int = 0,
    error_count: int = 0,
    state: Mapping[str, Any] | None = None,
    source: str | None = None,
) -> None:
    if status not in WATERMARK_STATUSES:
        raise ValueError(f"unsupported watermark status: {status}")
    updated_at = _now()
    conn.execute(
        """
        INSERT INTO data_service_watermarks(
            service_name, module_id, last_run_at_utc, status, source,
            rows_observed, rows_inserted, error_count, state_json, updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(service_name, module_id) DO UPDATE SET
            last_run_at_utc=excluded.last_run_at_utc,
            status=excluded.status,
            source=excluded.source,
            rows_observed=excluded.rows_observed,
            rows_inserted=excluded.rows_inserted,
            error_count=excluded.error_count,
            state_json=excluded.state_json,
            updated_at_utc=excluded.updated_at_utc
        """,
        (
            service_name,
            module_id,
            last_run_at_utc or updated_at,
            status,
            source,
            rows_observed,
            rows_inserted,
            error_count,
            json.dumps(dict(state or {}), sort_keys=True, default=str),
            updated_at,
        ),
    )


def read_watermark(conn: Any, *, service_name: str, module_id: str) -> Mapping[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM data_service_watermarks
        WHERE service_name=? AND module_id=?
        """,
        (service_name, module_id),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def evaluate_staleness(
    conn: Any,
    configs: Iterable[FeedWorkerConfig],
    *,
    now_utc: datetime | None = None,
) -> list[StalenessReport]:
    now = now_utc or datetime.now(UTC)
    reports: list[StalenessReport] = []
    for config in configs:
        watermark = read_watermark(conn, service_name=config.service_name, module_id=config.module_id)
        if watermark is None:
            reports.append(
                StalenessReport(
                    service_name=config.service_name,
                    module_id=config.module_id,
                    data_type=config.data_type,
                    status="stale",
                    age_seconds=None,
                    stale_after_seconds=config.stale_after_seconds,
                    last_run_at_utc=None,
                )
            )
            continue
        last_run = _parse_datetime(str(watermark["last_run_at_utc"]))
        age_seconds = (now - last_run).total_seconds()
        status = "stale" if age_seconds > config.stale_after_seconds else str(watermark["status"])
        reports.append(
            StalenessReport(
                service_name=config.service_name,
                module_id=config.module_id,
                data_type=config.data_type,
                status=status,
                age_seconds=age_seconds,
                stale_after_seconds=config.stale_after_seconds,
                last_run_at_utc=str(watermark["last_run_at_utc"]),
            )
        )
    return reports


def default_feed_configs() -> list[FeedWorkerConfig]:
    return list(DEFAULT_FEED_CONFIGS.values())


def _normalize_task_result(result: FeedTaskResult | Mapping[str, Any]) -> FeedTaskResult:
    if isinstance(result, FeedTaskResult):
        return result
    return FeedTaskResult(
        rows_observed=int(result.get("rows_observed", 0)),
        rows_inserted=int(result.get("rows_inserted", 0)),
        state=dict(result.get("state", {})),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def stale_time(seconds_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()
