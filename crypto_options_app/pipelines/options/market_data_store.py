from __future__ import annotations

"""Legacy SQLite compatibility store for market-data research artifacts.

The canonical crypto-options runtime uses Postgres through the application DB
adapter. This module is retained only for older market-data research CLIs and
migration/reference reads; production workers must not use it as runtime truth.
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MARKET_DATA_STORE_SCHEMA_VERSION = "crypto_options_market_data_store_v1"
LEGACY_SQLITE_COMPATIBILITY_STORE = True


def default_market_data_store_path() -> Path:
    return (
        Path("local")
        / "shared"
        / "artifacts"
        / "crypto-options-research"
        / "market-data"
        / "crypto_market_data.sqlite"
    )


def connect_market_data_store(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else default_market_data_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def initialize_market_data_store(db_path: str | Path | None = None) -> Path:
    path = Path(db_path) if db_path else default_market_data_store_path()
    conn = connect_market_data_store(path)
    try:
        _create_schema(conn)
        conn.commit()
    finally:
        conn.close()
    return path


def record_price_ingest_run(
    *,
    run_id: str,
    started_at_utc: str,
    completed_at_utc: str | None,
    status: str,
    source: str,
    symbols: list[str] | tuple[str, ...],
    rows_observed: int = 0,
    rows_inserted: int = 0,
    error_count: int = 0,
    fetch_interval_seconds: float | None = None,
    max_concurrency: int | None = None,
    summary: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    conn = connect_market_data_store(db_path)
    try:
        _create_schema(conn)
        conn.execute(
            """
            INSERT INTO crypto_price_ingest_runs (
                run_id, started_at_utc, completed_at_utc, status, source,
                symbols_json, fetch_interval_seconds, max_concurrency,
                rows_observed, rows_inserted, error_count, summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                completed_at_utc=excluded.completed_at_utc,
                status=excluded.status,
                rows_observed=excluded.rows_observed,
                rows_inserted=excluded.rows_inserted,
                error_count=excluded.error_count,
                summary_json=excluded.summary_json
            """,
            (
                run_id,
                started_at_utc,
                completed_at_utc,
                status,
                source,
                _json([str(symbol).upper() for symbol in symbols]),
                fetch_interval_seconds,
                max_concurrency,
                int(rows_observed),
                int(rows_inserted),
                int(error_count),
                _json(summary or {}),
            ),
        )
        conn.execute(
            """
            INSERT INTO crypto_market_data_watermarks(service_name, last_run_at_utc, status, state_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(service_name) DO UPDATE SET
                last_run_at_utc=excluded.last_run_at_utc,
                status=excluded.status,
                state_json=excluded.state_json
            """,
            (
                source,
                completed_at_utc or started_at_utc,
                status,
                _json({"run_id": run_id, "rows_inserted": rows_inserted, "error_count": error_count}),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "schema_version": MARKET_DATA_STORE_SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        "source": source,
        "rows_observed": rows_observed,
        "rows_inserted": rows_inserted,
        "error_count": error_count,
        "orders_allowed": False,
        "live_trading_authorized": False,
    }


def upsert_price_ticks(rows: list[dict[str, Any]], *, db_path: str | Path | None = None, run_id: str | None = None) -> int:
    conn = connect_market_data_store(db_path)
    inserted = 0
    try:
        _create_schema(conn)
        for row in rows:
            normalized = _normalize_tick_row(row, run_id=run_id)
            if not normalized:
                continue
            conn.execute(
                """
                INSERT INTO crypto_price_ticks (
                    tick_key, run_id, symbol, source, observed_at_utc,
                    exchange_timestamp_utc, price, bid, ask, last_size,
                    volume_24h, raw_json, inserted_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tick_key) DO UPDATE SET
                    run_id=COALESCE(excluded.run_id, crypto_price_ticks.run_id),
                    price=excluded.price,
                    bid=excluded.bid,
                    ask=excluded.ask,
                    last_size=excluded.last_size,
                    volume_24h=excluded.volume_24h,
                    raw_json=excluded.raw_json
                """,
                (
                    normalized["tick_key"],
                    normalized.get("run_id"),
                    normalized["symbol"],
                    normalized["source"],
                    normalized["observed_at_utc"],
                    normalized.get("exchange_timestamp_utc"),
                    normalized["price"],
                    normalized.get("bid"),
                    normalized.get("ask"),
                    normalized.get("last_size"),
                    normalized.get("volume_24h"),
                    _json(normalized.get("raw_json") or {}),
                    normalized["inserted_at_utc"],
                ),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted


def upsert_candles(rows: list[dict[str, Any]], *, db_path: str | Path | None = None, run_id: str | None = None) -> int:
    conn = connect_market_data_store(db_path)
    inserted = 0
    try:
        _create_schema(conn)
        for row in rows:
            normalized = _normalize_candle_row(row, run_id=run_id)
            if not normalized:
                continue
            conn.execute(
                """
                INSERT INTO crypto_candles (
                    candle_key, run_id, symbol, exchange, interval, opened_at_utc,
                    closed_at_utc, open, high, low, close, volume, trade_count,
                    source, raw_json, inserted_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candle_key) DO UPDATE SET
                    run_id=COALESCE(excluded.run_id, crypto_candles.run_id),
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    volume=excluded.volume,
                    trade_count=excluded.trade_count,
                    raw_json=excluded.raw_json
                """,
                (
                    normalized["candle_key"],
                    normalized.get("run_id"),
                    normalized["symbol"],
                    normalized["exchange"],
                    normalized["interval"],
                    normalized["opened_at_utc"],
                    normalized.get("closed_at_utc"),
                    normalized["open"],
                    normalized["high"],
                    normalized["low"],
                    normalized["close"],
                    normalized.get("volume"),
                    normalized.get("trade_count"),
                    normalized["source"],
                    _json(normalized.get("raw_json") or {}),
                    normalized["inserted_at_utc"],
                ),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted


def upsert_indicator_snapshots(rows: list[dict[str, Any]], *, db_path: str | Path | None = None) -> int:
    conn = connect_market_data_store(db_path)
    inserted = 0
    try:
        _create_schema(conn)
        for row in rows:
            normalized = _normalize_indicator_row(row)
            if not normalized:
                continue
            conn.execute(
                """
                INSERT INTO crypto_indicator_snapshots (
                    snapshot_key, symbol, interval, indicator_id, computed_at_utc,
                    candle_opened_at_utc, source, direction, confidence, signal_value,
                    components_json, quality_flags_json, inserted_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_key) DO UPDATE SET
                    direction=excluded.direction,
                    confidence=excluded.confidence,
                    signal_value=excluded.signal_value,
                    components_json=excluded.components_json,
                    quality_flags_json=excluded.quality_flags_json
                """,
                (
                    normalized["snapshot_key"],
                    normalized["symbol"],
                    normalized["interval"],
                    normalized["indicator_id"],
                    normalized["computed_at_utc"],
                    normalized.get("candle_opened_at_utc"),
                    normalized["source"],
                    normalized.get("direction"),
                    normalized.get("confidence"),
                    normalized.get("signal_value"),
                    _json(normalized.get("components_json") or {}),
                    _json(normalized.get("quality_flags_json") or {}),
                    normalized["inserted_at_utc"],
                ),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted


def upsert_event_indicator_context(rows: list[dict[str, Any]], *, db_path: str | Path | None = None) -> int:
    conn = connect_market_data_store(db_path)
    inserted = 0
    try:
        _create_schema(conn)
        for row in rows:
            normalized = _normalize_event_context_row(row)
            if not normalized:
                continue
            conn.execute(
                """
                INSERT INTO crypto_event_indicator_context (
                    context_key, event_id, market_slug, symbol, side, event_threshold_price,
                    event_end_at_utc, computed_at_utc, underlying_price, target_delta_abs,
                    target_delta_signed_for_side, trend_15m, trend_30m, trend_1h,
                    indicator_summary_json, blocker_summary_json, inserted_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(context_key) DO UPDATE SET
                    underlying_price=excluded.underlying_price,
                    target_delta_abs=excluded.target_delta_abs,
                    target_delta_signed_for_side=excluded.target_delta_signed_for_side,
                    trend_15m=excluded.trend_15m,
                    trend_30m=excluded.trend_30m,
                    trend_1h=excluded.trend_1h,
                    indicator_summary_json=excluded.indicator_summary_json,
                    blocker_summary_json=excluded.blocker_summary_json
                """,
                (
                    normalized["context_key"],
                    normalized.get("event_id"),
                    normalized.get("market_slug"),
                    normalized["symbol"],
                    normalized.get("side"),
                    normalized.get("event_threshold_price"),
                    normalized.get("event_end_at_utc"),
                    normalized["computed_at_utc"],
                    normalized.get("underlying_price"),
                    normalized.get("target_delta_abs"),
                    normalized.get("target_delta_signed_for_side"),
                    normalized.get("trend_15m"),
                    normalized.get("trend_30m"),
                    normalized.get("trend_1h"),
                    _json(normalized.get("indicator_summary_json") or {}),
                    _json(normalized.get("blocker_summary_json") or {}),
                    normalized["inserted_at_utc"],
                ),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted


def upsert_polymarket_event_universe(rows: list[dict[str, Any]], *, db_path: str | Path | None = None) -> int:
    conn = connect_market_data_store(db_path)
    inserted = 0
    try:
        _create_schema(conn)
        for row in rows:
            normalized = _normalize_polymarket_event_row(row)
            if not normalized:
                continue
            conn.execute(
                """
                INSERT INTO polymarket_crypto_event_universe (
                    event_token_key, event_id, event_slug, market_id, condition_id,
                    market_slug, symbol, cadence_seconds, outcome, token_id,
                    event_start_time_utc, event_end_time_utc, settlement_threshold,
                    active, closed, source, raw_json, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_token_key) DO UPDATE SET
                    event_id=excluded.event_id,
                    event_slug=excluded.event_slug,
                    market_id=excluded.market_id,
                    condition_id=excluded.condition_id,
                    market_slug=excluded.market_slug,
                    symbol=excluded.symbol,
                    cadence_seconds=excluded.cadence_seconds,
                    outcome=excluded.outcome,
                    token_id=excluded.token_id,
                    event_start_time_utc=excluded.event_start_time_utc,
                    event_end_time_utc=excluded.event_end_time_utc,
                    settlement_threshold=excluded.settlement_threshold,
                    active=excluded.active,
                    closed=excluded.closed,
                    source=excluded.source,
                    raw_json=excluded.raw_json,
                    updated_at_utc=excluded.updated_at_utc
                """,
                (
                    normalized["event_token_key"],
                    normalized.get("event_id"),
                    normalized.get("event_slug"),
                    normalized.get("market_id"),
                    normalized.get("condition_id"),
                    normalized.get("market_slug"),
                    normalized["symbol"],
                    normalized.get("cadence_seconds"),
                    normalized["outcome"],
                    normalized["token_id"],
                    normalized.get("event_start_time_utc"),
                    normalized.get("event_end_time_utc"),
                    normalized.get("settlement_threshold"),
                    normalized["active"],
                    normalized["closed"],
                    normalized["source"],
                    _json(normalized.get("raw_json") or {}),
                    normalized["updated_at_utc"],
                ),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted


def upsert_polymarket_event_price_ticks(rows: list[dict[str, Any]], *, db_path: str | Path | None = None, run_id: str | None = None) -> int:
    conn = connect_market_data_store(db_path)
    inserted = 0
    try:
        _create_schema(conn)
        for row in rows:
            normalized = _normalize_polymarket_price_row(row, run_id=run_id)
            if not normalized:
                continue
            previous = _latest_polymarket_price_for_token(conn, normalized["token_id"])
            if previous:
                normalized["mid_price_delta"] = _delta(normalized.get("mid_price"), previous.get("mid_price"))
                normalized["best_bid_delta"] = _delta(normalized.get("best_bid"), previous.get("best_bid"))
                normalized["best_ask_delta"] = _delta(normalized.get("best_ask"), previous.get("best_ask"))
                normalized["trade_price_delta"] = _delta(normalized.get("trade_price"), previous.get("trade_price"))
            conn.execute(
                """
                INSERT INTO polymarket_event_price_ticks (
                    event_price_tick_key, run_id, event_id, event_slug, market_id,
                    condition_id, market_slug, symbol, outcome, token_id,
                    event_start_time_utc, event_end_time_utc, event_type, source,
                    chart_timestamp_utc, system_received_at_utc, system_inserted_at_utc,
                    source_latency_ms, insert_latency_ms, mid_price, best_bid,
                    best_ask, spread, bid_size, ask_size, depth_top3_bid_size,
                    depth_top3_ask_size, trade_price, trade_size, side,
                    mid_price_delta, best_bid_delta, best_ask_delta, trade_price_delta,
                    raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_price_tick_key) DO UPDATE SET
                    best_bid=excluded.best_bid,
                    best_ask=excluded.best_ask,
                    mid_price=excluded.mid_price,
                    spread=excluded.spread,
                    bid_size=excluded.bid_size,
                    ask_size=excluded.ask_size,
                    depth_top3_bid_size=excluded.depth_top3_bid_size,
                    depth_top3_ask_size=excluded.depth_top3_ask_size,
                    trade_price=excluded.trade_price,
                    trade_size=excluded.trade_size,
                    raw_json=excluded.raw_json
                """,
                (
                    normalized["event_price_tick_key"],
                    normalized.get("run_id"),
                    normalized.get("event_id"),
                    normalized.get("event_slug"),
                    normalized.get("market_id"),
                    normalized.get("condition_id"),
                    normalized.get("market_slug"),
                    normalized.get("symbol"),
                    normalized.get("outcome"),
                    normalized["token_id"],
                    normalized.get("event_start_time_utc"),
                    normalized.get("event_end_time_utc"),
                    normalized.get("event_type"),
                    normalized["source"],
                    normalized.get("chart_timestamp_utc"),
                    normalized["system_received_at_utc"],
                    normalized["system_inserted_at_utc"],
                    normalized.get("source_latency_ms"),
                    normalized.get("insert_latency_ms"),
                    normalized.get("mid_price"),
                    normalized.get("best_bid"),
                    normalized.get("best_ask"),
                    normalized.get("spread"),
                    normalized.get("bid_size"),
                    normalized.get("ask_size"),
                    normalized.get("depth_top3_bid_size"),
                    normalized.get("depth_top3_ask_size"),
                    normalized.get("trade_price"),
                    normalized.get("trade_size"),
                    normalized.get("side"),
                    normalized.get("mid_price_delta"),
                    normalized.get("best_bid_delta"),
                    normalized.get("best_ask_delta"),
                    normalized.get("trade_price_delta"),
                    _json(normalized.get("raw_json") or {}),
                ),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted


def load_candles(
    *,
    symbols: list[str] | tuple[str, ...] | None = None,
    interval: str = "1m",
    lookback_minutes: int | None = None,
    limit: int = 5000,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    clauses = ["interval = ?"]
    params: list[Any] = [str(interval)]
    if symbols:
        normalized_symbols = [str(symbol).upper() for symbol in symbols]
        clauses.append(f"symbol IN ({','.join('?' for _ in normalized_symbols)})")
        params.extend(normalized_symbols)
    if lookback_minutes is not None:
        clauses.append("opened_at_utc >= datetime('now', ?)")
        params.append(f"-{int(lookback_minutes)} minutes")
    params.append(int(limit))
    query = f"""
        SELECT symbol, exchange, interval, opened_at_utc AS opened_at, closed_at_utc AS closed_at,
               open, high, low, close, volume, trade_count, source, raw_json
        FROM crypto_candles
        WHERE {' AND '.join(clauses)}
        ORDER BY symbol ASC, opened_at_utc ASC
        LIMIT ?
    """
    conn = connect_market_data_store(db_path)
    try:
        _create_schema(conn)
        return [_row_to_dict(row) for row in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def latest_price_ticks(
    *,
    symbols: list[str] | tuple[str, ...] | None = None,
    limit: int = 20,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    symbol_clause = ""
    params: list[Any] = []
    if symbols:
        normalized_symbols = [str(symbol).upper() for symbol in symbols]
        symbol_clause = f"WHERE symbol IN ({','.join('?' for _ in normalized_symbols)})"
        params.extend(normalized_symbols)
    params.append(int(limit))
    conn = connect_market_data_store(db_path)
    try:
        _create_schema(conn)
        rows = conn.execute(
            f"""
            SELECT *
            FROM v_crypto_options_latest_price_ticks
            {symbol_clause}
            ORDER BY observed_at_utc DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return {
            "schema_version": "crypto_options_latest_price_ticks_v1",
            "items": [_row_to_dict(row) for row in rows],
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    finally:
        conn.close()


def latest_indicator_snapshots(
    *,
    symbols: list[str] | tuple[str, ...] | None = None,
    indicator_id: str | None = None,
    limit: int = 50,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []
    if symbols:
        normalized_symbols = [str(symbol).upper() for symbol in symbols]
        clauses.append(f"symbol IN ({','.join('?' for _ in normalized_symbols)})")
        params.extend(normalized_symbols)
    if indicator_id:
        clauses.append("indicator_id = ?")
        params.append(str(indicator_id))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(int(limit))
    conn = connect_market_data_store(db_path)
    try:
        _create_schema(conn)
        rows = conn.execute(
            f"""
            SELECT *
            FROM v_crypto_options_latest_indicator_snapshots
            {where}
            ORDER BY computed_at_utc DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return {
            "schema_version": "crypto_options_latest_indicator_snapshots_v1",
            "items": [_row_to_dict(row) for row in rows],
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    finally:
        conn.close()


def latest_polymarket_event_prices(
    *,
    symbols: list[str] | tuple[str, ...] | None = None,
    event_slug: str | None = None,
    limit: int = 100,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []
    if symbols:
        normalized_symbols = [str(symbol).upper() for symbol in symbols]
        clauses.append(f"symbol IN ({','.join('?' for _ in normalized_symbols)})")
        params.extend(normalized_symbols)
    if event_slug:
        clauses.append("event_slug = ?")
        params.append(str(event_slug))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(int(limit))
    conn = connect_market_data_store(db_path)
    try:
        _create_schema(conn)
        rows = conn.execute(
            f"""
            SELECT *
            FROM v_crypto_options_latest_polymarket_event_prices
            {where}
            ORDER BY system_received_at_utc DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return {
            "schema_version": "crypto_options_latest_polymarket_event_prices_v1",
            "items": [_row_to_dict(row) for row in rows],
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    finally:
        conn.close()


def fresh_polymarket_event_universe(
    *,
    symbols: list[str] | tuple[str, ...] | None = None,
    event_slug: str | None = None,
    include_closed: bool = False,
    limit: int = 500,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []
    if symbols:
        normalized_symbols = [str(symbol).upper() for symbol in symbols]
        clauses.append(f"symbol IN ({','.join('?' for _ in normalized_symbols)})")
        params.extend(normalized_symbols)
    if event_slug:
        clauses.append("event_slug = ?")
        params.append(str(event_slug))
    if not include_closed:
        clauses.append("closed = 0")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(int(limit))
    conn = connect_market_data_store(db_path)
    try:
        _create_schema(conn)
        rows = conn.execute(
            f"""
            SELECT *
            FROM polymarket_crypto_event_universe
            {where}
            ORDER BY event_start_time_utc ASC, symbol ASC, outcome ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return {
            "schema_version": "crypto_options_polymarket_event_universe_v1",
            "items": [_row_to_dict(row) for row in rows],
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    finally:
        conn.close()


def market_data_store_summary(db_path: str | Path | None = None) -> dict[str, Any]:
    conn = connect_market_data_store(db_path)
    try:
        _create_schema(conn)
        tick_rows = conn.execute(
            """
            SELECT symbol, COUNT(*) AS count, MAX(observed_at_utc) AS latest_observed_at_utc
            FROM crypto_price_ticks
            GROUP BY symbol
            ORDER BY symbol
            """
        ).fetchall()
        candle_rows = conn.execute(
            """
            SELECT symbol, interval, COUNT(*) AS count, MAX(opened_at_utc) AS latest_opened_at_utc
            FROM crypto_candles
            GROUP BY symbol, interval
            ORDER BY symbol, interval
            """
        ).fetchall()
        indicator_rows = conn.execute(
            """
            SELECT symbol, indicator_id, COUNT(*) AS count, MAX(computed_at_utc) AS latest_computed_at_utc
            FROM crypto_indicator_snapshots
            GROUP BY symbol, indicator_id
            ORDER BY symbol, indicator_id
            """
        ).fetchall()
        watermarks = conn.execute(
            """
            SELECT service_name, last_run_at_utc, status, state_json
            FROM crypto_market_data_watermarks
            ORDER BY service_name
            """
        ).fetchall()
        event_rows = conn.execute(
            """
            SELECT symbol, COUNT(*) AS count, MIN(event_start_time_utc) AS earliest_start_utc,
                   MAX(event_start_time_utc) AS latest_start_utc
            FROM polymarket_crypto_event_universe
            WHERE closed=0
            GROUP BY symbol
            ORDER BY symbol
            """
        ).fetchall()
        polymarket_price_rows = conn.execute(
            """
            SELECT symbol, outcome, COUNT(*) AS count, MAX(system_received_at_utc) AS latest_received_at_utc,
                   AVG(source_latency_ms) AS avg_source_latency_ms
            FROM polymarket_event_price_ticks
            GROUP BY symbol, outcome
            ORDER BY symbol, outcome
            """
        ).fetchall()
        return {
            "schema_version": MARKET_DATA_STORE_SCHEMA_VERSION,
            "db_path": str(Path(db_path) if db_path else default_market_data_store_path()),
            "ingest_runs": _scalar(conn, "SELECT COUNT(*) FROM crypto_price_ingest_runs"),
            "ticks": _scalar(conn, "SELECT COUNT(*) FROM crypto_price_ticks"),
            "candles": _scalar(conn, "SELECT COUNT(*) FROM crypto_candles"),
            "indicator_snapshots": _scalar(conn, "SELECT COUNT(*) FROM crypto_indicator_snapshots"),
            "event_indicator_context_rows": _scalar(conn, "SELECT COUNT(*) FROM crypto_event_indicator_context"),
            "polymarket_event_tokens": _scalar(conn, "SELECT COUNT(*) FROM polymarket_crypto_event_universe"),
            "polymarket_event_price_ticks": _scalar(conn, "SELECT COUNT(*) FROM polymarket_event_price_ticks"),
            "ticks_by_symbol": [_row_to_dict(row) for row in tick_rows],
            "candles_by_symbol_interval": [_row_to_dict(row) for row in candle_rows],
            "indicators_by_symbol": [_row_to_dict(row) for row in indicator_rows],
            "polymarket_events_by_symbol": [_row_to_dict(row) for row in event_rows],
            "polymarket_event_prices_by_symbol_outcome": [_row_to_dict(row) for row in polymarket_price_rows],
            "watermarks": [_row_to_dict(row) for row in watermarks],
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    finally:
        conn.close()


def prune_market_data_retention(*, retention_days: int = 90, db_path: str | Path | None = None) -> dict[str, Any]:
    """Delete old market-data rows beyond the configured retention window."""

    cutoff = f"-{int(retention_days)} days"
    conn = connect_market_data_store(db_path)
    try:
        _create_schema(conn)
        deleted: dict[str, int] = {}
        for table, column in (
            ("crypto_price_ticks", "observed_at_utc"),
            ("crypto_candles", "opened_at_utc"),
            ("crypto_indicator_snapshots", "computed_at_utc"),
            ("crypto_event_indicator_context", "computed_at_utc"),
            ("polymarket_event_price_ticks", "system_received_at_utc"),
        ):
            cursor = conn.execute(f"DELETE FROM {table} WHERE {column} < datetime('now', ?)", (cutoff,))
            deleted[table] = int(cursor.rowcount or 0)
        conn.commit()
        return {
            "schema_version": "crypto_options_market_data_retention_prune_v1",
            "retention_days": int(retention_days),
            "deleted": deleted,
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    finally:
        conn.close()


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS crypto_price_ingest_runs (
            run_id TEXT PRIMARY KEY,
            started_at_utc TEXT NOT NULL,
            completed_at_utc TEXT,
            status TEXT NOT NULL,
            source TEXT NOT NULL,
            symbols_json TEXT NOT NULL,
            fetch_interval_seconds REAL,
            max_concurrency INTEGER,
            rows_observed INTEGER NOT NULL DEFAULT 0,
            rows_inserted INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            summary_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS crypto_price_ticks (
            tick_key TEXT PRIMARY KEY,
            run_id TEXT,
            symbol TEXT NOT NULL,
            source TEXT NOT NULL,
            observed_at_utc TEXT NOT NULL,
            exchange_timestamp_utc TEXT,
            price REAL NOT NULL,
            bid REAL,
            ask REAL,
            last_size REAL,
            volume_24h REAL,
            raw_json TEXT NOT NULL DEFAULT '{}',
            inserted_at_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_crypto_price_ticks_symbol_observed
            ON crypto_price_ticks(symbol, observed_at_utc);
        CREATE INDEX IF NOT EXISTS idx_crypto_price_ticks_source_symbol_observed
            ON crypto_price_ticks(source, symbol, observed_at_utc);

        CREATE TABLE IF NOT EXISTS crypto_candles (
            candle_key TEXT PRIMARY KEY,
            run_id TEXT,
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL,
            interval TEXT NOT NULL,
            opened_at_utc TEXT NOT NULL,
            closed_at_utc TEXT,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL,
            trade_count INTEGER,
            source TEXT NOT NULL,
            raw_json TEXT NOT NULL DEFAULT '{}',
            inserted_at_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_crypto_candles_symbol_interval_opened
            ON crypto_candles(symbol, interval, opened_at_utc);

        CREATE TABLE IF NOT EXISTS crypto_indicator_snapshots (
            snapshot_key TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            indicator_id TEXT NOT NULL,
            computed_at_utc TEXT NOT NULL,
            candle_opened_at_utc TEXT,
            source TEXT NOT NULL,
            direction TEXT,
            confidence REAL,
            signal_value REAL,
            components_json TEXT NOT NULL DEFAULT '{}',
            quality_flags_json TEXT NOT NULL DEFAULT '{}',
            inserted_at_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_crypto_indicator_symbol_indicator_computed
            ON crypto_indicator_snapshots(symbol, indicator_id, computed_at_utc);

        CREATE TABLE IF NOT EXISTS crypto_event_indicator_context (
            context_key TEXT PRIMARY KEY,
            event_id TEXT,
            market_slug TEXT,
            symbol TEXT NOT NULL,
            side TEXT,
            event_threshold_price REAL,
            event_end_at_utc TEXT,
            computed_at_utc TEXT NOT NULL,
            underlying_price REAL,
            target_delta_abs REAL,
            target_delta_signed_for_side REAL,
            trend_15m TEXT,
            trend_30m TEXT,
            trend_1h TEXT,
            indicator_summary_json TEXT NOT NULL DEFAULT '{}',
            blocker_summary_json TEXT NOT NULL DEFAULT '{}',
            inserted_at_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_crypto_event_indicator_context_event_symbol
            ON crypto_event_indicator_context(event_id, symbol, computed_at_utc);

        CREATE TABLE IF NOT EXISTS polymarket_crypto_event_universe (
            event_token_key TEXT PRIMARY KEY,
            event_id TEXT,
            event_slug TEXT,
            market_id TEXT,
            condition_id TEXT,
            market_slug TEXT,
            symbol TEXT NOT NULL,
            cadence_seconds INTEGER,
            outcome TEXT NOT NULL,
            token_id TEXT NOT NULL,
            event_start_time_utc TEXT,
            event_end_time_utc TEXT,
            settlement_threshold REAL,
            active INTEGER NOT NULL DEFAULT 1,
            closed INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL,
            raw_json TEXT NOT NULL DEFAULT '{}',
            updated_at_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_polymarket_event_universe_event
            ON polymarket_crypto_event_universe(event_slug, condition_id, token_id);
        CREATE INDEX IF NOT EXISTS idx_polymarket_event_universe_window
            ON polymarket_crypto_event_universe(symbol, event_start_time_utc, event_end_time_utc);

        CREATE TABLE IF NOT EXISTS polymarket_event_price_ticks (
            event_price_tick_key TEXT PRIMARY KEY,
            run_id TEXT,
            event_id TEXT,
            event_slug TEXT,
            market_id TEXT,
            condition_id TEXT,
            market_slug TEXT,
            symbol TEXT,
            outcome TEXT,
            token_id TEXT NOT NULL,
            event_start_time_utc TEXT,
            event_end_time_utc TEXT,
            event_type TEXT,
            source TEXT NOT NULL,
            chart_timestamp_utc TEXT,
            system_received_at_utc TEXT NOT NULL,
            system_inserted_at_utc TEXT NOT NULL,
            source_latency_ms REAL,
            insert_latency_ms REAL,
            mid_price REAL,
            best_bid REAL,
            best_ask REAL,
            spread REAL,
            bid_size REAL,
            ask_size REAL,
            depth_top3_bid_size REAL,
            depth_top3_ask_size REAL,
            trade_price REAL,
            trade_size REAL,
            side TEXT,
            mid_price_delta REAL,
            best_bid_delta REAL,
            best_ask_delta REAL,
            trade_price_delta REAL,
            raw_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_polymarket_event_price_ticks_token_time
            ON polymarket_event_price_ticks(token_id, system_received_at_utc);
        CREATE INDEX IF NOT EXISTS idx_polymarket_event_price_ticks_event_time
            ON polymarket_event_price_ticks(event_slug, outcome, system_received_at_utc);

        CREATE TABLE IF NOT EXISTS crypto_stat_backtest_runs (
            backtest_run_id TEXT PRIMARY KEY,
            started_at_utc TEXT NOT NULL,
            completed_at_utc TEXT,
            status TEXT NOT NULL,
            component_scope TEXT NOT NULL,
            symbols_json TEXT NOT NULL,
            start_at_utc TEXT,
            end_at_utc TEXT,
            config_json TEXT NOT NULL DEFAULT '{}',
            summary_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS crypto_stat_component_results (
            result_key TEXT PRIMARY KEY,
            backtest_run_id TEXT NOT NULL,
            component_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            sample_count INTEGER NOT NULL DEFAULT 0,
            hit_count INTEGER NOT NULL DEFAULT 0,
            miss_count INTEGER NOT NULL DEFAULT 0,
            neutral_count INTEGER NOT NULL DEFAULT 0,
            hit_rate REAL,
            avg_forward_return REAL,
            median_forward_return REAL,
            max_drawdown_proxy REAL,
            details_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS crypto_market_data_watermarks (
            service_name TEXT PRIMARY KEY,
            last_run_at_utc TEXT NOT NULL,
            status TEXT NOT NULL,
            state_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE VIEW IF NOT EXISTS v_crypto_options_latest_price_ticks AS
            SELECT t.*
            FROM crypto_price_ticks t
            JOIN (
                SELECT symbol, MAX(observed_at_utc) AS latest_observed_at_utc
                FROM crypto_price_ticks
                GROUP BY symbol
            ) latest
              ON latest.symbol = t.symbol
             AND latest.latest_observed_at_utc = t.observed_at_utc;

        CREATE VIEW IF NOT EXISTS v_crypto_options_latest_indicator_snapshots AS
            SELECT i.*
            FROM crypto_indicator_snapshots i
            JOIN (
                SELECT symbol, interval, indicator_id, MAX(computed_at_utc) AS latest_computed_at_utc
                FROM crypto_indicator_snapshots
                GROUP BY symbol, interval, indicator_id
            ) latest
              ON latest.symbol = i.symbol
             AND latest.interval = i.interval
             AND latest.indicator_id = i.indicator_id
             AND latest.latest_computed_at_utc = i.computed_at_utc;

        CREATE VIEW IF NOT EXISTS v_crypto_options_latest_polymarket_event_prices AS
            SELECT p.*
            FROM polymarket_event_price_ticks p
            JOIN (
                SELECT token_id, MAX(system_received_at_utc) AS latest_received_at_utc
                FROM polymarket_event_price_ticks
                GROUP BY token_id
            ) latest
              ON latest.token_id = p.token_id
             AND latest.latest_received_at_utc = p.system_received_at_utc;
        """
    )


def _normalize_tick_row(row: dict[str, Any], *, run_id: str | None) -> dict[str, Any] | None:
    symbol = str(row.get("symbol") or "").upper()
    source = str(row.get("source") or "unknown")
    observed_at = _text_time(row.get("observed_at_utc") or row.get("observed_at") or _now())
    price = _float(row.get("price"))
    if not symbol or price is None:
        return None
    exchange_ts = _text_time(row.get("exchange_timestamp_utc") or row.get("exchange_timestamp"))
    raw = row.get("raw_json") if isinstance(row.get("raw_json"), dict) else dict(row)
    trade_id = (raw or {}).get("t") or (raw or {}).get("a") or row.get("trade_id")
    tick_key = str(row.get("tick_key") or _stable_key(source, symbol, exchange_ts or observed_at, trade_id, price))
    return {
        "tick_key": tick_key,
        "run_id": run_id or row.get("run_id"),
        "symbol": symbol,
        "source": source,
        "observed_at_utc": observed_at,
        "exchange_timestamp_utc": exchange_ts,
        "price": price,
        "bid": _float(row.get("bid")),
        "ask": _float(row.get("ask")),
        "last_size": _float(row.get("last_size")),
        "volume_24h": _float(row.get("volume_24h")),
        "raw_json": raw,
        "inserted_at_utc": _now(),
    }


def _normalize_candle_row(row: dict[str, Any], *, run_id: str | None) -> dict[str, Any] | None:
    symbol = str(row.get("symbol") or "").upper()
    opened_at = _text_time(row.get("opened_at_utc") or row.get("opened_at") or row.get("timestamp"))
    close = _float(row.get("close"))
    open_ = _float(row.get("open"), default=close)
    high = _float(row.get("high"), default=close)
    low = _float(row.get("low"), default=close)
    if not symbol or not opened_at or close is None or open_ is None or high is None or low is None:
        return None
    exchange = str(row.get("exchange") or "unknown")
    interval = str(row.get("interval") or "1m")
    source = str(row.get("source") or "unknown")
    candle_key = str(row.get("candle_key") or _stable_key(exchange, symbol, interval, opened_at))
    return {
        "candle_key": candle_key,
        "run_id": run_id or row.get("run_id"),
        "symbol": symbol,
        "exchange": exchange,
        "interval": interval,
        "opened_at_utc": opened_at,
        "closed_at_utc": _text_time(row.get("closed_at_utc") or row.get("closed_at")),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": _float(row.get("volume")),
        "trade_count": _int(row.get("trade_count")),
        "source": source,
        "raw_json": row.get("raw_json") if isinstance(row.get("raw_json"), dict) else dict(row),
        "inserted_at_utc": _now(),
    }


def _normalize_indicator_row(row: dict[str, Any]) -> dict[str, Any] | None:
    symbol = str(row.get("symbol") or "").upper()
    indicator_id = str(row.get("indicator_id") or "")
    interval = str(row.get("interval") or "1m")
    computed_at = _text_time(row.get("computed_at_utc") or row.get("computed_at") or _now())
    if not symbol or not indicator_id:
        return None
    candle_opened_at = _text_time(row.get("candle_opened_at_utc") or row.get("candle_opened_at"))
    snapshot_key = str(row.get("snapshot_key") or _stable_key(symbol, interval, indicator_id, computed_at, candle_opened_at))
    return {
        "snapshot_key": snapshot_key,
        "symbol": symbol,
        "interval": interval,
        "indicator_id": indicator_id,
        "computed_at_utc": computed_at,
        "candle_opened_at_utc": candle_opened_at,
        "source": str(row.get("source") or "local_indicator_engine"),
        "direction": row.get("direction"),
        "confidence": _float(row.get("confidence")),
        "signal_value": _float(row.get("signal_value")),
        "components_json": row.get("components_json") or row.get("components") or {},
        "quality_flags_json": row.get("quality_flags_json") or row.get("quality_flags") or {},
        "inserted_at_utc": _now(),
    }


def _normalize_event_context_row(row: dict[str, Any]) -> dict[str, Any] | None:
    symbol = str(row.get("symbol") or "").upper()
    computed_at = _text_time(row.get("computed_at_utc") or row.get("computed_at") or _now())
    if not symbol:
        return None
    context_key = str(
        row.get("context_key")
        or _stable_key(
            row.get("event_id"),
            row.get("market_slug"),
            symbol,
            row.get("side"),
            computed_at,
        )
    )
    return {
        "context_key": context_key,
        "event_id": row.get("event_id"),
        "market_slug": row.get("market_slug"),
        "symbol": symbol,
        "side": row.get("side"),
        "event_threshold_price": _float(row.get("event_threshold_price")),
        "event_end_at_utc": _text_time(row.get("event_end_at_utc") or row.get("event_end_at")),
        "computed_at_utc": computed_at,
        "underlying_price": _float(row.get("underlying_price")),
        "target_delta_abs": _float(row.get("target_delta_abs")),
        "target_delta_signed_for_side": _float(row.get("target_delta_signed_for_side")),
        "trend_15m": row.get("trend_15m"),
        "trend_30m": row.get("trend_30m"),
        "trend_1h": row.get("trend_1h"),
        "indicator_summary_json": row.get("indicator_summary_json") or row.get("indicator_summary") or {},
        "blocker_summary_json": row.get("blocker_summary_json") or row.get("blocker_summary") or {},
        "inserted_at_utc": _now(),
    }


def _normalize_polymarket_event_row(row: dict[str, Any]) -> dict[str, Any] | None:
    token_id = str(row.get("token_id") or row.get("asset_id") or "").strip()
    outcome = str(row.get("outcome") or row.get("raw_outcome") or "").strip()
    symbol = str(row.get("symbol") or row.get("primary_symbol") or "").upper()
    if not token_id or not outcome or not symbol:
        return None
    event_slug = row.get("event_slug") or row.get("market_slug")
    event_token_key = str(row.get("event_token_key") or _stable_key(event_slug, row.get("condition_id"), token_id))
    return {
        "event_token_key": event_token_key,
        "event_id": row.get("event_id"),
        "event_slug": event_slug,
        "market_id": row.get("market_id"),
        "condition_id": row.get("condition_id"),
        "market_slug": row.get("market_slug"),
        "symbol": symbol,
        "cadence_seconds": _int(row.get("cadence_seconds")),
        "outcome": outcome,
        "token_id": token_id,
        "event_start_time_utc": _text_time(row.get("event_start_time_utc") or row.get("window_start_time") or row.get("start_time")),
        "event_end_time_utc": _text_time(row.get("event_end_time_utc") or row.get("window_end_time") or row.get("end_time")),
        "settlement_threshold": _float(row.get("settlement_threshold")),
        "active": 0 if row.get("active") is False else 1,
        "closed": _bool(row.get("closed") or row.get("ended")),
        "source": str(row.get("source") or "polymarket_gamma_event_discovery"),
        "raw_json": row.get("raw_json") if isinstance(row.get("raw_json"), dict) else dict(row),
        "updated_at_utc": _now(),
    }


def _normalize_polymarket_price_row(row: dict[str, Any], *, run_id: str | None) -> dict[str, Any] | None:
    token_id = str(row.get("token_id") or row.get("asset_id") or "").strip()
    if not token_id:
        return None
    received_at = _text_time(row.get("system_received_at_utc") or row.get("received_at_utc") or row.get("observed_at") or _now())
    inserted_at = _now()
    chart_time = _text_time(
        row.get("chart_timestamp_utc")
        or row.get("provider_timestamp_utc")
        or row.get("observed_at")
        or row.get("timestamp")
    )
    mid = _float(row.get("mid_price"))
    bid = _float(row.get("best_bid"))
    ask = _float(row.get("best_ask"))
    if mid is None and bid is not None and ask is not None:
        mid = (bid + ask) / 2.0
    spread = _float(row.get("spread"))
    if spread is None and bid is not None and ask is not None:
        spread = max(0.0, ask - bid)
    source_latency = _float(row.get("source_latency_ms"))
    if source_latency is None and chart_time and received_at:
        source_latency = _time_diff_ms(received_at, chart_time)
    insert_latency = _time_diff_ms(inserted_at, received_at)
    event_price_tick_key = str(
        row.get("event_price_tick_key")
        or _stable_key(
            row.get("source") or "polymarket_event_price",
            token_id,
            row.get("event_type"),
            chart_time,
            received_at,
            mid,
            bid,
            ask,
            row.get("trade_price"),
            row.get("raw_json"),
        )
    )
    return {
        "event_price_tick_key": event_price_tick_key,
        "run_id": run_id or row.get("run_id"),
        "event_id": row.get("event_id"),
        "event_slug": row.get("event_slug"),
        "market_id": row.get("market_id"),
        "condition_id": row.get("condition_id"),
        "market_slug": row.get("market_slug"),
        "symbol": str(row.get("symbol") or "").upper() or None,
        "outcome": row.get("outcome"),
        "token_id": token_id,
        "event_start_time_utc": _text_time(row.get("event_start_time_utc") or row.get("window_start_time")),
        "event_end_time_utc": _text_time(row.get("event_end_time_utc") or row.get("window_end_time")),
        "event_type": row.get("event_type"),
        "source": str(row.get("source") or "polymarket_event_price"),
        "chart_timestamp_utc": chart_time,
        "system_received_at_utc": received_at or inserted_at,
        "system_inserted_at_utc": inserted_at,
        "source_latency_ms": source_latency,
        "insert_latency_ms": insert_latency,
        "mid_price": mid,
        "best_bid": bid,
        "best_ask": ask,
        "spread": spread,
        "bid_size": _float(row.get("bid_size")),
        "ask_size": _float(row.get("ask_size")),
        "depth_top3_bid_size": _float(row.get("depth_top3_bid_size")),
        "depth_top3_ask_size": _float(row.get("depth_top3_ask_size")),
        "trade_price": _float(row.get("trade_price") or row.get("price")),
        "trade_size": _float(row.get("trade_size") or row.get("size")),
        "side": row.get("side"),
        "raw_json": row.get("raw_json") if isinstance(row.get("raw_json"), dict) else dict(row),
    }


def _latest_polymarket_price_for_token(conn: sqlite3.Connection, token_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT mid_price, best_bid, best_ask, trade_price
        FROM polymarket_event_price_ticks
        WHERE token_id=?
        ORDER BY system_received_at_utc DESC
        LIMIT 1
        """,
        (token_id,),
    ).fetchone()
    return dict(row) if row else None


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    for key, value in list(payload.items()):
        if isinstance(value, str) and (key.endswith("_json") or key in {"raw_json", "summary_json", "symbols_json", "state_json"}):
            payload[key] = _loads(value)
    return payload


def _scalar(conn: sqlite3.Connection, query: str) -> int:
    row = conn.execute(query).fetchone()
    return int(row[0] if row else 0)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _loads(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _stable_key(*parts: Any) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text_time(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        import pandas as pd

        parsed = pd.to_datetime(value, utc=True)
        if pd.isna(parsed):
            return None
        return parsed.isoformat()
    except (TypeError, ValueError, OverflowError):
        return str(value)


def _float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        import pandas as pd

        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if value else 0
    return 1 if str(value or "").strip().lower() in {"1", "true", "yes", "y"} else 0


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _delta(current: Any, previous: Any) -> float | None:
    current_float = _float(current)
    previous_float = _float(previous)
    if current_float is None or previous_float is None:
        return None
    return current_float - previous_float


def _time_diff_ms(later: Any, earlier: Any) -> float | None:
    try:
        import pandas as pd

        later_ts = pd.to_datetime(later, utc=True)
        earlier_ts = pd.to_datetime(earlier, utc=True)
        if pd.isna(later_ts) or pd.isna(earlier_ts):
            return None
        return max(0.0, float((later_ts - earlier_ts).total_seconds() * 1000.0))
    except (TypeError, ValueError, OverflowError):
        return None


__all__ = [
    "MARKET_DATA_STORE_SCHEMA_VERSION",
    "connect_market_data_store",
    "default_market_data_store_path",
    "initialize_market_data_store",
    "latest_indicator_snapshots",
    "latest_price_ticks",
    "latest_polymarket_event_prices",
    "load_candles",
    "market_data_store_summary",
    "prune_market_data_retention",
    "record_price_ingest_run",
    "fresh_polymarket_event_universe",
    "upsert_candles",
    "upsert_event_indicator_context",
    "upsert_indicator_snapshots",
    "upsert_price_ticks",
    "upsert_polymarket_event_price_ticks",
    "upsert_polymarket_event_universe",
]
