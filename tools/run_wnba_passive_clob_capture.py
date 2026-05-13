from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


POLYMARKET_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB_BASE_URL = "https://clob.polymarket.com"
DEFAULT_SLUGS = [
    "wnba-sea-tor-2026-05-13",
    "wnba-las-conn-2026-05-13",
    "wnba-chi-gsv-2026-05-13",
    "wnba-ind-la-2026-05-13",
]


@dataclass(frozen=True)
class OutcomeTarget:
    event_key: str
    event_slug: str
    event_title: str
    market_id: str
    outcome: str
    token_id: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _utc_now()).isoformat().replace("+00:00", "Z")


def _json_get(url: str, *, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "janus-wnba-passive-capture/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _api_json(api_root: str, method: str, path: str, payload: dict[str, Any] | None = None, *, timeout: float = 60.0) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{api_root.rstrip('/')}{path}",
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "janus-wnba-passive-capture/1.0"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _loads_json_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise SystemExit("expected a JSON list")
    return payload


def _loads_market_array(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(payload, list):
            return [str(item) for item in payload]
    return []


def _is_moneyline_market(market: dict[str, Any]) -> bool:
    market_type = str(market.get("sportsMarketType") or market.get("sports_market_type") or "").lower()
    question = str(market.get("question") or "").lower()
    if market_type == "moneyline":
        return True
    if market_type and market_type != "moneyline":
        return False
    return " vs. " in question or " vs " in question


def _resolve_slug(slug: str) -> list[OutcomeTarget]:
    params = urllib.parse.urlencode({"slug": slug})
    events = _json_get(f"{POLYMARKET_GAMMA_BASE_URL}/events?{params}")
    if not events:
        raise RuntimeError(f"no Polymarket Gamma event found for slug={slug}")
    event = events[0]
    markets = [market for market in event.get("markets") or [] if isinstance(market, dict) and _is_moneyline_market(market)]
    if not markets:
        raise RuntimeError(f"no moneyline market found for slug={slug}")
    market = markets[0]
    outcomes = _loads_market_array(market.get("outcomes"))
    token_ids = _loads_market_array(market.get("clobTokenIds"))
    if len(outcomes) != len(token_ids) or not token_ids:
        raise RuntimeError(f"moneyline market token/outcome mismatch for slug={slug}")
    event_key = slug
    title = str(event.get("title") or market.get("question") or slug)
    market_id = str(market.get("id") or market.get("conditionId") or "")
    if not market_id:
        raise RuntimeError(f"missing market_id for slug={slug}")
    return [
        OutcomeTarget(
            event_key=event_key,
            event_slug=slug,
            event_title=title,
            market_id=market_id,
            outcome=outcome,
            token_id=token_id,
        )
        for outcome, token_id in zip(outcomes, token_ids, strict=True)
    ]


def _parse_level(level: Any) -> tuple[float, float] | None:
    if isinstance(level, dict):
        price = level.get("price")
        size = level.get("size")
    elif isinstance(level, (list, tuple)) and len(level) >= 2:
        price, size = level[0], level[1]
    else:
        return None
    try:
        return float(price), float(size)
    except (TypeError, ValueError):
        return None


def _normalize_levels(raw_levels: Any, *, reverse: bool) -> list[dict[str, float]]:
    parsed = [_parse_level(item) for item in (raw_levels or [])]
    levels = [{"price": float(price), "size": float(size)} for item in parsed if item is not None for price, size in [item]]
    return sorted(levels, key=lambda row: row["price"], reverse=reverse)


def _book_to_tick(target: OutcomeTarget, book: dict[str, Any], *, elapsed_ms: float) -> dict[str, Any]:
    bids = _normalize_levels(book.get("bids"), reverse=True)
    asks = _normalize_levels(book.get("asks"), reverse=False)
    best_bid = bids[0]["price"] if bids else None
    best_ask = asks[0]["price"] if asks else None
    spread = (best_ask - best_bid) if best_bid is not None and best_ask is not None else None
    mid = ((best_bid + best_ask) / 2.0) if best_bid is not None and best_ask is not None else None
    return {
        "event_key": target.event_key,
        "market_id": target.market_id,
        "outcome_id": target.outcome,
        "token_id": target.token_id,
        "captured_at_utc": _iso(),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "mid_price": mid,
        "bid_depth": sum(level["size"] for level in bids),
        "ask_depth": sum(level["size"] for level in asks),
        "source_latency_ms": elapsed_ms,
        "ingest_latency_ms": elapsed_ms,
        "levels": {
            "bids": bids,
            "asks": asks,
            "outcome": target.outcome,
            "event_slug": target.event_slug,
        },
        "raw": {
            "asset_id": book.get("asset_id"),
            "hash": book.get("hash"),
            "min_order_size": book.get("min_order_size"),
            "tick_size": book.get("tick_size"),
            "neg_risk": book.get("neg_risk"),
        },
    }


def _fetch_book(target: OutcomeTarget) -> tuple[dict[str, Any] | None, float, str | None]:
    started = time.perf_counter()
    try:
        book = _json_get(f"{POLYMARKET_CLOB_BASE_URL}/book?{urllib.parse.urlencode({'token_id': target.token_id})}", timeout=20.0)
        return book, (time.perf_counter() - started) * 1000.0, None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return None, (time.perf_counter() - started) * 1000.0, str(exc)


def _register_watch_targets(api_root: str, targets: list[OutcomeTarget], *, source: str, cadence_ms: int) -> list[dict[str, Any]]:
    by_event: dict[str, list[OutcomeTarget]] = {}
    for target in targets:
        by_event.setdefault(target.event_key, []).append(target)
    records: list[dict[str, Any]] = []
    for event_key, event_targets in by_event.items():
        first = event_targets[0]
        watch_event = _api_json(
            api_root,
            "POST",
            "/v1/watchlists/events",
            {
                "source": source,
                "events": [
                    {
                        "event_key": event_key,
                        "category": "other",
                        "title": first.event_title,
                        "source_urls": [f"https://polymarket.com/event/{first.event_slug}"],
                        "market_id": first.market_id,
                        "notes": "WNBA passive-only CLOB capture. Orders are disabled.",
                        "passive_only": True,
                    }
                ],
            },
        )
        watch_session_id = f"wnba-passive-{event_key}"
        watch_session = _api_json(
            api_root,
            "POST",
            "/v1/watchlists/sessions",
            {
                "watch_session_id": watch_session_id,
                "event_key": event_key,
                "category": "other",
                "passive_only": True,
                "cadence_ms": cadence_ms,
                "reason": "WNBA passive CLOB orderbook/depth capture for future replay and shadow testing.",
                "metadata": {
                    "league": "wnba",
                    "orders_allowed": False,
                    "source": source,
                    "targets": [target.__dict__ for target in event_targets],
                },
            },
        )
        records.append({"event_key": event_key, "watch_event": watch_event, "watch_session": watch_session})
    return records


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run passive WNBA Polymarket CLOB orderbook capture.")
    parser.add_argument("--api-root", default="http://127.0.0.1:8010")
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--slug", action="append", default=[], help="Polymarket WNBA event slug. Can be repeated.")
    parser.add_argument("--targets-json", default=None, help="Optional JSON list of explicit target objects.")
    parser.add_argument("--interval-sec", type=float, default=2.0)
    parser.add_argument("--seconds", type=float, default=0.0, help="Run duration. 0 means run until interrupted.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--source", default="janus-wnba-passive-clob-capture")
    parser.add_argument("--status-path", type=Path, default=None)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    slugs = args.slug or DEFAULT_SLUGS
    targets: list[OutcomeTarget] = []
    for item in _loads_json_list(args.targets_json):
        targets.append(OutcomeTarget(**item))
    for slug in slugs:
        targets.extend(_resolve_slug(slug))
    if not targets:
        raise SystemExit("no WNBA passive capture targets resolved")

    session_date = args.session_date or _utc_now().date().isoformat()
    status_path = args.status_path or Path("local") / "shared" / "artifacts" / "wnba-live-capture" / session_date / "status.json"
    cadence_ms = int(max(args.interval_sec, 0.1) * 1000)
    registration = _register_watch_targets(args.api_root, targets, source=args.source, cadence_ms=cadence_ms)
    deadline = None if args.seconds <= 0 else time.monotonic() + args.seconds
    tick_count = 0
    error_count = 0
    started_at = _iso()

    _write_status(
        status_path,
        {
            "status": "running",
            "orders_allowed": False,
            "started_at_utc": started_at,
            "session_date": session_date,
            "target_count": len(targets),
            "event_keys": sorted({target.event_key for target in targets}),
            "registration": registration,
        },
    )

    while True:
        batch_started = _utc_now()
        ticks: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for target in targets:
            book, elapsed_ms, error = _fetch_book(target)
            if error:
                error_count += 1
                errors.append({"event_key": target.event_key, "outcome": target.outcome, "token_id": target.token_id, "error": error})
                continue
            if book is not None:
                ticks.append(_book_to_tick(target, book, elapsed_ms=elapsed_ms))
        persistence: dict[str, Any] | None = None
        if ticks:
            persistence = _api_json(
                args.api_root,
                "POST",
                "/v1/watchlists/orderbook-ticks",
                {"source": args.source, "ticks": ticks},
                timeout=60.0,
            )
        tick_count += len(ticks)
        _write_status(
            status_path,
            {
                "status": "running",
                "orders_allowed": False,
                "started_at_utc": started_at,
                "updated_at_utc": _iso(),
                "session_date": session_date,
                "event_keys": sorted({target.event_key for target in targets}),
                "target_count": len(targets),
                "total_tick_rows": tick_count,
                "total_errors": error_count,
                "last_batch": {
                    "started_at_utc": _iso(batch_started),
                    "tick_count": len(ticks),
                    "errors": errors,
                    "persistence": persistence,
                },
            },
        )
        if args.once:
            break
        if deadline is not None and time.monotonic() >= deadline:
            break
        elapsed = (_utc_now() - batch_started).total_seconds()
        time.sleep(max(0.0, args.interval_sec - elapsed))

    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["status"] = "completed"
    status["completed_at_utc"] = _iso()
    _write_status(status_path, status)
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
