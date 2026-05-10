from __future__ import annotations

import json
from pathlib import Path

try:
    from codex_tool._client import api_json, base_parser, exit_for_response
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response


def main() -> None:
    parser = base_parser("Record one or more observed market trades into Janus.")
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
    exit_for_response(api_json(args.api_root, "POST", "/v1/watchlists/trades", {"source": args.source, "trades": trades}))


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
