"""Janus watchlist capture helpers for the target Codex tools namespace."""

from __future__ import annotations

import json
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Any

from codex_tools.janus.client import api_json, base_parser, exit_for_response

WATCHLIST_SESSIONS_PATH = "/v1/watchlists/sessions"
WATCHLIST_ORDERBOOK_TICKS_PATH = "/v1/watchlists/orderbook-ticks"
WATCHLIST_TRADES_PATH = "/v1/watchlists/trades"
WATCHLIST_SESSION_CATEGORIES = ("nba", "crypto_options", "geopolitics", "other")


def load_json_records(inline: str | None, path: str | None, *, label: str) -> list[dict[str, Any]]:
    """Load one JSON object or a list of JSON objects from CLI inputs."""
    value = inline
    if path:
        value = Path(path).read_text(encoding="utf-8")
    if not value:
        return []
    parsed = json.loads(value)
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        if not all(isinstance(item, dict) for item in parsed):
            raise SystemExit(f"{label} list must contain objects")
        return parsed
    raise SystemExit("expected JSON object or array")


def loads_dict(value: str | None) -> dict[str, Any]:
    """Load a JSON object from an optional CLI string."""
    if not value:
        return {}
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise SystemExit("expected JSON object")
    return payload


def mid_price(best_bid: float | None, best_ask: float | None) -> float | None:
    """Return the midpoint for a bid/ask pair when both sides exist."""
    if best_bid is None or best_ask is None:
        return None
    return round((best_bid + best_ask) / 2.0, 6)


def spread(best_bid: float | None, best_ask: float | None) -> float | None:
    """Return the non-negative spread for a bid/ask pair when both sides exist."""
    if best_bid is None or best_ask is None:
        return None
    return round(max(0.0, best_ask - best_bid), 6)


def build_watch_session_payload(args: Namespace) -> dict[str, Any]:
    """Build the Janus watch-session payload used by the legacy CLI."""
    return {
        "watch_session_id": args.watch_session_id,
        "event_key": args.event_key,
        "category": args.category,
        "passive_only": not bool(args.active_trading),
        "cadence_ms": args.cadence_ms,
        "reason": args.reason,
        "metadata": loads_dict(args.metadata_json),
    }


def start_watch_session(api_root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Start or refresh a Janus passive/live market watch session."""
    return api_json(api_root, "POST", WATCHLIST_SESSIONS_PATH, payload)


def build_orderbook_tick_payload(args: Namespace) -> dict[str, Any]:
    """Build the Janus orderbook-tick capture payload used by the legacy CLI."""
    ticks = load_json_records(args.ticks_json, args.ticks_path, label="tick")
    if not ticks:
        if not args.event_key:
            raise SystemExit("--event-key is required when not using --ticks-json/--ticks-path")
        ticks = [
            {
                "event_key": args.event_key,
                "market_id": args.market_id,
                "outcome_id": args.outcome_id,
                "token_id": args.token_id,
                "best_bid": args.best_bid,
                "best_ask": args.best_ask,
                "spread": spread(args.best_bid, args.best_ask),
                "mid_price": mid_price(args.best_bid, args.best_ask),
                "bid_depth": args.bid_depth,
                "ask_depth": args.ask_depth,
                "source_latency_ms": args.source_latency_ms,
                "ingest_latency_ms": args.ingest_latency_ms,
            }
        ]
    return {"source": args.source, "ticks": ticks}


def record_orderbook_ticks(api_root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Record observed market orderbook ticks through Janus."""
    return api_json(api_root, "POST", WATCHLIST_ORDERBOOK_TICKS_PATH, payload)


def build_market_trade_payload(args: Namespace) -> dict[str, Any]:
    """Build the Janus market-trade capture payload used by the legacy CLI."""
    trades = load_json_records(args.trades_json, args.trades_path, label="trade")
    if not trades:
        if not args.event_key:
            raise SystemExit("--event-key is required when not using --trades-json/--trades-path")
        trades = [
            {
                "event_key": args.event_key,
                "market_id": args.market_id,
                "outcome_id": args.outcome_id,
                "token_id": args.token_id,
                "external_trade_id": args.external_trade_id,
                "side": args.side,
                "price": args.price,
                "size": args.size,
                "source_latency_ms": args.source_latency_ms,
            }
        ]
    return {"source": args.source, "trades": trades}


def record_market_trades(api_root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Record observed market trades through Janus."""
    return api_json(api_root, "POST", WATCHLIST_TRADES_PATH, payload)


def build_watch_session_parser(description: str) -> ArgumentParser:
    parser = base_parser(description)
    parser.add_argument("--event-key", required=True)
    parser.add_argument("--category", default="other", choices=WATCHLIST_SESSION_CATEGORIES)
    parser.add_argument("--watch-session-id", default=None)
    parser.add_argument("--cadence-ms", type=int, default=None)
    parser.add_argument("--reason", default=None)
    parser.add_argument("--active-trading", action="store_true", help="Mark the session as not passive-only.")
    parser.add_argument("--metadata-json", default=None)
    return parser


def build_orderbook_tick_parser(description: str) -> ArgumentParser:
    parser = base_parser(description)
    parser.add_argument("--event-key", default=None)
    parser.add_argument("--market-id", default=None)
    parser.add_argument("--outcome-id", default=None)
    parser.add_argument("--token-id", default=None)
    parser.add_argument("--best-bid", type=float, default=None)
    parser.add_argument("--best-ask", type=float, default=None)
    parser.add_argument("--bid-depth", type=float, default=None)
    parser.add_argument("--ask-depth", type=float, default=None)
    parser.add_argument("--source-latency-ms", type=float, default=None)
    parser.add_argument("--ingest-latency-ms", type=float, default=None)
    parser.add_argument("--ticks-json", default=None, help="Inline JSON array/object of ticks.")
    parser.add_argument("--ticks-path", default=None, help="Path to JSON array/object of ticks.")
    parser.add_argument("--source", default="codex")
    return parser


def build_market_trade_parser(description: str) -> ArgumentParser:
    parser = base_parser(description)
    parser.add_argument("--event-key", default=None)
    parser.add_argument("--market-id", default=None)
    parser.add_argument("--outcome-id", default=None)
    parser.add_argument("--token-id", default=None)
    parser.add_argument("--external-trade-id", default=None)
    parser.add_argument("--side", default=None)
    parser.add_argument("--price", type=float, default=None)
    parser.add_argument("--size", type=float, default=None)
    parser.add_argument("--source-latency-ms", type=float, default=None)
    parser.add_argument("--trades-json", default=None, help="Inline JSON array/object of trades.")
    parser.add_argument("--trades-path", default=None, help="Path to JSON array/object of trades.")
    parser.add_argument("--source", default="codex")
    return parser


def main_for_watch_session(description: str) -> None:
    args = build_watch_session_parser(description).parse_args()
    exit_for_response(start_watch_session(args.api_root, build_watch_session_payload(args)))


def main_for_orderbook_tick_record(description: str) -> None:
    args = build_orderbook_tick_parser(description).parse_args()
    exit_for_response(record_orderbook_ticks(args.api_root, build_orderbook_tick_payload(args)))


def main_for_market_trade_record(description: str) -> None:
    args = build_market_trade_parser(description).parse_args()
    exit_for_response(record_market_trades(args.api_root, build_market_trade_payload(args)))
