from __future__ import annotations

"""Run read-only external crypto technical observers for BTC/ETH."""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.config import CENTRAL_DB_PATH  # noqa: E402
from crypto_options_app.data_services.underlying_technical_observers import (  # noqa: E402
    TechnicalObserverConfig,
    capture_underlying_technical_observers_once,
)
from crypto_options_app.db.errors import is_transient_database_error  # noqa: E402
from crypto_options_app.scripts._status_io import write_json_atomically  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture read-only IFCM/TradersUnion crypto technical observers.")
    parser.add_argument("--db-path", default=str(CENTRAL_DB_PATH))
    parser.add_argument("--symbols", nargs="+", default=["BTC", "ETH"])
    parser.add_argument("--ifcm-periods", nargs="+", default=["1", "5", "15", "30", "60", "240", "1440", "10080"])
    parser.add_argument("--no-ifcm", action="store_true")
    parser.add_argument("--no-tradersunion", action="store_true")
    parser.add_argument("--target-refresh-seconds", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--loop", action="store_true", help="Run continuously until interrupted.")
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument(
        "--state-path",
        default="crypto_options_app/artifacts/automation/underlying_technical_observers_status.json",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = TechnicalObserverConfig(
        db_path=Path(args.db_path),
        symbols=tuple(symbol.upper() for symbol in args.symbols),
        ifcm_periods=tuple(str(period) for period in args.ifcm_periods),
        include_ifcm=not args.no_ifcm,
        include_tradersunion=not args.no_tradersunion,
        target_refresh_seconds=args.target_refresh_seconds,
        timeout_seconds=args.timeout_seconds,
    )
    if args.loop:
        return _run_loop(config=config, args=args)
    summary = capture_underlying_technical_observers_once(config=config)
    payload = summary.__dict__
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"status={summary.status}")
        print(f"db_path={summary.db_path}")
        print(f"snapshot_rows_inserted={summary.snapshot_rows_inserted}")
        print(f"component_rows_inserted={summary.component_rows_inserted}")
        print(f"readiness_rows_inserted={summary.readiness_rows_inserted}")
        print(f"blockers={list(summary.blockers)}")
    return 0 if summary.status in {"healthy", "degraded"} else 1


def _run_loop(*, config: TechnicalObserverConfig, args: argparse.Namespace) -> int:
    state_path = Path(args.state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    iteration = 0
    last_status = "unknown"
    while args.max_iterations is None or iteration < int(args.max_iterations):
        iteration += 1
        try:
            summary = _capture_with_transient_db_lock_retries(config=config)
            last_status = summary.status
            payload = {
                "schema_version": "crypto_options_underlying_technical_observers_status_v1",
                "status": summary.status,
                "iteration": iteration,
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "summary": summary.__dict__,
                "orders_allowed": False,
                "live_trading_authorized": False,
                "manual_orders_avoided": True,
            }
            write_json_atomically(state_path, payload)
            if args.json:
                print(json.dumps(payload, sort_keys=True, default=str), flush=True)
            else:
                print(
                    f"iteration={iteration} status={summary.status} "
                    f"snapshots={summary.snapshot_rows_inserted} "
                    f"components={summary.component_rows_inserted} readiness={summary.readiness_rows_inserted} "
                    f"blockers={list(summary.blockers)}",
                    flush=True,
                )
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - long-running data service must retry transient locks/network errors.
            last_status = "degraded"
            status = "degraded" if is_transient_database_error(exc) else "failed"
            payload = {
                "schema_version": "crypto_options_underlying_technical_observers_status_v1",
                "status": status,
                "iteration": iteration,
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "error": f"{type(exc).__name__}:{exc}",
                "blockers": ["transient_db_lock_retry_exhausted"] if is_transient_database_error(exc) else [f"{type(exc).__name__}:{exc}"],
                "orders_allowed": False,
                "live_trading_authorized": False,
                "manual_orders_avoided": True,
            }
            write_json_atomically(state_path, payload)
            print(f"iteration={iteration} status={status} error={payload['error']}", flush=True)
        time.sleep(max(1.0, float(args.interval_seconds)))
    return 0 if last_status in {"healthy", "degraded"} else 1


def _capture_with_transient_db_lock_retries(*, config: TechnicalObserverConfig, max_attempts: int = 4):
    last_error: Exception | None = None
    for attempt in range(max(1, max_attempts)):
        try:
            return capture_underlying_technical_observers_once(config=config)
        except Exception as exc:  # noqa: BLE001 - classify before deciding whether to retry.
            last_error = exc
            if not is_transient_database_error(exc) or attempt >= max_attempts - 1:
                raise
            time.sleep(0.35 * (attempt + 1))
    raise last_error or RuntimeError("database lock retry failed")


if __name__ == "__main__":
    raise SystemExit(main())
