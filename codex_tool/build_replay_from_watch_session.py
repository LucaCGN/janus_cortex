from __future__ import annotations

try:
    from codex_tools.janus.events import (
        REPLAY_FROM_WATCH_SESSION_PATH,
        build_replay_from_watch_session_payload,
        main_for_replay_from_watch_session,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response

    REPLAY_FROM_WATCH_SESSION_PATH = "/v1/replay/from-watch-session"

    def build_replay_from_watch_session_payload(args):
        return {
            "watch_session_id": args.watch_session_id,
            "event_key": args.event_key,
            "output_name": args.output_name,
            "notes": args.notes,
        }

    def main_for_replay_from_watch_session(description: str) -> None:
        parser = base_parser(description)
        parser.add_argument("--watch-session-id", required=True)
        parser.add_argument("--event-key", default=None)
        parser.add_argument("--output-name", default=None)
        parser.add_argument("--notes", default=None)
        args = parser.parse_args()
        exit_for_response(
            api_json(args.api_root, "POST", REPLAY_FROM_WATCH_SESSION_PATH, build_replay_from_watch_session_payload(args))
        )


def main() -> None:
    main_for_replay_from_watch_session("Request replay construction from a captured market watch session.")


if __name__ == "__main__":
    main()
