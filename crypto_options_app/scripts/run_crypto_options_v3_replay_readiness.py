from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.services.crypto_options.v3_replay_readiness import (  # noqa: E402
    apply_v3_passive_reconciliation,
    build_v3_replay_readiness_report,
    write_v3_replay_readiness_artifacts,
)


def run(args: argparse.Namespace) -> dict[str, Any]:
    applied_reconciliation = None
    if bool(args.apply_passive_reconciliation):
        applied_reconciliation = apply_v3_passive_reconciliation(
            args.run_root,
            backup=not bool(args.no_passive_reconciliation_backup),
        )
    payload = build_v3_replay_readiness_report(
        args.run_root,
        min_restart_win_rate=float(args.min_restart_win_rate),
        ideal_win_rate=float(args.ideal_win_rate),
        max_worst_case_loss_usd=float(args.max_worst_case_loss_usd),
        include_passive_reconciliation_preview=bool(args.include_passive_reconciliation_preview),
    )
    if applied_reconciliation:
        payload["applied_passive_reconciliation"] = applied_reconciliation
    payload["artifacts"] = write_v3_replay_readiness_artifacts(payload, output_dir=args.output_dir)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a V3 crypto-options replay/readiness report for a run root.")
    parser.add_argument("--run-root", required=True, help="V3 run root to inspect.")
    parser.add_argument("--output-dir", default=None, help="Optional output directory for artifacts.")
    parser.add_argument("--min-restart-win-rate", type=float, default=0.60)
    parser.add_argument("--ideal-win-rate", type=float, default=0.70)
    parser.add_argument("--max-worst-case-loss-usd", type=float, default=25.0)
    parser.add_argument(
        "--include-passive-reconciliation-preview",
        action="store_true",
        help="Dry-run settlement reconciliation and include post-reconciliation restart gates in the report.",
    )
    parser.add_argument(
        "--apply-passive-reconciliation",
        action="store_true",
        help="Apply passive settlement reconciliation to component ledgers before building readiness artifacts. Never submits orders.",
    )
    parser.add_argument(
        "--no-passive-reconciliation-backup",
        action="store_true",
        help="Do not create .bak-* copies before applying passive reconciliation.",
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"ready_for_live_restart={payload.get('ready_for_live_restart')}")
        print(f"restart_recommendation={payload.get('restart_recommendation')}")
        print(f"blocker_count={len(payload.get('blockers') or [])}")
        print(f"artifact_json={(payload.get('artifacts') or {}).get('json')}")
        print(f"artifact_markdown={(payload.get('artifacts') or {}).get('markdown')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
