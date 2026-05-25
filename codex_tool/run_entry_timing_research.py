from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.modules.agentic.entry_timing_research import (
    build_entry_timing_matrix,
    build_event_control_recommendation_pack,
    write_entry_timing_matrix,
    write_event_control_recommendation_pack,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a read-only Janus entry-timing research matrix.")
    parser.add_argument("--session-date", default=None)
    parser.add_argument("--fixture-backtest-path", default=None)
    parser.add_argument("--live-worker-ticks-path", default=None)
    parser.add_argument("--event-control-recommendations", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    matrix = build_entry_timing_matrix(
        day=args.session_date,
        fixture_backtest_path=Path(args.fixture_backtest_path) if args.fixture_backtest_path else None,
        live_worker_ticks_path=Path(args.live_worker_ticks_path) if args.live_worker_ticks_path else None,
    )
    result = write_entry_timing_matrix(matrix)
    if args.event_control_recommendations:
        pack = build_event_control_recommendation_pack(matrix, source_matrix_path=result["json_path"])
        result["event_control_recommendation_pack"] = write_event_control_recommendation_pack(pack)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result["markdown_path"])


if __name__ == "__main__":
    main()
