from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[6]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.data.nodes.polymarket.gamma.nba.fallback_stream_history_collector import (  # noqa: E402
    NBAFallbackStreamRequest,
    collect_nba_fallback_stream_df,
)


def _describe(df: pd.DataFrame) -> None:
    print(f"rows={len(df)}")
    if df.empty:
        return
    if "sample_no" in df.columns:
        print("sample_counts=", df["sample_no"].value_counts(dropna=False).sort_index().to_dict())
    if "source" in df.columns:
        print("source_counts=", df["source"].value_counts(dropna=False).to_dict())
    if "ts" in df.columns:
        ts = pd.to_datetime(df["ts"], errors="coerce", utc=True).dropna()
        if not ts.empty:
            print(f"ts_window={ts.min().isoformat()}..{ts.max().isoformat()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe fallback stream-to-history collector.")
    parser.add_argument("--sample-count", type=int, default=2)
    parser.add_argument("--sample-interval-sec", type=float, default=1.0)
    parser.add_argument("--max-outcomes", type=int, default=10)
    args = parser.parse_args()

    req = NBAFallbackStreamRequest(
        sample_count=args.sample_count,
        sample_interval_sec=args.sample_interval_sec,
        max_outcomes=args.max_outcomes,
        retries_per_sample=1,
        retry_backoff_sec=0.2,
    )
    df = collect_nba_fallback_stream_df(req=req)
    _describe(df)
    print(df.head(10).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
