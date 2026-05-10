from __future__ import annotations

try:
    from codex_tool._client import api_json, base_parser, exit_for_response
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response


def main() -> None:
    parser = base_parser("Export event context for Codex or Janus LLM review.")
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--session-date", default=None)
    args = parser.parse_args()
    exit_for_response(
        api_json(
            args.api_root,
            "GET",
            f"/v1/events/{args.event_id}/agent-context",
            query={"session_date": args.session_date},
        )
    )


if __name__ == "__main__":
    main()
