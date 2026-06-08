"""Janus live-strategy-worker helpers for the target Codex tools namespace."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from codex_tools.janus.client import DEFAULT_API_ROOT, api_json, base_parser, exit_for_response

LIVE_STRATEGY_WORKER_STATUS_PATH = "/v1/ops/live-strategy-worker/status"
LIVE_STRATEGY_WORKER_START_PATH = "/v1/ops/live-strategy-worker/start"
LIVE_STRATEGY_WORKER_STOP_PATH = "/v1/ops/live-strategy-worker/stop"
LIVE_STRATEGY_WORKER_TICK_PATH = "/v1/ops/live-strategy-worker/tick"
LIVE_STRATEGY_WORKER_RESTART_READBACK_SCHEMA = "live_strategy_worker_restart_readback_v1"
LIVE_STRATEGY_WORKER_RESTART_READBACK_CONTRACT_SCHEMA = "live_strategy_worker_restart_readback_contract_v1"


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
    parser.add_argument("--order-sizing-mode", default=None)
    parser.add_argument("--max-buy-notional-usd", type=float, default=None)
    parser.add_argument("--max-order-buy-notional-usd", type=float, default=None)
    parser.add_argument("--event-cap-usd", type=float, default=None)
    parser.add_argument("--side-budget-mode", default=None)
    parser.add_argument("--max-same-side-exposure-pct", type=float, default=None)
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
        "order_sizing_mode": args.order_sizing_mode,
        "max_buy_notional_usd": args.max_buy_notional_usd,
        "max_order_buy_notional_usd": args.max_order_buy_notional_usd,
        "event_cap_usd": args.event_cap_usd,
        "side_budget_mode": args.side_budget_mode,
        "max_same_side_exposure_pct": args.max_same_side_exposure_pct,
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
    parser.add_argument("--order-sizing-mode", default=None)
    parser.add_argument("--max-buy-notional-usd", type=float, default=None)
    parser.add_argument("--max-order-buy-notional-usd", type=float, default=None)
    parser.add_argument("--event-cap-usd", type=float, default=None)
    parser.add_argument("--side-budget-mode", default=None)
    parser.add_argument("--max-same-side-exposure-pct", type=float, default=None)
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
        "order_sizing_mode": args.order_sizing_mode,
        "max_buy_notional_usd": args.max_buy_notional_usd,
        "max_order_buy_notional_usd": args.max_order_buy_notional_usd,
        "event_cap_usd": args.event_cap_usd,
        "side_budget_mode": args.side_budget_mode,
        "max_same_side_exposure_pct": args.max_same_side_exposure_pct,
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


def build_live_strategy_worker_restart_readback(
    status: dict[str, Any],
    *,
    api_root: str = DEFAULT_API_ROOT,
    session_date: str | None = None,
    expected_event_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build a read-only worker restart/fail-closed readback artifact."""
    config = status.get("config") if isinstance(status.get("config"), dict) else {}
    config_trust = status.get("config_trust") if isinstance(status.get("config_trust"), dict) else {}
    contract = config_trust.get("schema_contract") if isinstance(config_trust.get("schema_contract"), dict) else {}
    heartbeat = status.get("latest_heartbeat") if isinstance(status.get("latest_heartbeat"), dict) else {}
    event_ids = [str(item) for item in (expected_event_ids or []) if item]
    effective_event_ids = [str(item) for item in config_trust.get("effective_event_ids") or config.get("event_ids") or [] if item]
    endpoint_has_config_trust = bool(config_trust)
    endpoint_has_contract = contract.get("schema_version") == "live_strategy_worker_config_trust_contract_v1"
    worker_running = bool(status.get("worker_thread_alive")) or str(status.get("status") or "") == "running"
    restart_allowed = bool(config_trust.get("restart_allowed"))
    start_allowed = bool(config_trust.get("start_allowed"))
    issues: list[str] = []
    if status.get("ok") is False:
        issues.append("worker_status_endpoint_failed")
    if not endpoint_has_config_trust:
        issues.append("config_trust_missing_from_running_api")
    if endpoint_has_config_trust and not endpoint_has_contract:
        issues.append("config_trust_contract_missing_from_running_api")
    if worker_running:
        issues.append("worker_running_readback_not_a_stopped_restart_probe")
    if restart_allowed and not worker_running:
        issues.append("stopped_worker_restart_allowed")
    if event_ids and sorted(event_ids) != sorted(effective_event_ids):
        issues.append("expected_event_scope_mismatch")
    if issues:
        gate = "YELLOW" if not worker_running else "RED"
    else:
        gate = "GREEN"
    return {
        "schema_version": LIVE_STRATEGY_WORKER_RESTART_READBACK_SCHEMA,
        "schema_contract": {
            "schema_version": LIVE_STRATEGY_WORKER_RESTART_READBACK_CONTRACT_SCHEMA,
            "source_endpoint": LIVE_STRATEGY_WORKER_STATUS_PATH,
            "required_sections": [
                "status_endpoint_has_config_trust",
                "status_endpoint_has_config_trust_contract",
                "worker_status",
                "worker_thread_alive",
                "config_trust_status",
                "start_allowed",
                "restart_allowed",
                "stale_config_reason",
                "effective_event_ids",
                "issues",
                "gate",
                "execution_authority",
            ],
            "execution_authority": False,
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "api_root": api_root,
        "session_date": session_date,
        "expected_event_ids": event_ids,
        "status_endpoint_has_config_trust": endpoint_has_config_trust,
        "status_endpoint_has_config_trust_contract": endpoint_has_contract,
        "worker_status": status.get("status"),
        "worker_thread_alive": bool(status.get("worker_thread_alive")),
        "config_trust_status": config_trust.get("config_trust_status"),
        "start_allowed": start_allowed,
        "restart_allowed": restart_allowed,
        "stale_config_reason": config_trust.get("stale_config_reason"),
        "effective_event_ids": effective_event_ids,
        "config_session_date": config.get("session_date"),
        "config_source": config.get("source"),
        "latest_heartbeat_event_ids": heartbeat.get("event_ids") or [],
        "latest_heartbeat_session_date": heartbeat.get("session_date"),
        "gate": gate,
        "issues": issues,
        "worker_status_compact": {
            "status": status.get("status"),
            "worker_thread_alive": bool(status.get("worker_thread_alive")),
            "tick_count": status.get("tick_count"),
            "consecutive_failures": status.get("consecutive_failures"),
            "last_error": status.get("last_error"),
        },
        "source_status": status,
        "order_endpoint_call_allowed": False,
        "worker_start_allowed_by_this_artifact": False,
        "execution_authority": False,
    }


def write_live_strategy_worker_restart_readback_artifact(
    payload: dict[str, Any],
    *,
    root: Path,
) -> dict[str, Any]:
    """Persist readback JSON plus a current pointer without mutating worker state."""
    root.mkdir(parents=True, exist_ok=True)
    compact = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = root / f"live_worker_restart_readback_{compact}.json"
    current_path = root / "current.json"
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    path.write_text(text, encoding="utf-8")
    current_path.write_text(text, encoding="utf-8")
    return {"path": str(path), "current_path": str(current_path)}


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
