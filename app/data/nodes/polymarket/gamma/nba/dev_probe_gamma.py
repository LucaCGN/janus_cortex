from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[6]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.data.nodes.polymarket.gamma.nba.events_node import NBAEventsRequest, fetch_nba_events_df
from app.data.nodes.polymarket.gamma.nba.markets_moneyline_node import (
    NBAMoneylineMarketsRequest,
    fetch_nba_moneyline_df,
)


def _parse_dt(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _print_time_span(df: pd.DataFrame, column: str, label: str) -> None:
    if df.empty or column not in df.columns:
        print(f"{label}: empty")
        return

    series = pd.to_datetime(df[column], errors="coerce", utc=True).dropna()
    if series.empty:
        print(f"{label}: no parseable timestamps")
        return

    print(f"{label}: min={series.min().isoformat()} max={series.max().isoformat()} rows={len(df)}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a local Gamma NBA probe for events and moneyline windows."
    )
    parser.add_argument("--start", help="ISO datetime, e.g. 2026-01-01T00:00:00Z")
    parser.add_argument("--end", help="ISO datetime, e.g. 2026-02-01T00:00:00Z")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--tag-id", type=int, default=None)
    parser.add_argument("--tag-slug", default="nba")
    args = parser.parse_args()

    start_dt = _parse_dt(args.start)
    end_dt = _parse_dt(args.end)

    events_req = NBAEventsRequest(
        tag_id=args.tag_id,
        tag_slug=args.tag_slug,
        only_open=False,
        page_size=args.page_size,
        max_pages=args.max_pages,
        start_date_min=start_dt,
        start_date_max=end_dt,
    )
    events_df = fetch_nba_events_df(req=events_req)
    print(f"events_rows={len(events_df)}")
    _print_time_span(events_df, "start_time", "events_start_time_span")

    moneyline_req = NBAMoneylineMarketsRequest(
        tag_id=args.tag_id,
        tag_slug=args.tag_slug,
        only_open=False,
        page_size=args.page_size,
        max_pages=args.max_pages,
        start_date_min=start_dt,
        start_date_max=end_dt,
        use_events_fallback=True,
    )
    moneyline_df = fetch_nba_moneyline_df(req=moneyline_req)
    print(f"moneyline_rows={len(moneyline_df)}")
    _print_time_span(moneyline_df, "game_start_time", "moneyline_game_start_span")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
