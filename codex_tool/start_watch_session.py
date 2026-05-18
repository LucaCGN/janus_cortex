from __future__ import annotations

import json

try:
    from codex_tools.janus.watchlists import (
        WATCHLIST_SESSION_CATEGORIES,
        WATCHLIST_SESSIONS_PATH,
        build_watch_session_payload,
        main_for_watch_session,
        start_watch_session,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response

    WATCHLIST_SESSIONS_PATH = "/v1/watchlists/sessions"
    WATCHLIST_SESSION_CATEGORIES = ("nba", "crypto_options", "geopolitics", "other")

    def _loads_dict(value: str | None) -> dict:
        if not value:
            return {}
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise SystemExit("expected JSON object")
        return payload

    def build_watch_session_payload(args) -> dict:
        return {
            "watch_session_id": args.watch_session_id,
            "event_key": args.event_key,
            "category": args.category,
            "passive_only": not bool(args.active_trading),
            "cadence_ms": args.cadence_ms,
            "reason": args.reason,
            "metadata": _loads_dict(args.metadata_json),
        }

    def start_watch_session(api_root: str, payload: dict) -> dict:
        return api_json(api_root, "POST", WATCHLIST_SESSIONS_PATH, payload)

    def main_for_watch_session(description: str) -> None:
        parser = base_parser(description)
        parser.add_argument("--event-key", required=True)
        parser.add_argument("--category", default="other", choices=WATCHLIST_SESSION_CATEGORIES)
        parser.add_argument("--watch-session-id", default=None)
        parser.add_argument("--cadence-ms", type=int, default=None)
        parser.add_argument("--reason", default=None)
        parser.add_argument("--active-trading", action="store_true", help="Mark the session as not passive-only.")
        parser.add_argument("--metadata-json", default=None)
        args = parser.parse_args()
        exit_for_response(start_watch_session(args.api_root, build_watch_session_payload(args)))


def main() -> None:
    main_for_watch_session("Start or refresh a Janus passive/live market watch session.")


if __name__ == "__main__":
    main()
