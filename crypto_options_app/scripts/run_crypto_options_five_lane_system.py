from __future__ import annotations

"""Build the issue #47 five-lane crypto-options profile aggregation package."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.pipelines.options.lane_system import (  # noqa: E402
    build_five_lane_comparison,
    build_five_lane_system_package,
    build_lane_execution_packets,
    build_order_sizing_matrix,
    default_five_lane_configs,
    write_lane_execution_packets,
    write_five_lane_system_artifacts,
)
from crypto_options_app.pipelines.options.live_micro_executor import load_json_object  # noqa: E402


def run(args: argparse.Namespace) -> dict[str, Any]:
    now = _parse_now(args.now_utc)
    if args.mode == "order-sizing-matrix":
        payload = build_order_sizing_matrix(generated_at=now)
        payload["artifacts"] = _write_standalone(payload, prefix="crypto_options_order_sizing_matrix", output_dir=args.output_dir)
        return payload
    if args.mode == "lane-configs":
        payload = {
            "schema_version": "crypto_options_five_lane_configs_v1",
            "generated_at_utc": (now or datetime.now(timezone.utc)).isoformat(),
            "lane_configs": default_five_lane_configs(),
        }
        payload["artifacts"] = _write_standalone(payload, prefix="crypto_options_five_lane_configs", output_dir=args.output_dir)
        return payload
    if args.mode == "compare-ledgers":
        ledgers = _load_lane_ledgers(args.lane_ledger)
        payload = build_five_lane_comparison(ledgers)
        payload["artifacts"] = _write_standalone(payload, prefix="crypto_options_five_lane_comparison", output_dir=args.output_dir)
        return payload
    if args.mode == "execution-packets" and args.five_lane_package:
        package = load_json_object(args.five_lane_package)
        payload = write_lane_execution_packets(package, output_dir=args.output_dir, now=now)
        return payload
    source_report_path = args.profile_report or args.monitor_artifact
    if not source_report_path:
        raise ValueError("--profile-report or --monitor-artifact is required for mode=package")
    profile_report = load_json_object(source_report_path)
    lane_ledgers = _load_lane_ledgers(args.lane_ledger)
    algo_signals = _load_json_list(args.algo_signals)
    payload = build_five_lane_system_package(
        profile_report,
        lane_ledgers=lane_ledgers,
        algo_signals=algo_signals,
        now=now,
    )
    if args.mode == "execution-packets":
        return write_lane_execution_packets(payload, output_dir=args.output_dir, now=now)
    artifacts = write_five_lane_system_artifacts(payload, output_dir=args.output_dir)
    payload["artifacts"] = artifacts
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build crypto-options five-lane system artifacts.")
    parser.add_argument(
        "--mode",
        choices=["package", "order-sizing-matrix", "lane-configs", "compare-ledgers", "execution-packets"],
        default="package",
        help="Artifact package to build.",
    )
    parser.add_argument("--profile-report", default=None, help="Profile signal report JSON from run_crypto_options_profile_signals.py.")
    parser.add_argument("--monitor-artifact", default=None, help="Profile signal monitor JSON containing current quoted candidates.")
    parser.add_argument("--five-lane-package", default=None, help="Existing five-lane package JSON for execution-packets mode.")
    parser.add_argument(
        "--lane-ledger",
        action="append",
        default=[],
        help="Lane ledger JSON path. Repeatable. Ledger lane_id keys are used for comparison.",
    )
    parser.add_argument("--algo-signals", default=None, help="Optional JSON list of deterministic algo signals.")
    parser.add_argument("--now-utc", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--json", action="store_true", help="Print full JSON payload.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"schema_version={payload.get('schema_version')}")
        print(f"artifact_json={payload.get('artifact_json') or (payload.get('artifacts') or {}).get('json')}")
        print(f"artifact_markdown={(payload.get('artifacts') or {}).get('markdown')}")
        if "lane_decision_report" in payload:
            decisions = (payload.get("lane_decision_report") or {}).get("decisions") or []
            print(f"lane_decision_count={len(decisions)}")
            print(f"accepted_lanes={','.join(row.get('lane_id') for row in decisions if row.get('status') == 'accepted')}")
        if "written_packets" in payload:
            packets = payload.get("written_packets") or []
            print(f"written_packet_count={len(packets)}")
            print(f"ready_packets={','.join(row.get('lane_id') for row in packets if row.get('status') == 'ready')}")
    return 0


def _load_lane_ledgers(paths: list[str] | None) -> dict[str, dict[str, Any]]:
    ledgers: dict[str, dict[str, Any]] = {}
    for raw in paths or []:
        ledger = load_json_object(raw)
        lane_id = str(ledger.get("lane_id") or Path(raw).stem.removeprefix("lane_ledger_"))
        ledgers[lane_id] = ledger
    return ledgers


def _load_json_list(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError("--algo-signals must point to a JSON list")
    return [dict(row) for row in payload if isinstance(row, dict)]


def _write_standalone(payload: dict[str, Any], *, prefix: str, output_dir: str | None) -> dict[str, str]:
    generated = _parse_now(payload.get("generated_at_utc")) or datetime.now(timezone.utc)
    root = Path(output_dir) if output_dir else Path.cwd()
    root.mkdir(parents=True, exist_ok=True)
    stamp = generated.strftime("%Y%m%dT%H%M%SZ")
    path = root / f"{prefix}_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return {"json": str(path)}


def _parse_now(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
