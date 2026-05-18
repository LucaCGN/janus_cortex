from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deterministic post-startup Janus reconciliation for a date window."
    )
    parser.add_argument("--start-date", default=date.today().isoformat())
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--api-root", default="http://127.0.0.1:8010")
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--account-catalog-backfill-limit", type=int, default=100)
    return parser.parse_args()


def _date_range(start_date: str, days: int) -> list[str]:
    start = date.fromisoformat(start_date)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(max(days, 1))]


def _run_for_date(
    *,
    session_date: str,
    api_root: str,
    season: str,
    account_catalog_backfill_limit: int,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(REPO_ROOT / "tools" / "run_janus_operational_cycle.py"),
        "--api-root",
        api_root,
        "--season",
        season,
        "--account-catalog-backfill-limit",
        str(int(account_catalog_backfill_limit)),
        "--session-date",
        session_date,
        "--stage",
        "data-refresh",
    ]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "session_date": session_date,
        "returncode": completed.returncode,
        "command": command,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def main() -> int:
    args = _parse_args()
    results = [
        _run_for_date(
            session_date=session_date,
            api_root=args.api_root,
            season=args.season,
            account_catalog_backfill_limit=args.account_catalog_backfill_limit,
        )
        for session_date in _date_range(args.start_date, args.days)
    ]
    payload = {
        "status": "success" if all(item["returncode"] == 0 for item in results) else "needs_attention",
        "start_date": args.start_date,
        "days": max(args.days, 1),
        "results": results,
        "live_order_impact": "none",
        "startup_contract": (
            "Call after API startup to rebuild session-date schedule, Polymarket probes, mappings, and portfolio mirrors "
            "without starting workers or placing orders."
        ),
    }
    print(json.dumps(payload, indent=2))
    return 0 if payload["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
