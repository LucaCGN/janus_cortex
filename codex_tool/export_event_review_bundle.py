from __future__ import annotations

try:
    from codex_tool._client import api_json, base_parser, exit_for_response
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response


def main() -> None:
    parser = base_parser("Export one Janus event review bundle for postgame or live-review reconstruction.")
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--account-id", default=None)
    args = parser.parse_args()
    exit_for_response(
        api_json(
            args.api_root,
            "GET",
            f"/v1/events/{args.event_id}/review-bundle",
            query={"session_date": args.session_date, "account_id": args.account_id},
        )
    )


if __name__ == "__main__":
    main()
