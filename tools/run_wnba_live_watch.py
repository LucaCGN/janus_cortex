from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.nodes.wnba.polymarket.moneyline import match_wnba_moneyline_markets_to_schedule
from app.data.nodes.wnba.schedule.season_schedule import fetch_season_schedule_payload, normalize_schedule_payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build passive WNBA Polymarket CLOB watch targets.")
    parser.add_argument(
        "--moneyline-csv",
        type=Path,
        default=None,
        help="CSV with Polymarket-like moneyline outcome rows. Required columns include market_id and outcome.",
    )
    parser.add_argument("--season", default="2026")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    payload = fetch_season_schedule_payload()
    schedule_df, _teams_df = normalize_schedule_payload(payload, season=args.season)

    if args.moneyline_csv is None:
        print("status=plan_only")
        print("orders_allowed=false")
        print("passive_watch_required=true")
        print("required_input=Polymarket WNBA moneyline outcome rows with market_id,outcome,token_id,event_slug")
        print(f"schedule_games_available={len(schedule_df)}")
        return 0

    moneyline_df = pd.read_csv(args.moneyline_csv)
    watch_targets = match_wnba_moneyline_markets_to_schedule(moneyline_df, schedule_df)
    print(f"status=ok watch_targets={len(watch_targets)} orders_allowed=false")
    if not watch_targets.empty:
        print(watch_targets.to_json(orient="records", date_format="iso"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
