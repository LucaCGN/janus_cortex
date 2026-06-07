from __future__ import annotations

from collections import Counter, deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Iterable

from crypto_options_app.services.crypto_options.v3_policy import (
    V3_FINAL_COMPONENT_IDS,
    default_v3_budget_policy,
    evaluate_v3_component_state,
)


OBSERVER_SCHEMA_VERSION = "crypto_options_v3_observer_snapshot_v1"


def default_artifact_base(repo_root: str | Path | None = None) -> Path:
    root = Path(repo_root or Path.cwd())
    return root / "local" / "shared" / "artifacts" / "crypto-options-research"


def discover_run_roots(base_dir: str | Path | None = None, *, limit: int = 20) -> list[dict[str, Any]]:
    base = Path(base_dir) if base_dir is not None else default_artifact_base()
    if not base.exists():
        return []
    rows: list[dict[str, Any]] = []
    for state_path in _walk_matching_files(base, "v2_service_state.json"):
        state = _read_json(state_path)
        run_root = Path(state.get("run_root") or state_path.parent)
        rows.append(
            {
                "run_root": str(run_root),
                "state_path": str(state_path),
                "state": state.get("state"),
                "reason": state.get("reason"),
                "tick_index": state.get("tick_index"),
                "candidate_config_mode": state.get("candidate_config_mode"),
                "generated_at_utc": state.get("generated_at_utc"),
                "last_write_utc": _mtime_iso(state_path),
            }
        )
    rows.sort(key=lambda row: row.get("last_write_utc") or "", reverse=True)
    return rows[:limit]


def load_observer_snapshot(
    run_root: str | Path,
    *,
    now_utc: datetime | None = None,
    log_tail_lines: int = 80,
    component_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    now_utc = now_utc or datetime.now(timezone.utc)
    root = Path(run_root)
    state_path = root / "v2_service_state.json"
    service_state = _read_json(state_path)
    latest_tick = service_state.get("latest_tick") if isinstance(service_state.get("latest_tick"), dict) else {}
    process_state = _read_json(root / "crypto_options_v2_service_process_state.json")

    packet_path = _path_from_pointer(latest_tick, ("packet_build", "artifact_json")) or root / "v2-packets" / "v2_packets.json"
    supervisor_path = _path_from_pointer(latest_tick, ("supervisor", "artifact_json")) or _latest_file(root / "v2-supervisor", "crypto_options_v2_supervisor_tick_*.json")
    monitor_path = _path_from_pointer(latest_tick, ("monitor", "artifact_monitor")) or _latest_file(root, "crypto_options_profile_signal_monitor_*.json")
    ledger_root = Path(latest_tick.get("ledger_root") or root / "v2-ledgers")
    log_path = root / "v2_service_log.jsonl"

    packets_payload = _read_json(packet_path)
    supervisor_payload = _read_json(supervisor_path)
    monitor_payload = _read_json(monitor_path)
    packet_rows = _packet_rows(packets_payload)
    supervisor_rows = _supervisor_rows(supervisor_payload)
    resolved_component_ids = _resolve_component_ids(
        ledger_root=ledger_root,
        packet_rows=packet_rows,
        supervisor_rows=supervisor_rows,
        requested_component_ids=component_ids,
    )
    ledger_payloads = _load_component_ledgers(ledger_root, resolved_component_ids)
    log_rows = _read_jsonl_tail(log_path, line_count=log_tail_lines)

    components = _component_rows(
        component_ids=resolved_component_ids,
        ledger_payloads=ledger_payloads,
        packet_rows=packet_rows,
        supervisor_rows=supervisor_rows,
    )
    monitor_state = latest_tick.get("monitor") if isinstance(latest_tick.get("monitor"), dict) else {}
    monitor_summary = _monitor_summary(
        monitor_payload,
        monitor_path=monitor_path,
        now_utc=now_utc,
        monitor_state=monitor_state,
    )
    service_summary = _service_summary(
        service_state=service_state,
        process_state=process_state,
        state_path=state_path,
        now_utc=now_utc,
    )

    return {
        "schema_version": OBSERVER_SCHEMA_VERSION,
        "generated_at_utc": now_utc.astimezone(timezone.utc).isoformat(),
        "run_root": str(root),
        "service": service_summary,
        "monitor": monitor_summary,
        "decision_set": latest_tick.get("decision_set") or {},
        "packets": _packet_summary(packet_rows, packet_path=packet_path),
        "supervisor": _supervisor_summary(supervisor_payload, supervisor_path=supervisor_path),
        "components": components,
        "bucket_rows": _bucket_rows(ledger_payloads),
        "source_profile_attribution_rows": _source_profile_attribution_rows(ledger_payloads),
        "profile_rows": _profile_rows(monitor_payload, limit=25),
        "candidate_rows": _candidate_rows(monitor_payload, limit=25),
        "log": _log_summary(log_rows, log_path=log_path),
        "stderr": _text_log_summary(process_state.get("stderr_log"), tail_lines=40),
        "stdout": _text_log_summary(process_state.get("stdout_log"), tail_lines=20),
        "latest_files": {
            "state": str(state_path) if state_path.exists() else None,
            "process_state": str(root / "crypto_options_v2_service_process_state.json")
            if (root / "crypto_options_v2_service_process_state.json").exists()
            else None,
            "packet": str(packet_path) if packet_path and packet_path.exists() else None,
            "supervisor": str(supervisor_path) if supervisor_path and supervisor_path.exists() else None,
            "monitor": str(monitor_path) if monitor_path and monitor_path.exists() else None,
            "ledger_root": str(ledger_root) if ledger_root.exists() else None,
            "service_log": str(log_path) if log_path.exists() else None,
        },
        "read_only": True,
        "manual_orders_allowed": False,
        "warnings": _snapshot_warnings(service_summary, monitor_summary, components),
    }


def _service_summary(
    *,
    service_state: dict[str, Any],
    process_state: dict[str, Any],
    state_path: Path,
    now_utc: datetime,
) -> dict[str, Any]:
    latest_tick = service_state.get("latest_tick") if isinstance(service_state.get("latest_tick"), dict) else {}
    generated_at = service_state.get("generated_at_utc")
    process_pid = _int(process_state.get("pid"))
    return {
        "state": service_state.get("state"),
        "reason": service_state.get("reason"),
        "tick_index": service_state.get("tick_index"),
        "latest_tick_index": latest_tick.get("tick_index"),
        "candidate_config_mode": service_state.get("candidate_config_mode"),
        "live_execution_requested": service_state.get("live_execution_requested"),
        "service_started_at_utc": service_state.get("service_started_at_utc"),
        "generated_at_utc": generated_at,
        "state_path": str(state_path),
        "state_file_mtime_utc": _mtime_iso(state_path),
        "staleness_seconds": _age_seconds(generated_at, now_utc=now_utc),
        "process_pid": process_pid,
        "process_alive": _pid_alive(process_pid) if process_pid is not None else None,
        "restart_reason": process_state.get("restart_reason"),
        "stdout_log": process_state.get("stdout_log"),
        "stderr_log": process_state.get("stderr_log"),
    }


def _component_rows(
    *,
    component_ids: Iterable[str],
    ledger_payloads: dict[str, dict[str, Any]],
    packet_rows: list[dict[str, Any]],
    supervisor_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    policy = default_v3_budget_policy()
    packets_by_candidate = {str(row.get("candidate_id") or row.get("packet_id")): row for row in packet_rows}
    supervisor_by_candidate = {str(row.get("candidate_id") or row.get("packet_id")): row for row in supervisor_rows}
    rows: list[dict[str, Any]] = []
    for component_id in component_ids:
        ledger = ledger_payloads.get(component_id) or {"entries": []}
        entries = [row for row in ledger.get("entries") or [] if isinstance(row, dict)]
        state = evaluate_v3_component_state(ledger, component_id=component_id, policy=policy)
        buys = [row for row in entries if _side(row) == "BUY"]
        sells = [row for row in entries if _side(row) == "SELL"]
        submitted_buys = [row for row in buys if str(row.get("status") or "").lower() == "submitted"]
        submitted_sells = [row for row in sells if str(row.get("status") or "").lower() == "submitted"]
        settled_buys = [row for row in buys if _is_settled(row)]
        open_buys = [row for row in buys if _is_open_position(row)]
        open_sells = [row for row in sells if _is_open_position(row) and not _is_not_open(row)]
        packet = packets_by_candidate.get(component_id) or {}
        supervisor = supervisor_by_candidate.get(component_id) or {}
        rows.append(
            {
                "component_id": component_id,
                "buy_rows": len(buys),
                "sell_rows": len(sells),
                "submitted_buys": len(submitted_buys),
                "settled_buys": len(settled_buys),
                "open_buys": len(open_buys),
                "submitted_sells": len(submitted_sells),
                "open_sells": len(open_sells),
                "wins": state.get("wins"),
                "losses": state.get("losses"),
                "win_rate": state.get("win_rate"),
                "realized_pnl_usd": state.get("realized_pnl_usd"),
                "loss_usd": state.get("loss_usd"),
                "active_cost_usd": state.get("active_cost_usd"),
                "worst_case_loss_usd": state.get("worst_case_loss_usd"),
                "peak_pnl_usd": state.get("peak_pnl_usd"),
                "drawdown_from_peak_usd": state.get("drawdown_from_peak_usd"),
                "current_budget_usd": state.get("current_budget_usd"),
                "next_order_notional_usd": state.get("next_order_notional_usd"),
                "disabled": state.get("disabled"),
                "disabled_reason": state.get("disabled_reason"),
                "stop_reasons": state.get("stop_reasons") or [],
                "latest_packet_status": packet.get("status"),
                "latest_packet_action": packet.get("packet_action"),
                "latest_packet_blockers": packet.get("blockers") or [],
                "latest_supervisor_status": supervisor.get("status"),
                "latest_supervisor_blockers": supervisor.get("supervisor_blockers") or [],
                "ledger_entry_count": len(entries),
            }
        )
    return rows


def _bucket_rows(ledger_payloads: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for component_id, ledger in ledger_payloads.items():
        for row in ledger.get("entries") or []:
            if not isinstance(row, dict) or _side(row) != "BUY" or str(row.get("status") or "").lower() != "submitted":
                continue
            bucket = str(row.get("v3_bucket_label") or _price_bucket(_entry_price(row)) or "unknown")
            key = (component_id, bucket)
            item = grouped.setdefault(
                key,
                {
                    "component_id": component_id,
                    "bucket": bucket,
                    "submitted_buys": 0,
                    "settled_buys": 0,
                    "open_buys": 0,
                    "wins": 0,
                    "losses": 0,
                    "realized_pnl_usd": 0.0,
                    "active_cost_usd": 0.0,
                },
            )
            item["submitted_buys"] += 1
            if _is_settled(row):
                item["settled_buys"] += 1
                pnl = _float(row.get("realized_pnl_net_usd")) or 0.0
                item["realized_pnl_usd"] += pnl
                if pnl > 0:
                    item["wins"] += 1
                elif pnl < 0:
                    item["losses"] += 1
            elif _is_open_position(row):
                item["open_buys"] += 1
                item["active_cost_usd"] += _entry_cost(row)
    rows = []
    for item in grouped.values():
        settled = int(item["settled_buys"])
        item["win_rate"] = (float(item["wins"]) / settled) if settled else None
        item["realized_pnl_usd"] = round(float(item["realized_pnl_usd"]), 6)
        item["active_cost_usd"] = round(float(item["active_cost_usd"]), 6)
        rows.append(item)
    rows.sort(key=lambda row: (str(row["component_id"]), str(row["bucket"])))
    return rows


def _source_profile_attribution_rows(ledger_payloads: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    unattributed = 0
    for component_id, ledger in ledger_payloads.items():
        for row in ledger.get("entries") or []:
            if not isinstance(row, dict) or _side(row) != "BUY" or str(row.get("status") or "").lower() != "submitted":
                continue
            attribution = row.get("source_signal_attribution") if isinstance(row.get("source_signal_attribution"), dict) else {}
            signals = [item for item in attribution.get("signals") or [] if isinstance(item, dict)]
            if not signals:
                unattributed += 1
                continue
            pnl = _float(row.get("realized_pnl_net_usd"))
            active_cost = 0.0 if _is_settled(row) else _entry_cost(row)
            for signal in signals:
                profile = str(signal.get("profile_name") or "unknown")
                grade = str(signal.get("profile_grade") or "unknown")
                key = (component_id, profile, grade)
                item = grouped.setdefault(
                    key,
                    {
                        "component_id": component_id,
                        "profile": profile,
                        "grade": grade,
                        "signals": 0,
                        "settled": 0,
                        "open": 0,
                        "wins": 0,
                        "losses": 0,
                        "realized_pnl_usd": 0.0,
                        "active_cost_usd": 0.0,
                    },
                )
                item["signals"] += 1
                if pnl is None:
                    item["open"] += 1
                    item["active_cost_usd"] += active_cost
                else:
                    item["settled"] += 1
                    item["realized_pnl_usd"] += pnl
                    if pnl > 0:
                        item["wins"] += 1
                    elif pnl < 0:
                        item["losses"] += 1
    rows = []
    for item in grouped.values():
        settled = int(item["settled"])
        item["win_rate"] = (float(item["wins"]) / settled) if settled else None
        item["realized_pnl_usd"] = round(float(item["realized_pnl_usd"]), 6)
        item["active_cost_usd"] = round(float(item["active_cost_usd"]), 6)
        rows.append(item)
    if unattributed:
        rows.append(
            {
                "component_id": "all",
                "profile": "unattributed",
                "grade": "unknown",
                "signals": unattributed,
                "settled": None,
                "open": None,
                "wins": None,
                "losses": None,
                "realized_pnl_usd": None,
                "active_cost_usd": None,
                "win_rate": None,
            }
        )
    rows.sort(key=lambda row: (str(row.get("component_id")), str(row.get("grade")), str(row.get("profile"))))
    return rows


def _monitor_summary(
    monitor: dict[str, Any],
    *,
    monitor_path: Path | None,
    now_utc: datetime,
    monitor_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    monitor_state = monitor_state or {}
    report = monitor.get("profile_signal_report") if isinstance(monitor.get("profile_signal_report"), dict) else {}
    profiles = report.get("profiles") if isinstance(report.get("profiles"), list) else []
    registry = report.get("profile_registry") if isinstance(report.get("profile_registry"), list) else []
    active_signals = report.get("active_signals") if isinstance(report.get("active_signals"), list) else []
    candidates = report.get("aggregated_candidates") if isinstance(report.get("aggregated_candidates"), list) else []
    generated_at = monitor.get("generated_at_utc") or report.get("generated_at_utc")
    payload_staleness = _age_seconds(generated_at, now_utc=now_utc)
    service_age = _float(monitor_state.get("age_seconds"))
    grade_counts = Counter(str(row.get("grade") or "U") for row in profiles if isinstance(row, dict))
    status_counts = Counter(str(row.get("status") or "unknown") for row in profiles if isinstance(row, dict))
    return {
        "path": str(monitor_path) if monitor_path and monitor_path.exists() else None,
        "generated_at_utc": generated_at,
        "staleness_seconds": service_age if service_age is not None else payload_staleness,
        "payload_staleness_seconds": payload_staleness,
        "source": monitor_state.get("source") or monitor.get("source"),
        "monitor_status": monitor.get("monitor_status"),
        "active_event_count": len(monitor.get("active_event_slugs") or report.get("active_event_slugs") or []),
        "observed_candidate_count": monitor.get("observed_candidate_count") or len(candidates),
        "eligible_manual_candidate_count": monitor.get("eligible_manual_candidate_count"),
        "profile_seed_count": report.get("profile_seed_count") or len(registry),
        "profile_snapshot_count": report.get("profile_snapshot_count") or len(profiles),
        "profile_registry_count": len(registry),
        "profile_count": len(profiles),
        "active_signal_count": report.get("active_signal_count") or len(active_signals),
        "candidate_count": report.get("candidate_count") or len(candidates),
        "grade_counts": dict(sorted(grade_counts.items())),
        "profile_status_counts": dict(sorted(status_counts.items())),
        "top_holder_payload_count": len(((report.get("top_holder_discovery") or {}).get("payloads")) or []),
        "scraped_profile_count": len(((report.get("scraped_profile_discovery") or {}).get("profiles")) or []),
    }


def _profile_rows(monitor: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    report = monitor.get("profile_signal_report") if isinstance(monitor.get("profile_signal_report"), dict) else {}
    profiles = report.get("profiles") if isinstance(report.get("profiles"), list) else []
    rows: list[dict[str, Any]] = []
    for row in profiles:
        if not isinstance(row, dict):
            continue
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        rows.append(
            {
                "profile": row.get("profile") or row.get("profile_ref"),
                "grade": row.get("grade"),
                "score": _float(row.get("score")),
                "status": row.get("status"),
                "polarity": row.get("polarity"),
                "signals": len(row.get("signals") or []),
                "historical_signals": len(row.get("historical_signals") or []),
                "daily_pnl": metrics.get("daily_pnl") or metrics.get("daily_pnl_usd"),
                "weekly_pnl": metrics.get("weekly_pnl") or metrics.get("weekly_pnl_usd"),
                "monthly_pnl": metrics.get("monthly_pnl") or metrics.get("monthly_pnl_usd"),
                "win_rate": metrics.get("closed_win_rate") or metrics.get("win_rate"),
                "reasons": "; ".join(str(item) for item in row.get("grade_reasons") or []),
            }
        )
    rows.sort(key=lambda item: (_float(item.get("score")) is not None, _float(item.get("score")) or -1), reverse=True)
    return rows[:limit]


def _candidate_rows(monitor: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    observed = monitor.get("observed_candidates") if isinstance(monitor.get("observed_candidates"), list) else []
    report = monitor.get("profile_signal_report") if isinstance(monitor.get("profile_signal_report"), dict) else {}
    aggregated = report.get("aggregated_candidates") if isinstance(report.get("aggregated_candidates"), list) else []
    rows: list[dict[str, Any]] = []
    for candidate in observed or aggregated:
        if not isinstance(candidate, dict):
            continue
        rows.append(
            {
                "event_slug": candidate.get("event_slug"),
                "outcome": candidate.get("outcome") or candidate.get("effective_outcome"),
                "status": candidate.get("status") or candidate.get("aggregate_candidate_status"),
                "best_ask": candidate.get("best_ask"),
                "best_bid": candidate.get("best_bid"),
                "support_weight": candidate.get("support_weight"),
                "conflict_weight": candidate.get("conflict_weight"),
                "supporting_profiles": len(candidate.get("supporting_profiles") or []),
                "failed_checks": "; ".join(str(item) for item in candidate.get("failed_checks") or candidate.get("aggregate_blockers") or []),
            }
        )
    return rows[:limit]


def _packet_summary(packet_rows: list[dict[str, Any]], *, packet_path: Path | None) -> dict[str, Any]:
    statuses = Counter(str(row.get("status") or "unknown") for row in packet_rows)
    actions = Counter(str(row.get("packet_action") or "unknown") for row in packet_rows)
    ready = [row for row in packet_rows if str(row.get("status")).lower() == "ready"]
    return {
        "path": str(packet_path) if packet_path and packet_path.exists() else None,
        "packet_count": len(packet_rows),
        "ready_packet_count": len(ready),
        "status_counts": dict(sorted(statuses.items())),
        "action_counts": dict(sorted(actions.items())),
        "rows": [
            {
                "candidate_id": row.get("candidate_id"),
                "status": row.get("status"),
                "packet_action": row.get("packet_action"),
                "execution_side": row.get("execution_side"),
                "execution_style": row.get("execution_style"),
                "order_type": row.get("order_type"),
                "blockers": "; ".join(str(item) for item in row.get("blockers") or []),
            }
            for row in packet_rows
        ],
    }


def _supervisor_summary(supervisor: dict[str, Any], *, supervisor_path: Path | None) -> dict[str, Any]:
    candidates = _supervisor_rows(supervisor)
    statuses = Counter(str(row.get("status") or "unknown") for row in candidates)
    executions = supervisor.get("executions") if isinstance(supervisor.get("executions"), list) else []
    notifiable = supervisor.get("notifiable_events") if isinstance(supervisor.get("notifiable_events"), list) else []
    return {
        "path": str(supervisor_path) if supervisor_path and supervisor_path.exists() else None,
        "candidate_count": len(candidates),
        "status_counts": dict(sorted(statuses.items())),
        "execution_count": len(executions),
        "notifiable_event_count": len(notifiable),
        "execute_live_requested": supervisor.get("execute_live_requested"),
        "explicit_live_flags_complete": supervisor.get("explicit_live_flags_complete"),
        "rows": [
            {
                "candidate_id": row.get("candidate_id"),
                "status": row.get("status"),
                "packet_action": row.get("packet_action"),
                "packet_blockers": "; ".join(str(item) for item in row.get("packet_blockers") or []),
                "supervisor_blockers": "; ".join(str(item) for item in row.get("supervisor_blockers") or []),
            }
            for row in candidates
        ],
        "executions": executions[-20:],
        "notifiable_events": notifiable[-20:],
    }


def _log_summary(log_rows: list[dict[str, Any]], *, log_path: Path) -> dict[str, Any]:
    states = Counter(str(row.get("state") or ((row.get("latest_tick") or {}).get("state")) or "unknown") for row in log_rows)
    notifiable_events: list[Any] = []
    tick_indexes: list[int] = []
    for row in log_rows:
        tick = row.get("latest_tick") if isinstance(row.get("latest_tick"), dict) else {}
        tick_index = _int(row.get("tick_index") or tick.get("tick_index"))
        if tick_index is not None:
            tick_indexes.append(tick_index)
        notifiable_events.extend(row.get("notifiable_events") or tick.get("notifiable_events") or [])
    return {
        "path": str(log_path) if log_path.exists() else None,
        "tail_count": len(log_rows),
        "state_counts": dict(sorted(states.items())),
        "latest_tick_in_tail": max(tick_indexes) if tick_indexes else None,
        "notifiable_event_count": len(notifiable_events),
        "tail": log_rows[-25:],
    }


def _text_log_summary(path_value: Any, *, tail_lines: int) -> dict[str, Any]:
    if not path_value:
        return {"path": None, "tail": "", "line_count": 0, "error_counts": {}}
    path = Path(path_value)
    lines = _tail_text(path, line_count=tail_lines)
    error_counts = Counter()
    for line in lines:
        lowered = line.lower()
        if "no orderbook exists" in lowered or "404" in lowered:
            error_counts["quote_404_no_orderbook"] += 1
        elif "traceback" in lowered:
            error_counts["traceback"] += 1
        elif "submit_error" in lowered:
            error_counts["submit_error"] += 1
        elif "credential" in lowered:
            error_counts["credentials"] += 1
        elif "connection" in lowered or "timeout" in lowered:
            error_counts["network_or_timeout"] += 1
    return {
        "path": str(path) if path.exists() else str(path),
        "tail": "\n".join(lines),
        "line_count": len(lines),
        "error_counts": dict(sorted(error_counts.items())),
    }


def _snapshot_warnings(
    service: dict[str, Any],
    monitor: dict[str, Any],
    components: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    staleness = _float(service.get("staleness_seconds"))
    if service.get("state") == "running" and staleness is not None and staleness > 300:
        warnings.append("service_state_stale_over_300s")
    monitor_staleness = _float(monitor.get("staleness_seconds"))
    if service.get("state") == "running" and monitor_staleness is not None and monitor_staleness > 90:
        warnings.append("monitor_stale_over_90s")
    disabled = [row["component_id"] for row in components if row.get("disabled")]
    if disabled:
        warnings.append("disabled_components:" + ",".join(disabled))
    return warnings


def _load_component_ledgers(ledger_root: Path, component_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    ledgers: dict[str, dict[str, Any]] = {}
    for component_id in component_ids:
        path = ledger_root / f"candidate_ledger_{component_id}.json"
        ledgers[component_id] = _read_json(path)
    return ledgers


def _resolve_component_ids(
    *,
    ledger_root: Path,
    packet_rows: list[dict[str, Any]],
    supervisor_rows: list[dict[str, Any]],
    requested_component_ids: Iterable[str] | None,
) -> list[str]:
    if requested_component_ids is not None:
        return [str(item) for item in requested_component_ids]
    ordered: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        component_id = str(value or "").strip()
        if not component_id or component_id in seen:
            return
        seen.add(component_id)
        ordered.append(component_id)

    for component_id in V3_FINAL_COMPONENT_IDS:
        add(component_id)
    for row in packet_rows:
        add(row.get("candidate_id") or row.get("packet_id"))
    for row in supervisor_rows:
        add(row.get("candidate_id") or row.get("packet_id"))
    if ledger_root.exists():
        for path in sorted(ledger_root.glob("candidate_ledger_*.json")):
            name = path.stem
            prefix = "candidate_ledger_"
            if name.startswith(prefix):
                add(name[len(prefix) :])
    return ordered


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        with Path(path).open("r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_jsonl_tail(path: Path, *, line_count: int) -> list[dict[str, Any]]:
    rows: deque[dict[str, Any]] = deque(maxlen=line_count)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
    except OSError:
        return []
    return list(rows)


def _tail_text(path: Path, *, line_count: int) -> list[str]:
    if not path.exists():
        return []
    lines: deque[str] = deque(maxlen=line_count)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                lines.append(line.rstrip("\n"))
    except OSError:
        return []
    return list(lines)


def _packet_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("packets") if isinstance(payload.get("packets"), list) else []
    return [row for row in rows if isinstance(row, dict)]


def _supervisor_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("candidate_rows") if isinstance(payload.get("candidate_rows"), list) else []
    if not rows:
        rows = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    return [row for row in rows if isinstance(row, dict)]


def _path_from_pointer(payload: dict[str, Any], keys: tuple[str, ...]) -> Path | None:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    if not value:
        return None
    path = Path(str(value))
    return path if path.exists() else None


def _latest_file(root: Path, pattern: str) -> Path | None:
    if not root.exists():
        return None
    try:
        matches = [path for path in root.rglob(pattern) if path.is_file()]
    except OSError:
        return None
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def _walk_matching_files(base: Path, filename: str) -> Iterable[Path]:
    for dirpath, _dirnames, filenames in os.walk(base, onerror=lambda _error: None):
        if filename in filenames:
            yield Path(dirpath) / filename


def _is_settled(row: dict[str, Any]) -> bool:
    if row.get("realized_pnl_net_usd") is not None:
        return True
    status = str(row.get("settlement_status") or "").lower()
    return status in {"settled", "closed"}


def _is_open_position(row: dict[str, Any]) -> bool:
    return str(row.get("status") or "").lower() == "submitted" and not _is_settled(row)


def _is_not_open(row: dict[str, Any]) -> bool:
    return str(row.get("last_reconciliation_status") or row.get("settlement_status") or "").lower() == "not_open"


def _side(row: dict[str, Any]) -> str:
    return str(row.get("side") or "BUY").upper()


def _entry_price(row: dict[str, Any]) -> float | None:
    quality = row.get("execution_quality") if isinstance(row.get("execution_quality"), dict) else {}
    submission = row.get("submission") if isinstance(row.get("submission"), dict) else {}
    order_request = submission.get("order_request") if isinstance(submission.get("order_request"), dict) else {}
    return (
        _float(quality.get("realized_price"))
        or _float(row.get("price"))
        or _float(order_request.get("observed_execution_price"))
        or _float(order_request.get("observed_best_ask"))
    )


def _entry_cost(row: dict[str, Any]) -> float:
    quality = row.get("execution_quality") if isinstance(row.get("execution_quality"), dict) else {}
    for value in (quality.get("filled_notional_usd"), row.get("estimated_total_cost_usd")):
        parsed = _float(value)
        if parsed is not None:
            return parsed
    price = _entry_price(row)
    size = _float(quality.get("filled_shares")) or _float(row.get("size"))
    if price is None or size is None:
        return 0.0
    return float(price) * float(size)


def _price_bucket(price: float | None) -> str | None:
    if price is None:
        return None
    for low, high in (
        (0.00, 0.05),
        (0.05, 0.10),
        (0.10, 0.15),
        (0.15, 0.20),
        (0.20, 0.25),
        (0.25, 0.30),
        (0.30, 0.35),
        (0.35, 0.40),
        (0.40, 0.45),
        (0.45, 0.50),
        (0.50, 0.55),
        (0.55, 0.60),
        (0.60, 0.70),
        (0.70, 0.80),
        (0.80, 1.00),
    ):
        if low <= float(price) < high:
            return f"{low:.2f}-{high:.2f}"
    return "out_of_policy_range"


def _age_seconds(value: Any, *, now_utc: datetime) -> float | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    return round(max(0.0, (now_utc - parsed).total_seconds()), 3)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _mtime_iso(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _pid_alive(pid: int | None) -> bool | None:
    if pid is None or pid <= 0:
        return None
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return f'"{pid}"' in completed.stdout or f",{pid}," in completed.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "OBSERVER_SCHEMA_VERSION",
    "default_artifact_base",
    "discover_run_roots",
    "load_observer_snapshot",
]
