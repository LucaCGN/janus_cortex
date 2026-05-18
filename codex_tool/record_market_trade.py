from __future__ import annotations

import json
from pathlib import Path

try:
    from codex_tools.janus.watchlists import (
        WATCHLIST_TRADES_PATH,
        build_market_trade_payload,
        main_for_market_trade_record,
        record_market_trades,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response

    WATCHLIST_TRADES_PATH = "/v1/watchlists/trades"


    def build_market_trade_payload(args) -> dict:
        trades = _load_trades(args.trades_json, args.trades_path)
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

    def record_market_trades(api_root: str, payload: dict) -> dict:
        return api_json(api_root, "POST", WATCHLIST_TRADES_PATH, payload)

    def main_for_market_trade_record(description: str) -> None:
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
        args = parser.parse_args()
        exit_for_response(record_market_trades(args.api_root, build_market_trade_payload(args)))


def main() -> None:
    main_for_market_trade_record("Record one or more observed market trades into Janus.")


def _load_trades(inline: str | None, path: str | None) -> list[dict]:
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
            raise SystemExit("trade list must contain objects")
        return parsed
    raise SystemExit("expected JSON object or array")


if __name__ == "__main__":
    main()
