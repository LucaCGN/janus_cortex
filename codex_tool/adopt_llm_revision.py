from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

try:
    from codex_tool._client import api_json, base_parser, exit_for_response
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from _client import api_json, base_parser, exit_for_response


def _read_json(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _build_payload(args: Namespace) -> dict[str, Any]:
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


def main() -> None:
    parser = base_parser("Submit a reviewed LLM revision response or trace artifact for StrategyPlanJSON adoption.")
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

    exit_for_response(
        api_json(
            args.api_root,
            "POST",
            f"/v1/events/{args.event_id}/llm-revision/adopt",
            _build_payload(args),
        )
    )


if __name__ == "__main__":
    main()
