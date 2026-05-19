"""Command line entrypoints for gated Polymarket portfolio planning."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence, TextIO

from codex_tools.polymarket.execution_gate import PolymarketFallbackIntent
from codex_tools.polymarket.grid_service import build_grid_service_preview, build_grid_service_spawn_plan
from codex_tools.polymarket.manager import build_portfolio_manager_action_plan
from codex_tools.polymarket.preview import build_fallback_preview
from codex_tools.polymarket.settlement import (
    build_post_redeem_reconciliation,
    build_redeem_preview,
    build_settlement_readiness_report,
    write_settlement_ledger_prewrite,
)


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
        description="Build gated Polymarket portfolio decisions and service plans.",
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

    grid_spawn = subparsers.add_parser(
        "plan-grid-service-spawn",
        help="Build a non-executing gated 1c grid service spawn plan.",
    )
    grid_spawn.add_argument("--grid-preview-json", required=True, type=Path)
    grid_spawn.add_argument("--service-config-json", required=True, type=Path)
    grid_spawn.add_argument("--now-utc")
    grid_spawn.add_argument(
        "--non-dry-run-intent",
        action="store_true",
        help="Authorize the service-spawn plan when every service gate is present; still does not start it.",
    )
    grid_spawn.set_defaults(func=_plan_grid_service_spawn)

    manager_plan = subparsers.add_parser(
        "plan-manager-action",
        help="Build a required portfolio-manager action plan from account, frontend, and profile evidence.",
    )
    manager_plan.add_argument("--direct-truth-json", required=True, type=Path)
    manager_plan.add_argument("--frontend-catalog-json", type=Path)
    manager_plan.add_argument("--profile-studies-json", type=Path)
    manager_plan.add_argument("--now-utc")
    manager_plan.add_argument("--target-notional-usd", default="1")
    manager_plan.add_argument("--max-initial-shares", default="5")
    manager_plan.add_argument("--max-initial-notional-usd", default="5")
    manager_plan.add_argument("--oscillation-grid-threshold-percent", default="3")
    manager_plan.add_argument("--action-optional", action="store_true")
    manager_plan.set_defaults(func=_plan_manager_action)

    redeem = subparsers.add_parser(
        "preview-redeem",
        help="Build a non-executing resolved-market redemption preview.",
    )
    redeem.add_argument("--direct-truth-json", required=True, type=Path)
    redeem.add_argument("--position-token-id", required=True)
    redeem.add_argument("--market-resolved", action="store_true")
    redeem.add_argument("--condition-id", required=True)
    redeem.add_argument("--market-slug", required=True)
    redeem.add_argument("--winning-token-id")
    redeem.add_argument("--expected-payout-usd")
    redeem.add_argument("--issue-link")
    redeem.add_argument("--ledger-link")
    redeem.add_argument("--post-redeem-recheck-plan")
    redeem.add_argument(
        "--non-dry-run-intent",
        action="store_true",
        help="Preview a non-dry-run redemption intent without preparing, signing, or submitting.",
    )
    redeem.add_argument("--wallet-ready", action="store_true")
    redeem.add_argument("--chain-ready", action="store_true")
    redeem.add_argument("--signer-ready", action="store_true")
    redeem.add_argument("--gas-fee-ready", action="store_true")
    redeem.add_argument("--kill-switch-clear", action="store_true")
    redeem.add_argument("--ledger-available", action="store_true")
    redeem.add_argument("--janus-codex-approval", action="store_true")
    redeem.add_argument("--truth-source", action="append", default=[])
    redeem.add_argument("--now-utc")
    redeem.add_argument("--write-settlement-ledger", action="store_true")
    redeem.add_argument("--settlement-ledger-root", type=Path)
    redeem.set_defaults(func=_preview_redeem)

    reconcile_redeem = subparsers.add_parser(
        "reconcile-redeem",
        help="Build a non-executing post-redeem direct-truth reconciliation report.",
    )
    reconcile_redeem.add_argument("--redeem-preview-json", required=True, type=Path)
    reconcile_redeem.add_argument("--direct-truth-json", required=True, type=Path)
    reconcile_redeem.add_argument("--settlement-ledger-write-json", type=Path)
    reconcile_redeem.add_argument("--redemption-tx-hash")
    reconcile_redeem.add_argument("--redemption-source")
    reconcile_redeem.add_argument("--now-utc")
    reconcile_redeem.set_defaults(func=_reconcile_redeem)

    settlement_readiness = subparsers.add_parser(
        "settlement-readiness",
        help="Build a non-executing settlement residual readiness report.",
    )
    settlement_readiness.add_argument("--direct-truth-json", required=True, type=Path)
    settlement_readiness.add_argument("--event-id")
    settlement_readiness.add_argument("--now-utc")
    settlement_readiness.set_defaults(func=_settlement_readiness)
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


def _plan_grid_service_spawn(args: argparse.Namespace, output: TextIO) -> int:
    plan = build_grid_service_spawn_plan(
        _read_required_json_file(args.grid_preview_json),
        _read_required_json_file(args.service_config_json),
        now_utc=args.now_utc,
        dry_run=not args.non_dry_run_intent,
    )
    json.dump(asdict(plan), output, indent=2, sort_keys=True)
    output.write("\n")
    return 0


def _plan_manager_action(args: argparse.Namespace, output: TextIO) -> int:
    profile_payload = _read_json_file(args.profile_studies_json)
    if profile_payload is None:
        profile_studies: list[dict[str, Any]] = []
    elif isinstance(profile_payload.get("profiles"), list):
        profile_studies = [dict(item) for item in profile_payload["profiles"] if isinstance(item, dict)]
    else:
        raise ValueError("--profile-studies-json must contain a 'profiles' list")

    plan = build_portfolio_manager_action_plan(
        _read_required_json_file(args.direct_truth_json),
        frontend_catalog_snapshot=_read_json_file(args.frontend_catalog_json),
        profile_studies=profile_studies,
        now_utc=args.now_utc,
        require_action_each_run=not args.action_optional,
        target_notional_usd=args.target_notional_usd,
        max_initial_shares=args.max_initial_shares,
        max_initial_notional_usd=args.max_initial_notional_usd,
        oscillation_grid_threshold_percent=args.oscillation_grid_threshold_percent,
    )
    json.dump(asdict(plan), output, indent=2, sort_keys=True)
    output.write("\n")
    return 0


def _preview_redeem(args: argparse.Namespace, output: TextIO) -> int:
    direct_truth = _read_required_json_file(args.direct_truth_json)
    position = _select_position(direct_truth, token_id=args.position_token_id)
    preview = build_redeem_preview(
        position,
        {
            "resolved": args.market_resolved,
            "condition_id": args.condition_id,
            "market_slug": args.market_slug,
            "winning_token_id": args.winning_token_id,
            "expected_payout_usd": args.expected_payout_usd,
        },
        direct_truth,
        dry_run=not args.non_dry_run_intent,
        issue_link=args.issue_link,
        ledger_link=args.ledger_link,
        post_redeem_recheck_plan=args.post_redeem_recheck_plan,
        wallet_ready=args.wallet_ready,
        chain_ready=args.chain_ready,
        signer_ready=args.signer_ready,
        gas_fee_ready=args.gas_fee_ready,
        kill_switch_clear=args.kill_switch_clear,
        ledger_available=args.ledger_available,
        janus_codex_approval=args.janus_codex_approval,
        truth_sources=args.truth_source,
        now_utc=args.now_utc,
    )
    payload = asdict(preview)
    if args.write_settlement_ledger:
        ledger_write = write_settlement_ledger_prewrite(
            preview,
            ledger_root=args.settlement_ledger_root,
            written_at_utc=args.now_utc,
        )
        payload["settlement_ledger_write"] = asdict(ledger_write)
    else:
        payload["settlement_ledger_write"] = None
    json.dump(payload, output, indent=2, sort_keys=True)
    output.write("\n")
    return 0


def _reconcile_redeem(args: argparse.Namespace, output: TextIO) -> int:
    redemption_evidence = None
    if args.redemption_tx_hash or args.redemption_source:
        redemption_evidence = {
            "transaction_hash": args.redemption_tx_hash,
            "source": args.redemption_source,
        }
    reconciliation = build_post_redeem_reconciliation(
        _read_required_json_file(args.redeem_preview_json),
        _read_required_json_file(args.direct_truth_json),
        settlement_ledger_write=_read_json_file(args.settlement_ledger_write_json),
        redemption_evidence=redemption_evidence,
        now_utc=args.now_utc,
    )
    json.dump(asdict(reconciliation), output, indent=2, sort_keys=True)
    output.write("\n")
    return 0


def _settlement_readiness(args: argparse.Namespace, output: TextIO) -> int:
    report = build_settlement_readiness_report(
        _read_required_json_file(args.direct_truth_json),
        event_id=args.event_id,
        now_utc=args.now_utc,
        source_evidence={"cli_command": "settlement-readiness"},
    )
    json.dump(asdict(report), output, indent=2, sort_keys=True)
    output.write("\n")
    return 0


def _read_required_json_file(path: Path) -> dict[str, Any]:
    payload = _read_json_file(path)
    if payload is None:
        raise ValueError("--direct-truth-json is required")
    return payload


def _select_position(direct_truth: dict[str, Any], *, token_id: str) -> dict[str, Any]:
    for position in direct_truth.get("open_positions") or []:
        if not isinstance(position, dict):
            continue
        candidates = (
            position.get("token_id"),
            position.get("asset_id"),
            position.get("asset"),
            position.get("outcomeTokenId"),
            position.get("clobTokenId"),
        )
        if any(str(candidate or "").strip() == token_id for candidate in candidates):
            return position
    raise ValueError(f"position token not found in direct truth snapshot: {token_id}")


def main(argv: Sequence[str] | None = None, output: TextIO | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args, output or sys.stdout))


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
