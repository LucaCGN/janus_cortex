from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.workers.signal_design_reviewer import (  # noqa: E402
    SignalDesignReviewerConfig,
    run_signal_design_reviewer_once,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review read-only signal validation progress.")
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run_signal_design_reviewer_once(
        SignalDesignReviewerConfig(db_path=Path(args.db_path) if args.db_path else None)
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"status={payload.get('status')}")
        print(f"needs_work_count={payload.get('needs_work_count')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
