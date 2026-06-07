from __future__ import annotations

"""Run profile-signal monitoring ticks for crypto up/down markets."""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.config import CENTRAL_ACTIVE_PROFILE_POOL  # noqa: E402
from app.data.pipelines.crypto.options.profile_signal_monitor import (  # noqa: E402
    build_profile_signal_monitor_tick,
    build_profile_signal_protocol,
    write_profile_signal_monitor_artifacts,
)


def compact_live_monitor_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop heavy research-only profile detail from live-runner monitor artifacts."""
    compact = dict(payload)
    report = compact.get("profile_signal_report")
    if not isinstance(report, dict):
        return compact

    compact_report = dict(report)
    for key in (
        "profiles",
        "active_signals",
        "profile_registry",
        "profile_signal_rows",
        "raw_profile_activity",
        "raw_profiles",
    ):
        value = compact_report.pop(key, None)
        if isinstance(value, list):
            compact_report[f"{key}_compacted_count"] = len(value)
        elif isinstance(value, dict):
            compact_report[f"{key}_compacted_count"] = len(value)
    compact_report["artifact_mode"] = "compact_live"
    compact["profile_signal_report"] = compact_report
    compact["artifact_mode"] = "compact_live"
    return compact


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    profile_report_cache = None
    if args.profile_report_cache:
        cache_payload = json.loads(Path(args.profile_report_cache).read_text(encoding="utf-8"))
        profile_report_cache = cache_payload.get("profile_signal_report") if isinstance(cache_payload.get("profile_signal_report"), dict) else cache_payload
    payload = build_profile_signal_monitor_tick(
        strategy_id=args.strategy_id,
        symbols=list(args.symbol or ["BTC", "ETH"]),
        profile_refs=list(args.profile or []),
        obsidian_vault=args.obsidian_vault,
        max_workers=int(args.max_workers),
        page_limit=int(args.page_limit),
        max_signal_to_ask_slippage_cents=float(args.max_signal_to_ask_slippage_cents),
        max_spread=float(args.max_spread),
        min_depth_top3_ask_size=float(args.min_depth_top3_ask_size),
        min_time_remaining_seconds=float(args.min_time_remaining_seconds),
        max_time_remaining_seconds=float(args.max_time_remaining_seconds),
        include_top_holders=bool(args.include_top_holders),
        include_active_profile_pool=not bool(args.no_active_profile_pool),
        active_profile_pool_path=args.active_profile_pool,
        active_profile_pool_limit=int(args.active_profile_pool_limit),
        include_scraped_profiles=bool(args.include_scraped_profiles),
        scrape_profile_limit=int(args.scrape_profile_limit),
        profile_report_cache=profile_report_cache,
        include_underlying_context=bool(args.include_underlying_context),
    )
    if args.compact_live_artifact:
        payload = compact_live_monitor_payload(payload)
    protocol = build_profile_signal_protocol(strategy_id=args.strategy_id, budget_cap_usd=float(args.budget_cap_usd))
    artifacts = write_profile_signal_monitor_artifacts(payload, output_dir=args.output_dir, protocol_payload=protocol)
    payload["artifacts"] = artifacts
    return payload


def run_watch(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    ticks: list[dict[str, Any]] = []
    output_dir = Path(args.output_dir).resolve() if args.output_dir else None
    if output_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        day = datetime.now(timezone.utc).date().isoformat()
        output_dir = REPO_ROOT / "local" / "shared" / "artifacts" / "crypto-options-research" / "profile-signal-loop" / day / stamp
        args.output_dir = str(output_dir)
    while time.monotonic() - started <= float(args.duration_seconds):
        tick = run_once(args)
        ticks.append(
            {
                "generated_at_utc": tick.get("generated_at_utc"),
                "monitor_status": tick.get("monitor_status"),
                "eligible_manual_candidate_count": tick.get("eligible_manual_candidate_count"),
                "candidate_count": (tick.get("profile_signal_report") or {}).get("candidate_count"),
                "backtest_best_variant": ((tick.get("profile_signal_report") or {}).get("backtest") or {}).get("best_variant"),
                "artifacts": tick.get("artifacts"),
            }
        )
        print(
            "profile_signal_monitor_tick "
            f"status={tick.get('monitor_status')} eligible={tick.get('eligible_manual_candidate_count')} "
            f"artifact={(tick.get('artifacts') or {}).get('monitor')}",
            flush=True,
        )
        if not args.watch:
            break
        elapsed = time.monotonic() - started
        if elapsed + float(args.interval_seconds) > float(args.duration_seconds):
            break
        time.sleep(max(1.0, float(args.interval_seconds)))
    summary = {
        "schema_version": "crypto_options_profile_signal_monitor_watch_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "watch_status": "complete",
        "duration_seconds": float(args.duration_seconds),
        "interval_seconds": float(args.interval_seconds),
        "tick_count": len(ticks),
        "ticks": ticks,
        "output_dir": str(output_dir),
    }
    summary_path = output_dir / "profile_signal_monitor_watch_summary.json"
    summary_path.write_text(json.dumps(summary, allow_nan=False, indent=2, sort_keys=True), encoding="utf-8")
    summary["summary_artifact"] = str(summary_path)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor profile aggregation signals for active crypto 5m markets.")
    parser.add_argument("--strategy-id", default="btc_eth_5m_mid_high")
    parser.add_argument("--symbol", action="append", help="Symbol to monitor. Repeatable; default BTC and ETH.")
    parser.add_argument("--profile", action="append", help="Extra profile handle/address/ref to seed. Repeatable.")
    parser.add_argument("--obsidian-vault", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--page-limit", type=int, default=200)
    parser.add_argument("--include-top-holders", action="store_true")
    parser.add_argument("--no-active-profile-pool", action="store_true", help="Disable the bounded local active crypto profile pool.")
    parser.add_argument("--active-profile-pool", default=str(CENTRAL_ACTIVE_PROFILE_POOL), help="Optional active crypto profile pool text file.")
    parser.add_argument("--active-profile-pool-limit", type=int, default=120, help="Maximum active-pool refs to include in one full profile refresh.")
    parser.add_argument("--include-scraped-profiles", action="store_true")
    parser.add_argument("--scrape-profile-limit", type=int, default=100)
    parser.add_argument("--profile-report-cache", default=None, help="Reuse a prior profile signal report JSON for low-latency live ticks.")
    parser.add_argument("--include-underlying-context", action="store_true", help="Attach Binance-derived underlying price and trend context to observed candidates.")
    parser.add_argument("--max-signal-to-ask-slippage-cents", type=float, default=10.0)
    parser.add_argument("--max-spread", type=float, default=0.03)
    parser.add_argument("--min-depth-top3-ask-size", type=float, default=5.0)
    parser.add_argument("--min-time-remaining-seconds", type=float, default=20.0)
    parser.add_argument("--max-time-remaining-seconds", type=float, default=300.0)
    parser.add_argument("--budget-cap-usd", type=float, default=50.0)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--duration-seconds", type=float, default=3600.0)
    parser.add_argument("--interval-seconds", type=float, default=300.0)
    parser.add_argument("--compact-live-artifact", action="store_true", help="Write a compact candidate-only artifact for supervised live runners.")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run_watch(args) if args.watch else run_once(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"monitor_status={payload.get('monitor_status') or payload.get('watch_status')}")
        print(f"eligible_manual_candidate_count={payload.get('eligible_manual_candidate_count')}")
        print(f"artifact_monitor={(payload.get('artifacts') or {}).get('monitor')}")
        print(f"summary_artifact={payload.get('summary_artifact')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
