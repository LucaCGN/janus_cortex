from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg2.extensions import connection as PsycopgConnection

from app.api.dependencies import get_db_connection
from app.modules.agentic.contracts import (
    OperatorInterventionRequest,
    OpsCycleRequest,
    PregamePlanRequest,
    ReplayFromWatchSessionRequest,
    StrategyPlan,
    StrategyPlanEvaluationRequest,
    WatchlistRequest,
)
from app.modules.agentic.engine import evaluate_strategy_plan
from app.modules.agentic.repository import (
    try_persist_operator_intervention,
    try_persist_replay_request,
    try_persist_watchlist_event,
)
from app.modules.agentic.store import (
    append_jsonl,
    build_event_agent_context,
    build_ops_status,
    load_current_strategy_plan,
    ops_artifact_root,
    record_ops_stage,
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
def run_ops_integrity_check(payload: OpsCycleRequest) -> dict[str, Any]:
    return record_ops_stage("integrity-check", payload.model_dump(mode="json"), day=payload.session_date)


@router.post("/ops/pregame-plan", status_code=status.HTTP_202_ACCEPTED)
def run_ops_pregame_plan(payload: PregamePlanRequest) -> dict[str, Any]:
    return record_ops_stage("pregame-plan", payload.model_dump(mode="json"), day=payload.session_date)


@router.post("/ops/live-monitor", status_code=status.HTTP_202_ACCEPTED)
def run_ops_live_monitor(payload: OpsCycleRequest) -> dict[str, Any]:
    return record_ops_stage("live-monitor", payload.model_dump(mode="json"), day=payload.session_date)


@router.post("/ops/postgame-review", status_code=status.HTTP_202_ACCEPTED)
def run_ops_postgame_review(payload: OpsCycleRequest) -> dict[str, Any]:
    return record_ops_stage("postgame-review", payload.model_dump(mode="json"), day=payload.session_date)


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
    return result.model_dump(mode="json")


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
        return result.model_dump(mode="json")

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
    return result.model_dump(mode="json")


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


@router.post("/replay/from-watch-session", status_code=status.HTTP_202_ACCEPTED)
def build_replay_from_watch_session(payload: ReplayFromWatchSessionRequest) -> dict[str, Any]:
    recorded = record_ops_stage("replay-from-watch-session", payload.model_dump(mode="json"))
    return {**recorded, "db_persistence": try_persist_replay_request(payload, output_root=recorded.get("path"))}


@router.post("/operator/interventions/reconcile", status_code=status.HTTP_202_ACCEPTED)
def reconcile_operator_interventions(payload: OperatorInterventionRequest) -> dict[str, Any]:
    recorded = record_ops_stage("operator-intervention-reconcile", payload.model_dump(mode="json"))
    return {**recorded, "db_persistence": try_persist_operator_intervention(payload)}


def _resolve_strategy_plan(event_id: str, payload: StrategyPlanEvaluationRequest) -> StrategyPlan:
    if payload.plan is not None:
        if payload.plan.event_id != event_id:
            raise HTTPException(status_code=422, detail="path event_id must match payload.plan.event_id")
        return payload.plan
    stored = load_current_strategy_plan(event_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="no current strategy plan for event_id")
    return StrategyPlan.model_validate(stored)
