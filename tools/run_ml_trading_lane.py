from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.pipelines.daily.nba.analysis.ml_trading_lane import (
    ML_ARTIFACT_NAME,
    MLTradingLaneRequest,
    REPLAY_ARTIFACT_NAME,
    run_ml_trading_lane,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the replay-aware ML trading lane and publish shared benchmark artifacts."
    )
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--analysis-version", default="v1_0_1")
    parser.add_argument("--shared-root", default=None)
    parser.add_argument("--analysis-output-root", default=None)
    parser.add_argument("--replay-artifact-name", default=REPLAY_ARTIFACT_NAME)
    parser.add_argument("--training-replay-artifact-name", action="append", default=[])
    parser.add_argument("--state-panel-phase", action="append", default=[])
    parser.add_argument("--holdout-season-phase", action="append", default=[])
    parser.add_argument("--training-season-phase", action="append", default=[])
    parser.add_argument("--use-phase-holdout", action="store_true")
    parser.add_argument("--artifact-name", default=ML_ARTIFACT_NAME)
    parser.add_argument("--warmup-dates", type=int, default=3)
    parser.add_argument("--gate-threshold", type=float, default=0.30)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    replay_artifact_names = tuple(
        dict.fromkeys(
            [
                *[str(value).strip() for value in args.training_replay_artifact_name if str(value).strip()],
                str(args.replay_artifact_name).strip(),
            ]
        )
    )
    payload = run_ml_trading_lane(
        MLTradingLaneRequest(
            season=args.season,
            analysis_version=args.analysis_version,
            shared_root=args.shared_root,
            analysis_output_root=args.analysis_output_root,
            replay_artifact_name=args.replay_artifact_name,
            replay_artifact_names=replay_artifact_names,
            state_panel_phases=tuple(args.state_panel_phase or ["play_in", "playoffs"]),
            training_season_phases=tuple(args.training_season_phase or ["regular_season"]),
            holdout_season_phases=tuple(args.holdout_season_phase or ["play_in", "playoffs"]),
            use_phase_holdout=bool(args.use_phase_holdout),
            artifact_name=args.artifact_name,
            warmup_dates=args.warmup_dates,
            gate_threshold=args.gate_threshold,
        )
    )
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
