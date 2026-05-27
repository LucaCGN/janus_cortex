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
    event_id_matches_session_date,
    load_current_strategy_plan,
    load_current_strategy_plan_for_event,
    ops_artifact_root,
    record_ops_stage,
    strategy_plan_root,
    write_json,
    write_strategy_plan,
)
from app.modules.nba.execution.adapter import create_live_order, resolve_trading_account
from codex_tools.polymarket.settlement import classify_documented_residual_positions


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
        day=payload.session_date,
    )
    postgame_evaluation = _build_postgame_evaluation(
        day=payload.session_date,
        reviewed_event_ids=reviewed_event_ids,
        strategy_plan_gate=strategy_plan_gate,
        postgame_live_evidence=postgame_live_evidence,
        portfolio_pnl_attribution=portfolio_pnl_attribution,
    )
    recorded = record_ops_stage(
        "postgame-review",
        {
            **payload.model_dump(mode="json"),
            "reviewed_event_ids": reviewed_event_ids,
            "strategy_plan_gate": strategy_plan_gate,
            "postgame_live_evidence": postgame_live_evidence,
            "portfolio_pnl_attribution": portfolio_pnl_attribution,
            "postgame_evaluation": postgame_evaluation,
        },
        day=payload.session_date,
    )
    return {
        **recorded,
        "reviewed_event_ids": reviewed_event_ids,
        "strategy_plan_gate": strategy_plan_gate,
        "postgame_live_evidence": postgame_live_evidence,
        "portfolio_pnl_attribution": portfolio_pnl_attribution,
        "postgame_evaluation": postgame_evaluation,
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
        if not event_id_matches_session_date(event_id, day):
            continue
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
        if not event_id_matches_session_date(event_id, day):
            continue
        if event_id:
            resolved.append(event_id)
    return _normalized_unique_values(resolved)


def _build_postgame_portfolio_pnl_attribution(
    connection: PsycopgConnection,
    payload: OpsCycleRequest,
    *,
    event_ids: list[str],
    day: str | None,
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
    direct_evidence_summary = {
        key: value for key, value in direct_evidence.items() if key not in {"trades", "open_orders", "open_positions"}
    }
    items: list[dict[str, Any]] = []
    for event_id in event_ids:
        try:
            event_direct_context = _event_scoped_order_lifecycle_direct_context(
                direct_context,
                event_id=event_id,
                day=day,
            )
            rows = _fetch_order_lifecycle_reconciliation_rows(
                connection,
                account_id=payload.account_id,
                event_slug=event_id,
            )
            lifecycle_report = build_order_lifecycle_reconciliation_report(
                rows,
                direct_open_order_external_ids=event_direct_context["direct_open_order_external_ids"],
                direct_open_order_count=event_direct_context["direct_open_order_count"],
                direct_open_position_count=event_direct_context["direct_open_position_count"],
                direct_trade_rows=event_direct_context["direct_trade_rows"],
            )
            pnl_attribution = build_portfolio_pnl_attribution_report(lifecycle_report)
            items.append(
                {
                    "ok": True,
                    "event_id": event_id,
                    "event_slug": event_id,
                    "direct_event_scope": event_direct_context.get("direct_event_scope"),
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


def _build_postgame_evaluation(
    *,
    day: str | None,
    reviewed_event_ids: list[str],
    strategy_plan_gate: dict[str, Any],
    postgame_live_evidence: dict[str, Any],
    portfolio_pnl_attribution: dict[str, Any],
) -> dict[str, Any]:
    pnl_items = portfolio_pnl_attribution.get("items") if isinstance(portfolio_pnl_attribution.get("items"), list) else []
    realized_items = [_build_postgame_realized_live_item(item) for item in pnl_items if isinstance(item, dict)]
    unresolved = [
        gap
        for item in realized_items
        for gap in item.get("unresolved_evidence", [])
        if isinstance(gap, dict)
    ]
    pnl_status = str(portfolio_pnl_attribution.get("status") or "not_requested")
    live_status = str(postgame_live_evidence.get("status") or "unknown")
    replay_inputs = {
        event_id: _read_postgame_replay_tick_stream_summary(day=day, event_id=event_id)
        for event_id in reviewed_event_ids
    }
    realized_live = {
        "schema_version": "postgame_realized_live_v1",
        "status": pnl_status,
        "source_confidence": "account_confirmed"
        if pnl_status == "ready"
        else "inferred",
        "account_pnl_source": "portfolio_order_lifecycle_pnl_attribution_v1",
        "public_market_tape_excluded_from_account_pnl": True,
        "event_count": len(realized_items),
        "items": realized_items,
    }
    replay_modes = {
        "sleeve_isolated": _build_postgame_replay_mode(
            "sleeve_isolated",
            "Run each sleeve alone with the same event budget and simulated fills from recorded direct CLOB prices.",
            replay_inputs=replay_inputs,
        ),
        "aggregate_replay": _build_postgame_replay_mode(
            "aggregate_replay",
            "Run all sleeves together through the current aggregator, risk, budget, and dedupe rules.",
            replay_inputs=replay_inputs,
        ),
        "leave_one_out": _build_postgame_replay_mode(
            "leave_one_out",
            "Run aggregate replay minus one sleeve to measure marginal sleeve value.",
            replay_inputs=replay_inputs,
        ),
    }
    mode_comparison = _build_postgame_mode_comparison(
        realized_live=realized_live,
        replay_modes=replay_modes,
    )
    sleeve_scoreboard = _build_postgame_sleeve_scoreboard(replay_modes=replay_modes)
    why_no_trade = _build_postgame_why_no_trade(replay_inputs=replay_inputs)
    if not reviewed_event_ids:
        status_text = "not_requested"
    elif pnl_status == "ready" and not unresolved:
        status_text = "ready"
    elif pnl_status in {"error", "partial"}:
        status_text = "review_required"
    else:
        status_text = "review_required"

    return to_jsonable(
        {
            "schema_version": "postgame_evaluation_v1",
            "status": status_text,
            "reviewed_event_ids": reviewed_event_ids,
            "source_authority": _postgame_evaluation_source_authority(),
            "source_confidence_labels": {
                "account_confirmed": "Metric is backed by account-scoped direct CLOB fills or Janus reconciliation.",
                "db_confirmed": "Metric is backed by local Janus DB lifecycle rows.",
                "clob_market_tape": "Metric is direct CLOB token market tape for price path/fillability only.",
                "ui_observed": "Metric is operator/UI display evidence and may be rounded.",
                "inferred": "Metric is derived from incomplete evidence and must stay review-gated.",
            },
            "strategy_plan_gate": {
                "status": strategy_plan_gate.get("status"),
                "ready": strategy_plan_gate.get("ready"),
                "event_count": len(reviewed_event_ids),
            },
            "realized_live": realized_live,
            "replay_modes": replay_modes,
            "mode_comparison": mode_comparison,
            "sleeve_scoreboard": sleeve_scoreboard,
            "why_no_trade": why_no_trade,
            "strategy_promotion_review": _build_postgame_strategy_promotion_review(
                evaluation_status=status_text,
                realized_live=realized_live,
                sleeve_scoreboard=sleeve_scoreboard,
                why_no_trade=why_no_trade,
                unresolved_evidence=unresolved,
            ),
            "replay_input": {
                "schema_version": "postgame_replay_input_v1",
                "source_confidence": "runtime_artifact",
                "same_tick_stream_for_all_modes": True,
                "event_count": len(replay_inputs),
                "events": replay_inputs,
            },
            "market_tape_policy": {
                "source_confidence": "clob_market_tape",
                "account_pnl_eligible": False,
                "allowed_uses": ["price_path", "fillability", "liquidity", "ui_rounding_comparison"],
                "blocked_uses": ["account_pnl", "realized_return", "all_account_performance"],
            },
            "postgame_live_evidence_status": live_status,
            "portfolio_pnl_attribution_status": pnl_status,
            "unresolved_evidence_count": len(unresolved),
            "unresolved_evidence": unresolved,
        }
    )


def _build_postgame_mode_comparison(
    *,
    realized_live: dict[str, Any],
    replay_modes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    realized_items = realized_live.get("items") if isinstance(realized_live.get("items"), list) else []
    known_cashflow = 0.0
    known_cashflow_available = False
    unresolved_count = 0
    for item in realized_items:
        if not isinstance(item, dict):
            continue
        account_pnl = item.get("account_pnl") if isinstance(item.get("account_pnl"), dict) else {}
        cashflow = _safe_float(account_pnl.get("known_cashflow_usd"))
        if cashflow is not None:
            known_cashflow += cashflow
            known_cashflow_available = True
        unresolved_count += len(item.get("unresolved_evidence") or [])
    rows.append(
        {
            "mode": "realized_live",
            "status": realized_live.get("status"),
            "source_confidence": realized_live.get("source_confidence"),
            "account_pnl_eligible": True,
            "event_count": realized_live.get("event_count", 0),
            "candidate_count": None,
            "simulated_fill_count": None,
            "known_cashflow_usd": round(known_cashflow, 6) if known_cashflow_available else None,
            "simulated_pnl_usd": None,
            "missed_window_estimated_value_usd": None,
            "unresolved_evidence_count": unresolved_count,
        }
    )

    sleeve_mode = replay_modes.get("sleeve_isolated") if isinstance(replay_modes.get("sleeve_isolated"), dict) else {}
    sleeve_rows = sleeve_mode.get("sleeves") if isinstance(sleeve_mode.get("sleeves"), list) else []
    sleeve_pnl: float | None = 0.0
    sleeve_cashflow = 0.0
    sleeve_mark_value = 0.0
    sleeve_candidate_count = 0
    sleeve_fill_count = 0
    sleeve_missing_count = 0
    sleeve_not_fillable_count = 0
    sleeve_missed_value = 0.0
    for row in sleeve_rows:
        if not isinstance(row, dict):
            continue
        sleeve_candidate_count += _safe_int(row.get("unique_candidate_count"))
        sleeve_fill_count += _safe_int(row.get("simulated_fill_count"))
        sleeve_missing_count += _safe_int(row.get("missing_price_count"))
        sleeve_not_fillable_count += _safe_int(row.get("not_fillable_count"))
        sleeve_cashflow += _safe_float(row.get("simulated_cashflow_usd")) or 0.0
        sleeve_mark_value += _safe_float(row.get("simulated_mark_value_usd")) or 0.0
        sleeve_missed_value += _safe_float(row.get("missed_window_estimated_value_usd")) or 0.0
        if row.get("simulated_pnl_usd") is None and _safe_int(row.get("unique_candidate_count")):
            sleeve_pnl = None
        elif sleeve_pnl is not None:
            sleeve_pnl += _safe_float(row.get("simulated_pnl_usd")) or 0.0
    rows.append(
        {
            "mode": "sleeve_isolated",
            "status": sleeve_mode.get("status"),
            "source_confidence": sleeve_mode.get("source_confidence"),
            "account_pnl_eligible": False,
            "sleeve_count": len(sleeve_rows),
            "candidate_count": sleeve_candidate_count,
            "simulated_fill_count": sleeve_fill_count,
            "missing_price_count": sleeve_missing_count,
            "not_fillable_count": sleeve_not_fillable_count,
            "simulated_cashflow_usd": round(sleeve_cashflow, 6),
            "simulated_mark_value_usd": round(sleeve_mark_value, 6),
            "simulated_pnl_usd": round(sleeve_pnl, 6) if sleeve_pnl is not None else None,
            "missed_window_estimated_value_usd": round(sleeve_missed_value, 6),
        }
    )

    aggregate_mode = replay_modes.get("aggregate_replay") if isinstance(replay_modes.get("aggregate_replay"), dict) else {}
    aggregate = aggregate_mode.get("aggregate") if isinstance(aggregate_mode.get("aggregate"), dict) else {}
    rows.append(
        {
            "mode": "aggregate_replay",
            "status": aggregate_mode.get("status"),
            "source_confidence": aggregate_mode.get("source_confidence"),
            "account_pnl_eligible": False,
            "candidate_count": aggregate.get("unique_candidate_count"),
            "simulated_fill_count": aggregate.get("simulated_fill_count"),
            "missing_price_count": aggregate.get("missing_price_count"),
            "not_fillable_count": aggregate.get("not_fillable_count"),
            "simulated_cashflow_usd": aggregate.get("simulated_cashflow_usd"),
            "simulated_mark_value_usd": aggregate.get("simulated_mark_value_usd"),
            "simulated_pnl_usd": aggregate.get("simulated_pnl_usd"),
            "missed_window_estimated_value_usd": aggregate.get("missed_window_estimated_value_usd"),
            "blocker_reason_count": len(aggregate.get("blocker_reason_counts") or {}),
        }
    )

    leave_mode = replay_modes.get("leave_one_out") if isinstance(replay_modes.get("leave_one_out"), dict) else {}
    leave_rows = leave_mode.get("leave_one_out_rows") if isinstance(leave_mode.get("leave_one_out_rows"), list) else []
    marginal_values = [
        value
        for value in (_safe_float(row.get("marginal_value_usd")) for row in leave_rows if isinstance(row, dict))
        if value is not None
    ]
    rows.append(
        {
            "mode": "leave_one_out",
            "status": leave_mode.get("status"),
            "source_confidence": leave_mode.get("source_confidence"),
            "account_pnl_eligible": False,
            "excluded_sleeve_count": len(leave_rows),
            "positive_marginal_sleeve_count": sum(1 for value in marginal_values if value > 0),
            "negative_marginal_sleeve_count": sum(1 for value in marginal_values if value < 0),
            "best_marginal_value_usd": round(max(marginal_values), 6) if marginal_values else None,
            "worst_marginal_value_usd": round(min(marginal_values), 6) if marginal_values else None,
            "total_marginal_value_usd": round(sum(marginal_values), 6) if marginal_values else None,
        }
    )
    simulated_rows = [row for row in rows if _safe_float(row.get("simulated_pnl_usd")) is not None]
    simulated_rows.sort(key=lambda row: _safe_float(row.get("simulated_pnl_usd")) or 0.0, reverse=True)
    return {
        "schema_version": "postgame_mode_comparison_v1",
        "source_confidence": "mixed",
        "same_tick_stream_for_replay_modes": True,
        "account_pnl_uses_market_tape": False,
        "row_count": len(rows),
        "rows": rows,
        "best_simulated_mode": simulated_rows[0]["mode"] if simulated_rows else None,
        "worst_simulated_mode": simulated_rows[-1]["mode"] if simulated_rows else None,
    }


def _build_postgame_sleeve_scoreboard(*, replay_modes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    isolated = replay_modes.get("sleeve_isolated") if isinstance(replay_modes.get("sleeve_isolated"), dict) else {}
    leave_one_out = replay_modes.get("leave_one_out") if isinstance(replay_modes.get("leave_one_out"), dict) else {}
    leave_rows = {
        str(row.get("excluded_sleeve_id")): row
        for row in leave_one_out.get("leave_one_out_rows", [])
        if isinstance(row, dict) and row.get("excluded_sleeve_id") is not None
    }
    rows: list[dict[str, Any]] = []
    for row in isolated.get("sleeves") or []:
        if not isinstance(row, dict):
            continue
        sleeve_id = str(row.get("sleeve_id") or "")
        leave_row = leave_rows.get(sleeve_id, {})
        simulated_pnl = _safe_float(row.get("simulated_pnl_usd"))
        missed_value = _safe_float(row.get("missed_window_estimated_value_usd")) or 0.0
        blocker_count = _safe_int(row.get("blocker_count"))
        candidate_count = _safe_int(row.get("unique_candidate_count") or row.get("candidate_count"))
        fill_count = _safe_int(row.get("simulated_fill_count"))
        status_text = _postgame_sleeve_performance_status(
            simulated_pnl=simulated_pnl,
            missed_value=missed_value,
            blocker_count=blocker_count,
            candidate_count=candidate_count,
            fill_count=fill_count,
        )
        rows.append(
            {
                "event_id": row.get("event_id"),
                "sleeve_id": sleeve_id,
                "strategy_id": row.get("strategy_id"),
                "sleeve_role": row.get("sleeve_role"),
                "sleeve_side": row.get("sleeve_side"),
                "strategy_family": row.get("strategy_family"),
                "status": status_text,
                "tick_count": row.get("tick_count"),
                "intent_count": row.get("intent_count"),
                "candidate_count": candidate_count,
                "simulated_fill_count": fill_count,
                "blocker_count": blocker_count,
                "top_blockers": list(row.get("blocker_reasons") or [])[:5],
                "simulated_pnl_usd": row.get("simulated_pnl_usd"),
                "missed_window_estimated_value_usd": row.get("missed_window_estimated_value_usd"),
                "leave_one_out_marginal_value_usd": leave_row.get("marginal_value_usd"),
                "source_confidence": row.get("source_confidence") or "runtime_artifact",
                "next_action": _postgame_sleeve_next_action(
                    status_text=status_text,
                    simulated_pnl=simulated_pnl,
                    missed_value=missed_value,
                    blocker_count=blocker_count,
                    candidate_count=candidate_count,
                    fill_count=fill_count,
                ),
            }
        )
    rows.sort(
        key=lambda item: (
            _safe_float(item.get("leave_one_out_marginal_value_usd")) or 0.0,
            _safe_float(item.get("simulated_pnl_usd")) or 0.0,
            _safe_float(item.get("missed_window_estimated_value_usd")) or 0.0,
        ),
        reverse=True,
    )
    return {
        "schema_version": "postgame_sleeve_scoreboard_v1",
        "source_confidence": "runtime_artifact",
        "row_count": len(rows),
        "rows": rows,
        "positive_simulated_sleeve_count": sum(
            1 for row in rows if (_safe_float(row.get("simulated_pnl_usd")) or 0.0) > 0
        ),
        "blocked_sleeve_count": sum(1 for row in rows if _safe_int(row.get("blocker_count")) > 0),
        "missed_window_sleeve_count": sum(
            1 for row in rows if (_safe_float(row.get("missed_window_estimated_value_usd")) or 0.0) > 0
        ),
        "top_rows": rows[:10],
    }


def _postgame_sleeve_performance_status(
    *,
    simulated_pnl: float | None,
    missed_value: float,
    blocker_count: int,
    candidate_count: int,
    fill_count: int,
) -> str:
    if candidate_count == 0 and blocker_count:
        return "blocked_without_candidates"
    if candidate_count == 0:
        return "no_candidates"
    if fill_count == 0:
        return "not_fillable_or_blocked"
    if simulated_pnl is None:
        return "review_required"
    if simulated_pnl > 0:
        return "positive_replay"
    if simulated_pnl < 0 and missed_value > 0:
        return "negative_with_missed_window"
    if simulated_pnl < 0:
        return "negative_replay"
    return "flat_replay"


def _postgame_sleeve_next_action(
    *,
    status_text: str,
    simulated_pnl: float | None,
    missed_value: float,
    blocker_count: int,
    candidate_count: int,
    fill_count: int,
) -> str:
    if status_text == "blocked_without_candidates":
        return "fix_or_reclassify_local_blockers_before_next_live_window"
    if candidate_count and not fill_count:
        return "inspect_fillability_limits_and_orderbook_spread_policy"
    if missed_value > 0 and (simulated_pnl is None or simulated_pnl <= 0):
        return "review_missed_window_trigger_thresholds_and_pairing"
    if simulated_pnl is not None and simulated_pnl > 0:
        return "replay_more_events_before_promotion"
    if simulated_pnl is not None and simulated_pnl < 0:
        return "demote_or_tighten_until_retested"
    if blocker_count:
        return "separate_local_sleeve_blocker_from_global_gate"
    return "collect_more_replay_evidence"


def _build_postgame_why_no_trade(*, replay_inputs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    event_rows: list[dict[str, Any]] = []
    aggregate_scope_counts: dict[str, int] = {}
    aggregate_blockers: dict[str, int] = {}
    for event_id, summary in replay_inputs.items():
        if not isinstance(summary, dict):
            continue
        blocker_counts = summary.get("blocker_reason_counts") if isinstance(summary.get("blocker_reason_counts"), dict) else {}
        for reason, count in blocker_counts.items():
            reason_text = str(reason)
            aggregate_blockers[reason_text] = aggregate_blockers.get(reason_text, 0) + _safe_int(count)
            scope = _postgame_blocker_scope(reason_text)
            aggregate_scope_counts[scope] = aggregate_scope_counts.get(scope, 0) + _safe_int(count)
        sleeve_rows: list[dict[str, Any]] = []
        for sleeve_id, sleeve in (summary.get("sleeves") or {}).items():
            if not isinstance(sleeve, dict):
                continue
            reasons = [str(reason) for reason in sleeve.get("blocker_reasons") or []]
            scope_counts: dict[str, int] = {}
            for reason in reasons:
                scope = _postgame_blocker_scope(reason)
                scope_counts[scope] = scope_counts.get(scope, 0) + 1
            sleeve_rows.append(
                {
                    "sleeve_id": str(sleeve_id),
                    "strategy_id": sleeve.get("strategy_id"),
                    "sleeve_role": sleeve.get("sleeve_role"),
                    "side": sleeve.get("sleeve_side"),
                    "blocker_count": sleeve.get("blocker_count"),
                    "candidate_count": (sleeve.get("fill_simulation") or {}).get("candidate_count")
                    if isinstance(sleeve.get("fill_simulation"), dict)
                    else 0,
                    "blocker_reasons": reasons[:10],
                    "blocker_scope_counts": dict(sorted(scope_counts.items())),
                    "next_diagnostic": "local_sleeve_thresholds"
                    if scope_counts.get("local_sleeve")
                    else "global_gate_or_runtime_evidence",
                }
            )
        sleeve_rows.sort(key=lambda row: _safe_int(row.get("blocker_count")), reverse=True)
        missed = summary.get("missed_window_analysis") if isinstance(summary.get("missed_window_analysis"), dict) else {}
        event_rows.append(
            {
                "event_id": event_id,
                "status": summary.get("status"),
                "tick_count": summary.get("tick_count"),
                "intent_count": summary.get("intent_count"),
                "executed_order_count": summary.get("executed_order_count"),
                "order_intent_candidate_count": summary.get("order_intent_candidate_count"),
                "top_global_blockers": _top_count_rows(blocker_counts, limit=8),
                "global_blocker_scope_counts": _count_scope_rows(blocker_counts),
                "sleeves": sleeve_rows[:20],
                "blocked_sleeve_windows": missed.get("blocked_sleeve_rows", [])[:10],
                "missed_candidate_windows": missed.get("rows", [])[:10],
                "source_confidence": summary.get("source_confidence") or "runtime_artifact",
            }
        )
    return {
        "schema_version": "postgame_why_no_trade_v1",
        "source_confidence": "runtime_artifact",
        "policy": {
            "global_gate_scope": "Only live-safety, account/direct-truth, worker, and strategy-plan readiness gates may block all sleeves.",
            "local_sleeve_scope": "Price, score, phase, spread, and sleeve budget blockers must stay local to the sleeve.",
        },
        "event_count": len(event_rows),
        "aggregate_blocker_reason_counts": dict(sorted(aggregate_blockers.items())),
        "aggregate_blocker_scope_counts": dict(sorted(aggregate_scope_counts.items())),
        "events": event_rows,
    }


def _build_postgame_strategy_promotion_review(
    *,
    evaluation_status: str,
    realized_live: dict[str, Any],
    sleeve_scoreboard: dict[str, Any],
    why_no_trade: dict[str, Any],
    unresolved_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    unresolved_count = len(unresolved_evidence)
    for row in sleeve_scoreboard.get("rows") or []:
        if not isinstance(row, dict):
            continue
        simulated_pnl = _safe_float(row.get("simulated_pnl_usd"))
        blocker_count = _safe_int(row.get("blocker_count"))
        fill_count = _safe_int(row.get("simulated_fill_count"))
        missed_value = _safe_float(row.get("missed_window_estimated_value_usd")) or 0.0
        eligible = (
            unresolved_count == 0
            and simulated_pnl is not None
            and simulated_pnl > 0
            and fill_count > 0
            and blocker_count == 0
        )
        reasons: list[str] = []
        if unresolved_count:
            reasons.append("realized_lifecycle_or_direct_evidence_unresolved")
        if simulated_pnl is None:
            reasons.append("simulated_pnl_missing")
        elif simulated_pnl <= 0:
            reasons.append("simulated_pnl_not_positive")
        if fill_count <= 0:
            reasons.append("no_simulated_fills")
        if blocker_count:
            reasons.append("live_blockers_present")
        if missed_value > 0:
            reasons.append("missed_window_review_required")
        rows.append(
            {
                "sleeve_id": row.get("sleeve_id"),
                "strategy_id": row.get("strategy_id"),
                "sleeve_role": row.get("sleeve_role"),
                "side": row.get("sleeve_side"),
                "eligible_for_promotion": eligible,
                "review_reasons": reasons,
                "recommended_change": "promote_to_more_replay_events"
                if eligible
                else row.get("next_action") or "collect_more_evidence",
                "source_confidence": row.get("source_confidence") or "runtime_artifact",
            }
        )
    global_gate_count = _safe_int((why_no_trade.get("aggregate_blocker_scope_counts") or {}).get("global_gate"))
    if not rows:
        status_text = "no_sleeve_rows"
    elif unresolved_count:
        status_text = "blocked_by_unresolved_realized_evidence"
    elif global_gate_count:
        status_text = "blocked_by_global_gate_review"
    elif any(row.get("eligible_for_promotion") for row in rows):
        status_text = "promotion_candidates_present"
    else:
        status_text = "no_promotion_candidates"
    return {
        "schema_version": "postgame_strategy_promotion_review_v1",
        "status": status_text,
        "evaluation_status": evaluation_status,
        "realized_live_status": realized_live.get("status"),
        "source_confidence": "runtime_artifact",
        "automation_ready": status_text == "promotion_candidates_present",
        "unresolved_evidence_count": unresolved_count,
        "global_gate_blocker_count": global_gate_count,
        "row_count": len(rows),
        "rows": rows,
        "promotion_policy": {
            "requires_account_or_db_realized_evidence_ready": True,
            "requires_positive_replay_pnl": True,
            "requires_no_live_blockers": True,
            "requires_fillability": True,
            "requires_missed_window_review_when_positive": True,
        },
    }


def _postgame_blocker_scope(reason: str) -> str:
    reason_text = reason.lower()
    global_markers = (
        "kill_switch",
        "operator",
        "account",
        "direct_truth",
        "strategy_plan",
        "worker",
        "scoreboard_freshness",
        "live_safety",
        "preflight",
        "clob",
    )
    local_markers = (
        "price_band",
        "score_gap",
        "clock",
        "phase",
        "spread",
        "budget",
        "duplicate",
        "position_limit",
        "orderbook_spread",
    )
    if any(marker in reason_text for marker in global_markers):
        return "global_gate"
    if any(marker in reason_text for marker in local_markers):
        return "local_sleeve"
    return "unknown"


def _top_count_rows(counts: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    rows = [{"reason": str(reason), "count": _safe_int(count)} for reason, count in counts.items()]
    rows.sort(key=lambda row: row["count"], reverse=True)
    return rows[:limit]


def _count_scope_rows(counts: dict[str, Any]) -> dict[str, int]:
    scope_counts: dict[str, int] = {}
    for reason, count in counts.items():
        scope = _postgame_blocker_scope(str(reason))
        scope_counts[scope] = scope_counts.get(scope, 0) + _safe_int(count)
    return dict(sorted(scope_counts.items()))


def _postgame_evaluation_source_authority() -> list[dict[str, Any]]:
    return [
        {
            "rank": 1,
            "source": "account_scoped_direct_clob_and_janus_reconciliation",
            "source_confidence": "account_confirmed",
            "allowed_for_account_pnl": True,
        },
        {
            "rank": 2,
            "source": "janus_db_order_trade_lifecycle",
            "source_confidence": "db_confirmed",
            "allowed_for_account_pnl": True,
        },
        {
            "rank": 3,
            "source": "direct_current_event_open_positions_and_orders",
            "source_confidence": "account_confirmed",
            "allowed_for_account_pnl": False,
            "allowed_for": ["exposure", "residual_inventory", "target_coverage"],
        },
        {
            "rank": 4,
            "source": "direct_clob_token_market_tape",
            "source_confidence": "clob_market_tape",
            "allowed_for_account_pnl": False,
            "allowed_for": ["price_path", "fillability", "liquidity"],
        },
        {
            "rank": 5,
            "source": "polymarket_ui_screenshots",
            "source_confidence": "ui_observed",
            "allowed_for_account_pnl": False,
            "allowed_for": ["operator_audit", "displayed_rounding"],
        },
    ]


def _build_postgame_replay_mode(
    mode: str,
    description: str,
    *,
    replay_inputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    recorded_inputs = [item for item in replay_inputs.values() if item.get("status") == "recorded"]
    sleeve_rows = _postgame_replay_mode_sleeve_rows(replay_inputs)
    aggregate = _postgame_replay_mode_aggregate(replay_inputs)
    status_text = "input_ready" if recorded_inputs else "input_missing"
    base = {
        "schema_version": "postgame_replay_mode_status_v1",
        "mode": mode,
        "status": status_text,
        "source_confidence": "runtime_artifact" if recorded_inputs else "inferred",
        "description": description,
        "account_pnl_eligible": False,
        "same_tick_stream_for_all_modes": True,
        "event_count": len(replay_inputs),
        "recorded_event_count": len(recorded_inputs),
        "simulation_status": aggregate.get("simulation_status") or "input_missing",
    }
    if mode == "sleeve_isolated":
        return {
            **base,
            "sleeve_count": len(sleeve_rows),
            "sleeves": sleeve_rows,
        }
    if mode == "aggregate_replay":
        return {
            **base,
            "aggregate": aggregate,
        }
    if mode == "leave_one_out":
        return {
            **base,
            "excluded_sleeve_count": len(sleeve_rows),
            "leave_one_out_rows": _postgame_replay_mode_leave_one_out_rows(
                sleeve_rows,
                aggregate=aggregate,
            ),
        }
    return {
        **base,
        "status": "unknown_mode",
    }


def _postgame_replay_mode_sleeve_rows(replay_inputs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event_id, summary in replay_inputs.items():
        for sleeve_id, sleeve in (summary.get("sleeves") or {}).items():
            if not isinstance(sleeve, dict):
                continue
            simulation = sleeve.get("fill_simulation") if isinstance(sleeve.get("fill_simulation"), dict) else {}
            missed = _postgame_missed_value_for_sleeve(summary, sleeve_id=str(sleeve_id))
            rows.append(
                {
                    "event_id": event_id,
                    "sleeve_id": sleeve_id,
                    "strategy_id": sleeve.get("strategy_id"),
                    "sleeve_role": sleeve.get("sleeve_role"),
                    "sleeve_side": sleeve.get("sleeve_side"),
                    "strategy_family": sleeve.get("strategy_family"),
                    "tick_count": sleeve.get("tick_count"),
                    "intent_count": sleeve.get("intent_count"),
                    "blocker_count": sleeve.get("blocker_count"),
                    "blocker_reasons": sleeve.get("blocker_reasons") or [],
                    "candidate_count": simulation.get("candidate_count", 0),
                    "unique_candidate_count": simulation.get("unique_candidate_count", 0),
                    "simulated_fill_count": simulation.get("simulated_fill_count", 0),
                    "not_fillable_count": simulation.get("not_fillable_count", 0),
                    "missing_price_count": simulation.get("missing_price_count", 0),
                    "unmatched_sell_count": simulation.get("unmatched_sell_count", 0),
                    "simulated_cashflow_usd": simulation.get("simulated_cashflow_usd"),
                    "simulated_mark_value_usd": simulation.get("simulated_mark_value_usd"),
                    "simulated_pnl_usd": simulation.get("simulated_pnl_usd"),
                    "simulation_status": simulation.get("status") or "no_candidates",
                    "missed_window_estimated_value_usd": missed,
                    "source_confidence": "runtime_artifact",
                }
            )
    return rows


def _postgame_replay_mode_aggregate(replay_inputs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    blocker_counts: dict[str, int] = {}
    decision_counts: dict[str, int] = {}
    tick_count = 0
    intent_count = 0
    executed_order_count = 0
    order_intent_candidate_count = 0
    candidate_count = 0
    unique_candidate_count = 0
    simulated_fill_count = 0
    not_fillable_count = 0
    missing_price_count = 0
    unmatched_sell_count = 0
    missed_window_estimated_value = 0.0
    missed_window_count = 0
    simulated_cashflow = 0.0
    simulated_mark_value = 0.0
    simulated_pnl: float | None = 0.0
    simulation_statuses: set[str] = set()
    for summary in replay_inputs.values():
        tick_count += _safe_int(summary.get("tick_count"))
        intent_count += _safe_int(summary.get("intent_count"))
        executed_order_count += _safe_int(summary.get("executed_order_count"))
        order_intent_candidate_count += _safe_int(summary.get("order_intent_candidate_count"))
        simulation = summary.get("fill_simulation") if isinstance(summary.get("fill_simulation"), dict) else {}
        candidate_count += _safe_int(simulation.get("candidate_count"))
        unique_candidate_count += _safe_int(simulation.get("unique_candidate_count"))
        simulated_fill_count += _safe_int(simulation.get("simulated_fill_count"))
        not_fillable_count += _safe_int(simulation.get("not_fillable_count"))
        missing_price_count += _safe_int(simulation.get("missing_price_count"))
        unmatched_sell_count += _safe_int(simulation.get("unmatched_sell_count"))
        missed = summary.get("missed_window_analysis") if isinstance(summary.get("missed_window_analysis"), dict) else {}
        missed_window_estimated_value += _safe_float(missed.get("estimated_missed_value_usd")) or 0.0
        missed_window_count += len(missed.get("rows") or [])
        simulated_cashflow += _safe_float(simulation.get("simulated_cashflow_usd")) or 0.0
        simulated_mark_value += _safe_float(simulation.get("simulated_mark_value_usd")) or 0.0
        if simulation.get("simulated_pnl_usd") is None and _safe_int(simulation.get("unique_candidate_count")):
            simulated_pnl = None
        elif simulated_pnl is not None:
            simulated_pnl += _safe_float(simulation.get("simulated_pnl_usd")) or 0.0
        if simulation.get("status"):
            simulation_statuses.add(str(simulation.get("status")))
        for key, count in (summary.get("blocker_reason_counts") or {}).items():
            blocker_counts[str(key)] = blocker_counts.get(str(key), 0) + _safe_int(count)
        for key, count in (summary.get("decision_type_counts") or {}).items():
            decision_counts[str(key)] = decision_counts.get(str(key), 0) + _safe_int(count)
    return {
        "tick_count": tick_count,
        "intent_count": intent_count,
        "executed_order_count": executed_order_count,
        "order_intent_candidate_count": order_intent_candidate_count,
        "blocker_reason_counts": dict(sorted(blocker_counts.items())),
        "decision_type_counts": dict(sorted(decision_counts.items())),
        "candidate_count": candidate_count,
        "unique_candidate_count": unique_candidate_count,
        "simulated_fill_count": simulated_fill_count,
        "not_fillable_count": not_fillable_count,
        "missing_price_count": missing_price_count,
        "unmatched_sell_count": unmatched_sell_count,
        "simulated_cashflow_usd": round(simulated_cashflow, 6),
        "simulated_mark_value_usd": round(simulated_mark_value, 6),
        "simulated_pnl_usd": round(simulated_pnl, 6) if simulated_pnl is not None else None,
        "missed_window_estimated_value_usd": round(missed_window_estimated_value, 6),
        "missed_window_count": missed_window_count,
        "simulation_status": _combined_postgame_replay_simulation_status(
            statuses=simulation_statuses,
            unique_candidate_count=unique_candidate_count,
            simulated_fill_count=simulated_fill_count,
            missing_price_count=missing_price_count,
            not_fillable_count=not_fillable_count,
        ),
    }


def _postgame_replay_mode_leave_one_out_rows(
    sleeve_rows: list[dict[str, Any]],
    *,
    aggregate: dict[str, Any],
) -> list[dict[str, Any]]:
    aggregate_pnl = _safe_float(aggregate.get("simulated_pnl_usd"))
    rows: list[dict[str, Any]] = []
    for row in sleeve_rows:
        sleeve_pnl = _safe_float(row.get("simulated_pnl_usd"))
        aggregate_without = None
        marginal = None
        status_text = row.get("simulation_status") or "no_candidates"
        confidence = "clob_market_tape" if sleeve_pnl is not None and aggregate_pnl is not None else "inferred"
        if sleeve_pnl is not None and aggregate_pnl is not None:
            aggregate_without = round(aggregate_pnl - sleeve_pnl, 6)
            marginal = round(aggregate_pnl - aggregate_without, 6)
        rows.append(
            {
                "event_id": row.get("event_id"),
                "excluded_sleeve_id": row.get("sleeve_id"),
                "excluded_strategy_id": row.get("strategy_id"),
                "sleeve_role": row.get("sleeve_role"),
                "status": status_text,
                "input_tick_count": row.get("tick_count"),
                "candidate_count": row.get("candidate_count", 0),
                "simulated_fill_count": row.get("simulated_fill_count", 0),
                "aggregate_simulated_pnl_usd": aggregate_pnl,
                "aggregate_without_excluded_simulated_pnl_usd": aggregate_without,
                "marginal_value_usd": marginal,
                "marginal_value_source_confidence": confidence,
            }
        )
    return rows


def _postgame_missed_value_for_sleeve(summary: dict[str, Any], *, sleeve_id: str) -> float:
    missed = summary.get("missed_window_analysis") if isinstance(summary.get("missed_window_analysis"), dict) else {}
    value = 0.0
    for row in missed.get("rows") or []:
        if isinstance(row, dict) and str(row.get("sleeve_id") or "") == sleeve_id:
            value += _safe_float(row.get("estimated_missed_value_usd")) or 0.0
    return round(value, 6)


def _combined_postgame_replay_simulation_status(
    *,
    statuses: set[str],
    unique_candidate_count: int,
    simulated_fill_count: int,
    missing_price_count: int,
    not_fillable_count: int,
) -> str:
    if not unique_candidate_count:
        return "no_candidates"
    if simulated_fill_count and not missing_price_count and not not_fillable_count:
        return "simulated_from_clob_tape"
    if simulated_fill_count:
        return "partial_fillability_simulated"
    if missing_price_count and "price_path_missing" in statuses:
        return "price_path_missing"
    if not_fillable_count:
        return "not_fillable_at_recorded_book"
    return "review_required"


def _build_postgame_realized_live_item(item: dict[str, Any]) -> dict[str, Any]:
    pnl = item.get("pnl_attribution") if isinstance(item.get("pnl_attribution"), dict) else {}
    reconciliation = item.get("reconciliation") if isinstance(item.get("reconciliation"), dict) else {}
    direct_scope = item.get("direct_event_scope") if isinstance(item.get("direct_event_scope"), dict) else {}
    buckets = pnl.get("buckets") if isinstance(pnl.get("buckets"), list) else []
    unresolved: list[dict[str, Any]] = []
    if item.get("ok") is False:
        unresolved.append({"reason": "pnl_attribution_error", "error": item.get("error")})
    if pnl.get("pnl_attribution_ready") is not True:
        unresolved.append(
            {
                "reason": "pnl_attribution_not_ready",
                "unknown_lifecycle_count": pnl.get("unknown_lifecycle_count"),
                "residual_status": pnl.get("residual_status"),
                "direct_final_flat": pnl.get("direct_final_flat"),
            }
        )
    if direct_scope.get("scoped") is not True:
        unresolved.append(
            {
                "reason": "direct_event_scope_not_confirmed",
                "scope_status": direct_scope.get("status"),
            }
        )

    return {
        "event_id": item.get("event_id") or item.get("event_slug"),
        "event_slug": item.get("event_slug") or item.get("event_id"),
        "status": "ready" if pnl.get("pnl_attribution_ready") is True and not unresolved else "review_required",
        "source_confidence": "account_confirmed" if pnl.get("pnl_attribution_ready") is True else "inferred",
        "account_pnl": {
            "source": "portfolio_order_lifecycle_pnl_attribution_v1",
            "source_confidence": "account_confirmed" if pnl else "inferred",
            "known_cashflow_usd": pnl.get("known_cashflow_usd"),
            "known_fee_usd": pnl.get("known_fee_usd"),
            "direct_collateral_delta_usd": pnl.get("direct_collateral_delta_usd"),
            "residual_cashflow_usd": pnl.get("residual_cashflow_usd"),
            "residual_status": pnl.get("residual_status"),
            "final_winning_outcome_id": pnl.get("final_winning_outcome_id"),
            "pnl_attribution_ready": pnl.get("pnl_attribution_ready"),
        },
        "actor_buckets": buckets,
        "clob_grounding": _build_postgame_clob_grounding(
            reconciliation=reconciliation,
            direct_scope=direct_scope,
        ),
        "lifecycle_summary": {
            "source": "portfolio_order_lifecycle_reconciliation_v1",
            "source_confidence": "db_confirmed",
            "order_count": reconciliation.get("order_count"),
            "linked_trade_count": reconciliation.get("linked_trade_count"),
            "unknown_lifecycle_count": reconciliation.get("unknown_lifecycle_count"),
            "lifecycle_status_counts": reconciliation.get("lifecycle_status_counts"),
        },
        "direct_event_scope": direct_scope,
        "market_tape": {
            "source_confidence": "clob_market_tape",
            "account_pnl_eligible": False,
            "event_scoped_trade_count": direct_scope.get("trade_count"),
            "allowed_uses": ["price_path", "fillability", "liquidity"],
        },
        "unresolved_evidence": unresolved,
    }


def _build_postgame_clob_grounding(
    *,
    reconciliation: dict[str, Any],
    direct_scope: dict[str, Any],
) -> dict[str, Any]:
    rows = reconciliation.get("items") if isinstance(reconciliation.get("items"), list) else []
    fill_rows: list[dict[str, Any]] = []
    external_order_ids: list[str] = []
    direct_trade_ids: list[str] = []
    fill_source_counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        external_order_id = str(row.get("external_order_id") or "").strip()
        if external_order_id:
            external_order_ids.append(external_order_id)
        row_trade_ids = [str(item) for item in row.get("direct_trade_ids") or [] if str(item).strip()]
        direct_trade_ids.extend(row_trade_ids)
        source = str(row.get("fill_evidence_source") or "unknown")
        fill_source_counts[source] = fill_source_counts.get(source, 0) + 1
        fill_size = _safe_float(row.get("effective_fill_size"))
        cashflow = _safe_float(row.get("effective_cashflow_usd"))
        effective_avg_price = None
        if fill_size and fill_size > 0 and cashflow is not None:
            effective_avg_price = round(abs(cashflow) / fill_size, 6)
        fill_rows.append(
            {
                "order_id": row.get("order_id"),
                "external_order_id": row.get("external_order_id"),
                "side": row.get("side"),
                "outcome_id": row.get("outcome_id"),
                "token_id": row.get("token_id"),
                "source_confidence": "account_confirmed"
                if source in {"direct_clob_trades", "local_and_direct_trades"}
                else "db_confirmed",
                "fill_evidence_source": row.get("fill_evidence_source"),
                "effective_fill_size": row.get("effective_fill_size"),
                "effective_cashflow_usd": row.get("effective_cashflow_usd"),
                "effective_fee_usd": row.get("effective_fee_usd"),
                "effective_avg_price": effective_avg_price,
                "direct_fill_size": row.get("direct_fill_size"),
                "direct_cashflow_usd": row.get("direct_cashflow_usd"),
                "direct_fee_usd": row.get("direct_fee_usd"),
                "direct_trade_ids": row_trade_ids,
                "direct_local_fill_mismatch": row.get("direct_local_fill_mismatch"),
            }
        )

    return {
        "schema_version": "postgame_clob_grounding_v1",
        "status": "recorded" if fill_rows else "not_recorded",
        "source_confidence": "account_confirmed" if direct_scope.get("scoped") is True else "inferred",
        "external_order_ids": sorted(set(external_order_ids)),
        "direct_trade_ids": sorted(set(direct_trade_ids)),
        "fill_source_counts": dict(sorted(fill_source_counts.items())),
        "fill_rows": fill_rows,
        "direct_event_scope": {
            "status": direct_scope.get("status"),
            "scoped": direct_scope.get("scoped"),
            "open_order_count": direct_scope.get("open_order_count"),
            "open_position_count": direct_scope.get("open_position_count"),
            "trade_count": direct_scope.get("trade_count"),
        },
        "ui_displayed_price_comparison": _build_ui_displayed_price_comparison(fill_rows),
    }


def _build_ui_displayed_price_comparison(fill_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for row in fill_rows:
        exact_price = _safe_float(row.get("effective_avg_price"))
        if exact_price is None:
            continue
        exact_cents = exact_price * 100.0
        whole_cent = round(exact_cents)
        one_decimal_cent = round(exact_cents, 1)
        fill_size = _safe_float(row.get("effective_fill_size")) or 0.0
        cashflow = _safe_float(row.get("effective_cashflow_usd"))
        notional = abs(cashflow) if cashflow is not None else round(exact_price * fill_size, 6)
        rows.append(
            {
                "order_id": row.get("order_id"),
                "external_order_id": row.get("external_order_id"),
                "side": row.get("side"),
                "token_id": row.get("token_id"),
                "exact_avg_price": round(exact_price, 6),
                "exact_cents": round(exact_cents, 6),
                "estimated_ui_whole_cent_label": f"{int(whole_cent)}c",
                "estimated_ui_one_decimal_cent_label": _cent_label(one_decimal_cent),
                "rounding_delta_to_whole_cent": round(whole_cent - exact_cents, 6),
                "minimum_checks": {
                    "min_size": 5.0,
                    "min_buy_notional_usd": 1.0,
                    "effective_fill_size": row.get("effective_fill_size"),
                    "effective_notional_usd": round(notional, 6),
                    "size_meets_exchange_minimum": fill_size >= 5.0,
                    "notional_meets_exchange_buy_minimum": notional >= 1.0,
                },
                "source_confidence": "account_confirmed",
            }
        )
    if not rows:
        return {
            "schema_version": "postgame_ui_displayed_price_comparison_v1",
            "status": "not_available",
            "source_confidence": "ui_observed",
            "actual_ui_observation_attached": False,
            "reason": "no_account_scoped_fill_rows_for_display_comparison",
            "account_pnl_eligible": False,
        }
    return {
        "schema_version": "postgame_ui_displayed_price_comparison_v1",
        "status": "derived_display_estimates",
        "source_confidence": "inferred",
        "actual_ui_observation_source_confidence": "ui_observed",
        "actual_ui_observation_attached": False,
        "reason": "exact_account_clob_prices_available_but_ui_screenshot_values_not_attached",
        "account_pnl_eligible": False,
        "rounding_policy": {
            "whole_cent_label": "nearest displayed whole-cent estimate; useful for audit only",
            "one_decimal_cent_label": "one-decimal cent estimate for sub-cent/low-price UI audit",
            "accounting_authority": "effective_avg_price from account-scoped direct CLOB/local reconciliation",
        },
        "row_count": len(rows),
        "rows": rows,
    }


def _cent_label(value: float) -> str:
    rounded = round(value, 1)
    if abs(rounded - int(rounded)) < 1e-9:
        return f"{int(rounded)}c"
    return f"{rounded:.1f}c"


def _read_postgame_replay_tick_stream_summary(*, day: str | None, event_id: str) -> dict[str, Any]:
    if not day:
        return {
            "schema_version": "postgame_replay_tick_stream_summary_v1",
            "status": "not_checked",
            "reason": "session_date_missing",
            "event_id": event_id,
            "tick_count": 0,
            "source_confidence": "inferred",
        }
    root = ops_artifact_root(day).parent.parent / "live-strategy-worker" / day
    ticks_path = root / "ticks.jsonl"
    if not ticks_path.exists():
        return {
            "schema_version": "postgame_replay_tick_stream_summary_v1",
            "status": "missing",
            "reason": "ticks_jsonl_missing",
            "event_id": event_id,
            "path": str(ticks_path),
            "tick_count": 0,
            "source_confidence": "inferred",
        }

    summary = {
        "schema_version": "postgame_replay_tick_stream_summary_v1",
        "status": "recorded",
        "event_id": event_id,
        "path": str(ticks_path),
        "source_confidence": "runtime_artifact",
        "tick_count": 0,
        "first_tick_at_utc": None,
        "latest_tick_at_utc": None,
        "intent_count": 0,
        "executed_order_count": 0,
        "order_intent_candidate_count": 0,
        "decision_type_counts": {},
        "blocker_reason_counts": {},
        "sleeves": {},
        "fill_simulation": _empty_postgame_replay_fill_simulation(),
        "missed_window_analysis": _empty_postgame_missed_window_analysis(),
        "_candidate_keys_seen": set(),
        "_latest_bid_by_token": {},
        "_latest_bid_by_outcome": {},
        "_open_positions_by_sleeve_token": {},
        "_price_path_by_token": {},
        "_candidate_windows": [],
        "_token_labels": {},
        "_outcome_to_token": {},
    }
    try:
        with ticks_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    tick = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_payload = _live_worker_tick_event_payload(tick, event_id=event_id)
                if not event_payload:
                    continue
                _accumulate_postgame_replay_tick_summary(summary, tick=tick, event_payload=event_payload)
    except OSError as exc:
        _finalize_postgame_replay_tick_summary(summary)
        return {
            **summary,
            "status": "error",
            "error": str(exc),
        }
    _finalize_postgame_replay_tick_summary(summary)
    if not summary["tick_count"]:
        summary["status"] = "missing"
        summary["reason"] = "event_not_found_in_tick_stream"
        summary["source_confidence"] = "inferred"
    summary["decision_type_counts"] = dict(sorted(summary["decision_type_counts"].items()))
    summary["blocker_reason_counts"] = dict(sorted(summary["blocker_reason_counts"].items()))
    summary["sleeves"] = dict(sorted(summary["sleeves"].items()))
    return summary


def _live_worker_tick_event_payload(tick: dict[str, Any], *, event_id: str) -> dict[str, Any] | None:
    stdout = tick.get("stdout") if isinstance(tick.get("stdout"), dict) else {}
    events = stdout.get("events") if isinstance(stdout.get("events"), list) else []
    for event_payload in events:
        if isinstance(event_payload, dict) and str(event_payload.get("event_id") or "") == event_id:
            return event_payload
    return None


def _empty_postgame_replay_fill_simulation() -> dict[str, Any]:
    return {
        "schema_version": "postgame_replay_fill_simulation_v1",
        "status": "no_candidates",
        "source_confidence": "clob_market_tape",
        "candidate_count": 0,
        "unique_candidate_count": 0,
        "duplicate_candidate_count": 0,
        "simulated_fill_count": 0,
        "not_fillable_count": 0,
        "missing_price_count": 0,
        "unmatched_sell_count": 0,
        "simulated_cashflow_usd": 0.0,
        "simulated_mark_value_usd": 0.0,
        "simulated_pnl_usd": 0.0,
        "dedupe_policy": "event+sleeve+signal_type+token+cycle_or_supporting_signal",
        "mark_policy": "open_buy_inventory_marked_at_latest_recorded_best_bid",
    }


def _empty_postgame_missed_window_analysis() -> dict[str, Any]:
    return {
        "schema_version": "postgame_missed_window_analysis_v1",
        "status": "no_candidates",
        "source_confidence": "clob_market_tape",
        "account_pnl_eligible": False,
        "candidate_window_count": 0,
        "blocked_sleeve_window_count": 0,
        "estimated_missed_value_usd": 0.0,
        "rows": [],
        "blocked_sleeve_rows": [],
        "policy": {
            "value_source": "recorded direct CLOB best ask/bid extrema after candidate windows",
            "blocked_sleeves": "inferred volatility context only unless a token can be mapped from sleeve side",
        },
    }


def _accumulate_postgame_replay_tick_summary(
    summary: dict[str, Any],
    *,
    tick: dict[str, Any],
    event_payload: dict[str, Any],
) -> None:
    summary["tick_count"] += 1
    timestamp = tick.get("finished_at_utc") or tick.get("started_at_utc")
    if timestamp and summary.get("first_tick_at_utc") is None:
        summary["first_tick_at_utc"] = timestamp
    if timestamp:
        summary["latest_tick_at_utc"] = timestamp

    live_execution = event_payload.get("live_execution") if isinstance(event_payload.get("live_execution"), dict) else {}
    summary["intent_count"] += _safe_int(live_execution.get("intent_count"))
    summary["executed_order_count"] += len(live_execution.get("executed_orders") or [])
    for blocker in live_execution.get("blockers") or []:
        if not isinstance(blocker, dict):
            continue
        reason = str(blocker.get("reason") or "unknown")
        summary["blocker_reason_counts"][reason] = summary["blocker_reason_counts"].get(reason, 0) + 1

    aggregation = (
        event_payload.get("live_signal_aggregation")
        if isinstance(event_payload.get("live_signal_aggregation"), dict)
        else {}
    )
    decision = aggregation.get("decision") if isinstance(aggregation.get("decision"), dict) else {}
    decision_type = str(decision.get("decision_type") or "unknown")
    summary["decision_type_counts"][decision_type] = summary["decision_type_counts"].get(decision_type, 0) + 1
    candidates = decision.get("order_intent_candidates") or []
    summary["order_intent_candidate_count"] += len(candidates)
    _update_postgame_replay_latest_books(summary, event_payload=event_payload)
    for candidate in candidates:
        if isinstance(candidate, dict):
            _accumulate_postgame_replay_candidate(summary, candidate=candidate, event_payload=event_payload)

    sleeve_states = live_execution.get("sleeve_states") or event_payload.get("sleeve_states") or []
    for sleeve in sleeve_states:
        if not isinstance(sleeve, dict):
            continue
        row = _postgame_replay_sleeve_summary(summary, sleeve)
        if row is None:
            continue
        row["tick_count"] += 1
        row["intent_count"] += _safe_int(sleeve.get("intent_count"))
        row["blocker_count"] += _safe_int(sleeve.get("blocker_count"))
        blocker_reasons = row["blocker_reasons"]
        for reason in sleeve.get("blocker_reasons") or []:
            reason_text = str(reason)
            if reason_text not in blocker_reasons:
                blocker_reasons.append(reason_text)


def _postgame_replay_sleeve_summary(summary: dict[str, Any], source: dict[str, Any]) -> dict[str, Any] | None:
    sleeve_id = str(source.get("sleeve_id") or source.get("strategy_id") or "").strip()
    if not sleeve_id:
        return None
    row = summary["sleeves"].setdefault(
        sleeve_id,
        {
            "sleeve_id": sleeve_id,
            "strategy_id": source.get("strategy_id"),
            "sleeve_role": source.get("sleeve_role"),
            "sleeve_side": source.get("sleeve_side") or source.get("side"),
            "strategy_family": source.get("strategy_family"),
            "tick_count": 0,
            "intent_count": 0,
            "blocker_count": 0,
            "blocker_reasons": [],
            "fill_simulation": _empty_postgame_replay_fill_simulation(),
        },
    )
    for key in ("strategy_id", "sleeve_role", "sleeve_side", "strategy_family"):
        if row.get(key) in (None, "") and source.get(key):
            row[key] = source.get(key)
    if row.get("sleeve_side") in (None, "") and source.get("side"):
        row["sleeve_side"] = source.get("side")
    return row


def _update_postgame_replay_latest_books(summary: dict[str, Any], *, event_payload: dict[str, Any]) -> None:
    market_state = event_payload.get("market_state") if isinstance(event_payload.get("market_state"), dict) else {}
    for sampled in market_state.get("sampled_outcomes") or []:
        if not isinstance(sampled, dict):
            continue
        token_id = str(sampled.get("token_id") or "")
        outcome_id = str(sampled.get("outcome_id") or "")
        label = str(sampled.get("outcome_label") or "").strip()
        if token_id and label:
            summary["_token_labels"][token_id] = label
        if outcome_id and token_id:
            summary["_outcome_to_token"][outcome_id] = token_id
    for token_id, token_state in (market_state.get("token_states") or {}).items():
        if not isinstance(token_state, dict):
            continue
        bid = _safe_float(token_state.get("best_bid"))
        if bid is not None:
            summary["_latest_bid_by_token"][str(token_id)] = bid
        _accumulate_postgame_price_path_point(summary, token_id=str(token_id), state=token_state)
    outcome_states = market_state.get("outcome_states") or {}
    if isinstance(outcome_states, dict):
        iterable = outcome_states.items()
    elif isinstance(outcome_states, list):
        iterable = ((item.get("outcome_id") or item.get("id"), item) for item in outcome_states if isinstance(item, dict))
    else:
        iterable = []
    for outcome_id, outcome_state in iterable:
        if not outcome_id or not isinstance(outcome_state, dict):
            continue
        bid = _safe_float(outcome_state.get("best_bid"))
        if bid is not None:
            summary["_latest_bid_by_outcome"][str(outcome_id)] = bid
        token_id = summary["_outcome_to_token"].get(str(outcome_id))
        if token_id:
            _accumulate_postgame_price_path_point(summary, token_id=str(token_id), state=outcome_state)


def _accumulate_postgame_price_path_point(summary: dict[str, Any], *, token_id: str, state: dict[str, Any]) -> None:
    if not token_id:
        return
    best_ask = _safe_float(state.get("best_ask"))
    best_bid = _safe_float(state.get("best_bid"))
    if best_ask is None and best_bid is None:
        return
    path = summary["_price_path_by_token"].setdefault(token_id, [])
    path.append(
        {
            "tick_index": summary.get("tick_count", 0),
            "best_ask": best_ask,
            "best_bid": best_bid,
        }
    )


def _accumulate_postgame_replay_candidate(
    summary: dict[str, Any],
    *,
    candidate: dict[str, Any],
    event_payload: dict[str, Any],
) -> None:
    summary_sim = summary["fill_simulation"]
    summary_sim["candidate_count"] += 1
    sleeve = _postgame_replay_sleeve_summary(summary, candidate)
    sleeve_sim = sleeve["fill_simulation"] if sleeve is not None else None
    if sleeve_sim is not None:
        sleeve_sim["candidate_count"] += 1

    candidate_key = _postgame_replay_candidate_key(candidate)
    seen = summary.setdefault("_candidate_keys_seen", set())
    if candidate_key in seen:
        summary_sim["duplicate_candidate_count"] += 1
        if sleeve_sim is not None:
            sleeve_sim["duplicate_candidate_count"] += 1
        return
    seen.add(candidate_key)
    summary_sim["unique_candidate_count"] += 1
    if sleeve_sim is not None:
        sleeve_sim["unique_candidate_count"] += 1

    fill = _postgame_replay_candidate_fill(candidate, event_payload=event_payload)
    _record_postgame_candidate_window(
        summary,
        candidate=candidate,
        fill=fill,
        candidate_key=candidate_key,
    )
    if fill["status"] == "missing_price":
        summary_sim["missing_price_count"] += 1
        if sleeve_sim is not None:
            sleeve_sim["missing_price_count"] += 1
        return
    if fill["status"] == "not_fillable":
        summary_sim["not_fillable_count"] += 1
        if sleeve_sim is not None:
            sleeve_sim["not_fillable_count"] += 1
        return
    if fill["status"] == "unsupported_signal_type":
        summary_sim["not_fillable_count"] += 1
        if sleeve_sim is not None:
            sleeve_sim["not_fillable_count"] += 1
        return

    signal_type = str(candidate.get("signal_type") or "").lower()
    token_id = str(candidate.get("market_token_id") or candidate.get("token_id") or "")
    outcome_id = str(candidate.get("outcome_id") or "")
    shares = fill["shares"]
    price = fill["price"]
    if signal_type in {"buy", "rebuy"}:
        cashflow = -(price * shares)
        _postgame_replay_apply_cashflow(summary_sim, cashflow)
        if sleeve_sim is not None:
            _postgame_replay_apply_cashflow(sleeve_sim, cashflow)
        summary_sim["simulated_fill_count"] += 1
        if sleeve_sim is not None:
            sleeve_sim["simulated_fill_count"] += 1
        position_key = _postgame_replay_position_key(candidate)
        position = summary["_open_positions_by_sleeve_token"].setdefault(
            position_key,
            {"shares": 0.0, "token_id": token_id, "outcome_id": outcome_id, "sleeve_id": candidate.get("sleeve_id")},
        )
        position["shares"] += shares
        return

    if signal_type in {"sell", "exit", "reduce"}:
        position_key = _postgame_replay_position_key(candidate)
        position = summary["_open_positions_by_sleeve_token"].get(position_key)
        available_shares = _safe_float(position.get("shares")) if isinstance(position, dict) else None
        if not available_shares:
            summary_sim["unmatched_sell_count"] += 1
            if sleeve_sim is not None:
                sleeve_sim["unmatched_sell_count"] += 1
            return
        filled_shares = min(shares, available_shares)
        cashflow = price * filled_shares
        position["shares"] = max(0.0, available_shares - filled_shares)
        _postgame_replay_apply_cashflow(summary_sim, cashflow)
        if sleeve_sim is not None:
            _postgame_replay_apply_cashflow(sleeve_sim, cashflow)
        summary_sim["simulated_fill_count"] += 1
        if sleeve_sim is not None:
            sleeve_sim["simulated_fill_count"] += 1


def _postgame_replay_candidate_key(candidate: dict[str, Any]) -> str:
    support = candidate.get("cycle_id") or ",".join(str(item) for item in candidate.get("supporting_signal_ids") or [])
    if not support:
        support = ",".join(str(item) for item in candidate.get("reason_codes") or []) or "candidate"
    return "|".join(
        [
            str(candidate.get("event_id") or ""),
            str(candidate.get("sleeve_id") or candidate.get("strategy_id") or ""),
            str(candidate.get("signal_type") or ""),
            str(candidate.get("market_token_id") or candidate.get("token_id") or candidate.get("outcome_id") or ""),
            support,
        ]
    )


def _postgame_replay_position_key(candidate: dict[str, Any]) -> str:
    return "|".join(
        [
            str(candidate.get("sleeve_id") or candidate.get("strategy_id") or ""),
            str(candidate.get("market_token_id") or candidate.get("token_id") or candidate.get("outcome_id") or ""),
        ]
    )


def _record_postgame_candidate_window(
    summary: dict[str, Any],
    *,
    candidate: dict[str, Any],
    fill: dict[str, Any],
    candidate_key: str,
) -> None:
    token_id = str(candidate.get("market_token_id") or candidate.get("token_id") or "")
    outcome_id = str(candidate.get("outcome_id") or "")
    if not token_id and outcome_id:
        token_id = str(summary.get("_outcome_to_token", {}).get(outcome_id) or "")
    summary["_candidate_windows"].append(
        {
            "candidate_key": candidate_key,
            "tick_index": summary.get("tick_count", 0),
            "event_id": candidate.get("event_id"),
            "sleeve_id": candidate.get("sleeve_id") or candidate.get("strategy_id"),
            "strategy_id": candidate.get("strategy_id"),
            "sleeve_role": candidate.get("sleeve_role"),
            "signal_type": candidate.get("signal_type"),
            "side": candidate.get("side"),
            "token_id": token_id,
            "outcome_id": outcome_id,
            "max_price": _safe_float(candidate.get("max_price") or candidate.get("limit_price") or candidate.get("price")),
            "min_price": _safe_float(candidate.get("min_price") or candidate.get("limit_price") or candidate.get("target_price")),
            "requested_shares": fill.get("shares")
            or _safe_float(candidate.get("requested_shares") or candidate.get("shares") or candidate.get("size")),
            "fill_status": fill.get("status"),
            "reference_price": fill.get("price"),
        }
    )


def _postgame_replay_candidate_fill(candidate: dict[str, Any], *, event_payload: dict[str, Any]) -> dict[str, Any]:
    signal_type = str(candidate.get("signal_type") or "").lower()
    if signal_type not in {"buy", "rebuy", "sell", "exit", "reduce"}:
        return {"status": "unsupported_signal_type"}
    book = _postgame_replay_candidate_book(candidate, event_payload=event_payload)
    if not book:
        return {"status": "missing_price"}
    price_field = "best_ask" if signal_type in {"buy", "rebuy"} else "best_bid"
    price = _safe_float(book.get(price_field))
    if price is None:
        return {"status": "missing_price"}
    shares = _safe_float(candidate.get("requested_shares") or candidate.get("shares") or candidate.get("size"))
    notional = _safe_float(candidate.get("requested_notional_usd") or candidate.get("notional_usd"))
    if shares is None and notional is not None and price > 0:
        shares = notional / price
    if shares is None or shares <= 0:
        return {"status": "missing_price"}
    if signal_type in {"buy", "rebuy"}:
        max_price = _safe_float(candidate.get("max_price") or candidate.get("limit_price") or candidate.get("price"))
        if max_price is not None and price > max_price:
            return {"status": "not_fillable", "price": price, "shares": shares}
    else:
        min_price = _safe_float(
            candidate.get("min_price")
            or candidate.get("limit_price")
            or candidate.get("target_price")
            or candidate.get("price")
        )
        if min_price is not None and price < min_price:
            return {"status": "not_fillable", "price": price, "shares": shares}
    return {"status": "fill", "price": price, "shares": shares}


def _postgame_replay_candidate_book(candidate: dict[str, Any], *, event_payload: dict[str, Any]) -> dict[str, Any] | None:
    outcome_id = str(candidate.get("outcome_id") or "")
    token_id = str(candidate.get("market_token_id") or candidate.get("token_id") or "")
    orderbook_results = event_payload.get("orderbook_results") if isinstance(event_payload.get("orderbook_results"), dict) else {}
    if outcome_id and isinstance(orderbook_results.get(outcome_id), dict):
        return orderbook_results[outcome_id]
    market_state = event_payload.get("market_state") if isinstance(event_payload.get("market_state"), dict) else {}
    token_states = market_state.get("token_states") if isinstance(market_state.get("token_states"), dict) else {}
    if token_id and isinstance(token_states.get(token_id), dict):
        return token_states[token_id]
    outcome_states = market_state.get("outcome_states")
    if isinstance(outcome_states, dict) and outcome_id and isinstance(outcome_states.get(outcome_id), dict):
        return outcome_states[outcome_id]
    if isinstance(outcome_states, list):
        for item in outcome_states:
            if isinstance(item, dict) and str(item.get("outcome_id") or item.get("id") or "") == outcome_id:
                return item
    return None


def _postgame_replay_apply_cashflow(simulation: dict[str, Any], cashflow: float) -> None:
    simulation["simulated_cashflow_usd"] = round(
        (_safe_float(simulation.get("simulated_cashflow_usd")) or 0.0) + cashflow,
        6,
    )


def _finalize_postgame_replay_tick_summary(summary: dict[str, Any]) -> None:
    for position in summary.get("_open_positions_by_sleeve_token", {}).values():
        if not isinstance(position, dict):
            continue
        shares = _safe_float(position.get("shares")) or 0.0
        if shares <= 0:
            continue
        token_id = str(position.get("token_id") or "")
        outcome_id = str(position.get("outcome_id") or "")
        mark_price = summary.get("_latest_bid_by_token", {}).get(token_id)
        if mark_price is None:
            mark_price = summary.get("_latest_bid_by_outcome", {}).get(outcome_id)
        if mark_price is None:
            summary["fill_simulation"]["missing_price_count"] += 1
            sleeve = summary["sleeves"].get(str(position.get("sleeve_id") or ""))
            if isinstance(sleeve, dict):
                sleeve["fill_simulation"]["missing_price_count"] += 1
            continue
        mark_value = shares * float(mark_price)
        summary["fill_simulation"]["simulated_mark_value_usd"] = round(
            (_safe_float(summary["fill_simulation"].get("simulated_mark_value_usd")) or 0.0) + mark_value,
            6,
        )
        sleeve = summary["sleeves"].get(str(position.get("sleeve_id") or ""))
        if isinstance(sleeve, dict):
            sleeve["fill_simulation"]["simulated_mark_value_usd"] = round(
                (_safe_float(sleeve["fill_simulation"].get("simulated_mark_value_usd")) or 0.0) + mark_value,
                6,
            )
    _finalize_postgame_replay_simulation(summary["fill_simulation"])
    for sleeve in (summary.get("sleeves") or {}).values():
        if isinstance(sleeve, dict):
            _finalize_postgame_replay_simulation(sleeve["fill_simulation"])
    _finalize_postgame_missed_window_analysis(summary)
    for key in (
        "_candidate_keys_seen",
        "_latest_bid_by_token",
        "_latest_bid_by_outcome",
        "_open_positions_by_sleeve_token",
        "_price_path_by_token",
        "_candidate_windows",
        "_token_labels",
        "_outcome_to_token",
    ):
        summary.pop(key, None)


def _finalize_postgame_replay_simulation(simulation: dict[str, Any]) -> None:
    unique_candidate_count = _safe_int(simulation.get("unique_candidate_count"))
    simulated_fill_count = _safe_int(simulation.get("simulated_fill_count"))
    missing_price_count = _safe_int(simulation.get("missing_price_count"))
    not_fillable_count = _safe_int(simulation.get("not_fillable_count"))
    cashflow = _safe_float(simulation.get("simulated_cashflow_usd")) or 0.0
    mark_value = _safe_float(simulation.get("simulated_mark_value_usd")) or 0.0
    if missing_price_count and simulated_fill_count:
        simulation["simulated_pnl_usd"] = None
    else:
        simulation["simulated_pnl_usd"] = round(cashflow + mark_value, 6)
    simulation["status"] = _combined_postgame_replay_simulation_status(
        statuses={"price_path_missing"} if missing_price_count else set(),
        unique_candidate_count=unique_candidate_count,
        simulated_fill_count=simulated_fill_count,
        missing_price_count=missing_price_count,
        not_fillable_count=not_fillable_count,
    )


def _finalize_postgame_missed_window_analysis(summary: dict[str, Any]) -> None:
    analysis = summary["missed_window_analysis"]
    rows: list[dict[str, Any]] = []
    for candidate in summary.get("_candidate_windows", []):
        if not isinstance(candidate, dict):
            continue
        row = _postgame_candidate_missed_window_row(summary, candidate)
        if row is not None:
            rows.append(row)
    blocked_rows = _postgame_blocked_sleeve_window_rows(summary)
    estimated_value = sum(_safe_float(row.get("estimated_missed_value_usd")) or 0.0 for row in rows)
    rows.sort(key=lambda item: _safe_float(item.get("estimated_missed_value_usd")) or 0.0, reverse=True)
    analysis.update(
        {
            "status": "estimated" if rows or blocked_rows else "no_candidates",
            "candidate_window_count": len(summary.get("_candidate_windows", [])),
            "blocked_sleeve_window_count": len(blocked_rows),
            "estimated_missed_value_usd": round(estimated_value, 6),
            "rows": rows[:20],
            "blocked_sleeve_rows": blocked_rows[:20],
        }
    )


def _postgame_candidate_missed_window_row(summary: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any] | None:
    signal_type = str(candidate.get("signal_type") or "").lower()
    if signal_type not in {"buy", "rebuy"}:
        return None
    token_id = str(candidate.get("token_id") or "")
    path = [
        point
        for point in summary.get("_price_path_by_token", {}).get(token_id, [])
        if _safe_int(point.get("tick_index")) >= _safe_int(candidate.get("tick_index"))
    ]
    if not path:
        return {
            "event_id": candidate.get("event_id"),
            "sleeve_id": candidate.get("sleeve_id"),
            "signal_type": candidate.get("signal_type"),
            "side": candidate.get("side") or summary.get("_token_labels", {}).get(token_id),
            "status": "price_path_missing",
            "source_confidence": "inferred",
            "account_pnl_eligible": False,
            "estimated_missed_value_usd": None,
        }
    bid_values = [_safe_float(point.get("best_bid")) for point in path]
    ask_values = [_safe_float(point.get("best_ask")) for point in path]
    bids = [value for value in bid_values if value is not None]
    asks = [value for value in ask_values if value is not None]
    max_bid = max(bids) if bids else None
    min_ask = min(asks) if asks else None
    shares = _safe_float(candidate.get("requested_shares")) or 0.0
    entry_price = _safe_float(candidate.get("reference_price"))
    status_text = str(candidate.get("fill_status") or "unknown")
    reason = "missed_exit_extrema_after_candidate"
    if status_text == "not_fillable":
        max_price = _safe_float(candidate.get("max_price"))
        if min_ask is None or max_price is None or min_ask > max_price:
            return None
        entry_price = min_ask
        reason = "missed_entry_became_fillable_later"
    if entry_price is None or max_bid is None or shares <= 0:
        return None
    value = max(0.0, (max_bid - entry_price) * shares)
    if value <= 0:
        return None
    return {
        "event_id": candidate.get("event_id"),
        "sleeve_id": candidate.get("sleeve_id"),
        "strategy_id": candidate.get("strategy_id"),
        "sleeve_role": candidate.get("sleeve_role"),
        "signal_type": candidate.get("signal_type"),
        "side": candidate.get("side") or summary.get("_token_labels", {}).get(token_id),
        "token_id": token_id,
        "reason": reason,
        "candidate_fill_status": status_text,
        "entry_price": round(entry_price, 6),
        "later_max_bid": round(max_bid, 6),
        "later_min_ask": round(min_ask, 6) if min_ask is not None else None,
        "requested_shares": shares,
        "estimated_missed_value_usd": round(value, 6),
        "source_confidence": "clob_market_tape",
        "account_pnl_eligible": False,
    }


def _postgame_blocked_sleeve_window_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    label_to_token = {str(label).lower(): token for token, label in summary.get("_token_labels", {}).items()}
    for sleeve_id, sleeve in (summary.get("sleeves") or {}).items():
        if not isinstance(sleeve, dict) or not _safe_int(sleeve.get("blocker_count")):
            continue
        side_label = str(sleeve.get("sleeve_side") or "").lower()
        token_id = label_to_token.get(side_label)
        path = summary.get("_price_path_by_token", {}).get(token_id or "", [])
        bids = [_safe_float(point.get("best_bid")) for point in path]
        asks = [_safe_float(point.get("best_ask")) for point in path]
        bids = [bid for bid in bids if bid is not None]
        asks = [ask for ask in asks if ask is not None]
        if not bids and not asks:
            continue
        min_ask = min(asks) if asks else None
        max_bid = max(bids) if bids else None
        range_cents = None
        if min_ask is not None and max_bid is not None:
            range_cents = round(max(0.0, max_bid - min_ask) * 100.0, 4)
        rows.append(
            {
                "sleeve_id": sleeve_id,
                "strategy_id": sleeve.get("strategy_id"),
                "sleeve_role": sleeve.get("sleeve_role"),
                "side": sleeve.get("sleeve_side"),
                "token_id": token_id,
                "blocker_count": sleeve.get("blocker_count"),
                "blocker_reasons": sleeve.get("blocker_reasons") or [],
                "min_recorded_ask": min_ask,
                "max_recorded_bid": max_bid,
                "recorded_range_cents": range_cents,
                "source_confidence": "inferred" if token_id else "runtime_artifact",
                "account_pnl_eligible": False,
            }
        )
    rows.sort(key=lambda item: _safe_float(item.get("recorded_range_cents")) or 0.0, reverse=True)
    return rows


def _event_scoped_order_lifecycle_direct_context(
    direct_context: dict[str, Any],
    *,
    event_id: str,
    day: str | None,
) -> dict[str, Any]:
    direct_evidence = direct_context.get("direct_evidence") if isinstance(direct_context.get("direct_evidence"), dict) else {}
    raw_orders = list(direct_evidence.get("open_orders") or [])
    raw_positions = list(direct_evidence.get("open_positions") or [])
    raw_trades = list(direct_context.get("direct_trade_rows") or direct_evidence.get("trades") or [])
    plan, plan_event_id, lookup_event_ids = load_current_strategy_plan_for_event(event_id, day=day)
    if not isinstance(plan, dict):
        return {
            **direct_context,
            "direct_event_scope": {
                "schema_version": "postgame_direct_event_scope_v1",
                "status": "missing_current_strategy_plan",
                "event_id": event_id,
                "lookup_event_ids": lookup_event_ids,
                "open_order_count": direct_context.get("direct_open_order_count"),
                "open_position_count": direct_context.get("direct_open_position_count"),
                "trade_count": len(raw_trades),
                "scoped": False,
            },
        }

    token_ids = set(_strategy_plan_token_ids(plan))
    event_slugs = set(_strategy_plan_event_slugs(plan))
    if not token_ids and not event_slugs:
        return {
            **direct_context,
            "direct_event_scope": {
                "schema_version": "postgame_direct_event_scope_v1",
                "status": "scope_keys_missing",
                "event_id": event_id,
                "plan_event_id": plan_event_id,
                "lookup_event_ids": lookup_event_ids,
                "open_order_count": direct_context.get("direct_open_order_count"),
                "open_position_count": direct_context.get("direct_open_position_count"),
                "trade_count": len(raw_trades),
                "scoped": False,
            },
        }

    condition_ids = _direct_condition_ids_for_event_scope(
        raw_orders=raw_orders,
        raw_positions=raw_positions,
        raw_trades=raw_trades,
        token_ids=token_ids,
        event_slugs=event_slugs,
    )
    open_orders = [
        order
        for order in raw_orders
        if _direct_item_matches_current_event(order, token_ids=token_ids, condition_ids=condition_ids, event_slugs=event_slugs)
    ]
    open_positions = [
        position
        for position in raw_positions
        if _direct_item_matches_current_event(
            position,
            token_ids=token_ids,
            condition_ids=condition_ids,
            event_slugs=event_slugs,
        )
    ]
    trades = [
        trade
        for trade in raw_trades
        if _direct_item_matches_current_event(trade, token_ids=token_ids, condition_ids=condition_ids, event_slugs=event_slugs)
    ]
    open_order_ids = [
        external_id
        for external_id in (_direct_item_external_id(order) for order in open_orders)
        if external_id is not None
    ]
    scoped_evidence = {
        **direct_evidence,
        "open_order_external_ids": open_order_ids,
        "open_order_count": len(open_orders),
        "open_position_count": len(open_positions),
        "trade_count": len(trades),
        "open_orders": open_orders,
        "open_positions": open_positions,
        "trades": trades,
    }
    return {
        **direct_context,
        "direct_open_order_external_ids": open_order_ids,
        "direct_open_order_count": len(open_orders),
        "direct_open_position_count": len(open_positions),
        "direct_trade_rows": trades,
        "direct_evidence": scoped_evidence,
        "direct_event_scope": {
            "schema_version": "postgame_direct_event_scope_v1",
            "status": "scoped",
            "event_id": event_id,
            "plan_event_id": plan_event_id,
            "lookup_event_ids": lookup_event_ids,
            "token_ids": sorted(token_ids),
            "condition_ids": sorted(condition_ids),
            "event_slugs": sorted(event_slugs),
            "open_order_count": len(open_orders),
            "open_position_count": len(open_positions),
            "trade_count": len(trades),
            "scoped": True,
        },
    }


def _direct_item_external_id(item: Any) -> str | None:
    if not isinstance(item, dict):
        item = getattr(item, "__dict__", {}) or {}
    for key in ("id", "order_id", "external_order_id", "external_id", "hash"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


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
        day=resolved_day,
    )
    postgame_evaluation = _build_postgame_evaluation(
        day=resolved_day,
        reviewed_event_ids=[event_id],
        strategy_plan_gate={
            "status": "ready" if strategy_plan_versions.get("current_exists") else "review_required",
            "ready": bool(strategy_plan_versions.get("current_exists")),
        },
        postgame_live_evidence=postgame_live_evidence,
        portfolio_pnl_attribution=portfolio_pnl_attribution,
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
            "postgame_evaluation": postgame_evaluation,
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
            "active_open_position_count": 0,
            "documented_residual_position_count": 0,
            "trade_count": 0,
            "unresolved_inventory_present": False,
        }
    raw_orders = (direct_clob.get("open_orders") or {}).get("orders") or []
    raw_positions = (direct_clob.get("open_positions") or {}).get("positions") or []
    raw_trades = (direct_clob.get("current_token_trades") or {}).get("trades") or []
    items: list[dict[str, Any]] = []
    total_open_orders = 0
    total_open_positions = 0
    total_active_open_positions = 0
    total_documented_residual_positions = 0
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
                    "active_open_position_count": 0,
                    "documented_residual_position_count": 0,
                    "trade_count": 0,
                    "unresolved_inventory_present": False,
                }
            )
            continue
        token_ids = set(_strategy_plan_token_ids(plan))
        event_slugs = set(_strategy_plan_event_slugs(plan))
        condition_ids = _direct_condition_ids_for_event_scope(
            raw_orders=raw_orders,
            raw_positions=raw_positions,
            raw_trades=raw_trades,
            token_ids=token_ids,
            event_slugs=event_slugs,
        )
        open_orders = [
            to_jsonable(order)
            for order in raw_orders
            if _direct_item_matches_current_event(order, token_ids=token_ids, condition_ids=condition_ids, event_slugs=event_slugs)
        ]
        open_positions = [
            to_jsonable(position)
            for position in raw_positions
            if _direct_item_matches_current_event(
                position, token_ids=token_ids, condition_ids=condition_ids, event_slugs=event_slugs
            )
        ]
        trades = [
            to_jsonable(trade)
            for trade in raw_trades
            if _direct_item_matches_current_event(trade, token_ids=token_ids, condition_ids=condition_ids, event_slugs=event_slugs)
        ]
        open_order_count = len(open_orders)
        open_position_count = len(open_positions)
        trade_count = len(trades)
        residual_review = classify_documented_residual_positions(
            open_orders=open_orders,
            open_positions=open_positions,
        )
        active_open_positions = residual_review["active_open_positions"]
        documented_residual_positions = residual_review["documented_residual_positions"]
        blocked_residual_classifications = residual_review["blocked_residual_classifications"]
        active_open_position_count = len(active_open_positions)
        total_open_orders += open_order_count
        total_open_positions += open_position_count
        total_active_open_positions += active_open_position_count
        total_documented_residual_positions += len(documented_residual_positions)
        total_trades += trade_count
        items.append(
            {
                "event_id": event_id,
                "plan_event_id": plan_event_id,
                "status": "recorded",
                "token_ids": sorted(token_ids),
                "condition_ids": sorted(condition_ids),
                "event_slugs": sorted(event_slugs),
                "open_order_count": open_order_count,
                "open_position_count": open_position_count,
                "active_open_position_count": active_open_position_count,
                "documented_residual_position_count": len(documented_residual_positions),
                "trade_count": trade_count,
                "unresolved_inventory_present": bool(open_order_count or active_open_position_count),
                "open_orders": open_orders,
                "open_positions": open_positions,
                "active_open_positions": active_open_positions,
                "documented_residual_positions": documented_residual_positions,
                "blocked_residual_classifications": blocked_residual_classifications,
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
        "active_open_position_count": total_active_open_positions,
        "documented_residual_position_count": total_documented_residual_positions,
        "trade_count": total_trades,
        "unresolved_inventory_present": bool(total_open_orders or total_active_open_positions),
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


def _strategy_plan_event_slugs(plan: dict[str, Any]) -> list[str]:
    context = plan.get("context_summary") if isinstance(plan.get("context_summary"), dict) else {}
    raw_values = [context.get("event_slug"), plan.get("event_slug")]
    return _normalized_unique_values([str(value) for value in raw_values if value])


def _direct_condition_ids_for_event_scope(
    *,
    raw_orders: list[Any],
    raw_positions: list[Any],
    raw_trades: list[Any],
    token_ids: set[str],
    event_slugs: set[str],
) -> set[str]:
    condition_ids: set[str] = set()
    for item in [*raw_orders, *raw_positions, *raw_trades]:
        token_id = _direct_item_token_id(item)
        event_slug = _direct_item_event_slug(item)
        if token_id not in token_ids and (not event_slug or event_slug not in event_slugs):
            continue
        condition_id = _direct_item_condition_id(item)
        if condition_id:
            condition_ids.add(condition_id)
    return condition_ids


def _direct_item_matches_current_event(
    item: Any,
    *,
    token_ids: set[str],
    condition_ids: set[str],
    event_slugs: set[str],
) -> bool:
    token_id = _direct_item_token_id(item)
    if token_id and token_id in token_ids:
        return True
    condition_id = _direct_item_condition_id(item)
    if condition_id and condition_id in condition_ids:
        return True
    event_slug = _direct_item_event_slug(item)
    return bool(event_slug and event_slug in event_slugs)


def _direct_item_token_id(item: Any) -> str:
    if not isinstance(item, dict):
        item = getattr(item, "__dict__", {}) or {}
    for key in ("token_id", "asset_id", "asset", "outcomeTokenId", "clobTokenId"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _direct_item_condition_id(item: Any) -> str:
    if not isinstance(item, dict):
        item = getattr(item, "__dict__", {}) or {}
    for key in ("condition_id", "conditionId", "market"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _direct_item_event_slug(item: Any) -> str:
    if not isinstance(item, dict):
        item = getattr(item, "__dict__", {}) or {}
    value = item.get("event_slug")
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
