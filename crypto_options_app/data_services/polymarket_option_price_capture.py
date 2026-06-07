from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from crypto_options_app.data_nodes.polymarket_crypto.history import fetch_current_order_book, normalize_order_book_snapshot
from crypto_options_app.data_nodes.polymarket_crypto.live_capture import LiveCaptureTarget, discover_live_crypto_updown_targets

from crypto_options_app.config import CENTRAL_DB_PATH
from crypto_options_app.db.connection import connect
from crypto_options_app.db.postgres_connection import should_use_postgres_runtime
from crypto_options_app.db.schema import create_schema, initialize_schema
from crypto_options_app.feeds.polymarket_prices import (
    build_updown_pair_snapshot,
    insert_polymarket_order_book_levels,
    insert_polymarket_price_tick,
    insert_updown_pair_snapshot,
    normalize_order_book_levels,
    normalize_polymarket_price_tick,
)
from crypto_options_app.indicators.event_path_stats import refresh_event_path_stats_for_event
from crypto_options_app.workers.feed_worker import FeedWorkerConfig, write_watermark


LIVE_FLAG_NAMES = (
    "JANUS_CRYPTO_OPTIONS_LIVE_EXECUTE",
    "JANUS_CRYPTO_OPTIONS_LIVE_APPROVED",
    "JANUS_CRYPTO_OPTIONS_ACK_LIVE_RISK",
)


@dataclass(frozen=True)
class OptionPriceCaptureConfig:
    db_path: Path = CENTRAL_DB_PATH
    symbols: tuple[str, ...] = ("BTC", "ETH")
    cadence_minutes: int = 5
    lookback_minutes: int = 0
    lookahead_minutes: int = 15
    max_tokens: int = 120
    max_concurrency: int = 8
    max_book_depth: int = 10
    module_id: str = "polymarket_option_price_capture"


@dataclass(frozen=True)
class OptionPriceCaptureSummary:
    generated_at_utc: str
    status: str
    db_path: str
    target_count: int
    tick_rows_inserted: int
    book_level_rows_inserted: int
    pair_snapshot_rows_inserted: int
    event_path_stats_rows_upserted: int = 0
    readiness_rows_inserted: int = 0
    blockers: tuple[str, ...] = ()
    state: dict[str, Any] = field(default_factory=dict)
    orders_allowed: bool = False
    live_trading_authorized: bool = False


OrderBookFetcher = Callable[[str], dict[str, Any]]
TargetDiscoverer = Callable[..., tuple[list[LiveCaptureTarget], dict[str, Any]]]


async def capture_option_price_paths_once(
    *,
    config: OptionPriceCaptureConfig,
    targets: Iterable[LiveCaptureTarget] | None = None,
    target_discoverer: TargetDiscoverer = discover_live_crypto_updown_targets,
    order_book_fetcher: OrderBookFetcher = fetch_current_order_book,
) -> OptionPriceCaptureSummary:
    _reject_live_env_flags()
    if not should_use_postgres_runtime(config.db_path):
        initialize_schema(config.db_path)
    discovered_state: dict[str, Any] = {}
    if targets is None:
        discovered_targets, discovered_state = target_discoverer(
            symbols=list(config.symbols),
            cadence_minutes=config.cadence_minutes,
            lookback_minutes=config.lookback_minutes,
            lookahead_minutes=config.lookahead_minutes,
        )
    else:
        discovered_targets = list(targets)
    bounded_targets = _select_bounded_targets(
        discovered_targets,
        max_tokens=config.max_tokens,
        symbols=config.symbols,
    )
    semaphore = asyncio.Semaphore(max(1, config.max_concurrency))

    async def fetch_one(target: LiveCaptureTarget) -> tuple[LiveCaptureTarget, dict[str, Any] | None, str | None]:
        async with semaphore:
            try:
                return target, await asyncio.to_thread(order_book_fetcher, target.token_id), None
            except Exception as exc:  # noqa: BLE001 - data services persist provider errors.
                return target, None, f"{type(exc).__name__}:{exc}"

    fetched = await asyncio.gather(*(fetch_one(target) for target in bounded_targets))
    observed_at = datetime.now(UTC)
    inserted_at = datetime.now(UTC)
    errors: list[str] = []
    ticks_by_target: dict[str, dict[str, Any]] = {}
    tick_rows = 0
    book_level_rows = 0
    pair_rows = 0
    event_path_stats_rows = 0
    readiness_rows = 0

    with connect(config.db_path) as conn:
        if not getattr(conn, "is_postgres", False):
            create_schema(conn)
        for target, book, error in fetched:
            if error:
                errors.append(error)
                continue
            if not book:
                continue
            normalized = normalize_order_book_snapshot(
                book,
                event_id=target.event_id,
                market_id=target.market_id,
                outcome=target.outcome,
                symbol=target.symbol,
            )
            if normalized.empty:
                continue
            row = normalized.iloc[0].to_dict()
            event_key = _event_key(target)
            event_token_key = _event_token_key(target)
            _upsert_event_and_token(conn, target, event_key=event_key, event_token_key=event_token_key)
            tick = normalize_polymarket_price_tick(
                {
                    "event_token_key": event_token_key,
                    "event_key": event_key,
                    "token_id": target.token_id,
                    "event_slug": target.event_slug,
                    "outcome": target.outcome,
                    "chart_timestamp_utc": row.get("observed_at"),
                    "mid_price": row.get("mid_price"),
                    "best_bid": row.get("best_bid"),
                    "best_ask": row.get("best_ask"),
                    "depth_top3_bid_size": row.get("depth_top3_bid_size"),
                    "depth_top3_ask_size": row.get("depth_top3_ask_size"),
                    "raw_book": book,
                },
                system_received_at_utc=observed_at,
                system_inserted_at_utc=inserted_at,
            )
            tick["symbol"] = target.symbol
            insert_polymarket_price_tick(conn, tick)
            tick_rows += 1
            ticks_by_target[target.token_id] = tick
            order_book_key = _stable_order_book_key(event_token_key, target.token_id, tick["system_received_at_utc"])
            conn.execute(
                """
                INSERT INTO polymarket_order_books(
                    order_book_key, event_token_key, token_id, observed_at_utc,
                    bids_json, asks_json, source_json, inserted_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_book_key) DO NOTHING
                """,
                (
                    order_book_key,
                    event_token_key,
                    target.token_id,
                    tick["system_received_at_utc"],
                    json.dumps(row.get("book_bids") or [], sort_keys=True, default=str),
                    json.dumps(row.get("book_asks") or [], sort_keys=True, default=str),
                    json.dumps(book, sort_keys=True, default=str),
                    inserted_at.isoformat(),
                ),
            )
            levels = normalize_order_book_levels(
                {"bids": row.get("book_bids") or [], "asks": row.get("book_asks") or []},
                order_book_key=order_book_key,
                event_token_key=event_token_key,
                token_id=target.token_id,
                observed_at_utc=observed_at,
                inserted_at_utc=inserted_at,
                max_depth=config.max_book_depth,
            )
            insert_polymarket_order_book_levels(conn, levels)
            book_level_rows += len(levels)

        touched_event_keys: set[str] = set()
        for up_target, down_target in _paired_targets(bounded_targets):
            up_tick = ticks_by_target.get(up_target.token_id)
            down_tick = ticks_by_target.get(down_target.token_id)
            if not up_tick or not down_tick:
                continue
            event_key = _event_key(up_target)
            snapshot = build_updown_pair_snapshot(
                event_key=event_key,
                event_slug=up_target.event_slug,
                symbol=up_target.symbol,
                bucket_timestamp_utc=observed_at,
                up_tick=up_tick,
                down_tick=down_tick,
            )
            insert_updown_pair_snapshot(conn, snapshot)
            pair_rows += 1
            touched_event_keys.add(event_key)

        for event_key in sorted(touched_event_keys):
            if refresh_event_path_stats_for_event(conn, event_key=event_key) is not None:
                event_path_stats_rows += 1

        status = "failed" if errors and len(errors) >= len(bounded_targets) else "degraded" if errors else "healthy"
        readiness_rows = _write_block_c_readiness(
            conn,
            generated_at_utc=inserted_at,
            target_refresh_seconds=30,
            symbols=config.symbols,
            pair_snapshot_rows_inserted=pair_rows,
            event_path_stats_rows_upserted=event_path_stats_rows,
            errors=errors,
        )
        write_watermark(
            conn,
            service_name="crypto_options_app",
            module_id=config.module_id,
            status=status,
            last_run_at_utc=inserted_at.isoformat(),
            rows_observed=len(bounded_targets),
            rows_inserted=tick_rows + book_level_rows + pair_rows + readiness_rows,
            error_count=len(errors),
            source="polymarket_clob",
            state={
                "discovery": discovered_state,
                "lookahead_minutes": config.lookahead_minutes,
                "target_count": len(bounded_targets),
                "pair_snapshot_rows_inserted": pair_rows,
                "event_path_stats_rows_upserted": event_path_stats_rows,
                "readiness_rows_inserted": readiness_rows,
                "orders_allowed": False,
                "live_trading_authorized": False,
                "errors": errors[:20],
            },
        )

    return OptionPriceCaptureSummary(
        generated_at_utc=inserted_at.isoformat(),
        status=status,
        db_path=str(config.db_path),
        target_count=len(bounded_targets),
        tick_rows_inserted=tick_rows,
        book_level_rows_inserted=book_level_rows,
        pair_snapshot_rows_inserted=pair_rows,
        event_path_stats_rows_upserted=event_path_stats_rows,
        readiness_rows_inserted=readiness_rows,
        blockers=tuple(errors),
        state={"discovery": discovered_state},
    )


def capture_option_price_paths_once_sync(
    *,
    config: OptionPriceCaptureConfig,
    targets: Iterable[LiveCaptureTarget] | None = None,
    target_discoverer: TargetDiscoverer = discover_live_crypto_updown_targets,
    order_book_fetcher: OrderBookFetcher = fetch_current_order_book,
) -> OptionPriceCaptureSummary:
    return asyncio.run(
        capture_option_price_paths_once(
            config=config,
            targets=targets,
            target_discoverer=target_discoverer,
            order_book_fetcher=order_book_fetcher,
        )
    )


def feed_worker_config(config: OptionPriceCaptureConfig) -> FeedWorkerConfig:
    return FeedWorkerConfig(
        service_name="crypto_options_app",
        module_id=config.module_id,
        data_type="polymarket_option_price_paths",
        provider="polymarket_clob",
        transport="rest",
        max_concurrency=config.max_concurrency,
        stale_after_seconds=30,
        read_only=True,
        metadata={
            "writes": [
                "polymarket_price_ticks",
                "polymarket_order_books",
                "polymarket_order_book_levels",
                "polymarket_updown_pair_snapshots",
                "polymarket_event_path_stats",
                "data_signal_readiness_snapshots",
            ],
            "orders_allowed": False,
        },
    )


def _write_block_c_readiness(
    conn: Any,
    *,
    generated_at_utc: datetime,
    target_refresh_seconds: int,
    symbols: Iterable[str],
    pair_snapshot_rows_inserted: int,
    event_path_stats_rows_upserted: int,
    errors: Iterable[str],
) -> int:
    rows = 0
    error_list = list(errors)
    unique_symbols = tuple(dict.fromkeys(str(symbol).upper() for symbol in symbols))
    for symbol in unique_symbols:
        latest = conn.execute(
            """
            SELECT event_slug, bucket_timestamp_utc, up_mid_price, down_mid_price,
                   up_best_bid, up_best_ask, down_best_bid, down_best_ask,
                   up_depth_top3_bid_size, up_depth_top3_ask_size,
                   down_depth_top3_bid_size, down_depth_top3_ask_size,
                   source_latency_ms, source_json
            FROM polymarket_updown_pair_snapshots
            WHERE symbol=?
            ORDER BY bucket_timestamp_utc DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        blockers: list[str] = []
        latest_source_at = None
        age: float | None = None
        payload: dict[str, Any] = {
            "source": "polymarket_clob",
            "required_payloads": [
                "polymarket_price_ticks",
                "polymarket_order_book_levels",
                "polymarket_updown_pair_snapshots",
                "polymarket_event_path_stats",
            ],
            "pair_snapshot_rows_inserted": pair_snapshot_rows_inserted,
            "event_path_stats_rows_upserted": event_path_stats_rows_upserted,
            "provider_errors": error_list[:20],
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
        if latest is None:
            blockers.append("missing_updown_pair_snapshot")
        else:
            latest_source_at = latest["bucket_timestamp_utc"]
            age = max(0.0, (generated_at_utc - _parse_datetime(latest_source_at)).total_seconds())
            if age > target_refresh_seconds:
                blockers.append(f"stale_updown_pair_snapshot:{age:.1f}s")
            up_mid = latest["up_mid_price"]
            down_mid = latest["down_mid_price"]
            up_reference_price = _reference_price(
                latest["up_mid_price"], latest["up_best_ask"], latest["up_best_bid"]
            )
            down_reference_price = _reference_price(
                latest["down_mid_price"], latest["down_best_ask"], latest["down_best_bid"]
            )
            if up_reference_price is None:
                blockers.append("missing_up_reference_price")
            if down_reference_price is None:
                blockers.append("missing_down_reference_price")
            up_spread = _optional_diff(latest["up_best_ask"], latest["up_best_bid"])
            down_spread = _optional_diff(latest["down_best_ask"], latest["down_best_bid"])
            pair_depth_pressure = _depth_pressure(
                up_bid=latest["up_depth_top3_bid_size"],
                up_ask=latest["up_depth_top3_ask_size"],
                down_bid=latest["down_depth_top3_bid_size"],
                down_ask=latest["down_depth_top3_ask_size"],
            )
            payload["latest_pair_snapshot"] = {
                "event_slug": latest["event_slug"],
                "bucket_timestamp_utc": latest["bucket_timestamp_utc"],
                "up_mid_price": latest["up_mid_price"],
                "down_mid_price": latest["down_mid_price"],
                "up_reference_price": up_reference_price,
                "down_reference_price": down_reference_price,
                "up_best_bid": latest["up_best_bid"],
                "up_best_ask": latest["up_best_ask"],
                "down_best_bid": latest["down_best_bid"],
                "down_best_ask": latest["down_best_ask"],
                "pair_sum": None if up_mid is None or down_mid is None else float(up_mid) + float(down_mid),
                "up_spread": up_spread,
                "down_spread": down_spread,
                "pair_depth_pressure": pair_depth_pressure,
                "source_latency_ms": latest["source_latency_ms"],
            }
        status = "ready" if not blockers else "degraded"
        readiness_key = _stable_readiness_key("block_c", symbol, generated_at_utc.isoformat())
        conn.execute(
            """
            INSERT INTO data_signal_readiness_snapshots(
                readiness_key, data_block, module_id, symbol, generated_at_utc,
                target_refresh_seconds, status, latest_source_at_utc,
                source_age_seconds, payload_json, blockers_json, inserted_at_utc
            )
            VALUES (?, 'C', 'polymarket_option_price_capture', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(readiness_key) DO NOTHING
            """,
            (
                readiness_key,
                symbol,
                generated_at_utc.isoformat(),
                target_refresh_seconds,
                status,
                latest_source_at,
                age,
                json.dumps(payload, sort_keys=True, default=str),
                json.dumps(blockers, sort_keys=True, default=str),
                datetime.now(UTC).isoformat(),
            ),
        )
        rows += int(conn.execute("SELECT changes() AS c").fetchone()["c"])
    return rows


def _stable_readiness_key(*parts: Any) -> str:
    import hashlib

    payload = "|".join(json.dumps(part, sort_keys=True, default=str) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def _optional_diff(left: Any, right: Any) -> float | None:
    if left is None or right is None:
        return None
    return float(left) - float(right)


def _reference_price(mid: Any, best_ask: Any, best_bid: Any) -> float | None:
    for value in (mid, best_ask, best_bid):
        if value is not None:
            return float(value)
    return None


def _depth_pressure(*, up_bid: Any, up_ask: Any, down_bid: Any, down_ask: Any) -> float | None:
    values = (up_bid, up_ask, down_bid, down_ask)
    if any(value is None for value in values):
        return None
    up_net = float(up_bid) - float(up_ask)
    down_net = float(down_bid) - float(down_ask)
    denom = abs(up_net) + abs(down_net)
    if denom <= 0:
        return 0.0
    return (up_net - down_net) / denom


def _parse_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _reject_live_env_flags() -> None:
    enabled = [name for name in LIVE_FLAG_NAMES if str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}]
    if enabled:
        raise RuntimeError(f"data_service_live_flags_rejected:{','.join(enabled)}")


def _event_key(target: LiveCaptureTarget) -> str:
    return target.event_id or target.market_id or target.event_slug


def _event_token_key(target: LiveCaptureTarget) -> str:
    return f"{_event_key(target)}:{target.outcome.lower()}:{target.token_id}"


def _select_bounded_targets(
    targets: Iterable[LiveCaptureTarget],
    *,
    max_tokens: int,
    symbols: Iterable[str],
) -> list[LiveCaptureTarget]:
    target_list = list(targets)
    limit = max(0, int(max_tokens))
    if limit <= 0:
        return []
    if len(target_list) <= limit:
        return target_list

    configured_symbols = [str(symbol).upper() for symbol in symbols if str(symbol).strip()]
    symbol_order = list(dict.fromkeys(configured_symbols))
    event_targets_by_symbol: dict[str, dict[str, list[LiveCaptureTarget]]] = {}
    event_order_by_symbol: dict[str, list[str]] = {}
    for target in target_list:
        symbol = str(target.symbol or "").upper()
        if symbol not in symbol_order:
            symbol_order.append(symbol)
        event_key = _event_key(target)
        symbol_events = event_targets_by_symbol.setdefault(symbol, {})
        if event_key not in symbol_events:
            symbol_events[event_key] = []
            event_order_by_symbol.setdefault(symbol, []).append(event_key)
        symbol_events[event_key].append(target)

    selected: list[LiveCaptureTarget] = []
    cursors = {symbol: 0 for symbol in symbol_order}
    while len(selected) < limit:
        progressed = False
        for symbol in symbol_order:
            event_order = event_order_by_symbol.get(symbol) or []
            cursor = cursors.get(symbol, 0)
            if cursor >= len(event_order):
                continue
            event_key = event_order[cursor]
            event_targets = event_targets_by_symbol[symbol][event_key]
            if len(selected) + len(event_targets) > limit:
                continue
            selected.extend(event_targets)
            cursors[symbol] = cursor + 1
            progressed = True
        if not progressed:
            break
    return selected or target_list[:limit]


def _upsert_event_and_token(conn: Any, target: LiveCaptureTarget, *, event_key: str, event_token_key: str) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO events(
            event_key, event_slug, condition_id, market_id, market_slug, symbol,
            cadence_seconds, event_start_time_utc, event_end_time_utc, settlement_threshold,
            source_table, source_json, inserted_at_utc, updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_key) DO UPDATE SET
            event_slug=excluded.event_slug,
            updated_at_utc=excluded.updated_at_utc
        """,
        (
            event_key,
            target.event_slug,
            target.condition_id,
            target.market_id,
            target.event_slug,
            target.symbol,
            300,
            target.window_start_time,
            target.window_end_time,
            target.settlement_threshold,
            "polymarket_option_price_capture",
            json.dumps(target.__dict__, sort_keys=True, default=str),
            now,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO event_tokens(
            event_token_key, event_key, token_id, outcome, condition_id, event_slug,
            market_id, symbol, active, closed, source_table, source_json, inserted_at_utc, updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?, ?)
        ON CONFLICT(event_token_key) DO UPDATE SET
            active=excluded.active,
            closed=excluded.closed,
            updated_at_utc=excluded.updated_at_utc
        """,
        (
            event_token_key,
            event_key,
            target.token_id,
            target.outcome,
            target.condition_id,
            target.event_slug,
            target.market_id,
            target.symbol,
            "polymarket_option_price_capture",
            json.dumps(target.__dict__, sort_keys=True, default=str),
            now,
            now,
        ),
    )


def _paired_targets(targets: Iterable[LiveCaptureTarget]) -> list[tuple[LiveCaptureTarget, LiveCaptureTarget]]:
    by_event: dict[str, dict[str, LiveCaptureTarget]] = {}
    for target in targets:
        outcome = target.outcome.strip().lower()
        if outcome in {"up", "yes"}:
            side = "up"
        elif outcome in {"down", "no"}:
            side = "down"
        else:
            continue
        by_event.setdefault(_event_key(target), {})[side] = target
    return [
        (sides["up"], sides["down"])
        for sides in by_event.values()
        if "up" in sides and "down" in sides
    ]


def _stable_order_book_key(event_token_key: str, token_id: str, observed_at_utc: str) -> str:
    return f"book:{event_token_key}:{token_id}:{observed_at_utc}"
