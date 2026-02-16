from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.data.nodes.nba.schedule.season_schedule import fetch_season_schedule_df
from app.data.nodes.polymarket.gamma.nba.events_node import NBAEventsRequest, fetch_nba_events_df
from app.data.nodes.polymarket.gamma.nba.markets_moneyline_node import (
    NBAMoneylineMarketsRequest,
    fetch_nba_moneyline_df,
)
from app.data.nodes.polymarket.gamma.nba.odds_history_node import (
    NBAOddsHistoryRequest,
    fetch_nba_odds_history_df,
)
from app.data.nodes.polymarket.gamma.nba.fallback_stream_history_collector import (
    NBAFallbackStreamRequest,
    collect_nba_fallback_stream_df,
)


def _fmt_window(df: pd.DataFrame, col: str) -> str:
    if df.empty or col not in df.columns:
        return "empty"
    values = pd.to_datetime(df[col], errors="coerce", utc=True).dropna()
    if values.empty:
        return "no-parseable-timestamps"
    return f"{values.min().isoformat()}..{values.max().isoformat()}"


def _parse_utc(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NBA sync checkpoint script for v0.1.x validation (dry-run by default)."
    )
    parser.add_argument("--season", default=None, help="Season hint for schedule feed (e.g. 2025-26)")
    parser.add_argument("--start", default=None, help="ISO UTC start bound for Gamma queries")
    parser.add_argument("--end", default=None, help="ISO UTC end bound for Gamma queries")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument(
        "--include-gamma",
        action="store_true",
        help="Also query Gamma events and moneyline in the same window.",
    )
    parser.add_argument(
        "--include-history",
        action="store_true",
        help="When --include-gamma is set, also query CLOB/Gamma odds history for moneyline outcomes.",
    )
    parser.add_argument("--history-interval", default="1m", help="CLOB history interval.")
    parser.add_argument("--history-fidelity", type=int, default=10, help="CLOB history fidelity.")
    parser.add_argument("--history-max-outcomes", type=int, default=25, help="Max outcomes to sample for history.")
    parser.add_argument(
        "--include-stream-fallback",
        action="store_true",
        help="Collect append-only fallback stream samples from live moneyline snapshots.",
    )
    parser.add_argument("--stream-sample-count", type=int, default=2)
    parser.add_argument("--stream-sample-interval-sec", type=float, default=0.5)
    parser.add_argument("--stream-max-outcomes", type=int, default=25)
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Validation mode only; no persistence side effects.",
    )
    args = parser.parse_args()

    if not args.dry_run:
        print("sync_db.py currently supports dry-run validation only.")
        return 2

    schedule_df = fetch_season_schedule_df(season=args.season)
    print(f"schedule_rows={len(schedule_df)}")
    if not schedule_df.empty and "game_date" in schedule_df.columns:
        print(f"schedule_game_date_window={schedule_df['game_date'].min()}..{schedule_df['game_date'].max()}")

    if not args.include_gamma:
        print("dry_run_complete=true gamma_skipped=true")
        return 0

    start_dt = _parse_utc(args.start)
    end_dt = _parse_utc(args.end)
    if start_dt is None and end_dt is None:
        now = datetime.now(timezone.utc)
        start_dt = now - timedelta(days=60)
        end_dt = now + timedelta(days=30)

    events_req = NBAEventsRequest(
        only_open=False,
        page_size=args.page_size,
        max_pages=args.max_pages,
        start_date_min=start_dt,
        start_date_max=end_dt,
    )
    events_df = fetch_nba_events_df(req=events_req)
    print(f"gamma_events_rows={len(events_df)}")
    print(f"gamma_events_start_window={_fmt_window(events_df, 'start_time')}")

    moneyline_req = NBAMoneylineMarketsRequest(
        only_open=False,
        page_size=args.page_size,
        max_pages=args.max_pages,
        start_date_min=start_dt,
        start_date_max=end_dt,
        use_events_fallback=True,
    )
    moneyline_df = fetch_nba_moneyline_df(req=moneyline_req)
    print(f"gamma_moneyline_rows={len(moneyline_df)}")
    print(f"gamma_moneyline_window={_fmt_window(moneyline_df, 'game_start_time')}")

    if args.include_history:
        history_req = NBAOddsHistoryRequest(
            only_open=False,
            page_size=args.page_size,
            max_pages=args.max_pages,
            start_date_min=start_dt,
            start_date_max=end_dt,
            use_events_fallback=True,
            interval=args.history_interval,
            fidelity=args.history_fidelity,
            max_outcomes=args.history_max_outcomes,
            allow_snapshot_fallback=True,
        )
        history_df = fetch_nba_odds_history_df(req=history_req)
        print(f"gamma_history_rows={len(history_df)}")
        print(f"gamma_history_ts_window={_fmt_window(history_df, 'ts')}")
        print(f"gamma_history_game_window={_fmt_window(history_df, 'game_start_time')}")

    if args.include_stream_fallback:
        stream_req = NBAFallbackStreamRequest(
            only_open=False,
            page_size=args.page_size,
            max_pages=args.max_pages,
            start_date_min=start_dt,
            start_date_max=end_dt,
            use_events_fallback=True,
            sample_count=args.stream_sample_count,
            sample_interval_sec=args.stream_sample_interval_sec,
            max_outcomes=args.stream_max_outcomes,
            retries_per_sample=1,
            retry_backoff_sec=0.2,
        )
        stream_df = collect_nba_fallback_stream_df(req=stream_req)
        print(f"gamma_stream_rows={len(stream_df)}")
        print(f"gamma_stream_ts_window={_fmt_window(stream_df, 'ts')}")
        print(f"gamma_stream_game_window={_fmt_window(stream_df, 'game_start_time')}")

    print("dry_run_complete=true gamma_skipped=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
