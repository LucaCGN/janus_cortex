from __future__ import annotations

try:
    from codex_tool._client import api_json, base_parser, exit_for_response
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response


def main() -> None:
    parser = base_parser("Print Janus operational status.")
    args = parser.parse_args()
    exit_for_response(api_json(args.api_root, "GET", "/v1/ops/status"))


if __name__ == "__main__":
    main()
