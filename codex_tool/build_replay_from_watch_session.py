from __future__ import annotations

try:
    from codex_tool._client import api_json, base_parser, exit_for_response
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response


def main() -> None:
    parser = base_parser("Request replay construction from a captured market watch session.")
    parser.add_argument("--watch-session-id", required=True)
    parser.add_argument("--event-key", default=None)
    parser.add_argument("--output-name", default=None)
    parser.add_argument("--notes", default=None)
    args = parser.parse_args()
    payload = {
        "watch_session_id": args.watch_session_id,
        "event_key": args.event_key,
        "output_name": args.output_name,
        "notes": args.notes,
    }
    exit_for_response(api_json(args.api_root, "POST", "/v1/replay/from-watch-session", payload))


if __name__ == "__main__":
    main()
