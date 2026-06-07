from __future__ import annotations

"""Read-only Polymarket event price feed for crypto options.

This service captures public CLOB market-channel updates for known crypto
Up/Down event tokens. It records provider/chart timestamps, local receive time,
local insert time, and quote/trade deltas so downstream analysis can measure
both price fluctuation and feed latency. It never authenticates and never
places, cancels, signs, broadcasts, routes, recommends, or authorizes orders.
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crypto_options_app.data_nodes.polymarket_crypto.history import fetch_current_order_book, normalize_order_book_snapshot
from crypto_options_app.data_nodes.polymarket_crypto.live_capture import (
    POLYMARKET_MARKET_WS_URL,
    LiveCaptureTarget,
    _normalize_ws_payload,
    discover_live_crypto_updown_targets,
)
from crypto_options_app.pipelines.options.market_data_store import (
    default_market_data_store_path,
    record_price_ingest_run,
    upsert_polymarket_event_price_ticks,
    upsert_polymarket_event_universe,
)
from crypto_options_app.pipelines.options.profile_store import refresh_profile_event_timing_links


DEFAULT_EVENT_PRICE_SYMBOLS = ("BTC", "ETH")


def live_capture_targets_to_event_rows(targets: list[LiveCaptureTarget]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target in targets:
        rows.append(
            {
                "event_id": target.event_id,
                "event_slug": target.event_slug,
                "market_id": target.market_id,
                "condition_id": target.condition_id,
                "market_slug": target.event_slug,
                "symbol": target.symbol,
                "cadence_seconds": 300,
                "outcome": target.outcome,
                "token_id": target.token_id,
                "event_start_time_utc": target.window_start_time,
                "event_end_time_utc": target.window_end_time,
                "settlement_threshold": target.settlement_threshold,
                "active": True,
                "closed": False,
                "source": "polymarket_gamma_crypto_event_discovery",
                "raw_json": target.__dict__,
            }
        )
    return rows


def normalize_polymarket_ws_records(
    raw: Any,
    *,
    target_by_token: dict[str, LiveCaptureTarget],
    received_at: datetime | None = None,
) -> list[dict[str, Any]]:
    received_at = received_at or datetime.now(timezone.utc)
    payload = _loads_ws_message(raw)
    records = payload if isinstance(payload, list) else [payload]
    rows: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        for row in _normalize_ws_payload(record, received_at, target_by_token):
            rows.append(_event_price_row_from_normalized(row, received_at=received_at))
    return rows


def discover_and_store_polymarket_event_universe(
    *,
    symbols: list[str] | tuple[str, ...] = DEFAULT_EVENT_PRICE_SYMBOLS,
    cadence_minutes: int = 5,
    lookback_minutes: int = 30,
    lookahead_minutes: int = 180,
    db_path: str | Path | None = None,
    profile_db_path: str | Path | None = None,
    link_profiles: bool = True,
) -> dict[str, Any]:
    targets, discovery = discover_live_crypto_updown_targets(
        symbols=[symbol.upper() for symbol in symbols],
        cadence_minutes=int(cadence_minutes),
        lookback_minutes=int(lookback_minutes),
        lookahead_minutes=int(lookahead_minutes),
    )
    event_rows = live_capture_targets_to_event_rows(targets)
    stored = upsert_polymarket_event_universe(event_rows, db_path=db_path)
    profile_links: dict[str, Any] | None = None
    if link_profiles:
        profile_links = refresh_profile_event_timing_links(event_rows, db_path=profile_db_path)
    return {
        "schema_version": "crypto_options_polymarket_event_discovery_v1",
        "symbols": [symbol.upper() for symbol in symbols],
        "target_count": len(targets),
        "event_token_rows_upserted": stored,
        "discovery": discovery,
        "profile_event_timing_links": profile_links,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


async def capture_polymarket_event_price_stream(
    *,
    symbols: list[str] | tuple[str, ...] = DEFAULT_EVENT_PRICE_SYMBOLS,
    seconds: float = 30.0,
    cadence_minutes: int = 5,
    lookback_minutes: int = 30,
    lookahead_minutes: int = 180,
    max_tokens: int = 120,
    db_path: str | Path | None = None,
    profile_db_path: str | Path | None = None,
    book_poll_seconds: float = 0.0,
    max_book_poll_tokens: int = 12,
) -> dict[str, Any]:
    """Capture public Polymarket event prices for current and near-future tokens."""

    import websockets

    started = _now()
    run_id = f"polymarket-event-price-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
    db_path = db_path or default_market_data_store_path()
    targets, discovery = discover_live_crypto_updown_targets(
        symbols=[symbol.upper() for symbol in symbols],
        cadence_minutes=int(cadence_minutes),
        lookback_minutes=int(lookback_minutes),
        lookahead_minutes=int(lookahead_minutes),
    )
    targets = _select_capture_targets(targets, max_tokens=max_tokens)
    target_by_token = {target.token_id: target for target in targets}
    event_rows = live_capture_targets_to_event_rows(targets)
    event_rows_upserted = upsert_polymarket_event_universe(event_rows, db_path=db_path)
    profile_links = refresh_profile_event_timing_links(event_rows, db_path=profile_db_path)

    if not targets:
        completed = _now()
        record_price_ingest_run(
            run_id=run_id,
            started_at_utc=started,
            completed_at_utc=completed,
            status="blocked",
            source="polymarket_event_market_ws",
            symbols=[symbol.upper() for symbol in symbols],
            rows_observed=0,
            rows_inserted=0,
            error_count=1,
            summary={"reason": "no_polymarket_crypto_event_tokens", "discovery": discovery},
            db_path=db_path,
        )
        return {
            "schema_version": "crypto_options_polymarket_event_price_capture_v1",
            "run_id": run_id,
            "status": "blocked",
            "reason": "no_polymarket_crypto_event_tokens",
            "orders_allowed": False,
            "live_trading_authorized": False,
        }

    deadline = time.monotonic() + max(float(seconds), 0.0)
    rows_observed = 0
    rows_inserted = 0
    error_count = 0
    last_error: str | None = None
    raw_message_count = 0
    book_poll_rows = 0
    stop_event = asyncio.Event()
    book_task = None
    if book_poll_seconds and float(book_poll_seconds) > 0:
        book_task = asyncio.create_task(
            _book_poll_loop(
                targets=targets,
                run_id=run_id,
                db_path=db_path,
                stop_event=stop_event,
                interval_seconds=max(float(book_poll_seconds), 15.0),
                max_tokens=max_book_poll_tokens,
            )
        )

    try:
        subscription = {"assets_ids": list(target_by_token), "type": "market", "custom_feature_enabled": True}
        async with websockets.connect(
            POLYMARKET_MARKET_WS_URL,
            ping_interval=10,
            ping_timeout=10,
            open_timeout=30,
            max_queue=1024,
        ) as websocket:
            await websocket.send(json.dumps(subscription))
            while time.monotonic() < deadline:
                timeout = min(1.0, max(0.0, deadline - time.monotonic()))
                if timeout <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    continue
                raw_message_count += 1
                received_at = datetime.now(timezone.utc)
                rows = normalize_polymarket_ws_records(raw, target_by_token=target_by_token, received_at=received_at)
                rows_observed += len(rows)
                rows_inserted += upsert_polymarket_event_price_ticks(rows, db_path=db_path, run_id=run_id)
    except Exception as exc:  # noqa: BLE001 - provider errors are recorded as ingestion status.
        error_count += 1
        last_error = f"{type(exc).__name__}:{exc}"
    finally:
        stop_event.set()
        if book_task is not None:
            result = await asyncio.gather(book_task, return_exceptions=True)
            if result and isinstance(result[0], dict):
                book_poll_rows = int(result[0].get("rows_inserted") or 0)
            elif result and isinstance(result[0], Exception):
                error_count += 1
                last_error = f"book_poll:{type(result[0]).__name__}:{result[0]}"

    completed = _now()
    total_rows_inserted = rows_inserted + book_poll_rows
    status = "complete" if error_count == 0 else "partial"
    record_price_ingest_run(
        run_id=run_id,
        started_at_utc=started,
        completed_at_utc=completed,
        status=status,
        source="polymarket_event_market_ws",
        symbols=[symbol.upper() for symbol in symbols],
        rows_observed=rows_observed,
        rows_inserted=total_rows_inserted,
        error_count=error_count,
        fetch_interval_seconds=None,
        max_concurrency=1,
        summary={
            "target_count": len(targets),
            "event_rows_upserted": event_rows_upserted,
            "raw_message_count": raw_message_count,
            "ws_rows_inserted": rows_inserted,
            "book_poll_rows_inserted": book_poll_rows,
            "profile_event_timing_links": profile_links,
            "discovery": discovery,
            "last_error": last_error,
            "rate_limit_policy": "primary_websocket_secondary_book_poll_disabled_by_default",
        },
        db_path=db_path,
    )
    return {
        "schema_version": "crypto_options_polymarket_event_price_capture_v1",
        "run_id": run_id,
        "status": status,
        "symbols": [symbol.upper() for symbol in symbols],
        "target_count": len(targets),
        "event_rows_upserted": event_rows_upserted,
        "profile_event_timing_links": profile_links,
        "raw_message_count": raw_message_count,
        "rows_observed": rows_observed,
        "rows_inserted": total_rows_inserted,
        "ws_rows_inserted": rows_inserted,
        "book_poll_rows_inserted": book_poll_rows,
        "error_count": error_count,
        "last_error": last_error,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


async def run_polymarket_event_price_collection_loop(
    *,
    symbols: list[str] | tuple[str, ...] = DEFAULT_EVENT_PRICE_SYMBOLS,
    capture_seconds: float = 600.0,
    loop_interval_seconds: float = 15.0,
    max_iterations: int = 1,
    db_path: str | Path | None = None,
    profile_db_path: str | Path | None = None,
) -> dict[str, Any]:
    iterations = 0
    last: dict[str, Any] | None = None
    while max_iterations <= 0 or iterations < max_iterations:
        last = await capture_polymarket_event_price_stream(
            symbols=symbols,
            seconds=capture_seconds,
            db_path=db_path,
            profile_db_path=profile_db_path,
        )
        iterations += 1
        if max_iterations > 0 and iterations >= max_iterations:
            break
        await asyncio.sleep(max(float(loop_interval_seconds), 1.0))
    return {
        "schema_version": "crypto_options_polymarket_event_price_loop_v1",
        "iterations": iterations,
        "last": last,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


async def _book_poll_loop(
    *,
    targets: list[LiveCaptureTarget],
    run_id: str,
    db_path: str | Path | None,
    stop_event: asyncio.Event,
    interval_seconds: float,
    max_tokens: int,
) -> dict[str, Any]:
    inserted = 0
    while not stop_event.is_set():
        captured_at = datetime.now(timezone.utc)
        for target in _select_capture_targets(targets, max_tokens=max_tokens):
            try:
                payload = await asyncio.to_thread(fetch_current_order_book, target.token_id)
                frame = normalize_order_book_snapshot(
                    payload,
                    event_id=target.event_id,
                    market_id=target.market_id,
                    outcome=target.outcome,
                    symbol=target.symbol,
                    source="polymarket_current_order_book_rate_limited_poll",
                )
                rows = []
                for row in frame.to_dict(orient="records"):
                    row.update(
                        {
                            "event_slug": target.event_slug,
                            "condition_id": target.condition_id,
                            "window_start_time": target.window_start_time,
                            "window_end_time": target.window_end_time,
                            "chart_timestamp_utc": row.get("observed_at"),
                            "system_received_at_utc": captured_at.isoformat(),
                            "event_type": "book_poll",
                        }
                    )
                    rows.append(row)
                inserted += upsert_polymarket_event_price_ticks(rows, db_path=db_path, run_id=run_id)
            except Exception:
                continue
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(float(interval_seconds), 15.0))
        except asyncio.TimeoutError:
            continue
    return {"rows_inserted": inserted}


def _event_price_row_from_normalized(row: dict[str, Any], *, received_at: datetime) -> dict[str, Any]:
    observed_at = row.get("observed_at")
    return {
        **row,
        "chart_timestamp_utc": row.get("chart_timestamp_utc") or observed_at,
        "system_received_at_utc": row.get("received_at_utc") or received_at.isoformat(),
        "event_start_time_utc": row.get("event_start_time_utc") or row.get("window_start_time"),
        "event_end_time_utc": row.get("event_end_time_utc") or row.get("window_end_time"),
        "market_slug": row.get("market_slug") or row.get("event_slug"),
        "mid_price": row.get("mid_price") or row.get("price"),
        "raw_json": row.get("raw_json") or row,
        "source": row.get("source") or f"polymarket_ws_{row.get('event_type') or 'market'}",
    }


def _select_capture_targets(targets: list[LiveCaptureTarget], *, max_tokens: int) -> list[LiveCaptureTarget]:
    now = datetime.now(timezone.utc)
    scored: list[tuple[float, str, str, LiveCaptureTarget]] = []
    for target in targets:
        start = _parse_time(target.window_start_time)
        end = _parse_time(target.window_end_time)
        if start is None or end is None:
            score = 1_000_000.0
        elif end < now:
            score = 100_000.0 + (now - end).total_seconds()
        elif start <= now <= end:
            score = 0.0
        else:
            score = max(0.0, (start - now).total_seconds())
        scored.append((score, target.symbol, target.event_slug, target))
    scored.sort(key=lambda item: (item[0], item[1], item[2], item[3].outcome))
    return [item[3] for item in scored[: max(1, int(max_tokens))]]


def _loads_ws_message(raw: Any) -> Any:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "capture_polymarket_event_price_stream",
    "discover_and_store_polymarket_event_universe",
    "live_capture_targets_to_event_rows",
    "normalize_polymarket_ws_records",
    "run_polymarket_event_price_collection_loop",
]
