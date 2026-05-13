from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg2.extensions import connection as PsycopgConnection

from app.api.db import to_jsonable
from app.api.dependencies import get_db_connection
from app.api.routers.portfolio import (
    _fetch_order_lifecycle_reconciliation_rows,
    _resolve_order_lifecycle_direct_context,
    build_order_lifecycle_reconciliation_report,
    build_portfolio_pnl_attribution_report,
)
from app.modules.agentic.contracts import (
    LLMRevisionAdoptionRequest,
    LLMRevisionResponse,
    LiveStrategyWorkerRequest,
    MarketOrderbookTickRequest,
    MarketTradeObservationRequest,
    MarketWatchSessionRequest,
    OperatorInterventionRequest,
    OpsCycleRequest,
    PregamePlanRequest,
    ReplayFromWatchSessionRequest,
    StrategyPlan,
    StrategyPlanEvaluationRequest,
    WatchlistRequest,
)
from app.modules.agentic.engine import evaluate_strategy_plan
from app.modules.agentic.live_strategy_worker import build_live_strategy_worker_readiness, get_live_strategy_worker
from app.modules.agentic.llm_runtime import load_latest_llm_runtime_status
from app.modules.agentic.ops_checks import build_integrity_snapshot
from app.modules.agentic.repository import (
    get_agentic_database_status,
    try_persist_market_trades,
    try_persist_orderbook_ticks,
    try_persist_operator_intervention,
    try_persist_replay_request,
    try_persist_strategy_decisions,
    try_persist_watch_session,
    try_persist_watchlist_event,
)
from app.modules.agentic.store import (
    append_pregame_research,
    append_jsonl,
    build_event_agent_context,
    build_ops_status,
    load_current_strategy_plan,
    load_current_strategy_plan_for_event,
    ops_artifact_root,
    record_ops_stage,
    strategy_plan_root,
    write_json,
    write_strategy_plan,
)
from app.modules.nba.execution.adapter import create_live_order, resolve_trading_account


router = APIRouter(prefix="/v1", tags=["ops"])


@router.get("/ops/status")
def get_ops_status() -> dict[str, Any]:
    return build_ops_status()


@router.post("/ops/data-refresh", status_code=status.HTTP_202_ACCEPTED)
def run_ops_data_refresh(payload: OpsCycleRequest) -> dict[str, Any]:
    return record_ops_stage("data-refresh", payload.model_dump(mode="json"), day=payload.session_date)


@router.post("/ops/integrity-check", status_code=status.HTTP_202_ACCEPTED)
def run_ops_integrity_check(
    payload: OpsCycleRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    ops_status = build_ops_status()
    integrity_event_ids = _resolve_current_plan_event_ids(payload.event_ids, day=payload.session_date)
    direct_trade_token_ids = _direct_trade_token_ids_for_events(integrity_event_ids, day=payload.session_date)
    integrity = build_integrity_snapshot(
        connection,
        account_id=payload.account_id,
        direct_trade_token_ids=direct_trade_token_ids,
    )
    recorded = record_ops_stage(
        "integrity-check",
        {
            **payload.model_dump(mode="json"),
            "ops_status": ops_status,
            "requested_event_ids": payload.event_ids,
            "resolved_event_ids": integrity_event_ids,
            "integrity": integrity,
        },
        day=payload.session_date,
    )
    return {
        **recorded,
        "ops_status": ops_status,
        "requested_event_ids": payload.event_ids,
        "resolved_event_ids": integrity_event_ids,
        "integrity": integrity,
    }


@router.post("/ops/pregame-plan", status_code=status.HTTP_202_ACCEPTED)
def run_ops_pregame_plan(payload: PregamePlanRequest) -> dict[str, Any]:
    pregame_file = append_pregame_research(
        day=payload.session_date,
        research_markdown=payload.research_markdown,
        research_path=payload.research_path,
        source=payload.source,
        event_ids=payload.event_ids,
        notes=payload.notes,
    )
    strategy_plan_records = [
        write_strategy_plan(plan, day=payload.session_date)
        for plan in payload.strategy_plans
    ]
    required_event_ids = payload.event_ids or [plan.event_id for plan in payload.strategy_plans]
    strategy_plan_gate = _build_strategy_plan_gate(required_event_ids, day=payload.session_date)
    recorded = record_ops_stage(
        "pregame-plan",
        {
            **payload.model_dump(mode="json"),
            "pregame_file": pregame_file,
            "strategy_plan_records": strategy_plan_records,
            "strategy_plan_gate": strategy_plan_gate,
        },
        day=payload.session_date,
    )
    return {
        **recorded,
        "pregame_file": pregame_file,
        "strategy_plan_records": strategy_plan_records,
        "strategy_plan_gate": strategy_plan_gate,
    }


@router.post("/ops/live-monitor", status_code=status.HTTP_202_ACCEPTED)
def run_ops_live_monitor(
    payload: OpsCycleRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    ops_status = build_ops_status()
    live_monitor_event_ids = _resolve_live_monitor_event_ids(payload.event_ids, day=payload.session_date)
    direct_trade_token_ids = _direct_trade_token_ids_for_events(live_monitor_event_ids, day=payload.session_date)
    integrity = build_integrity_snapshot(
        connection,
        account_id=payload.account_id,
        direct_trade_token_ids=direct_trade_token_ids,
    )
    strategy_plan_gate = _build_strategy_plan_gate(live_monitor_event_ids, day=payload.session_date)
    llm_runtime_status = load_latest_llm_runtime_status(
        session_date=payload.session_date,
        event_ids=live_monitor_event_ids,
    )
    live_strategy_worker_status = build_live_strategy_worker_readiness(
        session_date=payload.session_date,
        event_ids=live_monitor_event_ids,
        strategy_plan_gate=strategy_plan_gate,
    )
    live_execution_evidence = _build_live_execution_evidence(
        connection,
        event_ids=live_strategy_worker_status.get("expected_event_ids") or [],
        worker_required=bool(live_strategy_worker_status.get("worker_required")),
        worker_ready=bool(live_strategy_worker_status.get("ready_for_live_execution")),
    )
    live_monitor_readiness = _build_live_monitor_readiness(
        integrity=integrity,
        strategy_plan_gate=strategy_plan_gate,
        live_strategy_worker_status=live_strategy_worker_status,
        live_execution_evidence=live_execution_evidence,
    )
    recorded = record_ops_stage(
        "live-monitor",
        {
            **payload.model_dump(mode="json"),
            "ops_status": ops_status,
            "requested_event_ids": payload.event_ids,
            "resolved_event_ids": live_monitor_event_ids,
            "integrity": integrity,
            "strategy_plan_gate": strategy_plan_gate,
            "llm_runtime_status": llm_runtime_status,
            "live_strategy_worker_status": live_strategy_worker_status,
            "live_execution_evidence": live_execution_evidence,
            "live_monitor_readiness": live_monitor_readiness,
        },
        day=payload.session_date,
    )
    return {
        **recorded,
        "ops_status": ops_status,
        "requested_event_ids": payload.event_ids,
        "resolved_event_ids": live_monitor_event_ids,
        "integrity": integrity,
        "strategy_plan_gate": strategy_plan_gate,
        "llm_runtime_status": llm_runtime_status,
        "live_strategy_worker_status": live_strategy_worker_status,
        "live_execution_evidence": live_execution_evidence,
        "live_monitor_readiness": live_monitor_readiness,
    }


@router.get("/ops/live-strategy-worker/status")
def get_ops_live_strategy_worker_status() -> dict[str, Any]:
    return get_live_strategy_worker().status()


@router.post("/ops/live-strategy-worker/tick", status_code=status.HTTP_202_ACCEPTED)
def run_ops_live_strategy_worker_tick(payload: LiveStrategyWorkerRequest | None = None) -> dict[str, Any]:
    overrides = payload.model_dump(mode="json", exclude_none=True) if payload is not None else {}
    return get_live_strategy_worker().run_once(overrides)


@router.post("/ops/live-strategy-worker/start", status_code=status.HTTP_202_ACCEPTED)
def start_ops_live_strategy_worker(payload: LiveStrategyWorkerRequest | None = None) -> dict[str, Any]:
    overrides = payload.model_dump(mode="json", exclude_none=True) if payload is not None else {}
    return get_live_strategy_worker().start(overrides)


@router.post("/ops/live-strategy-worker/stop", status_code=status.HTTP_202_ACCEPTED)
def stop_ops_live_strategy_worker() -> dict[str, Any]:
    return get_live_strategy_worker().stop()


@router.post("/ops/postgame-review", status_code=status.HTTP_202_ACCEPTED)
def run_ops_postgame_review(
    payload: OpsCycleRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    reviewed_event_ids = _resolve_postgame_review_event_ids(payload.event_ids, day=payload.session_date)
    strategy_plan_gate = _build_strategy_plan_gate(reviewed_event_ids, day=payload.session_date)
    postgame_live_evidence = _build_postgame_live_evidence(
        connection,
        event_ids=reviewed_event_ids,
        day=payload.session_date,
    )
    portfolio_pnl_attribution = _build_postgame_portfolio_pnl_attribution(
        connection,
        payload,
        event_ids=reviewed_event_ids,
    )
    recorded = record_ops_stage(
        "postgame-review",
        {
            **payload.model_dump(mode="json"),
            "reviewed_event_ids": reviewed_event_ids,
            "strategy_plan_gate": strategy_plan_gate,
            "postgame_live_evidence": postgame_live_evidence,
            "portfolio_pnl_attribution": portfolio_pnl_attribution,
        },
        day=payload.session_date,
    )
    return {
        **recorded,
        "reviewed_event_ids": reviewed_event_ids,
        "strategy_plan_gate": strategy_plan_gate,
        "postgame_live_evidence": postgame_live_evidence,
        "portfolio_pnl_attribution": portfolio_pnl_attribution,
    }


@router.get("/events/{event_id}/agent-context")
def get_event_agent_context(event_id: str, session_date: str | None = None) -> dict[str, Any]:
    return build_event_agent_context(event_id, day=session_date)


@router.post("/events/{event_id}/strategy-plan", status_code=status.HTTP_201_CREATED)
def submit_event_strategy_plan(event_id: str, payload: StrategyPlan) -> dict[str, Any]:
    if payload.event_id != event_id:
        raise HTTPException(status_code=422, detail="path event_id must match payload.event_id")
    return write_strategy_plan(payload)


@router.get("/events/{event_id}/strategy-plan/current")
def get_current_event_strategy_plan(event_id: str, session_date: str | None = None) -> dict[str, Any]:
    context = build_event_agent_context(event_id, day=session_date)
    current_plan = context.get("current_strategy_plan")
    if not current_plan:
        raise HTTPException(status_code=404, detail="no current strategy plan for event_id")
    return current_plan


@router.post("/events/{event_id}/llm-revision/adopt", status_code=status.HTTP_202_ACCEPTED)
def adopt_event_llm_revision(event_id: str, payload: LLMRevisionAdoptionRequest) -> dict[str, Any]:
    response, trace_metadata = _resolve_llm_revision_response(event_id, payload)
    revised_plan_payload = response.revised_strategy_plan
    if response.status in {"detected_only", "skipped_unavailable"} or response.skipped_reason:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "llm_revision_not_adoptable",
                "response_status": response.status,
                "skipped_reason": response.skipped_reason,
            },
        )
    if not isinstance(revised_plan_payload, dict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"reason": "revised_strategy_plan_required"},
        )

    current_plan = load_current_strategy_plan(event_id, day=payload.session_date)
    try:
        revised_plan = StrategyPlan.model_validate(
            _with_llm_adoption_metadata(
                revised_plan_payload,
                event_id=event_id,
                payload=payload,
                response=response,
                trace_metadata=trace_metadata,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"reason": "revised_strategy_plan_invalid", "error": str(exc)},
        ) from exc
    if revised_plan.event_id != event_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"reason": "event_id_mismatch", "path_event_id": event_id, "plan_event_id": revised_plan.event_id},
        )

    plan_diff = _strategy_plan_diff(current_plan, revised_plan.model_dump(mode="json"))
    adoption_record = _write_llm_revision_adoption_artifact(
        event_id=event_id,
        payload=payload,
        response=response,
        trace_metadata=trace_metadata,
        plan_diff=plan_diff,
        revised_plan=revised_plan,
    )
    strategy_plan_record = None
    if payload.apply_current:
        strategy_plan_record = write_strategy_plan(revised_plan, day=payload.session_date)

    return {
        "status": "adopted_current" if payload.apply_current else "candidate_recorded",
        "event_id": event_id,
        "session_date": payload.session_date,
        "reviewed_by": payload.reviewed_by,
        "review_reason": payload.review_reason,
        "request_id": response.request_id,
        "selected_model": response.selected_model,
        "apply_current": payload.apply_current,
        "order_endpoint_call_allowed": False,
        "plan_diff": plan_diff,
        "adoption_artifact": adoption_record,
        "strategy_plan_record": strategy_plan_record,
    }


@router.post("/events/{event_id}/strategy-plan/evaluate")
def evaluate_event_strategy_plan(event_id: str, payload: StrategyPlanEvaluationRequest) -> dict[str, Any]:
    plan = _resolve_strategy_plan(event_id, payload)
    result = evaluate_strategy_plan(
        plan,
        market_state=payload.market_state,
        portfolio_state=payload.portfolio_state,
        dry_run=payload.dry_run,
        max_intents=payload.max_intents,
    )
    decision_persistence = try_persist_strategy_decisions(
        result,
        source=payload.source,
        execute_requested=False,
        account_id=payload.account_id,
    )
    return {**result.model_dump(mode="json"), "decision_persistence": decision_persistence}


@router.post("/events/{event_id}/strategy-plan/execute", status_code=status.HTTP_202_ACCEPTED)
def execute_event_strategy_plan(
    event_id: str,
    payload: StrategyPlanEvaluationRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    plan = _resolve_strategy_plan(event_id, payload)
    result = evaluate_strategy_plan(
        plan,
        market_state=payload.market_state,
        portfolio_state=payload.portfolio_state,
        dry_run=payload.dry_run,
        max_intents=payload.max_intents,
    )
    if not payload.execute:
        decision_persistence = try_persist_strategy_decisions(
            result,
            source=payload.source,
            execute_requested=False,
            account_id=payload.account_id,
        )
        return {**result.model_dump(mode="json"), "decision_persistence": decision_persistence}

    account = resolve_trading_account(connection, account_id=payload.account_id)
    executed_orders: list[dict[str, Any]] = []
    for intent in result.intents:
        metadata = {
            "run_id": f"strategy-plan-{event_id}",
            "execution_profile_version": "agentic_strategy_plan_v1",
            "controller_name": "agentic_strategy_plan",
            "controller_source": payload.source,
            "game_id": event_id,
            "market_id": intent.market_id,
            "outcome_id": intent.outcome_id,
            "strategy_family": intent.strategy_family,
            "strategy_id": intent.strategy_id,
            "signal_id": intent.intent_id,
            "signal_price": intent.price,
            "entry_reason": intent.reason,
            "order_policy": "strategy_plan_json",
            "agentic_strategy_plan": intent.metadata,
        }
        placed = create_live_order(
            connection,
            account=account,
            market_id=intent.market_id,
            outcome_id=intent.outcome_id,
            token_id=intent.token_id,
            side=intent.side,
            size=intent.size,
            price=intent.price,
            order_type=intent.order_type,
            metadata_json=metadata,
            dry_run=payload.dry_run,
            time_in_force=intent.time_in_force,
        )
        executed_orders.append({"intent_id": intent.intent_id, **placed})
    result.executed_orders = executed_orders
    decision_persistence = try_persist_strategy_decisions(
        result,
        source=payload.source,
        execute_requested=True,
        account_id=payload.account_id,
    )
    return {**result.model_dump(mode="json"), "decision_persistence": decision_persistence}


@router.post("/watchlists/events", status_code=status.HTTP_201_CREATED)
def add_watchlist_events(payload: WatchlistRequest) -> dict[str, Any]:
    root = ops_artifact_root()
    path = root / "watchlist_events.jsonl"
    db_persistence: list[dict[str, Any]] = []
    for item in payload.events:
        append_jsonl(
            path,
            {
                "source": payload.source,
                "event": item.model_dump(mode="json"),
            },
        )
        db_persistence.append({"event_key": item.event_key, **try_persist_watchlist_event(item, source=payload.source)})
    write_json(root / "watchlist_latest.json", payload.model_dump(mode="json"))
    return {"status": "stored", "event_count": len(payload.events), "path": str(path), "db_persistence": db_persistence}


@router.post("/watchlists/sessions", status_code=status.HTTP_201_CREATED)
def start_watch_session(payload: MarketWatchSessionRequest) -> dict[str, Any]:
    root = ops_artifact_root()
    db_persistence = try_persist_watch_session(payload)
    record = {
        "source": "watch-session",
        "payload": payload.model_dump(mode="json"),
        "db_persistence": db_persistence,
    }
    append_jsonl(root / "watch_sessions.jsonl", record)
    return {"status": "stored", "db_persistence": db_persistence}


@router.post("/watchlists/orderbook-ticks", status_code=status.HTTP_202_ACCEPTED)
def record_orderbook_ticks(payload: MarketOrderbookTickRequest) -> dict[str, Any]:
    root = ops_artifact_root()
    db_persistence = try_persist_orderbook_ticks(payload)
    append_jsonl(
        root / "orderbook_tick_batches.jsonl",
        {
            "source": payload.source,
            "tick_count": len(payload.ticks),
            "db_persistence": db_persistence,
        },
    )
    return {"status": "stored", "tick_count": len(payload.ticks), "db_persistence": db_persistence}


@router.post("/watchlists/trades", status_code=status.HTTP_202_ACCEPTED)
def record_market_trades(payload: MarketTradeObservationRequest) -> dict[str, Any]:
    root = ops_artifact_root()
    db_persistence = try_persist_market_trades(payload)
    append_jsonl(
        root / "trade_batches.jsonl",
        {
            "source": payload.source,
            "trade_count": len(payload.trades),
            "db_persistence": db_persistence,
        },
    )
    return {"status": "stored", "trade_count": len(payload.trades), "db_persistence": db_persistence}


@router.post("/replay/from-watch-session", status_code=status.HTTP_202_ACCEPTED)
def build_replay_from_watch_session(payload: ReplayFromWatchSessionRequest) -> dict[str, Any]:
    recorded = record_ops_stage("replay-from-watch-session", payload.model_dump(mode="json"))
    return {**recorded, "db_persistence": try_persist_replay_request(payload, output_root=recorded.get("path"))}


@router.post("/operator/interventions/reconcile", status_code=status.HTTP_202_ACCEPTED)
def reconcile_operator_interventions(payload: OperatorInterventionRequest) -> dict[str, Any]:
    recorded = record_ops_stage("operator-intervention-reconcile", payload.model_dump(mode="json"))
    return {
        **recorded,
        "db_persistence": try_persist_operator_intervention(payload),
        "database": get_agentic_database_status(),
    }


def _resolve_strategy_plan(event_id: str, payload: StrategyPlanEvaluationRequest) -> StrategyPlan:
    if payload.plan is not None:
        if payload.plan.event_id != event_id:
            raise HTTPException(status_code=422, detail="path event_id must match payload.plan.event_id")
        return payload.plan
    stored = load_current_strategy_plan(event_id, day=payload.session_date)
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "strategy_plan_required",
                "event_id": event_id,
                "session_date": payload.session_date,
                "message": "No current StrategyPlanJSON exists for this event. Submit a plan before evaluate/execute.",
            },
        )
    try:
        return StrategyPlan.model_validate(stored)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "reason": "current_strategy_plan_invalid",
                "event_id": event_id,
                "session_date": payload.session_date,
                "error": str(exc),
            },
        ) from exc


def _build_strategy_plan_gate(event_ids: list[str], *, day: str | None) -> dict[str, Any]:
    unique_event_ids = [event_id for event_id in dict.fromkeys(str(item).strip() for item in event_ids) if event_id]
    plans: list[dict[str, Any]] = []
    missing_event_ids: list[str] = []
    for event_id in unique_event_ids:
        plan = load_current_strategy_plan(event_id, day=day)
        if not plan:
            missing_event_ids.append(event_id)
            continue
        sleeves = _strategy_plan_sleeves(plan)
        plans.append(
            {
                "event_id": event_id,
                "market_id": plan.get("market_id"),
                "schema_version": plan.get("schema_version"),
                "plan_owner": plan.get("plan_owner"),
                "active_strategy_count": len(plan.get("active_strategies") or []),
                "sleeve_count": len(sleeves),
                "sleeves": sleeves,
            }
        )
    status_text = "not_required" if not unique_event_ids else ("ready" if not missing_event_ids else "blocked")
    return {
        "status": status_text,
        "required_event_count": len(unique_event_ids),
        "current_plan_count": len(plans),
        "missing_event_ids": missing_event_ids,
        "current_plans": plans,
        "ready_for_strategy_evaluation": bool(unique_event_ids) and not missing_event_ids,
        "blocker_reason": "missing_current_strategy_plan" if missing_event_ids else None,
    }


def _resolve_live_monitor_event_ids(event_ids: list[str], *, day: str | None) -> list[str]:
    explicit_event_ids = _normalized_unique_values(event_ids)
    if explicit_event_ids:
        return _resolve_current_plan_event_ids(explicit_event_ids, day=day)
    if not day:
        return []
    root = strategy_plan_root(day)
    if not root.exists():
        return []
    now = datetime.now(timezone.utc)
    resolved: list[str] = []
    for current_path in sorted(root.glob("*/current.json")):
        try:
            payload = json.loads(current_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        valid_until = _parse_datetime(payload.get("valid_until_utc"))
        if valid_until is not None and valid_until <= now:
            continue
        event_id = str(payload.get("event_id") or current_path.parent.name).strip()
        if event_id:
            resolved.append(event_id)
    return _normalized_unique_values(resolved)


def _resolve_current_plan_event_ids(event_ids: list[str], *, day: str | None) -> list[str]:
    resolved: list[str] = []
    for event_id in _normalized_unique_values(event_ids):
        _, resolved_event_id, _ = load_current_strategy_plan_for_event(event_id, day=day)
        resolved.append(resolved_event_id or event_id)
    return _normalized_unique_values(resolved)


def _build_live_monitor_readiness(
    *,
    integrity: dict[str, Any],
    strategy_plan_gate: dict[str, Any],
    live_strategy_worker_status: dict[str, Any],
    live_execution_evidence: dict[str, Any],
) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    strategy_status = str(strategy_plan_gate.get("status") or "")
    worker_required = bool(live_strategy_worker_status.get("worker_required"))
    worker_ready = bool(live_strategy_worker_status.get("ready_for_live_execution"))
    strategy_ready = bool(strategy_plan_gate.get("ready_for_strategy_evaluation"))

    if strategy_status == "blocked":
        blockers.append(
            {
                "reason": strategy_plan_gate.get("blocker_reason") or "strategy_plan_gate_blocked",
                "missing_event_ids": strategy_plan_gate.get("missing_event_ids") or [],
            }
        )
    if not bool(integrity.get("ready_for_live_minimum_orders")):
        blockers.append(
            {
                "reason": "integrity_not_ready_for_live_minimum_orders",
                "integrity_blockers": integrity.get("blockers") or [],
            }
        )
    if worker_required and not worker_ready:
        blockers.append(
            {
                "reason": live_strategy_worker_status.get("blocker_reason") or "live_strategy_worker_not_ready",
                "expected_event_ids": live_strategy_worker_status.get("expected_event_ids") or [],
                "heartbeat_age_seconds": live_strategy_worker_status.get("heartbeat_age_seconds"),
                "heartbeat_max_age_seconds": live_strategy_worker_status.get("heartbeat_max_age_seconds"),
            }
        )
    if live_execution_evidence.get("gate") == "RED":
        for reason in live_execution_evidence.get("blocker_reasons") or ["live_execution_evidence_blocked"]:
            blockers.append(
                {
                    "reason": reason,
                    "live_execution_evidence_status": live_execution_evidence.get("status"),
                    "event_ids": live_execution_evidence.get("event_ids") or [],
                }
            )

    live_scope_required = strategy_ready or worker_required
    if blockers:
        status_text = "blocked"
        gate = "RED"
        ready_for_live_execution = False
    elif not live_scope_required:
        status_text = "not_required"
        gate = "YELLOW"
        ready_for_live_execution = False
    else:
        status_text = "ready"
        gate = "GREEN"
        ready_for_live_execution = True

    return {
        "schema_version": "live_monitor_readiness_v1",
        "status": status_text,
        "gate": gate,
        "ready_for_live_execution": ready_for_live_execution,
        "health_only_not_executor": True,
        "blockers": blockers,
        "blocker_reasons": [str(blocker.get("reason")) for blocker in blockers if blocker.get("reason")],
        "strategy_plan_gate_status": strategy_status,
        "worker_required": worker_required,
        "worker_status": live_strategy_worker_status.get("status"),
        "live_execution_evidence_status": live_execution_evidence.get("status"),
    }


def _build_live_execution_evidence(
    connection: PsycopgConnection,
    *,
    event_ids: list[str],
    worker_required: bool,
    worker_ready: bool,
    now_utc: datetime | None = None,
    max_age_seconds: float = 120.0,
) -> dict[str, Any]:
    unique_event_ids = _normalized_unique_values(event_ids)
    if not unique_event_ids:
        return {
            "schema_version": "live_execution_evidence_v1",
            "status": "not_required",
            "gate": "YELLOW",
            "reason": "no_expected_event_ids",
            "event_ids": [],
            "items": [],
            "blocker_reasons": [],
        }
    if not worker_required:
        return {
            "schema_version": "live_execution_evidence_v1",
            "status": "not_required",
            "gate": "YELLOW",
            "reason": "live_strategy_worker_not_required",
            "event_ids": unique_event_ids,
            "items": [],
            "blocker_reasons": [],
        }
    if not worker_ready:
        return {
            "schema_version": "live_execution_evidence_v1",
            "status": "waiting_for_worker",
            "gate": "YELLOW",
            "reason": "live_strategy_worker_not_ready",
            "event_ids": unique_event_ids,
            "items": [],
            "blocker_reasons": [],
        }

    items: list[dict[str, Any]] = []
    now = now_utc or datetime.now(timezone.utc)
    for event_id in unique_event_ids:
        try:
            counts = _fetch_live_execution_evidence_counts(connection, event_id=event_id)
            item = _classify_live_execution_evidence_item(
                event_id=event_id,
                counts=counts,
                now_utc=now,
                max_age_seconds=max_age_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            item = {
                "event_id": event_id,
                "status": "blocked",
                "blockers": [{"reason": "live_execution_evidence_query_failed", "error": _exception_detail(exc)}],
                "counts": {},
            }
        items.append(item)

    blocker_reasons = sorted(
        {
            str(blocker.get("reason"))
            for item in items
            for blocker in item.get("blockers") or []
            if blocker.get("reason")
        }
    )
    if blocker_reasons:
        status_text = "blocked"
        gate = "RED"
    else:
        status_text = "ready"
        gate = "GREEN"
    return {
        "schema_version": "live_execution_evidence_v1",
        "status": status_text,
        "gate": gate,
        "event_ids": unique_event_ids,
        "event_count": len(items),
        "items": items,
        "blocker_reasons": blocker_reasons,
        "max_age_seconds": max_age_seconds,
    }


def _fetch_live_execution_evidence_counts(
    connection: PsycopgConnection,
    *,
    event_id: str,
) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                (SELECT count(*) FROM agentic.market_watch_sessions WHERE event_key = %s) AS watch_session_count,
                (
                    SELECT max(started_at)
                    FROM agentic.market_watch_sessions
                    WHERE event_key = %s
                ) AS latest_watch_session_started_at,
                (
                    SELECT max(ended_at)
                    FROM agentic.market_watch_sessions
                    WHERE event_key = %s
                ) AS latest_watch_session_ended_at,
                (SELECT count(*) FROM agentic.market_orderbook_ticks WHERE event_key = %s) AS orderbook_tick_count,
                (
                    SELECT max(captured_at)
                    FROM agentic.market_orderbook_ticks
                    WHERE event_key = %s
                ) AS latest_orderbook_tick_at,
                (SELECT count(*) FROM agentic.strategy_decisions WHERE event_key = %s) AS strategy_decision_count,
                (
                    SELECT max(decided_at)
                    FROM agentic.strategy_decisions
                    WHERE event_key = %s
                ) AS latest_strategy_decision_at;
            """,
            (
                event_id,
                event_id,
                event_id,
                event_id,
                event_id,
                event_id,
                event_id,
            ),
        )
        row = cursor.fetchone()
        columns = [description[0] for description in cursor.description]
    payload = dict(zip(columns, row or []))
    return {key: to_jsonable(value) for key, value in payload.items()}


def _classify_live_execution_evidence_item(
    *,
    event_id: str,
    counts: dict[str, Any],
    now_utc: datetime,
    max_age_seconds: float,
) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    watch_session_count = _safe_int(counts.get("watch_session_count"))
    orderbook_tick_count = _safe_int(counts.get("orderbook_tick_count"))
    strategy_decision_count = _safe_int(counts.get("strategy_decision_count"))
    latest_orderbook_tick_at = counts.get("latest_orderbook_tick_at")
    latest_strategy_decision_at = counts.get("latest_strategy_decision_at")
    orderbook_tick_age_seconds = _timestamp_age_seconds(latest_orderbook_tick_at, now_utc=now_utc)
    strategy_decision_age_seconds = _timestamp_age_seconds(latest_strategy_decision_at, now_utc=now_utc)

    if watch_session_count < 1:
        blockers.append({"reason": "watch_session_missing", "watch_session_count": watch_session_count})
    if orderbook_tick_age_seconds is None:
        blockers.append({"reason": "live_orderbook_tick_missing", "orderbook_tick_count": orderbook_tick_count})
    elif orderbook_tick_age_seconds > max_age_seconds:
        blockers.append(
            {
                "reason": "live_orderbook_tick_stale",
                "age_seconds": orderbook_tick_age_seconds,
                "max_age_seconds": max_age_seconds,
                "latest_orderbook_tick_at": latest_orderbook_tick_at,
            }
        )
    if strategy_decision_age_seconds is None:
        blockers.append({"reason": "live_strategy_decision_missing", "strategy_decision_count": strategy_decision_count})
    elif strategy_decision_age_seconds > max_age_seconds:
        blockers.append(
            {
                "reason": "live_strategy_decision_stale",
                "age_seconds": strategy_decision_age_seconds,
                "max_age_seconds": max_age_seconds,
                "latest_strategy_decision_at": latest_strategy_decision_at,
            }
        )

    return {
        "event_id": event_id,
        "status": "blocked" if blockers else "ready",
        "blockers": blockers,
        "counts": {
            "watch_session_count": watch_session_count,
            "orderbook_tick_count": orderbook_tick_count,
            "strategy_decision_count": strategy_decision_count,
            "latest_watch_session_started_at": counts.get("latest_watch_session_started_at"),
            "latest_watch_session_ended_at": counts.get("latest_watch_session_ended_at"),
            "latest_orderbook_tick_at": latest_orderbook_tick_at,
            "latest_strategy_decision_at": latest_strategy_decision_at,
            "orderbook_tick_age_seconds": orderbook_tick_age_seconds,
            "strategy_decision_age_seconds": strategy_decision_age_seconds,
        },
    }


def _strategy_plan_sleeves(plan: dict[str, Any]) -> list[dict[str, Any]]:
    sleeves: list[dict[str, Any]] = []
    for strategy in plan.get("active_strategies") or []:
        if not isinstance(strategy, dict):
            continue
        entry_rules = strategy.get("entry_rules") if isinstance(strategy.get("entry_rules"), dict) else {}
        strategy_id = str(strategy.get("strategy_id") or "").strip()
        sleeve_id = str(strategy.get("sleeve_id") or entry_rules.get("sleeve_id") or strategy_id).strip()
        sleeve = {
            "sleeve_id": sleeve_id or strategy_id,
            "strategy_id": strategy_id,
            "strategy_family": strategy.get("family"),
            "side": strategy.get("side"),
        }
        sleeve_group = str(strategy.get("sleeve_group") or entry_rules.get("sleeve_group") or "").strip()
        sleeve_role = str(strategy.get("sleeve_role") or entry_rules.get("sleeve_role") or "").strip()
        if sleeve_group:
            sleeve["sleeve_group"] = sleeve_group
        if sleeve_role:
            sleeve["sleeve_role"] = sleeve_role
        sleeves.append(sleeve)
    return sleeves


def _resolve_postgame_review_event_ids(event_ids: list[str], *, day: str | None) -> list[str]:
    explicit_event_ids = _normalized_unique_values(event_ids)
    if explicit_event_ids:
        return explicit_event_ids

    root = strategy_plan_root(day)
    if not root.exists():
        return []

    resolved: list[str] = []
    for current_path in sorted(root.glob("*/current.json")):
        event_id = current_path.parent.name
        try:
            plan = json.loads(current_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            plan = {}
        if isinstance(plan, dict):
            event_id = str(plan.get("event_id") or event_id).strip()
        if event_id:
            resolved.append(event_id)
    return _normalized_unique_values(resolved)


def _build_postgame_portfolio_pnl_attribution(
    connection: PsycopgConnection,
    payload: OpsCycleRequest,
    *,
    event_ids: list[str],
) -> dict[str, Any]:
    if not event_ids:
        return {
            "status": "not_requested",
            "reason": "no_reviewed_event_ids",
            "event_count": 0,
            "items": [],
        }
    if not payload.account_id:
        return {
            "status": "skipped",
            "reason": "account_id_required",
            "event_count": len(event_ids),
            "items": [],
        }

    try:
        direct_context = _resolve_order_lifecycle_direct_context(
            connection,
            account_id=payload.account_id,
            direct_open_order_external_id=None,
            direct_open_order_count=None,
            direct_open_position_count=None,
            include_direct_clob_evidence=True,
        )
    except Exception as exc:  # noqa: BLE001
        direct_context = {
            "direct_open_order_external_ids": [],
            "direct_open_order_count": None,
            "direct_open_position_count": None,
            "direct_trade_rows": [],
            "direct_evidence": {
                "enabled": True,
                "ok": False,
                "error": _exception_detail(exc),
            },
        }

    direct_evidence = direct_context.get("direct_evidence") or {}
    direct_evidence_summary = {key: value for key, value in direct_evidence.items() if key != "trades"}
    items: list[dict[str, Any]] = []
    for event_id in event_ids:
        try:
            rows = _fetch_order_lifecycle_reconciliation_rows(
                connection,
                account_id=payload.account_id,
                event_slug=event_id,
            )
            lifecycle_report = build_order_lifecycle_reconciliation_report(
                rows,
                direct_open_order_external_ids=direct_context["direct_open_order_external_ids"],
                direct_open_order_count=direct_context["direct_open_order_count"],
                direct_open_position_count=direct_context["direct_open_position_count"],
                direct_trade_rows=direct_context["direct_trade_rows"],
            )
            pnl_attribution = build_portfolio_pnl_attribution_report(lifecycle_report)
            items.append(
                {
                    "ok": True,
                    "event_id": event_id,
                    "event_slug": event_id,
                    "reconciliation": lifecycle_report,
                    "pnl_attribution": pnl_attribution,
                }
            )
        except Exception as exc:  # noqa: BLE001
            items.append(
                {
                    "ok": False,
                    "event_id": event_id,
                    "event_slug": event_id,
                    "error": _exception_detail(exc),
                }
            )

    error_count = sum(1 for item in items if item.get("ok") is False)
    ready_count = sum(
        1
        for item in items
        if item.get("ok") is True and (item.get("pnl_attribution") or {}).get("pnl_attribution_ready") is True
    )
    if error_count == len(items):
        status_text = "error"
    elif error_count:
        status_text = "partial"
    elif ready_count == len(items):
        status_text = "ready"
    else:
        status_text = "review_required"

    return to_jsonable(
        {
            "status": status_text,
            "source": "portfolio_order_lifecycle_pnl_attribution_v1",
            "event_count": len(items),
            "ready_event_count": ready_count,
            "error_count": error_count,
            "direct_evidence": direct_evidence_summary,
            "items": items,
        }
    )


def _build_postgame_live_evidence(
    connection: PsycopgConnection,
    *,
    event_ids: list[str],
    day: str | None,
) -> dict[str, Any]:
    if not event_ids:
        return {
            "schema_version": "postgame_live_evidence_v1",
            "status": "not_requested",
            "gate": "YELLOW",
            "reason": "no_reviewed_event_ids",
            "event_count": 0,
            "items": [],
        }

    items: list[dict[str, Any]] = []
    for event_id in event_ids:
        try:
            counts = _fetch_postgame_live_evidence_counts(connection, event_id=event_id)
            worker_summary = _read_live_worker_tick_summary(day=day, event_id=event_id)
            item = _classify_postgame_live_evidence_item(
                event_id=event_id,
                counts=counts,
                worker_summary=worker_summary,
            )
        except Exception as exc:  # noqa: BLE001
            item = {
                "event_id": event_id,
                "status": "unknown",
                "blockers": [{"reason": "postgame_live_evidence_query_failed", "error": _exception_detail(exc)}],
                "warnings": [],
            }
        items.append(item)

    if any(item.get("status") == "not_actually_live_tested" for item in items):
        status_text = "not_actually_live_tested"
        gate = "RED"
    elif any(item.get("status") == "unknown" for item in items):
        status_text = "unknown"
        gate = "YELLOW"
    else:
        status_text = "live_evidence_present"
        gate = "GREEN"
    return {
        "schema_version": "postgame_live_evidence_v1",
        "status": status_text,
        "gate": gate,
        "event_count": len(items),
        "items": items,
        "blocker_reasons": sorted(
            {
                str(blocker.get("reason"))
                for item in items
                for blocker in item.get("blockers") or []
                if blocker.get("reason")
            }
        ),
    }


def _fetch_postgame_live_evidence_counts(
    connection: PsycopgConnection,
    *,
    event_id: str,
) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                (SELECT count(*) FROM agentic.market_watch_sessions WHERE event_key = %s) AS watch_session_count,
                (SELECT count(*) FROM agentic.market_orderbook_ticks WHERE event_key = %s) AS orderbook_tick_count,
                (SELECT count(*) FROM agentic.market_trades WHERE event_key = %s) AS market_trade_count,
                (SELECT count(*) FROM agentic.strategy_decisions WHERE event_key = %s) AS strategy_decision_count,
                (
                    SELECT count(*)
                    FROM agentic.strategy_decisions
                    WHERE event_key = %s
                      AND decision_type = 'order_intent'
                ) AS order_intent_count,
                (
                    SELECT count(*)
                    FROM agentic.strategy_decisions
                    WHERE event_key = %s
                      AND decision_type = 'executed_order'
                ) AS executed_order_count,
                (
                    SELECT min(decided_at)
                    FROM agentic.strategy_decisions
                    WHERE event_key = %s
                ) AS first_strategy_decision_at,
                (
                    SELECT max(decided_at)
                    FROM agentic.strategy_decisions
                    WHERE event_key = %s
                ) AS last_strategy_decision_at,
                (
                    SELECT min(captured_at)
                    FROM agentic.market_orderbook_ticks
                    WHERE event_key = %s
                ) AS first_orderbook_tick_at,
                (
                    SELECT max(captured_at)
                    FROM agentic.market_orderbook_ticks
                    WHERE event_key = %s
                ) AS last_orderbook_tick_at,
                (SELECT count(*) FROM agentic.replay_sessions WHERE event_key = %s) AS replay_session_count;
            """,
            (
                event_id,
                event_id,
                event_id,
                event_id,
                event_id,
                event_id,
                event_id,
                event_id,
                event_id,
                event_id,
                event_id,
            ),
        )
        row = cursor.fetchone()
        columns = [description[0] for description in cursor.description]
    payload = dict(zip(columns, row or []))
    return {key: to_jsonable(value) for key, value in payload.items()}


def _classify_postgame_live_evidence_item(
    *,
    event_id: str,
    counts: dict[str, Any],
    worker_summary: dict[str, Any],
    min_orderbook_ticks: int = 10,
    min_strategy_decisions: int = 3,
) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    watch_session_count = _safe_int(counts.get("watch_session_count"))
    orderbook_tick_count = _safe_int(counts.get("orderbook_tick_count"))
    strategy_decision_count = _safe_int(counts.get("strategy_decision_count"))
    market_trade_count = _safe_int(counts.get("market_trade_count"))
    replay_session_count = _safe_int(counts.get("replay_session_count"))
    worker_tick_count = _safe_int(worker_summary.get("tick_count"))

    if watch_session_count < 1:
        blockers.append({"reason": "watch_session_missing", "watch_session_count": watch_session_count})
    if orderbook_tick_count < min_orderbook_ticks:
        blockers.append(
            {
                "reason": "insufficient_orderbook_ticks",
                "orderbook_tick_count": orderbook_tick_count,
                "minimum_expected": min_orderbook_ticks,
            }
        )
    if strategy_decision_count < min_strategy_decisions:
        blockers.append(
            {
                "reason": "insufficient_strategy_decisions",
                "strategy_decision_count": strategy_decision_count,
                "minimum_expected": min_strategy_decisions,
            }
        )
    if market_trade_count < 1:
        warnings.append({"reason": "market_trade_stream_missing", "market_trade_count": market_trade_count})
    if replay_session_count < 1:
        warnings.append({"reason": "replay_session_missing", "replay_session_count": replay_session_count})
    if worker_tick_count < 1:
        warnings.append({"reason": "live_worker_tick_evidence_missing", "worker_tick_count": worker_tick_count})

    return {
        "event_id": event_id,
        "status": "not_actually_live_tested" if blockers else "live_evidence_present",
        "blockers": blockers,
        "warnings": warnings,
        "thresholds": {
            "min_orderbook_ticks": min_orderbook_ticks,
            "min_strategy_decisions": min_strategy_decisions,
        },
        "counts": {
            "watch_session_count": watch_session_count,
            "orderbook_tick_count": orderbook_tick_count,
            "market_trade_count": market_trade_count,
            "strategy_decision_count": strategy_decision_count,
            "order_intent_count": _safe_int(counts.get("order_intent_count")),
            "executed_order_count": _safe_int(counts.get("executed_order_count")),
            "replay_session_count": replay_session_count,
            "first_strategy_decision_at": counts.get("first_strategy_decision_at"),
            "last_strategy_decision_at": counts.get("last_strategy_decision_at"),
            "first_orderbook_tick_at": counts.get("first_orderbook_tick_at"),
            "last_orderbook_tick_at": counts.get("last_orderbook_tick_at"),
        },
        "live_strategy_worker": worker_summary,
    }


def _read_live_worker_tick_summary(*, day: str | None, event_id: str) -> dict[str, Any]:
    if not day:
        return {"status": "not_checked", "reason": "session_date_missing", "tick_count": 0}
    root = ops_artifact_root(day).parent.parent / "live-strategy-worker" / day
    tick_count = 0
    latest_tick_at = None
    ticks_path = root / "ticks.jsonl"
    if ticks_path.exists():
        try:
            for line in ticks_path.read_text(encoding="utf-8").splitlines():
                try:
                    tick = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tick_event_ids = _normalized_unique_values(tick.get("event_ids") or [])
                if event_id in tick_event_ids:
                    tick_count += 1
                    latest_tick_at = tick.get("finished_at_utc") or tick.get("started_at_utc") or latest_tick_at
        except OSError:
            pass
    heartbeat = None
    heartbeat_path = root / "heartbeat.json"
    if heartbeat_path.exists():
        try:
            heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            heartbeat = None
    heartbeat_event_ids = _normalized_unique_values((heartbeat or {}).get("event_ids") or [])
    return {
        "status": "recorded" if tick_count or heartbeat_event_ids else "missing",
        "tick_count": tick_count,
        "latest_tick_at_utc": latest_tick_at,
        "heartbeat_present": heartbeat is not None,
        "heartbeat_event_match": event_id in heartbeat_event_ids,
        "heartbeat_event_ids": heartbeat_event_ids,
    }


def _exception_detail(exc: Exception) -> Any:
    if isinstance(exc, HTTPException):
        return exc.detail
    return str(exc)


def _direct_trade_token_ids_for_events(event_ids: list[str], *, day: str | None) -> list[str]:
    token_ids: list[str] = []
    seen: set[str] = set()
    for event_id in dict.fromkeys(str(item).strip() for item in event_ids):
        if not event_id:
            continue
        plan = load_current_strategy_plan(event_id, day=day)
        if not isinstance(plan, dict):
            continue
        for strategy in plan.get("active_strategies") or []:
            if not isinstance(strategy, dict):
                continue
            entry_rules = strategy.get("entry_rules") if isinstance(strategy.get("entry_rules"), dict) else {}
            token_id = str(entry_rules.get("token_id") or "").strip()
            if token_id and token_id not in seen:
                token_ids.append(token_id)
                seen.add(token_id)
        for item in plan.get("portfolio_reconciliation") or []:
            if not isinstance(item, dict):
                continue
            token_id = str(item.get("token_id") or "").strip()
            if token_id and token_id not in seen:
                token_ids.append(token_id)
                seen.add(token_id)
    return token_ids


def _resolve_llm_revision_response(
    event_id: str,
    payload: LLMRevisionAdoptionRequest,
) -> tuple[LLMRevisionResponse, dict[str, Any]]:
    if payload.response is not None:
        return payload.response, {"source": "request_body"}
    path = Path(str(payload.trace_artifact_path or "")).expanduser().resolve()
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "trace_artifact_not_readable", "path": str(path), "error": str(exc)},
        ) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"reason": "trace_artifact_invalid_json", "path": str(path), "error": str(exc)},
        ) from exc
    if not isinstance(artifact, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"reason": "trace_artifact_invalid", "path": str(path)},
        )

    artifact_event_id = str(artifact.get("event_id") or "").strip()
    if artifact_event_id and artifact_event_id != event_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"reason": "trace_event_id_mismatch", "path_event_id": event_id, "trace_event_id": artifact_event_id},
        )
    response_payload = artifact.get("response")
    if not isinstance(response_payload, dict):
        trace_payload = artifact.get("trace") if isinstance(artifact.get("trace"), dict) else {}
        response_payload = trace_payload.get("revision_response") if isinstance(trace_payload, dict) else None
    if not isinstance(response_payload, dict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"reason": "trace_response_missing", "path": str(path)},
        )
    try:
        response = LLMRevisionResponse.model_validate(response_payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"reason": "trace_response_invalid", "path": str(path), "error": str(exc)},
        ) from exc
    return response, {
        "source": "trace_artifact",
        "path": str(path),
        "trace_id": artifact.get("trace_id"),
        "trigger_types": artifact.get("trigger_types") or [],
        "selected_model": artifact.get("selected_model"),
        "persisted_at_utc": artifact.get("persisted_at_utc"),
    }


def _with_llm_adoption_metadata(
    plan_payload: dict[str, Any],
    *,
    event_id: str,
    payload: LLMRevisionAdoptionRequest,
    response: LLMRevisionResponse,
    trace_metadata: dict[str, Any],
) -> dict[str, Any]:
    plan = dict(plan_payload)
    explainability = dict(plan.get("explainability") or {})
    explainability["llm_revision_adoption"] = {
        "schema_version": "llm_revision_adoption_v1",
        "event_id": event_id,
        "adopted_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": payload.source,
        "reviewed_by": payload.reviewed_by,
        "review_reason": payload.review_reason,
        "request_id": response.request_id,
        "selected_model": response.selected_model,
        "confidence": response.confidence,
        "response_status": response.status,
        "trace_metadata": trace_metadata,
        "order_endpoint_call_allowed": False,
        "strategy_plan_auto_replace_attempted": False,
        "notes": payload.notes,
    }
    plan["explainability"] = explainability
    return plan


def _strategy_plan_diff(current_plan: dict[str, Any] | None, revised_plan: dict[str, Any]) -> dict[str, Any]:
    current = current_plan if isinstance(current_plan, dict) else {}
    before_strategies = _strategy_map(current)
    after_strategies = _strategy_map(revised_plan)
    before_ids = set(before_strategies)
    after_ids = set(after_strategies)
    changed_ids = sorted(
        strategy_id
        for strategy_id in before_ids.intersection(after_ids)
        if _canonical_json(before_strategies[strategy_id]) != _canonical_json(after_strategies[strategy_id])
    )
    return {
        "current_plan_exists": bool(current_plan),
        "market_id_changed": bool(current.get("market_id") and current.get("market_id") != revised_plan.get("market_id")),
        "before_strategy_count": len(before_strategies),
        "after_strategy_count": len(after_strategies),
        "added_strategy_ids": sorted(after_ids - before_ids),
        "removed_strategy_ids": sorted(before_ids - after_ids),
        "changed_strategy_ids": changed_ids,
        "portfolio_reconciliation_count_before": len(current.get("portfolio_reconciliation") or []),
        "portfolio_reconciliation_count_after": len(revised_plan.get("portfolio_reconciliation") or []),
    }


def _write_llm_revision_adoption_artifact(
    *,
    event_id: str,
    payload: LLMRevisionAdoptionRequest,
    response: LLMRevisionResponse,
    trace_metadata: dict[str, Any],
    plan_diff: dict[str, Any],
    revised_plan: StrategyPlan,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    event_dir = strategy_plan_root(payload.session_date) / _safe_name(event_id) / "llm_revision_adoptions"
    path = event_dir / f"adoption_{now.strftime('%Y%m%dT%H%M%SZ')}_{_safe_name(response.request_id)}.json"
    record = {
        "schema_version": "llm_revision_adoption_artifact_v1",
        "recorded_at_utc": now.isoformat(),
        "event_id": event_id,
        "session_date": payload.session_date,
        "source": payload.source,
        "reviewed_by": payload.reviewed_by,
        "review_reason": payload.review_reason,
        "request_id": response.request_id,
        "selected_model": response.selected_model,
        "response_status": response.status,
        "trace_metadata": trace_metadata,
        "plan_diff": plan_diff,
        "apply_current": payload.apply_current,
        "order_endpoint_call_allowed": False,
        "revised_strategy_plan": revised_plan.model_dump(mode="json"),
    }
    write_json(path, record)
    return {"status": "stored", "path": str(path), "recorded_at_utc": now.isoformat()}


def _strategy_map(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    strategies: dict[str, dict[str, Any]] = {}
    for item in plan.get("active_strategies") or []:
        if not isinstance(item, dict):
            continue
        strategy_id = str(item.get("strategy_id") or "").strip()
        if strategy_id:
            strategies[strategy_id] = item
    return strategies


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=to_jsonable)


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value))[:160] or "unknown"


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _timestamp_age_seconds(value: Any, *, now_utc: datetime) -> float | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    return max(0.0, (now_utc - parsed).total_seconds())


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalized_unique_values(values: list[str]) -> list[str]:
    return [value for value in dict.fromkeys(str(item).strip() for item in values) if value]
