from __future__ import annotations

try:
    from codex_tools.janus.status import STATUS_PATH, main_for_status
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response

    STATUS_PATH = "/v1/ops/status"

    def main_for_status(description: str) -> None:
        parser = base_parser(description)
        args = parser.parse_args()
        exit_for_response(api_json(args.api_root, "GET", STATUS_PATH))


def main() -> None:
    main_for_status("Print Janus operational status.")


if __name__ == "__main__":
    main()
