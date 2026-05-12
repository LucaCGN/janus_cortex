from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.nodes.wnba.balldontlie.client import describe_historical_backfill_readiness
from app.data.pipelines.daily.wnba.sync_postgres import (
    record_wnba_historical_backfill_readiness,
    run_wnba_current_season_sync,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WNBA backfill readiness and current-season CDN sync helper.")
    parser.add_argument("--current-season", default="2026")
    parser.add_argument("--last-season", default="2025")
    parser.add_argument("--record-blockers", action="store_true")
    parser.add_argument("--run-current-season-cdn-sync", action="store_true")
    parser.add_argument("--schedule-window-days", type=int, default=None)
    parser.add_argument("--skip-play-by-play", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    readiness = (
        record_wnba_historical_backfill_readiness(season=args.last_season)
        if args.record_blockers
        else describe_historical_backfill_readiness(season=args.last_season)
    )
    print(f"last_season={args.last_season} balldontlie_status={readiness['status']}")
    for blocker in readiness.get("blockers", []):
        print(f"blocker={blocker.get('code')} requirement={blocker.get('requirement')}")

    if args.run_current_season_cdn_sync:
        summary = run_wnba_current_season_sync(
            season=args.current_season,
            schedule_window_days=args.schedule_window_days,
            include_play_by_play=not args.skip_play_by_play,
        )
        print(f"sync_run_id={summary.sync_run_id}")
        print(f"status={summary.status} rows_read={summary.rows_read} rows_written={summary.rows_written}")
        if summary.error_text:
            print(f"error={summary.error_text}")
        return 0 if summary.status == "success" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
