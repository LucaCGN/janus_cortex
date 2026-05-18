from __future__ import annotations

try:
    from codex_tools.janus.events import EVENT_REVIEW_BUNDLE_PATH_TEMPLATE, main_for_event_review_bundle
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response

    EVENT_REVIEW_BUNDLE_PATH_TEMPLATE = "/v1/events/{event_id}/review-bundle"

    def main_for_event_review_bundle(description: str) -> None:
        parser = base_parser(description)
        parser.add_argument("--event-id", required=True)
        parser.add_argument("--session-date", default=None)
        parser.add_argument("--account-id", default=None)
        args = parser.parse_args()
        exit_for_response(
            api_json(
                args.api_root,
                "GET",
                EVENT_REVIEW_BUNDLE_PATH_TEMPLATE.format(event_id=args.event_id),
                query={"session_date": args.session_date, "account_id": args.account_id},
            )
        )


def main() -> None:
    main_for_event_review_bundle("Export one Janus event review bundle for postgame or live-review reconstruction.")


if __name__ == "__main__":
    main()
