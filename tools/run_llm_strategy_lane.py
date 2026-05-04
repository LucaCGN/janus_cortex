from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.pipelines.daily.nba.analysis.llm_strategy_lane import (  # noqa: E402
    LLMStrategyLaneRequest,
    run_llm_strategy_lane,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the replay-aware LLM strategy lane and publish shared benchmark artifacts."
    )
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--analysis-version", default="v1_0_1")
    parser.add_argument("--shared-root", default=None)
    parser.add_argument("--analysis-output-root", default=None)
    parser.add_argument("--replay-artifact-name", default="postseason_execution_replay")
    parser.add_argument("--artifact-name", default="postseason_replay_llm_v1")
    parser.add_argument("--cluster-window-minutes", type=int, default=15)
    parser.add_argument("--skip-dashboard-check", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = run_llm_strategy_lane(
        LLMStrategyLaneRequest(
            season=args.season,
            analysis_version=args.analysis_version,
            shared_root=args.shared_root,
            analysis_output_root=args.analysis_output_root,
            replay_artifact_name=args.replay_artifact_name,
            artifact_name=args.artifact_name,
            cluster_window_minutes=args.cluster_window_minutes,
            build_dashboard_check=not args.skip_dashboard_check,
        )
    )
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
