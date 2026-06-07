from __future__ import annotations

"""Submit at most one gated crypto-options live micro-test order.

This command is deliberately separate from the read-only monitor. It only
submits when the approved protocol, current monitor, explicit live flags, local
ledger, and Polymarket credentials all agree that one 5-share FOK ticket is
allowed.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.pipelines.crypto.options.live_micro_executor import (  # noqa: E402
    LiveExecutorApproval,
    build_live_micro_execution_tick,
    latest_monitor_artifact,
    load_json_object,
    submit_live_micro_order,
    write_json_object,
)


def run(args: argparse.Namespace) -> dict[str, Any]:
    loop_dir = Path(args.loop_dir).resolve() if args.loop_dir else None
    state = _load_loop_state(loop_dir)
    protocol_path = _resolve_path(args.protocol_artifact or state.get("protocol_artifact"), loop_dir=loop_dir)
    monitor_path = _resolve_monitor_path(args.monitor_artifact or state.get("last_monitor_artifact"), loop_dir=loop_dir)
    ledger_path = (
        _resolve_path(args.ledger_artifact or state.get("ledger_artifact"), loop_dir=loop_dir)
        if args.ledger_artifact or state.get("ledger_artifact")
        else _default_ledger_path(loop_dir)
    )
    output_dir = Path(args.output_dir).resolve() if args.output_dir else (loop_dir or Path.cwd())
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact_path = output_dir / f"exec_{stamp}.json"
    _preflight_writable_path(artifact_path)
    if ledger_path:
        _preflight_writable_path(ledger_path)

    if protocol_path is None:
        raise FileNotFoundError("Missing --protocol-artifact and no protocol_artifact in manual_loop_state.json")
    if monitor_path is None:
        raise FileNotFoundError("Missing --monitor-artifact and no monitor artifact found in loop directory")

    protocol_payload = load_json_object(protocol_path)
    monitor_payload = load_json_object(monitor_path)
    ledger_payload = load_json_object(ledger_path) if ledger_path and ledger_path.exists() else None
    approval = LiveExecutorApproval(
        execute_live=bool(args.execute_live),
        execution_approved=bool(args.execution_approved),
        acknowledge_live_risk=bool(args.acknowledge_live_risk),
        operator=args.operator,
        reason=args.reason,
        order_type=args.order_type,
        price_slippage_cents=float(args.price_slippage_cents),
        execution_style=args.execution_style,
        execution_side=args.execution_side,
    )
    submitter = submit_live_micro_order if args.execute_live else None
    payload = build_live_micro_execution_tick(
        protocol_payload=protocol_payload,
        monitor_payload=monitor_payload,
        ledger_payload=ledger_payload,
        approval=approval,
        submitter=submitter,
    )
    payload["inputs"] = {
        "loop_dir": str(loop_dir) if loop_dir else None,
        "protocol_artifact": str(protocol_path),
        "monitor_artifact": str(monitor_path),
        "ledger_artifact": str(ledger_path) if ledger_path else None,
    }
    payload["artifact_json"] = str(artifact_path)

    if ledger_path:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        write_json_object(payload["ledger"], ledger_path)
        payload["ledger_written"] = str(ledger_path)
    write_json_object(payload, artifact_path)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one gated crypto-options live micro-test execution tick.")
    parser.add_argument("--loop-dir", default=None, help="Manual/live loop artifact directory.")
    parser.add_argument("--protocol-artifact", default=None, help="Approved micro-test protocol JSON artifact.")
    parser.add_argument("--monitor-artifact", default=None, help="Current micro-test monitor JSON artifact.")
    parser.add_argument("--ledger-artifact", default=None, help="Live execution ledger JSON artifact.")
    parser.add_argument("--output-dir", default=None, help="Output directory for execution artifacts.")
    parser.add_argument("--execute-live", action="store_true", help="Allow the command to call the live CLOB submitter.")
    parser.add_argument("--execution-approved", action="store_true", help="Explicit approval flag for this live micro-test tick.")
    parser.add_argument("--acknowledge-live-risk", action="store_true", help="Acknowledge real-money execution risk.")
    parser.add_argument("--operator", default=None, help="Human/operator identifier for the live execution ledger.")
    parser.add_argument("--reason", default=None, help="Short reason for the live execution attempt.")
    parser.add_argument("--order-type", choices=["FOK", "FAK", "GTC", "GTD"], default="FOK", help="CLOB order type for the live ticket.")
    parser.add_argument(
        "--execution-side",
        choices=["BUY", "SELL"],
        default="BUY",
        help="BUY opens the selected outcome through ask-side JIT checks; SELL exits a matching held token through bid-side JIT checks.",
    )
    parser.add_argument(
        "--execution-style",
        choices=["limit", "market"],
        default="limit",
        help="Use market for CLOB market buy/sell triggers, otherwise marketable limit.",
    )
    parser.add_argument(
        "--price-slippage-cents",
        type=float,
        default=0.0,
        help="Maximum cents added to the observed ask for the submitted limit price.",
    )
    parser.add_argument("--watch", action="store_true", help="Watch a loop directory and process each new monitor artifact once.")
    parser.add_argument("--poll-seconds", type=float, default=1.0, help="Watch-mode polling interval.")
    parser.add_argument("--max-watch-seconds", type=float, default=900.0, help="Maximum watch-mode runtime before exiting.")
    parser.add_argument(
        "--continue-after-submit",
        action="store_true",
        help="Keep watching after a submitted order. The live ledger open-position gate still blocks new entries.",
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON payload.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.watch:
        payload = run_watch(args)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        else:
            print(f"watch_status={payload.get('watch_status')}")
            print(f"processed_monitor_count={payload.get('processed_monitor_count')}")
            print(f"last_execution_status={(payload.get('last_execution') or {}).get('status')}")
            print(f"last_blockers={(payload.get('last_execution') or {}).get('blockers')}")
            print(f"last_artifact={(payload.get('last_execution') or {}).get('artifact_json')}")
        return 0
    payload = run(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        selected = payload.get("selected_candidate") or {}
        print(f"status={payload.get('status')}")
        print(f"orders_allowed={payload.get('orders_allowed')}")
        print(f"order_submission_attempted={payload.get('order_submission_attempted')}")
        print(f"event_slug={selected.get('event_slug')}")
        print(f"outcome={selected.get('outcome')}")
        print(f"ask={selected.get('best_ask')}")
        print(f"cost={selected.get('min_order_total_cost')}")
        print(f"blockers={payload.get('blockers')}")
        print(f"artifact_json={payload.get('artifact_json')}")
    return 0


def run_watch(args: argparse.Namespace) -> dict[str, Any]:
    if not args.loop_dir:
        raise ValueError("--watch requires --loop-dir")
    loop_dir = Path(args.loop_dir).resolve()
    started = time.monotonic()
    processed: set[str] = set()
    last_payload: dict[str, Any] | None = None
    stop_reason = "max_watch_seconds_reached"
    while time.monotonic() - started <= float(args.max_watch_seconds):
        state = _load_loop_state(loop_dir)
        monitor_path = _resolve_monitor_path(args.monitor_artifact or state.get("last_monitor_artifact"), loop_dir=loop_dir)
        if monitor_path is not None:
            monitor_key = str(monitor_path)
            if monitor_key not in processed:
                processed.add(monitor_key)
                last_payload = run(args)
                status = str(last_payload.get("status") or "")
                blockers = set(str(item) for item in last_payload.get("blockers") or [])
                print(
                    "live_executor_tick "
                    f"status={status} monitor={monitor_path.name} "
                    f"candidate_count={last_payload.get('candidate_count')} blockers={sorted(blockers)}",
                    flush=True,
                )
                if status == "submitted" and not args.continue_after_submit:
                    stop_reason = "submitted_order"
                    break
                if status == "submit_error" and not args.continue_after_submit:
                    stop_reason = "submit_error"
                    break
                if "polymarket_execution_credentials_missing" in blockers:
                    stop_reason = "credentials_missing"
                    break
                if "open_position_requires_reconciliation" in blockers and not args.continue_after_submit:
                    stop_reason = "open_position_requires_reconciliation"
                    break
        time.sleep(max(0.1, float(args.poll_seconds)))
    return {
        "schema_version": "crypto_options_live_micro_executor_watch_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "watch_status": "stopped",
        "stop_reason": stop_reason,
        "loop_dir": str(loop_dir),
        "processed_monitor_count": len(processed),
        "last_execution": last_payload,
    }


def _load_loop_state(loop_dir: Path | None) -> dict[str, Any]:
    if loop_dir is None:
        return {}
    path = loop_dir / "manual_loop_state.json"
    if not path.exists():
        return {}
    return load_json_object(path)


def _preflight_writable_path(path: Path) -> None:
    """Fail before live submission if the final artifact/ledger path is not writable."""

    path.parent.mkdir(parents=True, exist_ok=True)
    probe = path.parent / ".write_test"
    probe.write_text("preflight", encoding="utf-8")
    probe.unlink(missing_ok=True)


def _resolve_monitor_path(raw: str | None, *, loop_dir: Path | None) -> Path | None:
    path = _resolve_path(raw, loop_dir=loop_dir)
    if path and path.exists():
        return path
    if loop_dir is None:
        return None
    return latest_monitor_artifact(loop_dir)


def _resolve_path(raw: str | None, *, loop_dir: Path | None) -> Path | None:
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute() and loop_dir is not None:
        path = loop_dir / path
    return path.resolve()


def _default_ledger_path(loop_dir: Path | None) -> Path | None:
    if loop_dir is None:
        return None
    return loop_dir / "live_execution_ledger.json"


if __name__ == "__main__":
    raise SystemExit(main())
