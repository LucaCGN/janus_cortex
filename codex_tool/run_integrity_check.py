from __future__ import annotations

try:
    from codex_tools.janus.ops import INTEGRITY_CHECK_PATH, main_for_cycle
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import add_cycle_args, api_json, base_parser, cycle_payload, exit_for_response

    INTEGRITY_CHECK_PATH = "/v1/ops/integrity-check"

    def main_for_cycle(description: str, path: str) -> None:
        parser = add_cycle_args(base_parser(description))
        args = parser.parse_args()
        exit_for_response(api_json(args.api_root, "POST", path, cycle_payload(args)))


def main() -> None:
    main_for_cycle("Record or trigger one Janus pregame integrity check.", INTEGRITY_CHECK_PATH)


if __name__ == "__main__":
    main()
