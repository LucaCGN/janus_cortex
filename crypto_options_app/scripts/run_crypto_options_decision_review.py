from __future__ import annotations

"""Generate read-only live decision-review artifacts for issue #47.

This CLI consumes live capture files and writes candidate/no-trade review
artifacts. It never places, cancels, signs, broadcasts, redeems, or routes
orders.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.pipelines.crypto.options.live_review import (  # noqa: E402
    build_live_decision_review,
    write_live_decision_review_artifacts,
)


def run(args: argparse.Namespace) -> dict[str, Any]:
    payload = build_live_decision_review(
        args.capture_dir,
        max_budget_usd=float(args.max_budget_usd),
        min_order_size=float(args.min_order_size),
        max_quote_age_seconds=float(args.max_quote_age_seconds),
        max_quote_history_seconds=float(args.max_quote_history_seconds) if args.max_quote_history_seconds is not None else None,
        max_quote_rows=int(args.max_quote_rows) if args.max_quote_rows is not None else None,
        fetch_settlements=bool(args.fetch_settlements),
        max_settlement_fetches=int(args.max_settlement_fetches),
    )
    artifacts = write_live_decision_review_artifacts(payload, output_dir=args.output_dir)
    payload["artifacts"] = artifacts
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build read-only crypto-options live decision-review artifacts.")
    parser.add_argument("--capture-dir", required=True, help="Live capture artifact directory.")
    parser.add_argument("--output-dir", default=None, help="Optional output directory for review artifacts.")
    parser.add_argument("--max-budget-usd", type=float, default=20.0, help="Maximum bankroll for the future micro live test.")
    parser.add_argument("--min-order-size", type=float, default=5.0, help="Polymarket minimum share order size to model.")
    parser.add_argument("--max-quote-age-seconds", type=float, default=20.0, help="Maximum quote age for candidate rows.")
    parser.add_argument("--max-quote-history-seconds", type=float, default=None, help="Optional recent quote history window for low-latency live review.")
    parser.add_argument("--max-quote-rows", type=int, default=None, help="Optional tail row limit for low-latency live review.")
    parser.add_argument("--fetch-settlements", action="store_true", help="Fetch public Gamma event metadata for expired windows and reconcile settled outcomes.")
    parser.add_argument("--max-settlement-fetches", type=int, default=60, help="Maximum expired event slugs to fetch for settlement reconciliation.")
    parser.add_argument("--json", action="store_true", help="Print full JSON payload.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        counts = payload.get("data_counts") or {}
        print(f"quote_rows={counts.get('quote_rows')}")
        print(f"latest_outcome_quotes={counts.get('latest_outcome_quotes')}")
        print(f"ready_candidate_count={counts.get('ready_candidate_count')}")
        print(f"settlement_label_rows={counts.get('settlement_label_rows')}")
        print(f"settled_candidate_rows={counts.get('settled_candidate_rows')}")
        print(f"live_trading_authorized={payload.get('live_trading_authorized')}")
        print(f"artifact_json={(payload.get('artifacts') or {}).get('json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
