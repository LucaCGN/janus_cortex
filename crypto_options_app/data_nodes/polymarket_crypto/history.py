from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

import pandas as pd

POLYMARKET_CLOB_BASE_URL = "https://clob.polymarket.com"
POLYMARKET_PUBLIC_HEADERS = {
    "User-Agent": "Janus crypto-options research (read-only)",
    "Accept": "application/json,text/plain,*/*",
}
PMXT_V2_DIRECT_BASE_URL = "https://r2v2.pmxt.dev"
PMXT_PUBLIC_HEADERS = {
    "User-Agent": "Janus crypto-options research (read-only)",
    "Accept": "application/octet-stream,*/*",
}


def fetch_clob_prices_history(
    *,
    token_id: str,
    start_ts: int | float | None = None,
    end_ts: int | float | None = None,
    interval: str | None = None,
    fidelity: int | None = None,
) -> dict[str, Any]:
    """Fetch public Polymarket outcome-token price history.

    The `market` query parameter is a token/asset id in Polymarket's CLOB docs.
    This is read-only public market data and does not authenticate or create orders.
    """

    query: dict[str, Any] = {"market": str(token_id)}
    if start_ts is not None:
        query["startTs"] = float(start_ts)
    if end_ts is not None:
        query["endTs"] = float(end_ts)
    if interval:
        query["interval"] = str(interval)
    if fidelity is not None:
        query["fidelity"] = int(fidelity)
    request = Request(f"{POLYMARKET_CLOB_BASE_URL}/prices-history?{urlencode(query)}", headers=POLYMARKET_PUBLIC_HEADERS)
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed public provider URL.
        import json

        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {"history": []}


def fetch_clob_prices_history_for_outcomes(
    events_df: pd.DataFrame,
    *,
    start_ts: int | float | None = None,
    end_ts: int | float | None = None,
    interval: str | None = None,
    fidelity: int | None = None,
) -> pd.DataFrame:
    """Fetch and normalize public price history for every token in event rows."""

    if events_df.empty or "token_id" not in events_df.columns:
        return normalize_clob_observations([])
    frames: list[pd.DataFrame] = []
    for _, row in events_df.dropna(subset=["token_id"]).iterrows():
        token_id = str(row["token_id"])
        if not token_id:
            continue
        payload = fetch_clob_prices_history(token_id=token_id, start_ts=start_ts, end_ts=end_ts, interval=interval, fidelity=fidelity)
        frames.append(
            normalize_clob_price_history(
                payload,
                token_id=token_id,
                event_id=row.get("event_id"),
                market_id=row.get("market_id"),
                outcome=row.get("outcome"),
                symbol=row.get("primary_symbol"),
            )
        )
    return pd.concat(frames, ignore_index=True) if frames else normalize_clob_observations([])


def fetch_clob_prices_history_for_event_windows(
    events_df: pd.DataFrame,
    *,
    pre_window_seconds: int = 300,
    post_window_seconds: int = 300,
    fidelity: int = 1,
) -> pd.DataFrame:
    """Fetch public outcome-token odds history bounded around each event window."""

    if events_df.empty or "token_id" not in events_df.columns:
        return normalize_clob_observations([])
    frames: list[pd.DataFrame] = []
    for _, row in events_df.dropna(subset=["token_id"]).iterrows():
        start_at = pd.to_datetime(row.get("window_start_time"), utc=True, errors="coerce")
        end_at = pd.to_datetime(row.get("window_end_time"), utc=True, errors="coerce")
        if pd.isna(start_at) or pd.isna(end_at):
            continue
        start_ts = int(start_at.timestamp()) - int(pre_window_seconds)
        end_ts = int(end_at.timestamp()) + int(post_window_seconds)
        token_id = str(row["token_id"])
        payload = fetch_clob_prices_history(token_id=token_id, start_ts=start_ts, end_ts=end_ts, fidelity=fidelity)
        frames.append(
            normalize_clob_price_history(
                payload,
                token_id=token_id,
                event_id=row.get("event_id"),
                market_id=row.get("market_id"),
                outcome=row.get("outcome"),
                symbol=row.get("primary_symbol"),
                source=f"polymarket_clob_prices_history_window_fidelity_{fidelity}",
            )
        )
    return pd.concat(frames, ignore_index=True) if frames else normalize_clob_observations([])


def fetch_current_order_book(token_id: str) -> dict[str, Any]:
    """Fetch the current public order book for a token id."""

    request = Request(f"{POLYMARKET_CLOB_BASE_URL}/book?{urlencode({'token_id': str(token_id)})}", headers=POLYMARKET_PUBLIC_HEADERS)
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed public provider URL.
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def fetch_current_order_books_for_outcomes(events_df: pd.DataFrame) -> pd.DataFrame:
    """Fetch current public order books for every token in event rows."""

    if events_df.empty or "token_id" not in events_df.columns:
        return normalize_clob_observations([])
    frames: list[pd.DataFrame] = []
    for _, row in events_df.dropna(subset=["token_id"]).iterrows():
        token_id = str(row["token_id"])
        if not token_id:
            continue
        try:
            payload = fetch_current_order_book(token_id)
        except Exception as exc:  # noqa: BLE001 - network provider failures become structured rows.
            frames.append(
                normalize_clob_observations(
                    [
                        {
                            "token_id": token_id,
                            "event_id": row.get("event_id"),
                            "market_id": row.get("market_id"),
                            "outcome": row.get("outcome"),
                            "symbol": row.get("primary_symbol"),
                            "observed_at": datetime.now(timezone.utc),
                            "source": "polymarket_current_order_book_fetch_failed",
                            "raw_json": {"error": str(exc)},
                        }
                    ]
                )
            )
            continue
        frames.append(
            normalize_order_book_snapshot(
                payload,
                event_id=row.get("event_id"),
                market_id=row.get("market_id"),
                outcome=row.get("outcome"),
                symbol=row.get("primary_symbol"),
            )
        )
    return pd.concat(frames, ignore_index=True) if frames else normalize_clob_observations([])


def normalize_order_book_snapshot(
    payload: dict[str, Any],
    *,
    event_id: str | None = None,
    market_id: str | None = None,
    outcome: str | None = None,
    symbol: str | None = None,
    source: str = "polymarket_current_order_book",
) -> pd.DataFrame:
    """Normalize a public Polymarket `/book` response into one executable quote row."""

    if not payload:
        return normalize_clob_observations([])
    bids = _depth_levels(payload.get("bids"), side="bid")
    asks = _depth_levels(payload.get("asks"), side="ask")
    best_bid = bids[0]["price"] if bids else None
    best_ask = asks[0]["price"] if asks else None
    observed_at = _first_value(payload, "timestamp", "captured_at", "observed_at") or datetime.now(timezone.utc)
    row = {
        "event_id": event_id,
        "market_id": market_id or payload.get("market"),
        "token_id": payload.get("asset_id") or payload.get("token_id"),
        "outcome": outcome,
        "symbol": symbol,
        "observed_at": observed_at,
        "mid_price": _mid(best_bid, best_ask),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_size": bids[0]["size"] if bids else None,
        "ask_size": asks[0]["size"] if asks else None,
        "depth_top3_bid_size": _depth_size(bids, limit=3),
        "depth_top3_ask_size": _depth_size(asks, limit=3),
        "book_bids": bids,
        "book_asks": asks,
        "spread": None,
        "source": source,
        "price_meaning": "polymarket_executable_orderbook_quote_not_underlying_price",
        "raw_json": payload,
    }
    return normalize_clob_observations([row])


def read_pmxt_orderbook_parquet(paths: list[str | Path], *, token_ids: set[str] | None = None) -> pd.DataFrame:
    """Read PMXT v2 Parquet files and normalize rows into Janus CLOB observations."""

    frames: list[pd.DataFrame] = []
    for path in paths:
        frame = _read_pmxt_parquet_filtered(path, token_ids=token_ids)
        if token_ids and "asset_id" in frame.columns:
            frame = frame[frame["asset_id"].astype(str).isin(token_ids)]
        frames.append(normalize_pmxt_orderbook_records(frame.to_dict(orient="records")))
    return pd.concat(frames, ignore_index=True) if frames else normalize_clob_observations([])


def pmxt_orderbook_hour_url(hour: Any) -> str:
    hour_at = pd.to_datetime(hour, utc=True).floor("h")
    stamp = hour_at.strftime("%Y-%m-%dT%H")
    return f"{PMXT_V2_DIRECT_BASE_URL}/polymarket_orderbook_{stamp}.parquet"


def pmxt_event_hours(events_df: pd.DataFrame) -> list[pd.Timestamp]:
    if events_df.empty or "window_start_time" not in events_df.columns or "window_end_time" not in events_df.columns:
        return []
    hours: set[pd.Timestamp] = set()
    for _, row in events_df.drop_duplicates(subset=["event_id", "market_id", "window_start_time", "window_end_time"]).iterrows():
        start_at = pd.to_datetime(row.get("window_start_time"), utc=True, errors="coerce")
        end_at = pd.to_datetime(row.get("window_end_time"), utc=True, errors="coerce")
        if pd.isna(start_at) or pd.isna(end_at):
            continue
        current = start_at.floor("h")
        while current <= end_at.floor("h"):
            hours.add(current)
            current += pd.Timedelta(hours=1)
    return sorted(hours)


def probe_pmxt_orderbook_hour(hour: Any) -> dict[str, Any]:
    """Check whether a PMXT hourly Parquet file is reachable."""

    url = pmxt_orderbook_hour_url(hour)
    try:
        request = Request(url, headers={**PMXT_PUBLIC_HEADERS, "Range": "bytes=0-3"})
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed public archive URL.
            first_bytes = response.read(4)
            size = _content_range_total(response.headers.get("Content-Range"))
            return {
                "hour": pd.to_datetime(hour, utc=True).floor("h").isoformat(),
                "url": url,
                "available": response.status in {200, 206} and first_bytes == b"PAR1",
                "http_status": int(response.status),
                "content_length": size,
                "blocker": None,
            }
    except Exception as exc:  # noqa: BLE001 - availability probe should be non-fatal.
        return {
            "hour": pd.to_datetime(hour, utc=True).floor("h").isoformat(),
            "url": url,
            "available": False,
            "http_status": None,
            "content_length": None,
            "blocker": f"pmxt_hour_unavailable:{type(exc).__name__}:{exc}",
        }


def download_pmxt_orderbook_hour(
    hour: Any,
    *,
    cache_dir: str | Path,
    max_download_bytes: int | None = None,
) -> dict[str, Any]:
    """Download one PMXT hourly Parquet file into a local cache."""

    probe = probe_pmxt_orderbook_hour(hour)
    target = Path(cache_dir) / Path(urlparse(str(probe["url"])).path).name
    probe["local_path"] = str(target)
    if not probe["available"]:
        return probe
    if max_download_bytes is not None and probe.get("content_length") and int(probe["content_length"]) > int(max_download_bytes):
        probe["available"] = False
        probe["blocker"] = f"pmxt_hour_exceeds_max_download_bytes:{probe['content_length']}>{max_download_bytes}"
        return probe
    if target.exists() and target.stat().st_size > 0:
        probe["downloaded"] = False
        probe["cached"] = True
        return probe
    target.parent.mkdir(parents=True, exist_ok=True)
    request = Request(str(probe["url"]), headers=PMXT_PUBLIC_HEADERS)
    with urlopen(request, timeout=120) as response, target.open("wb") as handle:  # noqa: S310 - fixed public archive URL.
        shutil.copyfileobj(response, handle, length=1024 * 1024)
    probe["downloaded"] = True
    probe["cached"] = False
    probe["content_length"] = target.stat().st_size
    return probe


def download_pmxt_orderbooks_for_events(
    events_df: pd.DataFrame,
    *,
    cache_dir: str | Path,
    max_download_bytes: int | None = None,
    pre_window_seconds: int = 900,
    post_window_seconds: int = 0,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Download PMXT files for event hours and normalize only matching outcome tokens."""

    attempts: list[dict[str, Any]] = []
    local_paths: list[str] = []
    for hour in pmxt_event_hours(events_df):
        attempt = download_pmxt_orderbook_hour(hour, cache_dir=cache_dir, max_download_bytes=max_download_bytes)
        attempts.append(attempt)
        if not attempt.get("blocker") and attempt.get("local_path"):
            local_paths.append(str(attempt["local_path"]))
    token_ids = set(events_df["token_id"].dropna().astype(str)) if "token_id" in events_df.columns else None
    frame = read_pmxt_orderbook_parquet(local_paths, token_ids=token_ids) if local_paths else normalize_clob_observations([])
    frame = filter_clob_observations_to_event_windows(
        frame,
        events_df,
        pre_window_seconds=pre_window_seconds,
        post_window_seconds=post_window_seconds,
    )
    return frame, attempts


def filter_clob_observations_to_event_windows(
    clob_df: pd.DataFrame,
    events_df: pd.DataFrame,
    *,
    pre_window_seconds: int = 900,
    post_window_seconds: int = 0,
) -> pd.DataFrame:
    """Keep token observations inside each event's replay window."""

    if clob_df.empty or events_df.empty or "token_id" not in clob_df.columns or "token_id" not in events_df.columns:
        return clob_df
    windows = events_df[["token_id", "event_id", "market_id", "window_start_time", "window_end_time"]].dropna(subset=["token_id"]).copy()
    windows["token_id"] = windows["token_id"].astype(str)
    windows["window_start_time"] = pd.to_datetime(windows["window_start_time"], utc=True, errors="coerce")
    windows["window_end_time"] = pd.to_datetime(windows["window_end_time"], utc=True, errors="coerce")
    windows = windows.dropna(subset=["window_start_time", "window_end_time"]).drop_duplicates(subset=["token_id"])
    if windows.empty:
        return clob_df
    frame = clob_df.copy()
    frame["token_id"] = frame["token_id"].astype(str)
    frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True, errors="coerce")
    joined = frame.merge(windows, on="token_id", how="left", suffixes=("", "_event_window"))
    start_at = joined["window_start_time"] - pd.to_timedelta(int(pre_window_seconds), unit="s")
    end_at = joined["window_end_time"] + pd.to_timedelta(int(post_window_seconds), unit="s")
    kept = joined[(joined["observed_at"] >= start_at) & (joined["observed_at"] <= end_at)].copy()
    if "event_id_event_window" in kept.columns:
        kept["event_id"] = kept["event_id"].fillna(kept["event_id_event_window"])
    if "market_id_event_window" in kept.columns:
        kept["market_id"] = kept["market_id"].fillna(kept["market_id_event_window"])
    drop_columns = [column for column in kept.columns if column.endswith("_event_window") or column in {"window_start_time", "window_end_time"}]
    return kept.drop(columns=drop_columns, errors="ignore").sort_values(["token_id", "observed_at"], kind="mergesort").reset_index(drop=True)


def normalize_pmxt_orderbook_records(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Normalize PMXT v2 orderbook rows.

    PMXT v2 stores Polymarket CLOB websocket events in hourly Parquet files with
    `asset_id`, `timestamp_received`, `bids`, `asks`, `best_bid`, and `best_ask`
    fields where available.
    """

    rows: list[dict[str, Any]] = []
    for record in records:
        token_id = str(_first_value(record, "asset_id", "token_id") or "")
        observed_at = _first_value(record, "timestamp", "observed_at", "timestamp_received")
        if not token_id or not observed_at:
            continue
        bids = _depth_levels(record.get("bids"), side="bid")
        asks = _depth_levels(record.get("asks"), side="ask")
        best_bid = _to_float(record.get("best_bid"))
        best_ask = _to_float(record.get("best_ask"))
        if best_bid is None and bids:
            best_bid = bids[0]["price"]
        if best_ask is None and asks:
            best_ask = asks[0]["price"]
        rows.append(
            {
                "event_id": record.get("event_id"),
                "market_id": _market_to_str(record.get("market")),
                "token_id": token_id,
                "outcome": record.get("outcome"),
                "observed_at": observed_at,
                "mid_price": _to_float(record.get("price")) if record.get("event_type") == "last_trade_price" else _mid(best_bid, best_ask),
                "best_bid": best_bid,
                "best_ask": best_ask,
                "bid_size": bids[0]["size"] if bids else None,
                "ask_size": asks[0]["size"] if asks else None,
                "depth_top3_bid_size": _depth_size(bids, limit=3),
                "depth_top3_ask_size": _depth_size(asks, limit=3),
                "book_bids": bids,
                "book_asks": asks,
                "spread": None,
                "source": "pmxt_polymarket_v2_orderbook_archive",
                "price_meaning": "polymarket_executable_orderbook_quote_not_underlying_price",
                "raw_json": record,
            }
        )
    return normalize_clob_observations(rows)


def normalize_clob_price_history(
    payload: dict[str, Any],
    *,
    token_id: str,
    event_id: str | None = None,
    market_id: str | None = None,
    outcome: str | None = None,
    symbol: str | None = None,
    source: str = "polymarket_clob_prices_history",
) -> pd.DataFrame:
    """Normalize Polymarket /prices-history rows as market odds, not underlying crypto prices."""

    history = payload.get("history") if isinstance(payload, dict) else []
    rows: list[dict[str, Any]] = []
    for item in history if isinstance(history, list) else []:
        if not isinstance(item, dict):
            continue
        timestamp = item.get("t")
        price = item.get("p")
        if timestamp is None or price is None:
            continue
        try:
            observed_at = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
            mid = float(price)
        except (TypeError, ValueError, OSError):
            continue
        rows.append(
            {
                "event_id": event_id,
                "market_id": market_id,
                "token_id": str(token_id),
                "outcome": outcome,
                "symbol": symbol,
                "observed_at": observed_at,
                "mid_price": mid,
                "best_bid": None,
                "best_ask": None,
                "bid_size": None,
                "ask_size": None,
                "depth_top3_bid_size": None,
                "depth_top3_ask_size": None,
                "book_bids": None,
                "book_asks": None,
                "spread": None,
                "source": source,
                "price_meaning": "polymarket_odds_not_underlying_price",
                "raw_json": item,
            }
        )
    return _sort_observations(pd.DataFrame(rows))


def normalize_clob_observations(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Normalize CLOB orderbook/trade/path observations for replay research."""

    rows: list[dict[str, Any]] = []
    for record in records:
        observed_at = _first_value(record, "observed_at", "captured_at", "timestamp", "price_at")
        token_id = str(_first_value(record, "token_id", "asset") or "")
        if not observed_at or not token_id:
            continue
        parsed_at = _parse_observed_time(observed_at)
        if parsed_at is None:
            continue
        bid = _to_float(_first_value(record, "best_bid", "bid"))
        ask = _to_float(_first_value(record, "best_ask", "ask"))
        mid = _to_float(_first_value(record, "mid_price", "price"))
        if mid is None and bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
        spread = _to_float(record.get("spread"))
        if spread is None and bid is not None and ask is not None:
            spread = max(0.0, ask - bid)
        rows.append(
            {
                "event_id": record.get("event_id"),
                "market_id": record.get("market_id"),
                "token_id": token_id,
                "outcome": record.get("outcome"),
                "symbol": str(record.get("symbol") or record.get("primary_symbol") or "").upper() or None,
                "observed_at": parsed_at,
                "mid_price": mid,
                "best_bid": bid,
                "best_ask": ask,
                "bid_size": _to_float(record.get("bid_size")),
                "ask_size": _to_float(record.get("ask_size")),
                "depth_top3_bid_size": _to_float(record.get("depth_top3_bid_size")),
                "depth_top3_ask_size": _to_float(record.get("depth_top3_ask_size")),
                "book_bids": record.get("book_bids"),
                "book_asks": record.get("book_asks"),
                "spread": spread,
                "source": record.get("source") or "polymarket_clob_observation",
                "price_meaning": record.get("price_meaning") or "polymarket_odds_not_underlying_price",
                "raw_json": record.get("raw_json") or record,
            }
        )
    return _sort_observations(pd.DataFrame(rows))


def _sort_observations(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "event_id",
        "market_id",
        "token_id",
        "outcome",
        "symbol",
        "observed_at",
        "mid_price",
        "best_bid",
        "best_ask",
        "bid_size",
        "ask_size",
        "depth_top3_bid_size",
        "depth_top3_ask_size",
        "book_bids",
        "book_asks",
        "spread",
        "source",
        "price_meaning",
        "raw_json",
    ]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    return frame.sort_values(["token_id", "observed_at"], kind="mergesort").reset_index(drop=True)


def _read_pmxt_parquet_filtered(path: str | Path, *, token_ids: set[str] | None) -> pd.DataFrame:
    path = Path(path)
    columns = [
        "timestamp_received",
        "timestamp",
        "market",
        "event_type",
        "asset_id",
        "bids",
        "asks",
        "price",
        "size",
        "side",
        "best_bid",
        "best_ask",
        "fee_rate_bps",
        "transaction_hash",
        "old_tick_size",
        "new_tick_size",
    ]
    try:
        import pyarrow.parquet as pq

        schema_names = set(pq.read_schema(path).names)
        available_columns = [column for column in columns if column in schema_names]
        filters = [("asset_id", "in", sorted(token_ids))] if token_ids and "asset_id" in schema_names else None
        return pq.read_table(path, columns=available_columns, filters=filters).to_pandas()
    except Exception:
        frame = pd.read_parquet(path)
        if token_ids and "asset_id" in frame.columns:
            frame = frame[frame["asset_id"].astype(str).isin(token_ids)]
        return frame


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _parse_observed_time(value: Any) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    unit = None
    numeric: float | None = None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = None
    if numeric is not None:
        unit = "ms" if numeric >= 10_000_000_000 else "s"
    try:
        parsed = pd.to_datetime(value, utc=True, unit=unit)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    return parsed


def _depth_levels(raw: Any, *, side: str | None = None) -> list[dict[str, float]]:
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    levels: list[dict[str, float]] = []
    if not isinstance(raw, list):
        return levels
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
        levels.sort(key=lambda item: item["price"], reverse=True)
    elif side == "ask":
        levels.sort(key=lambda item: item["price"])
    return levels


def _depth_size(levels: list[dict[str, float]], *, limit: int) -> float | None:
    if not levels:
        return None
    return float(sum(level["size"] for level in levels[:limit]))


def _mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


def _market_to_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("ascii")
        except UnicodeDecodeError:
            return value.hex()
    return str(value)


def _content_range_total(value: str | None) -> int | None:
    if not value or "/" not in value:
        return None
    try:
        return int(value.rsplit("/", 1)[1])
    except ValueError:
        return None


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


__all__ = [
    "fetch_clob_prices_history",
    "fetch_clob_prices_history_for_event_windows",
    "fetch_clob_prices_history_for_outcomes",
    "fetch_current_order_book",
    "fetch_current_order_books_for_outcomes",
    "download_pmxt_orderbook_hour",
    "download_pmxt_orderbooks_for_events",
    "filter_clob_observations_to_event_windows",
    "normalize_clob_observations",
    "normalize_clob_price_history",
    "normalize_order_book_snapshot",
    "normalize_pmxt_orderbook_records",
    "pmxt_event_hours",
    "pmxt_orderbook_hour_url",
    "probe_pmxt_orderbook_hour",
    "read_pmxt_orderbook_parquet",
]
