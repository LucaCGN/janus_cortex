from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from crypto_options_app.data_nodes.polymarket_crypto.accounts import build_polymarket_account_research_report
from crypto_options_app.data_nodes.polymarket_crypto.history import fetch_current_order_book, normalize_clob_observations, normalize_order_book_snapshot
from crypto_options_app.data_nodes.polymarket_crypto.markets import fetch_recurring_updown_events_by_slug_range, normalize_polymarket_crypto_events


POLYMARKET_MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
BINANCE_PUBLIC_BASE_URL = "https://data-api.binance.vision"
BINANCE_SYMBOL_MAP = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}


@dataclass(frozen=True)
class LiveCaptureTarget:
    event_id: str
    event_slug: str
    market_id: str
    condition_id: str | None
    token_id: str
    outcome: str
    symbol: str
    window_start_time: str
    window_end_time: str
    settlement_threshold: float | None = None


def discover_live_crypto_updown_targets(
    *,
    symbols: list[str],
    cadence_minutes: int = 5,
    lookback_minutes: int = 5,
    lookahead_minutes: int = 65,
    now: datetime | None = None,
) -> tuple[list[LiveCaptureTarget], dict[str, Any]]:
    """Discover current/future recurring crypto up/down targets by generated slugs."""

    now = now or datetime.now(timezone.utc)
    all_events: list[dict[str, Any]] = []
    attempts: dict[str, Any] = {}
    for symbol in symbols:
        events, symbol_attempts = fetch_recurring_updown_events_by_slug_range(
            symbol=symbol,
            cadence_minutes=cadence_minutes,
            start=now - timedelta(minutes=lookback_minutes),
            end=now + timedelta(minutes=lookahead_minutes),
        )
        all_events.extend(events)
        attempts[symbol.upper()] = symbol_attempts
    events_df = normalize_polymarket_crypto_events(all_events)
    targets: list[LiveCaptureTarget] = []
    if events_df.empty:
        return [], {"attempts": attempts, "event_rows": 0}
    for _, row in events_df.dropna(subset=["token_id"]).iterrows():
        targets.append(
            LiveCaptureTarget(
                event_id=str(row.get("event_id") or ""),
                event_slug=str(row.get("event_slug") or row.get("market_slug") or ""),
                market_id=str(row.get("market_id") or ""),
                condition_id=str(row.get("condition_id") or "") or None,
                token_id=str(row.get("token_id") or ""),
                outcome=str(row.get("outcome") or ""),
                symbol=str(row.get("primary_symbol") or "").upper(),
                window_start_time=str(row.get("window_start_time") or ""),
                window_end_time=str(row.get("window_end_time") or ""),
                settlement_threshold=_to_float(row.get("settlement_threshold")),
            )
        )
    return targets, {
        "attempts": attempts,
        "event_rows": int(len(events_df)),
        "distinct_events": int(events_df["event_id"].nunique()) if "event_id" in events_df.columns else 0,
        "distinct_markets": int(events_df["market_id"].nunique()) if "market_id" in events_df.columns else 0,
    }


async def run_live_crypto_options_capture(
    *,
    output_dir: str | Path,
    symbols: list[str],
    seconds: float = 1800.0,
    cadence_minutes: int = 5,
    lookback_minutes: int = 5,
    lookahead_minutes: int = 65,
    binance_poll_seconds: float = 1.0,
    book_poll_seconds: float = 5.0,
    max_book_poll_tokens: int = 16,
    account_handles: list[str] | None = None,
    account_snapshot_seconds: float = 300.0,
) -> dict[str, Any]:
    """Run a read-only live market-data capture session."""

    import websockets

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    targets, discovery = discover_live_crypto_updown_targets(
        symbols=symbols,
        cadence_minutes=cadence_minutes,
        lookback_minutes=lookback_minutes,
        lookahead_minutes=lookahead_minutes,
    )
    target_by_token = {target.token_id: target for target in targets}
    paths = _paths(root)
    metadata = {
        "schema_version": "crypto_options_live_capture_session_v1",
        "started_at_utc": _utc_now_iso(),
        "orders_allowed": False,
        "live_trading_authorized": False,
        "symbols": [symbol.upper() for symbol in symbols],
        "duration_seconds": seconds,
        "cadence_minutes": cadence_minutes,
        "book_poll_seconds": book_poll_seconds,
        "max_book_poll_tokens": max_book_poll_tokens,
        "target_count": len(targets),
        "targets": [target.__dict__ for target in targets],
        "discovery": discovery,
        "paths": {key: str(value) for key, value in paths.items()},
    }
    _write_json(paths["metadata"], metadata)
    status = {
        "status": "running",
        "orders_allowed": False,
        "started_at_utc": metadata["started_at_utc"],
        "target_count": len(targets),
        "raw_message_count": 0,
        "normalized_row_count": 0,
        "binance_tick_count": 0,
        "book_poll_count": 0,
        "book_poll_row_count": 0,
        "account_snapshot_count": 0,
        "last_error": None,
    }
    _write_json(paths["status"], status)
    if not targets:
        status.update({"status": "blocked", "last_error": "no_live_crypto_targets_discovered", "completed_at_utc": _utc_now_iso()})
        _write_json(paths["status"], status)
        return status

    deadline = time.monotonic() + float(seconds) if seconds > 0 else None
    stop_event = asyncio.Event()
    tasks = [
        asyncio.create_task(_capture_polymarket_ws(paths, list(target_by_token), target_by_token, status, stop_event)),
        asyncio.create_task(_capture_binance_ticker(paths, symbols, binance_poll_seconds, status, stop_event)),
    ]
    if book_poll_seconds and float(book_poll_seconds) > 0:
        tasks.append(
            asyncio.create_task(
                _capture_current_books(
                    paths,
                    targets,
                    book_poll_seconds,
                    max_book_poll_tokens,
                    status,
                    stop_event,
                )
            )
        )
    if account_handles:
        tasks.append(asyncio.create_task(_capture_accounts(paths, account_handles, account_snapshot_seconds, status, stop_event)))
    try:
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                break
            await asyncio.sleep(1.0)
            status["updated_at_utc"] = _utc_now_iso()
            _write_json(paths["status"], status)
    finally:
        stop_event.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        status["status"] = "completed"
        status["completed_at_utc"] = _utc_now_iso()
        _write_json(paths["status"], status)
    return status


async def _capture_polymarket_ws(
    paths: dict[str, Path],
    token_ids: list[str],
    target_by_token: dict[str, LiveCaptureTarget],
    status: dict[str, Any],
    stop_event: asyncio.Event,
) -> None:
    import websockets

    subscription = {"assets_ids": token_ids, "type": "market", "custom_feature_enabled": True}
    try:
        async with websockets.connect(
            POLYMARKET_MARKET_WS_URL,
            ping_interval=10,
            ping_timeout=10,
            open_timeout=30,
        ) as websocket:
            await websocket.send(json.dumps(subscription))
            while not stop_event.is_set():
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                received_at = datetime.now(timezone.utc)
                payload = _loads_ws_message(raw)
                raw_records = payload if isinstance(payload, list) else [payload]
                for record in raw_records:
                    if not isinstance(record, dict):
                        continue
                    enriched = {
                        "received_at_utc": received_at.isoformat(),
                        "source_latency_ms": _source_latency_ms(record, received_at),
                        "payload": record,
                    }
                    _append_jsonl(paths["polymarket_raw"], enriched)
                    status["raw_message_count"] = int(status.get("raw_message_count") or 0) + 1
                    for normalized in _normalize_ws_payload(record, received_at, target_by_token):
                        _append_jsonl(paths["polymarket_normalized"], normalized)
                        status["normalized_row_count"] = int(status.get("normalized_row_count") or 0) + 1
    except Exception as exc:  # noqa: BLE001 - capture status should preserve provider failures.
        status["last_error"] = f"polymarket_ws:{type(exc).__name__}:{exc}"


async def _capture_binance_ticker(
    paths: dict[str, Path],
    symbols: list[str],
    interval_seconds: float,
    status: dict[str, Any],
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        captured_at = datetime.now(timezone.utc)
        for symbol in symbols:
            try:
                payload = _fetch_binance_ticker(symbol)
                row = {
                    "captured_at_utc": captured_at.isoformat(),
                    "symbol": symbol.upper(),
                    "exchange_symbol": BINANCE_SYMBOL_MAP.get(symbol.upper(), symbol.upper()),
                    "price": _to_float(payload.get("price")),
                    "source": "binance_public_ticker_price",
                    "raw_json": payload,
                }
                _append_jsonl(paths["binance_ticker"], row)
                status["binance_tick_count"] = int(status.get("binance_tick_count") or 0) + 1
            except Exception as exc:  # noqa: BLE001 - capture should continue for other symbols.
                status["last_error"] = f"binance_ticker:{type(exc).__name__}:{exc}"
        await _sleep_or_stop(stop_event, max(float(interval_seconds), 0.2))


async def _capture_accounts(
    paths: dict[str, Path],
    account_handles: list[str],
    interval_seconds: float,
    status: dict[str, Any],
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        captured_at = _utc_now_iso()
        for handle in account_handles:
            try:
                report = build_polymarket_account_research_report(
                    handle=handle,
                    activity_pages=1,
                    positions_pages=1,
                    page_limit=500,
                )
                _append_jsonl(paths["accounts"], {"captured_at_utc": captured_at, "handle": handle, "report": report})
                status["account_snapshot_count"] = int(status.get("account_snapshot_count") or 0) + 1
            except Exception as exc:  # noqa: BLE001 - account capture should not stop book capture.
                status["last_error"] = f"account:{handle}:{type(exc).__name__}:{exc}"
        await _sleep_or_stop(stop_event, max(float(interval_seconds), 30.0))


async def _capture_current_books(
    paths: dict[str, Path],
    targets: list[LiveCaptureTarget],
    interval_seconds: float,
    max_tokens: int,
    status: dict[str, Any],
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        captured_at = datetime.now(timezone.utc)
        for target in _book_poll_candidates(targets, captured_at, max_tokens=max_tokens):
            try:
                payload = fetch_current_order_book(target.token_id)
                frame = normalize_order_book_snapshot(
                    payload,
                    event_id=target.event_id,
                    market_id=target.market_id,
                    outcome=target.outcome,
                    symbol=target.symbol,
                    source="polymarket_current_order_book_live_poll",
                )
                for row in frame.to_dict(orient="records"):
                    row.update(
                        {
                            "received_at_utc": captured_at.isoformat(),
                            "event_slug": target.event_slug,
                            "condition_id": target.condition_id,
                            "window_start_time": target.window_start_time,
                            "window_end_time": target.window_end_time,
                            "orders_allowed": False,
                        }
                    )
                    _append_jsonl(paths["polymarket_normalized"], _jsonable(row))
                    status["book_poll_row_count"] = int(status.get("book_poll_row_count") or 0) + 1
                status["book_poll_count"] = int(status.get("book_poll_count") or 0) + 1
            except Exception as exc:  # noqa: BLE001 - individual book failures should not stop capture.
                status["last_error"] = f"book_poll:{target.token_id}:{type(exc).__name__}:{exc}"
        await _sleep_or_stop(stop_event, max(float(interval_seconds), 1.0))


def _normalize_ws_payload(
    record: dict[str, Any],
    received_at: datetime,
    target_by_token: dict[str, LiveCaptureTarget],
) -> list[dict[str, Any]]:
    event_type = str(record.get("event_type") or "")
    rows: list[dict[str, Any]] = []
    if event_type == "price_change" and isinstance(record.get("price_changes"), list):
        for change in record["price_changes"]:
            merged = {**record, **change}
            rows.append(_normalized_row(merged, received_at, target_by_token))
        return rows
    rows.append(_normalized_row(record, received_at, target_by_token))
    return [row for row in rows if row.get("token_id")]


def _normalized_row(record: dict[str, Any], received_at: datetime, target_by_token: dict[str, LiveCaptureTarget]) -> dict[str, Any]:
    token_id = str(record.get("asset_id") or record.get("token_id") or "")
    target = target_by_token.get(token_id)
    observed_at = _message_time(record) or received_at
    bids = _depth_levels(record.get("bids"), side="bid")
    asks = _depth_levels(record.get("asks"), side="ask")
    best_bid = _to_float(record.get("best_bid"))
    best_ask = _to_float(record.get("best_ask"))
    if best_bid is None and bids:
        best_bid = bids[0]["price"]
    if best_ask is None and asks:
        best_ask = asks[0]["price"]
    normalized = normalize_clob_observations(
        [
            {
                "token_id": token_id,
                "market_id": record.get("market"),
                "observed_at": observed_at,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "bid_size": bids[0]["size"] if bids else None,
                "ask_size": asks[0]["size"] if asks else None,
                "depth_top3_bid_size": _depth_size(bids, limit=3),
                "depth_top3_ask_size": _depth_size(asks, limit=3),
                "book_bids": bids,
                "book_asks": asks,
                "price": record.get("price"),
                "source": f"polymarket_ws_{record.get('event_type')}",
                "raw_json": record,
            }
        ]
    )
    row = normalized.iloc[0].to_dict() if not normalized.empty else {"token_id": token_id, "observed_at": observed_at}
    row.update(
        {
            "received_at_utc": received_at.isoformat(),
            "source_latency_ms": _source_latency_ms(record, received_at),
            "event_type": record.get("event_type"),
            "side": record.get("side"),
            "trade_price": _to_float(record.get("price")),
            "trade_size": _to_float(record.get("size")),
            "event_id": target.event_id if target else row.get("event_id"),
            "event_slug": target.event_slug if target else None,
            "market_id": target.market_id if target else row.get("market_id"),
            "condition_id": target.condition_id if target else None,
            "outcome": target.outcome if target else row.get("outcome"),
            "symbol": target.symbol if target else row.get("symbol"),
            "window_start_time": target.window_start_time if target else None,
            "window_end_time": target.window_end_time if target else None,
            "orders_allowed": False,
        }
    )
    return _jsonable(row)


def _fetch_binance_ticker(symbol: str) -> dict[str, Any]:
    exchange_symbol = BINANCE_SYMBOL_MAP.get(str(symbol).upper(), str(symbol).upper())
    request = Request(
        f"{BINANCE_PUBLIC_BASE_URL}/api/v3/ticker/price?{urlencode({'symbol': exchange_symbol})}",
        headers={"User-Agent": "Janus crypto-options research capture (read-only)", "Accept": "application/json"},
    )
    with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed public market-data URL.
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def fetch_binance_ticker_price(symbol: str) -> float | None:
    """Return the latest public Binance price for the crypto symbol."""

    return _to_float(_fetch_binance_ticker(symbol).get("price"))


def fetch_binance_recent_closes(symbol: str, *, interval: str = "1m", limit: int = 61) -> list[dict[str, Any]]:
    """Return recent public Binance candle closes for lightweight event context."""

    exchange_symbol = BINANCE_SYMBOL_MAP.get(str(symbol).upper(), str(symbol).upper())
    query = urlencode({"symbol": exchange_symbol, "interval": interval, "limit": max(2, int(limit))})
    request = Request(
        f"{BINANCE_PUBLIC_BASE_URL}/api/v3/klines?{query}",
        headers={"User-Agent": "Janus crypto-options research capture (read-only)", "Accept": "application/json"},
    )
    with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed public market-data URL.
        payload = json.loads(response.read().decode("utf-8"))
    rows = payload if isinstance(payload, list) else []
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 5:
            continue
        output.append(
            {
                "opened_at_ms": row[0],
                "close": _to_float(row[4]),
                "raw": row,
            }
        )
    return output


def build_underlying_context(symbol: str) -> dict[str, Any]:
    """Build simple 15m/30m/1h trend context from public Binance candles."""

    closes = [row["close"] for row in fetch_binance_recent_closes(symbol, interval="1m", limit=61) if row.get("close") is not None]
    latest = closes[-1] if closes else fetch_binance_ticker_price(symbol)
    return {
        "schema_version": "crypto_options_underlying_context_v1",
        "symbol": str(symbol).upper(),
        "underlying_price": latest,
        "underlying_trend_15m": _trend_from_closes(closes, lookback=15),
        "underlying_trend_30m": _trend_from_closes(closes, lookback=30),
        "underlying_trend_1h": _trend_from_closes(closes, lookback=60),
        "source": "binance_public_klines",
    }


def _trend_from_closes(closes: list[float], *, lookback: int) -> str | None:
    if len(closes) <= lookback:
        return None
    start = _to_float(closes[-lookback - 1])
    end = _to_float(closes[-1])
    if start is None or end is None:
        return None
    change = end - start
    threshold = max(abs(start) * 0.00015, 1e-9)
    if change > threshold:
        return "up"
    if change < -threshold:
        return "down"
    return "sideways"


async def _sleep_or_stop(stop_event: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=max(float(seconds), 0.0))
    except asyncio.TimeoutError:
        return


def _book_poll_candidates(targets: list[LiveCaptureTarget], now: datetime, *, max_tokens: int) -> list[LiveCaptureTarget]:
    scored: list[tuple[float, LiveCaptureTarget]] = []
    for target in targets:
        start_at = _parse_time(target.window_start_time)
        end_at = _parse_time(target.window_end_time)
        if start_at is None or end_at is None:
            continue
        if end_at < now - timedelta(minutes=2):
            continue
        if start_at > now + timedelta(minutes=20):
            continue
        if start_at <= now <= end_at:
            score = 0.0
        elif now < start_at:
            score = (start_at - now).total_seconds()
        else:
            score = (now - end_at).total_seconds() + 10_000.0
        scored.append((score, target))
    scored.sort(key=lambda item: (item[0], item[1].symbol, item[1].event_slug, item[1].outcome))
    return [target for _, target in scored[: max(0, int(max_tokens))]]


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = pd.to_datetime(value, utc=True, errors="coerce")
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _paths(root: Path) -> dict[str, Path]:
    return {
        "metadata": root / "metadata.json",
        "status": root / "status.json",
        "polymarket_raw": root / "polymarket_ws_raw.jsonl",
        "polymarket_normalized": root / "polymarket_ws_normalized.jsonl",
        "binance_ticker": root / "binance_ticker.jsonl",
        "accounts": root / "account_snapshots.jsonl",
    }


def _loads_ws_message(raw: Any) -> Any:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _message_time(record: dict[str, Any]) -> datetime | None:
    timestamp = record.get("timestamp")
    if timestamp in (None, ""):
        return None
    try:
        value = float(timestamp)
    except (TypeError, ValueError):
        return None
    unit_divisor = 1000.0 if value >= 10_000_000_000 else 1.0
    try:
        return datetime.fromtimestamp(value / unit_divisor, tz=timezone.utc)
    except (OSError, ValueError):
        return None


def _source_latency_ms(record: dict[str, Any], received_at: datetime) -> float | None:
    source_at = _message_time(record)
    if source_at is None:
        return None
    return max(0.0, (received_at - source_at).total_seconds() * 1000.0)


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_jsonable(record), sort_keys=True) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(payload), indent=2, sort_keys=True), encoding="utf-8")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _depth_levels(raw: Any, *, side: str | None = None) -> list[dict[str, float]]:
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    levels: list[dict[str, float]] = []
    for item in raw:
        if isinstance(item, dict):
            price = _to_float(item.get("price"))
            size = _to_float(item.get("size"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            price = _to_float(item[0])
            size = _to_float(item[1])
        else:
            continue
        if price is None or size is None:
            continue
        levels.append({"price": price, "size": size})
    if side == "bid":
        levels.sort(key=lambda level: level["price"], reverse=True)
    elif side == "ask":
        levels.sort(key=lambda level: level["price"])
    return levels


def _depth_size(levels: list[dict[str, float]], *, limit: int) -> float | None:
    if not levels:
        return None
    return float(sum(level["size"] for level in levels[:limit]))


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


__all__ = [
    "LiveCaptureTarget",
    "discover_live_crypto_updown_targets",
    "run_live_crypto_options_capture",
]
