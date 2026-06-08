from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.config import CENTRAL_DB_PATH  # noqa: E402
from crypto_options_app.workers.strategy_live_replay import DEFAULT_STRATEGY_IDS, StrategyLiveReplayConfig, run_strategy_live_replay  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run read-only strategy live replay/shadow validation.")
    parser.add_argument("--db-path", default=str(CENTRAL_DB_PATH))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--strategy-ids", nargs="+", default=list(DEFAULT_STRATEGY_IDS))
    parser.add_argument("--max-trades-per-strategy", type=int, default=1)
    parser.add_argument("--validation-budget-cap-usd", type=float, default=50.0)
    parser.add_argument("--forward-mark-horizon-seconds", type=float, default=60.0)
    parser.add_argument("--max-scenarios", type=int, default=1)
    parser.add_argument(
        "--scenario-selector",
        choices=(
            "latest",
            "profile_preferred",
            "profile_opposed",
            "profile_group",
            "profile_group_quality",
            "high_inversion",
            "high_inversion_disjoint",
            "recent_high_inversion_disjoint",
            "hedge_grid_ready",
            "hedge_grid_closed_cycle_ready",
            "tail_touch",
            "tail_touch_forward_edge_clean",
            "low_range_no_edge",
        ),
        default="latest",
        help=(
            "Replay scenario selection policy. Use profile_preferred for profile-follow tests, "
            "profile_opposed for profile-fade/contrarian tests, profile_group for subgroup-specific "
            "profile strategies, profile_group_quality for subgroup-specific profile strategies after "
            "cheap entry/spread/concentration/path gates, high_inversion for hedge-floor/grid tests, high_inversion_disjoint "
            "for the same score with a small per-event cap, recent_high_inversion_disjoint "
            "for recent promotion-proof windows with the same per-event cap, hedge_grid_ready "
            "for V11-compatible protected-floor source windows, hedge_grid_closed_cycle_ready "
            "for V12-compatible closed-cycle protected-floor source windows, tail_touch "
            "for 1c/5c/10c comeback optionality tests, and tail_touch_forward_edge_clean "
            "for the stronger forward-cashout subset of those same tail windows. Use "
            "low_range_no_edge for diagnostic no-edge/dead-window controls."
        ),
    )
    parser.add_argument("--fail-on-blocked", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run_strategy_live_replay(
        StrategyLiveReplayConfig(
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
