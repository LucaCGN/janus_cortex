from __future__ import annotations

try:
    from codex_tool._client import api_json, base_parser, exit_for_response
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response


def main() -> None:
    parser = base_parser("Add one market event to the Janus passive/live watchlist.")
    parser.add_argument("--event-key", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--category", default="other", choices=["nba", "crypto_options", "geopolitics", "other"])
    parser.add_argument("--source-url", action="append", dest="source_urls", default=[])
    parser.add_argument("--market-id", default=None)
    parser.add_argument("--notes", default=None)
    parser.add_argument("--active", action="store_true", help="Set passive_only=false.")
    parser.add_argument("--source", default="codex")
    args = parser.parse_args()
    payload = {
        "source": args.source,
        "events": [
            {
                "event_key": args.event_key,
                "category": args.category,
                "title": args.title,
                "source_urls": args.source_urls,
                "market_id": args.market_id,
                "notes": args.notes,
                "passive_only": not bool(args.active),
            }
        ],
    }
    exit_for_response(api_json(args.api_root, "POST", "/v1/watchlists/events", payload))


if __name__ == "__main__":
    main()
