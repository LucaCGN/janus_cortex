from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.modules.agentic.prewindow_readiness import (
    build_prewindow_sleeve_readiness,
    render_prewindow_sleeve_readiness_markdown,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a pre-window sleeve readiness artifact.")
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--strategy-plan", default=None)
    parser.add_argument("--backtest-review", default=None)
    parser.add_argument("--artifact-root", default="local/shared/artifacts/prewindow-sleeve-readiness")
    parser.add_argument("--source", default="codex-prewindow-readiness")
    args = parser.parse_args(argv)

    session_date = args.session_date or datetime.now(timezone.utc).date().isoformat()
    plan_path = Path(args.strategy_plan) if args.strategy_plan else _default_strategy_plan_path(args.event_id, session_date)
    review_path = Path(args.backtest_review) if args.backtest_review else _latest_backtest_review(session_date)
    plan = _read_json(plan_path)
    review = _read_json(review_path) if review_path is not None else {}

    report = build_prewindow_sleeve_readiness(
        event_id=args.event_id,
        strategy_plan=plan,
        backtest_review=review,
        source=args.source,
    )
    report["input_paths"] = {
        "strategy_plan": str(plan_path),
        "backtest_review": str(review_path) if review_path is not None else None,
    }
    output_dir = Path(args.artifact_root) / session_date / args.event_id
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"prewindow_sleeve_readiness_{stamp}.json"
    md_path = output_dir / f"prewindow_sleeve_readiness_{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_prewindow_sleeve_readiness_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "event_id": args.event_id,
                "status": report["status"],
                "json_path": str(json_path),
                "markdown_path": str(md_path),
                "warnings": report.get("warnings", []),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _default_strategy_plan_path(event_id: str, session_date: str) -> Path:
    return Path("local/shared/artifacts/strategy-plans") / session_date / event_id / "current.json"


def _latest_backtest_review(session_date: str) -> Path | None:
    root = Path("local/shared/artifacts/sleeve-deep-backtests") / session_date
    candidates = sorted(root.rglob("sleeve_deep_backtest_review.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
