"""Janus reconciliation helpers for Codex automation."""

from __future__ import annotations

import json
from argparse import ArgumentParser, Namespace
from typing import Any

from codex_tools.janus.client import api_json, base_parser, exit_for_response

OPERATOR_INTERVENTION_RECONCILE_PATH = "/v1/operator/interventions/reconcile"
TRADE_RECONCILIATION_PATH = "/v1/portfolio/trades/reconciliation"


def build_order_reconciliation_parser(description: str) -> ArgumentParser:
    parser = base_parser(description)
    parser.add_argument("--account-id", default=None)
    parser.add_argument("--event-id", default=None)
    parser.add_argument("--market-id", default=None)
    parser.add_argument("--action", default="scan")
    parser.add_argument("--external-order-id", action="append", dest="external_order_ids", default=[])
    parser.add_argument("--external-trade-id", action="append", dest="external_trade_ids", default=[])
    parser.add_argument("--strategy-family", default=None)
    parser.add_argument("--manual-reason", default=None)
    parser.add_argument("--target-status", default=None)
    parser.add_argument("--stop-status", default=None)
    parser.add_argument("--hedge-status", default=None)
    parser.add_argument("--protective-order-status", default=None)
    parser.add_argument("--expected-close-path", default=None)
    parser.add_argument("--final-pnl-usd", type=float, default=None)
    parser.add_argument("--metadata-json", default=None)
    parser.add_argument("--notes", default=None)
    return parser


def build_order_reconciliation_payload(args: Namespace) -> dict[str, Any]:
    metadata = json.loads(args.metadata_json) if args.metadata_json else {}
    return {
        "account_id": args.account_id,
        "event_id": args.event_id,
        "market_id": args.market_id,
        "action": args.action,
        "external_order_ids": args.external_order_ids,
        "external_trade_ids": args.external_trade_ids,
        "strategy_family": args.strategy_family,
        "manual_reason": args.manual_reason,
        "target_status": args.target_status,
        "stop_status": args.stop_status,
        "hedge_status": args.hedge_status,
        "protective_order_status": args.protective_order_status,
        "expected_close_path": args.expected_close_path,
        "final_pnl_usd": args.final_pnl_usd,
        "metadata": metadata,
        "notes": args.notes,
    }


def reconcile_operator_interventions(api_root: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Ask Janus to reconcile operator/manual order interventions."""
    return api_json(api_root, "POST", OPERATOR_INTERVENTION_RECONCILE_PATH, payload)


def build_trade_reconciliation_parser(description: str) -> ArgumentParser:
    parser = base_parser(description)
    parser.add_argument("--account-id", default=None)
    parser.add_argument("--market-id", default=None)
    parser.add_argument("--outcome-id", default=None)
    parser.add_argument("--event-slug", default=None)
    parser.add_argument("--start-time", default=None)
    parser.add_argument("--end-time", default=None)
    parser.add_argument("--limit", type=int, default=5000)
    return parser


def build_trade_reconciliation_query(args: Namespace) -> dict[str, Any]:
    return {
        "account_id": args.account_id,
        "market_id": args.market_id,
        "outcome_id": args.outcome_id,
        "event_slug": args.event_slug,
        "start_time": args.start_time,
        "end_time": args.end_time,
        "limit": args.limit,
    }


def get_trade_reconciliation(api_root: str, query: dict[str, Any]) -> dict[str, Any]:
    """Build a non-destructive duplicate-fill reconciliation report."""
    return api_json(api_root, "GET", TRADE_RECONCILIATION_PATH, query=query)


def main_for_order_reconciliation(description: str) -> None:
    args = build_order_reconciliation_parser(description).parse_args()
    exit_for_response(reconcile_operator_interventions(args.api_root, build_order_reconciliation_payload(args)))


def main_for_trade_reconciliation(description: str) -> None:
    args = build_trade_reconciliation_parser(description).parse_args()
    exit_for_response(get_trade_reconciliation(args.api_root, build_trade_reconciliation_query(args)))
