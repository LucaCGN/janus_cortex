from __future__ import annotations

"""Read-only bucketed cashout simulator for issue #93/#96."""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.pipelines.options.cashout_simulator import (  # noqa: E402
    simulate_bucketed_cashout_from_paths,
    write_cashout_simulation_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build read-only bucketed cashout simulation artifacts.")
    parser.add_argument("--ledger", action="append", default=[], help="Path to a lane live_execution_ledger.json. Repeatable.")
    parser.add_argument("--monitor", action="append", default=[], help="Path to a profile monitor JSON artifact. Repeatable.")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--json", action="store_true", help="Print the full payload as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = simulate_bucketed_cashout_from_paths(ledger_paths=args.ledger, monitor_paths=args.monitor)
    artifacts = write_cashout_simulation_artifacts(payload, output_dir=args.output_dir)
    payload["artifacts"] = artifacts
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"artifact_json={artifacts['json']}")
        print(f"artifact_markdown={artifacts['markdown']}")
        print(f"scoreable_count={payload['scoreable_count']}")
        print(f"cashout_hit_count={payload['cashout_hit_count']}")
        print(f"prevented_loss_count={payload['prevented_loss_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
