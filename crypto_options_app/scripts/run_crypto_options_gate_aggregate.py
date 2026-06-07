from __future__ import annotations

"""Aggregate read-only crypto-options live gate evidence across capture dirs."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.pipelines.crypto.options.live_review import (  # noqa: E402
    build_live_gate_aggregate,
    write_live_gate_aggregate_artifacts,
)


def run(args: argparse.Namespace) -> dict[str, Any]:
    payload = build_live_gate_aggregate(
        [Path(path) for path in args.capture_dir],
        max_budget_usd=float(args.max_budget_usd),
        min_order_size=float(args.min_order_size),
        max_quote_age_seconds=float(args.max_quote_age_seconds),
        fetch_settlements=bool(args.fetch_settlements),
        max_settlement_fetches_per_capture=int(args.max_settlement_fetches_per_capture),
    )
    artifacts = write_live_gate_aggregate_artifacts(payload, output_dir=args.output_dir)
    payload["artifacts"] = artifacts
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aggregate read-only crypto-options live gate reports.")
    parser.add_argument("--capture-dir", action="append", required=True, help="Capture artifact directory. Repeat for multiple captures.")
    parser.add_argument("--output-dir", default=None, help="Optional output directory for aggregate artifacts.")
    parser.add_argument("--max-budget-usd", type=float, default=20.0, help="Maximum bankroll for the future micro live test.")
    parser.add_argument("--min-order-size", type=float, default=5.0, help="Polymarket minimum share order size to model.")
    parser.add_argument("--max-quote-age-seconds", type=float, default=90.0, help="Maximum quote age for candidate rows.")
    parser.add_argument("--fetch-settlements", action="store_true", help="Fetch public Gamma event metadata for expired windows and reconcile settled outcomes.")
    parser.add_argument("--max-settlement-fetches-per-capture", type=int, default=60, help="Maximum expired event slugs to fetch for each capture.")
    parser.add_argument("--json", action="store_true", help="Print full JSON payload.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        counts = payload.get("data_counts") or {}
        print(f"capture_count={counts.get('capture_count')}")
        print(f"quote_rows={counts.get('quote_rows')}")
        print(f"rolling_trade_candidate_rows={counts.get('rolling_trade_candidate_rows')}")
        print(f"rolling_settled_candidate_rows={counts.get('rolling_settled_candidate_rows')}")
        print(f"live_trading_authorized={payload.get('live_trading_authorized')}")
        print(f"artifact_json={(payload.get('artifacts') or {}).get('json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
