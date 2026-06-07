from __future__ import annotations

"""Build the issue #47 profile aggregation and signal report."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.pipelines.options.profile_signals import (  # noqa: E402
    ProfileSignalConfig,
    build_profile_signal_report,
    write_profile_signal_artifacts,
)


def run(args: argparse.Namespace) -> dict[str, Any]:
    config = ProfileSignalConfig(
        recent_signal_window_seconds=int(args.recent_signal_window_seconds),
        bot_trade_window_seconds=int(args.bot_trade_window_seconds),
        min_bot_trades_per_15m=int(args.min_bot_trades_per_15m),
        aggregate_trigger_weight=float(args.aggregate_trigger_weight),
        aggregate_min_profiles=int(args.aggregate_min_profiles),
        max_conflict_ratio=float(args.max_conflict_ratio),
        backtest_lookback_seconds=int(args.backtest_lookback_seconds),
        backtest_target_trade_count=int(args.backtest_target_trade_count),
        backtest_target_win_rate=float(args.backtest_target_win_rate),
    )
    payload = build_profile_signal_report(
        list(args.profile or []),
        active_event_slugs=list(args.active_event_slug or []),
        active_condition_ids=list(args.active_condition_id or []),
        obsidian_vault=args.obsidian_vault,
        include_obsidian_profiles=not args.no_obsidian_profiles,
        include_default_profiles=not args.no_default_profiles,
        include_active_profile_pool=not args.no_active_profile_pool,
        active_profile_pool_path=args.active_profile_pool,
        active_profile_pool_limit=int(args.active_profile_pool_limit),
        include_top_holders=args.include_top_holders,
        top_holder_limit=int(args.top_holder_limit),
        include_scraped_profiles=args.include_scraped_profiles,
        scrape_profile_limit=int(args.scrape_profile_limit),
        activity_pages=int(args.activity_pages),
        positions_pages=int(args.positions_pages),
        trades_pages=int(args.trades_pages),
        closed_pages=int(args.closed_pages),
        page_limit=int(args.page_limit),
        max_workers=int(args.max_workers),
        now_utc=_parse_now(args.now_utc),
        config=config,
    )
    artifacts = write_profile_signal_artifacts(payload, output_dir=args.output_dir)
    payload["artifacts"] = artifacts
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build crypto-options profile grading and aggregation signals.")
    parser.add_argument("--profile", action="append", help="Profile handle/address/ref. Repeatable.")
    parser.add_argument("--active-event-slug", action="append", help="Active 5m/15m event slug to filter current signals.")
    parser.add_argument("--active-condition-id", action="append", help="Active condition ID for optional top-holder discovery.")
    parser.add_argument("--obsidian-vault", default=None, help="Optional Janus Obsidian vault path.")
    parser.add_argument("--no-obsidian-profiles", action="store_true", help="Do not seed profiles from Obsidian hints.")
    parser.add_argument("--no-default-profiles", action="store_true", help="Do not include the user-requested default seed list.")
    parser.add_argument("--no-active-profile-pool", action="store_true", help="Do not include the bounded local active crypto profile pool.")
    parser.add_argument("--active-profile-pool", default=None, help="Optional active crypto profile pool text file.")
    parser.add_argument("--active-profile-pool-limit", type=int, default=120, help="Maximum active-pool refs to include in one full profile refresh.")
    parser.add_argument("--include-top-holders", action="store_true", help="Fetch Data API top holders for active condition IDs.")
    parser.add_argument("--top-holder-limit", type=int, default=20)
    parser.add_argument("--include-scraped-profiles", action="store_true", help="Scrape market pages for visible profile refs as a fallback.")
    parser.add_argument("--scrape-profile-limit", type=int, default=100)
    parser.add_argument("--activity-pages", type=int, default=1)
    parser.add_argument("--positions-pages", type=int, default=1)
    parser.add_argument("--trades-pages", type=int, default=1)
    parser.add_argument("--closed-pages", type=int, default=1)
    parser.add_argument("--page-limit", type=int, default=500)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--recent-signal-window-seconds", type=int, default=300)
    parser.add_argument("--bot-trade-window-seconds", type=int, default=900)
    parser.add_argument("--min-bot-trades-per-15m", type=int, default=8)
    parser.add_argument("--aggregate-trigger-weight", type=float, default=3.0)
    parser.add_argument("--aggregate-min-profiles", type=int, default=2)
    parser.add_argument("--max-conflict-ratio", type=float, default=0.5)
    parser.add_argument("--backtest-lookback-seconds", type=int, default=3600)
    parser.add_argument("--backtest-target-trade-count", type=int, default=12)
    parser.add_argument("--backtest-target-win-rate", type=float, default=0.70)
    parser.add_argument("--now-utc", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--json", action="store_true", help="Print full JSON payload.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        backtest = payload.get("backtest") or {}
        metrics = backtest.get("trade_metrics") or {}
        print(f"profile_snapshot_count={payload.get('profile_snapshot_count')}")
        print(f"active_signal_count={payload.get('active_signal_count')}")
        print(f"candidate_count={payload.get('candidate_count')}")
        print(f"backtest_status={backtest.get('status')}")
        print(f"backtest_trades={metrics.get('trade_count')}")
        print(f"backtest_win_rate={metrics.get('win_rate')}")
        print(f"artifact_json={(payload.get('artifacts') or {}).get('json')}")
        print(f"artifact_markdown={(payload.get('artifacts') or {}).get('markdown')}")
    return 0


def _parse_now(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
