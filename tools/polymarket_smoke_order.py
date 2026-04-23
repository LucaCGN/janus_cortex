from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import timezone, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.databases.postgres import managed_connection
from app.data.pipelines.daily.nba.analysis.mart_game_profiles import load_analysis_bundle
from app.modules.nba.execution.adapter import (
    cancel_live_order,
    create_live_order,
    fetch_latest_orderbook_summary,
    resolve_minimum_order_size,
    resolve_trading_account,
    build_live_creds,
)
from app.modules.nba.execution.contracts import LiveRunConfig, build_live_order_metadata


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Place and optionally cancel a Polymarket smoke-test order for one NBA game outcome.")
    parser.add_argument("--game-id", required=True)
    parser.add_argument("--team-side", choices=("home", "away"), required=True)
    parser.add_argument("--run-id", default=f"smoke-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--cancel-after-seconds", type=float, default=2.0)
    parser.add_argument("--offset-cents", type=float, default=5.0, help="How far below best bid to price a non-marketable buy.")
    parser.add_argument("--marketable", action="store_true", help="Use best ask directly instead of a safe non-marketable buy.")
    return parser.parse_args()


def _resolve_bundle_or_fail(connection: Any, game_id: str) -> dict[str, Any]:
    bundle = load_analysis_bundle(connection, game_id=game_id)
    if bundle is None:
        raise RuntimeError(f"Game bundle not found: {game_id}")
    selected_market = bundle.get("selected_market") or {}
    if not selected_market.get("market_id"):
        raise RuntimeError(f"No selected Polymarket market found for game {game_id}")
    return bundle


def _resolve_outcome(bundle: dict[str, Any], team_side: str) -> dict[str, Any]:
    for item in (bundle.get("selected_market") or {}).get("series") or []:
        if str(item.get("side") or "") == team_side:
            return item
    raise RuntimeError(f"No {team_side} outcome found in selected market")


def main() -> None:
    args = _parse_args()
    dry_run = True if args.dry_run else not args.live

    with managed_connection() as connection:
        account = resolve_trading_account(connection, account_id=None)
        bundle = _resolve_bundle_or_fail(connection, args.game_id)
        outcome = _resolve_outcome(bundle, args.team_side)
        market_id = str(bundle["selected_market"]["market_id"])
        outcome_id = str(outcome["outcome_id"])
        token_id = str(outcome["token_id"])
        creds = build_live_creds(account)
        orderbook = fetch_latest_orderbook_summary(creds=creds, market_id=market_id, token_id=token_id)
        best_bid = orderbook.get("best_bid")
        best_ask = orderbook.get("best_ask")
        if best_bid is None or best_ask is None:
            raise RuntimeError("Orderbook missing best bid/ask")

        if args.marketable:
            price = float(best_ask)
            order_policy = "smoke_limit_best_ask"
        else:
            price = max(0.01, float(best_bid) - (float(args.offset_cents) / 100.0))
            order_policy = "smoke_non_marketable_limit"
        size = resolve_minimum_order_size(price)

        metadata = build_live_order_metadata(
            config=LiveRunConfig(run_id=args.run_id, game_ids=[args.game_id], dry_run=dry_run),
            controller_name="smoke_order",
            controller_source="operator_smoke_test",
            game_id=args.game_id,
            market_id=market_id,
            outcome_id=outcome_id,
            strategy_family="smoke_test",
            signal_id=f"smoke|{args.game_id}|{args.team_side}",
            signal_price=float(best_ask),
            signal_timestamp=datetime.now(timezone.utc).isoformat(),
            entry_reason="smoke_test_order_path",
            stop_price=None,
            order_policy=order_policy,
            extra={
                "team_side": args.team_side,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread_cents": orderbook.get("spread_cents"),
                "marketable": bool(args.marketable),
            },
        )

        placed = create_live_order(
            connection,
            account=account,
            market_id=market_id,
            outcome_id=outcome_id,
            token_id=token_id,
            side="buy",
            size=size,
            price=price,
            order_type="limit",
            metadata_json=metadata,
            dry_run=dry_run,
        )

        result: dict[str, Any] = {
            "mode": "dry_run" if dry_run else "live",
            "run_id": args.run_id,
            "game_id": args.game_id,
            "team_side": args.team_side,
            "market_id": market_id,
            "outcome_id": outcome_id,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "price": price,
            "size": size,
            "placed": placed,
        }

        if args.cancel_after_seconds >= 0:
            time.sleep(float(args.cancel_after_seconds))
            canceled = cancel_live_order(
                connection,
                account=account,
                order_id=str(placed["order_id"]),
                dry_run=dry_run,
                reason="smoke_test_cancel",
            )
            result["canceled"] = canceled

    print(json.dumps(result, ensure_ascii=True, indent=2, default=str))


if __name__ == "__main__":
    main()
