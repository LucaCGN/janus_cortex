from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.config import CENTRAL_ARTIFACT_ROOT, CENTRAL_DB_PATH  # noqa: E402
from crypto_options_app.workers.core_flow_live_runner import CoreFlowLiveRunConfig, run_core_flow_live_test  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the guarded Crypto Options App Core Flow live structural validation.")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--db-path", default=str(CENTRAL_DB_PATH))
    parser.add_argument("--artifact-root", default=str(CENTRAL_ARTIFACT_ROOT))
    parser.add_argument("--strategy-ids", nargs="+", default=None)
    parser.add_argument("--max-event-cycles", type=int, default=3)
    parser.add_argument("--total-budget-cap-usd", type=float, default=10.0)
    parser.add_argument("--max-wall-seconds", type=float, default=1200.0)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--monitor-interval-seconds", type=float, default=300.0)
    parser.add_argument("--symbols", nargs="+", default=["BTC", "ETH"])
    parser.add_argument("--operator", default="codex-automation")
    parser.add_argument("--reason", default="Core Flow live structural validation")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    run_id = args.run_id or f"core-flow-live-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    result = run_core_flow_live_test(
        CoreFlowLiveRunConfig(
            run_id=run_id,
            strategy_ids=tuple(args.strategy_ids) if args.strategy_ids else CoreFlowLiveRunConfig(run_id=run_id).strategy_ids,
            max_event_cycles=args.max_event_cycles,
            total_budget_cap_usd=args.total_budget_cap_usd,
            max_wall_seconds=args.max_wall_seconds,
            poll_seconds=args.poll_seconds,
            monitor_interval_seconds=args.monitor_interval_seconds,
            symbols=tuple(symbol.upper() for symbol in args.symbols),
            db_path=Path(args.db_path),
            artifact_root=Path(args.artifact_root),
            operator=args.operator,
            reason=args.reason,
        )
    )
    payload = {
        "run_id": result.run_id,
        "status": result.status,
        "generated_at_utc": result.generated_at_utc,
        "event_results": [item.__dict__ for item in result.event_results],
        "estimated_spent_usd": result.estimated_spent_usd,
        "blockers": list(result.blockers),
        "artifact_json": result.artifact_json,
        "health_snapshot_json": result.health_snapshot_json,
        "order_audit_json": result.order_audit_json,
        "manual_orders_avoided": result.manual_orders_avoided,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"run_id={result.run_id}")
        print(f"status={result.status}")
        print(f"event_count={len(result.event_results)}")
        print(f"estimated_spent_usd={result.estimated_spent_usd}")
        print(f"blockers={list(result.blockers)}")
        print(f"artifact_json={result.artifact_json}")
        print(f"health_snapshot_json={result.health_snapshot_json}")
        print(f"order_audit_json={result.order_audit_json}")
    return 0 if result.status == "validated" else 1


if __name__ == "__main__":
    raise SystemExit(main())
