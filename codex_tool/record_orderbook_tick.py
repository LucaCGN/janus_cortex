from __future__ import annotations

import json
from pathlib import Path

try:
    from codex_tools.janus.watchlists import (
        WATCHLIST_ORDERBOOK_TICKS_PATH,
        build_orderbook_tick_payload,
        main_for_orderbook_tick_record,
        mid_price as _mid,
        record_orderbook_ticks,
        spread as _spread,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response

    WATCHLIST_ORDERBOOK_TICKS_PATH = "/v1/watchlists/orderbook-ticks"


    def build_orderbook_tick_payload(args) -> dict:
        ticks = _load_ticks(args.ticks_json, args.ticks_path)
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
                    "spread": _spread(args.best_bid, args.best_ask),
                    "mid_price": _mid(args.best_bid, args.best_ask),
                    "bid_depth": args.bid_depth,
                    "ask_depth": args.ask_depth,
                    "source_latency_ms": args.source_latency_ms,
                    "ingest_latency_ms": args.ingest_latency_ms,
                }
            ]
        return {"source": args.source, "ticks": ticks}

    def record_orderbook_ticks(api_root: str, payload: dict) -> dict:
        return api_json(api_root, "POST", WATCHLIST_ORDERBOOK_TICKS_PATH, payload)

    def main_for_orderbook_tick_record(description: str) -> None:
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
        args = parser.parse_args()
        exit_for_response(record_orderbook_ticks(args.api_root, build_orderbook_tick_payload(args)))


def main() -> None:
    main_for_orderbook_tick_record("Record one or more observed market orderbook ticks into Janus.")


def _load_ticks(inline: str | None, path: str | None) -> list[dict]:
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
            raise SystemExit("tick list must contain objects")
        return parsed
    raise SystemExit("expected JSON object or array")


def _mid(best_bid: float | None, best_ask: float | None) -> float | None:
    if best_bid is None or best_ask is None:
        return None
    return round((best_bid + best_ask) / 2.0, 6)


def _spread(best_bid: float | None, best_ask: float | None) -> float | None:
    if best_bid is None or best_ask is None:
        return None
    return round(max(0.0, best_ask - best_bid), 6)


if __name__ == "__main__":
    main()
