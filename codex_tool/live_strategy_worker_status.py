from __future__ import annotations

try:
    from codex_tools.janus.worker import LIVE_STRATEGY_WORKER_STATUS_PATH, main_for_live_strategy_worker_status
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response

    LIVE_STRATEGY_WORKER_STATUS_PATH = "/v1/ops/live-strategy-worker/status"

    def main_for_live_strategy_worker_status(description: str) -> None:
        parser = base_parser(description)
        args = parser.parse_args()
        exit_for_response(api_json(args.api_root, "GET", LIVE_STRATEGY_WORKER_STATUS_PATH))


def main() -> None:
    main_for_live_strategy_worker_status("Inspect the service-owned Janus live strategy worker.")


if __name__ == "__main__":
    main()
