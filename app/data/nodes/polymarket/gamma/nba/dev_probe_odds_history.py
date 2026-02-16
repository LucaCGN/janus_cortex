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

from app.data.nodes.polymarket.gamma.nba.odds_history_node import (  # noqa: E402
    NBAOddsHistoryRequest,
    fetch_nba_odds_history_df,
)


def _parse_dt(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _describe(df: pd.DataFrame) -> None:
    print(f"rows={len(df)}")
    if df.empty:
        return

    if "source" in df.columns:
        print("source_counts=", df["source"].value_counts(dropna=False).to_dict())
    if "game_start_time" in df.columns:
        games = pd.to_datetime(df["game_start_time"], errors="coerce", utc=True).dropna()
        if not games.empty:
            print(f"game_start_window={games.min().isoformat()}..{games.max().isoformat()}")
    if "ts" in df.columns:
        ts = pd.to_datetime(df["ts"], errors="coerce", utc=True).dropna()
        if not ts.empty:
            print(f"history_ts_window={ts.min().isoformat()}..{ts.max().isoformat()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe NBA historical odds acquisition node.")
    parser.add_argument("--start", help="ISO datetime")
    parser.add_argument("--end", help="ISO datetime")
    parser.add_argument("--interval", default="1m", help="CLOB interval: 1m, 1h, 6h, 1d, 1w")
    parser.add_argument("--fidelity", type=int, default=10)
    parser.add_argument("--max-outcomes", type=int, default=25)
    parser.add_argument("--allow-snapshot-fallback", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    req = NBAOddsHistoryRequest(
        start_date_min=_parse_dt(args.start),
        start_date_max=_parse_dt(args.end),
        interval=args.interval,
        fidelity=args.fidelity,
        max_outcomes=args.max_outcomes,
        allow_snapshot_fallback=args.allow_snapshot_fallback,
    )
    df = fetch_nba_odds_history_df(req=req)
    _describe(df)
    print(df.head(10).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
