from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app.data.nodes.polymarket.blockchain.manage_portfolio import PolymarketCredentials, view_trades
from app.data.nodes.polymarket.crypto.history import fetch_current_order_book, normalize_order_book_snapshot
from app.data.pipelines.crypto.options.live_micro_executor import inspect_polymarket_credentials

from crypto_options_app.config import (
    CENTRAL_ACTIVE_PROFILE_POOL,
    CENTRAL_ARTIFACT_ROOT,
    CENTRAL_DB_PATH,
    CryptoOptionsAppConfig,
)
from crypto_options_app.db.runtime_persistence import persist_runtime_validation_report
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.feeds.polymarket_status import build_cached_polymarket_status_provider
from crypto_options_app.reports.live_order_audit import audit_validation_orders_against_exchange
from crypto_options_app.reports.settlement_performance import reconcile_live_run_settlements
from crypto_options_app.reports.system_integrity import HealthBuildOptions, build_system_integrity_health
from crypto_options_app.risk.gates import RiskLimits
from crypto_options_app.signals.profile_signals import evaluate_profile_pressure_gate
from crypto_options_app.strategies.registry import get_strategy
from crypto_options_app.trading.executor_boundary import ExecutorBoundaryConfig
from crypto_options_app.trading.live_candidates import (
    LiveMarketCandidateVerification,
    VerifiedLiveMarketCandidate,
    scenario_from_verified_candidate,
    verify_live_market_candidate,
)
from crypto_options_app.trading.live_preflight import LiveEnvironmentFlags
from crypto_options_app.trading.polymarket_supervised_executor import (
    PolymarketSupervisedExecutorConfig,
    build_polymarket_supervised_executor,
)
from crypto_options_app.workers.core_flow_live_runner import (
    _build_order_audit_payload,
    _event_targets_to_try,
    _estimated_spent_from_rows,
    _is_runtime_breaker,
    _live_candidate_row_from_target,
    _projected_minimal_event_cost,
    _single_run_lock,
)
from crypto_options_app.workers.runtime_adapter import (
    RuntimeScenario,
    SupervisedRuntimeConfig,
    validate_all_strategy_scenarios,
)


DEFAULT_ARTIFACT_ROOT = CENTRAL_ARTIFACT_ROOT
DEFAULT_DB_PATH = CENTRAL_DB_PATH
DEFAULT_PROFILE_CACHE = Path("local/co4/profile-store-full-20260602T170705Z/crypto_options_profile_signal_report_20260602T171438Z.json")
DEFAULT_ACTIVE_PROFILE_POOL = CENTRAL_ACTIVE_PROFILE_POOL

FIRST_SIX_SIGNAL_STRATEGY_IDS = (
    "event_context_outcome_v1",
    "buying_ahead_pre_event_v1",
    "s_tier_outcome_consensus_cashout_v1",
    "indicator_confirmed_outcome_v1",
    "s_tier_outcome_hold_to_settlement_v1",
    "a_fallback_outcome_probe_v1",
)

SIGNAL_LIVE_NON_BREAKING_NO_FILL_BLOCKERS = {
    "submit_error_no_fill",
    "rejected_no_fill",
}


@dataclass(frozen=True)
class SignalLiveRunConfig:
    run_id: str
    strategy_ids: tuple[str, ...] = FIRST_SIX_SIGNAL_STRATEGY_IDS
    max_event_cycles: int = 2
    max_cycles_per_event_slug: int = 1
    min_repeat_event_seconds: float = 20.0
    total_budget_cap_usd: float = 15.0
    max_wall_seconds: float = 1200.0
    poll_seconds: float = 10.0
    monitor_interval_seconds: float = 60.0
    min_seconds_remaining: float = 75.0
    symbols: tuple[str, ...] = ("BTC", "ETH")
    db_path: Path = DEFAULT_DB_PATH
    artifact_root: Path = DEFAULT_ARTIFACT_ROOT
    active_profile_pool: Path = DEFAULT_ACTIVE_PROFILE_POOL
    profile_report_cache: Path = DEFAULT_PROFILE_CACHE
    active_profile_pool_limit: int = 120
    max_workers: int = 8
    page_limit: int = 120
    operator: str = "codex-automation"
    reason: str = "Signal-driven live structural validation"
    live_flags: LiveEnvironmentFlags = field(default_factory=lambda: LiveEnvironmentFlags(True, True, True))
    per_strategy_budget_cap_usd: float | None = None
    enable_lane_stop_gates: bool = False
    lane_loss_streak_limit: int = 3
    lane_stop_min_settled: int = 3
    lane_stop_max_win_rate: float = 0.50
    lane_stop_max_pnl_usd: float = 0.0
    target_filled_event_count: int | None = None
    target_filled_events_per_strategy: int | None = None
    lane_stop_min_realized_pnl_usd: float | None = None
    lane_stop_min_win_rate: float | None = None
    strategy_order_notional_usd: dict[str, float] = field(default_factory=dict)
    prevent_duplicate_event_tokens: bool = False


@dataclass(frozen=True)
class SignalLiveEventResult:
    event_index: int
    event_slug: str | None
    status: str
    generated_at_utc: str
    estimated_spent_usd: float
    runtime_blockers: tuple[str, ...]
    artifact_json: str


@dataclass(frozen=True)
class SignalLiveRunResult:
    run_id: str
    status: str
    generated_at_utc: str
    event_results: tuple[SignalLiveEventResult, ...]
    estimated_spent_usd: float
    blockers: tuple[str, ...]
    artifact_json: str
    health_snapshot_json: str | None
    order_audit_json: str | None
    manual_orders_avoided: bool = True


@dataclass(frozen=True)
class StrategySignalPlan:
    strategy_id: str
    verification: LiveMarketCandidateVerification | None
    blockers: tuple[str, ...]
    signal_context: dict[str, Any]


@dataclass(frozen=True)
class EventSignalPlan:
    event_slug: str | None
    monitor_artifact: str | None
    plans: tuple[StrategySignalPlan, ...]
    blockers: tuple[str, ...] = ()


SignalPlanProvider = Callable[[set[str], SignalLiveRunConfig], EventSignalPlan]
Submitter = Callable[[dict[str, Any]], dict[str, Any]]


def run_signal_live_test(
    config: SignalLiveRunConfig,
    *,
    signal_plan_provider: SignalPlanProvider | None = None,
    submitter: Submitter | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SignalLiveRunResult:
    started = time.monotonic()
    artifact_root = Path(config.artifact_root)
    validation_dir = artifact_root / "live-validation"
    report_dir = artifact_root / "reports"
    automation_dir = artifact_root / "automation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    automation_dir.mkdir(parents=True, exist_ok=True)
    initialize_schema(config.db_path)
    lock_path = automation_dir / "signal_live_test.lock"
    result_path = validation_dir / f"{config.run_id}.json"
    status_path = automation_dir / "signal_live_status.json"
    active_process_path = automation_dir / "signal_live_active_process.json"
    event_results: list[SignalLiveEventResult] = []
    all_strategy_rows: list[dict[str, Any]] = []
    event_cycle_counts: dict[str, int] = {}
    event_last_cycle_at: dict[str, float] = {}
    blockers: list[str] = []
    run_started_at_utc = _utc_now()
    _write_json(
        active_process_path,
        {
            "schema_version": "crypto_options_signal_live_active_process_v1",
            "run_id": config.run_id,
            "pid": os.getpid(),
            "script": str(Path(sys.argv[0]).resolve()) if sys.argv else None,
            "artifact_root": str(artifact_root),
            "started_at_utc": run_started_at_utc,
            "status": "running",
            "strategies": list(config.strategy_ids),
            "manual_orders_avoided": True,
            "live_flags_scoped_to_child": True,
            "global_api_live_flags_remain_false": True,
        },
    )
    estimated_spent = 0.0
    spent_by_strategy: dict[str, float] = {strategy_id: 0.0 for strategy_id in config.strategy_ids}
    disabled_strategy_ids: set[str] = set()
    lane_stop_state: dict[str, dict[str, Any]] = {}
    signal_plan_provider = signal_plan_provider or build_live_signal_plan
    status_provider = None if submitter is not None else build_cached_polymarket_status_provider(fresh_seconds=60.0, ttl_seconds=600.0)

    with _single_run_lock(lock_path, run_id=config.run_id):
        _write_json(
            status_path,
            {
                "schema_version": "crypto_options_signal_live_status_v1",
                "run_id": config.run_id,
                "status": "running",
                "started_at_utc": _utc_now(),
                "event_results": [],
                "estimated_spent_usd": 0.0,
                "spent_by_strategy_usd": spent_by_strategy,
                "disabled_strategy_ids": [],
                "lane_stop_state": {},
                "strategy_order_notional_usd": _rounded_spend_by_strategy(config.strategy_order_notional_usd),
                "prevent_duplicate_event_tokens": bool(config.prevent_duplicate_event_tokens),
                "manual_orders_avoided": True,
            },
        )
        _write_json(
            active_process_path,
            {
                "schema_version": "crypto_options_signal_live_active_process_v1",
                "run_id": config.run_id,
                "pid": os.getpid(),
                "script": str(Path(sys.argv[0]).resolve()) if sys.argv else None,
                "artifact_root": str(artifact_root),
                "started_at_utc": run_started_at_utc,
                "status": "running",
                "strategies": list(config.strategy_ids),
                "manual_orders_avoided": True,
                "live_flags_scoped_to_child": True,
                "global_api_live_flags_remain_false": True,
            },
        )
        last_monitor = 0.0
        while len(event_results) < config.max_event_cycles:
            completion = _filled_event_target_completion(all_strategy_rows, config=config)
            if _filled_event_targets_configured(config) and completion["target_reached"]:
                break
            if time.monotonic() - started > config.max_wall_seconds:
                blockers.append("signal_live_wall_clock_limit_reached")
                break
            if estimated_spent >= config.total_budget_cap_usd:
                blockers.append("signal_live_budget_cap_reached")
                break

            processed_events = _event_slugs_at_cycle_limit(event_cycle_counts, config)
            plan = signal_plan_provider(processed_events, config)
            if disabled_strategy_ids and disabled_strategy_ids.issuperset(config.strategy_ids):
                blockers.append("all_strategy_lanes_stopped")
                break
            executable = [
                item
                for item in plan.plans
                if item.strategy_id not in disabled_strategy_ids
                and item.verification is not None
                and item.verification.verified
                and not _strategy_budget_cap_reached(item.strategy_id, spent_by_strategy, config)
                and not _strategy_budget_cap_would_be_exceeded(item.strategy_id, item.verification, spent_by_strategy, config)
            ]
            if not executable:
                if time.monotonic() - last_monitor >= config.monitor_interval_seconds:
                    last_monitor = time.monotonic()
                    _write_json(
                        status_path,
                        {
                            "schema_version": "crypto_options_signal_live_status_v1",
                            "run_id": config.run_id,
                            "status": "waiting_for_signal_candidate",
                            "generated_at_utc": _utc_now(),
                            "processed_event_count": len(event_results),
                            "event_slug": plan.event_slug,
                            "blockers": _plan_blockers(plan),
                            "estimated_spent_usd": round(estimated_spent, 6),
                            "spent_by_strategy_usd": _rounded_spend_by_strategy(spent_by_strategy),
                            "disabled_strategy_ids": sorted(disabled_strategy_ids),
                            "lane_stop_state": lane_stop_state,
                            "filled_event_target": completion,
                            "manual_orders_avoided": True,
                        },
                    )
                sleep(max(1.0, config.poll_seconds))
                continue
            if plan.event_slug and event_cycle_counts.get(plan.event_slug, 0) >= max(1, config.max_cycles_per_event_slug):
                sleep(max(1.0, config.poll_seconds))
                continue
            if plan.event_slug and event_cycle_counts.get(plan.event_slug, 0) > 0:
                elapsed_since_event_cycle = time.monotonic() - event_last_cycle_at.get(plan.event_slug, 0.0)
                if elapsed_since_event_cycle < max(0.0, config.min_repeat_event_seconds):
                    if time.monotonic() - last_monitor >= config.monitor_interval_seconds:
                        last_monitor = time.monotonic()
                        _write_json(
                            status_path,
                            {
                                "schema_version": "crypto_options_signal_live_status_v1",
                                "run_id": config.run_id,
                                "status": "waiting_for_repeat_event_throttle",
                                "generated_at_utc": _utc_now(),
                                "processed_event_count": len(event_results),
                                "event_slug": plan.event_slug,
                                "event_cycle_counts": event_cycle_counts,
                                "seconds_until_next_repeat": round(max(0.0, config.min_repeat_event_seconds - elapsed_since_event_cycle), 3),
                                "estimated_spent_usd": round(estimated_spent, 6),
                                "manual_orders_avoided": True,
                            },
                        )
                    sleep(max(1.0, config.poll_seconds))
                    continue

            projected = estimated_spent + sum(
                _projected_strategy_event_cost(item.strategy_id, item.verification, config=config)
                for item in executable
                if item.verification is not None
            )
            if projected > config.total_budget_cap_usd + 1e-9:
                blockers.append("signal_live_budget_cap_would_be_exceeded")
                break

            event_index = len(event_results) + 1
            strategy_rows: list[dict[str, Any]] = []
            runtime_blockers: list[str] = []
            for item in plan.plans:
                if item.strategy_id in disabled_strategy_ids:
                    strategy_rows.append(_lane_stopped_strategy_row(item.strategy_id, event_slug=plan.event_slug, details=lane_stop_state.get(item.strategy_id, {})))
                    continue
                if _strategy_budget_cap_reached(item.strategy_id, spent_by_strategy, config) or _strategy_budget_cap_would_be_exceeded(item.strategy_id, item.verification, spent_by_strategy, config):
                    strategy_rows.append(_strategy_budget_blocked_row(item.strategy_id, event_slug=plan.event_slug, spent_by_strategy=spent_by_strategy, config=config))
                    continue
                if item.verification is None or not item.verification.verified:
                    strategy_rows.append(_blocked_strategy_row(item, event_slug=plan.event_slug))
                    continue
                result_rows, result_blockers = _run_one_strategy(
                    config=config,
                    event_index=event_index,
                    item=item,
                    submitter=submitter,
                    status_provider=status_provider,
                )
                strategy_rows.extend(result_rows)
                runtime_blockers.extend(result_blockers)
                row_spent = _estimated_spent_from_rows(result_rows)
                estimated_spent += row_spent
                spent_by_strategy[item.strategy_id] = spent_by_strategy.get(item.strategy_id, 0.0) + row_spent
                if result_blockers:
                    break
                if estimated_spent >= config.total_budget_cap_usd:
                    runtime_blockers.append("signal_live_budget_cap_reached_after_execution")
                    break
            all_strategy_rows.extend(strategy_rows)
            event_spent = _estimated_spent_from_rows(strategy_rows)
            event_artifact_path = validation_dir / f"{config.run_id}_event_{event_index}.json"
            event_window_start_utc = _event_window_start_utc(plan.event_slug)
            _write_json(
                event_artifact_path,
                {
                    "schema_version": "crypto_options_signal_live_event_v1",
                    "run_id": config.run_id,
                    "event_index": event_index,
                    "event_slug": plan.event_slug,
                    "event_window_start_utc": event_window_start_utc,
                    "generated_at_utc": _utc_now(),
                    "monitor_artifact": plan.monitor_artifact,
                    "strategy_rows": strategy_rows,
                    "estimated_spent_usd": round(event_spent, 6),
                    "cumulative_spent_usd": round(estimated_spent, 6),
                    "spent_by_strategy_usd": _rounded_spend_by_strategy(spent_by_strategy),
                    "disabled_strategy_ids": sorted(disabled_strategy_ids),
                    "lane_stop_state": lane_stop_state,
                    "runtime_blockers": runtime_blockers,
                    "manual_orders_avoided": True,
                },
            )
            event_result = SignalLiveEventResult(
                event_index=event_index,
                event_slug=plan.event_slug,
                status="validated" if not runtime_blockers else "stopped_on_breaker",
                generated_at_utc=_utc_now(),
                estimated_spent_usd=round(event_spent, 6),
                runtime_blockers=tuple(runtime_blockers),
                artifact_json=str(event_artifact_path),
            )
            event_results.append(event_result)
            if plan.event_slug:
                event_cycle_counts[plan.event_slug] = event_cycle_counts.get(plan.event_slug, 0) + 1
                event_last_cycle_at[plan.event_slug] = time.monotonic()
            partial_payload = _signal_run_payload(
                config=config,
                status="running",
                generated_at_utc=_utc_now(),
                event_cycle_counts=event_cycle_counts,
                estimated_spent=estimated_spent,
                spent_by_strategy=spent_by_strategy,
                event_results=event_results,
                all_strategy_rows=all_strategy_rows,
                blockers=blockers,
                health_path=None,
                order_audit_path=None,
                lane_stop_state=lane_stop_state,
                disabled_strategy_ids=disabled_strategy_ids,
            )
            _write_json(result_path, partial_payload)
            if config.enable_lane_stop_gates:
                decisions = _evaluate_lane_stop_gates_from_run_artifact(
                    run_artifact_path=result_path,
                    config=config,
                    db_path=config.db_path,
                    report_dir=report_dir,
                    disabled_strategy_ids=disabled_strategy_ids,
                )
                for strategy_id, decision in decisions.items():
                    disabled_strategy_ids.add(strategy_id)
                    lane_stop_state[strategy_id] = decision
            _write_json(
                status_path,
                {
                    "schema_version": "crypto_options_signal_live_status_v1",
                    "run_id": config.run_id,
                    "status": "running",
                    "generated_at_utc": event_result.generated_at_utc,
                    "event_results": [_event_result_dict(item) for item in event_results],
                    "event_cycle_counts": event_cycle_counts,
                    "estimated_spent_usd": round(estimated_spent, 6),
                    "spent_by_strategy_usd": _rounded_spend_by_strategy(spent_by_strategy),
                    "disabled_strategy_ids": sorted(disabled_strategy_ids),
                    "lane_stop_state": lane_stop_state,
                    "strategy_order_notional_usd": _rounded_spend_by_strategy(config.strategy_order_notional_usd),
                    "prevent_duplicate_event_tokens": bool(config.prevent_duplicate_event_tokens),
                    "filled_event_target": _filled_event_target_completion(all_strategy_rows, config=config),
                    "manual_orders_avoided": True,
                },
            )
            if runtime_blockers:
                blockers.extend(runtime_blockers)
                break

        final_target = _filled_event_target_completion(all_strategy_rows, config=config)
        if _filled_event_targets_configured(config) and not final_target["target_reached"] and not blockers:
            blockers.append("filled_event_target_not_reached")
        status = "validated" if not blockers and (_targetless_cycle_cap_reached(event_results, config) or final_target["target_reached"]) else "blocked"
        health_path = report_dir / f"{config.run_id}_health_after.json"
        order_audit_path = report_dir / f"{config.run_id}_order_audit.json"
        latest_order_audit_path = report_dir / "live_order_integrity_audit_latest.json"
        audit_payload = _build_order_audit_payload_with_retry(all_strategy_rows, exchange_trades=[] if submitter is not None else None)
        _write_json(order_audit_path, audit_payload)
        _write_json(latest_order_audit_path, audit_payload)
        health_payload = build_system_integrity_health(
            CryptoOptionsAppConfig(db_path=config.db_path),
            options=HealthBuildOptions(artifact_root=artifact_root),
        )
        _write_json(health_path, health_payload)
        final_payload = _signal_run_payload(
            config=config,
            status=status,
            generated_at_utc=_utc_now(),
            event_cycle_counts=event_cycle_counts,
            estimated_spent=estimated_spent,
            spent_by_strategy=spent_by_strategy,
            event_results=event_results,
            all_strategy_rows=all_strategy_rows,
            blockers=blockers,
            health_path=health_path,
            order_audit_path=order_audit_path,
            lane_stop_state=lane_stop_state,
            disabled_strategy_ids=disabled_strategy_ids,
        )
        _write_json(result_path, final_payload)
        _write_json(
            status_path,
            {
                "schema_version": "crypto_options_signal_live_status_v1",
                "run_id": config.run_id,
                "status": status,
                "generated_at_utc": final_payload["generated_at_utc"],
                "event_results": final_payload["event_results"],
                "event_cycle_counts": event_cycle_counts,
                "estimated_spent_usd": final_payload["estimated_spent_usd"],
                "spent_by_strategy_usd": final_payload["spent_by_strategy_usd"],
                "disabled_strategy_ids": sorted(disabled_strategy_ids),
                "lane_stop_state": lane_stop_state,
                "filled_event_target": final_target,
                "blockers": blockers,
                "artifact_json": str(result_path),
                "manual_orders_avoided": True,
            },
        )
        _write_json(
            active_process_path,
            {
                "schema_version": "crypto_options_signal_live_active_process_v1",
                "run_id": config.run_id,
                "pid": os.getpid(),
                "script": str(Path(sys.argv[0]).resolve()) if sys.argv else None,
                "artifact_root": str(artifact_root),
                "started_at_utc": run_started_at_utc,
                "completed_at_utc": final_payload["generated_at_utc"],
                "status": status,
                "strategies": list(config.strategy_ids),
                "manual_orders_avoided": True,
                "live_flags_scoped_to_child": True,
                "global_api_live_flags_remain_false": True,
            },
        )
    return SignalLiveRunResult(
        run_id=config.run_id,
        status=status,
        generated_at_utc=final_payload["generated_at_utc"],
        event_results=tuple(event_results),
        estimated_spent_usd=round(estimated_spent, 6),
        blockers=tuple(blockers),
        artifact_json=str(result_path),
        health_snapshot_json=str(health_path),
        order_audit_json=str(order_audit_path),
    )


def _event_slugs_at_cycle_limit(event_cycle_counts: dict[str, int], config: SignalLiveRunConfig) -> set[str]:
    max_cycles = max(1, int(config.max_cycles_per_event_slug))
    return {event_slug for event_slug, count in event_cycle_counts.items() if count >= max_cycles}


def _strategy_budget_cap_reached(strategy_id: str, spent_by_strategy: dict[str, float], config: SignalLiveRunConfig) -> bool:
    if config.per_strategy_budget_cap_usd is None:
        return False
    return spent_by_strategy.get(strategy_id, 0.0) >= float(config.per_strategy_budget_cap_usd)


def _strategy_budget_cap_would_be_exceeded(
    strategy_id: str,
    verification: LiveMarketCandidateVerification | None,
    spent_by_strategy: dict[str, float],
    config: SignalLiveRunConfig,
) -> bool:
    if config.per_strategy_budget_cap_usd is None or verification is None:
        return False
    projected = spent_by_strategy.get(strategy_id, 0.0) + _projected_strategy_event_cost(strategy_id, verification, config=config)
    return projected > float(config.per_strategy_budget_cap_usd) + 1e-9


def _projected_strategy_event_cost(
    strategy_id: str,
    verification: LiveMarketCandidateVerification,
    *,
    config: SignalLiveRunConfig,
) -> float:
    target_notional = _target_order_notional_usd(strategy_id, config=config)
    if target_notional <= 1.0:
        return _projected_minimal_event_cost(verification, strategy_count=1)
    ask = 1.0
    if verification.candidate is not None:
        ask = max(0.01, float(verification.candidate.best_ask))
    target_shares = _ceil_to_cents(target_notional / ask)
    return max(target_notional, target_shares * ask) + 0.10


def _target_order_notional_usd(strategy_id: str, *, config: SignalLiveRunConfig) -> float:
    value = _optional_float(config.strategy_order_notional_usd.get(strategy_id))
    if value is None or value <= 0:
        return 1.0
    return float(value)


def _shares_for_target_notional(
    verification: LiveMarketCandidateVerification,
    *,
    target_notional_usd: float,
) -> float:
    ask = 1.0
    if verification.candidate is not None:
        ask = max(0.01, float(verification.candidate.best_ask))
    return max(1.0, _ceil_to_cents(float(target_notional_usd) / ask))


def _ceil_to_cents(value: float) -> float:
    return float(int(value * 100.0 + 0.999999999) / 100.0)


def _event_window_start_utc(event_slug: str | None) -> str | None:
    if not event_slug:
        return None
    raw = str(event_slug).rsplit("-", 1)[-1]
    try:
        timestamp = int(raw)
    except ValueError:
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _rounded_spend_by_strategy(spent_by_strategy: dict[str, float]) -> dict[str, float]:
    return {strategy_id: round(float(value), 6) for strategy_id, value in sorted(spent_by_strategy.items())}


def _signal_run_payload(
    *,
    config: SignalLiveRunConfig,
    status: str,
    generated_at_utc: str,
    event_cycle_counts: dict[str, int],
    estimated_spent: float,
    spent_by_strategy: dict[str, float],
    event_results: list[SignalLiveEventResult],
    all_strategy_rows: list[dict[str, Any]],
    blockers: list[str],
    health_path: Path | None,
    order_audit_path: Path | None,
    lane_stop_state: dict[str, dict[str, Any]],
    disabled_strategy_ids: set[str],
) -> dict[str, Any]:
    return {
        "schema_version": "crypto_options_signal_live_run_v1",
        "run_id": config.run_id,
        "status": status,
        "generated_at_utc": generated_at_utc,
        "strategy_ids": list(config.strategy_ids),
        "max_event_cycles": config.max_event_cycles,
        "max_cycles_per_event_slug": config.max_cycles_per_event_slug,
        "min_repeat_event_seconds": config.min_repeat_event_seconds,
        "total_budget_cap_usd": config.total_budget_cap_usd,
        "per_strategy_budget_cap_usd": config.per_strategy_budget_cap_usd,
        "event_cycle_counts": event_cycle_counts,
        "estimated_spent_usd": round(estimated_spent, 6),
        "spent_by_strategy_usd": _rounded_spend_by_strategy(spent_by_strategy),
        "disabled_strategy_ids": sorted(disabled_strategy_ids),
        "lane_stop_state": lane_stop_state,
        "target_filled_event_count": config.target_filled_event_count,
        "target_filled_events_per_strategy": config.target_filled_events_per_strategy,
        "lane_stop_min_realized_pnl_usd": config.lane_stop_min_realized_pnl_usd,
        "lane_stop_min_win_rate": config.lane_stop_min_win_rate,
        "strategy_order_notional_usd": _rounded_spend_by_strategy(config.strategy_order_notional_usd),
        "prevent_duplicate_event_tokens": bool(config.prevent_duplicate_event_tokens),
        "filled_event_target": _filled_event_target_completion(all_strategy_rows, config=config),
        "event_results": [_event_result_dict(item) for item in event_results],
        "strategy_rows": all_strategy_rows,
        "blockers": blockers,
        "health_snapshot_json": None if health_path is None else str(health_path),
        "order_audit_json": None if order_audit_path is None else str(order_audit_path),
        "manual_orders_avoided": True,
    }


def _evaluate_lane_stop_gates_from_run_artifact(
    *,
    run_artifact_path: Path,
    config: SignalLiveRunConfig,
    db_path: Path,
    report_dir: Path,
    disabled_strategy_ids: set[str],
) -> dict[str, dict[str, Any]]:
    try:
        settlement_report = reconcile_live_run_settlements(
            run_artifact_path=run_artifact_path,
            db_path=db_path,
            report_dir=report_dir,
        )
    except Exception as exc:  # noqa: BLE001 - settlement provider errors should not crash a live structural runner.
        _ = exc
        return {}
    decisions = _lane_stop_decisions_from_settlement_report(settlement_report, config=config, already_disabled=disabled_strategy_ids)
    return decisions


def _filled_event_targets_configured(config: SignalLiveRunConfig) -> bool:
    return config.target_filled_event_count is not None or config.target_filled_events_per_strategy is not None


def _targetless_cycle_cap_reached(event_results: list[SignalLiveEventResult], config: SignalLiveRunConfig) -> bool:
    return not _filled_event_targets_configured(config) and len(event_results) >= config.max_event_cycles


def _filled_event_target_completion(rows: list[dict[str, Any]], *, config: SignalLiveRunConfig) -> dict[str, Any]:
    global_events: set[str] = set()
    by_strategy: dict[str, set[str]] = {strategy_id: set() for strategy_id in config.strategy_ids}
    for row in rows:
        if not _row_is_filled_buy(row):
            continue
        event_slug = str(row.get("event_slug") or row.get("event_key") or "").strip()
        strategy_id = str(row.get("strategy_id") or "").strip()
        if not event_slug:
            continue
        global_events.add(event_slug)
        if strategy_id:
            by_strategy.setdefault(strategy_id, set()).add(event_slug)
    global_target = config.target_filled_event_count
    per_strategy_target = config.target_filled_events_per_strategy
    global_reached = global_target is None or len(global_events) >= int(global_target)
    per_strategy_counts = {strategy_id: len(events) for strategy_id, events in sorted(by_strategy.items())}
    per_strategy_reached = per_strategy_target is None or all(
        per_strategy_counts.get(strategy_id, 0) >= int(per_strategy_target)
        for strategy_id in config.strategy_ids
    )
    targets_configured = _filled_event_targets_configured(config)
    return {
        "global_filled_event_count": len(global_events),
        "target_filled_event_count": global_target,
        "filled_event_count_by_strategy": per_strategy_counts,
        "target_filled_events_per_strategy": per_strategy_target,
        "target_reached": bool(targets_configured and global_reached and per_strategy_reached),
    }


def _row_is_filled_buy(row: dict[str, Any]) -> bool:
    if row.get("status") != "live_structural_executed":
        return False
    if row.get("order_status") != "filled":
        return False
    side = str(row.get("side") or row.get("candidate_side") or "").upper()
    return side in {"", "BUY"}


def _lane_stop_decisions_from_settlement_report(
    settlement_report: dict[str, Any],
    *,
    config: SignalLiveRunConfig,
    already_disabled: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    already_disabled = already_disabled or set()
    if not config.enable_lane_stop_gates:
        return {}
    summary = settlement_report.get("summary") if isinstance(settlement_report.get("summary"), dict) else {}
    by_strategy = summary.get("by_strategy") if isinstance(summary.get("by_strategy"), dict) else {}
    rows_by_strategy: dict[str, list[dict[str, Any]]] = {}
    for row in settlement_report.get("rows") or []:
        if not isinstance(row, dict) or row.get("status") != "settled":
            continue
        strategy_id = str(row.get("strategy_id") or "")
        if strategy_id:
            rows_by_strategy.setdefault(strategy_id, []).append(row)
    decisions: dict[str, dict[str, Any]] = {}
    for strategy_id, stats in by_strategy.items():
        if strategy_id in already_disabled:
            continue
        settled_count = int(float(stats.get("settled_position_count") or 0))
        win_rate = _optional_float(stats.get("win_rate"))
        realized_pnl = _optional_float(stats.get("realized_pnl_usd"))
        loss_streak = _current_loss_streak(rows_by_strategy.get(strategy_id, []))
        if (
            config.lane_stop_min_realized_pnl_usd is not None
            and realized_pnl is not None
            and realized_pnl <= float(config.lane_stop_min_realized_pnl_usd)
        ):
            decisions[strategy_id] = {
                "status": "blocked",
                "gate": "lane_realized_pnl_floor_stop",
                "settled_position_count": settled_count,
                "loss_streak": loss_streak,
                "win_rate": win_rate,
                "realized_pnl_usd": realized_pnl,
                "thresholds": {
                    "lane_stop_min_realized_pnl_usd": config.lane_stop_min_realized_pnl_usd,
                },
            }
            continue
        if (
            config.lane_stop_min_win_rate is not None
            and settled_count >= max(1, int(config.lane_stop_min_settled))
            and win_rate is not None
            and win_rate < float(config.lane_stop_min_win_rate)
        ):
            decisions[strategy_id] = {
                "status": "blocked",
                "gate": "lane_win_rate_floor_stop",
                "settled_position_count": settled_count,
                "loss_streak": loss_streak,
                "win_rate": win_rate,
                "realized_pnl_usd": realized_pnl,
                "thresholds": {
                    "lane_stop_min_settled": config.lane_stop_min_settled,
                    "lane_stop_min_win_rate": config.lane_stop_min_win_rate,
                },
            }
            continue
        if (
            settled_count >= max(1, int(config.lane_stop_min_settled))
            and loss_streak >= max(1, int(config.lane_loss_streak_limit))
            and win_rate is not None
            and win_rate < float(config.lane_stop_max_win_rate)
            and realized_pnl is not None
            and realized_pnl < float(config.lane_stop_max_pnl_usd)
        ):
            decisions[strategy_id] = {
                "status": "blocked",
                "gate": "lane_loss_streak_quality_stop",
                "settled_position_count": settled_count,
                "loss_streak": loss_streak,
                "win_rate": win_rate,
                "realized_pnl_usd": realized_pnl,
                "thresholds": {
                    "lane_loss_streak_limit": config.lane_loss_streak_limit,
                    "lane_stop_min_settled": config.lane_stop_min_settled,
                    "lane_stop_max_win_rate": config.lane_stop_max_win_rate,
                    "lane_stop_max_pnl_usd": config.lane_stop_max_pnl_usd,
                },
            }
    return decisions


def _current_loss_streak(rows: list[dict[str, Any]]) -> int:
    streak = 0
    for row in reversed(rows):
        pnl = _optional_float(row.get("pnl_usd"))
        if pnl is None:
            continue
        if pnl <= 0:
            streak += 1
            continue
        break
    return streak


def build_live_signal_plan(processed_events: set[str], config: SignalLiveRunConfig) -> EventSignalPlan:
    monitor_artifact = _run_profile_monitor(config)
    monitor = _read_json(Path(monitor_artifact)) if monitor_artifact else {}
    preferred_event_slug = _preferred_profile_event_slug(
        monitor,
        processed_events=processed_events,
        min_seconds_remaining=config.min_seconds_remaining,
    )
    event_context = _select_event_context_candidate(processed_events, config, preferred_event_slug=preferred_event_slug)
    profile_driven = any(_strategy_uses_profile_signal(strategy_id) for strategy_id in config.strategy_ids)
    event_slug = _selected_event_slug(event_context, monitor, preferred_event_slug=preferred_event_slug)
    if profile_driven and preferred_event_slug:
        verified_slug = None
        if event_context.candidate is not None:
            verified_slug = event_context.candidate.event_slug or event_context.candidate.event_key
        event_slug = preferred_event_slug if verified_slug == preferred_event_slug else None
    profile_candidates = _profile_candidates_for_event(monitor, event_slug=event_slug)
    broad_profile_candidates = _profile_candidates_for_event(monitor, event_slug=None)
    plans = []
    used_token_ids: set[str] = set()
    for strategy_id in config.strategy_ids:
        strategy_used_token_ids = used_token_ids if config.prevent_duplicate_event_tokens else set()
        plan = _strategy_plan(
            strategy_id,
            event_context=event_context,
            profile_candidates=profile_candidates,
            broad_profile_candidates=broad_profile_candidates,
            monitor_artifact=monitor_artifact,
            used_token_ids=strategy_used_token_ids,
        )
        if config.prevent_duplicate_event_tokens and plan.verification is not None and plan.verification.candidate is not None:
            used_token_ids.add(plan.verification.candidate.token_id)
        plans.append(plan)
    return EventSignalPlan(event_slug=event_slug, monitor_artifact=monitor_artifact, plans=tuple(plans))


def _strategy_uses_profile_signal(strategy_id: str) -> bool:
    return strategy_id not in {"event_context_outcome_v1", "event_context_outcome_v2"}


def _run_one_strategy(
    *,
    config: SignalLiveRunConfig,
    event_index: int,
    item: StrategySignalPlan,
    submitter: Submitter | None,
    status_provider: Callable[[], dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    assert item.verification is not None
    assert item.verification.candidate is not None
    flags = config.live_flags
    executor_boundary = ExecutorBoundaryConfig(
        supervised_runtime_gate=True,
        ledger_gate=True,
        risk_gate=True,
        reconciliation_gate=True,
        execution_approved=flags.execution_approved,
        live_risk_acknowledged=flags.risk_acknowledged,
    )
    target_notional_usd = _target_order_notional_usd(item.strategy_id, config=config)
    max_position_cost_usd = max(5.0, target_notional_usd + 0.50)
    executor = build_polymarket_supervised_executor(
        PolymarketSupervisedExecutorConfig(
            operator=config.operator,
            reason=f"{config.reason}; {item.strategy_id}; event {event_index}/{config.max_event_cycles}",
            order_type=_structural_order_type(item.strategy_id),
            price_slippage_cents=_structural_slippage_cents(item.strategy_id),
            max_position_cost_usd=max_position_cost_usd,
            min_order_notional_usd=max(1.0, target_notional_usd),
            status_provider=status_provider,
            allow_status_unavailable_with_verified_market=True,
        ),
        submitter=submitter,
    )
    report = validate_all_strategy_scenarios(
        SupervisedRuntimeConfig(
            run_id=f"{config.run_id}:event_{event_index}:{item.strategy_id}",
            mode="supervised_live",
            executor_boundary=executor_boundary,
            max_trades_per_strategy=1,
            minimal_sizing=True,
            allow_live_submission=flags.live_execute,
            live_environment_approved=flags.ready,
            credentials_ready=True if submitter is not None else bool(inspect_polymarket_credentials().get("ready")),
            supervised_executor=executor,
            risk_limits=RiskLimits(max_quote_age_seconds=15.0, max_profile_age_seconds=600.0, max_signal_age_seconds=120.0),
        ),
        scenario=scenario_from_verified_candidate(
            item.verification.candidate,
            shares=_shares_for_target_notional(item.verification, target_notional_usd=target_notional_usd),
            signal_context=item.signal_context,
            signal_age_seconds=float(item.signal_context.get("signal_age_seconds") or 1.0),
            profile_age_seconds=float(item.signal_context.get("profile_age_seconds") or 20.0),
        ),
        specs=(get_strategy(item.strategy_id),),
    )
    persist_runtime_validation_report(report, config.db_path)
    rows = []
    for result in report.results:
        row = result.to_candidate_row()
        row["status"] = result.status
        if result.status == "live_structural_executed" and result.order_status == "unfilled":
            row["status"] = "live_structural_unfilled"
        row["attribution"] = result.attribution
        row["signal_context"] = item.signal_context
        row["side"] = "BUY"
        row["run_id"] = report.run_id
        rows.append(row)
    blockers = [
        blocker
        for result in report.results
        for blocker in result.blockers
        if _is_signal_live_runtime_breaker(blocker)
    ]
    return rows, blockers


def _is_signal_live_runtime_breaker(blocker: str) -> bool:
    if blocker in SIGNAL_LIVE_NON_BREAKING_NO_FILL_BLOCKERS:
        return False
    return _is_runtime_breaker(blocker)


def _structural_slippage_cents(strategy_id: str) -> float:
    if strategy_id in {
        "grid_buyer_band_rebound_v1",
        "grid_buyer_band_rebound_v2",
        "hedger_ratio_replication_v1",
        "hedger_ratio_replication_v2",
        "hedger_ratio_replication_v3",
        "hedger_ratio_replication_v4",
        "volatility_spread_scalping_probe_v1",
        "grid_band_rebound_v3",
        "profile_hedge_scalping_v4",
    }:
        return 3.0
    if strategy_id in {"event_context_outcome_v2", "profile_hedge_scalping_v2", "profile_hedge_scalping_v3", "event_context_profile_confirmed_v1"}:
        return 2.0
    return 1.0


def _structural_order_type(strategy_id: str) -> str:
    if strategy_id in {
        "grid_buyer_band_rebound_v1",
        "grid_buyer_band_rebound_v2",
        "hedger_ratio_replication_v1",
        "hedger_ratio_replication_v2",
        "hedger_ratio_replication_v3",
        "hedger_ratio_replication_v4",
        "volatility_spread_scalping_probe_v1",
        "event_context_outcome_v2",
        "profile_hedge_scalping_v2",
        "profile_hedge_scalping_v3",
        "profile_hedge_scalping_v4",
        "event_context_profile_confirmed_v1",
        "grid_band_rebound_v3",
    }:
        return "FAK"
    return "FOK"


def _strategy_plan(
    strategy_id: str,
    *,
    event_context: LiveMarketCandidateVerification,
    profile_candidates: list[dict[str, Any]],
    monitor_artifact: str | None,
    used_token_ids: set[str] | None = None,
    broad_profile_candidates: list[dict[str, Any]] | None = None,
) -> StrategySignalPlan:
    used_token_ids = used_token_ids or set()
    broad_profile_candidates = broad_profile_candidates or profile_candidates
    if strategy_id == "event_context_outcome_v1":
        return StrategySignalPlan(strategy_id, event_context if event_context.verified else None, event_context.blockers, {"signal_type": "event_context", "monitor_artifact": monitor_artifact})
    if strategy_id == "event_context_outcome_v2":
        return StrategySignalPlan(
            strategy_id,
            event_context if event_context.verified else None,
            event_context.blockers,
            {
                "signal_type": "event_context_v2",
                "monitor_artifact": monitor_artifact,
                "volume_adjustment": "FAK_order_type_with_wider_structural_slippage",
            },
        )
    if strategy_id == "buying_ahead_pre_event_v1":
        row = _best_profile_candidate(profile_candidates, require_buying_ahead=True, allowed_grades={"S", "S+", "S++"}, excluded_token_ids=used_token_ids)
        return _profile_strategy_plan(strategy_id, row, "buying_ahead", monitor_artifact)
    if strategy_id == "s_tier_outcome_consensus_cashout_v1":
        row = _best_profile_candidate(profile_candidates, allowed_grades={"S", "S+", "S++"}, excluded_token_ids=used_token_ids)
        return _profile_strategy_plan(strategy_id, row, "s_tier_outcome_cashout", monitor_artifact)
    if strategy_id == "s_tier_outcome_hold_to_settlement_v1":
        row = _best_profile_candidate(profile_candidates, allowed_grades={"S", "S+", "S++"}, excluded_token_ids=used_token_ids)
        return _profile_strategy_plan(strategy_id, row, "s_tier_outcome_hold_to_settlement", monitor_artifact)
    if strategy_id == "hedger_ratio_replication_v1":
        row = _best_profile_candidate(profile_candidates, allowed_grades={"S", "S+", "S++"}, signal_styles={"hedger", "grid_buyer"}, excluded_token_ids=used_token_ids)
        return _profile_strategy_plan(strategy_id, row, "hedge_proportion_minimal_probe", monitor_artifact)
    if strategy_id == "hedger_ratio_replication_v2":
        row, grade_pool = _best_profile_candidate_with_fallback(
            profile_candidates,
            primary_grades={"S", "S+", "S++"},
            fallback_grades={"A"},
            signal_styles={"hedger", "grid_buyer"},
            excluded_token_ids=used_token_ids,
        )
        return _profile_strategy_plan_with_event_context_fallback(
            strategy_id,
            row,
            "hedge_proportion_v2_volume_probe",
            monitor_artifact,
            event_context=event_context,
            excluded_token_ids=used_token_ids,
            extra_context={
                "grade_pool": grade_pool,
                "volume_adjustment": "S_tier_preferred_A_fallback_with_event_context_quote_fallback",
            },
        )
    if strategy_id == "hedger_ratio_replication_v3":
        carry_forward_candidates = _profile_candidates_matching_event_context(
            broad_profile_candidates,
            event_context=event_context,
            signal_styles={"hedger", "grid_buyer"},
        )
        row, grade_pool = _best_profile_candidate_with_fallback(
            carry_forward_candidates,
            primary_grades={"S", "S+", "S++"},
            fallback_grades={"A"},
            signal_styles={"hedger", "grid_buyer"},
            excluded_token_ids=used_token_ids,
        )
        return _profile_strategy_plan_with_event_context_fallback(
            strategy_id,
            row,
            "hedge_proportion_v3_carry_forward_probe",
            monitor_artifact,
            event_context=event_context,
            excluded_token_ids=used_token_ids,
            allow_cross_event_profile_pressure=True,
            extra_context={
                "grade_pool": grade_pool,
                "volume_adjustment": "same_symbol_same_outcome_profile_pressure_carry_forward",
            },
        )
    if strategy_id == "grid_buyer_band_rebound_v1":
        row = _best_profile_candidate(profile_candidates, allowed_grades={"S", "S+", "S++"}, signal_styles={"grid_buyer"}, excluded_token_ids=used_token_ids)
        return _profile_strategy_plan(strategy_id, row, "band_rebound_minimal_probe", monitor_artifact)
    if strategy_id == "grid_buyer_band_rebound_v2":
        carry_forward_candidates = _profile_candidates_matching_event_context(
            broad_profile_candidates,
            event_context=event_context,
            signal_styles={"grid_buyer"},
        )
        row, grade_pool = _best_profile_candidate_with_fallback(
            carry_forward_candidates,
            primary_grades={"S", "S+", "S++"},
            fallback_grades={"A"},
            signal_styles={"grid_buyer"},
            excluded_token_ids=used_token_ids,
        )
        return _profile_strategy_plan_with_event_context_fallback(
            strategy_id,
            row,
            "band_rebound_v2_carry_forward_probe",
            monitor_artifact,
            event_context=event_context,
            excluded_token_ids=used_token_ids,
            allow_cross_event_profile_pressure=True,
            extra_context={
                "grade_pool": grade_pool,
                "volume_adjustment": "same_symbol_same_outcome_grid_pressure_carry_forward",
            },
        )
    if strategy_id == "profile_hedge_scalping_v1":
        row = _best_profile_candidate(
            profile_candidates,
            allowed_grades={"S", "S+", "S++"},
            signal_styles={"hedger", "grid_buyer", "scalping_trader"},
            excluded_token_ids=used_token_ids,
        )
        return _profile_strategy_plan(strategy_id, row, "profile_hedge_scalping_minimal_probe", monitor_artifact)
    if strategy_id == "profile_hedge_scalping_v2":
        row, grade_pool = _best_profile_candidate_with_fallback(
            profile_candidates,
            primary_grades={"S", "S+", "S++"},
            fallback_grades={"A"},
            signal_styles={"hedger", "grid_buyer", "scalping_trader", "outcome_predictor"},
            excluded_token_ids=used_token_ids,
        )
        return _profile_strategy_plan_with_event_context_fallback(
            strategy_id,
            row,
            "profile_hedge_scalping_v2_volume_probe",
            monitor_artifact,
            event_context=event_context,
            excluded_token_ids=used_token_ids,
            extra_context={
                "grade_pool": grade_pool,
                "volume_adjustment": "broader_profile_style_set_with_event_context_quote_fallback",
            },
        )
    if strategy_id == "profile_hedge_scalping_v3":
        carry_forward_candidates = _profile_candidates_matching_event_context(
            broad_profile_candidates,
            event_context=event_context,
            signal_styles={"hedger", "grid_buyer", "scalping_trader", "outcome_predictor"},
        )
        row, grade_pool = _best_profile_candidate_with_fallback(
            carry_forward_candidates,
            primary_grades={"S", "S+", "S++"},
            fallback_grades={"A"},
            signal_styles={"hedger", "grid_buyer", "scalping_trader", "outcome_predictor"},
            excluded_token_ids=used_token_ids,
        )
        return _profile_strategy_plan_with_event_context_fallback(
            strategy_id,
            row,
            "profile_hedge_scalping_v3_carry_forward_probe",
            monitor_artifact,
            event_context=event_context,
            excluded_token_ids=used_token_ids,
            allow_cross_event_profile_pressure=True,
            extra_context={
                "grade_pool": grade_pool,
                "volume_adjustment": "same_symbol_same_outcome_profile_pressure_carry_forward",
            },
        )
    if strategy_id == "volatility_spread_scalping_probe_v1":
        row = _best_profile_candidate(
            profile_candidates,
            allowed_grades={"S", "S+", "S++"},
            signal_styles={"scalping_trader"},
            excluded_token_ids=used_token_ids,
        )
        plan = _profile_strategy_plan(strategy_id, row, "volatility_liquidity_minimal_probe", monitor_artifact)
        if plan.verification is not None:
            return plan
        if row is not None and event_context.verified and event_context.candidate is not None:
            signal_ages = [float(sig.get("age_seconds")) for sig in row.get("supporting_signals") or [] if _optional_float(sig.get("age_seconds")) is not None]
            return StrategySignalPlan(
                strategy_id,
                event_context,
                (),
                {
                    "signal_type": "volatility_liquidity_minimal_probe",
                    "monitor_artifact": monitor_artifact,
                    "profile_age_seconds": max(signal_ages or [20.0]),
                    "signal_age_seconds": min(signal_ages or [1.0]),
                    "source_row": _compact_profile_row(row),
                    "executable_quote_source": "event_context_fallback",
                },
            )
        return plan
    if strategy_id == "indicator_confirmed_outcome_v1":
        row = _best_profile_candidate(profile_candidates, allowed_grades={"S", "S+", "S++"}, excluded_token_ids=used_token_ids)
        plan = _profile_strategy_plan(strategy_id, row, "indicator_confirmed_outcome", monitor_artifact)
        if plan.verification is not None:
            context = dict(plan.signal_context)
            context["indicator_confirmation"] = _indicator_confirmation(row or {})
            return StrategySignalPlan(strategy_id, plan.verification, plan.blockers, context)
        return plan
    if strategy_id == "a_fallback_outcome_probe_v1":
        s_row = _best_profile_candidate(profile_candidates, allowed_grades={"S", "S+", "S++"})
        if s_row is not None:
            return StrategySignalPlan(strategy_id, None, ("s_tier_source_present",), {"signal_type": "a_fallback", "monitor_artifact": monitor_artifact})
        row = _best_profile_candidate(profile_candidates, allowed_grades={"A"}, excluded_token_ids=used_token_ids)
        return _profile_strategy_plan(strategy_id, row, "a_fallback_outcome", monitor_artifact)
    if strategy_id == "event_context_profile_confirmed_v1":
        carry_forward_candidates = _profile_candidates_matching_event_context(
            broad_profile_candidates,
            event_context=event_context,
            signal_styles={"outcome_predictor", "hedger", "grid_buyer"},
        )
        row, grade_pool = _best_profile_candidate_with_fallback(
            carry_forward_candidates,
            primary_grades={"S", "S+", "S++"},
            fallback_grades={"A"},
            signal_styles={"outcome_predictor", "hedger", "grid_buyer"},
            excluded_token_ids=used_token_ids,
        )
        return _profile_strategy_plan_with_event_context_fallback(
            strategy_id,
            row,
            "event_context_profile_confirmed",
            monitor_artifact,
            event_context=event_context,
            excluded_token_ids=used_token_ids,
            allow_cross_event_profile_pressure=True,
            extra_context={
                "grade_pool": grade_pool,
                "volume_adjustment": "event_context_quote_profile_pressure_confirmation",
            },
        )
    if strategy_id == "hedger_ratio_replication_v4":
        carry_forward_candidates = _profile_candidates_matching_event_context(
            broad_profile_candidates,
            event_context=event_context,
            signal_styles={"hedger", "grid_buyer"},
        )
        row, grade_pool = _best_profile_candidate_with_fallback(
            carry_forward_candidates,
            primary_grades={"S", "S+", "S++"},
            fallback_grades={"A"},
            signal_styles={"hedger", "grid_buyer"},
            excluded_token_ids=used_token_ids,
        )
        return _profile_strategy_plan_with_event_context_fallback(
            strategy_id,
            row,
            "hedge_proportion_v4_managed_rebalance",
            monitor_artifact,
            event_context=event_context,
            excluded_token_ids=used_token_ids,
            allow_cross_event_profile_pressure=True,
            extra_context={
                "grade_pool": grade_pool,
                "managed_runtime": "hedge_ratio_rebalance_and_stale_order_review",
                "price_path_context_required": True,
                "volume_adjustment": "same_symbol_same_outcome_profile_pressure_with_managed_rebalance",
            },
        )
    if strategy_id == "profile_hedge_scalping_v4":
        carry_forward_candidates = _profile_candidates_matching_event_context(
            broad_profile_candidates,
            event_context=event_context,
            signal_styles={"hedger", "grid_buyer", "scalping_trader", "outcome_predictor"},
        )
        row, grade_pool = _best_profile_candidate_with_fallback(
            carry_forward_candidates,
            primary_grades={"S", "S+", "S++"},
            fallback_grades={"A"},
            signal_styles={"hedger", "grid_buyer", "scalping_trader", "outcome_predictor"},
            excluded_token_ids=used_token_ids,
        )
        return _profile_strategy_plan_with_event_context_fallback(
            strategy_id,
            row,
            "profile_hedge_scalping_v4_managed_cashout_rebuy",
            monitor_artifact,
            event_context=event_context,
            excluded_token_ids=used_token_ids,
            allow_cross_event_profile_pressure=True,
            extra_context={
                "grade_pool": grade_pool,
                "managed_runtime": "cashout_rebuy_and_stale_order_review",
                "price_path_context_required": True,
                "volume_adjustment": "same_symbol_same_outcome_profile_pressure_with_managed_cashout_rebuy",
            },
        )
    if strategy_id == "grid_band_rebound_v3":
        carry_forward_candidates = _profile_candidates_matching_event_context(
            broad_profile_candidates,
            event_context=event_context,
            signal_styles={"grid_buyer"},
        )
        row, grade_pool = _best_profile_candidate_with_fallback(
            carry_forward_candidates,
            primary_grades={"S", "S+", "S++"},
            fallback_grades={"A"},
            signal_styles={"grid_buyer"},
            excluded_token_ids=used_token_ids,
        )
        return _profile_strategy_plan_with_event_context_fallback(
            strategy_id,
            row,
            "grid_band_rebound_v3_managed_cashout_rebuy",
            monitor_artifact,
            event_context=event_context,
            excluded_token_ids=used_token_ids,
            allow_cross_event_profile_pressure=True,
            extra_context={
                "grade_pool": grade_pool,
                "managed_runtime": "grid_band_cashout_rebuy_and_stale_order_review",
                "minimum_order_notional_required": True,
                "price_path_context_required": True,
                "volume_adjustment": "same_symbol_same_outcome_grid_pressure_with_managed_cashout_rebuy",
            },
        )
    return StrategySignalPlan(strategy_id, None, ("unsupported_signal_strategy",), {"monitor_artifact": monitor_artifact})


def _profile_strategy_plan(
    strategy_id: str,
    row: dict[str, Any] | None,
    signal_type: str,
    monitor_artifact: str | None,
    *,
    extra_context: dict[str, Any] | None = None,
) -> StrategySignalPlan:
    extra_context = extra_context or {}
    if row is None:
        return StrategySignalPlan(strategy_id, None, (f"no_{signal_type}_signal",), {"signal_type": signal_type, "monitor_artifact": monitor_artifact, **extra_context})
    quoted_row = _refresh_profile_candidate_quote(row)
    verification = verify_live_market_candidate(quoted_row, max_quote_age_seconds=15.0, max_spread=0.08)
    if not verification.verified:
        return StrategySignalPlan(
            strategy_id,
            None,
            verification.blockers,
            {"signal_type": signal_type, "monitor_artifact": monitor_artifact, "source_row": _compact_profile_row(row), **extra_context},
        )
    managed_blockers = _managed_profile_entry_blockers(
        quoted_row,
        verification=verification,
        signal_type=signal_type,
    )
    if managed_blockers:
        return StrategySignalPlan(
            strategy_id,
            None,
            managed_blockers,
            {
                "signal_type": signal_type,
                "monitor_artifact": monitor_artifact,
                "source_row": _compact_profile_row(quoted_row),
                **extra_context,
            },
        )
    signal_ages = [float(sig.get("age_seconds")) for sig in row.get("supporting_signals") or [] if _optional_float(sig.get("age_seconds")) is not None]
    return StrategySignalPlan(
        strategy_id,
        verification,
        (),
        {
            "signal_type": signal_type,
            "monitor_artifact": monitor_artifact,
            "profile_age_seconds": max(signal_ages or [20.0]),
            "signal_age_seconds": min(signal_ages or [1.0]),
            "source_row": _compact_profile_row(quoted_row),
            **extra_context,
        },
    )


def _profile_strategy_plan_with_event_context_fallback(
    strategy_id: str,
    row: dict[str, Any] | None,
    signal_type: str,
    monitor_artifact: str | None,
    *,
    event_context: LiveMarketCandidateVerification,
    excluded_token_ids: set[str] | None = None,
    allow_cross_event_profile_pressure: bool = False,
    extra_context: dict[str, Any] | None = None,
) -> StrategySignalPlan:
    plan = _profile_strategy_plan(strategy_id, row, signal_type, monitor_artifact, extra_context=extra_context)
    if plan.verification is not None:
        return plan
    if row is None or not event_context.verified or event_context.candidate is None:
        return plan
    excluded_token_ids = excluded_token_ids or set()
    if event_context.candidate.token_id in excluded_token_ids:
        return StrategySignalPlan(
            strategy_id,
            None,
            ("duplicate_event_token_prevented",),
            {
                "signal_type": signal_type,
                "monitor_artifact": monitor_artifact,
                "source_row": _compact_profile_row(row),
                "executable_quote_source": "event_context_fallback",
                "duplicate_token_id": event_context.candidate.token_id,
                **(extra_context or {}),
            },
        )
    row_slug = str(row.get("event_slug") or row.get("event_key") or "").strip()
    context_slug = str(event_context.candidate.event_slug or event_context.candidate.event_key or "").strip()
    if row_slug and context_slug and row_slug != context_slug and not allow_cross_event_profile_pressure:
        return plan
    row_outcome = str(row.get("outcome") or row.get("effective_outcome") or "").strip().lower()
    context_outcome = str(event_context.candidate.outcome or "").strip().lower()
    if row_outcome and context_outcome and row_outcome != context_outcome:
        return plan
    if allow_cross_event_profile_pressure and not _row_matches_event_context_symbol(row, event_context):
        return plan
    managed_blockers = _managed_profile_entry_blockers(
        row,
        verification=event_context,
        signal_type=signal_type,
    )
    if managed_blockers:
        return StrategySignalPlan(
            strategy_id,
            None,
            managed_blockers,
            {
                "signal_type": signal_type,
                "monitor_artifact": monitor_artifact,
                "source_row": _compact_profile_row(row),
                "executable_quote_source": "event_context_fallback",
                **(extra_context or {}),
            },
        )
    signal_ages = [float(sig.get("age_seconds")) for sig in row.get("supporting_signals") or [] if _optional_float(sig.get("age_seconds")) is not None]
    return StrategySignalPlan(
        strategy_id,
        event_context,
        (),
        {
            "signal_type": signal_type,
            "monitor_artifact": monitor_artifact,
            "profile_age_seconds": max(signal_ages or [20.0]),
            "signal_age_seconds": min(signal_ages or [1.0]),
            "source_row": _compact_profile_row(row),
            "executable_quote_source": "event_context_fallback",
            "cross_event_profile_pressure": bool(allow_cross_event_profile_pressure and row_slug and context_slug and row_slug != context_slug),
            **(extra_context or {}),
        },
    )


def _profile_candidates_matching_event_context(
    rows: list[dict[str, Any]],
    *,
    event_context: LiveMarketCandidateVerification,
    signal_styles: set[str],
) -> list[dict[str, Any]]:
    if not event_context.verified or event_context.candidate is None:
        return []
    context_outcome = str(event_context.candidate.outcome or "").strip().lower()
    matched: list[dict[str, Any]] = []
    for row in rows:
        row_outcome = str(row.get("outcome") or row.get("effective_outcome") or "").strip().lower()
        if context_outcome and row_outcome and context_outcome != row_outcome:
            continue
        if not _row_matches_event_context_symbol(row, event_context):
            continue
        pressure_gate = evaluate_profile_pressure_gate(row)
        if not pressure_gate.executable:
            row = dict(row)
            row["profile_pressure_gate"] = {
                "executable": False,
                "blockers": list(pressure_gate.blockers),
                "support_weight": pressure_gate.support_weight,
                "conflict_weight": pressure_gate.conflict_weight,
                "max_signal_age_seconds": pressure_gate.max_signal_age_seconds,
                "signal_count": pressure_gate.signal_count,
            }
            continue
        if not _eligible_profile_signals(row, allowed_grades={"S", "S+", "S++", "A"}, signal_styles=signal_styles):
            continue
        matched.append(row)
    return matched


def _managed_profile_entry_blockers(
    row: dict[str, Any],
    *,
    verification: LiveMarketCandidateVerification,
    signal_type: str,
) -> tuple[str, ...]:
    if not _is_managed_profile_signal(signal_type):
        return ()
    candidate = verification.candidate
    best_ask = _optional_float(getattr(candidate, "best_ask", None) if candidate is not None else row.get("best_ask"))
    support_weight = _optional_float(row.get("support_weight")) or 0.0
    conflict_weight = _optional_float(row.get("conflict_weight")) or 0.0
    conflict_ratio = conflict_weight / max(support_weight + conflict_weight, 1.0)
    remaining = (
        _optional_float(getattr(candidate, "time_remaining_seconds", None))
        if candidate is not None
        else None
    )
    if remaining is None:
        remaining = _row_time_remaining_seconds(row, now_utc=datetime.now(UTC))
    blockers: list[str] = []
    if conflict_ratio > 0.30:
        blockers.append("managed_profile_pressure_conflict_too_high")
    if best_ask is not None and best_ask >= 0.90 and conflict_ratio > 0.20:
        blockers.append("managed_profile_entry_extreme_ask_with_conflict")
    if best_ask is not None and best_ask >= 0.92 and "rebuy" in signal_type:
        blockers.append("managed_cashout_rebuy_entry_price_too_high")
    if remaining is not None and remaining < 120.0:
        blockers.append("managed_entry_time_remaining_too_low")
    return tuple(blockers)


def _is_managed_profile_signal(signal_type: str) -> bool:
    text = signal_type.lower()
    return "managed" in text or text.startswith("grid_band_rebound_v3")


def _row_matches_event_context_symbol(row: dict[str, Any], event_context: LiveMarketCandidateVerification) -> bool:
    if not event_context.candidate:
        return False
    row_symbol = str(row.get("symbol") or "").strip().upper()
    context_symbol = str(getattr(event_context.candidate, "symbol", None) or "").strip().upper()
    if row_symbol and context_symbol:
        return row_symbol == context_symbol
    row_slug = str(row.get("event_slug") or row.get("event_key") or "").strip()
    context_slug = str(event_context.candidate.event_slug or event_context.candidate.event_key or "").strip()
    return bool(row_slug and context_slug and _symbol_from_event_slug(row_slug) == _symbol_from_event_slug(context_slug))


def _symbol_from_event_slug(event_slug: str) -> str:
    return event_slug.split("-", 1)[0].strip().upper()


def _select_event_context_candidate(
    processed_events: set[str],
    config: SignalLiveRunConfig,
    *,
    preferred_event_slug: str | None = None,
) -> LiveMarketCandidateVerification:
    from app.data.nodes.polymarket.crypto.live_capture import discover_live_crypto_updown_targets

    targets, _discovery = discover_live_crypto_updown_targets(
        symbols=list(config.symbols),
        cadence_minutes=5,
        lookback_minutes=2,
        lookahead_minutes=20,
    )
    now_utc = datetime.now(UTC)
    candidates: list[tuple[tuple[float, float, float, str], LiveMarketCandidateVerification]] = []
    blockers: list[str] = []
    filtered_targets = _event_targets_to_try(targets, processed_events=processed_events, now_utc=now_utc, min_seconds_remaining=config.min_seconds_remaining)
    if preferred_event_slug:
        preferred_targets = [target for target in filtered_targets if (target.event_slug or target.market_id) == preferred_event_slug]
        if preferred_targets:
            filtered_targets = preferred_targets
    for target in filtered_targets:
        verification = verify_live_market_candidate(_live_candidate_row_from_target(target), now_utc=now_utc, max_quote_age_seconds=15.0, max_spread=0.08)
        if not verification.verified or verification.candidate is None:
            blockers.extend(verification.blockers)
            continue
        score = (
            abs(float(verification.candidate.best_ask) - 0.5),
            verification.candidate.spread,
            -verification.candidate.depth_top3_ask_size,
            verification.candidate.token_id,
        )
        candidates.append((score, verification))
    if not candidates:
        return LiveMarketCandidateVerification(False, None, tuple(sorted(set(blockers or ["no_verified_live_candidate"]))))
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _selected_event_slug(
    event_context: LiveMarketCandidateVerification,
    monitor: dict[str, Any],
    *,
    preferred_event_slug: str | None = None,
) -> str | None:
    if preferred_event_slug and event_context.candidate is not None:
        current_slug = event_context.candidate.event_slug or event_context.candidate.event_key
        if current_slug == preferred_event_slug:
            return preferred_event_slug
    if event_context.candidate is not None:
        return event_context.candidate.event_slug or event_context.candidate.event_key
    rows = monitor.get("eligible_manual_candidates") or []
    for row in rows:
        slug = str(row.get("event_slug") or "").strip()
        if slug:
            return slug
    return None


def _preferred_profile_event_slug(
    monitor: dict[str, Any],
    *,
    processed_events: set[str],
    min_seconds_remaining: float,
) -> str | None:
    rows = [row for row in monitor.get("eligible_manual_candidates") or [] if isinstance(row, dict)]
    if not rows:
        rows = [row for row in monitor.get("observed_candidates") or [] if isinstance(row, dict)]
    executable_rows = [row for row in rows if _has_executable_quote_fields(row)]
    now_utc = datetime.now(UTC)
    for row in executable_rows or rows:
        slug = str(row.get("event_slug") or "").strip()
        remaining = _row_time_remaining_seconds(row, now_utc=now_utc)
        if slug and slug not in processed_events:
            if remaining is not None and remaining < min_seconds_remaining:
                continue
            return slug
    return None


def _has_executable_quote_fields(row: dict[str, Any]) -> bool:
    return all(_optional_float(row.get(key)) is not None for key in ("best_ask", "spread", "ask_size", "depth_top3_ask_size"))


def _profile_candidates_for_event(monitor: dict[str, Any], *, event_slug: str | None) -> list[dict[str, Any]]:
    rows = [dict(row) for row in monitor.get("eligible_manual_candidates") or [] if isinstance(row, dict)]
    observed_rows = [dict(row) for row in monitor.get("observed_candidates") or [] if isinstance(row, dict)]
    if not rows:
        rows = observed_rows
    report = monitor.get("profile_signal_report") if isinstance(monitor.get("profile_signal_report"), dict) else {}
    aggregated_rows = [dict(row) for row in report.get("aggregated_candidates") or [] if isinstance(row, dict)]
    if aggregated_rows:
        quote_rows = rows + observed_rows
        side_rows = [side_row for row in aggregated_rows for side_row in _split_aggregated_profile_row_by_outcome(row)]
        merged_rows = [_merge_profile_row_with_quote(row, quote_rows) for row in side_rows]
        rows = rows + [row for row in merged_rows if row.get("supporting_signals")]
    if event_slug:
        rows = [row for row in rows if str(row.get("event_slug") or "") == event_slug]
    for row in rows:
        if not row.get("outcome") and row.get("effective_outcome"):
            row["outcome"] = row.get("effective_outcome")
        row["token_id"] = row.get("token_id") or ((row.get("manual_execution_ticket") or {}).get("token_id"))
        row["event_key"] = row.get("event_slug")
        row["event_token_key"] = f"{row.get('event_slug')}:{str(row.get('outcome') or '').lower()}"
        row["observed_at_utc"] = row.get("quote_at_utc")
    return rows


def _split_aggregated_profile_row_by_outcome(row: dict[str, Any]) -> list[dict[str, Any]]:
    row_outcome = str(row.get("outcome") or row.get("effective_outcome") or "").strip()
    if row_outcome:
        return [row]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for signal in row.get("supporting_signals") or []:
        if not isinstance(signal, dict):
            continue
        outcome = str(signal.get("effective_outcome") or signal.get("raw_outcome") or "").strip()
        if not outcome:
            continue
        grouped.setdefault(outcome, []).append(signal)
    if not grouped:
        return [row]
    side_rows: list[dict[str, Any]] = []
    for outcome, signals in grouped.items():
        side_row = dict(row)
        side_row["outcome"] = outcome
        side_row["effective_outcome"] = outcome
        side_row["supporting_signals"] = signals
        side_rows.append(side_row)
    return side_rows


def _merge_profile_row_with_quote(row: dict[str, Any], quote_rows: list[dict[str, Any]]) -> dict[str, Any]:
    merged = dict(row)
    outcome = str(merged.get("outcome") or merged.get("effective_outcome") or "").strip().lower()
    slug = str(merged.get("event_slug") or "").strip()
    quote = next(
        (
            item
            for item in quote_rows
            if str(item.get("event_slug") or "").strip() == slug
            and str(item.get("outcome") or item.get("effective_outcome") or "").strip().lower() == outcome
        ),
        None,
    )
    if not quote:
        return merged
    for key in (
        "token_id",
        "best_bid",
        "best_ask",
        "spread",
        "ask_size",
        "depth_top3_ask_size",
        "quote_at_utc",
        "monitor_quote_age_seconds",
        "manual_execution_ticket",
        "event_threshold_price",
        "underlying_context",
        "underlying_price",
        "underlying_trend_15m",
        "underlying_trend_30m",
        "underlying_trend_1h",
        "window_start_time",
        "window_end_time",
        "time_remaining_seconds",
        "symbol",
    ):
        if merged.get(key) is None and quote.get(key) is not None:
            merged[key] = quote.get(key)
    if not merged.get("outcome") and quote.get("outcome"):
        merged["outcome"] = quote.get("outcome")
    return merged


def _row_time_remaining_seconds(row: dict[str, Any], *, now_utc: datetime) -> float | None:
    event_end = _parse_row_datetime(row.get("event_end_time_utc") or row.get("window_end_time") or row.get("end_time_utc"))
    if event_end is not None:
        return max(0.0, (event_end - now_utc).total_seconds())
    remaining = _optional_float(row.get("time_remaining_seconds"))
    quote_age = _optional_float(row.get("monitor_quote_age_seconds"))
    if remaining is None:
        return None
    if quote_age is not None:
        return max(0.0, remaining - quote_age)
    return remaining


def _parse_row_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _refresh_profile_candidate_quote(row: dict[str, Any]) -> dict[str, Any]:
    token_id = str(row.get("token_id") or ((row.get("manual_execution_ticket") or {}).get("token_id")) or "").strip()
    if not token_id:
        return row
    observed_at = datetime.now(UTC)
    try:
        payload = fetch_current_order_book(token_id)
        frame = normalize_order_book_snapshot(
            payload,
            event_id=str(row.get("event_id") or row.get("event_slug") or ""),
            market_id=str(row.get("market_id") or ""),
            outcome=str(row.get("outcome") or ""),
            symbol=str(row.get("symbol") or ""),
        )
    except Exception as exc:  # noqa: BLE001
        refreshed = dict(row)
        refreshed["quote_error"] = f"{type(exc).__name__}:{exc}"
        return refreshed
    refreshed = dict(row)
    refreshed["token_id"] = token_id
    refreshed["observed_at_utc"] = observed_at.isoformat()
    refreshed["source"] = "polymarket_current_order_book_profile_signal_refresh"
    if frame.empty:
        refreshed["quote_error"] = "empty_order_book_snapshot"
        return refreshed
    quote = frame.iloc[0].to_dict()
    for key in ("best_bid", "best_ask", "spread", "ask_size", "depth_top3_ask_size"):
        refreshed[key] = quote.get(key)
    return refreshed


def _best_profile_candidate(
    rows: list[dict[str, Any]],
    *,
    allowed_grades: set[str],
    require_buying_ahead: bool = False,
    signal_styles: set[str] | None = None,
    excluded_token_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    excluded_token_ids = excluded_token_ids or set()
    scored: list[tuple[tuple[float, float, float, float], dict[str, Any]]] = []
    fallback_scored: list[tuple[tuple[float, float, float, float], dict[str, Any]]] = []
    for row in rows:
        signals = _eligible_profile_signals(row, allowed_grades=allowed_grades, signal_styles=signal_styles)
        if require_buying_ahead and not _looks_buying_ahead(row, signals):
            continue
        if not signals:
            continue
        score = (
            float(len(signals)),
            max((_optional_float(sig.get("profile_score")) or 0.0 for sig in signals), default=0.0),
            float(row.get("support_weight") or 0.0),
            -float(row.get("monitor_quote_age_seconds") or 0.0),
        )
        token_id = str(row.get("token_id") or ((row.get("manual_execution_ticket") or {}).get("token_id")) or "").strip()
        if token_id and token_id in excluded_token_ids:
            fallback_scored.append((score, row))
        else:
            scored.append((score, row))
    if not scored:
        scored = fallback_scored
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _best_profile_candidate_with_fallback(
    rows: list[dict[str, Any]],
    *,
    primary_grades: set[str],
    fallback_grades: set[str],
    require_buying_ahead: bool = False,
    signal_styles: set[str] | None = None,
    excluded_token_ids: set[str] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    row = _best_profile_candidate(
        rows,
        allowed_grades=primary_grades,
        require_buying_ahead=require_buying_ahead,
        signal_styles=signal_styles,
        excluded_token_ids=excluded_token_ids,
    )
    if row is not None:
        return row, "S_or_better"
    row = _best_profile_candidate(
        rows,
        allowed_grades=fallback_grades,
        require_buying_ahead=require_buying_ahead,
        signal_styles=signal_styles,
        excluded_token_ids=excluded_token_ids,
    )
    if row is not None:
        return row, "A_fallback"
    return None, "none"


def _eligible_profile_signals(
    row: dict[str, Any],
    *,
    allowed_grades: set[str],
    signal_styles: set[str] | None = None,
) -> list[dict[str, Any]]:
    signal_styles = signal_styles or {"outcome_predictor"}
    outcome = str(row.get("outcome") or "").strip().lower()
    signals = []
    for signal in row.get("supporting_signals") or []:
        if not isinstance(signal, dict):
            continue
        if str(signal.get("profile_grade") or "").strip() not in allowed_grades:
            continue
        if str(signal.get("action_side") or "").upper() != "BUY":
            continue
        style = str(signal.get("profile_trading_style_detail") or signal.get("profile_trading_style") or "").strip()
        if style not in signal_styles:
            continue
        if outcome and str(signal.get("effective_outcome") or signal.get("raw_outcome") or "").strip().lower() != outcome:
            continue
        signals.append(signal)
    return signals


def _eligible_outcome_signals(row: dict[str, Any], *, allowed_grades: set[str]) -> list[dict[str, Any]]:
    return _eligible_profile_signals(row, allowed_grades=allowed_grades, signal_styles={"outcome_predictor"})


def _looks_buying_ahead(row: dict[str, Any], signals: list[dict[str, Any]]) -> bool:
    if bool(row.get("buying_ahead")):
        return True
    for signal in signals:
        raw = signal.get("raw_signal") if isinstance(signal.get("raw_signal"), dict) else {}
        if bool(raw.get("buying_ahead")) or bool(signal.get("buying_ahead")):
            return True
    return False


def _indicator_confirmation(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": bool(row.get("underlying_context") or row.get("event_threshold_price") is not None),
        "event_threshold_price": row.get("event_threshold_price"),
        "underlying_context_present": bool(row.get("underlying_context")),
    }


def _blocked_strategy_row(item: StrategySignalPlan, *, event_slug: str | None) -> dict[str, Any]:
    return {
        "strategy_id": item.strategy_id,
        "status": "blocked",
        "blockers": list(item.blockers or ("signal_candidate_not_ready",)),
        "event_key": event_slug,
        "event_slug": event_slug,
        "side": "BUY",
        "live_submission_attempted": False,
        "lifecycle_covered": False,
        "signal_context": item.signal_context,
    }


def _lane_stopped_strategy_row(strategy_id: str, *, event_slug: str | None, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "status": "blocked",
        "blockers": ["lane_stop_gate_triggered"],
        "event_key": event_slug,
        "event_slug": event_slug,
        "side": "BUY",
        "live_submission_attempted": False,
        "lifecycle_covered": False,
        "signal_context": {"lane_stop_details": details},
    }


def _strategy_budget_blocked_row(
    strategy_id: str,
    *,
    event_slug: str | None,
    spent_by_strategy: dict[str, float],
    config: SignalLiveRunConfig,
) -> dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "status": "blocked",
        "blockers": ["strategy_budget_cap_reached"],
        "event_key": event_slug,
        "event_slug": event_slug,
        "side": "BUY",
        "live_submission_attempted": False,
        "lifecycle_covered": False,
        "signal_context": {
            "spent_usd": round(spent_by_strategy.get(strategy_id, 0.0), 6),
            "per_strategy_budget_cap_usd": config.per_strategy_budget_cap_usd,
        },
    }


def _run_profile_monitor(config: SignalLiveRunConfig) -> str | None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(config.artifact_root) / "signal-live" / f"profile-monitor-{stamp}"
    cmd = [
        sys.executable,
        "crypto_options_app/scripts/run_crypto_options_profile_signal_monitor.py",
        "--output-dir",
        str(output_dir),
        "--max-workers",
        str(config.max_workers),
        "--page-limit",
        str(config.page_limit),
        "--active-profile-pool",
        str(config.active_profile_pool),
        "--active-profile-pool-limit",
        str(config.active_profile_pool_limit),
        "--profile-report-cache",
        str(config.profile_report_cache),
        "--include-underlying-context",
        "--compact-live-artifact",
        "--min-time-remaining-seconds",
        str(max(20.0, config.min_seconds_remaining + 90.0)),
        "--max-time-remaining-seconds",
        "900",
    ]
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("artifact_monitor="):
            return line.split("=", 1)[1].strip() or None
    candidates = sorted(output_dir.glob("crypto_options_profile_signal_monitor_*.json"))
    return str(candidates[-1]) if candidates else None


def _build_order_audit_payload_with_retry(strategy_rows: list[dict[str, Any]], *, exchange_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if exchange_trades is not None:
        audit = audit_validation_orders_against_exchange(validation_rows=strategy_rows, exchange_trades=exchange_trades)
        return {
            "schema_version": "crypto_options_core_flow_order_audit_wrapper_v1",
            "generated_at_utc": _utc_now(),
            "status": audit.status,
            "audit": audit.to_dict(),
            "audit_attempt_count": 1,
        }
    payload: dict[str, Any] = {}
    for attempt in range(1, 7):
        payload = _build_order_audit_payload(strategy_rows)
        audit = payload.get("audit") if isinstance(payload.get("audit"), dict) else {}
        blockers = set(audit.get("blockers") or [])
        if not blockers.intersection({"recorded_orders_without_exchange_match", "exchange_buy_trades_without_recorded_match"}):
            payload["audit_attempt_count"] = attempt
            return payload
        if attempt < 6:
            time.sleep(5.0)
    payload["audit_attempt_count"] = 6
    return payload


def _plan_blockers(plan: EventSignalPlan) -> list[str]:
    blockers = list(plan.blockers)
    for item in plan.plans:
        blockers.extend(f"{item.strategy_id}:{blocker}" for blocker in item.blockers)
    return sorted(set(blockers or ["no_executable_signal_candidate"]))


def _event_result_dict(result: SignalLiveEventResult) -> dict[str, Any]:
    return {
        "event_index": result.event_index,
        "event_slug": result.event_slug,
        "status": result.status,
        "generated_at_utc": result.generated_at_utc,
        "estimated_spent_usd": result.estimated_spent_usd,
        "runtime_blockers": list(result.runtime_blockers),
        "artifact_json": result.artifact_json,
    }


def _compact_profile_row(row: dict[str, Any]) -> dict[str, Any]:
    signals = row.get("supporting_signals") or []
    return {
        "event_slug": row.get("event_slug"),
        "outcome": row.get("outcome"),
        "token_id": row.get("token_id") or ((row.get("manual_execution_ticket") or {}).get("token_id")),
        "best_ask": row.get("best_ask"),
        "best_bid": row.get("best_bid"),
        "spread": row.get("spread"),
        "support_weight": row.get("support_weight"),
        "conflict_weight": row.get("conflict_weight"),
        "eligible_outcome_signal_count": len(_eligible_outcome_signals(row, allowed_grades={"S", "S+", "S++", "A"})),
        "sample_profiles": [str(sig.get("profile_name")) for sig in signals[:8] if isinstance(sig, dict)],
    }


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
