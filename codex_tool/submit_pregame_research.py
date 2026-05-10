from __future__ import annotations

try:
    from codex_tool._client import add_cycle_args, api_json, base_parser, cycle_payload, exit_for_response, read_text
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import add_cycle_args, api_json, base_parser, cycle_payload, exit_for_response, read_text


def main() -> None:
    parser = add_cycle_args(base_parser("Submit Codex pregame research to Janus."))
    parser.add_argument("--research-path", default=None)
    args = parser.parse_args()
    payload = cycle_payload(args)
    payload["research_path"] = args.research_path
    payload["research_markdown"] = read_text(args.research_path)
    exit_for_response(api_json(args.api_root, "POST", "/v1/ops/pregame-plan", payload))


if __name__ == "__main__":
    main()
