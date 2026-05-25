from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.modules.agentic.replay_config_review import (
    build_no_bid_min_price_lottery_study,
    build_replay_config_review,
    build_replay_fixture_backtest,
    write_no_bid_min_price_lottery_study,
    write_replay_config_review,
    write_replay_fixture_backtest,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate one read-only Janus postgame replay/config review.")
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--report-limit", type=int, default=6)
    parser.add_argument("--fixture-backtest", action="store_true")
    parser.add_argument("--no-bid-study", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    review = build_replay_config_review(day=args.session_date, report_limit=args.report_limit)
    result = write_replay_config_review(review)
    backtest = build_replay_fixture_backtest(review) if args.fixture_backtest or args.no_bid_study else None
    if args.fixture_backtest and backtest is not None:
        result["fixture_backtest"] = write_replay_fixture_backtest(backtest)
    if args.no_bid_study and backtest is not None:
        result["no_bid_min_price_lottery_study"] = write_no_bid_min_price_lottery_study(
            build_no_bid_min_price_lottery_study(backtest)
        )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result["markdown_path"])


if __name__ == "__main__":
    main()
