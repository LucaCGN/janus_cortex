from __future__ import annotations

"""Continuously run the Crypto Options V2 supervised candidate service.

The service owns cadence for V2 candidate packet generation and dispatches only
through the supervised live micro-executor compatibility wrapper. Heartbeats
should guard/report this process; they must not submit orders directly.
"""

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.data.nodes.polymarket.blockchain.manage_portfolio import PolymarketCredentials  # noqa: E402
from app.data.pipelines.crypto.options.live_micro_executor import (  # noqa: E402
    load_json_object,
    reconcile_live_execution_ledger,
    write_json_object,
)
from app.data.pipelines.crypto.options.live_micro_executor import parse_clob_orderbook_quote  # noqa: E402
from app.data.pipelines.crypto.options.v2_service import (  # noqa: E402
    build_v2_decision_set,
    build_v2_execution_packet_bundle,
    evaluate_v2_promotion_states,
    load_v2_candidate_ledgers,
    write_v2_execution_packets,
)
from app.data.pipelines.crypto.options.v2_candidates import (  # noqa: E402
    default_v2_candidate_configs,
    default_v3_candidate_configs,
    default_v3_validation_candidate_configs,
    default_v4_candidate_configs,
)
from app.services.crypto_options.v3_policy import default_v3_budget_policy, evaluate_v3_component_state  # noqa: E402
from app.services.crypto_options.v3_policy import classify_quote_health  # noqa: E402

_CASHOUT_SHARE_COVERAGE_EPSILON = 0.01


def _load_py_clob_runtime() -> dict[str, Any]:
    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import ApiCreds
        from py_clob_client_v2.constants import POLYGON
    except ModuleNotFoundError as exc:
        raise RuntimeError("py_clob_client_v2_missing") from exc
    return {"ApiCreds": ApiCreds, "ClobClient": ClobClient, "POLYGON": POLYGON}


def run(args: argparse.Namespace) -> dict[str, Any]:
    run_root = Path(args.run_root).resolve()
    monitor_dir = Path(args.monitor_dir).resolve() if args.monitor_dir else run_root / "monitor-service"
    packets_dir = Path(args.packets_dir).resolve() if args.packets_dir else run_root / "v2-packets"
    supervisor_dir = Path(args.supervisor_dir).resolve() if args.supervisor_dir else run_root / "v2-supervisor"
    ledger_root = Path(args.ledger_root).resolve() if args.ledger_root else run_root / "v2-ledgers"
    state_path = Path(args.state_path).resolve() if args.state_path else run_root / "v2_service_state.json"
    log_path = Path(args.log_path).resolve() if args.log_path else run_root / "v2_service_log.jsonl"
    disabled_path = Path(args.disabled_candidates_file).resolve() if args.disabled_candidates_file else run_root / "v2_guard_disabled_candidates.json"
    stop_path = run_root / "STOP_CRYPTO_OPTIONS_V2_SERVICE"
    for path in (monitor_dir, packets_dir, supervisor_dir, ledger_root, state_path.parent, log_path.parent):
        path.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    service_started_at = datetime.now(timezone.utc).isoformat()
    tick_index = 0
    static_monitor_artifact = args.monitor_artifact
    latest_monitor_artifact = static_monitor_artifact
    latest_profile_report_cache = args.profile_report_cache or static_monitor_artifact
    latest_monitor_at: float | None = None
    final_payload: dict[str, Any] | None = None
    stop_cashout_drain_attempted = False
    while True:
        tick_started = time.monotonic()
        tick_index += 1
        stop_cashout_drain_requested = False
        if stop_path.exists():
            configs_for_stop = _candidate_configs_for_args(args, ledger_root=ledger_root)
            ledgers_for_stop = load_v2_candidate_ledgers(configs_for_stop)
            ledgers_for_stop = _reconcile_and_persist_candidate_ledgers(configs_for_stop, ledgers_for_stop)
            stop_cashout_drain_requested = (
                not stop_cashout_drain_attempted
                and _has_unarmed_cashout_positions(configs_for_stop, ledgers_for_stop)
            )
            if stop_cashout_drain_requested:
                stop_cashout_drain_attempted = True
            else:
                final_payload = _state_payload(
                    args=args,
                    run_root=run_root,
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
            "ledger_root": str(ledger_root),
        }
        try:
            configs = _candidate_configs_for_args(args, ledger_root=ledger_root)
            ledgers = load_v2_candidate_ledgers(configs)
            ledgers = _reconcile_and_persist_candidate_ledgers(configs, ledgers)
            active_cashout_positions = _has_active_cashout_positions(configs, ledgers)
            monitor_artifact = latest_monitor_artifact
            monitor_age = None if latest_monitor_at is None else time.monotonic() - latest_monitor_at
            should_refresh_monitor = not static_monitor_artifact and (
                not monitor_artifact
                or not Path(str(monitor_artifact)).exists()
                or float(args.monitor_refresh_seconds) <= 0.0
                or monitor_age is None
                or monitor_age >= float(args.monitor_refresh_seconds)
            )
            max_cashout_defer = float(getattr(args, "max_cashout_monitor_defer_seconds", 15.0))
            can_defer_for_cashout = (
                active_cashout_positions
                and bool(getattr(args, "cashout_direct_quotes", False))
                and monitor_age is not None
                and monitor_age < max_cashout_defer
            )
            if can_defer_for_cashout:
                should_refresh_monitor = False
                tick["cashout_fast_path"] = {
                    "active_cashout_positions": active_cashout_positions,
                    "monitor_refresh_deferred": True,
                    "reason": "prioritize_direct_quote_cashout_exit",
                    "monitor_age_seconds": round(float(monitor_age or 0.0), 3),
                    "max_defer_seconds": max_cashout_defer,
                }
            elif active_cashout_positions and bool(getattr(args, "cashout_direct_quotes", False)):
                tick["cashout_fast_path"] = {
                    "active_cashout_positions": active_cashout_positions,
                    "monitor_refresh_deferred": False,
                    "reason": "max_cashout_monitor_defer_elapsed_or_no_monitor",
                    "monitor_age_seconds": round(float(monitor_age or 0.0), 3) if monitor_age is not None else None,
                    "max_defer_seconds": max_cashout_defer,
                }
            if should_refresh_monitor:
                previous_profile_report_cache = latest_profile_report_cache
                monitor = _run_monitor(args, monitor_dir=monitor_dir, profile_report_cache=latest_profile_report_cache)
                monitor_artifact = monitor.get("artifact_monitor")
                if monitor_artifact:
                    cache_health = _profile_report_cache_health(monitor_artifact)
                    tick["monitor_profile_cache_health"] = cache_health
                    if cache_health.get("usable_for_profile_cache"):
                        latest_profile_report_cache = str(monitor_artifact)
                    else:
                        previous_health = _profile_report_cache_health(previous_profile_report_cache)
                        latest_profile_report_cache = (
                            str(previous_profile_report_cache)
                            if previous_profile_report_cache and previous_health.get("usable_for_profile_cache")
                            else None
                        )
                        tick["monitor_profile_cache_rejected"] = {
                            "artifact": str(monitor_artifact),
                            "reason": cache_health.get("reason"),
                            "fallback_cache": str(latest_profile_report_cache) if latest_profile_report_cache else None,
                        }
                latest_monitor_at = time.monotonic()
                tick["monitor"] = monitor
            else:
                tick["monitor"] = {
                    "artifact_monitor": str(Path(str(monitor_artifact)).resolve()),
                    "source": "provided_static" if static_monitor_artifact else "recent_cached",
                    "age_seconds": round(float(monitor_age or 0.0), 3),
                }
            latest_monitor_artifact = monitor_artifact or latest_monitor_artifact
            monitor_payload = load_json_object(str(latest_monitor_artifact)) if latest_monitor_artifact else _empty_monitor_payload()
            promotion_states = _promotion_states_for_args(args, configs=configs, ledgers=ledgers)
            decision_set = build_v2_decision_set(
                monitor_payload,
                candidate_configs=configs,
                promotion_states=promotion_states,
                candidate_ledgers=ledgers,
            )
            if stop_cashout_drain_requested:
                tick["stop_request_cashout_drain"] = {
                    "requested": True,
                    "mode": "cashout_only",
                    "reason": "stop_file_present_with_unarmed_cashout_position",
                }
                decision_set = _cashout_only_stop_drain_decision_set(decision_set)
            disabled = _load_disabled_candidates(disabled_path)
            packet_bundle = build_v2_execution_packet_bundle(
                monitor_payload=monitor_payload,
                candidate_configs=configs,
                candidate_decisions=decision_set.get("decisions") or [],
                ledgers=ledgers,
                promotion_states=promotion_states,
                disabled_candidates=disabled,
                execute_live=bool(args.execute_live),
                execution_approved=bool(args.execution_approved),
                acknowledge_live_risk=bool(args.acknowledge_live_risk),
                cashout_quote_resolver=_resolve_cashout_quote_from_clob if bool(getattr(args, "cashout_direct_quotes", False)) else None,
            )
            written = write_v2_execution_packets(packet_bundle, output_dir=packets_dir)
            supervisor = run_v2_supervisor(
                written,
                output_dir=supervisor_dir,
                disabled_candidates=disabled,
                execute_live=bool(args.execute_live),
                execution_approved=bool(args.execution_approved),
                acknowledge_live_risk=bool(args.acknowledge_live_risk),
                max_executor_workers=int(args.max_executor_workers),
                operator_prefix=str(args.operator_prefix),
                max_correlated_entry_orders=int(getattr(args, "max_correlated_entry_orders", 3)),
            )
            tick["decision_set"] = _compact_decisions(decision_set)
            tick["packet_build"] = _compact_packets(written)
            tick["supervisor"] = _compact_supervisor(supervisor)
            tick["notifiable_events"] = supervisor.get("notifiable_events") or []
            tick["state"] = "running"
        except Exception as exc:  # noqa: BLE001
            tick["state"] = "error"
            tick["error"] = f"{type(exc).__name__}: {exc}"
            tick["notifiable_events"] = ["unexpected_traceback"]

        service_state = tick.get("state") or "running"
        service_reason = None
        if args.once and service_state == "running":
            service_state = "stopped"
            service_reason = "once_completed"
        state = _state_payload(
            args=args,
            run_root=run_root,
            state=service_state,
            service_started_at=service_started_at,
            tick_index=tick_index,
            latest_tick=tick,
            reason=service_reason,
        )
        _write_state(state_path, log_path, state)
        print(json.dumps({"tick_index": tick_index, "state": state["state"], "notifiable_events": tick.get("notifiable_events") or []}, sort_keys=True), flush=True)
        if args.once:
            final_payload = state
            break
        elapsed = time.monotonic() - tick_started
        time.sleep(max(0.25, float(args.interval_seconds) - elapsed))
    return final_payload or load_json_object(state_path)


def _reconcile_and_persist_candidate_ledgers(
    configs: list[dict[str, Any]],
    ledgers: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    reconciled_ledgers: dict[str, dict[str, Any]] = {}
    now = datetime.now(timezone.utc)
    for config in configs:
        candidate_id = str(config.get("candidate_id") or "")
        ledger = ledgers.get(candidate_id) or {"schema_version": "crypto_options_live_execution_ledger_v1", "entries": []}
        before_entries_json = json.dumps(ledger.get("entries") or [], sort_keys=True, default=str)
        reconciled = reconcile_live_execution_ledger(ledger, now=now) | {"lane_id": candidate_id}
        _mark_exit_rows_closed_by_position_settlement(reconciled, now=now)
        reconciled_ledgers[candidate_id] = reconciled
        if _ledger_entries_json_changed(before_entries_json, reconciled):
            raw_ledger_path = config.get("ledger_path")
            if raw_ledger_path:
                write_json_object(reconciled, Path(str(raw_ledger_path)))
    return reconciled_ledgers


def _ledger_entries_json_changed(before_entries_json: str, after: dict[str, Any]) -> bool:
    return before_entries_json != json.dumps(after.get("entries") or [], sort_keys=True, default=str)


def _mark_exit_rows_closed_by_position_settlement(ledger: dict[str, Any], *, now: datetime) -> None:
    settled_positions = {
        (
            str(entry.get("event_slug") or entry.get("event_id") or ""),
            str(entry.get("token_id") or ""),
        )
        for entry in ledger.get("entries") or []
        if isinstance(entry, dict)
        and str(entry.get("side") or "").upper() == "BUY"
        and str(entry.get("settlement_status") or "") == "settled"
    }
    if not settled_positions:
        return
    for entry in ledger.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("side") or "").upper() != "SELL":
            continue
        if str(entry.get("settlement_status") or "") != "exit_submitted_requires_reconciliation":
            continue
        key = (str(entry.get("event_slug") or entry.get("event_id") or ""), str(entry.get("token_id") or ""))
        if key not in settled_positions:
            continue
        entry["settlement_status"] = "not_open"
        entry["exit_reconciliation_status"] = "underlying_position_settled"
        entry["exit_reconciled_at_utc"] = now.astimezone(timezone.utc).isoformat()


def run_v2_supervisor(
    packet_bundle: dict[str, Any],
    *,
    output_dir: Path,
    disabled_candidates: dict[str, Any] | None = None,
    execute_live: bool,
    execution_approved: bool,
    acknowledge_live_risk: bool,
    max_executor_workers: int,
    operator_prefix: str,
    max_correlated_entry_orders: int = 3,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    disabled_candidates = disabled_candidates or {}
    rows = [_supervisor_row(row, disabled_candidates=disabled_candidates) for row in packet_bundle.get("written_packets") or [] if isinstance(row, dict)]
    for row in rows:
        if not execute_live:
            row["supervisor_blockers"].append("execute_live_not_requested")
        elif not (execution_approved and acknowledge_live_risk):
            row["supervisor_blockers"].append("explicit_live_flags_incomplete")
        if row.get("status") != "ready":
            row["supervisor_blockers"].append("candidate_packet_not_ready")
    _apply_correlated_entry_guard(rows, max_correlated_entry_orders=max_correlated_entry_orders)
    executable = [row for row in rows if not row["supervisor_blockers"]]
    executions: list[dict[str, Any]] = []
    if executable:
        workers = max(1, min(int(max_executor_workers), len(executable)))
        if _has_duplicate_ledger_paths(executable):
            workers = 1
        if workers == 1:
            executions = [_run_isolated_executor(row, operator_prefix=operator_prefix) for row in executable]
        else:
            by_index: dict[int, dict[str, Any]] = {}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_run_isolated_executor, row, operator_prefix=operator_prefix): index for index, row in enumerate(executable)}
                for future in as_completed(futures):
                    by_index[futures[future]] = future.result()
            executions = [by_index[index] for index in sorted(by_index)]
    payload = {
        "schema_version": "crypto_options_v2_supervisor_tick_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "execute_live_requested": bool(execute_live),
        "explicit_live_flags_complete": bool(execute_live and execution_approved and acknowledge_live_risk),
        "executor_dispatch_mode": "parallel" if int(max_executor_workers) > 1 else "serial",
        "candidate_rows": rows,
        "executions": executions,
        "notifiable_events": _notifiable_events(rows, executions),
    }
    artifact = output_dir / f"crypto_options_v2_supervisor_tick_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    write_json_object(payload, artifact)
    payload["artifact_json"] = str(artifact)
    write_json_object(payload, artifact)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the supervised Crypto Options V2 8-candidate service loop.")
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--monitor-artifact", default=None)
    parser.add_argument("--profile-report-cache", default=None)
    parser.add_argument("--monitor-dir", default=None)
    parser.add_argument("--packets-dir", default=None)
    parser.add_argument("--supervisor-dir", default=None)
    parser.add_argument("--ledger-root", default=None)
    parser.add_argument("--state-path", default=None)
    parser.add_argument("--log-path", default=None)
    parser.add_argument("--disabled-candidates-file", default=None)
    parser.add_argument("--obsidian-vault", default=None)
    parser.add_argument(
        "--candidate-config-mode",
        choices=("v2-all", "v3-finalists", "v3-validation-single", "v4-three-lane"),
        default="v2-all",
        help="Candidate configuration set. v4-three-lane enables the three isolated V4 development strategies.",
    )
    parser.add_argument(
        "--include-underlying-context",
        action="store_true",
        help="Pass through Binance-derived underlying price/trend context for V4 event gates.",
    )
    parser.add_argument(
        "--v4-pulse-trade-limit",
        type=int,
        default=0,
        help=(
            "For v4-three-lane pulse validation, cap submitted BUY entry groups per strategy. "
            "Use 1, 3, then 10 during staged live integrity validation. Default 0 keeps the config cap."
        ),
    )
    parser.add_argument("--strategy-id", default="btc_eth_5m_mid_high")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--page-limit", type=int, default=200)
    parser.add_argument("--no-active-profile-pool", action="store_true", help="Disable the bounded local active crypto profile pool for monitor refreshes.")
    parser.add_argument("--active-profile-pool", default=None, help="Optional active crypto profile pool text file for monitor refreshes.")
    parser.add_argument("--active-profile-pool-limit", type=int, default=120, help="Maximum active-pool refs to include in one full profile refresh.")
    parser.add_argument("--max-signal-to-ask-slippage-cents", type=float, default=10.0)
    parser.add_argument("--max-spread", type=float, default=0.03)
    parser.add_argument("--min-depth-top3-ask-size", type=float, default=5.0)
    parser.add_argument("--min-time-remaining-seconds", type=float, default=20.0)
    parser.add_argument("--max-time-remaining-seconds", type=float, default=300.0)
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument(
        "--monitor-refresh-seconds",
        type=float,
        default=0.0,
        help="Seconds to reuse a generated monitor artifact. Default 0 regenerates every loop; --monitor-artifact remains static.",
    )
    parser.add_argument("--duration-seconds", type=float, default=0.0)
    parser.add_argument("--max-executor-workers", type=int, default=1)
    parser.add_argument(
        "--max-correlated-entry-orders",
        type=int,
        default=3,
        help="Maximum live BUY entries allowed for the same event/token/outcome in one supervisor tick.",
    )
    parser.add_argument(
        "--cashout-direct-quotes",
        action="store_true",
        help="Use direct CLOB orderbook quotes for cashout exits instead of relying only on profile monitor rows.",
    )
    parser.add_argument(
        "--max-cashout-monitor-defer-seconds",
        type=float,
        default=15.0,
        help="Maximum seconds to defer the slower profile monitor while direct-quote cashout positions are open.",
    )
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--execution-approved", action="store_true")
    parser.add_argument("--acknowledge-live-risk", action="store_true")
    parser.add_argument("--operator-prefix", default="codex-v2")
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


def _candidate_configs_for_args(args: argparse.Namespace, *, ledger_root: Path) -> list[dict[str, Any]]:
    mode = str(getattr(args, "candidate_config_mode", "v2-all"))
    if mode == "v3-finalists":
        return default_v3_candidate_configs(ledger_root=ledger_root)
    if mode == "v3-validation-single":
        return default_v3_validation_candidate_configs(ledger_root=ledger_root)
    if mode == "v4-three-lane":
        return _apply_v4_pulse_trade_limit(
            default_v4_candidate_configs(ledger_root=ledger_root),
            trade_limit=int(getattr(args, "v4_pulse_trade_limit", 0) or 0),
        )
    return default_v2_candidate_configs(ledger_root=ledger_root)


def _apply_v4_pulse_trade_limit(configs: list[dict[str, Any]], *, trade_limit: int) -> list[dict[str, Any]]:
    if trade_limit <= 0:
        return configs
    if trade_limit > 100:
        raise ValueError("--v4-pulse-trade-limit must be between 1 and 100")
    for config in configs:
        policy = config.get("promotion_policy")
        if not isinstance(policy, dict):
            policy = {}
            config["promotion_policy"] = policy
        policy["max_submitted_entry_groups"] = int(trade_limit)
        config["pulse_validation_trade_limit"] = int(trade_limit)
    return configs


def _promotion_states_for_args(
    args: argparse.Namespace,
    *,
    configs: list[dict[str, Any]],
    ledgers: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    mode = str(getattr(args, "candidate_config_mode", "v2-all"))
    if not (mode.startswith("v3-") or mode.startswith("v4-")):
        return evaluate_v2_promotion_states(configs, ledgers)
    states: dict[str, dict[str, Any]] = {}
    for config in configs:
        candidate_id = str(config.get("candidate_id") or "")
        config_policy = config.get("promotion_policy") if isinstance(config.get("promotion_policy"), dict) else {}
        policy = {**default_v3_budget_policy(), **config_policy}
        if "max_component_loss_usd" not in policy and policy.get("hard_stop_loss_usd") is not None:
            policy["max_component_loss_usd"] = policy["hard_stop_loss_usd"]
        if "max_active_cost_usd" not in policy and policy.get("component_budget_usd") is not None:
            policy["max_active_cost_usd"] = policy["component_budget_usd"]
        state = evaluate_v3_component_state(ledgers.get(candidate_id) or {"entries": []}, component_id=candidate_id, policy=policy)
        states[candidate_id] = {
            **state,
            "current_ticket_usd": state["next_order_notional_usd"],
            "dynamic_loss_stop_usd": policy["max_component_loss_usd"],
            "realized_pnl_usd": state["realized_pnl_usd"],
            "disabled": state["disabled"],
            "disabled_reason": state["disabled_reason"],
        }
    return states


def _run_monitor(args: argparse.Namespace, *, monitor_dir: Path, profile_report_cache: str | None) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "codex_tool" / "run_crypto_options_profile_signal_monitor.py"),
        "--strategy-id",
        str(args.strategy_id),
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
    if args.obsidian_vault:
        cmd.extend(["--obsidian-vault", str(args.obsidian_vault)])
    if bool(getattr(args, "include_underlying_context", False)) or str(getattr(args, "candidate_config_mode", "")) == "v4-three-lane":
        cmd.append("--include-underlying-context")
    if bool(getattr(args, "no_active_profile_pool", False)):
        cmd.append("--no-active-profile-pool")
    if getattr(args, "active_profile_pool", None):
        cmd.extend(["--active-profile-pool", str(args.active_profile_pool)])
    cmd.extend(["--active-profile-pool-limit", str(int(getattr(args, "active_profile_pool_limit", 120) or 120))])
    if profile_report_cache:
        cmd.extend(["--profile-report-cache", str(profile_report_cache)])
    completed = _run_command(cmd, timeout=180)
    return {
        **completed,
        "artifact_monitor": _extract_key(completed["stdout"], "artifact_monitor"),
        "profile_report_cache": str(profile_report_cache) if profile_report_cache else None,
    }


def _empty_monitor_payload() -> dict[str, Any]:
    return {
        "schema_version": "crypto_options_empty_monitor_payload_v1",
        "monitor_status": "blocked",
        "eligible_manual_candidate_count": 0,
        "eligible_manual_candidates": [],
        "observed_candidates": [],
    }


def _has_active_cashout_positions(candidate_configs: list[dict[str, Any]], ledgers: dict[str, dict[str, Any]]) -> int:
    configs_by_id = {str(row.get("candidate_id") or ""): row for row in candidate_configs}
    count = 0
    for candidate_id, ledger in ledgers.items():
        capabilities = (configs_by_id.get(candidate_id) or {}).get("capabilities") or {}
        if not capabilities.get("cashout_managed"):
            continue
        for entry in ledger.get("entries") or []:
            if (
                isinstance(entry, dict)
                and entry.get("settlement_status") == "open_requires_reconciliation"
                and str(entry.get("side") or "").upper() == "BUY"
                and not _ledger_has_submitted_exit_for_entry(ledger, entry)
            ):
                count += 1
    return count


def _has_unarmed_cashout_positions(candidate_configs: list[dict[str, Any]], ledgers: dict[str, dict[str, Any]]) -> bool:
    configs_by_id = {str(row.get("candidate_id") or ""): row for row in candidate_configs}
    for candidate_id, ledger in ledgers.items():
        capabilities = (configs_by_id.get(candidate_id) or {}).get("capabilities") or {}
        if not capabilities.get("cashout_managed"):
            continue
        for entry in ledger.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("side") or "").upper() != "BUY":
                continue
            if str(entry.get("settlement_status") or "") != "open_requires_reconciliation":
                continue
            if not _ledger_has_submitted_exit_for_entry(ledger, entry):
                return True
    return False


def _ledger_has_submitted_exit_for_entry(ledger: dict[str, Any], entry: dict[str, Any]) -> bool:
    return _cashout_coverage_for_position(ledger, entry)["uncovered_shares"] <= _CASHOUT_SHARE_COVERAGE_EPSILON


def _cashout_coverage_for_position(ledger: dict[str, Any], entry: dict[str, Any]) -> dict[str, float]:
    event_slug = str(entry.get("event_slug") or entry.get("event_id") or "")
    token_id = str(entry.get("token_id") or "")
    if not event_slug and not token_id:
        return {"open_buy_shares": 1.0, "submitted_sell_shares": 0.0, "uncovered_shares": 1.0}
    open_buy_shares = 0.0
    submitted_sell_shares = 0.0
    for row in ledger.get("entries") or []:
        if not isinstance(row, dict):
            continue
        if event_slug and str(row.get("event_slug") or row.get("event_id") or "") != event_slug:
            continue
        if token_id and str(row.get("token_id") or "") != token_id:
            continue
        side = str(row.get("side") or "BUY").upper()
        if side == "BUY" and str(row.get("settlement_status") or "") == "open_requires_reconciliation":
            open_buy_shares += _position_held_shares(row) or 1.0
        if (
            side == "SELL"
            and str(row.get("status") or "") == "submitted"
            and str(row.get("settlement_status") or "") == "exit_submitted_requires_reconciliation"
        ):
            submitted_sell_shares += _position_held_shares(row) or 1.0
    uncovered = max(0.0, open_buy_shares - submitted_sell_shares)
    if uncovered <= _CASHOUT_SHARE_COVERAGE_EPSILON:
        uncovered = 0.0
    return {
        "open_buy_shares": round(open_buy_shares, 8),
        "submitted_sell_shares": round(submitted_sell_shares, 8),
        "uncovered_shares": round(uncovered, 8),
    }


def _position_held_shares(entry: dict[str, Any]) -> float | None:
    execution_quality = entry.get("execution_quality") if isinstance(entry.get("execution_quality"), dict) else {}
    return _to_float(execution_quality.get("filled_shares")) or _to_float(entry.get("size"))


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _cashout_only_stop_drain_decision_set(decision_set: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(decision_set, default=str))
    for row in payload.get("decisions") or []:
        if not isinstance(row, dict):
            continue
        blockers = [str(item) for item in row.get("blockers") or []]
        row["status"] = "blocked"
        row["blockers"] = sorted(set([*blockers, "stop_requested_cashout_only_drain"]))
    safety = payload.setdefault("safety_boundary", {})
    safety["stop_requested_cashout_only_drain"] = True
    return payload


def _profile_report_cache_health(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {"usable_for_profile_cache": False, "reason": "profile_report_cache_missing"}
    try:
        payload = load_json_object(str(path))
    except Exception as exc:  # noqa: BLE001 - cache health should not crash the live loop.
        return {
            "usable_for_profile_cache": False,
            "reason": f"profile_report_cache_unreadable:{type(exc).__name__}",
        }
    report = payload.get("profile_signal_report") if isinstance(payload.get("profile_signal_report"), dict) else payload
    profiles = [row for row in report.get("profiles") or [] if isinstance(row, dict)] if isinstance(report, dict) else []
    if not profiles:
        return {"usable_for_profile_cache": False, "reason": "profile_report_cache_no_profiles"}
    network_failed = 0
    scored_or_signaled = 0
    for profile in profiles:
        blockers = " ".join(str(item) for item in profile.get("blockers") or [])
        if _profile_fetch_network_error(blockers):
            network_failed += 1
        if profile.get("score") is not None or profile.get("signals") or profile.get("historical_signals"):
            scored_or_signaled += 1
    failure_threshold = max(3, int(len(profiles) * 0.5))
    if scored_or_signaled <= 0 and network_failed >= failure_threshold:
        return {
            "usable_for_profile_cache": False,
            "reason": "profile_report_cache_network_failed_all_profiles",
            "profile_count": len(profiles),
            "network_failed_profile_count": network_failed,
            "scored_or_signaled_profile_count": scored_or_signaled,
        }
    return {
        "usable_for_profile_cache": True,
        "reason": "profile_report_cache_usable",
        "profile_count": len(profiles),
        "network_failed_profile_count": network_failed,
        "scored_or_signaled_profile_count": scored_or_signaled,
    }


def _profile_fetch_network_error(value: str) -> bool:
    lowered = str(value or "").lower()
    return any(
        token in lowered
        for token in (
            "getaddrinfo failed",
            "urlerror",
            "temporary failure in name resolution",
            "name resolution",
            "network is unreachable",
            "connection refused",
            "connection reset",
            "timed out",
        )
    )


def _supervisor_row(row: dict[str, Any], *, disabled_candidates: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(row.get("candidate_id") or "")
    blockers: list[str] = []
    disabled_row = disabled_candidates.get(candidate_id)
    if disabled_row is True or (isinstance(disabled_row, dict) and disabled_row.get("disabled", True)):
        blockers.append("candidate_guard_disabled")
    return {
        "candidate_id": candidate_id,
        "packet_id": row.get("packet_id") or candidate_id,
        "leg_id": row.get("leg_id"),
        "status": row.get("status"),
        "packet_action": row.get("packet_action"),
        "execution_side": row.get("execution_side"),
        "execution_style": row.get("execution_style"),
        "order_type": row.get("order_type"),
        "price_slippage_cents": row.get("price_slippage_cents"),
        "event_slug": row.get("event_slug"),
        "token_id": row.get("token_id"),
        "outcome": row.get("outcome"),
        "loop_dir": row.get("loop_dir"),
        "ledger": row.get("ledger"),
        "packet_blockers": row.get("blockers") or [],
        "supervisor_blockers": blockers,
    }


_CORRELATED_ENTRY_PRIORITY = {
    "dynamic_bucketed_takeprofit_v1": 100,
    "control_dynamic_hold_v1": 90,
    "dynamic_dynamic_sizing_v1": 80,
    "dynamic_parallel_conflict_cashout_v1": 70,
    "ev_overlay_cashout_parallel_v1": 60,
    "banded_profile_cashout_router_v1": 50,
    "consensus_conflict_filter_cashout_v2": 40,
    "inverse_profile_scalp_cashout_v1": 30,
}


def _apply_correlated_entry_guard(rows: list[dict[str, Any]], *, max_correlated_entry_orders: int) -> None:
    max_orders = max(1, int(max_correlated_entry_orders or 1))
    existing_counts = _existing_correlated_entry_counts(rows)
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("packet_action") != "entry" or row.get("execution_side") != "BUY":
            continue
        if row.get("status") != "ready" or row.get("supervisor_blockers"):
            continue
        key = (str(row.get("event_slug") or ""), str(row.get("token_id") or ""), str(row.get("outcome") or ""))
        if not all(key):
            continue
        groups.setdefault(key, []).append(row)
    for key, group in groups.items():
        existing_count = existing_counts.get(key, 0)
        remaining_slots = max(0, max_orders - existing_count)
        if len(group) <= remaining_slots:
            continue
        ranked = sorted(
            group,
            key=lambda row: (-_CORRELATED_ENTRY_PRIORITY.get(str(row.get("candidate_id") or ""), 0), str(row.get("candidate_id") or "")),
        )
        allowed = {id(row) for row in ranked[:remaining_slots]}
        reason = f"correlated_entry_exposure_cap:{key[0]}:{key[2]}:max_{max_orders}:existing_{existing_count}"
        for row in group:
            if id(row) not in allowed:
                row["supervisor_blockers"].append(reason)


def _existing_correlated_entry_counts(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], int]:
    counts: dict[tuple[str, str, str], int] = {}
    ledger_paths = sorted({str(row.get("ledger") or "") for row in rows if row.get("ledger")})
    for ledger_path in ledger_paths:
        try:
            ledger = load_json_object(ledger_path)
        except Exception:  # noqa: BLE001
            continue
        for entry in ledger.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("side") or "").upper() != "BUY":
                continue
            if str(entry.get("status") or "") not in {"submitted", "open"}:
                continue
            if str(entry.get("settlement_status") or "") == "settled":
                continue
            key = (
                str(entry.get("event_slug") or entry.get("event_id") or ""),
                str(entry.get("token_id") or ""),
                str(entry.get("outcome") or ""),
            )
            if all(key):
                counts[key] = counts.get(key, 0) + 1
    return counts


def _run_isolated_executor(row: dict[str, Any], *, operator_prefix: str) -> dict[str, Any]:
    candidate_id = str(row.get("candidate_id") or "unknown_candidate")
    loop_dir = Path(str(row["loop_dir"]))
    loop_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(REPO_ROOT / "codex_tool" / "run_crypto_options_live_micro_executor.py"),
        "--loop-dir",
        str(loop_dir),
        "--output-dir",
        str(loop_dir),
        "--ledger-artifact",
        str(row.get("ledger") or loop_dir / "live_execution_ledger.json"),
        "--order-type",
        str(row.get("order_type") or "FAK"),
        "--execution-style",
        str(row.get("execution_style") or "limit"),
        "--execution-side",
        str(row.get("execution_side") or "BUY"),
        "--price-slippage-cents",
        str(row.get("price_slippage_cents") or 0.0),
        "--operator",
        f"{operator_prefix}-{candidate_id}",
        "--reason",
        f"crypto_options_v2_supervised_{candidate_id}_{row.get('packet_action') or 'entry'}",
        "--execute-live",
        "--execution-approved",
        "--acknowledge-live-risk",
        "--json",
    ]
    completed = _run_command(cmd, timeout=240)
    try:
        payload = json.loads(completed["stdout"])
    except json.JSONDecodeError:
        payload = {
            "status": "unexpected_executor_output",
            "order_submission_attempted": False,
            "blockers": ["executor_json_parse_error"],
            "stdout_tail": completed["stdout"][-2000:],
        }
    payload["candidate_id"] = candidate_id
    payload["packet_id"] = row.get("packet_id") or candidate_id
    payload["leg_id"] = row.get("leg_id")
    payload["packet_action"] = row.get("packet_action")
    payload["returncode"] = completed["returncode"]
    payload["stderr_tail"] = completed["stderr_tail"]
    return payload


def _resolve_cashout_quote_from_clob(position: dict[str, Any]) -> dict[str, Any]:
    token_id = str(position.get("token_id") or "").strip()
    if not token_id:
        return {"best_bid": None, "best_ask": None, "source": "direct_clob_quote", "reason": "token_id_missing"}
    creds = PolymarketCredentials.from_env()
    try:
        clob = _load_py_clob_runtime()
    except RuntimeError as exc:
        return {
            "best_bid": None,
            "best_ask": None,
            "source": "direct_clob_quote",
            "reason": str(exc),
            "error": "py-clob-client-v2 is required for direct cashout quotes",
        }
    ClobClient = clob["ClobClient"]
    ApiCreds = clob["ApiCreds"]
    POLYGON = clob["POLYGON"]
    client = ClobClient(
        host=creds.clob_host or "https://clob.polymarket.com",
        key=creds.private_key,
        chain_id=creds.chain_id or POLYGON,
        signature_type=creds.signature_type,
        funder=creds.funder_address or creds.wallet_address,
    )
    client.set_api_creds(
        ApiCreds(
            api_key=creds.api_key,
            api_secret=creds.secret,
            api_passphrase=creds.passphrase,
        )
    )
    started = time.perf_counter()
    try:
        raw_book = client.get_order_book(token_id)
    except Exception as exc:  # noqa: BLE001
        health = classify_quote_health(f"{type(exc).__name__}: {exc}")
        return {
            "best_bid": None,
            "best_ask": None,
            "source": "direct_clob_quote",
            "reason": health["quote_error_type"],
            "error": f"{type(exc).__name__}: {exc}",
            "quote_error_type": health["quote_error_type"],
            "quote_health": health,
        }
    quote = parse_clob_orderbook_quote(raw_book)
    quote["source"] = "direct_clob_quote"
    quote["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    return quote


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


def _load_disabled_candidates(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = load_json_object(path)
    if isinstance(payload.get("candidates"), dict):
        return payload["candidates"]
    if isinstance(payload.get("disabled_candidates"), list):
        return {str(item): {"disabled": True, "reason": "listed_disabled"} for item in payload["disabled_candidates"]}
    return payload if isinstance(payload, dict) else {}


def _notifiable_events(rows: list[dict[str, Any]], executions: list[dict[str, Any]]) -> list[str]:
    events: list[str] = []
    for execution in executions:
        candidate_id = str(execution.get("candidate_id") or "")
        status = str(execution.get("status") or "")
        if status == "submitted":
            events.append(f"submitted:{candidate_id}:{execution.get('packet_action')}:{execution.get('leg_id') or 'primary'}")
        elif status == "submit_error":
            events.append(f"submit_error:{candidate_id}:{execution.get('packet_action')}")
        elif status == "unexpected_executor_output":
            events.append(f"executor_unexpected_output:{candidate_id}:{execution.get('packet_action')}")
        elif execution.get("returncode") not in (None, 0):
            events.append(f"executor_nonzero_exit:{candidate_id}:{execution.get('packet_action')}")
        blockers = {str(item) for item in execution.get("blockers") or []}
        if "polymarket_execution_credentials_missing" in blockers:
            events.append(f"credentials_missing:{candidate_id}")
    for row in rows:
        if "candidate_guard_disabled" in (row.get("supervisor_blockers") or []):
            events.append(f"candidate_guard_disabled:{row.get('candidate_id')}")
    return sorted(set(events))


def _compact_decisions(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_count": payload.get("candidate_count"),
        "accepted": [row.get("candidate_id") for row in payload.get("decisions") or [] if row.get("status") == "accepted"],
        "blocked": [row.get("candidate_id") for row in payload.get("decisions") or [] if row.get("status") != "accepted"],
    }


def _compact_packets(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_json": payload.get("artifact_json"),
        "packet_count": payload.get("packet_count"),
        "ready_packet_count": payload.get("ready_packet_count"),
        "ready": [row.get("candidate_id") for row in payload.get("written_packets") or [] if row.get("status") == "ready"],
        "blocked": [row.get("candidate_id") for row in payload.get("written_packets") or [] if row.get("status") != "ready"],
    }


def _compact_supervisor(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_json": payload.get("artifact_json"),
        "execute_live_requested": payload.get("execute_live_requested"),
        "explicit_live_flags_complete": payload.get("explicit_live_flags_complete"),
        "executions": [
            {
                "candidate_id": row.get("candidate_id"),
                "packet_id": row.get("packet_id"),
                "leg_id": row.get("leg_id"),
                "packet_action": row.get("packet_action"),
                "status": row.get("status"),
                "order_submission_attempted": row.get("order_submission_attempted"),
                "blockers": row.get("blockers") or [],
            }
            for row in payload.get("executions") or []
            if isinstance(row, dict)
        ],
        "candidates": [
            {
                "candidate_id": row.get("candidate_id"),
                "packet_id": row.get("packet_id"),
                "leg_id": row.get("leg_id"),
                "status": row.get("status"),
                "packet_action": row.get("packet_action"),
                "supervisor_blockers": row.get("supervisor_blockers") or [],
                "packet_blockers": row.get("packet_blockers") or [],
            }
            for row in payload.get("candidate_rows") or []
            if isinstance(row, dict)
        ],
        "notifiable_events": payload.get("notifiable_events") or [],
    }


def _state_payload(
    *,
    args: argparse.Namespace,
    run_root: Path,
    state: str,
    service_started_at: str,
    tick_index: int,
    reason: str | None = None,
    latest_tick: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "crypto_options_v2_service_state_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "service_started_at_utc": service_started_at,
        "state": state,
        "reason": reason,
        "tick_index": tick_index,
        "run_root": str(run_root),
        "state_path": str(Path(args.state_path).resolve()) if args.state_path else str(run_root / "v2_service_state.json"),
        "interval_seconds": float(args.interval_seconds),
        "candidate_config_mode": str(getattr(args, "candidate_config_mode", "v2-all")),
        "live_execution_requested": bool(args.execute_live),
        "latest_tick": latest_tick or {},
    }


def _has_duplicate_ledger_paths(rows: list[dict[str, Any]]) -> bool:
    seen: set[str] = set()
    for row in rows:
        ledger = str(row.get("ledger") or "")
        if not ledger:
            continue
        if ledger in seen:
            return True
        seen.add(ledger)
    return False


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
