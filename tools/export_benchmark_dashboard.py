from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.pipelines.daily.nba.analysis.benchmark_integration import (
    UnifiedBenchmarkRequest,
    export_benchmark_integration_bundle,
    resolve_default_shared_root,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export the unified benchmark dashboard snapshot into the shared coordination space."
    )
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--replay-artifact-name", default="postseason_execution_replay")
    parser.add_argument("--shared-root", default=None)
    parser.add_argument("--finalist-limit", type=int, default=6)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    shared_root = Path(args.shared_root) if args.shared_root else resolve_default_shared_root()
    artifact_root = shared_root / "artifacts" / "benchmark-integration"
    report_root = shared_root / "reports" / "benchmark-integration"
    request = UnifiedBenchmarkRequest(
        season=args.season,
        replay_artifact_name=args.replay_artifact_name,
        shared_root=str(shared_root),
        finalist_limit=args.finalist_limit,
    )
    result = export_benchmark_integration_bundle(
        request,
        artifact_root=artifact_root,
        report_root=report_root,
    )
    snapshot = result["snapshot"]
    print(f"Exported benchmark integration bundle for {snapshot['season']} to {artifact_root}")


if __name__ == "__main__":
    main()
