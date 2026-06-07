from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.config import CENTRAL_ACTIVE_PROFILE_POOL, CENTRAL_ARTIFACT_ROOT, CENTRAL_DB_PATH  # noqa: E402
from crypto_options_app.workers.signal_live_runner import (  # noqa: E402
    FIRST_SIX_SIGNAL_STRATEGY_IDS,
    SignalLiveRunConfig,
    run_signal_live_test,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run guarded signal-driven Crypto Options App live structural validation.")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--db-path", default=str(CENTRAL_DB_PATH))
    parser.add_argument("--artifact-root", default=str(CENTRAL_ARTIFACT_ROOT))
    parser.add_argument("--strategy-ids", nargs="+", default=None)
    parser.add_argument("--max-event-cycles", type=int, default=2)
    parser.add_argument("--max-cycles-per-event-slug", type=int, default=1)
    parser.add_argument("--min-repeat-event-seconds", type=float, default=20.0)
    parser.add_argument("--total-budget-cap-usd", type=float, default=15.0)
    parser.add_argument("--max-wall-seconds", type=float, default=1200.0)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--monitor-interval-seconds", type=float, default=60.0)
    parser.add_argument("--min-seconds-remaining", type=float, default=75.0)
    parser.add_argument("--symbols", nargs="+", default=["BTC", "ETH"])
    parser.add_argument("--active-profile-pool", default=str(CENTRAL_ACTIVE_PROFILE_POOL))
    parser.add_argument("--profile-report-cache", default="local/co4/profile-store-full-20260602T170705Z/crypto_options_profile_signal_report_20260602T171438Z.json")
    parser.add_argument("--active-profile-pool-limit", type=int, default=120)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--page-limit", type=int, default=120)
    parser.add_argument("--operator", default="codex-automation")
    parser.add_argument("--reason", default="Signal-driven live structural validation")
    parser.add_argument("--per-strategy-budget-cap-usd", type=float, default=None)
    parser.add_argument("--enable-lane-stop-gates", action="store_true")
    parser.add_argument("--lane-loss-streak-limit", type=int, default=3)
    parser.add_argument("--lane-stop-min-settled", type=int, default=3)
    parser.add_argument("--lane-stop-max-win-rate", type=float, default=0.50)
    parser.add_argument("--lane-stop-max-pnl-usd", type=float, default=0.0)
    parser.add_argument("--target-filled-event-count", type=int, default=None)
    parser.add_argument("--target-filled-events-per-strategy", type=int, default=None)
    parser.add_argument("--lane-stop-min-realized-pnl-usd", type=float, default=None)
    parser.add_argument("--lane-stop-min-win-rate", type=float, default=None)
    parser.add_argument(
        "--strategy-order-notional-usd",
        action="append",
        default=[],
        metavar="STRATEGY=USD",
        help="Per-strategy target order notional for evidence runs, e.g. profile_hedge_scalping_v3=2.5.",
    )
    parser.add_argument("--prevent-duplicate-event-tokens", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    run_id = args.run_id or f"signal-live-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    result = run_signal_live_test(
        SignalLiveRunConfig(
            run_id=run_id,
            strategy_ids=tuple(args.strategy_ids) if args.strategy_ids else FIRST_SIX_SIGNAL_STRATEGY_IDS,
            max_event_cycles=args.max_event_cycles,
            max_cycles_per_event_slug=args.max_cycles_per_event_slug,
            min_repeat_event_seconds=args.min_repeat_event_seconds,
            total_budget_cap_usd=args.total_budget_cap_usd,
            max_wall_seconds=args.max_wall_seconds,
            poll_seconds=args.poll_seconds,
            monitor_interval_seconds=args.monitor_interval_seconds,
            min_seconds_remaining=args.min_seconds_remaining,
            symbols=tuple(symbol.upper() for symbol in args.symbols),
            db_path=Path(args.db_path),
            artifact_root=Path(args.artifact_root),
            active_profile_pool=Path(args.active_profile_pool),
            profile_report_cache=Path(args.profile_report_cache),
            active_profile_pool_limit=args.active_profile_pool_limit,
            max_workers=args.max_workers,
            page_limit=args.page_limit,
            operator=args.operator,
            reason=args.reason,
            per_strategy_budget_cap_usd=args.per_strategy_budget_cap_usd,
            enable_lane_stop_gates=args.enable_lane_stop_gates,
            lane_loss_streak_limit=args.lane_loss_streak_limit,
            lane_stop_min_settled=args.lane_stop_min_settled,
            lane_stop_max_win_rate=args.lane_stop_max_win_rate,
            lane_stop_max_pnl_usd=args.lane_stop_max_pnl_usd,
            target_filled_event_count=args.target_filled_event_count,
            target_filled_events_per_strategy=args.target_filled_events_per_strategy,
            lane_stop_min_realized_pnl_usd=args.lane_stop_min_realized_pnl_usd,
            lane_stop_min_win_rate=args.lane_stop_min_win_rate,
            strategy_order_notional_usd=_parse_strategy_notional_overrides(args.strategy_order_notional_usd),
            prevent_duplicate_event_tokens=bool(args.prevent_duplicate_event_tokens),
        )
    )
    payload = {
        "run_id": result.run_id,
        "status": result.status,
        "generated_at_utc": result.generated_at_utc,
        "event_results": [item.__dict__ for item in result.event_results],
        "estimated_spent_usd": result.estimated_spent_usd,
        "blockers": list(result.blockers),
        "artifact_json": result.artifact_json,
        "health_snapshot_json": result.health_snapshot_json,
        "order_audit_json": result.order_audit_json,
        "manual_orders_avoided": result.manual_orders_avoided,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"run_id={result.run_id}")
        print(f"status={result.status}")
        print(f"event_count={len(result.event_results)}")
        print(f"estimated_spent_usd={result.estimated_spent_usd}")
        print(f"blockers={list(result.blockers)}")
        print(f"artifact_json={result.artifact_json}")
        print(f"health_snapshot_json={result.health_snapshot_json}")
        print(f"order_audit_json={result.order_audit_json}")
    return 0 if result.status == "validated" else 1


def _parse_strategy_notional_overrides(values: list[str]) -> dict[str, float]:
    overrides: dict[str, float] = {}
    for raw in values:
        if "=" not in raw:
            raise SystemExit(f"invalid --strategy-order-notional-usd value {raw!r}; expected STRATEGY=USD")
        strategy_id, value = raw.split("=", 1)
        strategy_id = strategy_id.strip()
        if not strategy_id:
            raise SystemExit(f"invalid --strategy-order-notional-usd value {raw!r}; missing strategy id")
        try:
            amount = float(value)
        except ValueError as exc:
            raise SystemExit(f"invalid --strategy-order-notional-usd value {raw!r}; amount must be numeric") from exc
        if amount <= 0:
            raise SystemExit(f"invalid --strategy-order-notional-usd value {raw!r}; amount must be positive")
        overrides[strategy_id] = amount
    return overrides


if __name__ == "__main__":
    raise SystemExit(main())
