from __future__ import annotations

try:
    from codex_tool._client import add_cycle_args, api_json, base_parser, cycle_payload, exit_for_response
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import add_cycle_args, api_json, base_parser, cycle_payload, exit_for_response


def main() -> None:
    parser = add_cycle_args(base_parser("Record or trigger one Janus postgame review."))
    args = parser.parse_args()
    exit_for_response(api_json(args.api_root, "POST", "/v1/ops/postgame-review", cycle_payload(args)))


if __name__ == "__main__":
    main()
