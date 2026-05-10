from __future__ import annotations

import json

try:
    from codex_tool._client import api_json, base_parser, exit_for_response
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response


def main() -> None:
    parser = base_parser("Start or refresh a Janus passive/live market watch session.")
    parser.add_argument("--event-key", required=True)
    parser.add_argument("--category", default="other", choices=["nba", "crypto_options", "geopolitics", "other"])
    parser.add_argument("--watch-session-id", default=None)
    parser.add_argument("--cadence-ms", type=int, default=None)
    parser.add_argument("--reason", default=None)
    parser.add_argument("--active-trading", action="store_true", help="Mark the session as not passive-only.")
    parser.add_argument("--metadata-json", default=None)
    args = parser.parse_args()
    payload = {
        "watch_session_id": args.watch_session_id,
        "event_key": args.event_key,
        "category": args.category,
        "passive_only": not bool(args.active_trading),
        "cadence_ms": args.cadence_ms,
        "reason": args.reason,
        "metadata": _loads_dict(args.metadata_json),
    }
    exit_for_response(api_json(args.api_root, "POST", "/v1/watchlists/sessions", payload))


def _loads_dict(value: str | None) -> dict:
    if not value:
        return {}
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise SystemExit("expected JSON object")
    return payload


if __name__ == "__main__":
    main()
