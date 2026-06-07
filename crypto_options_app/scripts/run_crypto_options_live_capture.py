from __future__ import annotations

"""Run read-only live capture for recurring Polymarket crypto up/down markets.

This CLI only records public market data and public account snapshots. It never
places, cancels, signs, broadcasts, redeems, or routes orders.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.api.db import to_jsonable  # noqa: E402
from crypto_options_app.data_nodes.polymarket_crypto.live_capture import run_live_crypto_options_capture  # noqa: E402


def run(args: argparse.Namespace) -> dict[str, Any]:
    symbols = _symbols(args)
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir()
    status = asyncio.run(
        run_live_crypto_options_capture(
            output_dir=output_dir,
            symbols=symbols,
            seconds=float(args.seconds),
            cadence_minutes=int(args.cadence_minutes),
            lookback_minutes=int(args.lookback_minutes),
            lookahead_minutes=int(args.lookahead_minutes),
            binance_poll_seconds=float(args.binance_poll_seconds),
            book_poll_seconds=float(args.book_poll_seconds),
            max_book_poll_tokens=int(args.max_book_poll_tokens),
            account_handles=list(args.account_handle or []),
            account_snapshot_seconds=float(args.account_snapshot_seconds),
        )
    )
    return to_jsonable(
        {
            "schema_version": "crypto_options_live_capture_cli_result_v1",
            "status": status,
            "output_dir": str(output_dir),
            "symbols": symbols,
            "orders_allowed": False,
            "live_trading_authorized": False,
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run read-only live Polymarket crypto-options data capture.")
    parser.add_argument("--symbol", action="append", default=None, help="Crypto symbol to capture, repeatable. Default: BTC and ETH.")
    parser.add_argument("--symbols", default=None, help="Comma-separated crypto symbols. Combined with repeated --symbol.")
    parser.add_argument("--seconds", type=float, default=1800.0, help="Capture duration in seconds.")
    parser.add_argument("--cadence-minutes", type=int, default=5, help="Recurring up/down market cadence.")
    parser.add_argument("--lookback-minutes", type=int, default=5, help="Slug discovery lookback from session start.")
    parser.add_argument("--lookahead-minutes", type=int, default=65, help="Slug discovery lookahead from session start.")
    parser.add_argument("--binance-poll-seconds", type=float, default=1.0, help="Polling interval for public Binance ticker proxy.")
    parser.add_argument("--book-poll-seconds", type=float, default=5.0, help="Polling interval for public CLOB /book fallback. Use 0 to disable.")
    parser.add_argument("--max-book-poll-tokens", type=int, default=16, help="Maximum near-window outcome tokens to poll per /book cycle.")
    parser.add_argument("--account-handle", action="append", default=None, help="Public Polymarket handle to snapshot, repeatable.")
    parser.add_argument("--account-snapshot-seconds", type=float, default=300.0, help="Public account snapshot interval.")
    parser.add_argument("--output-dir", default=None, help="Output directory for JSONL capture artifacts.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable result.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        status = payload.get("status") or {}
        print(f"status={status.get('status')}")
        print(f"target_count={status.get('target_count')}")
        print(f"raw_message_count={status.get('raw_message_count')}")
        print(f"normalized_row_count={status.get('normalized_row_count')}")
        print(f"binance_tick_count={status.get('binance_tick_count')}")
        print(f"account_snapshot_count={status.get('account_snapshot_count')}")
        print(f"last_error={status.get('last_error')}")
        print(f"output_dir={payload.get('output_dir')}")
        print(f"live_trading_authorized={payload.get('live_trading_authorized')}")
    return 0


def _symbols(args: argparse.Namespace) -> list[str]:
    raw: list[str] = []
    if args.symbols:
        raw.extend(part.strip() for part in str(args.symbols).split(","))
    if args.symbol:
        raw.extend(str(symbol).strip() for symbol in args.symbol)
    if not raw:
        raw = ["BTC", "ETH"]
    cleaned = []
    for symbol in raw:
        upper = symbol.upper()
        if upper and upper not in cleaned:
            cleaned.append(upper)
    return cleaned


def _default_output_dir() -> Path:
    now = datetime.now(timezone.utc)
    return (
        REPO_ROOT
        / "local"
        / "shared"
        / "artifacts"
        / "crypto-options-research"
        / "live-capture"
        / now.strftime("%Y-%m-%d")
        / now.strftime("%Y%m%dT%H%M%SZ")
    )


if __name__ == "__main__":
    raise SystemExit(main())
