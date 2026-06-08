from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.workers.signal_validation_worker import (  # noqa: E402
    SignalValidationWorkerConfig,
    run_signal_validation_worker_batch,
    run_signal_validation_worker_once,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one read-only signal validation queue item.")
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--owner-id", default="crypto-options-signal-validator-worker")
    parser.add_argument("--max-frames", type=int, default=100)
    parser.add_argument("--ttl-minutes", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = SignalValidationWorkerConfig(
        db_path=Path(args.db_path) if args.db_path else None,
        owner_id=str(args.owner_id),
        max_frames=max(1, int(args.max_frames)),
        ttl_minutes=max(1, int(args.ttl_minutes)),
        batch_size=max(1, int(args.batch_size)),
    )
    payload = run_signal_validation_worker_batch(config) if config.batch_size > 1 else run_signal_validation_worker_once(config)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"status={payload.get('status')}")
        print(f"validation_run_key={payload.get('validation_run_key')}")
        print(f"run_count={payload.get('run_count')}")
        print(f"blockers={payload.get('blockers')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
