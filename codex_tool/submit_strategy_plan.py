from __future__ import annotations

import json
from pathlib import Path

try:
    from codex_tools.janus.strategy import (
        STRATEGY_PLAN_PATH_TEMPLATE,
        main_for_strategy_plan_submit,
        submit_strategy_plan,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response

    STRATEGY_PLAN_PATH_TEMPLATE = "/v1/events/{event_id}/strategy-plan"

    def submit_strategy_plan(api_root: str, event_id: str, payload: dict):  # type: ignore[type-arg]
        return api_json(api_root, "POST", STRATEGY_PLAN_PATH_TEMPLATE.format(event_id=event_id), payload)

    def main_for_strategy_plan_submit(description: str) -> None:
        parser = base_parser(description)
        parser.add_argument("--event-id", required=True)
        parser.add_argument("--plan-path", required=True)
        args = parser.parse_args()
        payload = json.loads(Path(args.plan_path).read_text(encoding="utf-8-sig"))
        exit_for_response(submit_strategy_plan(args.api_root, args.event_id, payload))


def main() -> None:
    main_for_strategy_plan_submit("Submit a Janus StrategyPlanJSON for one event.")


if __name__ == "__main__":
    main()
