"""Janus live-strategy-worker helpers for the target Codex tools namespace."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from typing import Any

from codex_tools.janus.client import DEFAULT_API_ROOT, api_json, base_parser, exit_for_response

LIVE_STRATEGY_WORKER_STATUS_PATH = "/v1/ops/live-strategy-worker/status"
LIVE_STRATEGY_WORKER_START_PATH = "/v1/ops/live-strategy-worker/start"
LIVE_STRATEGY_WORKER_STOP_PATH = "/v1/ops/live-strategy-worker/stop"
LIVE_STRATEGY_WORKER_TICK_PATH = "/v1/ops/live-strategy-worker/tick"


def get_live_strategy_worker_status(api_root: str = DEFAULT_API_ROOT) -> dict[str, Any]:
    """Return the service-owned live strategy worker status payload."""
    return api_json(api_root, "GET", LIVE_STRATEGY_WORKER_STATUS_PATH)


def build_live_strategy_worker_start_parser(description: str) -> ArgumentParser:
    """Build the parser for the live strategy worker start command."""
    parser = base_parser(description)
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--event-id", action="append", dest="event_ids", default=[])
    parser.add_argument("--account-id", default=None)
    parser.add_argument("--source", default="janus-live-strategy-worker")
    parser.add_argument("--interval-seconds", type=float, default=None)
    parser.add_argument("--timeout-seconds", type=float, default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--live-money", action="store_true")
    parser.add_argument("--enable-llm-dispatch", action="store_true")
    parser.add_argument("--submit-candidate-strategy-plan", action="store_true")
    parser.add_argument("--max-intents", type=int, default=None)
    parser.add_argument("--orderbook-sample-count", type=int, default=None)
    parser.add_argument("--orderbook-sample-interval-sec", type=float, default=None)
    parser.add_argument("--min-size", type=float, default=None)
    parser.add_argument("--min-buy-notional-usd", type=float, default=None)
    parser.add_argument("--max-buy-notional-usd", type=float, default=None)
    parser.add_argument("--share-precision", type=int, default=None)
    parser.add_argument("--manual-target-delta-cents", type=float, default=None)
    parser.add_argument("--no-auto-protect-manual-positions", action="store_true")
    return parser


def build_live_strategy_worker_start_payload(args: Namespace) -> dict[str, Any]:
    """Return the Janus live strategy worker start payload."""
    payload: dict[str, Any] = {
        "session_date": args.session_date,
        "event_ids": args.event_ids,
        "account_id": args.account_id,
        "source": args.source,
        "interval_seconds": args.interval_seconds,
        "timeout_seconds": args.timeout_seconds,
        "execute": args.execute,
        "live_money": args.live_money,
        "enable_llm_dispatch": args.enable_llm_dispatch,
        "submit_candidate_strategy_plan": args.submit_candidate_strategy_plan,
        "max_intents": args.max_intents,
        "orderbook_sample_count": args.orderbook_sample_count,
        "orderbook_sample_interval_sec": args.orderbook_sample_interval_sec,
        "min_size": args.min_size,
        "min_buy_notional_usd": args.min_buy_notional_usd,
        "max_buy_notional_usd": args.max_buy_notional_usd,
        "share_precision": args.share_precision,
        "manual_target_delta_cents": args.manual_target_delta_cents,
    }
    if args.no_auto_protect_manual_positions:
        payload["auto_protect_manual_positions"] = False
    return {key: value for key, value in payload.items() if value is not None}


def start_live_strategy_worker(api_root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Call the Janus live strategy worker start endpoint."""
    return api_json(api_root, "POST", LIVE_STRATEGY_WORKER_START_PATH, payload)


def stop_live_strategy_worker(api_root: str = DEFAULT_API_ROOT) -> dict[str, Any]:
    """Call the Janus live strategy worker stop endpoint."""
    return api_json(api_root, "POST", LIVE_STRATEGY_WORKER_STOP_PATH, {})


def build_live_strategy_worker_tick_parser(description: str) -> ArgumentParser:
    """Build the parser for a single service-owned live strategy worker tick."""
    parser = base_parser(description)
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--event-id", action="append", dest="event_ids", default=[])
    parser.add_argument("--account-id", default=None)
    parser.add_argument("--source", default="janus-live-strategy-worker")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--live-money", action="store_true")
    parser.add_argument("--enable-llm-dispatch", action="store_true")
    parser.add_argument("--submit-candidate-strategy-plan", action="store_true")
    parser.add_argument("--max-intents", type=int, default=None)
    parser.add_argument("--orderbook-sample-count", type=int, default=None)
    parser.add_argument("--orderbook-sample-interval-sec", type=float, default=None)
    parser.add_argument("--min-size", type=float, default=None)
    parser.add_argument("--min-buy-notional-usd", type=float, default=None)
    parser.add_argument("--max-buy-notional-usd", type=float, default=None)
    parser.add_argument("--share-precision", type=int, default=None)
    parser.add_argument("--manual-target-delta-cents", type=float, default=None)
    parser.add_argument("--timeout-seconds", type=float, default=None)
    parser.add_argument("--no-auto-protect-manual-positions", action="store_true")
    return parser


def build_live_strategy_worker_tick_payload(args: Namespace) -> dict[str, Any]:
    """Return the Janus live strategy worker tick payload."""
    payload: dict[str, Any] = {
        "session_date": args.session_date,
        "event_ids": args.event_ids,
        "account_id": args.account_id,
        "source": args.source,
        "execute": args.execute,
        "live_money": args.live_money,
        "enable_llm_dispatch": args.enable_llm_dispatch,
        "submit_candidate_strategy_plan": args.submit_candidate_strategy_plan,
        "max_intents": args.max_intents,
        "orderbook_sample_count": args.orderbook_sample_count,
        "orderbook_sample_interval_sec": args.orderbook_sample_interval_sec,
        "min_size": args.min_size,
        "min_buy_notional_usd": args.min_buy_notional_usd,
        "max_buy_notional_usd": args.max_buy_notional_usd,
        "share_precision": args.share_precision,
        "manual_target_delta_cents": args.manual_target_delta_cents,
        "timeout_seconds": args.timeout_seconds,
    }
    if args.no_auto_protect_manual_positions:
        payload["auto_protect_manual_positions"] = False
    return {key: value for key, value in payload.items() if value is not None}


def run_live_strategy_worker_tick(api_root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Call the Janus live strategy worker tick endpoint."""
    return api_json(api_root, "POST", LIVE_STRATEGY_WORKER_TICK_PATH, payload)


def main_for_live_strategy_worker_status(description: str) -> None:
    """Parse status args, call the worker status endpoint, and print JSON."""
    parser = base_parser(description)
    args = parser.parse_args()
    exit_for_response(get_live_strategy_worker_status(args.api_root))


def main_for_live_strategy_worker_start(description: str) -> None:
    """Parse start args, call the worker start endpoint, and print JSON."""
    parser = build_live_strategy_worker_start_parser(description)
    args = parser.parse_args()
    exit_for_response(start_live_strategy_worker(args.api_root, build_live_strategy_worker_start_payload(args)))


def main_for_live_strategy_worker_stop(description: str) -> None:
    """Parse stop args, call the worker stop endpoint, and print JSON."""
    parser = base_parser(description)
    args = parser.parse_args()
    exit_for_response(stop_live_strategy_worker(args.api_root))


def main_for_live_strategy_worker_tick(description: str) -> None:
    """Parse tick args, call the worker tick endpoint, and print JSON."""
    parser = build_live_strategy_worker_tick_parser(description)
    args = parser.parse_args()
    exit_for_response(run_live_strategy_worker_tick(args.api_root, build_live_strategy_worker_tick_payload(args)))
