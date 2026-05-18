from __future__ import annotations

try:
    from codex_tools.janus.events import EVENT_AGENT_CONTEXT_PATH_TEMPLATE, main_for_event_context
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response

    EVENT_AGENT_CONTEXT_PATH_TEMPLATE = "/v1/events/{event_id}/agent-context"

    def main_for_event_context(description: str) -> None:
        parser = base_parser(description)
        parser.add_argument("--event-id", required=True)
        parser.add_argument("--session-date", default=None)
        args = parser.parse_args()
        exit_for_response(
            api_json(
                args.api_root,
                "GET",
                EVENT_AGENT_CONTEXT_PATH_TEMPLATE.format(event_id=args.event_id),
                query={"session_date": args.session_date},
            )
        )


def main() -> None:
    main_for_event_context("Export event context for Codex or Janus LLM review.")


if __name__ == "__main__":
    main()
