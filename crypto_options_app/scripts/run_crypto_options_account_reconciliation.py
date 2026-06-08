from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from crypto_options_app.config import CENTRAL_DB_PATH
from crypto_options_app.db.postgres_connection import PostgresCompatConnection
from crypto_options_app.trading.account_reconciliation import (
    account_position_settlements_from_local_event_resolution,
    account_position_settlements_from_portfolio,
    apply_account_position_settlements,
    cancel_underpriced_paired_exit_orders,
    reconcile_pending_exchange_orders,
)
from crypto_options_app.trading.polymarket_portfolio import (
    PolymarketCredentials,
    cancel_order,
    view_closed_positions,
    view_orders,
    view_open_positions,
    view_trades,
)
from crypto_options_app.reports.settlement_performance import (
    fetch_gamma_event_by_slug,
    resolve_outcome_from_gamma_payload,
)


DEFAULT_REPORT_JSON = Path("crypto_options_app/artifacts/reports/account_reconciliation_latest.json")
DEFAULT_REPORT_MD = Path("crypto_options_app/artifacts/reports/account_reconciliation_latest.md")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read Polymarket Data API positions and write account-authoritative PnL into validation ledgers."
    )
    parser.add_argument("--db-path", type=Path, default=CENTRAL_DB_PATH)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--min-open-size", type=float, default=0.0)
    parser.add_argument("--default-max-loss-usd", type=float, default=10.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    creds = PolymarketCredentials.from_env()
    if not creds.wallet_address:
        payload = {
            "schema_version": "crypto_options_account_reconciliation_run_v1",
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "status": "blocked",
            "blockers": ["missing_polymarket_wallet_address"],
            "dry_run": bool(args.dry_run),
        }
        _write_reports(payload, args.report_json, args.report_md)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    open_positions = view_open_positions(creds, min_size=float(args.min_open_size))
    closed_positions = view_closed_positions(creds)
    open_orders = view_orders(creds, open_only=True)
    trades = view_trades(creds)
    with PostgresCompatConnection(readonly=bool(args.dry_run)) as conn:
        portfolio_settlements = account_position_settlements_from_portfolio(
            conn,
            open_positions=open_positions,
            closed_positions=closed_positions,
            now_dt=datetime.now(UTC),
        )
        portfolio_keys = {
            (settlement.strategy_id, settlement.event_token_key)
            for settlement in portfolio_settlements
        }
        fallback_settlements = [
            settlement
            for settlement in account_position_settlements_from_local_event_resolution(
                conn,
                event_outcome_resolver=_resolve_outcome_from_gamma_slug,
                now_dt=datetime.now(UTC),
            )
            if (settlement.strategy_id, settlement.event_token_key) not in portfolio_keys
        ]
        settlements = [*portfolio_settlements, *fallback_settlements]
        writeback = (
            {
                "schema_version": "crypto_options_account_position_settlement_writeback_v1",
                "settlement_count": len(settlements),
                "updated_ledger_count": 0,
                "closed_position_count": 0,
                "missing_ledger_count": 0,
                "missing_ledgers": [],
                "dry_run": True,
            }
            if args.dry_run
            else apply_account_position_settlements(
                conn,
                settlements,
                default_max_loss_usd=float(args.default_max_loss_usd),
            )
        )
        pending_orders = (
            {
                "schema_version": "crypto_options_pending_exchange_order_reconciliation_v1",
                "checked_order_count": 0,
                "still_open_order_count": 0,
                "filled_order_count": 0,
                "expired_order_count": 0,
                "updated_ledger_count": 0,
                "closed_position_count": 0,
                "partial_position_count": 0,
                "dry_run": True,
            }
            if args.dry_run
            else _reconcile_pending_orders_with_safety(
                conn,
                creds=creds,
                open_orders=open_orders,
                trades=trades,
                default_max_loss_usd=float(args.default_max_loss_usd),
            )
        )
    payload = {
        "schema_version": "crypto_options_account_reconciliation_run_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "status": "completed",
        "wallet_address": creds.wallet_address,
        "dry_run": bool(args.dry_run),
        "open_position_count": len(open_positions),
        "closed_position_count": len(closed_positions),
        "open_order_count": len(open_orders),
        "trade_count": len(trades),
        "mapped_settlement_count": len(settlements),
        "portfolio_mapped_settlement_count": len(portfolio_settlements),
        "local_event_resolution_settlement_count": len(fallback_settlements),
        "writeback": writeback,
        "pending_order_reconciliation": pending_orders,
        "manual_orders_avoided": True,
    }
    _write_reports(payload, args.report_json, args.report_md)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _write_reports(payload: dict, report_json: Path, report_md: Path) -> None:
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    report_md.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Account Reconciliation",
        "",
        f"Generated: `{payload.get('generated_at_utc')}`",
        f"Status: `{payload.get('status')}`",
        f"Dry run: `{payload.get('dry_run')}`",
        f"Open positions fetched: `{payload.get('open_position_count', 0)}`",
        f"Closed positions fetched: `{payload.get('closed_position_count', 0)}`",
        f"Open orders fetched: `{payload.get('open_order_count', 0)}`",
        f"Trades fetched: `{payload.get('trade_count', 0)}`",
        f"Mapped settlements: `{payload.get('mapped_settlement_count', 0)}`",
        f"Manual orders avoided: `{payload.get('manual_orders_avoided', True)}`",
    ]
    writeback = payload.get("writeback") if isinstance(payload.get("writeback"), dict) else {}
    if writeback:
        lines.extend(
            [
                "",
                "## Writeback",
                "",
                f"- Updated ledgers: `{writeback.get('updated_ledger_count')}`",
                f"- Closed local positions: `{writeback.get('closed_position_count')}`",
                f"- Missing ledgers: `{writeback.get('missing_ledger_count')}`",
            ]
        )
    pending_orders = (
        payload.get("pending_order_reconciliation")
        if isinstance(payload.get("pending_order_reconciliation"), dict)
        else {}
    )
    if pending_orders:
        lines.extend(
            [
                "",
                "## Pending Exchange Orders",
                "",
                f"- Checked orders: `{pending_orders.get('checked_order_count')}`",
                f"- Still open: `{pending_orders.get('still_open_order_count')}`",
                f"- Filled from trade history: `{pending_orders.get('filled_order_count')}`",
                f"- Expired/cancelled without fill: `{pending_orders.get('expired_order_count')}`",
                f"- Updated ledgers: `{pending_orders.get('updated_ledger_count')}`",
                f"- Closed positions: `{pending_orders.get('closed_position_count')}`",
            ]
        )
    blockers = payload.get("blockers") or []
    if blockers:
        lines.extend(["", "## Blockers", ""])
        lines.extend(f"- `{blocker}`" for blocker in blockers)
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _reconcile_pending_orders_with_safety(
    conn,
    *,
    creds: PolymarketCredentials,
    open_orders: list,
    trades: list,
    default_max_loss_usd: float,
) -> dict:
    paired_exit_safety = cancel_underpriced_paired_exit_orders(
        conn,
        open_orders=open_orders,
        cancel_order_fn=lambda order_id: cancel_order(creds, order_id),
    )
    if int(paired_exit_safety.get("cancelled_order_count") or 0) > 0:
        open_orders = view_orders(creds, open_only=True)
    pending = reconcile_pending_exchange_orders(
        conn,
        open_orders=open_orders,
        trades=trades,
        default_max_loss_usd=float(default_max_loss_usd),
    )
    return {
        **pending,
        "paired_exit_safety": paired_exit_safety,
    }


def _resolve_outcome_from_gamma_slug(event_slug: str) -> str | None:
    return resolve_outcome_from_gamma_payload(fetch_gamma_event_by_slug(event_slug))


if __name__ == "__main__":
    main()
