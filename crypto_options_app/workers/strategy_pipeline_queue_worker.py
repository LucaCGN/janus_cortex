from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_options_app.config import CENTRAL_DB_PATH
from crypto_options_app.db.connection import connect
from crypto_options_app.db.schema import initialize_schema
from crypto_options_app.strategies.pipeline_queue import (
    LEGACY_PIPELINE_PHASE_SUPERVISED_LIVE_CANDIDATE,
    PIPELINE_PHASE_HISTORICAL_BACKTEST,
    PIPELINE_PHASE_LIVE_CANDIDATE,
    PIPELINE_PHASE_RECENT_SHADOW_SAMPLE,
    PIPELINE_PHASE_SHADOW_REPLAY,
    PIPELINE_PHASE_SUPERVISED_LIVE_CANDIDATE,
    claim_strategy_pipeline_work,
    complete_strategy_pipeline_work,
    enqueue_strategy_pipeline_work,
    pipeline_queue_summary,
    promotion_state_to_pipeline_phase,
)
from crypto_options_app.strategies.promotion import (
    active_live_order_summary,
    active_live_position_summary,
    evaluate_and_persist_strategy_promotions,
    promotion_state_summary,
)
from crypto_options_app.workers.strategy_pipeline_scheduler import (
    StrategyPipelineSchedulerConfig,
    _postgres_resource_guard,
    _run_backtest_action,
    _live_candidate_action,
    _run_live_replay_action,
    _selector_for_strategy,
    _strategy_priority,
)


@dataclass(frozen=True)
class StrategyPipelineQueueWorkerConfig:
    db_path: Path = CENTRAL_DB_PATH
    run_id: str | None = None
    owner: str = "strategy-pipeline-queue-worker"
    max_items: int = 3
    lease_seconds: int = 1800
    process_lock_stale_seconds: int = 2700
    backfill_from_promotions: bool = True
    refresh_promotions: bool = True
    max_scenarios_per_action: int = 1
    max_trades_per_strategy: int = 1
    validation_budget_cap_usd: float = 50.0
    forward_mark_horizon_seconds: float = 60.0
    recent_shadow_retry_seconds: int = 300
    max_parallel_live_candidates: int = 3
    max_live_strategy_slots: int = 3
    dry_run: bool = False
    max_postgres_cpu_percent: float = 350.0
    max_postgres_memory_percent: float = 80.0
    report_dir: Path = Path("crypto_options_app/artifacts/team_coordination/automation_status")


def run_strategy_pipeline_queue_worker(config: StrategyPipelineQueueWorkerConfig | None = None) -> dict[str, Any]:
    """Drain durable strategy pipeline work that was enqueued by registration or promotion state."""

    config = config or StrategyPipelineQueueWorkerConfig()
    db_path = initialize_schema(config.db_path)
    generated_at = datetime.now(UTC)
    run_id = config.run_id or f"strategy-pipeline-queue-worker-{generated_at.strftime('%Y%m%dT%H%M%SZ')}"
    process_lock = _acquire_worker_process_lock(config, run_id=run_id, generated_at=generated_at)
    if not process_lock["acquired"]:
        payload = _process_lock_skip_payload(config, run_id=run_id, generated_at=generated_at, process_lock=process_lock)
        _write_queue_worker_report(payload, config.report_dir)
        return payload

    before_promotions = _load_promotion_summary(db_path, refresh=config.refresh_promotions)
    backfilled_count = 0
    with connect(db_path) as conn:
        if config.backfill_from_promotions:
            backfilled_count = _backfill_queue_from_promotion_summary(conn, before_promotions)
        before_queue = pipeline_queue_summary(conn, limit=100)

    resource_guard = _postgres_resource_guard(
        StrategyPipelineSchedulerConfig(
            db_path=db_path,
            max_postgres_cpu_percent=float(config.max_postgres_cpu_percent),
            max_postgres_memory_percent=float(config.max_postgres_memory_percent),
        )
    )
    actions: list[dict[str, Any]] = []
    claimed: list[dict[str, Any]] = []
    if resource_guard["blockers"] and not config.dry_run:
        actions.append(
            {
                "phase": "resource_guard",
                "strategy_ids": [],
                "status": "skipped",
                "blockers": resource_guard["blockers"],
                "observed": resource_guard["observed"],
            }
        )
    else:
        with connect(db_path) as conn:
            claimed = claim_strategy_pipeline_work(
                conn,
                owner=config.owner,
                limit=max(1, int(config.max_items)),
                lease_seconds=max(60, int(config.lease_seconds)),
            )
        if config.dry_run:
            with connect(db_path) as conn:
                for item in claimed:
                    complete_strategy_pipeline_work(
                        conn,
                        str(item["queue_key"]),
                        queue_status="queued",
                        result={"status": "dry_run_planned", "phase": item.get("phase")},
                        run_id=run_id,
                    )
                    actions.append(_planned_item_payload(item))
        else:
            actions.extend(_execute_claimed_items(claimed, config=config, run_id=run_id, db_path=db_path))

    post_live_reconciliation = _post_live_order_reconciliation_if_needed(
        db_path,
        actions=actions,
        default_max_loss_usd=float(config.validation_budget_cap_usd),
        dry_run=bool(config.dry_run),
    )
    after_promotions = _load_promotion_summary(db_path, refresh=not config.dry_run)
    with connect(db_path) as conn:
        after_queue = pipeline_queue_summary(conn, limit=100)
    payload = {
        "schema_version": "crypto_options_strategy_pipeline_queue_worker_v1",
        "run_id": run_id,
        "generated_at_utc": generated_at.isoformat(),
        "dry_run": bool(config.dry_run),
        "backfill_from_promotions": bool(config.backfill_from_promotions),
        "backfilled_count": backfilled_count,
        "claimed_count": len(claimed),
        "executed_action_count": sum(1 for action in actions if action.get("status") == "executed"),
        "before_promotion_counts": before_promotions.get("by_promotion_state", {}),
        "after_promotion_counts": after_promotions.get("by_promotion_state", {}),
        "before_queue_counts": {
            "by_status": before_queue.get("by_status", {}),
            "by_phase": before_queue.get("by_phase", {}),
            "active_by_status": before_queue.get("active_by_status", {}),
            "active_by_phase": before_queue.get("active_by_phase", {}),
        },
        "after_queue_counts": {
            "by_status": after_queue.get("by_status", {}),
            "by_phase": after_queue.get("by_phase", {}),
            "active_by_status": after_queue.get("active_by_status", {}),
            "active_by_phase": after_queue.get("active_by_phase", {}),
        },
        "actions": actions,
        "post_live_reconciliation": post_live_reconciliation,
        "resource_guard": resource_guard,
        "process_lock": process_lock,
        "live_submission_attempted": any(bool(action.get("live_submission_attempted")) for action in actions),
        "manual_orders_avoided": True,
        "next_action": _next_queue_action(after_queue, after_promotions),
    }
    _write_queue_worker_report(payload, config.report_dir)
    _release_worker_process_lock(process_lock)
    return payload


def _post_live_order_reconciliation_if_needed(
    db_path: Path,
    *,
    actions: list[dict[str, Any]],
    default_max_loss_usd: float,
    dry_run: bool,
) -> dict[str, Any]:
    if dry_run:
        return {"status": "skipped", "reason": "dry_run"}
    live_submission_attempted = any(bool(action.get("live_submission_attempted")) for action in actions)
    with connect(db_path) as conn:
        active_orders = active_live_order_summary(conn)
    active_live_order_count = int(active_orders.get("active_live_order_count") or 0)
    if not live_submission_attempted and active_live_order_count <= 0:
        return {"status": "skipped", "reason": "no_live_submission_or_active_order"}
    try:
        from crypto_options_app.trading.account_reconciliation import (
            cancel_underpriced_paired_exit_orders,
            reconcile_pending_exchange_orders,
        )
        from crypto_options_app.trading.polymarket_portfolio import (
            PolymarketCredentials,
            cancel_order,
            view_orders,
            view_trades,
        )

        creds = PolymarketCredentials.from_env()
        open_orders = view_orders(creds, open_only=True)
        trades = view_trades(creds)
        with connect(db_path) as conn:
            paired_exit_safety = cancel_underpriced_paired_exit_orders(
                conn,
                open_orders=open_orders,
                cancel_order_fn=lambda order_id: cancel_order(creds, order_id),
            )
            if int(paired_exit_safety.get("cancelled_order_count") or 0) > 0:
                open_orders = view_orders(creds, open_only=True)
            result = reconcile_pending_exchange_orders(
                conn,
                open_orders=open_orders,
                trades=trades,
                default_max_loss_usd=float(default_max_loss_usd),
            )
        return {
            "status": "completed",
            "open_order_count": len(open_orders),
            "trade_count": len(trades),
            "paired_exit_safety": paired_exit_safety,
            "result": result,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "blocked",
            "blockers": [f"post_live_order_reconciliation_error:{type(exc).__name__}:{exc}"],
        }


def _execute_claimed_items(
    claimed: list[dict[str, Any]],
    *,
    config: StrategyPipelineQueueWorkerConfig,
    run_id: str,
    db_path: Path,
) -> list[dict[str, Any]]:
    if not claimed:
        return []
    actions: list[dict[str, Any]] = []
    live_items = [item for item in claimed if str(item.get("phase") or "") == PIPELINE_PHASE_LIVE_CANDIDATE]
    other_items = [item for item in claimed if item not in live_items]
    if live_items:
        live_slot_status = _live_strategy_slot_status(db_path, config=config)
        open_live_slots = int(live_slot_status["open_live_slots"])
        if open_live_slots <= 0:
            for item in live_items:
                actions.append(
                    _requeue_live_item_for_slot_capacity(
                        item,
                        config=config,
                        run_id=run_id,
                        db_path=db_path,
                        live_slot_status=live_slot_status,
                    )
                )
            live_items = []
        elif len(live_items) > open_live_slots:
            overflow = live_items[open_live_slots:]
            live_items = live_items[:open_live_slots]
            for item in overflow:
                actions.append(
                    _requeue_live_item_for_slot_capacity(
                        item,
                        config=config,
                        run_id=run_id,
                        db_path=db_path,
                        live_slot_status=live_slot_status,
                    )
                )
    parallel_live = max(1, min(int(config.max_parallel_live_candidates), max(1, int(config.max_live_strategy_slots))))
    if live_items and parallel_live > 1:
        with ThreadPoolExecutor(max_workers=min(parallel_live, len(live_items))) as executor:
            futures = {
                executor.submit(_execute_queue_item_with_resource_guard, item, config=config, run_id=run_id, db_path=db_path): item
                for item in live_items
            }
            for future in as_completed(futures):
                actions.append(future.result())
    else:
        for item in live_items:
            actions.append(_execute_queue_item_with_resource_guard(item, config=config, run_id=run_id, db_path=db_path))
    for item in other_items:
        actions.append(_execute_queue_item_with_resource_guard(item, config=config, run_id=run_id, db_path=db_path))
    return actions


def _live_strategy_slot_status(db_path: Path, *, config: StrategyPipelineQueueWorkerConfig) -> dict[str, Any]:
    max_slots = max(0, int(config.max_live_strategy_slots))
    with connect(db_path) as conn:
        position_summary = active_live_position_summary(conn)
        order_summary = active_live_order_summary(conn)
    active_strategies = sorted(
        {
            str(strategy_id)
            for strategy_id in (
                list(position_summary.get("active_live_strategies") or [])
                + list(order_summary.get("active_live_order_strategies") or [])
            )
            if str(strategy_id).strip()
        }
    )
    active_strategy_count = len(active_strategies)
    return {
        "max_live_strategy_slots": max_slots,
        "active_live_strategy_count": active_strategy_count,
        "active_live_strategies": active_strategies,
        "active_live_position_count": int(position_summary.get("active_live_position_count") or 0),
        "active_live_order_count": int(order_summary.get("active_live_order_count") or 0),
        "open_live_slots": max(0, max_slots - active_strategy_count),
    }


def _requeue_live_item_for_slot_capacity(
    item: dict[str, Any],
    *,
    config: StrategyPipelineQueueWorkerConfig,
    run_id: str,
    db_path: Path,
    live_slot_status: dict[str, Any],
) -> dict[str, Any]:
    with connect(db_path) as conn:
        shadow_queued = enqueue_strategy_pipeline_work(
            conn,
            strategy_id=str(item.get("strategy_id") or ""),
            strategy_version=str(item.get("strategy_version") or ""),
            phase=PIPELINE_PHASE_RECENT_SHADOW_SAMPLE,
            priority=int(item.get("priority") or 100),
            reason="live_slot_capacity_shadow_continuation",
            requeue_terminal_done=True,
            requeue_terminal_blocked=False,
        )
        complete_strategy_pipeline_work(
            conn,
            str(item["queue_key"]),
            queue_status="queued",
            result={
                "status": "skipped",
                "phase": item.get("phase"),
                "blockers": ["live_strategy_slot_capacity_reached"],
                "live_slot_status": live_slot_status,
                "shadow_phase_queued": shadow_queued,
            },
            run_id=run_id,
            next_run_after_seconds=max(60, int(config.recent_shadow_retry_seconds)),
        )
    return {
        "phase": item.get("phase"),
        "strategy_ids": [item.get("strategy_id")],
        "queue_key": item.get("queue_key"),
        "status": "skipped",
        "blockers": ["live_strategy_slot_capacity_reached"],
        "live_slot_status": live_slot_status,
        "shadow_phase_queued": shadow_queued,
        "manual_orders_avoided": True,
        "live_submission_attempted": False,
    }


def _execute_queue_item_with_resource_guard(
    item: dict[str, Any],
    *,
    config: StrategyPipelineQueueWorkerConfig,
    run_id: str,
    db_path: Path,
) -> dict[str, Any]:
    item_resource_guard = _postgres_resource_guard(
        StrategyPipelineSchedulerConfig(
            db_path=db_path,
            max_postgres_cpu_percent=float(config.max_postgres_cpu_percent),
            max_postgres_memory_percent=float(config.max_postgres_memory_percent),
        )
    )
    if item_resource_guard["blockers"]:
        with connect(db_path) as conn:
            complete_strategy_pipeline_work(
                conn,
                str(item["queue_key"]),
                queue_status="queued",
                result={
                    "status": "skipped",
                    "phase": item.get("phase"),
                    "blockers": item_resource_guard["blockers"],
                    "observed": item_resource_guard["observed"],
                },
                run_id=run_id,
                next_run_after_seconds=60,
            )
        return {
            "phase": item.get("phase"),
            "strategy_ids": [item.get("strategy_id")],
            "queue_key": item.get("queue_key"),
            "status": "skipped",
            "blockers": item_resource_guard["blockers"],
            "observed": item_resource_guard["observed"],
            "manual_orders_avoided": True,
            "live_submission_attempted": False,
        }
    return _execute_queue_item(item, config=config, run_id=run_id, db_path=db_path)


def _acquire_worker_process_lock(
    config: StrategyPipelineQueueWorkerConfig,
    *,
    run_id: str,
    generated_at: datetime,
) -> dict[str, Any]:
    lock_path = config.report_dir / "strategy_pipeline_queue_worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stale_seconds = max(300, int(config.process_lock_stale_seconds))
    owner = {
        "run_id": run_id,
        "owner": config.owner,
        "pid": os.getpid(),
        "created_at_utc": generated_at.isoformat(),
        "stale_after_seconds": stale_seconds,
    }
    for _ in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing_owner = _read_worker_process_lock(lock_path)
            age_seconds = _worker_process_lock_age_seconds(existing_owner, generated_at)
            if age_seconds is not None and age_seconds > stale_seconds:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                else:
                    continue
            return {
                "acquired": False,
                "path": str(lock_path),
                "owner": owner,
                "existing_owner": existing_owner,
                "age_seconds": age_seconds,
            }
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(owner, handle, sort_keys=True)
        return {"acquired": True, "path": str(lock_path), "owner": owner}
    return {"acquired": False, "path": str(lock_path), "owner": owner, "existing_owner": None, "age_seconds": None}


def _release_worker_process_lock(process_lock: dict[str, Any]) -> None:
    if not process_lock.get("acquired"):
        return
    path = Path(str(process_lock.get("path") or ""))
    if not path:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _read_worker_process_lock(lock_path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _worker_process_lock_age_seconds(existing_owner: dict[str, Any] | None, now: datetime) -> float | None:
    if not existing_owner:
        return None
    created = existing_owner.get("created_at_utc")
    if not created:
        return None
    try:
        created_at = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    except ValueError:
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return (now - created_at.astimezone(UTC)).total_seconds()


def _process_lock_skip_payload(
    config: StrategyPipelineQueueWorkerConfig,
    *,
    run_id: str,
    generated_at: datetime,
    process_lock: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "crypto_options_strategy_pipeline_queue_worker_v1",
        "run_id": run_id,
        "generated_at_utc": generated_at.isoformat(),
        "dry_run": bool(config.dry_run),
        "backfill_from_promotions": bool(config.backfill_from_promotions),
        "backfilled_count": 0,
        "claimed_count": 0,
        "executed_action_count": 0,
        "before_promotion_counts": {},
        "after_promotion_counts": {},
        "before_queue_counts": {"by_status": {}, "by_phase": {}},
        "after_queue_counts": {"by_status": {}, "by_phase": {}},
        "actions": [
            {
                "phase": "process_lock",
                "strategy_ids": [],
                "status": "skipped",
                "blockers": ["strategy_pipeline_queue_worker_already_running"],
                "existing_owner": process_lock.get("existing_owner"),
            }
        ],
        "resource_guard": {"status": "not_checked", "blockers": [], "observed": {}},
        "process_lock": process_lock,
        "live_submission_attempted": False,
        "manual_orders_avoided": True,
        "next_action": "wait for existing strategy pipeline queue worker to finish",
    }


def _load_promotion_summary(db_path: Path, *, refresh: bool) -> dict[str, Any]:
    with connect(db_path) as conn:
        if refresh:
            return evaluate_and_persist_strategy_promotions(conn)
        return promotion_state_summary(conn)


def _backfill_queue_from_promotion_summary(conn: Any, summary: dict[str, Any]) -> int:
    created = 0
    for row in summary.get("strategies") or []:
        strategy_id = str(row.get("strategy_id") or "").strip()
        strategy_version = str(row.get("strategy_version") or "").strip()
        promotion_state = str(row.get("promotion_state") or "").strip().upper()
        phase = promotion_state_to_pipeline_phase(promotion_state)
        if not strategy_id or not strategy_version or phase is None:
            continue
        active_live = {}
        if phase == PIPELINE_PHASE_LIVE_CANDIDATE:
            active_live = active_live_position_summary(conn, strategy_id=strategy_id)
            active_orders = active_live_order_summary(conn, strategy_id=strategy_id)
            if (
                int(active_live.get("active_live_position_count") or 0) > 0
                or int(active_orders.get("active_live_order_count") or 0) > 0
            ):
                continue
        priority, _ = _strategy_priority(row)
        if enqueue_strategy_pipeline_work(
            conn,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            phase=phase,
            priority=priority,
            reason=f"promotion_state_backfill:{promotion_state}",
            requeue_terminal_done=True,
            requeue_terminal_blocked=phase == PIPELINE_PHASE_LIVE_CANDIDATE,
        ):
            created += 1
    return created


def _execute_queue_item(
    item: dict[str, Any],
    *,
    config: StrategyPipelineQueueWorkerConfig,
    run_id: str,
    db_path: Path,
) -> dict[str, Any]:
    phase = str(item.get("phase") or "")
    strategy_id = str(item.get("strategy_id") or "")
    planned = {
        "phase": phase,
        "promotion_state": "queued",
        "strategy_ids": [strategy_id],
        "scenario_selector": _selector_for_strategy(strategy_id, phase=phase),
        "reason": item.get("reason") or "strategy_pipeline_queue",
        "queue_key": item.get("queue_key"),
        "attempt_count": item.get("attempt_count"),
    }
    scheduler_config = StrategyPipelineSchedulerConfig(
        db_path=db_path,
        max_scenarios_per_action=max(1, int(config.max_scenarios_per_action)),
        max_trades_per_strategy=max(1, int(config.max_trades_per_strategy)),
        validation_budget_cap_usd=float(config.validation_budget_cap_usd),
        forward_mark_horizon_seconds=float(config.forward_mark_horizon_seconds),
        max_postgres_cpu_percent=float(config.max_postgres_cpu_percent),
        max_postgres_memory_percent=float(config.max_postgres_memory_percent),
        max_live_strategy_slots=max(1, int(config.max_live_strategy_slots)),
        report_dir=config.report_dir,
    )
    try:
        if phase == PIPELINE_PHASE_HISTORICAL_BACKTEST:
            action = _run_backtest_action(planned, config=scheduler_config, run_id=run_id)
        elif phase in {PIPELINE_PHASE_SHADOW_REPLAY, PIPELINE_PHASE_RECENT_SHADOW_SAMPLE}:
            action = _run_live_replay_action(planned, config=scheduler_config, run_id=run_id)
        elif phase in {
            PIPELINE_PHASE_LIVE_CANDIDATE,
            PIPELINE_PHASE_SUPERVISED_LIVE_CANDIDATE,
            LEGACY_PIPELINE_PHASE_SUPERVISED_LIVE_CANDIDATE,
        }:
            action = _live_candidate_action(planned, config=scheduler_config)
        else:
            action = planned | {"status": "blocked", "blockers": [f"unknown_pipeline_phase:{phase}"]}
    except Exception as exc:
        action = planned | {"status": "blocked", "blockers": [f"pipeline_action_error:{type(exc).__name__}:{exc}"]}

    after = _load_promotion_summary(db_path, refresh=True)
    state_row = _promotion_row_for_strategy(after, strategy_id)
    next_phase = promotion_state_to_pipeline_phase((state_row or {}).get("promotion_state"))
    queue_status, next_run_after_seconds = _queue_status_after_action(
        phase=phase,
        action=action,
        next_phase=next_phase,
        recent_shadow_retry_seconds=config.recent_shadow_retry_seconds,
    )
    with connect(db_path) as conn:
        if next_phase is not None and next_phase != phase and queue_status == "done":
            enqueue_strategy_pipeline_work(
                conn,
                strategy_id=strategy_id,
                strategy_version=str(item.get("strategy_version") or ""),
                phase=next_phase,
                priority=int(item.get("priority") or 100),
                reason=f"promotion_state_advanced:{(state_row or {}).get('promotion_state')}",
            )
        complete_strategy_pipeline_work(
            conn,
            str(item["queue_key"]),
            queue_status=queue_status,
            result=action | {"next_phase": next_phase, "promotion_row": state_row},
            run_id=str(action.get("run_id") or run_id),
            next_run_after_seconds=next_run_after_seconds,
        )
    return action | {
        "queue_key": item.get("queue_key"),
        "queue_status_after_action": queue_status,
        "next_phase": next_phase,
        "promotion_state_after_action": (state_row or {}).get("promotion_state"),
    }


def _promotion_row_for_strategy(summary: dict[str, Any], strategy_id: str) -> dict[str, Any] | None:
    for row in summary.get("strategies") or []:
        if str(row.get("strategy_id") or "") == strategy_id:
            return dict(row)
    return None


def _queue_status_after_action(
    *,
    phase: str,
    action: dict[str, Any],
    next_phase: str | None,
    recent_shadow_retry_seconds: int,
) -> tuple[str, int | None]:
    if action.get("status") != "executed":
        if phase == PIPELINE_PHASE_LIVE_CANDIDATE and _live_candidate_block_is_retryable_market_context(action):
            return "queued", max(300, int(recent_shadow_retry_seconds))
        return "blocked", None
    passed_count = int(action.get("passed_count") or 0)
    blocked_count = int(action.get("blocked_count") or 0)
    if phase == PIPELINE_PHASE_RECENT_SHADOW_SAMPLE and next_phase == PIPELINE_PHASE_RECENT_SHADOW_SAMPLE:
        if passed_count > 0:
            return "queued", max(60, int(recent_shadow_retry_seconds))
        return "blocked", None
    if passed_count <= 0 and blocked_count > 0:
        return "blocked", None
    return "done", None


def _live_candidate_block_is_retryable_market_context(action: dict[str, Any]) -> bool:
    if bool(action.get("live_submission_attempted")):
        return False
    blockers = {str(blocker) for blocker in (action.get("blockers") or [])}
    if not blockers:
        return False
    hard_blockers = {
        "initial_live_validation_strategy_style_excluded",
        "live_candidate_launcher_error",
        "submit_error",
        "submit_error_no_fill",
        "submit_error_fill_ambiguous",
        "paired_exit_order_not_submitted",
        "paired_exit_reconciliation_mismatch",
        "reconciliation_mismatch",
        "credentials_access_failure",
        "executor_boundary_not_ready",
        "live_submission_not_allowed",
        "env_live_flags_missing",
    }
    if any(blocker in hard_blockers or blocker.startswith("live_candidate_launcher_error:") for blocker in blockers):
        return False
    retryable_prefixes = (
        "hedge_floor_",
        "option_path_",
        "paired_scalp_path_",
        "paired_seed_",
        "profile_distribution_",
        "grid_price_",
        "tail_reversal_",
        "no_verified_live_candidate",
        "missing_",
        "spread_",
        "insufficient_",
        "stale_",
    )
    return all(any(blocker.startswith(prefix) for prefix in retryable_prefixes) for blocker in blockers)


def _planned_item_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "phase": item.get("phase"),
        "strategy_ids": [item.get("strategy_id")],
        "queue_key": item.get("queue_key"),
        "status": "planned",
        "live_submission_attempted": False,
        "manual_orders_avoided": True,
    }


def _next_queue_action(queue: dict[str, Any], promotions: dict[str, Any]) -> str:
    queue_counts = dict(queue.get("by_status") or {})
    if queue_counts.get("queued", 0):
        return "continue draining strategy_pipeline_queue"
    promotion_counts = dict(promotions.get("by_promotion_state") or {})
    if promotion_counts.get("LIVE_CANDIDATE", 0):
        return "live candidates exist; run live candidate queue item through strategy-defined gates"
    return "review blocked queue rows and strategy promotion blockers"


def _write_queue_worker_report(payload: dict[str, Any], report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    latest_json = report_dir / "strategy_pipeline_queue_worker_latest.json"
    latest_md = report_dir / "strategy_pipeline_queue_worker_latest.md"
    latest_json.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    lines = [
        "# Strategy Pipeline Queue Worker",
        "",
        f"Generated UTC: {payload['generated_at_utc']}",
        f"Run ID: `{payload['run_id']}`",
        f"Dry run: `{payload['dry_run']}`",
        "",
        "## Queue Contract",
        "",
        "Strategy registration and promotion-state changes create durable queue work. This worker only drains bounded queued work; it does not discover strategies as a scheduler.",
        "",
        "## Counts",
        "",
        f"- Backfilled queue items: `{payload['backfilled_count']}`",
        f"- Claimed queue items: `{payload['claimed_count']}`",
        f"- Executed actions: `{payload['executed_action_count']}`",
        f"- Before promotions: `{json.dumps(payload['before_promotion_counts'], sort_keys=True)}`",
        f"- After promotions: `{json.dumps(payload['after_promotion_counts'], sort_keys=True)}`",
        f"- Before queue: `{json.dumps(payload['before_queue_counts'], sort_keys=True)}`",
        f"- After queue: `{json.dumps(payload['after_queue_counts'], sort_keys=True)}`",
        "",
        "## Actions",
        "",
    ]
    if not payload["actions"]:
        lines.append("- No queue item claimed.")
    for action in payload["actions"]:
        lines.append(
            "- "
            f"`{action.get('phase')}` `{action.get('status')}` "
            f"queue=`{action.get('queue_key', 'n/a')}` "
            f"strategies=`{','.join(str(item) for item in (action.get('strategy_ids') or []))}` "
            f"passed=`{action.get('passed_count', 'n/a')}` "
            f"blocked=`{action.get('blocked_count', 'n/a')}` "
            f"queue_after=`{action.get('queue_status_after_action', 'n/a')}`"
        )
        blockers = action.get("blockers") or []
        if blockers:
            lines.append(f"  blockers=`{','.join(str(blocker) for blocker in blockers)}`")
    post_live = payload.get("post_live_reconciliation") if isinstance(payload.get("post_live_reconciliation"), dict) else {}
    if post_live:
        result = post_live.get("result") if isinstance(post_live.get("result"), dict) else {}
        lines.extend(
            [
                "",
                "## Post-Live Reconciliation",
                "",
                f"- Status: `{post_live.get('status')}`",
                f"- Open orders fetched: `{post_live.get('open_order_count', 0)}`",
                f"- Trades fetched: `{post_live.get('trade_count', 0)}`",
                f"- Pending orders checked: `{result.get('checked_order_count', 0)}`",
                f"- Filled from trade history: `{result.get('filled_order_count', 0)}`",
                f"- Expired/cancelled without fill: `{result.get('expired_order_count', 0)}`",
                f"- Updated ledgers: `{result.get('updated_ledger_count', 0)}`",
            ]
        )
        blockers = post_live.get("blockers") or []
        if blockers:
            lines.append(f"- Blockers: `{','.join(str(blocker) for blocker in blockers)}`")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            f"- Live submission attempted: `{payload['live_submission_attempted']}`",
            f"- Manual orders avoided: `{payload['manual_orders_avoided']}`",
            "",
            "## Next Action",
            "",
            str(payload["next_action"]),
            "",
        ]
    )
    latest_md.write_text("\n".join(lines), encoding="utf-8")
