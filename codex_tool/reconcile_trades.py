from __future__ import annotations

try:
    from codex_tool._client import api_json, base_parser, exit_for_response
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response


def main() -> None:
    parser = base_parser("Build a non-destructive duplicate-fill reconciliation report.")
    parser.add_argument("--account-id", default=None)
    parser.add_argument("--market-id", default=None)
    parser.add_argument("--outcome-id", default=None)
    parser.add_argument("--event-slug", default=None)
    parser.add_argument("--start-time", default=None)
    parser.add_argument("--end-time", default=None)
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args()
    query = {
        "account_id": args.account_id,
        "market_id": args.market_id,
        "outcome_id": args.outcome_id,
        "event_slug": args.event_slug,
        "start_time": args.start_time,
        "end_time": args.end_time,
        "limit": args.limit,
    }
    exit_for_response(api_json(args.api_root, "GET", "/v1/portfolio/trades/reconciliation", query=query))


if __name__ == "__main__":
    main()
