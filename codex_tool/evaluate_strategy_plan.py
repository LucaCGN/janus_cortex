from __future__ import annotations

import json
from pathlib import Path

try:
    from codex_tool._client import api_json, base_parser, exit_for_response
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response


def main() -> None:
    parser = base_parser("Evaluate or execute the current Janus strategy plan for one event.")
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--plan-path", default=None)
    parser.add_argument("--account-id", default=None)
    parser.add_argument("--live-money", action="store_true", help="Set dry_run=false.")
    parser.add_argument("--execute", action="store_true", help="Submit valid intents through the audited order path.")
    parser.add_argument("--market-state-json", default=None)
    parser.add_argument("--portfolio-state-json", default=None)
    parser.add_argument("--source", default="codex")
    parser.add_argument("--max-intents", type=int, default=10)
    args = parser.parse_args()

    payload = {
        "plan": _read_json(args.plan_path),
        "account_id": args.account_id,
        "dry_run": not bool(args.live_money),
        "execute": bool(args.execute),
        "market_state": _loads_dict(args.market_state_json),
        "portfolio_state": _loads_dict(args.portfolio_state_json),
        "source": args.source,
        "max_intents": args.max_intents,
    }
    endpoint = "execute" if args.execute else "evaluate"
    exit_for_response(api_json(args.api_root, "POST", f"/v1/events/{args.event_id}/strategy-plan/{endpoint}", payload))


def _read_json(path: str | None):
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _loads_dict(value: str | None) -> dict:
    if not value:
        return {}
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise SystemExit("expected JSON object")
    return payload


if __name__ == "__main__":
    main()
