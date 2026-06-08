from __future__ import annotations

"""Build the read-only $20 crypto-options micro-test protocol artifact."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.pipelines.options.micro_test_protocol import (  # noqa: E402
    build_micro_test_protocol,
    write_micro_test_protocol_artifacts,
)


def run(args: argparse.Namespace) -> dict[str, Any]:
    aggregate = json.loads(Path(args.aggregate_artifact).read_text(encoding="utf-8"))
    payload = build_micro_test_protocol(
        aggregate,
        max_budget_usd=float(args.max_budget_usd),
        min_order_size=float(args.min_order_size),
        user_approval_recorded=bool(args.user_approval_recorded),
        allowed_strategy_ids=args.strategy or None,
        max_test_trades_before_review=int(args.max_test_trades_before_review),
        hard_stop_full_losses=int(args.hard_stop_full_losses),
        hard_stop_loss_usd=float(args.hard_stop_loss_usd),
        max_position_cost_usd=float(args.max_position_cost_usd),
    )
    artifacts = write_micro_test_protocol_artifacts(payload, output_dir=args.output_dir)
    payload["artifacts"] = artifacts
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a read-only crypto-options $20 micro-test protocol.")
    parser.add_argument("--aggregate-artifact", required=True, help="Aggregate gate JSON artifact.")
    parser.add_argument("--output-dir", default=None, help="Optional output directory.")
    parser.add_argument("--max-budget-usd", type=float, default=20.0, help="Maximum bankroll for the possible micro-test.")
    parser.add_argument("--min-order-size", type=float, default=5.0, help="Polymarket minimum share order size to model.")
    parser.add_argument("--strategy", action="append", help="Allowed strategy id. Repeat to build a multi-strategy manual protocol.")
    parser.add_argument("--max-test-trades-before-review", type=int, default=5, help="Maximum manual test trades before review.")
    parser.add_argument("--hard-stop-full-losses", type=int, default=2, help="Full-loss hard stop for the manual test ledger.")
    parser.add_argument("--hard-stop-loss-usd", type=float, default=3.0, help="Net realized loss hard stop in dollars.")
    parser.add_argument("--max-position-cost-usd", type=float, default=5.0, help="Maximum total cost for one 5-share ticket.")
    parser.add_argument(
        "--user-approval-recorded",
        action="store_true",
        help="Set only after explicit user approval for a separate micro-test protocol review.",
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON payload.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"protocol_status={payload.get('protocol_status')}")
        print(f"live_trading_authorized={payload.get('live_trading_authorized')}")
        print(f"orders_allowed={payload.get('orders_allowed')}")
        print(f"blockers={payload.get('blockers')}")
        print(f"artifact_json={(payload.get('artifacts') or {}).get('json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
