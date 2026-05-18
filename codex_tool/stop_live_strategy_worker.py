from __future__ import annotations

try:
    from codex_tools.janus.worker import LIVE_STRATEGY_WORKER_STOP_PATH, main_for_live_strategy_worker_stop
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response

    LIVE_STRATEGY_WORKER_STOP_PATH = "/v1/ops/live-strategy-worker/stop"

    def main_for_live_strategy_worker_stop(description: str) -> None:
        parser = base_parser(description)
        args = parser.parse_args()
        exit_for_response(api_json(args.api_root, "POST", LIVE_STRATEGY_WORKER_STOP_PATH, {}))


def main() -> None:
    main_for_live_strategy_worker_stop("Stop the service-owned Janus live strategy worker.")


if __name__ == "__main__":
    main()
