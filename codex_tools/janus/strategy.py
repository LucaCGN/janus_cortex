"""Janus StrategyPlanJSON and LLM revision helpers for Codex automation."""

from __future__ import annotations

import json
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Any

from codex_tool._client import add_cycle_args, cycle_payload, read_text
from codex_tools.janus.client import api_json, base_parser, exit_for_response

STRATEGY_PLAN_PATH_TEMPLATE = "/v1/events/{event_id}/strategy-plan"
STRATEGY_PLAN_EVALUATE_PATH_TEMPLATE = "/v1/events/{event_id}/strategy-plan/evaluate"
STRATEGY_PLAN_EXECUTE_PATH_TEMPLATE = "/v1/events/{event_id}/strategy-plan/execute"
LLM_REVISION_ADOPT_PATH_TEMPLATE = "/v1/events/{event_id}/llm-revision/adopt"
PREGAME_PLAN_PATH = "/v1/ops/pregame-plan"


def _read_json(path: str | None) -> Any:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _read_json_object(path: str) -> dict[str, Any]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _loads_dict(value: str | None, path: str | None = None) -> dict[str, Any]:
    if path:
        value = Path(path).read_text(encoding="utf-8-sig")
    if not value:
        return {}
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise SystemExit("expected JSON object")
    return payload


def build_strategy_plan_submit_parser(description: str) -> ArgumentParser:
    parser = base_parser(description)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--plan-path", required=True)
    return parser


def submit_strategy_plan(api_root: str, event_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return api_json(api_root, "POST", STRATEGY_PLAN_PATH_TEMPLATE.format(event_id=event_id), payload)


def build_strategy_plan_evaluate_parser(description: str) -> ArgumentParser:
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
    return parser


def build_strategy_plan_evaluate_payload(args: Namespace) -> dict[str, Any]:
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


def evaluate_strategy_plan(
    api_root: str,
    event_id: str,
    payload: dict[str, Any],
    *,
    execute: bool = False,
) -> dict[str, Any]:
    path_template = STRATEGY_PLAN_EXECUTE_PATH_TEMPLATE if execute else STRATEGY_PLAN_EVALUATE_PATH_TEMPLATE
    return api_json(api_root, "POST", path_template.format(event_id=event_id), payload)


def build_llm_revision_adoption_parser(description: str) -> ArgumentParser:
    parser = base_parser(description)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--source", default="codex-llm-revision-adoption")
    parser.add_argument("--reviewed-by", required=True)
    parser.add_argument("--review-reason", required=True)
    parser.add_argument("--notes", default=None)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--response-path", default=None)
    source.add_argument("--trace-artifact-path", default=None)
    parser.add_argument(
        "--apply-current",
        action="store_true",
        help="Promote the reviewed revision to current StrategyPlanJSON. Omit to record only a candidate adoption artifact.",
    )
    return parser


def build_llm_revision_adoption_payload(args: Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_date": args.session_date,
        "source": args.source,
        "reviewed_by": args.reviewed_by,
        "review_reason": args.review_reason,
        "apply_current": bool(args.apply_current),
        "notes": args.notes,
    }
    if args.response_path:
        payload["response"] = _read_json_object(args.response_path)
    if args.trace_artifact_path:
        payload["trace_artifact_path"] = args.trace_artifact_path
    return {key: value for key, value in payload.items() if value is not None}


def adopt_llm_revision(api_root: str, event_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return api_json(api_root, "POST", LLM_REVISION_ADOPT_PATH_TEMPLATE.format(event_id=event_id), payload)


def build_pregame_research_parser(description: str) -> ArgumentParser:
    parser = add_cycle_args(base_parser(description))
    parser.add_argument("--research-path", default=None)
    return parser


def build_pregame_research_payload(args: Namespace) -> dict[str, Any]:
    payload = cycle_payload(args)
    payload["research_path"] = args.research_path
    payload["research_markdown"] = read_text(args.research_path)
    return payload


def submit_pregame_research(api_root: str, payload: dict[str, Any]) -> dict[str, Any]:
    return api_json(api_root, "POST", PREGAME_PLAN_PATH, payload)


def main_for_strategy_plan_submit(description: str) -> None:
    args = build_strategy_plan_submit_parser(description).parse_args()
    payload = _read_json_object(args.plan_path)
    exit_for_response(submit_strategy_plan(args.api_root, args.event_id, payload))


def main_for_strategy_plan_evaluate(description: str) -> None:
    args = build_strategy_plan_evaluate_parser(description).parse_args()
    exit_for_response(
        evaluate_strategy_plan(
            args.api_root,
            args.event_id,
            build_strategy_plan_evaluate_payload(args),
            execute=bool(args.execute),
        )
    )


def main_for_llm_revision_adoption(description: str) -> None:
    args = build_llm_revision_adoption_parser(description).parse_args()
    exit_for_response(adopt_llm_revision(args.api_root, args.event_id, build_llm_revision_adoption_payload(args)))


def main_for_pregame_research(description: str) -> None:
    args = build_pregame_research_parser(description).parse_args()
    exit_for_response(submit_pregame_research(args.api_root, build_pregame_research_payload(args)))
