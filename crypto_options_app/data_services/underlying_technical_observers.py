from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from bs4 import BeautifulSoup

from crypto_options_app.config import CENTRAL_DB_PATH
from crypto_options_app.db.connection import connect
from crypto_options_app.db.postgres_connection import should_use_postgres_runtime
from crypto_options_app.db.schema import create_schema, initialize_schema
from crypto_options_app.workers.feed_worker import FeedWorkerConfig, write_watermark


LIVE_FLAG_NAMES = (
    "JANUS_CRYPTO_OPTIONS_LIVE_EXECUTE",
    "JANUS_CRYPTO_OPTIONS_LIVE_APPROVED",
    "JANUS_CRYPTO_OPTIONS_ACK_LIVE_RISK",
)

IFCM_AJAX_URL = "https://www.ifcm.co.uk/technicals/ajax"
IFCM_PAGE_URL_TEMPLATE = "https://www.ifcm.co.uk/technicals/crypto-technical-analysis/{slug}"
TRADERSUNION_URL_TEMPLATE = "https://tradersunion.com/currencies/forecast/{slug}/signals/"
IFCM_PERIOD_LABELS = {
    "1": "1m",
    "5": "5m",
    "15": "15m",
    "30": "30m",
    "60": "1h",
    "240": "4h",
    "1440": "1d",
    "10080": "1w",
}
DEFAULT_IFCM_SYMBOLS = {
    "BTC": {"slug": "btcusd", "instrument_id": "903", "group_id": "22"},
    "ETH": {"slug": "ethusd", "instrument_id": "904", "group_id": "22"},
}
DEFAULT_TRADERSUNION_SYMBOLS = {
    "BTC": {"slug": "btc-usd"},
    "ETH": {"slug": "eth-usd"},
}


@dataclass(frozen=True)
class TechnicalObserverConfig:
    db_path: Path = CENTRAL_DB_PATH
    symbols: tuple[str, ...] = ("BTC", "ETH")
    ifcm_periods: tuple[str, ...] = ("1", "5", "15", "30", "60", "240", "1440", "10080")
    include_ifcm: bool = True
    include_tradersunion: bool = True
    target_refresh_seconds: int = 30
    timeout_seconds: int = 20
    module_id: str = "underlying_technical_observers"


@dataclass(frozen=True)
class TechnicalObserverSnapshot:
    provider: str
    symbol: str
    interval: str
    source_url: str
    request_started_at_utc: str
    observed_at_utc: str
    completed_at_utc: str
    latency_ms: int
    summary: dict[str, Any]
    components: tuple[dict[str, Any], ...]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TechnicalObserverSummary:
    generated_at_utc: str
    status: str
    db_path: str
    snapshot_rows_inserted: int
    component_rows_inserted: int
    readiness_rows_inserted: int
    blockers: tuple[str, ...] = ()
    state: dict[str, Any] = field(default_factory=dict)
    orders_allowed: bool = False
    live_trading_authorized: bool = False


IfcmFetcher = Callable[[str, str, str, str, str, int], TechnicalObserverSnapshot]
TradersUnionFetcher = Callable[[str, str, int], TechnicalObserverSnapshot]


def capture_underlying_technical_observers_once(
    *,
    config: TechnicalObserverConfig,
    ifcm_fetcher: IfcmFetcher = None,  # type: ignore[assignment]
    tradersunion_fetcher: TradersUnionFetcher = None,  # type: ignore[assignment]
) -> TechnicalObserverSummary:
    _reject_live_env_flags()
    if not should_use_postgres_runtime(config.db_path):
        initialize_schema(config.db_path)
    ifcm_fetcher = ifcm_fetcher or fetch_ifcm_technical_snapshot
    tradersunion_fetcher = tradersunion_fetcher or fetch_tradersunion_technical_snapshot
    generated_at = datetime.now(UTC)
    snapshots: list[TechnicalObserverSnapshot] = []
    blockers: list[str] = []

    for symbol in tuple(dict.fromkeys(symbol.upper() for symbol in config.symbols)):
        if config.include_ifcm:
            symbol_cfg = DEFAULT_IFCM_SYMBOLS.get(symbol)
            if symbol_cfg is None:
                blockers.append(f"ifcm_symbol_unsupported:{symbol}")
            else:
                for period in config.ifcm_periods:
                    try:
                        snapshots.append(
                            ifcm_fetcher(
                                symbol,
                                period,
                                symbol_cfg["instrument_id"],
                                symbol_cfg["group_id"],
                                symbol_cfg["slug"],
                                config.timeout_seconds,
                            )
                        )
                    except Exception as exc:  # noqa: BLE001 - provider failures are persisted.
                        blockers.append(f"ifcm:{symbol}:{period}:{type(exc).__name__}:{exc}")
        if config.include_tradersunion:
            symbol_cfg = DEFAULT_TRADERSUNION_SYMBOLS.get(symbol)
            if symbol_cfg is None:
                blockers.append(f"tradersunion_symbol_unsupported:{symbol}")
            else:
                try:
                    snapshots.append(
                        tradersunion_fetcher(symbol, symbol_cfg["slug"], config.timeout_seconds)
                    )
                except Exception as exc:  # noqa: BLE001 - provider failures are persisted.
                    blockers.append(f"tradersunion:{symbol}:{type(exc).__name__}:{exc}")

    inserted_snapshots = 0
    inserted_components = 0
    readiness_rows = 0
    status = "failed" if blockers and not snapshots else "degraded" if blockers else "healthy"
    source_name = ",".join(sorted({snapshot.provider for snapshot in snapshots})) or "ifcm,tradersunion"
    with connect(config.db_path) as conn:
        if not getattr(conn, "is_postgres", False):
            create_schema(conn)
        for snapshot in snapshots:
            snapshot_inserted, components_inserted = insert_observer_snapshot(conn, snapshot)
            inserted_snapshots += snapshot_inserted
            inserted_components += components_inserted
        readiness_rows += write_block_a_readiness(
            conn,
            snapshots=snapshots,
            generated_at_utc=generated_at,
            target_refresh_seconds=config.target_refresh_seconds,
            blockers=blockers,
            source_name=source_name,
        )
        write_watermark(
            conn,
            service_name="crypto_options_app",
            module_id=config.module_id,
            status=status,
            last_run_at_utc=generated_at.isoformat(),
            rows_observed=len(snapshots),
            rows_inserted=inserted_snapshots + inserted_components + readiness_rows,
            error_count=len(blockers),
            source=source_name,
            state={
                "symbols": list(config.symbols),
                "target_refresh_seconds": config.target_refresh_seconds,
                "snapshots_observed": len(snapshots),
                "snapshot_rows_inserted": inserted_snapshots,
                "component_rows_inserted": inserted_components,
                "readiness_rows_inserted": readiness_rows,
                "blockers": blockers[:20],
                "orders_allowed": False,
                "live_trading_authorized": False,
            },
        )

    return TechnicalObserverSummary(
        generated_at_utc=generated_at.isoformat(),
        status=status,
        db_path=str(config.db_path),
        snapshot_rows_inserted=inserted_snapshots,
        component_rows_inserted=inserted_components,
        readiness_rows_inserted=readiness_rows,
        blockers=tuple(blockers),
        state={"snapshot_count": len(snapshots), "symbols": list(config.symbols)},
    )


def fetch_ifcm_technical_snapshot(
    symbol: str,
    period: str,
    instrument_id: str,
    group_id: str,
    slug: str,
    timeout_seconds: int,
) -> TechnicalObserverSnapshot:
    started = datetime.now(UTC)
    started_mono = time.monotonic()
    page_url = IFCM_PAGE_URL_TEMPLATE.format(slug=slug)
    body = urllib.parse.urlencode({"period": period, "instrumentId": instrument_id, "groupId": group_id}).encode()
    request = urllib.request.Request(
        IFCM_AJAX_URL,
        data=body,
        method="POST",
        headers=_browser_headers(referer=page_url, ajax=True),
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw_text = response.read().decode("utf-8", "replace")
    completed = datetime.now(UTC)
    payload = json.loads(raw_text)
    indicators = _ifcm_rows(payload.get("indicators") or [], "moving_average")
    oscillators = _ifcm_rows(payload.get("oscillators") or [], "oscillator")
    pivots = _pivot_components(payload.get("pivots") or {})
    components = tuple(indicators + oscillators + pivots)
    return TechnicalObserverSnapshot(
        provider="ifcm",
        symbol=symbol.upper(),
        interval=IFCM_PERIOD_LABELS.get(period, period),
        source_url=IFCM_AJAX_URL,
        request_started_at_utc=started.isoformat(),
        observed_at_utc=completed.isoformat(),
        completed_at_utc=completed.isoformat(),
        latency_ms=int((time.monotonic() - started_mono) * 1000),
        summary=_summarize_components(components),
        components=components,
        raw={"period": period, "instrument_id": instrument_id, "group_id": group_id},
    )


def fetch_tradersunion_technical_snapshot(symbol: str, slug: str, timeout_seconds: int) -> TechnicalObserverSnapshot:
    started = datetime.now(UTC)
    started_mono = time.monotonic()
    source_url = TRADERSUNION_URL_TEMPLATE.format(slug=slug)
    request = urllib.request.Request(source_url, headers=_browser_headers(referer=source_url))
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        html = response.read().decode("utf-8", "replace")
    completed = datetime.now(UTC)
    components = tuple(_parse_tradersunion_components(html))
    return TechnicalObserverSnapshot(
        provider="tradersunion",
        symbol=symbol.upper(),
        interval="current",
        source_url=source_url,
        request_started_at_utc=started.isoformat(),
        observed_at_utc=completed.isoformat(),
        completed_at_utc=completed.isoformat(),
        latency_ms=int((time.monotonic() - started_mono) * 1000),
        summary=_summarize_components(components),
        components=components,
        raw={"slug": slug, "bytes": len(html.encode("utf-8", "replace"))},
    )


def insert_observer_snapshot(conn: Any, snapshot: TechnicalObserverSnapshot) -> tuple[int, int]:
    key = _snapshot_key(snapshot)
    counts = snapshot.summary.get("counts") or {}
    conn.execute(
        """
        INSERT INTO external_technical_observer_snapshots(
            observer_snapshot_key, provider, symbol, interval, source_url,
            request_started_at_utc, observed_at_utc, completed_at_utc, latency_ms,
            summary_label, summary_score, buy_count, sell_count, neutral_count,
            error_count, component_count, components_json, source_json, inserted_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(observer_snapshot_key) DO NOTHING
        """,
        (
            key,
            snapshot.provider,
            snapshot.symbol,
            snapshot.interval,
            snapshot.source_url,
            snapshot.request_started_at_utc,
            snapshot.observed_at_utc,
            snapshot.completed_at_utc,
            snapshot.latency_ms,
            snapshot.summary.get("label"),
            snapshot.summary.get("score"),
            int(counts.get("BUY", 0)),
            int(counts.get("SELL", 0)),
            int(counts.get("NEUTRAL", 0)),
            int(counts.get("ERROR", 0)),
            len(snapshot.components),
            json.dumps(list(snapshot.components), sort_keys=True, default=str),
            json.dumps(snapshot.raw, sort_keys=True, default=str),
            datetime.now(UTC).isoformat(),
        ),
    )
    snapshot_inserted = conn.execute("SELECT changes() AS c").fetchone()["c"]
    component_rows = 0
    for idx, component in enumerate(snapshot.components):
        component_key = _stable_key(key, str(idx), component.get("group"), component.get("name"))
        conn.execute(
            """
            INSERT INTO external_technical_observer_components(
                observer_component_key, observer_snapshot_key, provider, symbol,
                interval, component_group, component_name, component_value, action,
                source_json, inserted_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(observer_component_key) DO NOTHING
            """,
            (
                component_key,
                key,
                snapshot.provider,
                snapshot.symbol,
                snapshot.interval,
                component.get("group") or "unknown",
                component.get("name") or "unknown",
                None if component.get("value") is None else str(component.get("value")),
                component.get("action"),
                json.dumps(component.get("raw") or {}, sort_keys=True, default=str),
                datetime.now(UTC).isoformat(),
            ),
        )
        component_rows += conn.execute("SELECT changes() AS c").fetchone()["c"]
    return int(snapshot_inserted), int(component_rows)


def write_block_a_readiness(
    conn: Any,
    *,
    snapshots: Iterable[TechnicalObserverSnapshot],
    generated_at_utc: datetime,
    target_refresh_seconds: int,
    blockers: Iterable[str] = (),
    source_name: str = "ifcm,tradersunion",
) -> int:
    rows = 0
    blockers_list = list(blockers)
    by_symbol: dict[str, list[TechnicalObserverSnapshot]] = {}
    for snapshot in snapshots:
        by_symbol.setdefault(snapshot.symbol, []).append(snapshot)
    for symbol, symbol_snapshots in sorted(by_symbol.items()):
        latest = max(symbol_snapshots, key=lambda snapshot: snapshot.completed_at_utc)
        age = max(0.0, (generated_at_utc - _parse_datetime(latest.completed_at_utc)).total_seconds())
        required_intervals = {"1m", "5m", "15m"}
        available_intervals = {
            snapshot.interval
            for snapshot in symbol_snapshots
            if snapshot.provider == "ifcm" and snapshot.summary.get("label")
        }
        readiness_blockers = list(blockers_list)
        missing = sorted(required_intervals - available_intervals)
        readiness_blockers.extend(f"missing_ifcm_interval:{interval}" for interval in missing)
        if age > target_refresh_seconds:
            readiness_blockers.append(f"stale_external_technical_observer:{age:.1f}s")
        status = "ready" if not readiness_blockers else "degraded"
        payload = {
            "source": source_name,
            "available_intervals": sorted(available_intervals),
            "required_30s_intervals": sorted(required_intervals),
            "latest_provider": latest.provider,
            "latest_summary": latest.summary,
            "snapshot_count": len(symbol_snapshots),
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
        readiness_key = _stable_key("block_a", symbol, generated_at_utc.isoformat())
        conn.execute(
            """
            INSERT INTO data_signal_readiness_snapshots(
                readiness_key, data_block, module_id, symbol, generated_at_utc,
                target_refresh_seconds, status, latest_source_at_utc,
                source_age_seconds, payload_json, blockers_json, inserted_at_utc
            )
            VALUES (?, 'A', 'underlying_technical_observers', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(readiness_key) DO NOTHING
            """,
            (
                readiness_key,
                symbol,
                generated_at_utc.isoformat(),
                target_refresh_seconds,
                status,
                latest.completed_at_utc,
                age,
                json.dumps(payload, sort_keys=True, default=str),
                json.dumps(readiness_blockers, sort_keys=True),
                datetime.now(UTC).isoformat(),
            ),
        )
        rows += conn.execute("SELECT changes() AS c").fetchone()["c"]
    return rows


def feed_worker_config(config: TechnicalObserverConfig) -> FeedWorkerConfig:
    return FeedWorkerConfig(
        service_name="crypto_options_app",
        module_id=config.module_id,
        data_type="external_technical_observer_snapshots",
        provider="ifcm,tradersunion",
        transport="rest",
        max_concurrency=1,
        stale_after_seconds=config.target_refresh_seconds,
        read_only=True,
        metadata={
            "writes": [
                "external_technical_observer_snapshots",
                "external_technical_observer_components",
                "data_signal_readiness_snapshots",
            ],
            "orders_allowed": False,
        },
    )


def _ifcm_rows(rows: list[dict[str, Any]], group: str) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for row in rows:
        data = row.get("data")
        if not isinstance(data, dict) or data.get("error"):
            components.append(
                {
                    "group": group,
                    "name": str(row.get("name") or "unknown"),
                    "value": None,
                    "action": "ERROR",
                    "raw": data,
                }
            )
            continue
        components.append(
            {
                "group": group,
                "name": str(row.get("name") or "unknown"),
                "value": data.get("value"),
                "action": _normalize_action(data.get("signal")),
                "raw": data,
            }
        )
    return components


def _pivot_components(pivots: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "group": "pivot",
            "name": str(name),
            "value": None,
            "action": "NEUTRAL",
            "raw": levels,
        }
        for name, levels in pivots.items()
    ]


def _parse_tradersunion_components(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    components: list[dict[str, Any]] = []
    if tables:
        for row in _table_rows(tables[0]):
            if len(row) >= 5 and row[0].startswith("MA"):
                components.append(
                    {
                        "group": "moving_average_simple",
                        "name": row[0],
                        "value": _strip_action_suffix(row[1]),
                        "action": _normalize_action(row[2]),
                        "raw": {"row": row},
                    }
                )
                components.append(
                    {
                        "group": "moving_average_exponential",
                        "name": row[0],
                        "value": _strip_action_suffix(row[3]),
                        "action": _normalize_action(row[4]),
                        "raw": {"row": row},
                    }
                )
    if len(tables) > 1:
        for row in _table_rows(tables[1]):
            if len(row) == 3 and row[0] != "Name":
                components.append(
                    {
                        "group": "oscillator",
                        "name": row[0],
                        "value": row[1],
                        "action": _normalize_action(row[2]),
                        "raw": {"row": row},
                    }
                )
    if len(tables) > 2:
        for row in _table_rows(tables[2]):
            if len(row) >= 2 and row[0]:
                components.append(
                    {
                        "group": "pivot",
                        "name": row[0],
                        "value": None,
                        "action": "NEUTRAL",
                        "raw": {"row": row},
                    }
                )
    return components


def _table_rows(table: Any) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        row = [" ".join(cell.get_text(" ", strip=True).split()) for cell in tr.find_all(["td", "th"])]
        if row and any(row):
            rows.append(row)
    return rows


def _summarize_components(components: Iterable[dict[str, Any]]) -> dict[str, Any]:
    counts = {"BUY": 0, "SELL": 0, "NEUTRAL": 0, "ERROR": 0}
    for component in components:
        action = _normalize_action(component.get("action"))
        if action not in counts:
            action = "NEUTRAL"
        counts[action] += 1
    score = counts["BUY"] - counts["SELL"]
    if score >= 4:
        label = "Strong Buy"
    elif score > 0:
        label = "Buy"
    elif score <= -4:
        label = "Strong Sell"
    elif score < 0:
        label = "Sell"
    else:
        label = "Neutral"
    return {"label": label, "score": score, "counts": counts}


def _browser_headers(*, referer: str, ajax: bool = False) -> dict[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
    }
    if ajax:
        headers.update(
            {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://www.ifcm.co.uk",
                "X-Requested-With": "XMLHttpRequest",
            }
        )
    else:
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    return headers


def _normalize_action(action: Any) -> str:
    value = str(action or "").strip().upper()
    if value in {"BUY", "STRONG BUY", "OVERSOLD"}:
        return "BUY"
    if value in {"SELL", "STRONG SELL", "OVERBOUGHT"}:
        return "SELL"
    if value in {"NEUTRAL", "HIGH VOLATILITY", ""}:
        return "NEUTRAL"
    if value == "ERROR":
        return "ERROR"
    return value if value in {"BUY", "SELL", "NEUTRAL", "ERROR"} else "NEUTRAL"


def _strip_action_suffix(value: str) -> str:
    return re.sub(r"\s+(Strong Sell|Strong Buy|Sell|Buy|Neutral|Oversold|Overbought)$", "", str(value)).strip()


def _parse_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _snapshot_key(snapshot: TechnicalObserverSnapshot) -> str:
    return _stable_key(
        "observer_snapshot",
        snapshot.provider,
        snapshot.symbol,
        snapshot.interval,
        snapshot.completed_at_utc,
        snapshot.summary,
        snapshot.components,
    )


def _stable_key(*parts: Any) -> str:
    payload = "|".join(json.dumps(part, sort_keys=True, default=str) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def _reject_live_env_flags() -> None:
    enabled = [name for name in LIVE_FLAG_NAMES if str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}]
    if enabled:
        raise RuntimeError(f"data_service_live_flags_rejected:{','.join(enabled)}")
