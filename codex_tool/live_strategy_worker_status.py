from __future__ import annotations

try:
    from codex_tool._client import api_json, base_parser, exit_for_response
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response


def main() -> None:
    parser = base_parser("Inspect the service-owned Janus live strategy worker.")
    args = parser.parse_args()
    exit_for_response(api_json(args.api_root, "GET", "/v1/ops/live-strategy-worker/status"))


if __name__ == "__main__":
    main()
