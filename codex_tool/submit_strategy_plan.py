from __future__ import annotations

import json
from pathlib import Path

try:
    from codex_tool._client import api_json, base_parser, exit_for_response
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response


def main() -> None:
    parser = base_parser("Submit a Janus StrategyPlanJSON for one event.")
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--plan-path", required=True)
    args = parser.parse_args()

    payload = json.loads(Path(args.plan_path).read_text(encoding="utf-8-sig"))
    exit_for_response(api_json(args.api_root, "POST", f"/v1/events/{args.event_id}/strategy-plan", payload))


if __name__ == "__main__":
    main()
