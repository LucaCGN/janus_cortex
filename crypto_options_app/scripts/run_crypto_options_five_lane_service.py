from __future__ import annotations

"""Continuously run the issue #47 five-lane crypto-options live-test loop."""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crypto_options_app.pipelines.options.lane_system import (  # noqa: E402
    build_five_lane_comparison,
    build_unified_signal_router_design,
    render_five_lane_system_markdown,
)
from crypto_options_app.pipelines.options.live_micro_executor import load_json_object, write_json_object  # noqa: E402


LANE_DIRS = {
    "single_best_profile_market_follow": "lane1_single_best",
    "top_profile_consensus_market_follow": "lane2_consensus",
    "ev_quality_overlay_limit_hold": "lane3_ev_overlay",
    "dynamic_multi_signal_event_manager": "lane4_dynamic",
    "proprietary_algo_profile_confirmed": "lane5_algo_confirmed",
}


def run(args: argparse.Namespace) -> dict[str, Any]:
    run_root = Path(args.run_root).resolve()
    monitor_dir = Path(args.monitor_dir).resolve() if args.monitor_dir else run_root / "monitor-service"
    packets_dir = Path(args.packets_dir).resolve() if args.packets_dir else run_root / "packets"
    supervisor_dir = Path(args.supervisor_dir).resolve() if args.supervisor_dir else run_root / "supervisor-service"
    state_path = Path(args.state_path).resolve() if args.state_path else run_root / "five_lane_service_state.json"
    log_path = Path(args.log_path).resolve() if args.log_path else run_root / "five_lane_service_log.jsonl"
    if not args.disabled_lanes_file:
        args.disabled_lanes_file = str(run_root / "guard_disabled_lanes.json")
    stop_path = run_root / "STOP_FIVE_LANE_SERVICE"
    for path in (monitor_dir, packets_dir, supervisor_dir, state_path.parent, log_path.parent):
        path.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    service_started_at = datetime.now(timezone.utc).isoformat()
    tick_index = 0
    last_monitor_artifact = args.profile_report_cache
    final_payload: dict[str, Any] | None = None

    while True:
        tick_started = time.monotonic()
        tick_index += 1
        if stop_path.exists():
            final_payload = _state_payload(
                args=args,
                run_root=run_root,
                packets_dir=packets_dir,
                state="stopped",
                service_started_at=service_started_at,
                tick_index=tick_index,
                reason="stop_file_present",
            )
            _write_state(state_path, log_path, final_payload)
            break
        if args.duration_seconds > 0 and time.monotonic() - started >= float(args.duration_seconds):
            final_payload = _state_payload(
                args=args,
                run_root=run_root,
                packets_dir=packets_dir,
                state="stopped",
                service_started_at=service_started_at,
                tick_index=tick_index,
                reason="duration_elapsed",
            )
            _write_state(state_path, log_path, final_payload)
            break

        tick: dict[str, Any] = {
            "tick_index": tick_index,
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "run_root": str(run_root),
            "packets_dir": str(packets_dir),
        }
        try:
            reconcile = _run_supervisor(args, packets_dir=packets_dir, supervisor_dir=supervisor_dir, execute_live=False)
            tick["reconcile"] = _compact_supervisor(reconcile)
            if _global_stop_hit(reconcile):
                tick["state"] = "stopped"
                tick["reason"] = "global_or_lane_stop_after_reconcile"
                _write_state(state_path, log_path, _state_payload(args=args, run_root=run_root, packets_dir=packets_dir, state="stopped", service_started_at=service_started_at, tick_index=tick_index, latest_tick=tick))
                final_payload = _write_final_report(run_root=run_root, packets_dir=packets_dir, global_loss_stop_usd=args.global_loss_stop_usd)
                break

            monitor = _run_monitor(args, monitor_dir=monitor_dir, profile_report_cache=last_monitor_artifact)
            last_monitor_artifact = monitor.get("artifact_monitor") or last_monitor_artifact
            tick["monitor"] = monitor
            packet_build = _build_packets(args, monitor_artifact=str(last_monitor_artifact), packets_dir=packets_dir)
            tick["packet_build"] = packet_build
            supervisor = _run_supervisor(args, packets_dir=packets_dir, supervisor_dir=supervisor_dir, execute_live=bool(args.execute_live))
            tick["supervisor"] = _compact_supervisor(supervisor)
            tick["notifiable_events"] = supervisor.get("notifiable_events") or []
            tick["state"] = "running"
            if _all_lanes_finished(supervisor):
                tick["state"] = "stopped"
                tick["reason"] = "all_lanes_finished"
                final_payload = _write_final_report(run_root=run_root, packets_dir=packets_dir, global_loss_stop_usd=args.global_loss_stop_usd)
                tick["final_report"] = final_payload
                _write_state(state_path, log_path, _state_payload(args=args, run_root=run_root, packets_dir=packets_dir, state="stopped", service_started_at=service_started_at, tick_index=tick_index, latest_tick=tick))
                break
        except Exception as exc:  # noqa: BLE001
            tick["state"] = "error"
            tick["error"] = f"{type(exc).__name__}: {exc}"

        state = _state_payload(
            args=args,
            run_root=run_root,
            packets_dir=packets_dir,
            state=tick.get("state") or "running",
            service_started_at=service_started_at,
            tick_index=tick_index,
            latest_tick=tick,
        )
        _write_state(state_path, log_path, state)
        print(json.dumps({"tick_index": tick_index, "state": state["state"], "notifiable_events": tick.get("notifiable_events") or []}, sort_keys=True), flush=True)

        if args.once:
            final_payload = state
            break
        elapsed = time.monotonic() - tick_started
        time.sleep(max(0.25, float(args.interval_seconds) - elapsed))

    return final_payload or load_json_object(state_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a continuous five-lane crypto-options service loop.")
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--monitor-dir", default=None)
    parser.add_argument("--packets-dir", default=None)
    parser.add_argument("--supervisor-dir", default=None)
    parser.add_argument("--state-path", default=None)
    parser.add_argument("--log-path", default=None)
    parser.add_argument("--disabled-lanes-file", default=None)
    parser.add_argument("--profile-report-cache", default=None)
    parser.add_argument("--strategy-id", default="btc_eth_5m_mid_high")
    parser.add_argument("--obsidian-vault", default=None)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--page-limit", type=int, default=200)
    parser.add_argument("--max-signal-to-ask-slippage-cents", type=float, default=10.0)
    parser.add_argument("--max-spread", type=float, default=0.03)
    parser.add_argument("--min-depth-top3-ask-size", type=float, default=5.0)
    parser.add_argument("--min-time-remaining-seconds", type=float, default=20.0)
    parser.add_argument("--max-time-remaining-seconds", type=float, default=300.0)
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument("--duration-seconds", type=float, default=0.0)
    parser.add_argument("--lane-loss-stop-usd", type=float, default=10.0)
    parser.add_argument("--global-loss-stop-usd", type=float, default=50.0)
    parser.add_argument("--max-submitted-trades-per-lane", type=int, default=60)
    parser.add_argument("--positive-max-submitted-trades-per-lane", type=int, default=60)
    parser.add_argument("--max-executor-workers", type=int, default=5)
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--execution-approved", action="store_true")
    parser.add_argument("--acknowledge-live-risk", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = run(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"service_state={payload.get('state')}")
        print(f"state_path={payload.get('state_path')}")
        print(f"tick_index={payload.get('tick_index')}")
    return 0


def _run_monitor(args: argparse.Namespace, *, monitor_dir: Path, profile_report_cache: str | None) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "codex_tool" / "run_crypto_options_profile_signal_monitor.py"),
        "--strategy-id",
        str(args.strategy_id),
        "--obsidian-vault",
        str(args.obsidian_vault),
        "--output-dir",
        str(monitor_dir),
        "--max-workers",
        str(args.max_workers),
        "--page-limit",
        str(args.page_limit),
        "--max-signal-to-ask-slippage-cents",
        str(args.max_signal_to_ask_slippage_cents),
        "--max-spread",
        str(args.max_spread),
        "--min-depth-top3-ask-size",
        str(args.min_depth_top3_ask_size),
        "--min-time-remaining-seconds",
        str(args.min_time_remaining_seconds),
        "--max-time-remaining-seconds",
        str(args.max_time_remaining_seconds),
    ]
    if profile_report_cache:
        cmd.extend(["--profile-report-cache", str(profile_report_cache)])
    completed = _run_command(cmd, timeout=180)
    artifact = _extract_key(completed["stdout"], "artifact_monitor")
    return {**completed, "artifact_monitor": artifact}


def _build_packets(args: argparse.Namespace, *, monitor_artifact: str, packets_dir: Path) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "codex_tool" / "run_crypto_options_five_lane_system.py"),
        "--mode",
        "execution-packets",
        "--monitor-artifact",
        str(monitor_artifact),
        "--output-dir",
        str(packets_dir),
    ]
    for ledger in _lane_ledger_paths(packets_dir):
        cmd.extend(["--lane-ledger", str(ledger)])
    completed = _run_command(cmd, timeout=60)
    return {
        **completed,
        "artifact_json": _extract_key(completed["stdout"], "artifact_json"),
        "ready_packets": _extract_key(completed["stdout"], "ready_packets"),
    }


def _run_supervisor(args: argparse.Namespace, *, packets_dir: Path, supervisor_dir: Path, execute_live: bool) -> dict[str, Any]:
    packet_bundle = packets_dir / "packets.json"
    if not packet_bundle.exists():
        return {"schema_version": "crypto_options_five_lane_supervisor_tick_v1", "skipped": True, "reason": "packet_bundle_missing", "lane_rows": [], "executions": [], "notifiable_events": []}
    cmd = [
        sys.executable,
        str(REPO_ROOT / "codex_tool" / "run_crypto_options_five_lane_supervisor.py"),
        "--packet-bundle",
        str(packet_bundle),
        "--output-dir",
        str(supervisor_dir),
        "--lane-loss-stop-usd",
        str(args.lane_loss_stop_usd),
        "--global-loss-stop-usd",
        str(args.global_loss_stop_usd),
        "--max-submitted-trades",
        "0",
        "--max-submitted-trades-per-lane",
        str(args.max_submitted_trades_per_lane),
        "--positive-max-submitted-trades-per-lane",
        str(args.positive_max_submitted_trades_per_lane),
        "--max-executor-workers",
        str(args.max_executor_workers),
        "--json",
    ]
    if args.disabled_lanes_file:
        cmd.extend(["--disabled-lanes-file", str(args.disabled_lanes_file)])
    if execute_live:
        cmd.append("--execute-live")
    if execute_live and args.execution_approved:
        cmd.append("--execution-approved")
    if execute_live and args.acknowledge_live_risk:
        cmd.append("--acknowledge-live-risk")
    completed = _run_command(cmd, timeout=240)
    try:
        return json.loads(completed["stdout"])
    except json.JSONDecodeError:
        return {**completed, "schema_version": "crypto_options_five_lane_supervisor_tick_v1", "json_parse_error": True, "lane_rows": [], "executions": [], "notifiable_events": ["unexpected_traceback"]}


def _run_command(cmd: list[str], *, timeout: int) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True, timeout=timeout, check=False)
    return {
        "returncode": completed.returncode,
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "stdout": completed.stdout,
        "stderr_tail": completed.stderr[-2000:] if completed.stderr else "",
        "cmd": cmd,
    }


def _lane_ledger_paths(packets_dir: Path) -> list[Path]:
    paths = []
    for lane_dir in LANE_DIRS.values():
        path = packets_dir / lane_dir / "live_execution_ledger.json"
        if path.exists():
            paths.append(path)
    return paths


def _compact_supervisor(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "skipped": payload.get("skipped"),
        "reason": payload.get("reason"),
        "artifact_json": payload.get("artifact_json"),
        "global_realized_pnl_usd": payload.get("global_realized_pnl_usd"),
        "global_loss_stop_hit": payload.get("global_loss_stop_hit"),
        "submitted_count_before_tick": payload.get("submitted_count_before_tick"),
        "executions": [
            {
                "lane_id": row.get("lane_id"),
                "status": row.get("status"),
                "order_submission_attempted": row.get("order_submission_attempted"),
                "blockers": row.get("blockers") or [],
            }
            for row in payload.get("executions") or []
            if isinstance(row, dict)
        ],
        "lanes": [
            {
                "lane_id": row.get("lane_id"),
                "status": row.get("status"),
                "risk_state": row.get("risk_state"),
                "supervisor_blockers": row.get("supervisor_blockers") or [],
                "packet_blockers": row.get("packet_blockers") or [],
            }
            for row in payload.get("lane_rows") or []
            if isinstance(row, dict)
        ],
        "notifiable_events": payload.get("notifiable_events") or [],
    }


def _global_stop_hit(payload: dict[str, Any]) -> bool:
    if payload.get("global_loss_stop_hit"):
        return True
    for row in payload.get("lane_rows") or []:
        if not isinstance(row, dict):
            continue
        if "lane_loss_stop_hit" in (row.get("supervisor_blockers") or []):
            return True
    return False


def _all_lanes_finished(payload: dict[str, Any]) -> bool:
    rows = [row for row in payload.get("lane_rows") or [] if isinstance(row, dict)]
    if len(rows) < len(LANE_DIRS):
        return False
    finish_blockers = {"lane_loss_stop_hit", "lane_max_submitted_trades_hit", "lane_positive_max_submitted_trades_hit"}
    for row in rows:
        blockers = {str(item) for item in row.get("supervisor_blockers") or []}
        if not blockers.intersection(finish_blockers):
            return False
    return True


def _write_final_report(*, run_root: Path, packets_dir: Path, global_loss_stop_usd: float) -> dict[str, Any]:
    ledgers = {}
    for path in _lane_ledger_paths(packets_dir):
        ledger = load_json_object(path)
        ledgers[str(ledger.get("lane_id") or path.parent.name)] = ledger
    comparison = build_five_lane_comparison(ledgers, global_loss_stop_usd=global_loss_stop_usd)
    router = build_unified_signal_router_design(comparison)
    final = {
        "schema_version": "crypto_options_five_lane_60trade_final_report_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "lane_comparison": comparison,
        "unified_signal_router_design": router,
    }
    final_dir = run_root / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    json_path = final_dir / "five_lane_60trade_final_report.json"
    md_path = final_dir / "five_lane_60trade_final_report.md"
    write_json_object(final, json_path)
    md_path.write_text(_render_final_markdown(final), encoding="utf-8")
    final["artifact_json"] = str(json_path)
    final["artifact_markdown"] = str(md_path)
    return final


def _render_final_markdown(final: dict[str, Any]) -> str:
    comparison = final.get("lane_comparison") or {}
    lines = [
        "# Five-Lane 60-Trade Live Test Report",
        "",
        f"- Generated: `{final.get('generated_at_utc')}`",
        f"- Global realized PnL: `{comparison.get('global_net_realized_pnl_usd')}`",
        "",
        "| Lane | Status | Trades | PnL | Win Rate | Decision |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for lane in comparison.get("lanes") or []:
        metrics = lane.get("trade_metrics") or {}
        decision = lane.get("promotion_decision") or {}
        lines.append(
            "| {lane} | {status} | {trades} | {pnl} | {win_rate} | {decision} |".format(
                lane=lane.get("lane_id"),
                status=lane.get("status"),
                trades=metrics.get("trade_count"),
                pnl=metrics.get("return_sum"),
                win_rate=metrics.get("win_rate"),
                decision=decision.get("decision"),
            )
        )
    lines.extend(["", "## Router Design", "", "```json", json.dumps(final.get("unified_signal_router_design") or {}, indent=2, sort_keys=True), "```", ""])
    return "\n".join(lines)


def _state_payload(
    *,
    args: argparse.Namespace,
    run_root: Path,
    packets_dir: Path,
    state: str,
    service_started_at: str,
    tick_index: int,
    reason: str | None = None,
    latest_tick: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "crypto_options_five_lane_service_state_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "service_started_at_utc": service_started_at,
        "state": state,
        "reason": reason,
        "tick_index": tick_index,
        "run_root": str(run_root),
        "packets_dir": str(packets_dir),
        "state_path": str(Path(args.state_path).resolve()) if args.state_path else str(run_root / "five_lane_service_state.json"),
        "disabled_lanes_file": str(Path(args.disabled_lanes_file).resolve()) if args.disabled_lanes_file else str(run_root / "guard_disabled_lanes.json"),
        "interval_seconds": float(args.interval_seconds),
        "live_execution_requested": bool(args.execute_live),
        "latest_tick": latest_tick or {},
    }


def _write_state(state_path: Path, log_path: Path, payload: dict[str, Any]) -> None:
    write_json_object(payload, state_path)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


def _extract_key(stdout: str, key: str) -> str | None:
    prefix = f"{key}="
    for line in stdout.splitlines():
        if line.startswith(prefix):
            value = line[len(prefix) :].strip()
            return value or None
    return None


if __name__ == "__main__":
    raise SystemExit(main())
