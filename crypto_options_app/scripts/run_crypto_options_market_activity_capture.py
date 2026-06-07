from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.data_services.polymarket_live_activity_capture import (
    LiveActivityCaptureConfig,
    capture_live_activity_once_sync,
)
from crypto_options_app.db.errors import is_transient_database_error  # noqa: E402
from crypto_options_app.scripts._status_io import write_json_atomically  # noqa: E402


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
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument("--state-path", default="crypto_options_app/artifacts/automation/market_activity_capture_status.json")
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
    iteration = 0
    last_status = "unknown"
    while True:
        iteration += 1
        try:
            summary = _capture_with_transient_db_lock_retries(config=config, condition_ids=condition_ids)
            last_status = summary.status
            payload = _summary_payload(summary, iteration=iteration)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - bounded read-only service should persist failures for observability.
            status = "degraded" if is_transient_database_error(exc) else "failed"
            last_status = status
            payload = {
                "schema_version": "crypto_options_market_activity_capture_status_v1",
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "status": status,
                "iteration": iteration,
                "error": f"{type(exc).__name__}:{exc}",
                "blockers": ["transient_db_lock_retry_exhausted"] if status == "degraded" else [f"{type(exc).__name__}:{exc}"],
                "orders_allowed": False,
                "live_trading_authorized": False,
                "manual_orders_avoided": True,
            }
        if args.state_path:
            write_json_atomically(Path(args.state_path), payload)
        print(json.dumps(payload, sort_keys=True) if args.json else payload, flush=True)
        if not args.loop:
            return 0 if last_status in {"healthy", "degraded"} else 1
        if args.max_iterations is not None and iteration >= int(args.max_iterations):
            return 0 if last_status in {"healthy", "degraded"} else 1
        time.sleep(max(1.0, args.interval_seconds))


def _summary_payload(summary, *, iteration: int) -> dict:
    return {
        "schema_version": "crypto_options_market_activity_capture_status_v1",
        "generated_at_utc": summary.generated_at_utc,
        "status": summary.status,
        "iteration": iteration,
        "db_path": summary.db_path,
        "condition_count": summary.condition_count,
        "trade_rows_inserted": summary.trade_rows_inserted,
        "blockers": list(summary.blockers),
        "state": summary.state,
        "orders_allowed": summary.orders_allowed,
        "live_trading_authorized": summary.live_trading_authorized,
        "manual_orders_avoided": True,
    }


def _capture_with_transient_db_lock_retries(
    *,
    config: LiveActivityCaptureConfig,
    condition_ids,
    max_attempts: int = 4,
):
    last_error: Exception | None = None
    for attempt in range(max(1, max_attempts)):
        try:
            return capture_live_activity_once_sync(config=config, condition_ids=condition_ids)
        except Exception as exc:  # noqa: BLE001 - classify before deciding whether to retry.
            last_error = exc
            if not is_transient_database_error(exc) or attempt >= max_attempts - 1:
                raise
            time.sleep(0.35 * (attempt + 1))
    raise last_error or RuntimeError("database lock retry failed")


if __name__ == "__main__":
    raise SystemExit(main())
