from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

from crypto_options_app.config import CENTRAL_DB_PATH
from crypto_options_app.db.connection import connect
from crypto_options_app.db.schema import create_schema, initialize_schema
from crypto_options_app.feeds.polymarket_prices import normalize_polymarket_trade_print, insert_polymarket_trade_print
from crypto_options_app.workers.feed_worker import FeedWorkerConfig, write_watermark


LIVE_FLAG_NAMES = (
    "JANUS_CRYPTO_OPTIONS_LIVE_EXECUTE",
    "JANUS_CRYPTO_OPTIONS_LIVE_APPROVED",
    "JANUS_CRYPTO_OPTIONS_ACK_LIVE_RISK",
)


@dataclass(frozen=True)
class LiveActivityCaptureConfig:
    db_path: Path = CENTRAL_DB_PATH
    host: str = "https://clob.polymarket.com"
    lookback_minutes: int = 15
    lookahead_minutes: int = 15
    max_markets: int = 80
    max_concurrency: int = 6
    timeout_seconds: float = 5.0
    module_id: str = "polymarket_live_activity_capture"


@dataclass(frozen=True)
class LiveActivityCaptureSummary:
    generated_at_utc: str
    status: str
    db_path: str
    condition_count: int
    trade_rows_inserted: int
    blockers: tuple[str, ...] = ()
    state: dict[str, Any] = field(default_factory=dict)
    orders_allowed: bool = False
    live_trading_authorized: bool = False


MarketActivityFetcher = Callable[[str], dict[str, Any] | list[Any]]


def fetch_market_trades_events(
    condition_id: str,
    *,
    host: str = "https://clob.polymarket.com",
    timeout_seconds: float = 5.0,
) -> dict[str, Any] | list[Any]:
    url = f"{host.rstrip('/')}/markets/live-activity/{condition_id}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "crypto-options-app-readonly-data-service/0.1"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - official configured host.
        return json.loads(response.read().decode("utf-8"))


async def capture_live_activity_once(
    *,
    config: LiveActivityCaptureConfig,
    condition_ids: Iterable[str] | None = None,
    activity_fetcher: MarketActivityFetcher | None = None,
) -> LiveActivityCaptureSummary:
    _reject_live_env_flags()
    initialize_schema(config.db_path)
    inserted_at = datetime.now(UTC)
    fetcher = activity_fetcher or (lambda condition_id: fetch_market_trades_events(
        condition_id,
        host=config.host,
        timeout_seconds=config.timeout_seconds,
    ))
    with connect(config.db_path) as conn:
        create_schema(conn)
        ids = list(condition_ids) if condition_ids is not None else _discover_recent_condition_ids(conn, config=config)

    bounded_ids = ids[: max(0, config.max_markets)]
    semaphore = asyncio.Semaphore(max(1, config.max_concurrency))

    async def fetch_one(condition_id: str) -> tuple[str, dict[str, Any] | list[Any] | None, str | None]:
        async with semaphore:
            try:
                return condition_id, await asyncio.to_thread(fetcher, condition_id), None
            except Exception as exc:  # noqa: BLE001 - data service persists provider errors.
                return condition_id, None, f"{condition_id}:{type(exc).__name__}:{exc}"

    fetched = await asyncio.gather(*(fetch_one(condition_id) for condition_id in bounded_ids))
    errors: list[str] = []
    inserted = 0
    with connect(config.db_path) as conn:
        create_schema(conn)
        token_map = _event_token_map(conn)
        for condition_id, payload, error in fetched:
            if error:
                errors.append(error)
                continue
            for raw_trade in _iter_trade_payloads(payload):
                token_id = _raw_token_id(raw_trade)
                if token_id is None:
                    errors.append(f"{condition_id}:trade_missing_token_id")
                    continue
                try:
                    trade = normalize_polymarket_trade_print(
                        raw_trade,
                        event_token_key=token_map.get(token_id),
                        inserted_at_utc=inserted_at,
                    )
                except (TypeError, ValueError) as exc:
                    errors.append(f"{condition_id}:{type(exc).__name__}:{exc}")
                    continue
                before = conn.total_changes
                insert_polymarket_trade_print(conn, trade)
                if conn.total_changes > before:
                    inserted += 1

        status = "failed" if errors and len(errors) >= max(1, len(bounded_ids)) else "degraded" if errors else "healthy"
        write_watermark(
            conn,
            service_name="crypto_options_app",
            module_id=config.module_id,
            status=status,
            last_run_at_utc=inserted_at.isoformat(),
            rows_observed=len(bounded_ids),
            rows_inserted=inserted,
            error_count=len(errors),
            source="polymarket_clob_live_activity",
            state={
                "endpoint": "/markets/live-activity/{condition_id}",
                "condition_count": len(bounded_ids),
                "trade_rows_inserted": inserted,
                "orders_allowed": False,
                "live_trading_authorized": False,
                "errors": errors[:20],
            },
        )

    return LiveActivityCaptureSummary(
        generated_at_utc=inserted_at.isoformat(),
        status=status,
        db_path=str(config.db_path),
        condition_count=len(bounded_ids),
        trade_rows_inserted=inserted,
        blockers=tuple(errors),
        state={"endpoint": "/markets/live-activity/{condition_id}"},
    )


def capture_live_activity_once_sync(
    *,
    config: LiveActivityCaptureConfig,
    condition_ids: Iterable[str] | None = None,
    activity_fetcher: MarketActivityFetcher | None = None,
) -> LiveActivityCaptureSummary:
    return asyncio.run(
        capture_live_activity_once(
            config=config,
            condition_ids=condition_ids,
            activity_fetcher=activity_fetcher,
        )
    )


def feed_worker_config(config: LiveActivityCaptureConfig) -> FeedWorkerConfig:
    return FeedWorkerConfig(
        service_name="crypto_options_app",
        module_id=config.module_id,
        data_type="polymarket_market_activity",
        provider="polymarket_clob",
        transport="rest",
        max_concurrency=config.max_concurrency,
        stale_after_seconds=30,
        read_only=True,
        metadata={
            "official_source": "Polymarket/py-clob-client-v2 GET_MARKET_TRADES_EVENTS",
            "writes": ["polymarket_trade_prints", "data_service_watermarks"],
            "orders_allowed": False,
        },
    )


def _discover_recent_condition_ids(conn: Any, *, config: LiveActivityCaptureConfig) -> list[str]:
    now = datetime.now(UTC)
    lower = (now - timedelta(minutes=max(0, config.lookback_minutes))).isoformat()
    upper = (now + timedelta(minutes=max(0, config.lookahead_minutes))).isoformat()
    rows = conn.execute(
        """
        SELECT DISTINCT condition_id
        FROM events
        WHERE condition_id IS NOT NULL
          AND source_table = 'polymarket_option_price_capture'
          AND (event_end_time_utc IS NULL OR event_end_time_utc >= ?)
          AND (event_start_time_utc IS NULL OR event_start_time_utc <= ?)
        ORDER BY event_start_time_utc
        LIMIT ?
        """,
        (lower, upper, max(0, config.max_markets)),
    ).fetchall()
    return [str(row["condition_id"]) for row in rows if row["condition_id"]]


def _event_token_map(conn: Any) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT token_id, event_token_key
        FROM event_tokens
        WHERE token_id IS NOT NULL
        """
    ).fetchall()
    return {str(row["token_id"]): str(row["event_token_key"]) for row in rows}


def _iter_trade_payloads(payload: dict[str, Any] | list[Any] | None) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("trades", "events", "activity", "data", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    nested = payload.get("payload")
    if isinstance(nested, dict):
        return [nested]
    if any(key in payload for key in ("token_id", "asset_id", "asset", "price", "size")):
        return [payload]
    return []


def _raw_token_id(raw: dict[str, Any]) -> str | None:
    value = raw.get("token_id") or raw.get("asset_id") or raw.get("asset") or raw.get("market")
    return None if value is None else str(value)


def _reject_live_env_flags() -> None:
    enabled = [name for name in LIVE_FLAG_NAMES if str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}]
    if enabled:
        raise RuntimeError(f"data_service_live_flags_rejected:{','.join(enabled)}")
