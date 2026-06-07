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

from crypto_options_app.config import CENTRAL_DB_PATH  # noqa: E402
from crypto_options_app.data_services.underlying_market_price_capture import (  # noqa: E402
    UnderlyingMarketPriceConfig,
    capture_underlying_market_prices_once,
)
from crypto_options_app.scripts._status_io import write_json_atomically  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture read-only BTC/ETH spot ticks and candles.")
    parser.add_argument("--db-path", default=str(CENTRAL_DB_PATH))
    parser.add_argument("--symbols", nargs="+", default=["BTC", "ETH"])
    parser.add_argument("--timeout-seconds", type=int, default=8)
    parser.add_argument("--candle-granularity-seconds", type=int, default=60)
    parser.add_argument("--candle-limit", type=int, default=3)
    parser.add_argument("--target-refresh-seconds", type=int, default=30)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument(
        "--state-path",
        default="crypto_options_app/artifacts/automation/underlying_market_price_capture_status.json",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = UnderlyingMarketPriceConfig(
        db_path=Path(args.db_path),
        symbols=tuple(symbol.upper() for symbol in args.symbols),
        timeout_seconds=args.timeout_seconds,
        candle_granularity_seconds=args.candle_granularity_seconds,
        candle_limit=args.candle_limit,
        target_refresh_seconds=args.target_refresh_seconds,
    )
    if args.loop:
        return _run_loop(config=config, args=args)
    summary = capture_underlying_market_prices_once(config=config)
    payload = summary.__dict__
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"status={summary.status}")
        print(f"tick_rows_inserted={summary.tick_rows_inserted}")
        print(f"candle_rows_inserted={summary.candle_rows_inserted}")
        print(f"readiness_rows_inserted={summary.readiness_rows_inserted}")
        print(f"blockers={list(summary.blockers)}")
    return 0 if summary.status in {"healthy", "degraded"} else 1


def _run_loop(*, config: UnderlyingMarketPriceConfig, args: argparse.Namespace) -> int:
    state_path = Path(args.state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    iteration = 0
    last_status = "unknown"
    while args.max_iterations is None or iteration < int(args.max_iterations):
        iteration += 1
        try:
            summary = capture_underlying_market_prices_once(config=config)
            last_status = summary.status
            payload = {
                "schema_version": "crypto_options_underlying_market_price_capture_status_v1",
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
                    f"ticks={summary.tick_rows_inserted} candles={summary.candle_rows_inserted} "
                    f"readiness={summary.readiness_rows_inserted} blockers={list(summary.blockers)}",
                    flush=True,
                )
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001
            last_status = "failed"
            payload = {
                "schema_version": "crypto_options_underlying_market_price_capture_status_v1",
                "status": "failed",
                "iteration": iteration,
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "error": f"{type(exc).__name__}:{exc}",
                "blockers": [f"{type(exc).__name__}:{exc}"],
                "orders_allowed": False,
                "live_trading_authorized": False,
                "manual_orders_avoided": True,
            }
            write_json_atomically(state_path, payload)
            print(f"iteration={iteration} status=failed error={payload['error']}", flush=True)
        time.sleep(max(1.0, float(args.interval_seconds)))
    return 0 if last_status in {"healthy", "degraded"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
