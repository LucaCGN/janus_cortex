from __future__ import annotations

"""Manage the crypto options underlying market-data store."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.config import CENTRAL_DB_PATH  # noqa: E402
from crypto_options_app.pipelines.options.market_data_store import (  # noqa: E402
    fresh_polymarket_event_universe,
    initialize_market_data_store,
    latest_indicator_snapshots,
    latest_polymarket_event_prices,
    latest_price_ticks,
    market_data_store_summary,
)
from crypto_options_app.pipelines.options.polymarket_event_price_service import (  # noqa: E402
    capture_polymarket_event_price_stream,
    discover_and_store_polymarket_event_universe,
    run_polymarket_event_price_collection_loop,
)
from crypto_options_app.pipelines.options.price_stream_service import (  # noqa: E402
    capture_binance_trade_stream,
    compute_and_store_market_indicators,
    fetch_and_store_binance_klines,
    run_market_data_collection_loop,
)
from crypto_options_app.pipelines.options.profile_store import profile_store_buying_ahead_profiles  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage read-only crypto options market data.")
    parser.add_argument("--db-path", default=str(CENTRAL_DB_PATH))
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create the market-data schema.")
    init.set_defaults(func=_cmd_init)

    summary = sub.add_parser("summary", help="Print market-data store counts.")
    summary.set_defaults(func=_cmd_summary)

    latest_prices = sub.add_parser("latest-prices", help="Print latest captured ticks.")
    latest_prices.add_argument("--symbols", nargs="+", default=["BTC", "ETH"])
    latest_prices.add_argument("--limit", type=int, default=20)
    latest_prices.set_defaults(func=_cmd_latest_prices)

    latest_indicators = sub.add_parser("latest-indicators", help="Print latest computed indicator snapshots.")
    latest_indicators.add_argument("--symbols", nargs="+", default=["BTC", "ETH"])
    latest_indicators.add_argument("--indicator-id", default=None)
    latest_indicators.add_argument("--limit", type=int, default=50)
    latest_indicators.set_defaults(func=_cmd_latest_indicators)

    event_universe = sub.add_parser("event-universe", help="Print known Polymarket crypto event tokens.")
    event_universe.add_argument("--symbols", nargs="+", default=["BTC", "ETH"])
    event_universe.add_argument("--event-slug", default=None)
    event_universe.add_argument("--include-closed", action="store_true")
    event_universe.add_argument("--limit", type=int, default=200)
    event_universe.set_defaults(func=_cmd_event_universe)

    discover_poly = sub.add_parser("discover-polymarket-events", help="Discover and store current/future crypto Polymarket event tokens.")
    discover_poly.add_argument("--symbols", nargs="+", default=["BTC", "ETH"])
    discover_poly.add_argument("--cadence-minutes", type=int, default=5)
    discover_poly.add_argument("--lookback-minutes", type=int, default=30)
    discover_poly.add_argument("--lookahead-minutes", type=int, default=180)
    discover_poly.add_argument("--profile-db-path", default=None)
    discover_poly.set_defaults(func=_cmd_discover_polymarket_events)

    capture_poly = sub.add_parser("capture-polymarket-events", help="Capture Polymarket event YES/NO prices through the public market WebSocket.")
    capture_poly.add_argument("--symbols", nargs="+", default=["BTC", "ETH"])
    capture_poly.add_argument("--seconds", type=float, default=30.0)
    capture_poly.add_argument("--cadence-minutes", type=int, default=5)
    capture_poly.add_argument("--lookback-minutes", type=int, default=30)
    capture_poly.add_argument("--lookahead-minutes", type=int, default=180)
    capture_poly.add_argument("--max-tokens", type=int, default=120)
    capture_poly.add_argument("--profile-db-path", default=None)
    capture_poly.add_argument("--book-poll-seconds", type=float, default=0.0)
    capture_poly.add_argument("--max-book-poll-tokens", type=int, default=12)
    capture_poly.set_defaults(func=_cmd_capture_polymarket_events)

    latest_event_prices = sub.add_parser("latest-event-prices", help="Print latest Polymarket event YES/NO prices.")
    latest_event_prices.add_argument("--symbols", nargs="+", default=["BTC", "ETH"])
    latest_event_prices.add_argument("--event-slug", default=None)
    latest_event_prices.add_argument("--limit", type=int, default=50)
    latest_event_prices.set_defaults(func=_cmd_latest_event_prices)

    ahead = sub.add_parser("buying-ahead-profiles", help="Print profiles detected buying before known event start.")
    ahead.add_argument("--profile-db-path", default=None)
    ahead.add_argument("--limit", type=int, default=100)
    ahead.set_defaults(func=_cmd_buying_ahead_profiles)

    stream = sub.add_parser("capture-stream", help="Capture Binance public WebSocket trade ticks.")
    stream.add_argument("--symbols", nargs="+", default=["BTC", "ETH"])
    stream.add_argument("--max-messages", type=int, default=20)
    stream.add_argument("--timeout-seconds", type=float, default=20.0)
    stream.set_defaults(func=_cmd_capture_stream)

    klines = sub.add_parser("fetch-klines", help="Fetch Binance public klines.")
    klines.add_argument("--symbols", nargs="+", default=["BTC", "ETH"])
    klines.add_argument("--interval", default="1m")
    klines.add_argument("--lookback-minutes", type=int, default=120)
    klines.set_defaults(func=_cmd_fetch_klines)

    indicators = sub.add_parser("compute-indicators", help="Compute latest local indicator snapshots.")
    indicators.add_argument("--symbols", nargs="+", default=["BTC", "ETH"])
    indicators.add_argument("--interval", default="1m")
    indicators.add_argument("--lookback-minutes", type=int, default=180)
    indicators.set_defaults(func=_cmd_compute_indicators)

    loop = sub.add_parser("run-loop", help="Run the forward collection loop. Use --max-iterations 0 for continuous mode.")
    loop.add_argument("--symbols", nargs="+", default=["BTC", "ETH"])
    loop.add_argument("--interval", default="1m")
    loop.add_argument("--kline-lookback-minutes", type=int, default=180)
    loop.add_argument("--loop-interval-seconds", type=float, default=30.0)
    loop.add_argument("--stream-max-messages", type=int, default=20)
    loop.add_argument("--stream-timeout-seconds", type=float, default=20.0)
    loop.add_argument("--max-iterations", type=int, default=1)
    loop.add_argument("--retention-days", type=int, default=90)
    loop.set_defaults(func=_cmd_run_loop)

    poly_loop = sub.add_parser("run-polymarket-loop", help="Run the Polymarket event price collection loop.")
    poly_loop.add_argument("--symbols", nargs="+", default=["BTC", "ETH"])
    poly_loop.add_argument("--capture-seconds", type=float, default=600.0)
    poly_loop.add_argument("--loop-interval-seconds", type=float, default=15.0)
    poly_loop.add_argument("--max-iterations", type=int, default=1)
    poly_loop.add_argument("--profile-db-path", default=None)
    poly_loop.set_defaults(func=_cmd_run_polymarket_loop)
    return parser


def _cmd_init(args: argparse.Namespace) -> dict[str, object]:
    path = initialize_market_data_store(args.db_path)
    return {"status": "initialized", "db_path": str(path)}


def _cmd_summary(args: argparse.Namespace) -> dict[str, object]:
    return market_data_store_summary(args.db_path)


def _cmd_latest_prices(args: argparse.Namespace) -> dict[str, object]:
    return latest_price_ticks(symbols=args.symbols, limit=args.limit, db_path=args.db_path)


def _cmd_latest_indicators(args: argparse.Namespace) -> dict[str, object]:
    return latest_indicator_snapshots(
        symbols=args.symbols,
        indicator_id=args.indicator_id,
        limit=args.limit,
        db_path=args.db_path,
    )


def _cmd_event_universe(args: argparse.Namespace) -> dict[str, object]:
    return fresh_polymarket_event_universe(
        symbols=args.symbols,
        event_slug=args.event_slug,
        include_closed=args.include_closed,
        limit=args.limit,
        db_path=args.db_path,
    )


def _cmd_discover_polymarket_events(args: argparse.Namespace) -> dict[str, object]:
    return discover_and_store_polymarket_event_universe(
        symbols=args.symbols,
        cadence_minutes=args.cadence_minutes,
        lookback_minutes=args.lookback_minutes,
        lookahead_minutes=args.lookahead_minutes,
        db_path=args.db_path,
        profile_db_path=args.profile_db_path,
    )


def _cmd_capture_polymarket_events(args: argparse.Namespace) -> dict[str, object]:
    return asyncio.run(
        capture_polymarket_event_price_stream(
            symbols=args.symbols,
            seconds=args.seconds,
            cadence_minutes=args.cadence_minutes,
            lookback_minutes=args.lookback_minutes,
            lookahead_minutes=args.lookahead_minutes,
            max_tokens=args.max_tokens,
            db_path=args.db_path,
            profile_db_path=args.profile_db_path,
            book_poll_seconds=args.book_poll_seconds,
            max_book_poll_tokens=args.max_book_poll_tokens,
        )
    )


def _cmd_latest_event_prices(args: argparse.Namespace) -> dict[str, object]:
    return latest_polymarket_event_prices(
        symbols=args.symbols,
        event_slug=args.event_slug,
        limit=args.limit,
        db_path=args.db_path,
    )


def _cmd_buying_ahead_profiles(args: argparse.Namespace) -> dict[str, object]:
    return profile_store_buying_ahead_profiles(limit=args.limit, db_path=args.profile_db_path)


def _cmd_capture_stream(args: argparse.Namespace) -> dict[str, object]:
    return asyncio.run(
        capture_binance_trade_stream(
            symbols=args.symbols,
            max_messages=args.max_messages,
            timeout_seconds=args.timeout_seconds,
            db_path=args.db_path,
        )
    )


def _cmd_fetch_klines(args: argparse.Namespace) -> dict[str, object]:
    return fetch_and_store_binance_klines(
        symbols=args.symbols,
        interval=args.interval,
        lookback_minutes=args.lookback_minutes,
        db_path=args.db_path,
    )


def _cmd_compute_indicators(args: argparse.Namespace) -> dict[str, object]:
    return compute_and_store_market_indicators(
        symbols=args.symbols,
        interval=args.interval,
        lookback_minutes=args.lookback_minutes,
        db_path=args.db_path,
    )


def _cmd_run_loop(args: argparse.Namespace) -> dict[str, object]:
    return asyncio.run(
        run_market_data_collection_loop(
            symbols=args.symbols,
            interval=args.interval,
            kline_lookback_minutes=args.kline_lookback_minutes,
            loop_interval_seconds=args.loop_interval_seconds,
            stream_max_messages=args.stream_max_messages,
            stream_timeout_seconds=args.stream_timeout_seconds,
            max_iterations=args.max_iterations,
            retention_days=args.retention_days,
            db_path=args.db_path,
        )
    )


def _cmd_run_polymarket_loop(args: argparse.Namespace) -> dict[str, object]:
    return asyncio.run(
        run_polymarket_event_price_collection_loop(
            symbols=args.symbols,
            capture_seconds=args.capture_seconds,
            loop_interval_seconds=args.loop_interval_seconds,
            max_iterations=args.max_iterations,
            db_path=args.db_path,
            profile_db_path=args.profile_db_path,
        )
    )


def main() -> int:
    args = build_parser().parse_args()
    payload = args.func(args)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
