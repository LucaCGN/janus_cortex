from __future__ import annotations

import json
from pathlib import Path

try:
    from codex_tools.janus.strategy import (
        STRATEGY_PLAN_EVALUATE_PATH_TEMPLATE,
        STRATEGY_PLAN_EXECUTE_PATH_TEMPLATE,
        _loads_dict,
        _read_json,
        build_strategy_plan_evaluate_payload,
        evaluate_strategy_plan,
        main_for_strategy_plan_evaluate,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response

    STRATEGY_PLAN_EVALUATE_PATH_TEMPLATE = "/v1/events/{event_id}/strategy-plan/evaluate"
    STRATEGY_PLAN_EXECUTE_PATH_TEMPLATE = "/v1/events/{event_id}/strategy-plan/execute"

    def _read_json(path: str | None):
        if not path:
            return None
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))

    def _loads_dict(value: str | None, path: str | None = None) -> dict:
        if path:
            value = Path(path).read_text(encoding="utf-8-sig")
        if not value:
            return {}
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise SystemExit("expected JSON object")
        return payload

    def build_strategy_plan_evaluate_payload(args) -> dict:  # type: ignore[no-untyped-def,type-arg]
        return {
            "plan": _read_json(args.plan_path),
            "account_id": args.account_id,
            "dry_run": not bool(args.live_money),
            "execute": bool(args.execute),
            "market_state": _loads_dict(args.market_state_json, args.market_state_path),
            "portfolio_state": _loads_dict(args.portfolio_state_json, args.portfolio_state_path),
            "source": args.source,
            "max_intents": args.max_intents,
        }

    def evaluate_strategy_plan(api_root: str, event_id: str, payload: dict, *, execute: bool = False):  # type: ignore[type-arg]
        path_template = STRATEGY_PLAN_EXECUTE_PATH_TEMPLATE if execute else STRATEGY_PLAN_EVALUATE_PATH_TEMPLATE
        return api_json(api_root, "POST", path_template.format(event_id=event_id), payload)

    def main_for_strategy_plan_evaluate(description: str) -> None:
        parser = base_parser(description)
        parser.add_argument("--event-id", required=True)
        parser.add_argument("--plan-path", default=None)
        parser.add_argument("--account-id", default=None)
        parser.add_argument("--live-money", action="store_true", help="Set dry_run=false.")
        parser.add_argument("--execute", action="store_true", help="Submit valid intents through the audited order path.")
        parser.add_argument("--market-state-json", default=None)
        parser.add_argument("--market-state-path", default=None)
        parser.add_argument("--portfolio-state-json", default=None)
        parser.add_argument("--portfolio-state-path", default=None)
        parser.add_argument("--source", default="codex")
        parser.add_argument("--max-intents", type=int, default=10)
        args = parser.parse_args()
        exit_for_response(
            evaluate_strategy_plan(
                args.api_root,
                args.event_id,
                build_strategy_plan_evaluate_payload(args),
                execute=bool(args.execute),
            )
        )


def main() -> None:
    main_for_strategy_plan_evaluate("Evaluate or execute the current Janus strategy plan for one event.")


if __name__ == "__main__":
    main()
