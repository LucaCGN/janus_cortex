"""Command line entrypoints for preview-only Polymarket fallback planning."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence, TextIO

from codex_tools.polymarket.execution_gate import PolymarketFallbackIntent
from codex_tools.polymarket.grid_service import build_grid_service_preview
from codex_tools.polymarket.preview import build_fallback_preview


def _read_json_file(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("--direct-truth-json must contain a JSON object")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build preview-only Polymarket fallback decisions.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview = subparsers.add_parser(
        "preview-fallback",
        help="Build a non-executing fallback decision preview.",
    )
    preview.add_argument("--action", required=True)
    preview.add_argument("--account-id", required=True)
    preview.add_argument("--market-slug", required=True)
    preview.add_argument("--token-id", required=True)
    preview.add_argument("--side", required=True)
    preview.add_argument("--price", required=True)
    preview.add_argument("--size", required=True)
    preview.add_argument("--reason", required=True)
    preview.add_argument(
        "--non-dry-run-intent",
        action="store_true",
        help="Preview a non-dry-run intent without preparing or submitting an order.",
    )
    preview.add_argument("--idempotency-key")
    preview.add_argument("--direct-truth-json", type=Path)
    preview.add_argument("--now-utc")
    preview.add_argument("--direct-truth-max-age-seconds", type=float, default=300.0)
    preview.add_argument("--risk-budget-name")
    preview.add_argument("--risk-budget-max-notional-usd")
    preview.add_argument("--risk-budget-used-notional-usd", default="0")
    preview.add_argument("--min-size", type=float, default=5.0)
    preview.add_argument("--min-buy-notional-usd", type=float, default=1.0)
    preview.add_argument("--market-order-exception-approved", action="store_true")
    preview.add_argument("--kill-switch-clear", action="store_true")
    preview.add_argument("--kill-switch-source")
    preview.add_argument("--kill-switch-blocked-reason", action="append", default=[])
    preview.add_argument("--target-stop-rebuy-policy-json")
    preview.add_argument("--janus-degraded-or-direct-path-selected", action="store_true")
    preview.add_argument("--ledger-available", action="store_true")
    preview.add_argument("--reconciliation-plan")
    preview.add_argument("--explicit-execution-approval", action="store_true")
    preview.add_argument("--truth-source", action="append", default=[])
    preview.add_argument("--write-ledger", action="store_true")
    preview.add_argument("--ledger-root", type=Path)
    preview.set_defaults(func=_preview_fallback)

    grid = subparsers.add_parser(
        "preview-grid-service",
        help="Build a non-executing 1c grid service preview from a direct account snapshot.",
    )
    grid.add_argument("--direct-truth-json", required=True, type=Path)
    grid.add_argument("--now-utc")
    grid.add_argument("--min-abs-pnl-percent", default="5")
    grid.add_argument("--grid-step-cents", type=int, default=1)
    grid.add_argument("--include-other-basketball", action="store_true", default=True)
    grid.add_argument("--include-covered-basketball", action="store_true")
    grid.set_defaults(func=_preview_grid_service)
    return parser


def _preview_fallback(args: argparse.Namespace, output: TextIO) -> int:
    intent = PolymarketFallbackIntent(
        action=args.action,
        account_id=args.account_id,
        market_slug=args.market_slug,
        token_id=args.token_id,
        side=args.side,
        price=args.price,
        size=args.size,
        reason=args.reason,
        dry_run=not args.non_dry_run_intent,
        idempotency_key=args.idempotency_key,
    )
    preview = build_fallback_preview(
        intent,
        direct_truth_snapshot=_read_json_file(args.direct_truth_json),
        now_utc=args.now_utc,
        direct_truth_max_age_seconds=args.direct_truth_max_age_seconds,
        risk_budget_name=args.risk_budget_name,
        risk_budget_max_notional_usd=args.risk_budget_max_notional_usd,
        risk_budget_used_notional_usd=args.risk_budget_used_notional_usd,
        min_size=args.min_size,
        min_buy_notional_usd=args.min_buy_notional_usd,
        market_order_exception_approved=args.market_order_exception_approved,
        kill_switch_clear=args.kill_switch_clear,
        kill_switch_source=args.kill_switch_source,
        kill_switch_blocked_reasons=args.kill_switch_blocked_reason,
        target_stop_rebuy_policy=_read_json_text(args.target_stop_rebuy_policy_json),
        janus_degraded_or_direct_path_selected=args.janus_degraded_or_direct_path_selected,
        ledger_available=args.ledger_available,
        reconciliation_plan=args.reconciliation_plan,
        explicit_execution_approval=args.explicit_execution_approval,
        truth_sources=args.truth_source,
        write_ledger=args.write_ledger,
        ledger_root=args.ledger_root,
    )
    json.dump(asdict(preview), output, indent=2, sort_keys=True)
    output.write("\n")
    return 0


def _read_json_text(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("--target-stop-rebuy-policy-json must contain a JSON object")
    return payload


def _preview_grid_service(args: argparse.Namespace, output: TextIO) -> int:
    preview = build_grid_service_preview(
        _read_required_json_file(args.direct_truth_json),
        now_utc=args.now_utc,
        min_abs_pnl_percent=args.min_abs_pnl_percent,
        grid_step_cents=args.grid_step_cents,
        include_other_basketball=args.include_other_basketball,
        include_covered_basketball=args.include_covered_basketball,
    )
    json.dump(asdict(preview), output, indent=2, sort_keys=True)
    output.write("\n")
    return 0


def _read_required_json_file(path: Path) -> dict[str, Any]:
    payload = _read_json_file(path)
    if payload is None:
        raise ValueError("--direct-truth-json is required")
    return payload


def main(argv: Sequence[str] | None = None, output: TextIO | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args, output or sys.stdout))


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
