from __future__ import annotations

"""Evaluate a fresh decision-review artifact against the approved micro-test protocol."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.pipelines.crypto.options.micro_test_monitor import (  # noqa: E402
    build_micro_test_monitor,
    write_micro_test_monitor_artifacts,
)


def run(args: argparse.Namespace) -> dict[str, Any]:
    protocol = json.loads(Path(args.protocol_artifact).read_text(encoding="utf-8"))
    decision_review = json.loads(Path(args.decision_review_artifact).read_text(encoding="utf-8"))
    trade_ledger = json.loads(Path(args.trade_ledger_artifact).read_text(encoding="utf-8-sig")) if args.trade_ledger_artifact else None
    payload = build_micro_test_monitor(
        protocol,
        decision_review,
        manual_no_open_position_confirmed=bool(args.manual_no_open_position_confirmed),
        trade_ledger_payload=trade_ledger,
    )
    artifacts = write_micro_test_monitor_artifacts(payload, output_dir=args.output_dir)
    payload["artifacts"] = artifacts
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor approved crypto-options micro-test protocol candidates.")
    parser.add_argument("--protocol-artifact", required=True, help="Approved micro-test protocol JSON artifact.")
    parser.add_argument("--decision-review-artifact", required=True, help="Fresh live decision-review JSON artifact.")
    parser.add_argument("--trade-ledger-artifact", default=None, help="Optional manual trade ledger JSON artifact for trade/loss stop gates.")
    parser.add_argument("--output-dir", default=None, help="Optional output directory.")
    parser.add_argument(
        "--manual-no-open-position-confirmed",
        action="store_true",
        help="Use only after manually confirming no crypto-options micro-test position is open.",
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON payload.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        watch = payload.get("reference_account_watch") or {}
        print(f"monitor_status={payload.get('monitor_status')}")
        print(f"eligible_manual_candidate_count={payload.get('eligible_manual_candidate_count')}")
        print(f"reference_watch_only_candidate_count={watch.get('watch_only_candidate_count')}")
        print(f"live_trading_authorized={payload.get('live_trading_authorized')}")
        print(f"orders_allowed={payload.get('orders_allowed')}")
        print(f"blockers={payload.get('blockers')}")
        print(f"artifact_json={(payload.get('artifacts') or {}).get('json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
