from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

try:
    from codex_tools.janus.strategy import (
        LLM_REVISION_ADOPT_PATH_TEMPLATE,
        adopt_llm_revision,
        build_llm_revision_adoption_payload,
        main_for_llm_revision_adoption,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response

    LLM_REVISION_ADOPT_PATH_TEMPLATE = "/v1/events/{event_id}/llm-revision/adopt"

    def adopt_llm_revision(api_root: str, event_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return api_json(api_root, "POST", LLM_REVISION_ADOPT_PATH_TEMPLATE.format(event_id=event_id), payload)


def _read_json(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _fallback_build_payload(args: Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_date": args.session_date,
        "source": args.source,
        "reviewed_by": args.reviewed_by,
        "review_reason": args.review_reason,
        "apply_current": bool(args.apply_current),
        "notes": args.notes,
    }
    if args.response_path:
        payload["response"] = _read_json(args.response_path)
    if args.trace_artifact_path:
        payload["trace_artifact_path"] = args.trace_artifact_path
    return {key: value for key, value in payload.items() if value is not None}


if "build_llm_revision_adoption_payload" not in globals():  # pragma: no cover - direct script execution
    def build_llm_revision_adoption_payload(args: Namespace) -> dict[str, Any]:
        return _fallback_build_payload(args)

    def main_for_llm_revision_adoption(description: str) -> None:
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
        args = parser.parse_args()
        exit_for_response(adopt_llm_revision(args.api_root, args.event_id, build_llm_revision_adoption_payload(args)))


_build_payload = build_llm_revision_adoption_payload


def main() -> None:
    main_for_llm_revision_adoption(
        "Submit a reviewed LLM revision response or trace artifact for StrategyPlanJSON adoption."
    )


if __name__ == "__main__":
    main()
