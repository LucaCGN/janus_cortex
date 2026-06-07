from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.config import CENTRAL_DB_PATH  # noqa: E402
from crypto_options_app.workers.strategy_backtest_replay import StrategyBacktestReplayConfig, run_strategy_backtest_replay  # noqa: E402
from crypto_options_app.workers.strategy_live_replay import DEFAULT_STRATEGY_IDS  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run read-only strategy historical backtest replay validation.")
    parser.add_argument("--db-path", default=str(CENTRAL_DB_PATH))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--strategy-ids", nargs="+", default=list(DEFAULT_STRATEGY_IDS))
    parser.add_argument("--max-trades-per-strategy", type=int, default=1)
    parser.add_argument("--validation-budget-cap-usd", type=float, default=50.0)
    parser.add_argument("--forward-mark-horizon-seconds", type=float, default=60.0)
    parser.add_argument("--max-scenarios", type=int, default=1)
    parser.add_argument(
        "--scenario-selector",
        choices=("fixture", "latest", "profile_preferred", "profile_opposed", "profile_group", "high_inversion", "tail_touch", "tail_touch_forward_edge_clean"),
        default="fixture",
        help=(
            "Historical replay scenario selection policy. The default fixture preserves legacy structural tests. "
            "Use tail_touch, tail_touch_forward_edge_clean, or high_inversion to replay against captured "
            "option paths while staying in dry_run mode."
        ),
    )
    parser.add_argument("--fail-on-blocked", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run_strategy_backtest_replay(
        StrategyBacktestReplayConfig(
            db_path=Path(args.db_path),
            run_id=args.run_id,
            strategy_ids=tuple(str(strategy_id) for strategy_id in args.strategy_ids),
            max_trades_per_strategy=int(args.max_trades_per_strategy),
            validation_budget_cap_usd=float(args.validation_budget_cap_usd),
            forward_mark_horizon_seconds=float(args.forward_mark_horizon_seconds),
            max_scenarios=int(args.max_scenarios),
            scenario_selector=str(args.scenario_selector),
        )
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"run_id={payload['run_id']}")
        print(f"scenario_source={payload['scenario_source']}")
        print(f"scenario_count={payload['scenario_count']}")
        print(f"strategy_ids={','.join(payload['strategy_ids'])}")
        print(f"passed_count={payload['passed_count']}")
        print(f"blocked_count={payload['blocked_count']}")
        print(f"db_counts={json.dumps(payload['db_counts'], sort_keys=True)}")
    return 1 if args.fail_on_blocked and payload["blocked_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
