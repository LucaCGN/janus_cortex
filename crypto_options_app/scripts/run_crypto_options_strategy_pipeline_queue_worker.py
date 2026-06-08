from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.config import CENTRAL_DB_PATH  # noqa: E402
from crypto_options_app.workers.strategy_pipeline_queue_worker import (  # noqa: E402
    StrategyPipelineQueueWorkerConfig,
    run_strategy_pipeline_queue_worker,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Drain durable strategy pipeline queue work.")
    parser.add_argument("--db-path", default=str(CENTRAL_DB_PATH))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--owner", default="strategy-pipeline-queue-worker")
    parser.add_argument("--max-items", type=int, default=3)
    parser.add_argument("--lease-seconds", type=int, default=1800)
    parser.add_argument("--process-lock-stale-seconds", type=int, default=2700)
    parser.add_argument("--no-backfill-from-promotions", action="store_true")
    parser.add_argument("--no-refresh-promotions", action="store_true")
    parser.add_argument("--max-scenarios-per-action", type=int, default=1)
    parser.add_argument("--max-trades-per-strategy", type=int, default=1)
    parser.add_argument("--validation-budget-cap-usd", type=float, default=50.0)
    parser.add_argument("--forward-mark-horizon-seconds", type=float, default=60.0)
    parser.add_argument("--recent-shadow-retry-seconds", type=int, default=300)
    parser.add_argument("--max-parallel-live-candidates", type=int, default=3)
    parser.add_argument("--max-live-strategy-slots", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-postgres-cpu-percent", type=float, default=350.0)
    parser.add_argument("--max-postgres-memory-percent", type=float, default=80.0)
    parser.add_argument("--report-dir", default="crypto_options_app/artifacts/team_coordination/automation_status")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run_strategy_pipeline_queue_worker(
        StrategyPipelineQueueWorkerConfig(
            db_path=Path(args.db_path),
            run_id=args.run_id,
            owner=str(args.owner),
            max_items=max(1, int(args.max_items)),
            lease_seconds=max(60, int(args.lease_seconds)),
            process_lock_stale_seconds=max(300, int(args.process_lock_stale_seconds)),
            backfill_from_promotions=not bool(args.no_backfill_from_promotions),
            refresh_promotions=not bool(args.no_refresh_promotions),
            max_scenarios_per_action=max(1, int(args.max_scenarios_per_action)),
            max_trades_per_strategy=max(1, int(args.max_trades_per_strategy)),
            validation_budget_cap_usd=float(args.validation_budget_cap_usd),
            forward_mark_horizon_seconds=float(args.forward_mark_horizon_seconds),
            recent_shadow_retry_seconds=max(60, int(args.recent_shadow_retry_seconds)),
            max_parallel_live_candidates=max(1, int(args.max_parallel_live_candidates)),
            max_live_strategy_slots=max(0, int(args.max_live_strategy_slots)),
            dry_run=bool(args.dry_run),
            max_postgres_cpu_percent=float(args.max_postgres_cpu_percent),
            max_postgres_memory_percent=float(args.max_postgres_memory_percent),
            report_dir=Path(args.report_dir),
        )
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"run_id={payload['run_id']}")
        print(f"backfilled_count={payload['backfilled_count']}")
        print(f"claimed_count={payload['claimed_count']}")
        print(f"executed_action_count={payload['executed_action_count']}")
        print(f"before_queue_counts={json.dumps(payload['before_queue_counts'], sort_keys=True)}")
        print(f"after_queue_counts={json.dumps(payload['after_queue_counts'], sort_keys=True)}")
        print(f"next_action={payload['next_action']}")
        print("report=crypto_options_app/artifacts/team_coordination/automation_status/strategy_pipeline_queue_worker_latest.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
