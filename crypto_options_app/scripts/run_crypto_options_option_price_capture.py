from __future__ import annotations

"""Run the read-only Polymarket option price-path capture service."""

import argparse
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.config import CENTRAL_DB_PATH  # noqa: E402
from crypto_options_app.data_services.polymarket_option_price_capture import (  # noqa: E402
    OptionPriceCaptureConfig,
    capture_option_price_paths_once_sync,
)
from crypto_options_app.scripts._status_io import write_json_atomically  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture read-only Polymarket Up/Down option price paths.")
    parser.add_argument("--db-path", default=str(CENTRAL_DB_PATH))
    parser.add_argument("--symbols", nargs="+", default=["BTC", "ETH"])
    parser.add_argument("--cadence-minutes", type=int, default=5)
    parser.add_argument("--lookback-minutes", type=int, default=0)
    parser.add_argument("--lookahead-minutes", type=int, default=15)
    parser.add_argument("--max-tokens", type=int, default=120)
    parser.add_argument("--max-concurrency", type=int, default=8)
    parser.add_argument("--max-book-depth", type=int, default=10)
    parser.add_argument("--loop", action="store_true", help="Run continuously until interrupted.")
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument("--state-path", default="crypto_options_app/artifacts/automation/option_price_capture_status.json")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = OptionPriceCaptureConfig(
        db_path=Path(args.db_path),
        symbols=tuple(symbol.upper() for symbol in args.symbols),
        cadence_minutes=args.cadence_minutes,
        lookback_minutes=args.lookback_minutes,
        lookahead_minutes=args.lookahead_minutes,
        max_tokens=args.max_tokens,
        max_concurrency=args.max_concurrency,
        max_book_depth=args.max_book_depth,
    )
    if args.loop:
        return _run_loop(config=config, args=args)
    summary = capture_option_price_paths_once_sync(config=config)
    payload = summary.__dict__
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"status={summary.status}")
        print(f"db_path={summary.db_path}")
        print(f"target_count={summary.target_count}")
        print(f"tick_rows_inserted={summary.tick_rows_inserted}")
        print(f"book_level_rows_inserted={summary.book_level_rows_inserted}")
        print(f"pair_snapshot_rows_inserted={summary.pair_snapshot_rows_inserted}")
        print(f"blockers={list(summary.blockers)}")
    return 0 if summary.status in {"healthy", "degraded"} else 1


def _run_loop(*, config: OptionPriceCaptureConfig, args: argparse.Namespace) -> int:
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
                "schema_version": "crypto_options_option_price_capture_status_v1",
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
                    f"targets={summary.target_count} ticks={summary.tick_rows_inserted} "
                    f"pairs={summary.pair_snapshot_rows_inserted} blockers={list(summary.blockers)}",
                    flush=True,
                )
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - long-running data service must retry transient locks/network errors.
            last_status = "degraded"
            status = "degraded" if _is_transient_db_lock_error(exc) else "failed"
            payload = {
                "schema_version": "crypto_options_option_price_capture_status_v1",
                "status": status,
                "iteration": iteration,
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "error": f"{type(exc).__name__}:{exc}",
                "blockers": ["transient_db_lock_retry_exhausted"] if _is_transient_db_lock_error(exc) else [f"{type(exc).__name__}:{exc}"],
                "orders_allowed": False,
                "live_trading_authorized": False,
                "manual_orders_avoided": True,
            }
            write_json_atomically(state_path, payload)
            print(f"iteration={iteration} status={status} error={payload['error']}", flush=True)
        time.sleep(max(0.25, float(args.interval_seconds)))
    return 0 if last_status in {"healthy", "degraded"} else 1


def _capture_with_transient_db_lock_retries(*, config: OptionPriceCaptureConfig, max_attempts: int = 4):
    last_error: Exception | None = None
    for attempt in range(max(1, max_attempts)):
        try:
            return capture_option_price_paths_once_sync(config=config)
        except Exception as exc:  # noqa: BLE001 - classify before deciding whether to retry.
            last_error = exc
            if not _is_transient_db_lock_error(exc) or attempt >= max_attempts - 1:
                raise
            time.sleep(0.35 * (attempt + 1))
    raise last_error or sqlite3.OperationalError("sqlite lock retry failed")


def _is_transient_db_lock_error(exc: Exception) -> bool:
    if isinstance(exc, sqlite3.OperationalError):
        text = str(exc).lower()
        return "locked" in text or "busy" in text
    text = f"{type(exc).__name__}:{exc}".lower()
    return (
        ("operationalerror" in text and ("locked" in text or "busy" in text))
        or "deadlockdetected" in text
        or "deadlock detected" in text
        or "locknotavailable" in text
        or "could not obtain lock" in text
        or "canceling statement due to statement timeout" in text
        or "lock timeout" in text
        or "serializationfailure" in text
    )


if __name__ == "__main__":
    raise SystemExit(main())
