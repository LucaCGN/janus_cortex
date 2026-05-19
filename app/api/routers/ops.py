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
    ManualClobOrderAssistantRequest,
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
from app.modules.agentic.llm_runtime import build_llm_runtime_safety_controls_status, load_latest_llm_runtime_status
from app.modules.agentic.manual_order_assistant import build_manual_clob_order_assistant_review
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

_MICROSTRUCTURE_BASE_GRID_MOVE_THRESHOLD = 0.02
_MICROSTRUCTURE_BASE_SPIKE_MOVE_THRESHOLD = 0.03
_MICROSTRUCTURE_BASE_DIRECTION_NOISE_FLOOR = 0.005


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
    llm_runtime_safety_controls = build_llm_runtime_safety_controls_status()
    recorded = record_ops_stage(
        "integrity-check",
        {
            **payload.model_dump(mode="json"),
            "ops_status": ops_status,
            "requested_event_ids": payload.event_ids,
            "resolved_event_ids": integrity_event_ids,
            "integrity": integrity,
            "llm_runtime_safety_controls": llm_runtime_safety_controls,
        },
        day=payload.session_date,
    )
    return {
        **recorded,
        "ops_status": ops_status,
        "requested_event_ids": payload.event_ids,
        "resolved_event_ids": integrity_event_ids,
        "integrity": integrity,
        "llm_runtime_safety_controls": llm_runtime_safety_controls,
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
    live_microstructure_context = _build_live_monitor_microstructure_context(
        connection,
        event_ids=live_monitor_event_ids,
    )
    current_event_inventory = _build_live_monitor_current_event_inventory(
        integrity=integrity,
        event_ids=live_monitor_event_ids,
        day=payload.session_date,
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
            "live_microstructure_context": live_microstructure_context,
            "current_event_inventory": current_event_inventory,
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
        "live_microstructure_context": live_microstructure_context,
        "current_event_inventory": current_event_inventory,
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


@router.get("/events/{event_id}/review-bundle")
def get_event_review_bundle(
    event_id: str,
    session_date: str | None = None,
    account_id: str | None = None,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    return _build_event_review_bundle(
        connection,
        event_id=event_id,
        session_date=session_date,
        account_id=account_id,
    )


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


@router.post("/events/{event_id}/manual-order-assistant", status_code=status.HTTP_202_ACCEPTED)
def review_event_manual_order_assistant(
    event_id: str,
    payload: ManualClobOrderAssistantRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    matched_outcome = _fetch_manual_order_assistant_outcome_mapping(
        connection,
        event_id=event_id,
        market_id=payload.market_id,
        outcome_id=payload.outcome_id,
        token_id=payload.token_id,
    )
    orderbook = _fetch_manual_order_assistant_orderbook(
        connection,
        event_id=event_id,
        market_id=payload.market_id,
        outcome_id=payload.outcome_id,
        token_id=payload.token_id,
    )
    inventory = _fetch_manual_order_assistant_inventory(
        connection,
        event_id=event_id,
        market_id=payload.market_id,
        outcome_id=payload.outcome_id,
        token_id=payload.token_id,
        account_id=payload.account_id,
    )
    review = build_manual_clob_order_assistant_review(
        payload,
        event_id=event_id,
        matched_outcome=matched_outcome,
        orderbook=orderbook,
        inventory=inventory,
    )
    artifact = _write_manual_order_assistant_artifact(event_id=event_id, payload=payload, review=review)
    db_persistence = try_persist_operator_intervention(
        OperatorInterventionRequest(
            account_id=payload.account_id,
            event_id=event_id,
            market_id=payload.market_id,
            action="scan" if not payload.execute else "protect",
            manual_reason=payload.reason,
            metadata={
                "source": "manual_clob_order_assistant",
                "actor": payload.actor,
                "assistant_status": review.get("status"),
                "artifact_path": artifact.get("path"),
                "order_payload": review.get("order_payload"),
                "blockers": review.get("blockers") or [],
            },
        )
    )

    executed_order = None
    if payload.execute and review.get("approved"):
        account = resolve_trading_account(connection, account_id=payload.account_id)
        order_payload = review["order_payload"]
        executed_order = create_live_order(
            connection,
            account=account,
            market_id=payload.market_id,
            outcome_id=payload.outcome_id,
            token_id=payload.token_id,
            side=payload.side,
            size=payload.size,
            price=float(order_payload.get("limit_price") or 0.0),
            order_type=payload.order_type,
            metadata_json=review["metadata"],
            dry_run=False,
            time_in_force=payload.time_in_force,
        )
    return {
        **review,
        "artifact": artifact,
        "db_persistence": db_persistence,
        "executed_order": executed_order,
        "raw_exchange_order_allowed": False,
    }


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
        if _llm_revision_actions_are_conservative(response.reconciliation_actions):
            adoption_record = _write_llm_conservative_action_adoption_artifact(
                event_id=event_id,
                payload=payload,
                response=response,
                trace_metadata=trace_metadata,
            )
            return {
                "status": "conservative_actions_recorded",
                "event_id": event_id,
                "session_date": payload.session_date,
                "reviewed_by": payload.reviewed_by,
                "review_reason": payload.review_reason,
                "request_id": response.request_id,
                "selected_model": response.selected_model,
                "apply_current": False,
                "order_endpoint_call_allowed": False,
                "conservative_action_count": len(response.reconciliation_actions),
                "adoption_artifact": adoption_record,
                "post_adoption_proof": {
                    "schema_version": "llm_conservative_action_post_adoption_proof_v1",
                    "plan_version_changed": False,
                    "raw_order_placed": False,
                    "recorded_for_worker_or_operator_review": True,
                },
            }
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"reason": "revised_strategy_plan_or_conservative_actions_required"},
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


def _fetch_manual_order_assistant_outcome_mapping(
    connection: PsycopgConnection,
    *,
    event_id: str,
    market_id: str,
    outcome_id: str,
    token_id: str,
) -> dict[str, Any] | None:
    queries = [
        (
            """
            SELECT event_key AS event_id, market_id, outcome_id, token_id, label, side, metadata_json
            FROM agentic.market_outcomes
            WHERE event_key = %s
              AND market_id = %s
              AND (outcome_id = %s OR token_id = %s)
            LIMIT 1;
            """,
            (event_id, market_id, outcome_id, token_id),
        ),
        (
            """
            SELECT e.event_id::text AS event_id, m.market_id::text AS market_id, o.outcome_id::text AS outcome_id,
                   o.token_id, o.outcome_label AS label, NULL AS side, o.metadata_json
            FROM catalog.outcomes o
            JOIN catalog.markets m ON m.market_id = o.market_id
            JOIN catalog.events e ON e.event_id = m.event_id
            WHERE m.market_id = %s
              AND (o.outcome_id = %s OR o.token_id = %s)
            LIMIT 1;
            """,
            (market_id, outcome_id, token_id),
        ),
    ]
    for sql, params in queries:
        try:
            rows = _fetch_event_review_rows(connection, sql, params)
        except Exception:  # noqa: BLE001
            continue
        if rows:
            return rows[0]
    return None


def _fetch_manual_order_assistant_orderbook(
    connection: PsycopgConnection,
    *,
    event_id: str,
    market_id: str,
    outcome_id: str,
    token_id: str,
) -> dict[str, Any]:
    try:
        rows = _fetch_event_review_rows(
            connection,
            """
            SELECT event_key AS event_id, market_id, outcome_id, token_id,
                   captured_at AS captured_at_utc, source_timestamp AS source_timestamp_utc,
                   best_bid, best_ask, spread, mid_price, bid_depth, ask_depth,
                   source_latency_ms, ingest_latency_ms, levels_json, raw_json
            FROM agentic.market_orderbook_ticks
            WHERE event_key = %s
              AND market_id = %s
              AND (outcome_id = %s OR token_id = %s)
            ORDER BY captured_at DESC
            LIMIT 1;
            """,
            (event_id, market_id, outcome_id, token_id),
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "query_failed", "error": _exception_detail(exc), "market_id": market_id, "token_id": token_id}
    if not rows:
        return {"status": "missing", "event_id": event_id, "market_id": market_id, "outcome_id": outcome_id, "token_id": token_id}
    row = rows[0]
    spread = _safe_float(row.get("spread"))
    if spread is not None:
        row["spread_cents"] = round(spread * 100.0, 6) if spread <= 1.0 else round(spread, 6)
    row["status"] = "recorded"
    return row


def _fetch_manual_order_assistant_inventory(
    connection: PsycopgConnection,
    *,
    event_id: str,
    market_id: str,
    outcome_id: str,
    token_id: str,
    account_id: str | None,
) -> dict[str, Any]:
    account_filter = "AND o.account_id = %s" if account_id else ""
    account_params: tuple[Any, ...] = (account_id,) if account_id else ()
    try:
        open_orders = _fetch_event_review_rows(
            connection,
            f"""
            SELECT o.order_id::text AS order_id, o.account_id::text AS account_id, o.market_id::text AS market_id,
                   o.outcome_id::text AS outcome_id, oc.token_id, o.side, o.order_type, o.limit_price, o.size,
                   o.status, o.external_order_id, o.metadata_json, o.updated_at
            FROM portfolio.orders o
            LEFT JOIN catalog.outcomes oc ON oc.outcome_id = o.outcome_id
            WHERE o.market_id = %s
              AND (o.outcome_id = %s OR oc.token_id = %s)
              AND lower(o.status) IN ('open','submitted','working','pending','partially_filled','partial','pending_submit')
              {account_filter}
            ORDER BY o.updated_at DESC
            LIMIT 50;
            """,
            (market_id, outcome_id, token_id, *account_params),
        )
    except Exception:  # noqa: BLE001
        open_orders = []
    try:
        pending_intents = _fetch_event_review_rows(
            connection,
            """
            SELECT strategy_decision_id::text AS strategy_decision_id, decided_at, strategy_id,
                   decision_type, order_intent_json, raw_json
            FROM agentic.strategy_decisions
            WHERE event_key = %s
              AND decision_type IN ('order_intent', 'pending_intent')
            ORDER BY decided_at DESC
            LIMIT 50;
            """,
            (event_id,),
        )
    except Exception:  # noqa: BLE001
        pending_intents = []
    normalized_pending = [_normalize_pending_intent(row) for row in pending_intents]
    unresolved = bool(open_orders or normalized_pending)
    return {
        "schema_version": "manual_order_assistant_inventory_v1",
        "event_id": event_id,
        "account_id": account_id,
        "open_order_count": len(open_orders),
        "pending_intent_count": len(normalized_pending),
        "unresolved_inventory_present": unresolved,
        "open_orders": open_orders,
        "pending_intents": normalized_pending,
        "stale_mirror_rows": [],
    }


def _normalize_pending_intent(row: dict[str, Any]) -> dict[str, Any]:
    intent = row.get("order_intent_json") if isinstance(row.get("order_intent_json"), dict) else {}
    return {
        "strategy_decision_id": row.get("strategy_decision_id"),
        "decided_at": row.get("decided_at"),
        "strategy_id": row.get("strategy_id") or intent.get("strategy_id"),
        "outcome_id": intent.get("outcome_id"),
        "token_id": intent.get("token_id"),
        "side": intent.get("side"),
        "status": "pending",
        "raw": row,
    }


def _write_manual_order_assistant_artifact(
    *,
    event_id: str,
    payload: ManualClobOrderAssistantRequest,
    review: dict[str, Any],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    path = (
        ops_artifact_root(payload.session_date)
        / "manual-order-assistant"
        / _safe_name(event_id)
        / f"review_{now.strftime('%Y%m%dT%H%M%SZ')}_{_safe_name(payload.idempotency_key or payload.actor)}.json"
    )
    record = {
        "schema_version": "manual_clob_order_assistant_artifact_v1",
        "recorded_at_utc": now.isoformat(),
        "event_id": event_id,
        "request": payload.model_dump(mode="json"),
        "review": review,
    }
    write_json(path, record)
    return {"status": "stored", "path": str(path), "recorded_at_utc": now.isoformat()}


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


def _build_event_review_bundle(
    connection: PsycopgConnection,
    *,
    event_id: str,
    session_date: str | None,
    account_id: str | None,
) -> dict[str, Any]:
    resolved_day = session_date
    agent_context = build_event_agent_context(event_id, day=resolved_day)
    strategy_plan_versions = _load_event_strategy_plan_versions(event_id, day=resolved_day)
    runtime_evidence = _build_event_review_runtime_evidence(connection, event_id=event_id)
    llm_runtime_status = load_latest_llm_runtime_status(
        session_date=resolved_day,
        event_ids=[event_id],
    )
    postgame_live_evidence = _build_postgame_live_evidence(
        connection,
        event_ids=[event_id],
        day=resolved_day,
    )
    payload = OpsCycleRequest(
        session_date=resolved_day,
        event_ids=[event_id],
        account_id=account_id,
        source="event-review-bundle",
    )
    portfolio_pnl_attribution = _build_postgame_portfolio_pnl_attribution(
        connection,
        payload,
        event_ids=[event_id],
    )
    decision_timeline = _build_event_review_decision_timeline(
        event_id=event_id,
        agent_context=agent_context,
        strategy_plan_versions=strategy_plan_versions,
        runtime_evidence=runtime_evidence,
        llm_runtime_status=llm_runtime_status,
    )
    actor_attribution = _build_event_review_actor_attribution(portfolio_pnl_attribution)
    token_cost_timeline = _build_event_review_token_cost_timeline(llm_runtime_status)
    market_microstructure = _build_event_review_microstructure_summary(runtime_evidence)
    missed_opportunities = _build_event_review_missed_opportunity_candidates(
        runtime_evidence=runtime_evidence,
        decision_timeline=decision_timeline,
        market_microstructure=market_microstructure,
    )
    timeline_slices = _build_event_review_timeline_slices(decision_timeline)
    known_gaps = _event_review_known_gaps(
        account_id=account_id,
        runtime_evidence=runtime_evidence,
        postgame_live_evidence=postgame_live_evidence,
        portfolio_pnl_attribution=portfolio_pnl_attribution,
    )
    status_text = "ready" if not known_gaps else "review_required"
    return to_jsonable(
        {
            "schema_version": "event_review_bundle_v1",
            "status": status_text,
            "event_id": event_id,
            "session_date": resolved_day,
            "generated_at_utc": datetime.now(timezone.utc),
            "authority_order": [
                "direct_clob_truth",
                "janus_db_api",
                "runtime_artifacts",
                "runtime_handoffs",
                "runtime_reports",
                "tracked_docs",
            ],
            "agent_context": agent_context,
            "strategy_plan_versions": strategy_plan_versions,
            "runtime_evidence": runtime_evidence,
            "llm_runtime_status": llm_runtime_status,
            "postgame_live_evidence": postgame_live_evidence,
            "portfolio_pnl_attribution": portfolio_pnl_attribution,
            "actor_attribution": actor_attribution,
            "token_cost_timeline": token_cost_timeline,
            "market_microstructure": market_microstructure,
            "missed_opportunities": missed_opportunities,
            "decision_timeline": decision_timeline,
            "timeline_slices": timeline_slices,
            "postgame_tooling_status": {
                "schema_version": "postgame_tooling_status_v1",
                "screenshot_dependency": False,
                "grep_dependency": False,
                "bundle_endpoint": "/v1/events/{event_id}/review-bundle",
            },
            "known_gaps": known_gaps,
        }
    )


def _load_event_strategy_plan_versions(event_id: str, *, day: str | None) -> dict[str, Any]:
    root = strategy_plan_root(day) / _safe_name(event_id)
    current_path = root / "current.json"
    versions_root = root / "versions"
    versions: list[dict[str, Any]] = []
    if versions_root.exists():
        for path in sorted(versions_root.glob("*.json")):
            payload = _read_json_file(path)
            if isinstance(payload, dict):
                versions.append(_strategy_plan_version_summary(payload, path=path))
    current = _read_json_file(current_path)
    return {
        "schema_version": "event_strategy_plan_versions_v1",
        "event_id": event_id,
        "current_path": str(current_path),
        "current_exists": isinstance(current, dict),
        "current": _strategy_plan_version_summary(current, path=current_path) if isinstance(current, dict) else None,
        "version_count": len(versions),
        "versions": versions,
    }


def _strategy_plan_version_summary(plan: dict[str, Any], *, path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "event_id": plan.get("event_id"),
        "market_id": plan.get("market_id"),
        "plan_owner": plan.get("plan_owner"),
        "generated_at_utc": plan.get("generated_at_utc"),
        "valid_until_utc": plan.get("valid_until_utc"),
        "active_strategy_count": len(plan.get("active_strategies") or []),
        "strategy_ids": [
            str(item.get("strategy_id"))
            for item in plan.get("active_strategies") or []
            if isinstance(item, dict) and item.get("strategy_id")
        ],
        "revision_trigger_count": len(plan.get("trigger_conditions") or []),
        "explainability_keys": sorted((plan.get("explainability") or {}).keys())
        if isinstance(plan.get("explainability"), dict)
        else [],
    }


def _build_event_review_runtime_evidence(
    connection: PsycopgConnection,
    *,
    event_id: str,
) -> dict[str, Any]:
    queries = {
        "market_event": (
            """
            SELECT event_key, category, provider, title, status, start_time, end_time, liquidity, metadata_json
            FROM agentic.market_events
            WHERE event_key = %s
            LIMIT 1;
            """,
            (event_id,),
        ),
        "market_outcomes": (
            """
            SELECT market_id, outcome_id, token_id, label, side, metadata_json
            FROM agentic.market_outcomes
            WHERE event_key = %s
            ORDER BY label
            LIMIT 40;
            """,
            (event_id,),
        ),
        "watch_sessions": (
            """
            SELECT watch_session_id::text AS watch_session_id, category, started_at, ended_at, cadence_ms,
                   passive_only, reason, gap_summary_json, provider_errors_json, metadata_json
            FROM agentic.market_watch_sessions
            WHERE event_key = %s
            ORDER BY started_at DESC
            LIMIT 20;
            """,
            (event_id,),
        ),
        "orderbook_ticks": (
            """
            SELECT market_orderbook_tick_id::text AS market_orderbook_tick_id, market_id, outcome_id, token_id,
                   captured_at, source_timestamp, best_bid, best_ask, spread, mid_price, bid_depth, ask_depth,
                   source_latency_ms, ingest_latency_ms, levels_json, raw_json
            FROM agentic.market_orderbook_ticks
            WHERE event_key = %s
            ORDER BY captured_at DESC
            LIMIT 120;
            """,
            (event_id,),
        ),
        "play_by_play_context": (
            """
            WITH event_identity AS (
                SELECT
                    %s::text AS event_key,
                    (
                        SELECT market_event_id::text
                        FROM agentic.market_events
                        WHERE event_key = %s
                        LIMIT 1
                    ) AS agentic_market_event_id
            )
            SELECT *
            FROM (
                SELECT
                    'nba' AS league,
                    p.game_id,
                    p.event_index,
                    p.action_id,
                    NULL::bigint AS action_number,
                    NULL::timestamptz AS time_actual,
                    p.period,
                    p.clock,
                    NULL::text AS action_type,
                    NULL::text AS sub_type,
                    p.description,
                    p.home_score,
                    p.away_score,
                    p.is_score_change,
                    p.payload_json
                FROM event_identity e
                JOIN nba.nba_game_event_links l ON l.event_id::text = e.event_key
                JOIN nba.nba_play_by_play p ON p.game_id = l.game_id

                UNION ALL

                SELECT
                    'wnba' AS league,
                    p.game_id,
                    p.event_index,
                    p.action_id,
                    p.action_number,
                    p.time_actual,
                    p.period,
                    p.clock,
                    p.action_type,
                    p.sub_type,
                    p.description,
                    p.home_score,
                    p.away_score,
                    p.is_score_change,
                    p.raw_payload_json AS payload_json
                FROM event_identity e
                JOIN wnba.wnba_game_event_links l ON (
                    l.agentic_event_key = e.event_key
                    OR l.agentic_market_event_id::text = e.agentic_market_event_id
                    OR l.catalog_event_id::text = e.event_key
                )
                JOIN wnba.wnba_play_by_play p ON p.game_id = l.game_id
            ) rows
            ORDER BY game_id, event_index DESC
            LIMIT 160;
            """,
            (event_id, event_id),
        ),
        "market_trades": (
            """
            SELECT market_trade_id::text AS market_trade_id, market_id, outcome_id, token_id, external_trade_id,
                   trade_time, observed_at, side, price, size, source_latency_ms, raw_json
            FROM agentic.market_trades
            WHERE event_key = %s
            ORDER BY trade_time DESC
            LIMIT 120;
            """,
            (event_id,),
        ),
        "strategy_decisions": (
            """
            SELECT strategy_decision_id::text AS strategy_decision_id,
                   strategy_plan_version_id::text AS strategy_plan_version_id,
                   decided_at, strategy_id, decision_type, order_intent_json, blockers_json, fill_json,
                   exit_json, hedge_json, raw_json
            FROM agentic.strategy_decisions
            WHERE event_key = %s
            ORDER BY decided_at ASC
            LIMIT 240;
            """,
            (event_id,),
        ),
        "operator_interventions": (
            """
            SELECT operator_intervention_id::text AS operator_intervention_id, market_id, account_id,
                   detected_at, action, external_order_ids_json, reconciliation_action, status, notes, raw_json
            FROM agentic.operator_interventions
            WHERE event_key = %s
            ORDER BY detected_at ASC
            LIMIT 120;
            """,
            (event_id,),
        ),
        "replay_sessions": (
            """
            SELECT replay_session_id::text AS replay_session_id, watch_session_id::text AS watch_session_id,
                   output_name, created_at, source_tick_count, source_trade_count, latency_summary_json,
                   replay_config_json, output_root
            FROM agentic.replay_sessions
            WHERE event_key = %s
            ORDER BY created_at DESC
            LIMIT 20;
            """,
            (event_id,),
        ),
    }
    sections: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    for name, (sql, params) in queries.items():
        try:
            rows = _fetch_event_review_rows(connection, sql, params)
            sections[name] = rows[0] if name == "market_event" and rows else rows
        except Exception as exc:  # noqa: BLE001
            sections[name] = None if name == "market_event" else []
            errors.append({"section": name, "error": _exception_detail(exc)})

    orderbook_ticks = sections.get("orderbook_ticks") if isinstance(sections.get("orderbook_ticks"), list) else []
    return {
        "schema_version": "event_review_runtime_evidence_v1",
        "event_id": event_id,
        "status": "partial" if errors else "ready",
        "errors": errors,
        **sections,
        "orderbook_window_summary": _orderbook_window_summary(orderbook_ticks),
    }


def _build_live_monitor_microstructure_context(
    connection: PsycopgConnection,
    *,
    event_ids: list[str],
) -> dict[str, Any]:
    unique_event_ids = _normalized_unique_values(event_ids)
    items: list[dict[str, Any]] = []
    for event_id in unique_event_ids:
        runtime_evidence = _build_event_review_runtime_evidence(connection, event_id=event_id)
        summary = _build_event_review_microstructure_summary(runtime_evidence)
        tick_count = _safe_int(summary.get("tick_count"))
        items.append(
            {
                "schema_version": "live_monitor_event_microstructure_context_v1",
                "event_id": event_id,
                "status": "recorded" if tick_count > 0 else "not_recorded",
                "runtime_evidence_status": runtime_evidence.get("status"),
                "runtime_evidence_error_count": len(runtime_evidence.get("errors") or []),
                "tick_count": tick_count,
                "outcome_count": _safe_int(summary.get("outcome_count")),
                "trend_profile": summary.get("trend_profile"),
                "mid_price_range": summary.get("mid_price_range"),
                "spike_count": _safe_int(summary.get("spike_count")),
                "oscillation_band_count": _safe_int(summary.get("oscillation_band_count")),
                "grid_opportunity_count": _safe_int(summary.get("grid_opportunity_count")),
                "favorite_underdog_inversion_count": _safe_int(
                    summary.get("favorite_underdog_inversion_count")
                ),
                "period_context_status": summary.get("period_context_status"),
                "period_summary_count": _safe_int(summary.get("period_summary_count")),
                "period_summaries": _compact_microstructure_period_summaries(summary),
                "threshold_calibration": summary.get("threshold_calibration") or {},
                "orderbook_window_summary": summary.get("orderbook_window_summary") or {},
                "screenshot_dependency": False,
                "trading_authority": "review_evidence_only",
                "requires_replay_fillability_before_trading_authority": True,
            }
        )
    recorded_count = sum(1 for item in items if item.get("status") == "recorded")
    return {
        "schema_version": "live_monitor_microstructure_context_v1",
        "status": "not_required" if not unique_event_ids else ("recorded" if recorded_count else "not_recorded"),
        "event_count": len(unique_event_ids),
        "recorded_event_count": recorded_count,
        "screenshot_dependency": False,
        "items": items,
    }


def _compact_microstructure_period_summaries(summary: dict[str, Any]) -> list[dict[str, Any]]:
    period_summaries = summary.get("period_summaries") if isinstance(summary.get("period_summaries"), dict) else {}
    items: list[dict[str, Any]] = []
    for period_key, period_summary in sorted(
        period_summaries.items(),
        key=lambda item: _microstructure_period_sort_key(str(item[0])),
    ):
        if not isinstance(period_summary, dict):
            continue
        items.append(
            {
                "period_key": str(period_key),
                "period": period_summary.get("period"),
                "status": period_summary.get("status"),
                "tick_count": _safe_int(period_summary.get("tick_count")),
                "first_clock": period_summary.get("first_clock"),
                "last_clock": period_summary.get("last_clock"),
                "trend_profile": period_summary.get("trend_profile"),
                "mid_price_range": period_summary.get("mid_price_range"),
                "grid_opportunity_count": _safe_int(period_summary.get("grid_opportunity_count")),
                "spike_count": _safe_int(period_summary.get("spike_count")),
                "oscillation_band_count": _safe_int(period_summary.get("oscillation_band_count")),
                "threshold_calibration": period_summary.get("threshold_calibration") or {},
            }
        )
    return items


def _fetch_event_review_rows(
    connection: PsycopgConnection,
    sql: str,
    params: tuple[Any, ...],
) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
    return [to_jsonable(dict(zip(columns, row))) for row in rows]


def _orderbook_window_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    timestamps = [_parse_datetime(row.get("captured_at")) for row in rows]
    present_timestamps = [item for item in timestamps if item is not None]
    spreads = [_safe_float(row.get("spread")) for row in rows]
    mids = [_safe_float(row.get("mid_price")) for row in rows]
    present_spreads = [item for item in spreads if item is not None]
    present_mids = [item for item in mids if item is not None]
    outcome_ids = sorted({str(row.get("outcome_id")) for row in rows if row.get("outcome_id")})
    return {
        "schema_version": "orderbook_window_summary_v1",
        "tick_count": len(rows),
        "outcome_ids": outcome_ids,
        "first_captured_at": min(present_timestamps).isoformat() if present_timestamps else None,
        "last_captured_at": max(present_timestamps).isoformat() if present_timestamps else None,
        "min_spread": min(present_spreads) if present_spreads else None,
        "max_spread": max(present_spreads) if present_spreads else None,
        "min_mid_price": min(present_mids) if present_mids else None,
        "max_mid_price": max(present_mids) if present_mids else None,
    }


def _build_event_review_decision_timeline(
    *,
    event_id: str,
    agent_context: dict[str, Any],
    strategy_plan_versions: dict[str, Any],
    runtime_evidence: dict[str, Any],
    llm_runtime_status: dict[str, Any],
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    current_plan = strategy_plan_versions.get("current") if isinstance(strategy_plan_versions.get("current"), dict) else None
    if current_plan:
        entries.append(
            _timeline_entry(
                timestamp=current_plan.get("generated_at_utc"),
                source="strategy_plan",
                kind="current_strategy_plan",
                summary=f"Current StrategyPlanJSON with {current_plan.get('active_strategy_count')} active strategies.",
                payload=current_plan,
            )
        )
    for version in strategy_plan_versions.get("versions") or []:
        if isinstance(version, dict):
            entries.append(
                _timeline_entry(
                    timestamp=version.get("generated_at_utc"),
                    source="strategy_plan",
                    kind="strategy_plan_version",
                    summary=f"StrategyPlanJSON version with {version.get('active_strategy_count')} active strategies.",
                    payload=version,
                )
            )
    for decision in runtime_evidence.get("strategy_decisions") or []:
        if isinstance(decision, dict):
            decision_type = str(decision.get("decision_type") or "strategy_decision")
            entries.append(
                _timeline_entry(
                    timestamp=decision.get("decided_at"),
                    source="janus_engine",
                    kind=decision_type,
                    summary=f"{decision_type} for strategy {decision.get('strategy_id') or 'unknown'}.",
                    payload=decision,
                )
            )
    for intervention in runtime_evidence.get("operator_interventions") or []:
        if isinstance(intervention, dict):
            entries.append(
                _timeline_entry(
                    timestamp=intervention.get("detected_at"),
                    source="operator",
                    kind="operator_intervention",
                    summary=f"Operator action {intervention.get('action') or 'unknown'} status {intervention.get('status') or 'unknown'}.",
                    payload=intervention,
                )
            )
    for trade in runtime_evidence.get("market_trades") or []:
        if isinstance(trade, dict):
            entries.append(
                _timeline_entry(
                    timestamp=trade.get("trade_time"),
                    source="market_trade_stream",
                    kind="market_trade",
                    summary=f"Observed {trade.get('side') or 'trade'} {trade.get('size')} @ {trade.get('price')}.",
                    payload=trade,
                )
            )
    for item in llm_runtime_status.get("items") or []:
        if isinstance(item, dict) and item.get("status") != "not_recorded":
            entries.append(
                _timeline_entry(
                    timestamp=item.get("persisted_at_utc"),
                    source="llm_runtime",
                    kind="llm_runtime_trace",
                    summary=f"LLM runtime {item.get('response_status') or 'recorded'} using {item.get('selected_model') or 'unknown model'}.",
                    payload=item,
                )
            )
    orderbook_window = runtime_evidence.get("orderbook_window_summary") or {}
    if orderbook_window.get("tick_count"):
        entries.append(
            _timeline_entry(
                timestamp=orderbook_window.get("first_captured_at"),
                source="clob_orderbook",
                kind="orderbook_window_start",
                summary=f"Orderbook capture window started with {orderbook_window.get('tick_count')} sampled ticks in bundle.",
                payload=orderbook_window,
            )
        )
        entries.append(
            _timeline_entry(
                timestamp=orderbook_window.get("last_captured_at"),
                source="clob_orderbook",
                kind="orderbook_window_end",
                summary="Orderbook capture window ended for sampled bundle evidence.",
                payload=orderbook_window,
            )
        )

    entries = sorted(entries, key=lambda item: (_parse_datetime(item.get("timestamp_utc")) is None, item.get("timestamp_utc") or ""))
    counts: dict[str, int] = {}
    for entry in entries:
        kind = str(entry.get("kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return {
        "schema_version": "event_decision_timeline_v1",
        "event_id": event_id,
        "resolved_strategy_plan_event_id": agent_context.get("resolved_strategy_plan_event_id"),
        "entry_count": len(entries),
        "kind_counts": dict(sorted(counts.items())),
        "entries": entries,
    }


def _build_event_review_actor_attribution(portfolio_pnl_attribution: dict[str, Any]) -> dict[str, Any]:
    actor_summary: dict[str, Any] = {}
    items = portfolio_pnl_attribution.get("items") if isinstance(portfolio_pnl_attribution.get("items"), list) else []
    for item in items:
        pnl = item.get("pnl_attribution") if isinstance(item, dict) else None
        if not isinstance(pnl, dict):
            continue
        reconciliation = pnl.get("reconciliation") if isinstance(pnl.get("reconciliation"), dict) else {}
        for actor, summary in (reconciliation.get("actor_summary") or {}).items():
            if isinstance(summary, dict):
                actor_summary[str(actor)] = summary
    return {
        "schema_version": "event_actor_attribution_v1",
        "status": "recorded" if actor_summary else "not_recorded",
        "actor_count": len(actor_summary),
        "actors": actor_summary,
        "separates_autonomous_codex_manual": True,
    }


def _build_event_review_token_cost_timeline(llm_runtime_status: dict[str, Any]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    total_estimated_cost = 0.0
    total_tokens = 0
    for item in llm_runtime_status.get("items") or []:
        if not isinstance(item, dict):
            continue
        usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
        input_tokens = _safe_int(usage.get("input_tokens"))
        output_tokens = _safe_int(usage.get("output_tokens"))
        total_tokens += input_tokens + output_tokens
        estimated_cost = _safe_float(item.get("estimated_cost_usd") or usage.get("estimated_cost_usd")) or 0.0
        total_estimated_cost += estimated_cost
        entries.append(
            {
                "timestamp_utc": item.get("persisted_at_utc"),
                "trace_id": item.get("trace_id"),
                "selected_model": item.get("selected_model"),
                "selected_tier": item.get("selected_tier"),
                "trigger_types": item.get("trigger_types") or [],
                "response_status": item.get("response_status"),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated_cost_usd": round(estimated_cost, 6),
                "adoption_status": item.get("adoption_status"),
            }
        )
    return {
        "schema_version": "event_token_cost_timeline_v1",
        "status": "recorded" if entries else "not_recorded",
        "entry_count": len(entries),
        "total_tokens": total_tokens,
        "total_estimated_cost_usd": round(total_estimated_cost, 6),
        "entries": entries,
    }


def _build_event_review_microstructure_summary(runtime_evidence: dict[str, Any]) -> dict[str, Any]:
    ticks = runtime_evidence.get("orderbook_ticks") if isinstance(runtime_evidence.get("orderbook_ticks"), list) else []
    play_by_play_rows = (
        runtime_evidence.get("play_by_play_context")
        if isinstance(runtime_evidence.get("play_by_play_context"), list)
        else []
    )
    mids = [_safe_float(row.get("mid_price")) for row in ticks if isinstance(row, dict)]
    mids = [item for item in mids if item is not None]
    prices_by_outcome: dict[str, list[dict[str, Any]]] = {}
    pbp_aligned_tick_count = 0
    for row in ticks:
        if not isinstance(row, dict):
            continue
        outcome_id = str(row.get("outcome_id") or row.get("token_id") or "unknown")
        value = _safe_float(row.get("mid_price"))
        if value is None:
            continue
        context = _microstructure_tick_context(row, play_by_play_rows=play_by_play_rows)
        if str(context.get("context_source") or "").startswith("play_by_play_context"):
            pbp_aligned_tick_count += 1
        prices_by_outcome.setdefault(outcome_id, []).append(
            {
                "captured_at": row.get("captured_at") or row.get("captured_at_utc") or row.get("source_timestamp"),
                "mid_price": value,
                "spread": _safe_float(row.get("spread")),
                **context,
            }
        )
    outcome_summaries = {
        outcome_id: _microstructure_outcome_summary(outcome_id, rows)
        for outcome_id, rows in sorted(prices_by_outcome.items())
    }
    period_summaries = _microstructure_period_summaries(prices_by_outcome)
    threshold_calibration = _microstructure_threshold_calibration(
        [row for rows in prices_by_outcome.values() for row in rows]
    )
    inversion_count = _paired_price_inversion_point_count(prices_by_outcome)
    leader_inversion_count = _favorite_underdog_leader_inversion_count(prices_by_outcome)
    spike_count = sum(_safe_int(summary.get("spike_count")) for summary in outcome_summaries.values())
    oscillation_band_count = sum(_safe_int(summary.get("oscillation_band_count")) for summary in outcome_summaries.values())
    grid_opportunity_count = sum(_safe_int(summary.get("grid_opportunity_count")) for summary in outcome_summaries.values())
    smoothness_values = [
        _safe_float(summary.get("trend_smoothness_score"))
        for summary in outcome_summaries.values()
        if summary.get("trend_smoothness_score") is not None
    ]
    avg_smoothness = sum(smoothness_values) / len(smoothness_values) if smoothness_values else None
    return {
        "schema_version": "event_market_microstructure_summary_v1",
        "tick_count": len(ticks),
        "outcome_count": len(prices_by_outcome),
        "min_mid_price": min(mids) if mids else None,
        "max_mid_price": max(mids) if mids else None,
        "mid_price_range": round(max(mids) - min(mids), 6) if mids else None,
        "price_inversion_point_count": inversion_count,
        "favorite_underdog_inversion_count": leader_inversion_count,
        "spike_count": spike_count,
        "oscillation_band_count": oscillation_band_count,
        "grid_opportunity_count": grid_opportunity_count,
        "trend_smoothness_score": round(avg_smoothness, 6) if avg_smoothness is not None else None,
        "trend_profile": _microstructure_trend_profile(
            spike_count=spike_count,
            oscillation_band_count=oscillation_band_count,
            trend_smoothness_score=avg_smoothness,
            mid_price_range=round(max(mids) - min(mids), 6) if mids else None,
        ),
        "threshold_calibration": threshold_calibration,
        "threshold_calibration_status": threshold_calibration["calibration_status"],
        "trading_authority_status": "review_only_thresholds_pending_backtest",
        "outcome_summaries": outcome_summaries,
        "period_context_status": "recorded"
        if any(str(key).startswith("period_") for key in period_summaries)
        else "not_recorded",
        "period_summary_count": len(period_summaries),
        "period_summaries": period_summaries,
        "play_by_play_context_status": "recorded" if play_by_play_rows else "not_recorded",
        "play_by_play_event_count": len(play_by_play_rows),
        "pbp_aligned_tick_count": pbp_aligned_tick_count,
        "pbp_alignment_status": "recorded"
        if pbp_aligned_tick_count
        else ("available_without_timestamp_match" if play_by_play_rows else "not_recorded"),
        "orderbook_window_summary": runtime_evidence.get("orderbook_window_summary") or {},
        "metric_definitions": {
            "price_inversion_point_count": "paired sampled ticks where one outcome is below 50c and the other is above 50c",
            "favorite_underdog_inversion_count": "leader changes between paired sampled binary outcomes",
            "oscillation_band_count": "direction changes in chronological mid-price movement",
            "grid_opportunity_count": "adjacent sampled mid-price moves of at least 2c",
            "spike_count": "adjacent sampled mid-price moves of at least 3c",
            "trend_smoothness_score": "absolute net move divided by total absolute move; lower means more jagged",
            "period_summaries": "same metrics grouped by period/clock context from tick evidence or nearest persisted play-by-play rows",
            "pbp_aligned_tick_count": "ticks whose period/clock context was filled from nearest persisted play-by-play evidence",
            "threshold_calibration": "effective grid, spike, and direction-noise thresholds after observed spread calibration",
        },
    }


def _microstructure_period_summaries(prices_by_outcome: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for outcome_id, rows in prices_by_outcome.items():
        for row in rows:
            period = _safe_int(row.get("period"))
            key = f"period_{period}" if period is not None and period > 0 else "unclassified_period"
            grouped.setdefault(key, []).append({**row, "outcome_id": outcome_id})
    return {
        key: _microstructure_period_summary(key, rows)
        for key, rows in sorted(grouped.items(), key=lambda item: _microstructure_period_sort_key(item[0]))
    }


def _microstructure_period_sort_key(key: str) -> tuple[int, str]:
    if key.startswith("period_"):
        try:
            return (int(key.removeprefix("period_")), key)
        except ValueError:
            return (999, key)
    return (1000, key)


def _microstructure_period_summary(key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = _chronological_microstructure_rows(rows)
    threshold_calibration = _microstructure_threshold_calibration(ordered)
    prices = [_safe_float(row.get("mid_price")) for row in ordered]
    prices = [price for price in prices if price is not None]
    summaries: dict[str, dict[str, Any]] = {}
    rows_by_outcome: dict[str, list[dict[str, Any]]] = {}
    for row in ordered:
        rows_by_outcome.setdefault(str(row.get("outcome_id") or "unknown"), []).append(row)
    for outcome_id, outcome_rows in sorted(rows_by_outcome.items()):
        summaries[outcome_id] = _microstructure_outcome_summary(outcome_id, outcome_rows)
    spike_count = sum(_safe_int(summary.get("spike_count")) for summary in summaries.values())
    oscillation_band_count = sum(_safe_int(summary.get("oscillation_band_count")) for summary in summaries.values())
    grid_opportunity_count = sum(_safe_int(summary.get("grid_opportunity_count")) for summary in summaries.values())
    smoothness_values = [
        _safe_float(summary.get("trend_smoothness_score"))
        for summary in summaries.values()
        if summary.get("trend_smoothness_score") is not None
    ]
    avg_smoothness = sum(smoothness_values) / len(smoothness_values) if smoothness_values else None
    clocks = [str(row.get("clock")) for row in ordered if row.get("clock")]
    clock_seconds_values = [
        _safe_float(row.get("clock_seconds_remaining"))
        for row in ordered
        if row.get("clock_seconds_remaining") is not None
    ]
    clock_seconds_values = [value for value in clock_seconds_values if value is not None]
    sources = sorted({str(row.get("context_source")) for row in ordered if row.get("context_source")})
    period = None
    for row in ordered:
        parsed_period = _safe_int(row.get("period"))
        if parsed_period > 0:
            period = parsed_period
            break
    return {
        "schema_version": "event_market_microstructure_period_summary_v1",
        "status": "recorded" if prices else "not_recorded",
        "period": period,
        "tick_count": len(rows),
        "outcome_count": len(rows_by_outcome),
        "first_captured_at": _microstructure_timestamp(ordered[0]) if ordered else None,
        "last_captured_at": _microstructure_timestamp(ordered[-1]) if ordered else None,
        "first_clock": clocks[0] if clocks else None,
        "last_clock": clocks[-1] if clocks else None,
        "min_clock_seconds_remaining": min(clock_seconds_values) if clock_seconds_values else None,
        "max_clock_seconds_remaining": max(clock_seconds_values) if clock_seconds_values else None,
        "clock_context_count": len(clocks) + len(clock_seconds_values),
        "context_sources": sources,
        "min_mid_price": min(prices) if prices else None,
        "max_mid_price": max(prices) if prices else None,
        "mid_price_range": round(max(prices) - min(prices), 6) if prices else None,
        "spike_count": spike_count,
        "oscillation_band_count": oscillation_band_count,
        "grid_opportunity_count": grid_opportunity_count,
        "trend_smoothness_score": round(avg_smoothness, 6) if avg_smoothness is not None else None,
        "threshold_calibration": threshold_calibration,
        "threshold_calibration_status": threshold_calibration["calibration_status"],
        "trend_profile": _microstructure_trend_profile(
            spike_count=spike_count,
            oscillation_band_count=oscillation_band_count,
            trend_smoothness_score=avg_smoothness,
            mid_price_range=round(max(prices) - min(prices), 6) if prices else None,
        ),
        "outcome_summaries": summaries,
    }


def _microstructure_tick_context(
    row: dict[str, Any],
    *,
    play_by_play_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    candidates = _microstructure_context_candidates(row)
    period: int | None = None
    clock: Any = None
    clock_seconds: float | None = None
    source: str | None = None
    for source_name, candidate in candidates:
        if period is None:
            period = _microstructure_period(candidate)
        if clock is None:
            clock = _first_present(candidate, ("clock", "game_clock", "period_clock", "clock_label", "time_remaining"))
        if clock_seconds is None:
            clock_seconds = _first_safe_float(
                candidate,
                (
                    "clock_seconds_remaining",
                    "game_clock_seconds_remaining",
                    "clock_remaining_seconds",
                    "seconds_remaining",
                    "period_seconds_remaining",
                ),
            )
        if source is None and (period is not None or clock is not None or clock_seconds is not None):
            source = source_name
        if period is not None and (clock is not None or clock_seconds is not None):
            break
    if (period is None or period <= 0 or (clock is None and clock_seconds is None)) and play_by_play_rows:
        pbp_context = _nearest_play_by_play_context(row, play_by_play_rows)
        if pbp_context:
            if period is None or period <= 0:
                period = _microstructure_period(pbp_context)
            if clock is None:
                clock = _first_present(pbp_context, ("clock", "game_clock", "period_clock", "time_remaining"))
            if clock_seconds is None:
                clock_seconds = _first_safe_float(
                    pbp_context,
                    (
                        "clock_seconds_remaining",
                        "game_clock_seconds_remaining",
                        "clock_remaining_seconds",
                        "seconds_remaining",
                        "period_seconds_remaining",
                    ),
                )
            if source is None and (period is not None or clock is not None or clock_seconds is not None):
                league = str(pbp_context.get("league") or "").strip()
                source = f"play_by_play_context.{league}" if league else "play_by_play_context"
    return {
        "period": period,
        "clock": clock,
        "clock_seconds_remaining": clock_seconds,
        "context_source": source,
    }


def _microstructure_context_candidates(row: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    candidates: list[tuple[str, dict[str, Any]]] = [("tick", row)]
    raw = row.get("raw_json") if isinstance(row.get("raw_json"), dict) else {}
    if raw:
        candidates.append(("raw_json", raw))
    trace = raw.get("trace") if isinstance(raw.get("trace"), dict) else {}
    if trace:
        candidates.append(("trace", trace))
    for parent_name, parent in (("raw_json", raw), ("trace", trace), ("tick", row)):
        for key in (
            "state",
            "state_context",
            "latest_state",
            "latest_state_row",
            "latest_snapshot",
            "scoreboard",
            "game",
            "play_by_play",
            "pbp",
            "evidence",
            "market_state",
        ):
            nested = parent.get(key) if isinstance(parent, dict) else None
            if isinstance(nested, dict):
                candidates.append((f"{parent_name}.{key}", nested))
    return candidates


def _nearest_play_by_play_context(
    row: dict[str, Any],
    play_by_play_rows: list[dict[str, Any]],
    *,
    max_delta_seconds: float = 180.0,
) -> dict[str, Any] | None:
    tick_time = _parse_datetime(row.get("captured_at") or row.get("captured_at_utc") or row.get("source_timestamp"))
    if tick_time is None:
        return None
    nearest: tuple[float, dict[str, Any]] | None = None
    for item in play_by_play_rows:
        if not isinstance(item, dict):
            continue
        pbp_time = _play_by_play_timestamp(item)
        if pbp_time is None:
            continue
        delta = abs((tick_time - pbp_time).total_seconds())
        if delta > max_delta_seconds:
            continue
        if nearest is None or delta < nearest[0]:
            nearest = (delta, item)
    if nearest is None:
        return None
    delta, item = nearest
    return {**item, "pbp_alignment_delta_seconds": round(delta, 3)}


def _play_by_play_timestamp(row: dict[str, Any]) -> datetime | None:
    for key in ("time_actual", "captured_at", "observed_at", "event_time", "timestamp"):
        parsed = _parse_datetime(row.get(key))
        if parsed is not None:
            return parsed
    payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
    for key in ("timeActual", "time_actual", "wallClock", "wall_clock", "eventTime", "timestamp"):
        parsed = _parse_datetime(payload.get(key))
        if parsed is not None:
            return parsed
    return None


def _microstructure_period(candidate: dict[str, Any]) -> int | None:
    for key in ("period", "game_period", "quarter", "quarter_number"):
        parsed = _safe_int(candidate.get(key))
        if parsed is not None and parsed > 0:
            return parsed
    label = candidate.get("period_label") or candidate.get("quarter_label")
    if label is None:
        return None
    text = str(label).strip().lower()
    for prefix in ("q", "quarter", "period"):
        if text.startswith(prefix):
            digits = "".join(char for char in text if char.isdigit())
            parsed = _safe_int(digits)
            if parsed is not None and parsed > 0:
                return parsed
    return None


def _first_present(candidate: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = candidate.get(key)
        if value is not None and value != "":
            return value
    return None


def _first_safe_float(candidate: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _safe_float(candidate.get(key))
        if value is not None:
            return value
    return None


def _microstructure_outcome_summary(outcome_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = _chronological_microstructure_rows(rows)
    threshold_calibration = _microstructure_threshold_calibration(ordered)
    prices = [_safe_float(row.get("mid_price")) for row in ordered]
    prices = [price for price in prices if price is not None]
    if not prices:
        return {
            "outcome_id": outcome_id,
            "tick_count": 0,
            "status": "not_recorded",
        }
    deltas = [round(right - left, 6) for left, right in zip(prices, prices[1:])]
    abs_deltas = [abs(delta) for delta in deltas]
    total_abs_move = sum(abs_deltas)
    net_move = prices[-1] - prices[0] if len(prices) >= 2 else 0.0
    smoothness = abs(net_move) / total_abs_move if total_abs_move > 0 else None
    spike_threshold = _safe_float(threshold_calibration["spike_move_threshold"]) or _MICROSTRUCTURE_BASE_SPIKE_MOVE_THRESHOLD
    grid_threshold = _safe_float(threshold_calibration["grid_move_threshold"]) or _MICROSTRUCTURE_BASE_GRID_MOVE_THRESHOLD
    direction_noise_floor = (
        _safe_float(threshold_calibration["direction_noise_floor"])
        or _MICROSTRUCTURE_BASE_DIRECTION_NOISE_FLOOR
    )
    spike_count = _movement_count(deltas, threshold=spike_threshold)
    oscillation_band_count = _direction_change_count(deltas, noise_floor=direction_noise_floor)
    grid_opportunity_count = _movement_count(deltas, threshold=grid_threshold)
    return {
        "outcome_id": outcome_id,
        "status": "recorded",
        "tick_count": len(prices),
        "first_captured_at": _microstructure_timestamp(ordered[0]),
        "last_captured_at": _microstructure_timestamp(ordered[-1]),
        "min_mid_price": min(prices),
        "max_mid_price": max(prices),
        "mid_price_range": round(max(prices) - min(prices), 6),
        "net_move": round(net_move, 6),
        "total_absolute_move": round(total_abs_move, 6),
        "average_absolute_move": round(total_abs_move / len(abs_deltas), 6) if abs_deltas else 0.0,
        "trend_smoothness_score": round(smoothness, 6) if smoothness is not None else None,
        "threshold_calibration": threshold_calibration,
        "threshold_calibration_status": threshold_calibration["calibration_status"],
        "trend_profile": _microstructure_trend_profile(
            spike_count=spike_count,
            oscillation_band_count=oscillation_band_count,
            trend_smoothness_score=smoothness,
            mid_price_range=round(max(prices) - min(prices), 6),
        ),
        "spike_count": spike_count,
        "oscillation_band_count": oscillation_band_count,
        "grid_opportunity_count": grid_opportunity_count,
        "deltas": deltas,
    }


def _chronological_microstructure_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            _parse_datetime(row.get("captured_at")) is None,
            (_parse_datetime(row.get("captured_at")) or datetime.min.replace(tzinfo=timezone.utc)).isoformat(),
        ),
    )


def _microstructure_timestamp(row: dict[str, Any]) -> str | None:
    parsed = _parse_datetime(row.get("captured_at"))
    return parsed.isoformat() if parsed is not None else None


def _paired_price_series(prices_by_outcome: dict[str, list[dict[str, Any]]]) -> list[tuple[float, float]]:
    ordered = [
        [
            _safe_float(row.get("mid_price"))
            for row in _chronological_microstructure_rows(rows)
            if _safe_float(row.get("mid_price")) is not None
        ]
        for _, rows in sorted(prices_by_outcome.items())
    ]
    if len(ordered) < 2:
        return []
    left, right = ordered[0], ordered[1]
    return list(zip(left, right))


def _paired_price_inversion_point_count(prices_by_outcome: dict[str, list[dict[str, Any]]]) -> int:
    count = 0
    for left, right in _paired_price_series(prices_by_outcome):
        if min(left, right) < 0.5 < max(left, right):
            count += 1
    return count


def _favorite_underdog_leader_inversion_count(prices_by_outcome: dict[str, list[dict[str, Any]]]) -> int:
    previous_leader: int | None = None
    changes = 0
    for left, right in _paired_price_series(prices_by_outcome):
        if left == right:
            continue
        leader = 0 if left > right else 1
        if previous_leader is not None and leader != previous_leader:
            changes += 1
        previous_leader = leader
    return changes


def _movement_count(deltas: list[float], *, threshold: float) -> int:
    return sum(1 for delta in deltas if abs(delta) >= threshold)


def _microstructure_threshold_calibration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    spreads = sorted(
        value
        for value in (_safe_float(row.get("spread")) for row in rows)
        if value is not None and value > 0
    )
    median_spread = _median_float(spreads)
    grid_threshold = max(_MICROSTRUCTURE_BASE_GRID_MOVE_THRESHOLD, median_spread or 0.0)
    spike_threshold = max(_MICROSTRUCTURE_BASE_SPIKE_MOVE_THRESHOLD, grid_threshold * 1.5)
    direction_noise_floor = (
        grid_threshold
        if median_spread is not None and median_spread > _MICROSTRUCTURE_BASE_GRID_MOVE_THRESHOLD
        else _MICROSTRUCTURE_BASE_DIRECTION_NOISE_FLOOR
    )
    calibration_status = "default"
    if median_spread is not None and grid_threshold > _MICROSTRUCTURE_BASE_GRID_MOVE_THRESHOLD:
        calibration_status = "spread_adjusted"
    return {
        "schema_version": "microstructure_threshold_calibration_v1",
        "calibration_status": calibration_status,
        "base_grid_move_threshold": _MICROSTRUCTURE_BASE_GRID_MOVE_THRESHOLD,
        "base_spike_move_threshold": _MICROSTRUCTURE_BASE_SPIKE_MOVE_THRESHOLD,
        "base_direction_noise_floor": _MICROSTRUCTURE_BASE_DIRECTION_NOISE_FLOOR,
        "observed_spread_count": len(spreads),
        "observed_median_spread": round(median_spread, 6) if median_spread is not None else None,
        "grid_move_threshold": round(grid_threshold, 6),
        "spike_move_threshold": round(spike_threshold, 6),
        "direction_noise_floor": round(direction_noise_floor, 6),
        "strategy_authority": "review_only_pending_replay_fillability",
    }


def _median_float(values: list[float]) -> float | None:
    if not values:
        return None
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return (values[midpoint - 1] + values[midpoint]) / 2


def _direction_change_count(
    deltas: list[float],
    *,
    noise_floor: float = _MICROSTRUCTURE_BASE_DIRECTION_NOISE_FLOOR,
) -> int:
    directions: list[int] = []
    for delta in deltas:
        if abs(delta) < noise_floor:
            continue
        directions.append(1 if delta > 0 else -1)
    return sum(1 for left, right in zip(directions, directions[1:]) if left != right)


def _microstructure_trend_profile(
    *,
    spike_count: int,
    oscillation_band_count: int,
    trend_smoothness_score: float | None,
    mid_price_range: float | None,
) -> str:
    price_range = mid_price_range or 0.0
    if price_range < 0.01:
        return "flat_or_sparse"
    if trend_smoothness_score is not None and trend_smoothness_score >= 0.75 and oscillation_band_count <= 1:
        return "smooth_trend"
    if spike_count >= 2 or oscillation_band_count >= 2:
        return "jagged_oscillation"
    if trend_smoothness_score is not None and trend_smoothness_score <= 0.35:
        return "jagged_trend"
    return "mixed_trend"


def _build_event_review_missed_opportunity_candidates(
    *,
    runtime_evidence: dict[str, Any],
    decision_timeline: dict[str, Any],
    market_microstructure: dict[str, Any],
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    order_intent_count = _safe_int((decision_timeline.get("kind_counts") or {}).get("order_intent"))
    price_range = _safe_float(market_microstructure.get("mid_price_range")) or 0.0
    spike_count = _safe_int(market_microstructure.get("spike_count"))
    if price_range >= 0.05 and order_intent_count == 0:
        candidates.append(
            {
                "reason": "price_window_without_order_intent",
                "price_range": round(price_range, 6),
                "order_intent_count": order_intent_count,
                "status": "needs_replay_fillability_check",
            }
        )
    if spike_count >= 2:
        candidates.append(
            {
                "reason": "multiple_microstructure_spikes",
                "spike_count": spike_count,
                "status": "candidate_micro_grid_review",
            }
        )
    grid_opportunity_count = _safe_int(market_microstructure.get("grid_opportunity_count"))
    if grid_opportunity_count >= 2 and order_intent_count == 0:
        candidates.append(
            {
                "reason": "grid_opportunity_without_order_intent",
                "grid_opportunity_count": grid_opportunity_count,
                "status": "needs_replay_fillability_check",
            }
        )
    return {
        "schema_version": "event_missed_opportunity_candidates_v1",
        "status": "recorded",
        "candidate_count": len(candidates),
        "candidates": candidates,
        "requires_queue_depth_replay_before_profit_claim": True,
    }


def _build_event_review_timeline_slices(decision_timeline: dict[str, Any]) -> dict[str, Any]:
    slices: dict[str, list[dict[str, Any]]] = {}
    for entry in decision_timeline.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        period = _timeline_payload_period(payload)
        key = f"period_{period}" if period is not None else "unclassified_period"
        slices.setdefault(key, []).append(entry)
    return {
        "schema_version": "event_timeline_slices_v1",
        "slice_count": len(slices),
        "slices": {key: {"entry_count": len(value), "entries": value} for key, value in sorted(slices.items())},
    }


def _timeline_payload_period(payload: dict[str, Any]) -> int | None:
    candidates = [payload.get("period")]
    raw = payload.get("raw_json") if isinstance(payload.get("raw_json"), dict) else {}
    candidates.append(raw.get("period"))
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    candidates.append(evidence.get("period"))
    for value in candidates:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _timeline_entry(
    *,
    timestamp: Any,
    source: str,
    kind: str,
    summary: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    parsed = _parse_datetime(timestamp)
    return {
        "timestamp_utc": parsed.isoformat() if parsed is not None else (str(timestamp) if timestamp else None),
        "source": source,
        "kind": kind,
        "summary": summary,
        "payload": payload,
    }


def _event_review_known_gaps(
    *,
    account_id: str | None,
    runtime_evidence: dict[str, Any],
    postgame_live_evidence: dict[str, Any],
    portfolio_pnl_attribution: dict[str, Any],
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    if not account_id:
        gaps.append({"reason": "account_id_missing", "impact": "portfolio_pnl_attribution_skipped"})
    if runtime_evidence.get("status") != "ready":
        gaps.append({"reason": "runtime_evidence_partial", "errors": runtime_evidence.get("errors") or []})
    live_items = postgame_live_evidence.get("items") if isinstance(postgame_live_evidence.get("items"), list) else []
    for item in live_items:
        for blocker in item.get("blockers") or []:
            gaps.append({"reason": "postgame_live_evidence_blocker", **dict(blocker)})
    if portfolio_pnl_attribution.get("status") not in {"ready", "not_requested"}:
        gaps.append(
            {
                "reason": "portfolio_pnl_attribution_not_ready",
                "status": portfolio_pnl_attribution.get("status"),
                "source": portfolio_pnl_attribution.get("source"),
            }
        )
    return gaps


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


def _build_live_monitor_current_event_inventory(
    *,
    integrity: dict[str, Any],
    event_ids: list[str],
    day: str | None,
) -> dict[str, Any]:
    direct_clob = integrity.get("direct_clob") if isinstance(integrity.get("direct_clob"), dict) else {}
    if not direct_clob:
        return {
            "schema_version": "live_monitor_current_event_inventory_v1",
            "status": "direct_clob_unavailable",
            "event_count": len(event_ids),
            "items": [],
            "open_order_count": 0,
            "open_position_count": 0,
            "trade_count": 0,
            "unresolved_inventory_present": False,
        }
    raw_orders = (direct_clob.get("open_orders") or {}).get("orders") or []
    raw_positions = (direct_clob.get("open_positions") or {}).get("positions") or []
    raw_trades = (direct_clob.get("current_token_trades") or {}).get("trades") or []
    items: list[dict[str, Any]] = []
    total_open_orders = 0
    total_open_positions = 0
    total_trades = 0
    for event_id in _normalized_unique_values(event_ids):
        plan, plan_event_id, lookup_event_ids = load_current_strategy_plan_for_event(event_id, day=day)
        if not isinstance(plan, dict):
            items.append(
                {
                    "event_id": event_id,
                    "status": "missing_current_strategy_plan",
                    "lookup_event_ids": lookup_event_ids,
                    "token_ids": [],
                    "open_order_count": 0,
                    "open_position_count": 0,
                    "trade_count": 0,
                    "unresolved_inventory_present": False,
                }
            )
            continue
        token_ids = set(_strategy_plan_token_ids(plan))
        open_orders = [to_jsonable(order) for order in raw_orders if _direct_item_token_id(order) in token_ids]
        open_positions = [to_jsonable(position) for position in raw_positions if _direct_item_token_id(position) in token_ids]
        trades = [to_jsonable(trade) for trade in raw_trades if _direct_item_token_id(trade) in token_ids]
        open_order_count = len(open_orders)
        open_position_count = len(open_positions)
        trade_count = len(trades)
        total_open_orders += open_order_count
        total_open_positions += open_position_count
        total_trades += trade_count
        items.append(
            {
                "event_id": event_id,
                "plan_event_id": plan_event_id,
                "status": "recorded",
                "token_ids": sorted(token_ids),
                "open_order_count": open_order_count,
                "open_position_count": open_position_count,
                "trade_count": trade_count,
                "unresolved_inventory_present": bool(open_order_count or open_position_count),
                "open_orders": open_orders,
                "open_positions": open_positions,
                "trades": trades,
            }
        )
    return {
        "schema_version": "live_monitor_current_event_inventory_v1",
        "status": "recorded",
        "event_count": len(items),
        "items": items,
        "open_order_count": total_open_orders,
        "open_position_count": total_open_positions,
        "trade_count": total_trades,
        "unresolved_inventory_present": bool(total_open_orders or total_open_positions),
        "direct_global_open_order_count": direct_clob.get("open_order_count"),
        "direct_global_open_position_count": len(raw_positions),
    }


def _strategy_plan_token_ids(plan: dict[str, Any]) -> list[str]:
    token_ids: list[str] = []
    seen: set[str] = set()
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


def _direct_item_token_id(item: Any) -> str:
    if not isinstance(item, dict):
        item = getattr(item, "__dict__", {}) or {}
    for key in ("token_id", "asset_id", "asset", "outcomeTokenId", "clobTokenId"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _resolve_llm_revision_response(
    event_id: str,
    payload: LLMRevisionAdoptionRequest,
) -> tuple[LLMRevisionResponse, dict[str, Any]]:
    if payload.response is not None:
        return payload.response, {**payload.response.trace_metadata, "source": "request_body"}
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
        "trigger_list": artifact.get("trigger_list") or [],
        "selected_model": artifact.get("selected_model"),
        "persisted_at_utc": artifact.get("persisted_at_utc"),
    }


def _quarter_end_periods_from_trace_metadata(trace_metadata: dict[str, Any]) -> list[int]:
    periods: set[int] = set()
    trigger_list = trace_metadata.get("trigger_list")
    if not isinstance(trigger_list, list):
        return []

    for item in trigger_list:
        if not isinstance(item, dict) or item.get("trigger_type") != "quarter_end":
            continue
        period = _trigger_period_from_trace_item(item)
        if period is not None:
            periods.add(period)
    return sorted(periods)


def _trigger_period_from_trace_item(item: dict[str, Any]) -> int | None:
    evidence = item.get("evidence")
    if not isinstance(evidence, dict):
        return None

    candidates: list[Any] = [evidence.get("period")]
    latest_snapshot = evidence.get("latest_snapshot")
    if isinstance(latest_snapshot, dict):
        candidates.append(latest_snapshot.get("period"))

    for value in candidates:
        try:
            period = int(value)
        except (TypeError, ValueError):
            continue
        if period > 0:
            return period
    return None


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
    adopted_at_utc = datetime.now(timezone.utc).isoformat()
    explainability["llm_revision_adoption"] = {
        "schema_version": "llm_revision_adoption_v1",
        "event_id": event_id,
        "adopted_at_utc": adopted_at_utc,
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
    for period in _quarter_end_periods_from_trace_metadata(trace_metadata):
        marker_prefix = f"q{period}_quarter_end_reviewed"
        explainability[f"{marker_prefix}_utc"] = adopted_at_utc
        explainability.setdefault(
            marker_prefix,
            f"Reviewed through LLM revision adoption request {response.request_id}.",
        )
    for marker_key in _passive_plan_markers_from_trace_metadata(trace_metadata):
        explainability[f"{marker_key}_reviewed_utc"] = adopted_at_utc
        explainability.setdefault(
            f"{marker_key}_reviewed",
            f"Reviewed through LLM revision adoption request {response.request_id}.",
        )
    plan["explainability"] = explainability
    return plan


def _passive_plan_markers_from_trace_metadata(trace_metadata: dict[str, Any]) -> list[str]:
    markers: set[str] = set()
    trigger_list = trace_metadata.get("trigger_list")
    if not isinstance(trigger_list, list):
        return []
    for item in trigger_list:
        if not isinstance(item, dict) or item.get("trigger_type") != "strategy_plan_revision_trigger":
            continue
        evidence = item.get("evidence")
        trigger = evidence.get("trigger") if isinstance(evidence, dict) else None
        if not isinstance(trigger, dict):
            continue
        trigger_type = str(trigger.get("type") or trigger.get("trigger_type") or "").strip().lower()
        if trigger_type == "fresh_q3_state_after_halftime":
            markers.add("fresh_q3_state_after_halftime")
    return sorted(markers)


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


def _llm_revision_actions_are_conservative(actions: list[dict[str, Any]]) -> bool:
    if not actions:
        return False
    conservative_actions = {
        "pause",
        "no_new_entry",
        "cancel_stale_order",
        "adopt_known_position",
        "adopt_manual_position",
        "set_target",
        "target",
        "position_management_only",
        "hold",
        "lower_target",
    }
    unsafe_actions = {
        "new_exposure",
        "increase_size",
        "tail_risk_allocation",
        "ambiguous_hedge",
        "market_order",
        "raw_order",
    }
    for action in actions:
        if not isinstance(action, dict):
            return False
        action_type = str(action.get("action") or action.get("type") or "").strip().lower()
        if not action_type or action_type in unsafe_actions:
            return False
        if action_type not in conservative_actions:
            return False
        if action.get("size") or action.get("notional_usd") or action.get("max_notional_usd"):
            return False
    return True


def _write_llm_conservative_action_adoption_artifact(
    *,
    event_id: str,
    payload: LLMRevisionAdoptionRequest,
    response: LLMRevisionResponse,
    trace_metadata: dict[str, Any],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    event_dir = strategy_plan_root(payload.session_date) / _safe_name(event_id) / "llm_revision_adoptions"
    path = event_dir / f"conservative_actions_{now.strftime('%Y%m%dT%H%M%SZ')}_{_safe_name(response.request_id)}.json"
    record = {
        "schema_version": "llm_conservative_action_adoption_artifact_v1",
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
        "apply_current": False,
        "order_endpoint_call_allowed": False,
        "actions": response.reconciliation_actions,
        "post_adoption_proof": {
            "plan_version_changed": False,
            "raw_order_placed": False,
            "recorded_for_worker_or_operator_review": True,
        },
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


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value))[:160] or "unknown"


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


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
