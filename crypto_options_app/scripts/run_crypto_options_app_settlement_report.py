from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.config import CENTRAL_ARTIFACT_ROOT, CENTRAL_DB_PATH  # noqa: E402
from crypto_options_app.reports.settlement_performance import reconcile_live_run_settlements  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build read-only settlement/PnL report for a Crypto Options App live run.")
    parser.add_argument("--run-artifact", required=True)
    parser.add_argument("--db-path", default=str(CENTRAL_DB_PATH))
    parser.add_argument("--report-dir", default=str(CENTRAL_ARTIFACT_ROOT / "reports"))
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = reconcile_live_run_settlements(
        run_artifact_path=Path(args.run_artifact),
        db_path=Path(args.db_path),
        report_dir=Path(args.report_dir),
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(f"run_id={report.get('run_id')}")
        print(f"status={report.get('status')}")
        print(f"blockers={report.get('blockers')}")
        print(f"realized_pnl_usd={(report.get('summary') or {}).get('realized_pnl_usd')}")
        print(f"report_json={report.get('report_json')}")
    return 0 if report.get("status") in {"settled", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
