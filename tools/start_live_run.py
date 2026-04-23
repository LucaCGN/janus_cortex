from __future__ import annotations

import argparse
import json
import sys
from urllib import request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start or resume a Janus live NBA playoff run.")
    parser.add_argument("--api-root", default="http://127.0.0.1:8010", help="Base API origin.")
    parser.add_argument("--run-id", default="live-2026-04-23-v1", help="Live run id.")
    parser.add_argument("--game-id", action="append", dest="game_ids", required=True, help="NBA game id. Repeat for multiple games.")
    parser.add_argument("--controller-name", default="controller_vnext_unified_v1 :: balanced")
    parser.add_argument("--fallback-controller-name", default="controller_vnext_deterministic_v1 :: tight")
    parser.add_argument("--execution-profile-version", default="v1")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--disable-entries", action="store_true")
    parser.add_argument("--poll-live-sec", type=float, default=5.0)
    parser.add_argument("--poll-idle-sec", type=float, default=15.0)
    parser.add_argument("--notes", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = {
        "run_id": args.run_id,
        "execution_profile_version": args.execution_profile_version,
        "controller_name": args.controller_name,
        "fallback_controller_name": args.fallback_controller_name,
        "game_ids": args.game_ids,
        "dry_run": bool(args.dry_run),
        "entries_enabled": not bool(args.disable_entries),
        "poll_interval_live_sec": float(args.poll_live_sec),
        "poll_interval_idle_sec": float(args.poll_idle_sec),
        "stop_loss_mode": "market_on_local_trigger",
        "notes": args.notes,
    }
    url = args.api_root.rstrip("/") + "/v1/nba/live/runs"
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to start live run: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
