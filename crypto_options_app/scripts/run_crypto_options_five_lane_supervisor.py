from __future__ import annotations

"""Guard and run five crypto-options lane packets through the live executor."""

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.pipelines.crypto.options.live_micro_executor import (  # noqa: E402
    load_json_object,
    reconcile_live_execution_ledger,
    write_json_object,
)


def run(args: argparse.Namespace) -> dict[str, Any]:
    bundle_path = Path(args.packet_bundle).resolve()
    bundle = load_json_object(bundle_path)
    written_packets = [row for row in bundle.get("written_packets") or [] if isinstance(row, dict)]
    output_dir = Path(args.output_dir).resolve() if args.output_dir else bundle_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    disabled_lanes = _load_disabled_lanes(args.disabled_lanes_file)

    lane_rows = [_lane_supervisor_row(row, args=args, disabled_lanes=disabled_lanes) for row in written_packets]
    global_net = sum(float((row.get("risk_state") or {}).get("realized_pnl_net_usd") or 0.0) for row in lane_rows)
    submitted_count = sum(int((row.get("risk_state") or {}).get("submitted_count") or 0) for row in lane_rows)
    global_loss_hit = max(0.0, -global_net) >= float(args.global_loss_stop_usd)
    max_trades = int(args.max_submitted_trades)
    max_trades_hit = max_trades > 0 and submitted_count >= max_trades

    executions = []
    for row in lane_rows:
        status = str(row.get("status") or "")
        if global_loss_hit:
            row["supervisor_blockers"].append("global_loss_stop_hit")
        if max_trades_hit:
            row["supervisor_blockers"].append("max_submitted_trades_hit")
        if not args.execute_live:
            row["supervisor_blockers"].append("execute_live_not_requested")
        elif not (args.execution_approved and args.acknowledge_live_risk):
            row["supervisor_blockers"].append("explicit_live_flags_incomplete")
        if status != "ready":
            row["supervisor_blockers"].append("lane_packet_not_ready")
    executable_rows = [row for row in lane_rows if not row["supervisor_blockers"]]
    if max_trades > 0:
        remaining = max(0, max_trades - submitted_count)
        executable_rows = executable_rows[:remaining]
    if executable_rows:
        workers = max(1, min(int(args.max_executor_workers), len(executable_rows)))
        if workers == 1:
            executions = [_run_isolated_executor(row, args=args) for row in executable_rows]
        else:
            by_index: dict[int, dict[str, Any]] = {}
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_run_isolated_executor, row, args=args): index for index, row in enumerate(executable_rows)}
                for future in as_completed(futures):
                    by_index[futures[future]] = future.result()
            executions = [by_index[index] for index in sorted(by_index)]

    payload = {
        "schema_version": "crypto_options_five_lane_supervisor_tick_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "packet_bundle": str(bundle_path),
        "execute_live_requested": bool(args.execute_live),
        "explicit_live_flags_complete": bool(args.execute_live and args.execution_approved and args.acknowledge_live_risk),
        "global_loss_stop_usd": float(args.global_loss_stop_usd),
        "lane_loss_stop_usd": float(args.lane_loss_stop_usd),
        "max_submitted_trades": int(args.max_submitted_trades),
        "max_submitted_trades_per_lane": int(args.max_submitted_trades_per_lane),
        "positive_max_submitted_trades_per_lane": int(args.positive_max_submitted_trades_per_lane),
        "max_executor_workers": int(args.max_executor_workers),
        "executor_dispatch_mode": "parallel" if int(args.max_executor_workers) > 1 else "serial",
        "disabled_lanes_file": str(Path(args.disabled_lanes_file).resolve()) if args.disabled_lanes_file else None,
        "disabled_lanes": sorted(disabled_lanes),
        "global_realized_pnl_usd": round(global_net, 6),
        "global_loss_stop_hit": global_loss_hit,
        "submitted_count_before_tick": sum(int((row.get("risk_state") or {}).get("submitted_count") or 0) for row in lane_rows),
        "lane_rows": lane_rows,
        "executions": executions,
        "notifiable_events": _notifiable_events(lane_rows, executions, global_loss_hit=global_loss_hit),
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact_path = output_dir / f"crypto_options_five_lane_supervisor_tick_{stamp}.json"
    write_json_object(payload, artifact_path)
    payload["artifact_json"] = str(artifact_path)
    write_json_object(payload, artifact_path)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guard and run ready five-lane packets through the isolated live executor.")
    parser.add_argument("--packet-bundle", required=True, help="crypto_options_lane_execution_packets_*.json artifact.")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--lane-loss-stop-usd", type=float, default=10.0)
    parser.add_argument("--global-loss-stop-usd", type=float, default=50.0)
    parser.add_argument("--max-submitted-trades", type=int, default=0, help="Optional global submitted-trade cap. Use 0 to disable.")
    parser.add_argument("--max-submitted-trades-per-lane", type=int, default=36)
    parser.add_argument("--positive-max-submitted-trades-per-lane", type=int, default=60)
    parser.add_argument("--max-executor-workers", type=int, default=5)
    parser.add_argument("--disabled-lanes-file", default=None, help="Optional guard-owned JSON file listing lane_ids blocked from live submission.")
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--execution-approved", action="store_true")
    parser.add_argument("--acknowledge-live-risk", action="store_true")
    parser.add_argument("--operator-prefix", default="codex-issue47")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"supervisor_status=complete")
        print(f"executions={len(payload.get('executions') or [])}")
        print(f"notifiable_events={','.join(payload.get('notifiable_events') or [])}")
        print(f"artifact_json={payload.get('artifact_json')}")
    return 0


def _lane_supervisor_row(packet: dict[str, Any], *, args: argparse.Namespace, disabled_lanes: set[str] | None = None) -> dict[str, Any]:
    loop_dir = Path(str(packet.get("loop_dir") or "")).resolve()
    ledger_path = Path(str(packet.get("ledger") or loop_dir / "live_execution_ledger.json")).resolve()
    ledger = load_json_object(ledger_path) if ledger_path.exists() else {"entries": []}
    reconciled_ledger = reconcile_live_execution_ledger(ledger)
    if reconciled_ledger != ledger:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_object(reconciled_ledger, ledger_path)
        ledger = reconciled_ledger
    risk = _live_ledger_risk(ledger)
    blockers: list[str] = []
    lane_id = str(packet.get("lane_id") or "")
    if lane_id in (disabled_lanes or set()):
        blockers.append("lane_guard_disabled")
    if risk["relative_realized_loss_usd"] >= float(args.lane_loss_stop_usd):
        blockers.append("lane_loss_stop_hit")
    cap_blocker = _lane_submission_cap_blocker(risk, args=args)
    if cap_blocker:
        blockers.append(cap_blocker)
    return {
        "lane_id": packet.get("lane_id"),
        "status": packet.get("status"),
        "loop_dir": str(loop_dir),
        "protocol": str(Path(str(packet.get("protocol") or "")).resolve()) if packet.get("protocol") else None,
        "monitor": str(Path(str(packet.get("monitor") or "")).resolve()) if packet.get("monitor") else None,
        "ledger": str(ledger_path),
        "risk_state": risk,
        "supervisor_blockers": blockers,
        "packet_blockers": packet.get("blockers") or [],
        "executor_command": packet.get("executor_command"),
    }


def _lane_submission_cap_blocker(risk: dict[str, Any], *, args: argparse.Namespace) -> str | None:
    submitted = int(risk.get("submitted_count") or 0)
    net = float(risk.get("realized_pnl_net_usd") or 0.0)
    base_cap = int(args.max_submitted_trades_per_lane)
    positive_cap = int(args.positive_max_submitted_trades_per_lane)
    if positive_cap < base_cap:
        positive_cap = base_cap
    if base_cap > 0 and submitted >= base_cap and net <= 0.0:
        return "lane_max_submitted_trades_hit"
    if positive_cap > 0 and submitted >= positive_cap:
        return "lane_positive_max_submitted_trades_hit"
    return None


def _load_disabled_lanes(path: str | None) -> set[str]:
    if not path:
        return set()
    guard_path = Path(path).resolve()
    if not guard_path.exists():
        return set()
    payload = load_json_object(guard_path)
    if isinstance(payload, list):
        return {str(item) for item in payload if item}
    disabled: set[str] = set()
    if isinstance(payload, dict):
        for item in payload.get("disabled_lanes") or []:
            if item:
                disabled.add(str(item))
        lanes = payload.get("lanes")
        if isinstance(lanes, dict):
            for lane_id, row in lanes.items():
                if isinstance(row, dict) and row.get("disabled"):
                    disabled.add(str(lane_id))
                elif row is True:
                    disabled.add(str(lane_id))
    return disabled


def _live_ledger_risk(ledger: dict[str, Any]) -> dict[str, Any]:
    entries = [row for row in ledger.get("entries") or [] if isinstance(row, dict)]
    submitted = [row for row in entries if row.get("status") == "submitted" or row.get("settlement_status") == "open_requires_reconciliation"]
    realized = [row for row in entries if row.get("realized_pnl_net_usd") is not None]
    net = sum(float(row.get("realized_pnl_net_usd") or 0.0) for row in realized)
    return {
        "entry_count": len(entries),
        "submitted_count": len(submitted),
        "realized_count": len(realized),
        "open_position_count": sum(1 for row in entries if row.get("settlement_status") == "open_requires_reconciliation"),
        "realized_pnl_net_usd": round(net, 6),
        "relative_realized_loss_usd": round(max(0.0, -net), 6),
    }


def _run_isolated_executor(row: dict[str, Any], *, args: argparse.Namespace) -> dict[str, Any]:
    lane_id = str(row.get("lane_id") or "unknown_lane")
    cmd = [
        sys.executable,
        str(REPO_ROOT / "codex_tool" / "run_crypto_options_live_micro_executor.py"),
        "--loop-dir",
        str(row.get("loop_dir")),
        "--protocol-artifact",
        str(row.get("protocol")),
        "--monitor-artifact",
        str(row.get("monitor")),
        "--ledger-artifact",
        str(row.get("ledger")),
        "--output-dir",
        str(row.get("loop_dir")),
        "--order-type",
        "FAK",
        "--execution-style",
        _execution_style_from_template(str(row.get("executor_command") or "")),
        "--execution-side",
        "BUY",
        "--price-slippage-cents",
        _slippage_from_template(str(row.get("executor_command") or "")),
        "--operator",
        f"{args.operator_prefix}-{lane_id}",
        "--reason",
        f"issue47_{lane_id}_supervised_10usd_lane_test",
        "--json",
    ]
    if args.execute_live:
        cmd.append("--execute-live")
    if args.execution_approved:
        cmd.append("--execution-approved")
    if args.acknowledge_live_risk:
        cmd.append("--acknowledge-live-risk")
    completed = subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True, timeout=120, check=False)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {}
    return {
        "lane_id": lane_id,
        "returncode": completed.returncode,
        "status": payload.get("status") or ("executor_error" if completed.returncode else "unknown"),
        "order_submission_attempted": bool(payload.get("order_submission_attempted")),
        "artifact_json": payload.get("artifact_json"),
        "blockers": payload.get("blockers") or [],
        "stderr_tail": completed.stderr[-1000:] if completed.stderr else "",
        "stdout_json": payload,
    }


def _execution_style_from_template(template: str) -> str:
    return "market" if "--execution-style market" in template else "limit"


def _slippage_from_template(template: str) -> str:
    parts = template.split()
    if "--price-slippage-cents" in parts:
        idx = parts.index("--price-slippage-cents")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return "0"


def _notifiable_events(lane_rows: list[dict[str, Any]], executions: list[dict[str, Any]], *, global_loss_hit: bool) -> list[str]:
    events: list[str] = []
    if global_loss_hit:
        events.append("global_loss_stop_hit")
    for row in lane_rows:
        if "lane_loss_stop_hit" in (row.get("supervisor_blockers") or []):
            events.append(f"lane_loss_stop_hit:{row.get('lane_id')}")
        if "lane_max_submitted_trades_hit" in (row.get("supervisor_blockers") or []):
            events.append(f"lane_max_submitted_trades_hit:{row.get('lane_id')}")
        if "lane_positive_max_submitted_trades_hit" in (row.get("supervisor_blockers") or []):
            events.append(f"lane_positive_max_submitted_trades_hit:{row.get('lane_id')}")
    for execution in executions:
        status = str(execution.get("status") or "")
        blockers = {str(item) for item in execution.get("blockers") or []}
        if status == "submitted":
            events.append(f"submitted:{execution.get('lane_id')}")
        if status == "submit_error":
            events.append(f"submit_error:{execution.get('lane_id')}")
        if "polymarket_execution_credentials_missing" in blockers:
            events.append(f"credentials_missing:{execution.get('lane_id')}")
    return events


if __name__ == "__main__":
    raise SystemExit(main())
