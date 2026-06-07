from __future__ import annotations

"""Render a complete report of every crypto-options strategy candidate."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.pipelines.options.candidate_report import (  # noqa: E402
    build_candidate_report,
    write_candidate_report_artifacts,
)


def run(args: argparse.Namespace) -> dict[str, Any]:
    aggregate = json.loads(Path(args.aggregate_artifact).read_text(encoding="utf-8"))
    criteria = _criteria_from_args(args)
    payload = build_candidate_report(
        aggregate,
        operator_criteria=criteria,
        top_breakdown_rows=int(args.top_breakdown_rows),
        representative_rows=int(args.representative_rows),
    )
    artifacts = write_candidate_report_artifacts(payload, output_dir=args.output_dir)
    payload["artifacts"] = artifacts
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a complete crypto-options candidate evidence report.")
    parser.add_argument("--aggregate-artifact", required=True, help="Live gate aggregate JSON artifact.")
    parser.add_argument("--output-dir", default=None, help="Optional output directory.")
    parser.add_argument("--min-deduped-trades", type=int, default=30, help="Operator preview criterion.")
    parser.add_argument("--min-win-rate", type=float, default=0.60, help="Operator preview criterion.")
    parser.add_argument("--min-return-sum", type=float, default=0.0, help="Operator preview criterion.")
    parser.add_argument("--max-sequential-losses", type=int, default=3, help="Operator preview criterion.")
    parser.add_argument("--min-budget-floor", type=float, default=17.0, help="Operator preview criterion.")
    parser.add_argument("--min-settlement-coverage", type=float, default=0.95, help="Operator preview criterion.")
    parser.add_argument("--top-breakdown-rows", type=int, default=5, help="Rows per feature breakdown to include in JSON.")
    parser.add_argument("--representative-rows", type=int, default=5, help="Representative settled rows per strategy.")
    parser.add_argument("--json", action="store_true", help="Print full JSON payload.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"candidate_count={payload.get('candidate_count')}")
        print(f"operator_selection_required={payload.get('operator_selection_required')}")
        print(f"artifact_json={(payload.get('artifacts') or {}).get('json')}")
        print(f"artifact_markdown={(payload.get('artifacts') or {}).get('markdown')}")
    return 0


def _criteria_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "min_deduped_trades": int(args.min_deduped_trades),
        "min_win_rate": float(args.min_win_rate),
        "min_return_sum": float(args.min_return_sum),
        "max_sequential_losses": int(args.max_sequential_losses),
        "min_budget_floor": float(args.min_budget_floor),
        "min_settlement_coverage": float(args.min_settlement_coverage),
    }


if __name__ == "__main__":
    raise SystemExit(main())
