from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.data_services.polymarket_live_activity_capture import (
    LiveActivityCaptureConfig,
    capture_live_activity_once_sync,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture Polymarket live activity trade prints for crypto option events.")
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--condition-id", action="append", default=[])
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument("--lookback-minutes", type=int, default=15)
    parser.add_argument("--lookahead-minutes", type=int, default=15)
    parser.add_argument("--max-markets", type=int, default=80)
    parser.add_argument("--max-concurrency", type=int, default=6)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    config = LiveActivityCaptureConfig(
        db_path=args.db_path or LiveActivityCaptureConfig.db_path,
        lookback_minutes=args.lookback_minutes,
        lookahead_minutes=args.lookahead_minutes,
        max_markets=args.max_markets,
        max_concurrency=args.max_concurrency,
        timeout_seconds=args.timeout_seconds,
    )
    condition_ids = tuple(args.condition_id) or None
    while True:
        summary = capture_live_activity_once_sync(config=config, condition_ids=condition_ids)
        payload = {
            "generated_at_utc": summary.generated_at_utc,
            "status": summary.status,
            "db_path": summary.db_path,
            "condition_count": summary.condition_count,
            "trade_rows_inserted": summary.trade_rows_inserted,
            "blockers": list(summary.blockers),
            "orders_allowed": summary.orders_allowed,
            "live_trading_authorized": summary.live_trading_authorized,
        }
        print(json.dumps(payload, sort_keys=True) if args.json else payload, flush=True)
        if not args.loop:
            return 0 if summary.status in {"healthy", "degraded"} else 1
        time.sleep(max(1.0, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
