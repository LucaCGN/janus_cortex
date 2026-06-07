from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
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
from app.data.nodes.polymarket.gamma.gamma_client import PolymarketDataClient
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
from app.modules.agentic.runtime_control import (
    event_control_root,
    event_control_to_aggregation_control,
    load_event_control_config,
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
    read_json,
    record_ops_stage,
    session_date,
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
    janus_live_event_states = _build_janus_live_event_states(
        session_date=payload.session_date,
        event_ids=live_monitor_event_ids,
        ops_status=ops_status,
        integrity=integrity,
        strategy_plan_gate=strategy_plan_gate,
        llm_runtime_status=llm_runtime_status,
        live_strategy_worker_status=live_strategy_worker_status,
        live_execution_evidence=live_execution_evidence,
        live_microstructure_context=live_microstructure_context,
        current_event_inventory=current_event_inventory,
        live_monitor_readiness=live_monitor_readiness,
    )
    janus_live_event_state_records = _persist_janus_live_event_states(
        janus_live_event_states,
        day=payload.session_date,
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
            "janus_live_event_states": janus_live_event_states,
            "janus_live_event_state_records": janus_live_event_state_records,
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
        "janus_live_event_states": janus_live_event_states,
        "janus_live_event_state_records": janus_live_event_state_records,
    }


@router.get("/ops/live-strategy-worker/status")
def get_ops_live_strategy_worker_status() -> dict[str, Any]:
    return get_live_strategy_worker().status()


@router.get("/events/{event_id}/worker-restart-readiness")
def get_event_worker_restart_readiness(event_id: str, session_date: str | None = None) -> dict[str, Any]:
    resolved_event_ids = _resolve_current_plan_event_ids([event_id], day=session_date)
    resolved_event_id = resolved_event_ids[0] if resolved_event_ids else event_id
    strategy_plan_gate = _build_strategy_plan_gate(resolved_event_ids or [event_id], day=session_date)
    live_strategy_worker_status = build_live_strategy_worker_readiness(
        session_date=session_date,
        event_ids=resolved_event_ids or [event_id],
        strategy_plan_gate=strategy_plan_gate,
    )
    latest_path = _janus_live_event_state_latest_path(resolved_event_id, day=session_date)
    latest_state = read_json(latest_path) or {}
    canonical_restart_state = (
        latest_state.get("worker_restart_safety_state")
        if isinstance(latest_state.get("worker_restart_safety_state"), dict)
        else {}
    )
    current_restart_state = _build_worker_restart_safety_state(
        live_strategy_worker_status,
        event_id=resolved_event_id,
    )
    blocker_reasons = set(str(item) for item in current_restart_state.get("blocker_reasons") or [] if item)
    if strategy_plan_gate.get("blocker_reason"):
        blocker_reasons.add(str(strategy_plan_gate["blocker_reason"]))
    status_value = (
        "restart_ready_current_scope"
        if current_restart_state.get("safe_to_restart_without_explicit_payload")
        else "restart_blocked_explicit_current_payload_required"
    )
    return {
        "schema_version": "janus_event_worker_restart_readiness_v1",
        "schema_contract": _janus_event_worker_restart_readiness_schema_contract(),
        "requested_event_id": event_id,
        "event_id": resolved_event_id,
        "session_date": session_date,
        "status": status_value,
        "source": "live_strategy_worker_readiness",
        "source_schema": current_restart_state.get("source_schema"),
        "latest_live_event_state_path": str(latest_path),
        "latest_live_event_state_present": bool(latest_state),
        "worker_restart_safety_state": current_restart_state,
        "canonical_worker_restart_safety_state": canonical_restart_state,
        "strategy_plan_gate": strategy_plan_gate,
        "live_strategy_worker_status": live_strategy_worker_status,
        "blocker_reasons": sorted(blocker_reasons),
        "readback_endpoints": _janus_worker_restart_readiness_readback_endpoints(
            resolved_event_id,
            day=session_date,
        ),
        "order_endpoint_call_allowed": False,
        "execution_authority": False,
    }


@router.get("/events/{event_id}/live-state")
def get_event_live_state(event_id: str, session_date: str | None = None) -> dict[str, Any]:
    path = _janus_live_event_state_latest_path(event_id, day=session_date)
    state = read_json(path)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason": "janus_live_event_state_missing",
                "event_id": event_id,
                "session_date": session_date,
                "expected_path": str(path),
            },
        )
    return state


@router.get("/events/{event_id}/live-state/history")
def get_event_live_state_history(
    event_id: str,
    session_date: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    latest_path = _janus_live_event_state_latest_path(event_id, day=session_date)
    history_path = latest_path.parent / "history.jsonl"
    if not history_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason": "janus_live_event_state_history_missing",
                "event_id": event_id,
                "session_date": session_date,
                "expected_path": str(history_path),
            },
        )
    readback = _read_janus_live_event_state_history(history_path, limit=limit)
    return {
        "schema_version": "janus_live_event_state_history_readback_v1",
        "schema_contract": _janus_live_event_state_history_schema_contract(),
        "event_id": event_id,
        "session_date": session_date,
        "latest_path": str(latest_path),
        "history_path": str(history_path),
        **readback,
        "execution_authority": False,
    }


@router.get("/events/{event_id}/quarter-revisions")
def get_event_quarter_revisions(event_id: str, session_date: str | None = None) -> dict[str, Any]:
    llm_runtime_status = load_latest_llm_runtime_status(
        session_date=session_date,
        event_ids=[event_id],
    )
    item = _find_event_state_item(llm_runtime_status.get("items") or [], event_id)
    if not isinstance(item, dict):
        item = {}
    quarter_revision_state = _build_quarter_revision_state(llm_runtime_status, event_id=event_id)
    reviews = [review for review in item.get("quarter_revision_reviews") or [] if isinstance(review, dict)]
    artifacts = [
        artifact
        for artifact in item.get("quarter_revision_review_artifacts") or []
        if isinstance(artifact, dict)
    ]
    resolved_session_date = str(llm_runtime_status.get("session_date") or session_date or "")
    return {
        "schema_version": "janus_event_quarter_revision_readback_v1",
        "schema_contract": _janus_event_quarter_revision_readback_schema_contract(),
        "event_id": event_id,
        "session_date": resolved_session_date,
        "status": quarter_revision_state.get("status") or "not_recorded",
        "source": "llm_runtime_status",
        "source_schema": "strategy_plan_quarter_revision_review_v1",
        "llm_runtime_status": {
            "status": llm_runtime_status.get("status"),
            "event_count": llm_runtime_status.get("event_count"),
            "recorded_event_count": llm_runtime_status.get("recorded_event_count"),
            "artifact_root": llm_runtime_status.get("artifact_root"),
            "item_status": item.get("status"),
            "trace_artifact_path": item.get("path"),
        },
        "quarter_revision_state": quarter_revision_state,
        "quarter_revision_reviews": reviews,
        "quarter_revision_review_artifacts": artifacts,
        "artifact_paths": quarter_revision_state.get("artifact_paths") or [],
        "readback_endpoints": _janus_quarter_revision_readback_endpoints(
            event_id,
            day=resolved_session_date,
        ),
        "order_endpoint_call_allowed": False,
        "execution_authority": False,
    }


@router.get("/events/{event_id}/db-stat-context")
def get_event_db_stat_context(event_id: str, session_date: str | None = None) -> dict[str, Any]:
    try:
        context = build_event_agent_context(event_id, day=session_date)
        db_context_state = _db_stat_context_state_from_context(event_id, context)
        context_error = None
    except Exception as exc:  # noqa: BLE001
        context = {}
        db_context_state = _db_stat_context_error_state(event_id, exc)
        context_error = str(exc)
    trace = context.get("db_stat_context_trace") if isinstance(context.get("db_stat_context_trace"), dict) else {}
    sources = [
        source
        for source in (trace.get("db_context_sources") or context.get("db_context_sources") or [])
        if isinstance(source, dict)
    ]
    resolved_session_date = str(session_date or "")
    return {
        "schema_version": "janus_event_db_stat_context_readback_v1",
        "schema_contract": _janus_event_db_stat_context_readback_schema_contract(),
        "event_id": event_id,
        "session_date": resolved_session_date,
        "status": db_context_state.get("status") or "not_recorded",
        "source": "event_agent_context",
        "source_schema": trace.get("schema_version") or "db_stat_context_trace_v1",
        "resolved_strategy_plan_event_id": context.get("resolved_strategy_plan_event_id"),
        "strategy_plan_lookup_event_ids": context.get("strategy_plan_lookup_event_ids") or [],
        "db_stat_context_state": db_context_state,
        "db_stat_context_trace": trace,
        "db_context_sources": sources,
        "readback_endpoints": _janus_db_stat_context_readback_endpoints(event_id, day=resolved_session_date),
        "context_error": context_error,
        "order_endpoint_call_allowed": False,
        "execution_authority": False,
    }


@router.get("/events/{event_id}/signal-blockers")
def get_event_signal_blockers(event_id: str, session_date: str | None = None) -> dict[str, Any]:
    latest_path = _janus_live_event_state_latest_path(event_id, day=session_date)
    latest_state = read_json(latest_path)
    signal_state = (
        latest_state.get("signal_aggregation_state")
        if isinstance(latest_state, dict) and isinstance(latest_state.get("signal_aggregation_state"), dict)
        else {}
    )
    source = "janus_live_event_state_latest_json" if signal_state else "missing"
    source_path = str(latest_path)
    latest_tick_state = (
        latest_state.get("latest_live_worker_tick_state")
        if isinstance(latest_state, dict) and isinstance(latest_state.get("latest_live_worker_tick_state"), dict)
        else {}
    )

    if not signal_state or signal_state.get("status") in {None, "not_recorded_by_live_monitor"}:
        latest_tick_state = _latest_live_worker_tick_state(day=session_date, event_id=event_id)
        tick_signal_state = (
            latest_tick_state.get("signal_aggregation_state")
            if isinstance(latest_tick_state.get("signal_aggregation_state"), dict)
            else {}
        )
        if tick_signal_state:
            signal_state = tick_signal_state
            source = "live_strategy_worker_tick_history_fallback"
            source_path = str(latest_tick_state.get("path") or source_path)

    return {
        "schema_version": "janus_event_signal_blocker_readback_v1",
        "schema_contract": _janus_event_signal_blocker_readback_schema_contract(),
        "event_id": event_id,
        "session_date": session_date,
        "status": signal_state.get("status") or "not_recorded",
        "source": source,
        "source_path": source_path,
        "source_schema": signal_state.get("schema_version") or "janus_live_event_state_signal_aggregation_v1",
        "latest_tick_state": {
            "status": latest_tick_state.get("status"),
            "path": latest_tick_state.get("path"),
            "tick_started_at_utc": latest_tick_state.get("tick_started_at_utc"),
            "tick_finished_at_utc": latest_tick_state.get("tick_finished_at_utc"),
        },
        "signal_aggregation_state": signal_state,
        "blocker_scope_rows": signal_state.get("blocker_scope_rows") or [],
        "blocker_reason_counts": signal_state.get("blocker_reason_counts") or {},
        "blocker_scope_counts": signal_state.get("blocker_scope_counts") or {},
        "expected_next_action_counts": signal_state.get("expected_next_action_counts") or {},
        "signal_count": _safe_int(signal_state.get("signal_count")),
        "order_intent_candidate_count": _safe_int(signal_state.get("order_intent_candidate_count")),
        "blocker_count": _safe_int(signal_state.get("blocker_count")),
        "candidate_blocking_blocker_count": _safe_int(signal_state.get("candidate_blocking_blocker_count")),
        "nonblocking_local_blocker_count": _safe_int(signal_state.get("nonblocking_local_blocker_count")),
        "readback_endpoints": _janus_signal_blocker_readback_endpoints(event_id, day=session_date),
        "order_endpoint_call_allowed": False,
        "execution_authority": False,
    }


@router.get("/events/{event_id}/budget-state")
def get_event_budget_state(event_id: str, session_date: str | None = None) -> dict[str, Any]:
    latest_path = _janus_live_event_state_latest_path(event_id, day=session_date)
    latest_state = read_json(latest_path)
    budget_state = (
        latest_state.get("budget_state")
        if isinstance(latest_state, dict) and isinstance(latest_state.get("budget_state"), dict)
        else {}
    )
    source = "janus_live_event_state_latest_json" if budget_state else "missing"
    source_path = str(latest_path)
    latest_tick_state = (
        latest_state.get("latest_live_worker_tick_state")
        if isinstance(latest_state, dict) and isinstance(latest_state.get("latest_live_worker_tick_state"), dict)
        else {}
    )
    if not budget_state or budget_state.get("status") in {None, "not_recorded_by_live_monitor"}:
        latest_tick_state = _latest_live_worker_tick_state(day=session_date, event_id=event_id)
        tick_budget_state = (
            latest_tick_state.get("budget_state")
            if isinstance(latest_tick_state.get("budget_state"), dict)
            else {}
        )
        if tick_budget_state:
            budget_state = tick_budget_state
            source = "live_strategy_worker_tick_history_fallback"
            source_path = str(latest_tick_state.get("path") or source_path)

    risk_promotion = (
        budget_state.get("risk_promotion_evidence")
        if isinstance(budget_state.get("risk_promotion_evidence"), dict)
        else {}
    )
    profit_addon_policy = (
        risk_promotion.get("profit_addon_policy")
        if isinstance(risk_promotion.get("profit_addon_policy"), dict)
        else {}
    )
    return {
        "schema_version": "janus_event_budget_readback_v1",
        "schema_contract": _janus_event_budget_readback_schema_contract(),
        "event_id": event_id,
        "session_date": session_date,
        "status": budget_state.get("status") or "not_recorded",
        "source": source,
        "source_path": source_path,
        "source_schema": budget_state.get("schema_version") or "janus_live_event_state_budget_state_v1",
        "latest_tick_state": {
            "status": latest_tick_state.get("status"),
            "path": latest_tick_state.get("path"),
            "tick_started_at_utc": latest_tick_state.get("tick_started_at_utc"),
            "tick_finished_at_utc": latest_tick_state.get("tick_finished_at_utc"),
        },
        "budget_state": budget_state,
        "risk_promotion_evidence": risk_promotion,
        "profit_addon_policy": profit_addon_policy,
        "profit_addon_policy_schema": profit_addon_policy.get("schema_version"),
        "event_cap_before_profit_addon_usd": budget_state.get("event_cap_before_profit_addon_usd"),
        "event_cap_usd": budget_state.get("event_cap_usd"),
        "remaining_notional_usd": budget_state.get("remaining_notional_usd"),
        "profit_ratcheted_requested_addon_usd": budget_state.get("profit_ratcheted_requested_addon_usd"),
        "profit_ratcheted_addon_usd": budget_state.get("profit_ratcheted_addon_usd"),
        "profit_ratcheted_blocked_addon_usd": budget_state.get("profit_ratcheted_blocked_addon_usd"),
        "risk_promotion_allowed": risk_promotion.get("risk_promotion_allowed"),
        "risk_promotion_source_confidence": risk_promotion.get("source_confidence"),
        "risk_promotion_blocker_codes": risk_promotion.get("blocker_codes") or [],
        "readback_endpoints": _janus_budget_readback_endpoints(event_id, day=session_date),
        "order_endpoint_call_allowed": False,
        "execution_authority": False,
    }


def _build_janus_live_event_states(
    *,
    session_date: str | None,
    event_ids: list[str],
    ops_status: dict[str, Any],
    integrity: dict[str, Any],
    strategy_plan_gate: dict[str, Any],
    llm_runtime_status: dict[str, Any],
    live_strategy_worker_status: dict[str, Any],
    live_execution_evidence: dict[str, Any],
    live_microstructure_context: dict[str, Any],
    current_event_inventory: dict[str, Any],
    live_monitor_readiness: dict[str, Any],
) -> list[dict[str, Any]]:
    generated_at = datetime.now(timezone.utc).isoformat()
    states: list[dict[str, Any]] = []
    for event_id in _normalized_unique_values(event_ids):
        plan_state = _find_event_state_item(strategy_plan_gate.get("current_plans") or [], event_id)
        inventory_state = _find_event_state_item(current_event_inventory.get("items") or [], event_id)
        microstructure_state = _find_event_state_item(live_microstructure_context.get("items") or [], event_id)
        latest_tick_state = _latest_live_worker_tick_state(day=session_date, event_id=event_id)
        db_stat_context_state = _build_db_stat_context_state(event_id, day=session_date)
        event_controls_state = _build_event_controls_state(event_id, day=session_date)
        budget_state = _budget_state_with_cap_readback(
            latest_tick_state.get("budget_state") or {"status": "not_recorded_by_live_monitor"},
            event_controls_state=event_controls_state,
            worker_state=live_strategy_worker_status,
        )
        direct_target_coverage_state = _direct_target_coverage_state(inventory_state)
        target_management_state = _target_management_state_with_direct_coverage(
            latest_tick_state.get("target_management_state"),
            direct_target_coverage_state=direct_target_coverage_state,
        )
        reduce_stop_state = _reduce_stop_state_with_direct_coverage(
            latest_tick_state.get("reduce_stop_state"),
            direct_target_coverage_state=direct_target_coverage_state,
        )
        strategy_review_escalation_state = _build_strategy_review_escalation_state(
            llm_runtime_status,
            event_id=event_id,
        )
        blockers = _build_janus_live_event_state_blockers(
            event_id=event_id,
            strategy_plan_gate=strategy_plan_gate,
            live_strategy_worker_status=live_strategy_worker_status,
            live_monitor_readiness=live_monitor_readiness,
            inventory_state=inventory_state,
            strategy_review_escalation_state=strategy_review_escalation_state,
        )
        states.append(
            {
                "schema_version": "janus_live_event_state_v1",
                "schema_contract": _janus_live_event_state_schema_contract(),
                "generated_at_utc": generated_at,
                "session_date": session_date,
                "event_identity": {
                    "event_id": event_id,
                    "requested_event_ids": event_ids,
                    "strategy_plan_event_id": (plan_state or {}).get("event_id"),
                },
                "readback_endpoints": _janus_live_event_state_readback_endpoints(
                    event_id,
                    day=session_date,
                ),
                "execution_authority": False,
                "health_only_not_executor": True,
                "latest_live_worker_tick_state": latest_tick_state,
                "ops_status": {
                    "status": ops_status.get("status"),
                    "timestamp_utc": ops_status.get("timestamp_utc"),
                    "current_plan_count_today": ((ops_status.get("strategy_plans") or {}).get("current_plan_count_today")),
                },
                "scoreboard_resolution": latest_tick_state.get("scoreboard_resolution")
                or {
                    "status": "not_recorded_by_live_monitor",
                    "execution_blocking_if_required": True,
                },
                "game_state": latest_tick_state.get("game_state") or {"status": "not_recorded_by_live_monitor"},
                "market_state": {
                    "status": "recorded" if microstructure_state else "not_recorded",
                    "microstructure": microstructure_state,
                },
                "direct_clob_account_state": {
                    "status": (inventory_state or {}).get("status") or current_event_inventory.get("status"),
                    "summary": {
                        "open_order_count": (inventory_state or {}).get("open_order_count", 0),
                        "open_position_count": (inventory_state or {}).get("open_position_count", 0),
                        "active_open_position_count": (inventory_state or {}).get("active_open_position_count", 0),
                        "trusted_trade_count": (inventory_state or {}).get("trusted_trade_count", 0),
                        "untrusted_trade_count": (inventory_state or {}).get("untrusted_trade_count", 0),
                        "all_observed_trade_count": (inventory_state or {}).get("all_observed_trade_count", 0),
                        "unresolved_inventory_present": bool(
                            (inventory_state or {}).get("unresolved_inventory_present")
                        ),
                    },
                    "event_inventory": inventory_state,
                    "integrity_ready_for_live_minimum_orders": integrity.get("ready_for_live_minimum_orders"),
                },
                "strategy_plan_state": plan_state
                or {
                    "status": "missing_current_strategy_plan",
                    "event_id": event_id,
                    "blocker_reason": "missing_current_strategy_plan",
                },
                "event_controls_state": event_controls_state,
                "worker_state": live_strategy_worker_status,
                "worker_restart_safety_state": _build_worker_restart_safety_state(
                    live_strategy_worker_status,
                    event_id=event_id,
                ),
                "llm_runtime_state": llm_runtime_status,
                "strategy_review_escalation_state": strategy_review_escalation_state,
                "db_stat_context_state": db_stat_context_state,
                "quarter_revision_state": _build_quarter_revision_state(
                    llm_runtime_status,
                    event_id=event_id,
                ),
                "signal_aggregation_state": latest_tick_state.get("signal_aggregation_state")
                or {"status": "not_recorded_by_live_monitor"},
                "sleeve_state": (plan_state or {}).get("sleeves") or [],
                "budget_state": budget_state,
                "order_lifecycle_state": {
                    "status": "current_event_inventory_recorded" if inventory_state else "not_recorded",
                    "open_order_count": (inventory_state or {}).get("open_order_count", 0),
                    "open_position_count": (inventory_state or {}).get("open_position_count", 0),
                },
                "target_management_state": target_management_state,
                "reduce_stop_state": reduce_stop_state,
                "manual_interference_state": {
                    "status": "observed_from_direct_account_inventory" if inventory_state else "not_recorded",
                    "untrusted_trade_count": (inventory_state or {}).get("untrusted_trade_count", 0),
                },
                "post_call_reconciliation_state": {
                    "status": "not_applicable",
                    "reason": "live_monitor_does_not_submit_orders",
                },
                "live_execution_evidence": live_execution_evidence,
                "blockers": blockers,
                "freshness_status": {
                    "status": live_monitor_readiness.get("status"),
                    "gate": live_monitor_readiness.get("gate"),
                    "ready_for_live_execution": live_monitor_readiness.get("ready_for_live_execution"),
                    "generated_at_utc": generated_at,
                },
                "latest_artifact_paths": [],
            }
        )
    return states


def _build_event_controls_state(event_id: str, *, day: str | None) -> dict[str, Any]:
    try:
        config = load_event_control_config(event_id, day=day)
        control_path = event_control_root(day) / _ops_artifact_safe_name(event_id) / "current.json"
        artifact_present = control_path.exists()
        config_payload = config.model_dump(mode="json")
        aggregation_control = event_control_to_aggregation_control(config).model_dump(mode="json")
        blocker_reasons: list[str] = []
        if not artifact_present:
            blocker_reasons.append("event_control_current_artifact_missing")
        if not bool(config.enabled):
            blocker_reasons.append("event_control_disabled")
        return {
            "schema_version": "janus_live_event_state_event_controls_state_v1",
            "status": "recorded" if artifact_present else "default_missing_current_artifact",
            "event_id": event_id,
            "session_date": config.session_date,
            "current_artifact_present": artifact_present,
            "current_artifact_path": str(control_path),
            "enabled": bool(config.enabled),
            "updated_at_utc": config_payload.get("updated_at_utc"),
            "updated_by": config_payload.get("updated_by"),
            "source": config_payload.get("source"),
            "reason": config_payload.get("reason"),
            "signal_source_toggles": config_payload.get("signal_source_toggles") or {},
            "parameters": config_payload.get("parameters") or {},
            "stop_with_positions_policy": config_payload.get("stop_with_positions_policy") or {},
            "aggregation_control": aggregation_control,
            "blocker_reasons": blocker_reasons,
            "order_endpoint_call_allowed": False,
            "execution_authority": False,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "schema_version": "janus_live_event_state_event_controls_state_v1",
            "status": "unavailable",
            "event_id": event_id,
            "blocker_reasons": [f"event_control_read_failed:{type(exc).__name__}"],
            "order_endpoint_call_allowed": False,
            "execution_authority": False,
        }


def _latest_live_worker_tick_state(*, day: str | None, event_id: str) -> dict[str, Any]:
    ticks_path = ops_artifact_root(day).parent.parent / "live-strategy-worker" / session_date(day) / "ticks.jsonl"
    if not ticks_path.exists():
        return {
            "schema_version": "janus_live_event_state_live_tick_readback_v1",
            "status": "missing",
            "event_id": event_id,
            "path": str(ticks_path),
            "reason": "live_strategy_worker_ticks_jsonl_missing",
            "execution_authority": False,
        }

    latest_tick: dict[str, Any] | None = None
    latest_event_payload: dict[str, Any] | None = None
    try:
        with ticks_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    tick = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(tick, dict):
                    continue
                event_payload = _live_worker_tick_event_payload(tick, event_id=event_id)
                if event_payload is None:
                    continue
                latest_tick = tick
                latest_event_payload = event_payload
    except OSError as exc:
        return {
            "schema_version": "janus_live_event_state_live_tick_readback_v1",
            "status": "error",
            "event_id": event_id,
            "path": str(ticks_path),
            "error": str(exc),
            "execution_authority": False,
        }

    if latest_event_payload is None or latest_tick is None:
        return {
            "schema_version": "janus_live_event_state_live_tick_readback_v1",
            "status": "missing",
            "event_id": event_id,
            "path": str(ticks_path),
            "reason": "event_not_found_in_live_strategy_worker_ticks",
            "execution_authority": False,
        }

    return {
        "schema_version": "janus_live_event_state_live_tick_readback_v1",
        "status": "recorded",
        "event_id": event_id,
        "path": str(ticks_path),
        "tick_started_at_utc": latest_tick.get("started_at_utc"),
        "tick_finished_at_utc": latest_tick.get("finished_at_utc"),
        "scoreboard_resolution": _live_tick_scoreboard_resolution(latest_event_payload),
        "game_state": _live_tick_game_state(latest_event_payload),
        "signal_aggregation_state": _live_tick_signal_aggregation_state(latest_event_payload),
        "budget_state": _live_tick_budget_state(latest_event_payload),
        "target_management_state": _live_tick_target_management_state(latest_event_payload),
        "reduce_stop_state": _live_tick_reduce_stop_state(latest_event_payload),
        "execution_authority": False,
    }


def _live_tick_scoreboard_resolution(event_payload: dict[str, Any]) -> dict[str, Any]:
    market_state = event_payload.get("market_state") if isinstance(event_payload.get("market_state"), dict) else {}
    game = _live_tick_game_payload(event_payload, market_state)
    normalized = event_payload.get("normalized_live_snapshot")
    if not isinstance(normalized, dict):
        normalized = market_state.get("normalized_live_snapshot") if isinstance(market_state.get("normalized_live_snapshot"), dict) else {}
    for candidate in (
        game.get("scoreboard_resolution"),
        market_state.get("scoreboard_resolution"),
        normalized.get("scoreboard_resolution") if isinstance(normalized, dict) else None,
    ):
        if isinstance(candidate, dict):
            return {
                **candidate,
                "status": candidate.get("status") or ("resolved" if candidate.get("selected_game_id") else "recorded"),
                "source": "latest_live_strategy_worker_tick",
            }
    if game:
        selected_game_id = _first_non_empty(game, keys=("selected_game_id", "game_id", "gameId"))
        resolved = bool(game.get("resolved") or selected_game_id)
        return {
            "schema_version": "scoreboard_resolution_diagnostics_v1",
            "status": "resolved" if resolved else "recorded",
            "source": "latest_live_strategy_worker_tick",
            "source_feed": _first_non_empty(game, keys=("source",)),
            "resolution_source": _first_non_empty(game, keys=("resolution_source",)),
            "selected_game_id": selected_game_id,
            "event_id": _first_non_empty(game, keys=("event_id",)),
            "league": _first_non_empty(game, keys=("league",)),
            "game_date": _first_non_empty(game, keys=("game_date",)),
            "game_start_time": _first_non_empty(game, keys=("game_start_time", "gameTimeUTC", "gameEt")),
            "game_status": _first_non_empty(game, keys=("game_status", "status", "gameStatus")),
            "game_status_text": _first_non_empty(game, keys=("game_status_text", "gameStatusText")),
            "execution_blocking_if_required": not resolved,
        }
    return {
        "status": "not_recorded_by_latest_live_tick",
        "source": "latest_live_strategy_worker_tick",
        "execution_blocking_if_required": True,
    }


def _live_tick_game_state(event_payload: dict[str, Any]) -> dict[str, Any]:
    market_state = event_payload.get("market_state") if isinstance(event_payload.get("market_state"), dict) else {}
    game = _live_tick_game_payload(event_payload, market_state)
    live_state = market_state.get("live_state") if isinstance(market_state.get("live_state"), dict) else {}
    normalized = event_payload.get("normalized_live_snapshot")
    if not isinstance(normalized, dict):
        normalized = market_state.get("normalized_live_snapshot") if isinstance(market_state.get("normalized_live_snapshot"), dict) else {}
    return {
        "schema_version": "janus_live_event_state_game_state_v1",
        "status": "recorded" if game or live_state or normalized else "not_recorded_by_latest_live_tick",
        "source": "latest_live_strategy_worker_tick",
        "period": _first_non_empty(game, live_state, normalized, keys=("period", "game_period")),
        "clock": _first_non_empty(game, live_state, normalized, keys=("clock", "game_clock")),
        "game_status": _first_non_empty(game, live_state, normalized, keys=("status", "game_status", "state")),
        "game_status_text": _first_non_empty(game, live_state, normalized, keys=("game_status_text", "status_text")),
        "game_start_time": _first_non_empty(game, live_state, normalized, keys=("game_start_time", "gameTimeUTC", "gameEt")),
        "home_team": _first_non_empty(
            game,
            live_state,
            normalized,
            keys=("home_team", "home_team_name", "home_team_tricode", "home"),
        ),
        "away_team": _first_non_empty(
            game,
            live_state,
            normalized,
            keys=("away_team", "away_team_name", "away_team_tricode", "away"),
        ),
        "home_score": _first_non_empty(game, live_state, normalized, keys=("home_score", "home_points")),
        "away_score": _first_non_empty(game, live_state, normalized, keys=("away_score", "away_points")),
        "score_gap": _first_non_empty(game, live_state, normalized, keys=("score_gap", "abs_score_gap")),
        "feed_stale": bool(_first_non_empty(game, live_state, normalized, keys=("feed_stale", "stale"))),
    }


def _live_tick_game_payload(event_payload: dict[str, Any], market_state: dict[str, Any]) -> dict[str, Any]:
    for candidate in (event_payload.get("game"), market_state.get("game")):
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _first_non_empty(*sources: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return value
    return None


def _live_tick_signal_aggregation_state(event_payload: dict[str, Any]) -> dict[str, Any]:
    aggregation = event_payload.get("live_signal_aggregation") if isinstance(event_payload.get("live_signal_aggregation"), dict) else {}
    decision = aggregation.get("decision") if isinstance(aggregation.get("decision"), dict) else {}
    blockers = [item for item in decision.get("blocker_artifacts") or [] if isinstance(item, dict)]
    blocker_reason_counts: dict[str, int] = {}
    blocker_scope_counts: dict[str, int] = {}
    expected_next_action_counts: dict[str, int] = {}
    blocker_scope_rows: list[dict[str, Any]] = []
    candidate_blocking_count = 0
    nonblocking_local_count = 0
    for blocker in blockers:
        reason = str(blocker.get("reason_code") or "unknown")
        blocker_reason_counts[reason] = blocker_reason_counts.get(reason, 0) + 1
        detail = blocker.get("detail") if isinstance(blocker.get("detail"), dict) else {}
        scope = str(detail.get("scope") or "unknown")
        blocker_scope_counts[scope] = blocker_scope_counts.get(scope, 0) + 1
        next_action = str(detail.get("expected_next_action") or "unknown")
        expected_next_action_counts[next_action] = expected_next_action_counts.get(next_action, 0) + 1
        candidate_blocking = detail.get("candidate_blocking") is not False
        blocker_scope_rows.append(
            {
                "schema_version": str(detail.get("blocker_scope_schema_version") or "live_signal_blocker_scope_v1"),
                "reason_code": reason,
                "scope": scope,
                "candidate_blocking": candidate_blocking,
                "expected_next_action": next_action,
                "affected_sleeve_ids": [
                    str(item) for item in detail.get("affected_sleeve_ids") or [] if item is not None
                ],
                "candidate_sleeve_ids": [
                    str(item) for item in detail.get("candidate_sleeve_ids") or [] if item is not None
                ],
                "unaffected_sleeve_ids": [
                    str(item) for item in detail.get("unaffected_sleeve_ids") or [] if item is not None
                ],
                "trigger_id": detail.get("trigger_id"),
                "trigger_type": detail.get("trigger_type"),
                "trigger_source": detail.get("trigger_source"),
                "affected_signal_types": [
                    str(item) for item in detail.get("affected_signal_types") or [] if item is not None
                ],
            }
        )
        if detail.get("candidate_blocking") is False:
            if scope in {"local_sleeve", "signal"}:
                nonblocking_local_count += 1
        else:
            candidate_blocking_count += 1
    return {
        "schema_version": "janus_live_event_state_signal_aggregation_v1",
        "status": "recorded" if decision else "not_recorded_by_latest_live_tick",
        "source": "latest_live_strategy_worker_tick",
        "decision_type": decision.get("decision_type"),
        "signal_count": aggregation.get("signal_count"),
        "selected_signal_count": len(decision.get("selected_signal_ids") or []),
        "suppressed_signal_count": len(decision.get("suppressed_signal_ids") or []),
        "order_intent_candidate_count": len(decision.get("order_intent_candidates") or []),
        "blocker_count": len(blockers),
        "candidate_blocking_blocker_count": candidate_blocking_count,
        "nonblocking_local_blocker_count": nonblocking_local_count,
        "blocker_reason_counts": dict(sorted(blocker_reason_counts.items())),
        "blocker_scope_counts": dict(sorted(blocker_scope_counts.items())),
        "expected_next_action_counts": dict(sorted(expected_next_action_counts.items())),
        "blocker_scope_rows": blocker_scope_rows,
        "minimum_order_policy_summary": _live_tick_minimum_order_policy_summary(decision),
        "persistence": aggregation.get("persistence") if isinstance(aggregation.get("persistence"), dict) else {},
    }


def _live_tick_minimum_order_policy_summary(decision: dict[str, Any]) -> dict[str, Any]:
    candidate_policies: list[dict[str, Any]] = []
    blocker_policies: list[dict[str, Any]] = []
    for candidate in decision.get("order_intent_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        policy = candidate.get("minimum_order_policy")
        if isinstance(policy, dict) and policy:
            candidate_policies.append(policy)
    for blocker in decision.get("blocker_artifacts") or []:
        if not isinstance(blocker, dict):
            continue
        detail = blocker.get("detail") if isinstance(blocker.get("detail"), dict) else {}
        policy = detail.get("minimum_order_policy") if isinstance(detail.get("minimum_order_policy"), dict) else {}
        if policy:
            blocker_policies.append(policy)

    policies = [*candidate_policies, *blocker_policies]
    rule_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    order_type_counts: dict[str, int] = {}
    share_minimum_applies_count = 0
    notional_minimum_applies_count = 0
    for policy in policies:
        rule = str(policy.get("order_type_minimum_rule") or "unknown")
        rule_counts[rule] = rule_counts.get(rule, 0) + 1
        status_value = str(policy.get("minimum_rule_status") or "unknown")
        status_counts[status_value] = status_counts.get(status_value, 0) + 1
        order_type = str(policy.get("order_type") or "unknown")
        order_type_counts[order_type] = order_type_counts.get(order_type, 0) + 1
        if policy.get("share_minimum_applies"):
            share_minimum_applies_count += 1
        if policy.get("notional_minimum_applies"):
            notional_minimum_applies_count += 1

    return {
        "schema_version": "janus_live_event_state_minimum_order_policy_summary_v1",
        "schema_contract": _janus_live_event_state_minimum_order_policy_schema_contract(),
        "status": "recorded" if policies else "not_recorded_by_latest_live_tick",
        "source": "latest_live_strategy_worker_tick.live_signal_aggregation.decision",
        "policy_count": len(policies),
        "candidate_policy_count": len(candidate_policies),
        "blocker_policy_count": len(blocker_policies),
        "order_type_minimum_rule_counts": dict(sorted(rule_counts.items())),
        "minimum_rule_status_counts": dict(sorted(status_counts.items())),
        "order_type_counts": dict(sorted(order_type_counts.items())),
        "share_minimum_applies_count": share_minimum_applies_count,
        "notional_minimum_applies_count": notional_minimum_applies_count,
        "execution_authority": False,
    }


def _janus_live_event_state_minimum_order_policy_schema_contract() -> dict[str, Any]:
    return {
        "schema_version": "janus_live_event_state_minimum_order_policy_summary_contract_v1",
        "primary_schema": "janus_live_event_state_minimum_order_policy_summary_v1",
        "source_schema": "polymarket_order_type_minimum_policy_v1",
        "execution_authority": False,
        "required_sections": [
            "status",
            "source",
            "policy_count",
            "candidate_policy_count",
            "blocker_policy_count",
            "order_type_minimum_rule_counts",
            "minimum_rule_status_counts",
            "order_type_counts",
            "share_minimum_applies_count",
            "notional_minimum_applies_count",
            "execution_authority",
        ],
        "consumer_notes": [
            "Limit order evidence should use limit_min_shares and share_minimum_applies_count.",
            "Market order evidence should use market_min_notional and notional_minimum_applies_count.",
            "This summary is read-only evidence and never authorizes order submission.",
        ],
    }


def _live_tick_budget_state(event_payload: dict[str, Any]) -> dict[str, Any]:
    aggregation = event_payload.get("live_signal_aggregation") if isinstance(event_payload.get("live_signal_aggregation"), dict) else {}
    budget = aggregation.get("event_risk_budget") if isinstance(aggregation.get("event_risk_budget"), dict) else {}
    if not budget:
        return {"status": "not_recorded_by_latest_live_tick", "source": "latest_live_strategy_worker_tick"}
    return {
        "schema_version": "janus_live_event_state_budget_state_v1",
        "status": "recorded",
        "source": "latest_live_strategy_worker_tick",
        "event_cap_usd": budget.get("event_cap_usd"),
        "base_event_cap_usd": budget.get("base_event_cap_usd"),
        "event_cap_before_profit_addon_usd": budget.get("event_cap_before_profit_addon_usd"),
        "remaining_notional_usd": budget.get("remaining_notional_usd"),
        "used_notional_usd": budget.get("used_notional_usd"),
        "budget_status": budget.get("budget_status"),
        "profit_ratcheted_requested_addon_usd": budget.get("profit_ratcheted_requested_addon_usd"),
        "profit_ratcheted_addon_usd": budget.get("profit_ratcheted_addon_usd"),
        "profit_ratcheted_blocked_addon_usd": budget.get("profit_ratcheted_blocked_addon_usd"),
        "risk_promotion_evidence": budget.get("risk_promotion_evidence"),
        "manual_interference_budget_rebase": budget.get("manual_interference_budget_rebase"),
    }


def _live_tick_target_management_state(event_payload: dict[str, Any]) -> dict[str, Any]:
    state = _live_tick_nested_state(
        event_payload,
        section_key="target_management",
        source_schema="sports_live_target_management_evidence_v1",
    )
    if state:
        return state
    return {
        "status": "not_recorded_by_latest_live_tick",
        "source": "latest_live_strategy_worker_tick",
        "execution_authority": False,
    }


def _live_tick_reduce_stop_state(event_payload: dict[str, Any]) -> dict[str, Any]:
    state = _live_tick_nested_state(
        event_payload,
        section_key="reduce_stop_lifecycle",
        source_schema="sports_live_reduce_stop_lifecycle_evidence_v1",
    )
    if state:
        return state
    return {
        "status": "not_recorded_by_latest_live_tick",
        "source": "latest_live_strategy_worker_tick",
        "execution_authority": False,
    }


def _live_tick_nested_state(
    event_payload: dict[str, Any],
    *,
    section_key: str,
    source_schema: str,
) -> dict[str, Any] | None:
    market_state = event_payload.get("market_state") if isinstance(event_payload.get("market_state"), dict) else {}
    portfolio_state = event_payload.get("portfolio_state") if isinstance(event_payload.get("portfolio_state"), dict) else {}
    aggregation = (
        event_payload.get("live_signal_aggregation")
        if isinstance(event_payload.get("live_signal_aggregation"), dict)
        else {}
    )
    for candidate in (
        event_payload.get(section_key),
        market_state.get(section_key),
        portfolio_state.get(section_key),
        aggregation.get(section_key),
    ):
        if isinstance(candidate, dict):
            return {
                **candidate,
                "status": candidate.get("status") or "recorded",
                "source": "latest_live_strategy_worker_tick",
                "source_schema": candidate.get("schema_version") or source_schema,
                "execution_authority": False,
            }
    return None


def _budget_state_with_cap_readback(
    budget_state: dict[str, Any] | None,
    *,
    event_controls_state: dict[str, Any],
    worker_state: dict[str, Any],
) -> dict[str, Any]:
    state = dict(budget_state or {})
    state.setdefault("schema_version", "janus_live_event_state_budget_state_v1")
    state.setdefault("source", state.get("source") or "latest_live_strategy_worker_tick")
    configured_cap = _configured_event_cap_usd(
        event_controls_state=event_controls_state,
        worker_state=worker_state,
    )
    base_cap = _safe_float(state.get("base_event_cap_usd"))
    effective_cap = _safe_float(state.get("event_cap_usd"))
    state["configured_event_cap_usd"] = configured_cap
    state["effective_event_cap_usd"] = effective_cap
    state["cap_readback_status"] = _budget_cap_readback_status(
        configured_cap_usd=configured_cap,
        base_event_cap_usd=base_cap,
        effective_event_cap_usd=effective_cap,
    )
    if configured_cap is not None and effective_cap is not None:
        state["effective_minus_configured_cap_usd"] = round(effective_cap - configured_cap, 6)
    else:
        state["effective_minus_configured_cap_usd"] = None
    state["consumer_note"] = (
        "configured_event_cap_usd is the operator/event-control cap; "
        "event_cap_usd/effective_event_cap_usd may be lowered by accounted exposure or raised by verified profit add-ons."
    )
    state["execution_authority"] = False
    return state


def _configured_event_cap_usd(
    *,
    event_controls_state: dict[str, Any],
    worker_state: dict[str, Any],
) -> float | None:
    control_parameters = (
        event_controls_state.get("parameters")
        if isinstance(event_controls_state.get("parameters"), dict)
        else {}
    )
    aggregation_control = (
        event_controls_state.get("aggregation_control")
        if isinstance(event_controls_state.get("aggregation_control"), dict)
        else {}
    )
    for candidate in (
        control_parameters.get("event_cap_usd"),
        aggregation_control.get("event_cap_usd"),
        _nested_value(worker_state, ("config", "event_cap_usd")),
        _nested_value(worker_state, ("config", "parameters", "event_cap_usd")),
        _nested_value(worker_state, ("worker_config", "event_cap_usd")),
        _nested_value(worker_state, ("effective_config", "event_cap_usd")),
    ):
        value = _safe_float(candidate)
        if value is not None:
            return value
    return None


def _nested_value(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _budget_cap_readback_status(
    *,
    configured_cap_usd: float | None,
    base_event_cap_usd: float | None,
    effective_event_cap_usd: float | None,
) -> str:
    if configured_cap_usd is None:
        return "configured_cap_not_recorded"
    if base_event_cap_usd is None and effective_event_cap_usd is None:
        return "configured_cap_recorded_without_live_tick_budget"
    if base_event_cap_usd is not None and abs(base_event_cap_usd - configured_cap_usd) <= 1e-6:
        return "configured_cap_matches_base_event_cap"
    if effective_event_cap_usd is not None and abs(effective_event_cap_usd - configured_cap_usd) <= 1e-6:
        return "configured_cap_matches_effective_event_cap"
    return "effective_cap_adjusted_from_configured_cap"


def _target_management_state_with_direct_coverage(
    latest_tick_state: Any,
    *,
    direct_target_coverage_state: dict[str, Any],
) -> dict[str, Any]:
    state = dict(latest_tick_state) if isinstance(latest_tick_state, dict) else {}
    if not state or state.get("status") == "not_recorded_by_latest_live_tick":
        return {
            "schema_version": "janus_live_event_state_target_management_state_v1",
            "status": direct_target_coverage_state.get("status") or "not_recorded",
            "source": "direct_clob_event_inventory_fallback",
            "direct_target_coverage": direct_target_coverage_state,
            "execution_authority": False,
        }
    state["direct_target_coverage"] = direct_target_coverage_state
    state["execution_authority"] = False
    return state


def _reduce_stop_state_with_direct_coverage(
    latest_tick_state: Any,
    *,
    direct_target_coverage_state: dict[str, Any],
) -> dict[str, Any]:
    state = dict(latest_tick_state) if isinstance(latest_tick_state, dict) else {}
    if not state or state.get("status") == "not_recorded_by_latest_live_tick":
        return {
            "schema_version": "janus_live_event_state_reduce_stop_state_v1",
            "status": "not_applicable_flat"
            if direct_target_coverage_state.get("status") == "not_applicable_flat"
            else "not_recorded_by_latest_live_tick",
            "source": "latest_live_strategy_worker_tick",
            "direct_target_coverage": direct_target_coverage_state,
            "execution_authority": False,
        }
    state["direct_target_coverage"] = direct_target_coverage_state
    state["execution_authority"] = False
    return state


def _direct_target_coverage_state(inventory_state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(inventory_state, dict):
        return {
            "schema_version": "janus_live_event_state_direct_target_coverage_v1",
            "status": "not_recorded",
            "source": "direct_clob_event_inventory",
            "reason": "event_inventory_missing",
            "execution_authority": False,
        }
    positions = [
        position
        for position in inventory_state.get("active_open_positions") or []
        if isinstance(position, dict) and (_safe_float(position.get("size")) or 0.0) > 0.0
    ]
    sell_orders_by_token: dict[str, list[dict[str, Any]]] = {}
    for order in inventory_state.get("open_orders") or []:
        if not isinstance(order, dict) or str(order.get("side") or "").strip().lower() != "sell":
            continue
        token_id = _direct_item_token_id(order)
        if not token_id:
            continue
        size = _direct_order_open_size(order)
        if size <= 0.0:
            continue
        sell_orders_by_token.setdefault(token_id, []).append(order)

    rows: list[dict[str, Any]] = []
    uncovered_count = 0
    covered_count = 0
    for position in positions:
        token_id = _direct_item_token_id(position)
        matching_orders = sell_orders_by_token.get(token_id, [])
        sell_order_size = sum(_direct_order_open_size(order) for order in matching_orders)
        prices = [
            price
            for price in (_safe_float(order.get("price")) for order in matching_orders)
            if price is not None
        ]
        covered = bool(matching_orders and sell_order_size > 0.0)
        if covered:
            covered_count += 1
        else:
            uncovered_count += 1
        rows.append(
            {
                "token_id": token_id or None,
                "outcome": position.get("outcome") or position.get("title") or position.get("name"),
                "position_size": _safe_float(position.get("size")),
                "current_value": _safe_float(position.get("current_value")),
                "sell_order_count": len(matching_orders),
                "sell_order_size": round(sell_order_size, 6),
                "highest_target_price": round(max(prices), 6) if prices else None,
                "target_order_external_ids": [
                    order_id
                    for order_id in (_direct_item_external_id(order) for order in matching_orders)
                    if order_id
                ],
                "coverage_status": "covered_by_open_sell_order" if covered else "missing_open_sell_target",
            }
        )
    if not positions:
        status = "not_applicable_flat"
    elif uncovered_count:
        status = "uncovered_positions_present"
    else:
        status = "covered"
    return {
        "schema_version": "janus_live_event_state_direct_target_coverage_v1",
        "status": status,
        "source": "direct_clob_event_inventory",
        "event_id": inventory_state.get("event_id"),
        "active_position_count": len(positions),
        "covered_position_count": covered_count,
        "uncovered_position_count": uncovered_count,
        "open_sell_order_count": sum(len(orders) for orders in sell_orders_by_token.values()),
        "rows": rows,
        "execution_authority": False,
    }


def _direct_order_open_size(order: dict[str, Any]) -> float:
    for key in ("remaining_size", "remainingSize", "size_matched_remaining", "size", "original_size", "originalSize"):
        value = _safe_float(order.get(key))
        if value is not None:
            return max(0.0, value)
    return 0.0


def _persist_janus_live_event_states(states: list[dict[str, Any]], *, day: str | None) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for state in states:
        event_id = str((state.get("event_identity") or {}).get("event_id") or "unknown")
        latest_path = _janus_live_event_state_latest_path(event_id, day=day)
        history_path = latest_path.parent / "history.jsonl"
        state_with_paths = {
            **state,
            "latest_artifact_paths": [
                *[path for path in state.get("latest_artifact_paths") or [] if path],
                str(latest_path),
                str(history_path),
            ],
        }
        write_json(latest_path, state_with_paths)
        append_jsonl(history_path, state_with_paths)
        records.append(
            {
                "event_id": event_id,
                "schema_version": state_with_paths.get("schema_version"),
                "latest_path": str(latest_path),
                "history_path": str(history_path),
                "recorded_at_utc": state_with_paths.get("generated_at_utc"),
            }
        )
    return {
        "schema_version": "janus_live_event_state_records_v1",
        "state_count": len(records),
        "records": records,
    }


def _janus_live_event_state_latest_path(event_id: str, *, day: str | None) -> Path:
    return ops_artifact_root(day) / "live-event-state" / _ops_artifact_safe_name(event_id) / "latest.json"


def _janus_live_event_state_readback_endpoints(event_id: str, *, day: str | None) -> dict[str, str]:
    query = f"?session_date={day}" if day else ""
    return {
        "latest": f"/v1/events/{event_id}/live-state{query}",
        "history": f"/v1/events/{event_id}/live-state/history{query}",
        "worker_restart_readiness": f"/v1/events/{event_id}/worker-restart-readiness{query}",
    }


def _janus_quarter_revision_readback_endpoints(event_id: str, *, day: str | None) -> dict[str, str]:
    query = f"?session_date={day}" if day else ""
    return {
        "self": f"/v1/events/{event_id}/quarter-revisions{query}",
        "live_state": f"/v1/events/{event_id}/live-state{query}",
        "live_state_history": f"/v1/events/{event_id}/live-state/history{query}",
    }


def _janus_db_stat_context_readback_endpoints(event_id: str, *, day: str | None) -> dict[str, str]:
    query = f"?session_date={day}" if day else ""
    return {
        "self": f"/v1/events/{event_id}/db-stat-context{query}",
        "agent_context": f"/v1/events/{event_id}/agent-context{query}",
        "live_state": f"/v1/events/{event_id}/live-state{query}",
        "live_state_history": f"/v1/events/{event_id}/live-state/history{query}",
    }


def _janus_signal_blocker_readback_endpoints(event_id: str, *, day: str | None) -> dict[str, str]:
    query = f"?session_date={day}" if day else ""
    return {
        "self": f"/v1/events/{event_id}/signal-blockers{query}",
        "live_state": f"/v1/events/{event_id}/live-state{query}",
        "live_state_history": f"/v1/events/{event_id}/live-state/history{query}",
    }


def _janus_budget_readback_endpoints(event_id: str, *, day: str | None) -> dict[str, str]:
    query = f"?session_date={day}" if day else ""
    return {
        "self": f"/v1/events/{event_id}/budget-state{query}",
        "live_state": f"/v1/events/{event_id}/live-state{query}",
        "live_state_history": f"/v1/events/{event_id}/live-state/history{query}",
    }


def _janus_worker_restart_readiness_readback_endpoints(event_id: str, *, day: str | None) -> dict[str, str]:
    query = f"?session_date={day}" if day else ""
    return {
        "self": f"/v1/events/{event_id}/worker-restart-readiness{query}",
        "worker_status": "/v1/ops/live-strategy-worker/status",
        "live_state": f"/v1/events/{event_id}/live-state{query}",
        "live_state_history": f"/v1/events/{event_id}/live-state/history{query}",
    }


def _janus_live_event_state_schema_contract() -> dict[str, Any]:
    return {
        "schema_version": "janus_live_event_state_contract_v1",
        "primary_schema": "janus_live_event_state_v1",
        "execution_authority": False,
        "durable_outputs": [
            "ops/live-event-state/{event_id}/latest.json",
            "ops/live-event-state/{event_id}/history.jsonl",
            "GET /v1/events/{event_id}/live-state",
            "GET /v1/events/{event_id}/live-state/history",
            "GET /v1/events/{event_id}/worker-restart-readiness",
        ],
        "required_sections": [
            "event_identity",
            "readback_endpoints",
            "latest_live_worker_tick_state",
            "scoreboard_resolution",
            "game_state",
            "market_state",
            "direct_clob_account_state",
            "strategy_plan_state",
            "event_controls_state",
            "worker_state",
            "worker_restart_safety_state",
            "llm_runtime_state",
            "strategy_review_escalation_state",
            "db_stat_context_state",
            "quarter_revision_state",
            "signal_aggregation_state",
            "sleeve_state",
            "budget_state",
            "order_lifecycle_state",
            "target_management_state",
            "reduce_stop_state",
            "manual_interference_state",
            "post_call_reconciliation_state",
            "blockers",
            "freshness_status",
            "latest_artifact_paths",
        ],
        "source_sections": {
            "latest_live_worker_tick_state": "live-strategy-worker/{day}/ticks.jsonl",
            "scoreboard_resolution": "latest live worker tick scoreboard_resolution when available",
            "game_state": "latest live worker tick game_state when available",
            "direct_clob_account_state": "ops live-monitor current_event_inventory",
            "strategy_plan_state": "ops live-monitor strategy_plan_gate.current_plans",
            "event_controls_state": "event-controls/{day}/{event_id}/current.json",
            "strategy_review_escalation_state": "llm-runtime latest event trace codex fallback/review flags",
            "db_stat_context_state": "event agent-context db_stat_context_trace for the requested event",
            "quarter_revision_state": "llm-runtime quarter_revision_reviews for the requested event",
            "signal_aggregation_state": "latest live worker tick aggregation/blocker readback",
            "budget_state": "latest live worker tick event budget and risk-promotion readback",
        },
        "consumer_notes": [
            "This schema is read-only evidence and never authorizes order submission.",
            "Missing sections must remain explicit with status/reason fields instead of being omitted.",
            "Consumers should prefer latest.json for current state and history.jsonl for postgame timelines.",
            "If latest.json is missing, postgame summaries may fall back to live-strategy-worker ticks.jsonl with explicit source/reason.",
        ],
    }


def _janus_live_event_state_history_schema_contract() -> dict[str, Any]:
    return {
        "schema_version": "janus_live_event_state_history_readback_contract_v1",
        "primary_schema": "janus_live_event_state_history_readback_v1",
        "source_schema": "janus_live_event_state_v1",
        "execution_authority": False,
        "durable_outputs": [
            "ops/live-event-state/{event_id}/history.jsonl",
            "GET /v1/events/{event_id}/live-state/history",
        ],
        "required_sections": [
            "event_id",
            "session_date",
            "latest_path",
            "history_path",
            "status",
            "requested_limit",
            "effective_limit",
            "total_record_count",
            "returned_record_count",
            "invalid_line_count",
            "records",
            "execution_authority",
        ],
        "record_required_sections": [
            "line_number",
            "schema_version",
            "generated_at_utc",
            "event_identity",
            "latest_live_worker_tick_state",
            "scoreboard_resolution",
            "game_state",
            "signal_aggregation_state",
            "budget_state",
            "blockers",
            "execution_authority",
        ],
        "limit_bounds": {"min": 1, "max": 500},
        "consumer_notes": [
            "This endpoint is read-only evidence and never authorizes order submission.",
            "Records are compact tail records ordered oldest-to-newest within the returned window.",
            "Use total_record_count and invalid_line_count to detect truncation or malformed history rows.",
        ],
    }


def _janus_event_quarter_revision_readback_schema_contract() -> dict[str, Any]:
    return {
        "schema_version": "janus_event_quarter_revision_readback_contract_v1",
        "primary_schema": "janus_event_quarter_revision_readback_v1",
        "source_schema": "strategy_plan_quarter_revision_review_v1",
        "execution_authority": False,
        "durable_outputs": [
            "GET /v1/events/{event_id}/quarter-revisions",
            "llm-runtime/{day}/quarter_revision_reviews.jsonl",
            "llm-runtime/{day}/quarter-revision-reviews/{event_id}/*.json",
        ],
        "required_sections": [
            "event_id",
            "session_date",
            "status",
            "source",
            "source_schema",
            "llm_runtime_status",
            "quarter_revision_state",
            "quarter_revision_reviews",
            "quarter_revision_review_artifacts",
            "artifact_paths",
            "readback_endpoints",
            "order_endpoint_call_allowed",
            "execution_authority",
        ],
        "state_schema_contract": "janus_live_event_state_quarter_revision_contract_v1",
        "expected_quarter_labels": ["q1_end", "halftime_q2_end", "q3_end"],
        "consumer_notes": [
            "This endpoint is read-only evidence and never authorizes order submission.",
            "Use quarter_revision_state.missing_expected_quarter_labels to identify unproven Q1, halftime/Q2, and Q3 reviews.",
            "quarter_revision_reviews are copied from LLM runtime artifacts without adopting or mutating StrategyPlans.",
        ],
    }


def _janus_event_db_stat_context_readback_schema_contract() -> dict[str, Any]:
    return {
        "schema_version": "janus_event_db_stat_context_readback_contract_v1",
        "primary_schema": "janus_event_db_stat_context_readback_v1",
        "source_schema": "db_stat_context_trace_v1",
        "execution_authority": False,
        "durable_outputs": [
            "GET /v1/events/{event_id}/db-stat-context",
            "GET /v1/events/{event_id}/agent-context",
            "janus_live_event_state_v1.db_stat_context_state",
        ],
        "required_sections": [
            "event_id",
            "session_date",
            "status",
            "source",
            "source_schema",
            "resolved_strategy_plan_event_id",
            "strategy_plan_lookup_event_ids",
            "db_stat_context_state",
            "db_stat_context_trace",
            "db_context_sources",
            "readback_endpoints",
            "order_endpoint_call_allowed",
            "execution_authority",
        ],
        "state_schema": "janus_live_event_state_db_stat_context_state_v1",
        "consumer_notes": [
            "This endpoint is read-only context evidence and never authorizes order submission.",
            "Use db_stat_context_state.stale_blocker_counts and stale_source_ids to decide whether NBA/WNBA context must be refreshed.",
            "db_context_sources preserves source timestamps, team/player counts, freshness, and LLM inclusion flags from agent context.",
        ],
    }


def _janus_event_signal_blocker_readback_schema_contract() -> dict[str, Any]:
    return {
        "schema_version": "janus_event_signal_blocker_readback_contract_v1",
        "primary_schema": "janus_event_signal_blocker_readback_v1",
        "source_schema": "janus_live_event_state_signal_aggregation_v1",
        "execution_authority": False,
        "durable_outputs": [
            "GET /v1/events/{event_id}/signal-blockers",
            "janus_live_event_state_v1.signal_aggregation_state",
            "live-strategy-worker/{day}/ticks.jsonl signal aggregation fallback",
        ],
        "required_sections": [
            "event_id",
            "session_date",
            "status",
            "source",
            "source_schema",
            "signal_aggregation_state",
            "blocker_scope_rows",
            "blocker_reason_counts",
            "blocker_scope_counts",
            "expected_next_action_counts",
            "candidate_blocking_blocker_count",
            "nonblocking_local_blocker_count",
            "readback_endpoints",
            "order_endpoint_call_allowed",
            "execution_authority",
        ],
        "blocker_row_schema": "live_signal_blocker_scope_v1",
        "consumer_notes": [
            "This endpoint is read-only blocker evidence and never authorizes order submission.",
            "candidate_blocking=false local rows should not be treated as global suppression.",
            "expected_next_action distinguishes preserve-unrelated-candidates from true safety blocks.",
        ],
    }


def _janus_event_budget_readback_schema_contract() -> dict[str, Any]:
    return {
        "schema_version": "janus_event_budget_readback_contract_v1",
        "primary_schema": "janus_event_budget_readback_v1",
        "source_schema": "janus_live_event_state_budget_state_v1",
        "execution_authority": False,
        "durable_outputs": [
            "GET /v1/events/{event_id}/budget-state",
            "janus_live_event_state_v1.budget_state",
            "live-strategy-worker/{day}/ticks.jsonl budget fallback",
        ],
        "required_sections": [
            "event_id",
            "session_date",
            "status",
            "source",
            "source_schema",
            "budget_state",
            "risk_promotion_evidence",
            "profit_addon_policy",
            "event_cap_before_profit_addon_usd",
            "event_cap_usd",
            "remaining_notional_usd",
            "profit_ratcheted_requested_addon_usd",
            "profit_ratcheted_addon_usd",
            "profit_ratcheted_blocked_addon_usd",
            "risk_promotion_allowed",
            "risk_promotion_source_confidence",
            "risk_promotion_blocker_codes",
            "readback_endpoints",
            "order_endpoint_call_allowed",
            "execution_authority",
        ],
        "profit_addon_policy_schema": "account_confirmed_profit_budget_addon_v1",
        "consumer_notes": [
            "This endpoint is read-only budget evidence and never authorizes order submission.",
            "Usable profit add-ons require account/db-confirmed closed PnL and clean lifecycle evidence.",
            "risk_promotion_blocker_codes must remain blocking when lifecycle, final cleanup, or open-unrealized evidence is unresolved.",
        ],
    }


def _janus_event_worker_restart_readiness_schema_contract() -> dict[str, Any]:
    return {
        "schema_version": "janus_event_worker_restart_readiness_contract_v1",
        "primary_schema": "janus_event_worker_restart_readiness_v1",
        "source_schema": "live_strategy_worker_config_trust_v1",
        "execution_authority": False,
        "durable_outputs": [
            "GET /v1/events/{event_id}/worker-restart-readiness",
            "GET /v1/ops/live-strategy-worker/status",
            "janus_live_event_state_v1.worker_restart_safety_state",
        ],
        "required_sections": [
            "status",
            "worker_restart_safety_state",
            "canonical_worker_restart_safety_state",
            "strategy_plan_gate",
            "live_strategy_worker_status",
            "blocker_reasons",
            "readback_endpoints",
            "order_endpoint_call_allowed",
            "execution_authority",
        ],
        "worker_restart_safety_schema": "janus_live_event_state_worker_restart_safety_v1",
        "restart_requires": [
            "current event scope",
            "trusted config or explicit current payload",
            "explicit account scope for live-money worker starts",
        ],
        "consumer_notes": [
            "This endpoint is read-only restart readiness evidence and never starts a worker.",
            "safe_to_restart_without_explicit_payload=false means a fresh Janus-control start payload is required.",
            "Use blocker_reasons to distinguish stale singleton config, missing StrategyPlan, and account-scope blockers.",
        ],
    }


def _postgame_canonical_live_event_state_schema_contract() -> dict[str, Any]:
    return {
        "schema_version": "postgame_canonical_live_event_state_contract_v1",
        "primary_schema": "postgame_canonical_live_event_state_summary_v1",
        "source_schema": "janus_live_event_state_v1",
        "execution_authority": False,
        "durable_outputs": [
            "POST /v1/ops/postgame-review response.postgame_canonical_live_event_state",
            "ops/postgame-review_*.json",
        ],
        "required_sections": [
            "items",
            "event_count",
            "recorded_event_count",
            "missing_event_count",
            "aggregate_source_counts",
            "aggregate_reason_counts",
            "aggregate_scoreboard_resolution_status_counts",
            "aggregate_blocker_scope_counts",
            "aggregate_blocker_reason_counts",
            "aggregate_expected_next_action_counts",
            "aggregate_signal_count",
            "aggregate_order_intent_candidate_count",
            "aggregate_blocker_count",
            "aggregate_candidate_blocking_blocker_count",
            "aggregate_nonblocking_local_blocker_count",
            "aggregate_minimum_order_policy_state",
            "aggregate_budget_state",
            "aggregate_db_context_inclusion_status_counts",
            "aggregate_db_context_stale_blocker_counts",
            "aggregate_quarter_revision_review_count",
            "aggregate_quarter_revision_review_status_counts",
        ],
        "item_required_sections": [
            "event_id",
            "source",
            "latest_path",
            "history_path",
            "readback_endpoints",
            "history_count",
            "scoreboard_resolution_status",
            "scoreboard_resolution_state",
            "game_state_status",
            "db_stat_context_state",
            "quarter_revision_state",
            "signal_aggregation_state",
            "budget_state",
        ],
        "consumer_notes": [
            "This summary is postgame evidence only and never authorizes live-money action.",
            "Missing live-event-state latest.json files must appear as missing items with paths.",
            "Blocker counts are aggregated from canonical live-state signal_aggregation_state sections.",
        ],
    }


def _postgame_canonical_minimum_order_policy_schema_contract() -> dict[str, Any]:
    return {
        "schema_version": "postgame_canonical_minimum_order_policy_summary_contract_v1",
        "primary_schema": "postgame_canonical_minimum_order_policy_summary_v1",
        "source_schema": "janus_live_event_state_minimum_order_policy_summary_v1",
        "execution_authority": False,
        "required_sections": [
            "status",
            "policy_count",
            "candidate_policy_count",
            "blocker_policy_count",
            "order_type_minimum_rule_counts",
            "minimum_rule_status_counts",
            "order_type_counts",
            "share_minimum_applies_count",
            "notional_minimum_applies_count",
            "execution_authority",
        ],
        "consumer_notes": [
            "Aggregate counts preserve order-type minimum semantics across canonical live-state items.",
            "Limit orders are validated by share-minimum evidence; market orders are validated by notional evidence.",
            "This postgame summary is read-only evidence and never authorizes order action.",
        ],
    }


def _ops_artifact_safe_name(value: Any) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value or ""))
    return safe[:160] or "unknown"


def _find_event_state_item(items: list[Any], event_id: str) -> dict[str, Any] | None:
    for item in items:
        if not isinstance(item, dict):
            continue
        candidates = {
            str(item.get("event_id") or ""),
            str(item.get("plan_event_id") or ""),
            str(item.get("strategy_plan_event_id") or ""),
        }
        if event_id in candidates:
            return item
    return None


_CRITICAL_STRATEGY_REVIEW_TRIGGER_TYPES = frozenset(
    {
        "manual_operator_position",
        "position_adverse_move",
    }
)


def _build_strategy_review_escalation_state(
    llm_runtime_status: dict[str, Any],
    *,
    event_id: str,
) -> dict[str, Any]:
    item = _find_event_state_item(llm_runtime_status.get("items") or [], event_id)
    if not isinstance(item, dict):
        return {
            "schema_version": "janus_live_event_state_strategy_review_escalation_v1",
            "status": "not_recorded",
            "event_id": event_id,
            "source_schema": "llm_runtime_trace_artifact_v1",
            "trigger_count": 0,
            "trigger_types": [],
            "critical_trigger_types": [],
            "review_required": False,
            "critical_review_required": False,
            "codex_strategy_required": False,
            "internal_llm_unavailable": False,
            "budget_blocked": False,
            "blocker_reasons": [],
            "expected_next_action": "none",
            "allowed_fallback_actions": [],
            "artifact_paths": [],
            "order_endpoint_call_allowed": False,
            "execution_authority": False,
        }

    llm_state = item.get("llm_runtime_state") if isinstance(item.get("llm_runtime_state"), dict) else {}
    fallback_state = (
        item.get("codex_fallback_state") if isinstance(item.get("codex_fallback_state"), dict) else {}
    )
    trigger_types = [str(value) for value in _normalized_unique_values(item.get("trigger_types") or [])]
    critical_trigger_types = [
        trigger_type
        for trigger_type in trigger_types
        if trigger_type in _CRITICAL_STRATEGY_REVIEW_TRIGGER_TYPES
    ]
    review_required = bool(
        llm_state.get("review_required")
        or fallback_state.get("review_required")
        or llm_state.get("codex_strategy_required")
        or fallback_state.get("codex_strategy_required")
    )
    codex_strategy_required = bool(
        llm_state.get("codex_strategy_required") or fallback_state.get("codex_strategy_required")
    )
    internal_llm_unavailable = bool(
        llm_state.get("internal_llm_unavailable") or fallback_state.get("internal_llm_unavailable")
    )
    budget_blocked = bool(llm_state.get("budget_blocked") or fallback_state.get("budget_blocked"))
    critical_review_required = bool(review_required and critical_trigger_types)

    if critical_review_required:
        status_value = "critical_review_required"
        blocker_reasons = ["critical_strategy_review_required"]
        expected_next_action = "submit_or_record_janus_gated_strategy_review_before_live_restart"
    elif review_required:
        status_value = "review_required"
        blocker_reasons = ["strategy_review_required"]
        expected_next_action = "record_strategy_review_before_new_live_scope"
    elif trigger_types:
        status_value = "review_not_required"
        blocker_reasons = []
        expected_next_action = "none"
    else:
        status_value = "no_revision_triggers"
        blocker_reasons = []
        expected_next_action = "none"

    artifact_path_values: list[Any] = [item.get("path"), item.get("artifact_path")]
    raw_artifact_paths = item.get("artifact_paths")
    if isinstance(raw_artifact_paths, list):
        artifact_path_values.extend(raw_artifact_paths)
    elif raw_artifact_paths:
        artifact_path_values.append(raw_artifact_paths)
    artifact_paths = [str(path) for path in _normalized_unique_values(artifact_path_values) if path]
    allowed_fallback_actions = (
        fallback_state.get("allowed_fallback_actions")
        or llm_state.get("allowed_fallback_actions")
        or []
    )

    return {
        "schema_version": "janus_live_event_state_strategy_review_escalation_v1",
        "status": status_value,
        "event_id": event_id,
        "source_schema": "llm_runtime_trace_artifact_v1",
        "source_status": item.get("status") or llm_runtime_status.get("status"),
        "response_status": item.get("response_status"),
        "adoption_status": item.get("adoption_status"),
        "trigger_count": _safe_int(item.get("trigger_count")),
        "trigger_types": trigger_types,
        "critical_trigger_types": critical_trigger_types,
        "review_required": review_required,
        "critical_review_required": critical_review_required,
        "codex_strategy_required": codex_strategy_required,
        "internal_llm_unavailable": internal_llm_unavailable,
        "budget_blocked": budget_blocked,
        "blocker_reasons": blocker_reasons,
        "expected_next_action": expected_next_action,
        "allowed_fallback_actions": allowed_fallback_actions,
        "artifact_paths": artifact_paths,
        "order_endpoint_call_allowed": False,
        "execution_authority": False,
    }


def _build_db_stat_context_state(event_id: str, *, day: str | None) -> dict[str, Any]:
    try:
        context = build_event_agent_context(event_id, day=day)
    except Exception as exc:  # noqa: BLE001
        return _db_stat_context_error_state(event_id, exc)
    return _db_stat_context_state_from_context(event_id, context)


def _db_stat_context_error_state(event_id: str, exc: Exception) -> dict[str, Any]:
    return {
        "schema_version": "janus_live_event_state_db_stat_context_state_v1",
        "status": "error",
        "event_id": event_id,
        "error": str(exc),
        "source_schema": "db_stat_context_trace_v1",
        "source_count": 0,
        "included_source_count": 0,
        "stale_source_count": 0,
        "missing_source_count": 0,
        "blockers": ["db_stat_context_trace_unavailable"],
        "liveness_blocking": False,
        "execution_authority": False,
    }


def _db_stat_context_state_from_context(event_id: str, context: dict[str, Any]) -> dict[str, Any]:
    trace = context.get("db_stat_context_trace") if isinstance(context.get("db_stat_context_trace"), dict) else {}
    sources = [source for source in trace.get("db_context_sources") or context.get("db_context_sources") or [] if isinstance(source, dict)]
    source_status_counts: dict[str, int] = {}
    freshness_status_counts: dict[str, int] = {}
    stale_blocker_counts: dict[str, int] = {}
    source_ids: list[str] = []
    included_source_ids: list[str] = []
    stale_source_ids: list[str] = []
    source_paths: list[str] = []
    teams: list[str] = []
    players: list[str] = []
    for source in sources:
        source_id = str(source.get("source_id") or "unknown")
        source_ids.append(source_id)
        status_value = str(source.get("status") or "unknown")
        source_status_counts[status_value] = source_status_counts.get(status_value, 0) + 1
        freshness_value = str(source.get("freshness_status") or status_value)
        freshness_status_counts[freshness_value] = freshness_status_counts.get(freshness_value, 0) + 1
        if source.get("included_in_llm_context"):
            included_source_ids.append(source_id)
        if source.get("stale"):
            stale_source_ids.append(source_id)
        for blocker in source.get("stale_blockers") or []:
            blocker_key = str(blocker or "unknown")
            stale_blocker_counts[blocker_key] = stale_blocker_counts.get(blocker_key, 0) + 1
        path = str(source.get("source_path") or "").strip()
        if path and path not in source_paths:
            source_paths.append(path)
        for team in source.get("teams") or []:
            team_text = str(team or "").strip()
            if team_text and team_text not in teams:
                teams.append(team_text)
        for player in source.get("players") or []:
            player_text = str(player or "").strip()
            if player_text and player_text not in players:
                players.append(player_text)

    return {
        "schema_version": "janus_live_event_state_db_stat_context_state_v1",
        "status": "recorded" if trace else "not_recorded",
        "event_id": event_id,
        "source_schema": trace.get("schema_version") or "db_stat_context_trace_v1",
        "resolved_strategy_plan_event_id": context.get("resolved_strategy_plan_event_id"),
        "llm_context_inclusion_status": trace.get("llm_context_inclusion_status") or "unknown",
        "source_count": _safe_int(trace.get("source_count")) if trace else len(sources),
        "included_source_count": _safe_int(trace.get("included_source_count")),
        "stale_source_count": _safe_int(trace.get("stale_source_count")),
        "missing_source_count": _safe_int(trace.get("missing_source_count")),
        "source_ids": source_ids,
        "included_source_ids": included_source_ids,
        "stale_source_ids": stale_source_ids,
        "source_status_counts": dict(sorted(source_status_counts.items())),
        "freshness_status_counts": dict(sorted(freshness_status_counts.items())),
        "stale_blocker_counts": dict(sorted(stale_blocker_counts.items())),
        "blockers": trace.get("blockers") or [],
        "teams": teams,
        "players": players,
        "source_paths": source_paths,
        "liveness_blocking": bool(trace.get("liveness_blocking")) if trace else False,
        "execution_authority": False,
    }


def _build_quarter_revision_state(llm_runtime_status: dict[str, Any], *, event_id: str) -> dict[str, Any]:
    item = _find_event_state_item(llm_runtime_status.get("items") or [], event_id)
    if not isinstance(item, dict):
        return {
            "schema_version": "janus_live_event_state_quarter_revision_state_v1",
            "schema_contract": _janus_live_event_state_quarter_revision_schema_contract(),
            "status": "not_recorded",
            "event_id": event_id,
            "source_schema": "strategy_plan_quarter_revision_review_v1",
            "quarter_revision_review_count": 0,
            "artifact_count": 0,
            "quarter_labels": [],
            "missing_expected_quarter_labels": ["q1_end", "halftime_q2_end", "q3_end"],
            "review_status_counts": {},
            "reviewed_sleeve_count": 0,
            "artifact_paths": [],
            "order_endpoint_call_allowed": False,
            "execution_authority": False,
        }

    reviews = [review for review in item.get("quarter_revision_reviews") or [] if isinstance(review, dict)]
    artifacts = [artifact for artifact in item.get("quarter_revision_review_artifacts") or [] if isinstance(artifact, dict)]
    quarter_labels: list[str] = []
    review_status_counts: dict[str, int] = {}
    artifact_paths: list[str] = []
    reviewed_sleeve_count = 0
    order_endpoint_call_allowed = False

    for review in reviews:
        label = str(review.get("quarter_label") or "").strip()
        if label and label not in quarter_labels:
            quarter_labels.append(label)
        status_value = str(review.get("review_status") or review.get("status") or "unknown")
        review_status_counts[status_value] = review_status_counts.get(status_value, 0) + 1
        reviewed_sleeve_count += _safe_int(review.get("reviewed_sleeve_count"))
        if review.get("order_endpoint_call_allowed"):
            order_endpoint_call_allowed = True
        path = str(review.get("path") or review.get("artifact_path") or "").strip()
        if path and path not in artifact_paths:
            artifact_paths.append(path)

    for artifact in artifacts:
        path = str(artifact.get("path") or artifact.get("artifact_path") or "").strip()
        if path and path not in artifact_paths:
            artifact_paths.append(path)

    expected_labels = ["q1_end", "halftime_q2_end", "q3_end"]
    missing_expected = [label for label in expected_labels if label not in set(quarter_labels)]
    return {
        "schema_version": "janus_live_event_state_quarter_revision_state_v1",
        "schema_contract": _janus_live_event_state_quarter_revision_schema_contract(),
        "status": "recorded" if reviews else "not_recorded",
        "event_id": event_id,
        "source_schema": "strategy_plan_quarter_revision_review_v1",
        "llm_runtime_status": item.get("status") or llm_runtime_status.get("status"),
        "quarter_revision_review_count": len(reviews),
        "artifact_count": len(artifacts),
        "quarter_labels": quarter_labels,
        "missing_expected_quarter_labels": missing_expected,
        "review_status_counts": dict(sorted(review_status_counts.items())),
        "reviewed_sleeve_count": reviewed_sleeve_count,
        "artifact_paths": artifact_paths,
        "order_endpoint_call_allowed": order_endpoint_call_allowed,
        "execution_authority": False,
    }


def _janus_live_event_state_quarter_revision_schema_contract() -> dict[str, Any]:
    return {
        "schema_version": "janus_live_event_state_quarter_revision_contract_v1",
        "primary_schema": "janus_live_event_state_quarter_revision_state_v1",
        "source_schema": "strategy_plan_quarter_revision_review_v1",
        "execution_authority": False,
        "required_sections": [
            "status",
            "event_id",
            "source_schema",
            "quarter_revision_review_count",
            "artifact_count",
            "quarter_labels",
            "missing_expected_quarter_labels",
            "review_status_counts",
            "reviewed_sleeve_count",
            "artifact_paths",
            "order_endpoint_call_allowed",
            "execution_authority",
        ],
        "expected_quarter_labels": ["q1_end", "halftime_q2_end", "q3_end"],
        "consumer_notes": [
            "Quarter revision evidence is LLM/StrategyPlan review readback and never authorizes order action.",
            "missing_expected_quarter_labels should be used to identify gaps in Q1, halftime/Q2, and Q3 review traces.",
            "order_endpoint_call_allowed must remain false for pure review artifacts.",
        ],
    }


def _build_janus_live_event_state_blockers(
    *,
    event_id: str,
    strategy_plan_gate: dict[str, Any],
    live_strategy_worker_status: dict[str, Any],
    live_monitor_readiness: dict[str, Any],
    inventory_state: dict[str, Any] | None,
    strategy_review_escalation_state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []

    if event_id in set(_normalized_unique_values(strategy_plan_gate.get("missing_event_ids") or [])):
        blockers.append(
            {
                "source": "strategy_plan_gate",
                "reason": "missing_current_strategy_plan",
                "severity": "RED",
                "scope": "global_safety",
                "execution_blocking": True,
            }
        )
    worker_reason = live_strategy_worker_status.get("blocker_reason")
    if worker_reason:
        blockers.append(
            {
                "source": "live_strategy_worker_status",
                "reason": worker_reason,
                "severity": "RED",
                "scope": "global_safety",
                "execution_blocking": True,
            }
        )
    readiness_gate = str(live_monitor_readiness.get("gate") or "").upper()
    for reason in live_monitor_readiness.get("blocker_reasons") or []:
        blockers.append(
            {
                "source": "live_monitor_readiness",
                "reason": reason,
                "severity": "RED" if readiness_gate == "RED" else "YELLOW",
                "scope": "global_safety" if readiness_gate == "RED" else "local_or_advisory",
                "execution_blocking": readiness_gate == "RED",
            }
        )
    if inventory_state and inventory_state.get("unresolved_inventory_present"):
        blockers.append(
            {
                "source": "direct_clob_account_state",
                "reason": "unresolved_current_event_inventory_present",
                "severity": "YELLOW",
                "scope": "local_sleeve",
                "execution_blocking": False,
            }
        )
    if strategy_review_escalation_state and strategy_review_escalation_state.get("critical_review_required"):
        blockers.append(
            {
                "source": "strategy_review_escalation_state",
                "reason": "critical_strategy_review_required",
                "severity": "RED",
                "scope": "event_strategy_review",
                "execution_blocking": True,
                "trigger_types": strategy_review_escalation_state.get("critical_trigger_types") or [],
                "codex_strategy_required": bool(strategy_review_escalation_state.get("codex_strategy_required")),
                "expected_next_action": strategy_review_escalation_state.get("expected_next_action"),
            }
        )
    elif strategy_review_escalation_state and strategy_review_escalation_state.get("review_required"):
        blockers.append(
            {
                "source": "strategy_review_escalation_state",
                "reason": "strategy_review_required",
                "severity": "YELLOW",
                "scope": "event_strategy_review",
                "execution_blocking": False,
                "trigger_types": strategy_review_escalation_state.get("trigger_types") or [],
                "codex_strategy_required": bool(strategy_review_escalation_state.get("codex_strategy_required")),
                "expected_next_action": strategy_review_escalation_state.get("expected_next_action"),
            }
        )

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for blocker in blockers:
        key = (str(blocker.get("source")), str(blocker.get("reason")), str(blocker.get("severity")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(blocker)
    return deduped


def _build_worker_restart_safety_state(
    live_strategy_worker_status: dict[str, Any],
    *,
    event_id: str,
) -> dict[str, Any]:
    worker_status = (
        live_strategy_worker_status.get("worker_status")
        if isinstance(live_strategy_worker_status.get("worker_status"), dict)
        else {}
    )
    config_trust = live_strategy_worker_status.get("config_trust")
    if not isinstance(config_trust, dict):
        config_trust = worker_status.get("config_trust") if isinstance(worker_status.get("config_trust"), dict) else {}

    effective_event_ids = [str(item) for item in config_trust.get("effective_event_ids") or [] if item]
    expected_event_ids = [str(item) for item in live_strategy_worker_status.get("expected_event_ids") or [] if item]
    running = bool(worker_status.get("worker_thread_alive")) or str(live_strategy_worker_status.get("status") or "") == "running"
    restart_allowed = bool(config_trust.get("restart_allowed"))
    start_allowed = bool(config_trust.get("start_allowed"))
    event_scope_contains_requested_event = event_id in effective_event_ids
    expected_scope_contains_requested_event = event_id in expected_event_ids
    trusted_env_autostart = bool(config_trust.get("trusted_env_autostart"))
    explicit_event_scope_payload = bool(config_trust.get("explicit_event_scope_payload"))
    explicit_account_payload = bool(config_trust.get("explicit_account_payload"))
    safe_to_restart_without_explicit_payload = bool(
        restart_allowed
        and event_scope_contains_requested_event
        and (trusted_env_autostart or (explicit_event_scope_payload and explicit_account_payload))
    )

    if running and restart_allowed and event_scope_contains_requested_event:
        status_value = "trusted_running_current_scope"
    elif safe_to_restart_without_explicit_payload:
        status_value = "restart_allowed_current_scope"
    else:
        status_value = "fail_closed_explicit_current_scope_required"

    blocker_reasons: list[str] = []
    stale_reason = config_trust.get("stale_config_reason")
    if stale_reason:
        blocker_reasons.append(str(stale_reason))
    if not event_scope_contains_requested_event:
        blocker_reasons.append("effective_worker_scope_missing_requested_event")
    if not restart_allowed:
        blocker_reasons.append("worker_restart_not_allowed_by_config_trust")
    if (config_trust.get("execute") or config_trust.get("live_money")) and not explicit_account_payload and not trusted_env_autostart:
        blocker_reasons.append("live_money_restart_requires_explicit_account_scope")

    return {
        "schema_version": "janus_live_event_state_worker_restart_safety_v1",
        "status": status_value,
        "execution_authority": False,
        "event_id": event_id,
        "worker_running": running,
        "restart_allowed": restart_allowed,
        "start_allowed": start_allowed,
        "safe_to_restart_without_explicit_payload": safe_to_restart_without_explicit_payload,
        "config_trust_status": config_trust.get("config_trust_status"),
        "stale_config_reason": stale_reason,
        "effective_event_ids": effective_event_ids,
        "expected_event_ids": expected_event_ids,
        "event_scope_contains_requested_event": event_scope_contains_requested_event,
        "expected_scope_contains_requested_event": expected_scope_contains_requested_event,
        "explicit_event_scope_payload": explicit_event_scope_payload,
        "explicit_account_payload": explicit_account_payload,
        "trusted_env_autostart": trusted_env_autostart,
        "blocker_reasons": sorted(set(blocker_reasons)),
        "source_schema": config_trust.get("schema_version"),
    }


@router.post("/ops/live-strategy-worker/tick", status_code=status.HTTP_202_ACCEPTED)
def run_ops_live_strategy_worker_tick(payload: LiveStrategyWorkerRequest | None = None) -> dict[str, Any]:
    overrides = _live_strategy_worker_overrides(payload)
    worker = get_live_strategy_worker()
    worker_status = worker.status()
    tick_request_safety = _live_strategy_worker_tick_request_safety(
        payload=payload,
        overrides=overrides,
        worker_status=worker_status,
    )
    if not tick_request_safety["tick_allowed"]:
        return {
            "ok": False,
            "status": "blocked",
            "reason": "live_strategy_worker_tick_request_not_trusted",
            "tick_request_safety": tick_request_safety,
            "worker_status": worker_status,
            "execution_authority": False,
        }
    result = worker.run_once(overrides)
    if isinstance(result, dict):
        return {
            **result,
            "tick_request_safety": tick_request_safety,
        }
    return {
        "ok": False,
        "status": "error",
        "reason": "live_strategy_worker_tick_returned_non_dict",
        "tick_request_safety": tick_request_safety,
        "execution_authority": False,
    }


def _live_strategy_worker_tick_request_safety(
    *,
    payload: LiveStrategyWorkerRequest | None,
    overrides: dict[str, Any],
    worker_status: dict[str, Any],
) -> dict[str, Any]:
    config = worker_status.get("config") if isinstance(worker_status.get("config"), dict) else {}
    explicit_fields = set(payload.model_fields_set) if payload is not None else set()
    explicit_event_scope_payload = "event_ids" in explicit_fields and bool(overrides.get("event_ids"))
    explicit_account_payload = "account_id" in explicit_fields and bool(overrides.get("account_id"))
    effective_session_date = str(overrides.get("session_date") or config.get("session_date") or session_date(None))
    inherited_event_ids = config.get("event_ids") if isinstance(config.get("event_ids"), list) else []
    effective_event_ids = [str(item) for item in (overrides.get("event_ids") or inherited_event_ids or []) if item]
    effective_execute = overrides.get("execute") if "execute" in overrides else config.get("execute")
    effective_live_money = overrides.get("live_money") if "live_money" in overrides else config.get("live_money")
    current_session_date = session_date(None)
    event_scope_matches_requested_date = all(
        event_id_matches_session_date(event_id, effective_session_date) for event_id in effective_event_ids
    )
    event_scope_current = effective_session_date == current_session_date and event_scope_matches_requested_date

    blocker_reasons: list[str] = []
    if not explicit_event_scope_payload:
        blocker_reasons.append("manual_tick_requires_explicit_event_scope")
    if effective_event_ids and not event_scope_current:
        blocker_reasons.append("manual_tick_stale_event_scope")
    if (effective_execute or effective_live_money) and not explicit_account_payload:
        blocker_reasons.append("manual_live_tick_requires_explicit_account_scope")

    return {
        "schema_version": "live_strategy_worker_tick_request_safety_v1",
        "status": "trusted_current_manual_tick_request" if not blocker_reasons else "fail_closed_manual_tick_request",
        "tick_allowed": not blocker_reasons,
        "execution_authority": False,
        "explicit_event_scope_payload": explicit_event_scope_payload,
        "explicit_account_payload": explicit_account_payload,
        "effective_session_date": effective_session_date,
        "current_session_date": current_session_date,
        "effective_event_ids": effective_event_ids,
        "event_scope_current": event_scope_current,
        "effective_execute": bool(effective_execute),
        "effective_live_money": bool(effective_live_money),
        "worker_status": worker_status.get("status"),
        "worker_thread_alive": bool(worker_status.get("worker_thread_alive")),
        "blocker_reasons": sorted(set(blocker_reasons)),
    }


@router.post("/ops/live-strategy-worker/start", status_code=status.HTTP_202_ACCEPTED)
def start_ops_live_strategy_worker(payload: LiveStrategyWorkerRequest | None = None) -> dict[str, Any]:
    overrides = _live_strategy_worker_overrides(payload)
    return get_live_strategy_worker().start(overrides)


@router.post("/ops/live-strategy-worker/stop", status_code=status.HTTP_202_ACCEPTED)
def stop_ops_live_strategy_worker() -> dict[str, Any]:
    return get_live_strategy_worker().stop()


def _live_strategy_worker_overrides(payload: LiveStrategyWorkerRequest | None) -> dict[str, Any]:
    if payload is None:
        return {}
    overrides = payload.model_dump(mode="json", exclude_none=True)
    if "event_ids" not in payload.model_fields_set:
        overrides.pop("event_ids", None)
    for field in payload.model_fields_set:
        if getattr(payload, field) is None:
            overrides[field] = None
    return overrides


def _build_postgame_canonical_live_event_state_summary(
    *,
    day: str | None,
    event_ids: list[str],
) -> dict[str, Any]:
    items = [_postgame_canonical_live_event_state_item(day=day, event_id=event_id) for event_id in event_ids]
    aggregate_source_counts: dict[str, int] = {}
    aggregate_reason_counts: dict[str, int] = {}
    aggregate_blocker_scope_counts: dict[str, int] = {}
    aggregate_blocker_reason_counts: dict[str, int] = {}
    aggregate_expected_next_action_counts: dict[str, int] = {}
    aggregate_budget_status_counts: dict[str, int] = {}
    aggregate_risk_promotion_source_confidence_counts: dict[str, int] = {}
    aggregate_scoreboard_resolution_status_counts: dict[str, int] = {}
    aggregate_db_context_inclusion_status_counts: dict[str, int] = {}
    aggregate_db_context_stale_blocker_counts: dict[str, int] = {}
    aggregate_quarter_revision_review_status_counts: dict[str, int] = {}
    aggregate_quarter_revision_review_count = 0
    aggregate_signal_count = 0
    aggregate_order_intent_candidate_count = 0
    aggregate_blocker_count = 0
    aggregate_candidate_blocking_blocker_count = 0
    aggregate_nonblocking_local_blocker_count = 0
    aggregate_minimum_order_policy_rule_counts: dict[str, int] = {}
    aggregate_minimum_order_policy_status_counts: dict[str, int] = {}
    aggregate_minimum_order_policy_order_type_counts: dict[str, int] = {}
    aggregate_minimum_order_policy_count = 0
    aggregate_minimum_order_policy_candidate_count = 0
    aggregate_minimum_order_policy_blocker_count = 0
    aggregate_minimum_order_policy_share_minimum_applies_count = 0
    aggregate_minimum_order_policy_notional_minimum_applies_count = 0
    aggregate_event_cap_usd = 0.0
    aggregate_remaining_notional_usd = 0.0
    aggregate_profit_ratcheted_requested_addon_usd = 0.0
    aggregate_profit_ratcheted_addon_usd = 0.0
    aggregate_profit_ratcheted_blocked_addon_usd = 0.0
    aggregate_risk_promotion_allowed_count = 0
    aggregate_risk_promotion_blocked_count = 0
    for item in items:
        source_key = str(item.get("source") or item.get("status") or "unknown")
        aggregate_source_counts[source_key] = aggregate_source_counts.get(source_key, 0) + 1
        reason = item.get("reason")
        if reason:
            reason_key = str(reason)
            aggregate_reason_counts[reason_key] = aggregate_reason_counts.get(reason_key, 0) + 1
        signal_state = item.get("signal_aggregation_state") if isinstance(item.get("signal_aggregation_state"), dict) else {}
        aggregate_signal_count += _safe_int(signal_state.get("signal_count"))
        aggregate_order_intent_candidate_count += _safe_int(signal_state.get("order_intent_candidate_count"))
        aggregate_blocker_count += _safe_int(signal_state.get("blocker_count"))
        aggregate_candidate_blocking_blocker_count += _safe_int(signal_state.get("candidate_blocking_blocker_count"))
        aggregate_nonblocking_local_blocker_count += _safe_int(signal_state.get("nonblocking_local_blocker_count"))
        for scope, count in (signal_state.get("blocker_scope_counts") or {}).items():
            aggregate_blocker_scope_counts[str(scope)] = aggregate_blocker_scope_counts.get(str(scope), 0) + _safe_int(count)
        for reason, count in (signal_state.get("blocker_reason_counts") or {}).items():
            aggregate_blocker_reason_counts[str(reason)] = aggregate_blocker_reason_counts.get(str(reason), 0) + _safe_int(count)
        for next_action, count in (signal_state.get("expected_next_action_counts") or {}).items():
            next_action_key = str(next_action)
            aggregate_expected_next_action_counts[next_action_key] = (
                aggregate_expected_next_action_counts.get(next_action_key, 0) + _safe_int(count)
            )
        minimum_policy = (
            signal_state.get("minimum_order_policy_summary")
            if isinstance(signal_state.get("minimum_order_policy_summary"), dict)
            else {}
        )
        aggregate_minimum_order_policy_count += _safe_int(minimum_policy.get("policy_count"))
        aggregate_minimum_order_policy_candidate_count += _safe_int(minimum_policy.get("candidate_policy_count"))
        aggregate_minimum_order_policy_blocker_count += _safe_int(minimum_policy.get("blocker_policy_count"))
        aggregate_minimum_order_policy_share_minimum_applies_count += _safe_int(
            minimum_policy.get("share_minimum_applies_count")
        )
        aggregate_minimum_order_policy_notional_minimum_applies_count += _safe_int(
            minimum_policy.get("notional_minimum_applies_count")
        )
        for rule, count in (minimum_policy.get("order_type_minimum_rule_counts") or {}).items():
            rule_key = str(rule)
            aggregate_minimum_order_policy_rule_counts[rule_key] = (
                aggregate_minimum_order_policy_rule_counts.get(rule_key, 0) + _safe_int(count)
            )
        for status_value, count in (minimum_policy.get("minimum_rule_status_counts") or {}).items():
            status_key = str(status_value)
            aggregate_minimum_order_policy_status_counts[status_key] = (
                aggregate_minimum_order_policy_status_counts.get(status_key, 0) + _safe_int(count)
            )
        for order_type, count in (minimum_policy.get("order_type_counts") or {}).items():
            order_type_key = str(order_type)
            aggregate_minimum_order_policy_order_type_counts[order_type_key] = (
                aggregate_minimum_order_policy_order_type_counts.get(order_type_key, 0) + _safe_int(count)
            )
        budget_state = item.get("budget_state") if isinstance(item.get("budget_state"), dict) else {}
        budget_status = str(budget_state.get("budget_status") or budget_state.get("status") or "unknown")
        aggregate_budget_status_counts[budget_status] = aggregate_budget_status_counts.get(budget_status, 0) + 1
        aggregate_event_cap_usd += _safe_float(budget_state.get("event_cap_usd")) or 0.0
        aggregate_remaining_notional_usd += _safe_float(budget_state.get("remaining_notional_usd")) or 0.0
        aggregate_profit_ratcheted_requested_addon_usd += (
            _safe_float(budget_state.get("profit_ratcheted_requested_addon_usd")) or 0.0
        )
        aggregate_profit_ratcheted_addon_usd += _safe_float(budget_state.get("profit_ratcheted_addon_usd")) or 0.0
        aggregate_profit_ratcheted_blocked_addon_usd += (
            _safe_float(budget_state.get("profit_ratcheted_blocked_addon_usd")) or 0.0
        )
        risk_promotion = (
            budget_state.get("risk_promotion_evidence")
            if isinstance(budget_state.get("risk_promotion_evidence"), dict)
            else {}
        )
        if risk_promotion.get("risk_promotion_allowed") is True:
            aggregate_risk_promotion_allowed_count += 1
        elif risk_promotion:
            aggregate_risk_promotion_blocked_count += 1
        source_confidence = str(risk_promotion.get("source_confidence") or "unknown")
        aggregate_risk_promotion_source_confidence_counts[source_confidence] = (
            aggregate_risk_promotion_source_confidence_counts.get(source_confidence, 0) + 1
        )
        scoreboard_state = (
            item.get("scoreboard_resolution_state")
            if isinstance(item.get("scoreboard_resolution_state"), dict)
            else {}
        )
        scoreboard_status = str(scoreboard_state.get("status") or item.get("scoreboard_resolution_status") or "unknown")
        aggregate_scoreboard_resolution_status_counts[scoreboard_status] = (
            aggregate_scoreboard_resolution_status_counts.get(scoreboard_status, 0) + 1
        )
        db_context_state = item.get("db_stat_context_state") if isinstance(item.get("db_stat_context_state"), dict) else {}
        inclusion_status = str(db_context_state.get("llm_context_inclusion_status") or "unknown")
        aggregate_db_context_inclusion_status_counts[inclusion_status] = (
            aggregate_db_context_inclusion_status_counts.get(inclusion_status, 0) + 1
        )
        for blocker, count in (db_context_state.get("stale_blocker_counts") or {}).items():
            blocker_key = str(blocker)
            aggregate_db_context_stale_blocker_counts[blocker_key] = (
                aggregate_db_context_stale_blocker_counts.get(blocker_key, 0) + _safe_int(count)
            )
        quarter_revision_state = (
            item.get("quarter_revision_state") if isinstance(item.get("quarter_revision_state"), dict) else {}
        )
        aggregate_quarter_revision_review_count += _safe_int(
            quarter_revision_state.get("quarter_revision_review_count")
        )
        for status_value, count in (quarter_revision_state.get("review_status_counts") or {}).items():
            status_key = str(status_value)
            aggregate_quarter_revision_review_status_counts[status_key] = (
                aggregate_quarter_revision_review_status_counts.get(status_key, 0) + _safe_int(count)
            )
    recorded_count = sum(1 for item in items if item.get("status") == "recorded")
    missing_count = len(items) - recorded_count
    return {
        "schema_version": "postgame_canonical_live_event_state_summary_v1",
        "schema_contract": _postgame_canonical_live_event_state_schema_contract(),
        "status": "recorded" if recorded_count and not missing_count else "partial" if recorded_count else "missing",
        "source": "janus_live_event_state_v1",
        "session_date": day,
        "event_count": len(items),
        "recorded_event_count": recorded_count,
        "missing_event_count": missing_count,
        "aggregate_source_counts": dict(sorted(aggregate_source_counts.items())),
        "aggregate_reason_counts": dict(sorted(aggregate_reason_counts.items())),
        "aggregate_scoreboard_resolution_status_counts": dict(
            sorted(aggregate_scoreboard_resolution_status_counts.items())
        ),
        "aggregate_blocker_scope_counts": dict(sorted(aggregate_blocker_scope_counts.items())),
        "aggregate_blocker_reason_counts": dict(sorted(aggregate_blocker_reason_counts.items())),
        "aggregate_expected_next_action_counts": dict(sorted(aggregate_expected_next_action_counts.items())),
        "aggregate_signal_count": aggregate_signal_count,
        "aggregate_order_intent_candidate_count": aggregate_order_intent_candidate_count,
        "aggregate_blocker_count": aggregate_blocker_count,
        "aggregate_candidate_blocking_blocker_count": aggregate_candidate_blocking_blocker_count,
        "aggregate_nonblocking_local_blocker_count": aggregate_nonblocking_local_blocker_count,
        "aggregate_minimum_order_policy_state": {
            "schema_version": "postgame_canonical_minimum_order_policy_summary_v1",
            "schema_contract": _postgame_canonical_minimum_order_policy_schema_contract(),
            "source_schema": "janus_live_event_state_minimum_order_policy_summary_v1",
            "status": "recorded" if aggregate_minimum_order_policy_count else "not_recorded",
            "policy_count": aggregate_minimum_order_policy_count,
            "candidate_policy_count": aggregate_minimum_order_policy_candidate_count,
            "blocker_policy_count": aggregate_minimum_order_policy_blocker_count,
            "order_type_minimum_rule_counts": dict(sorted(aggregate_minimum_order_policy_rule_counts.items())),
            "minimum_rule_status_counts": dict(sorted(aggregate_minimum_order_policy_status_counts.items())),
            "order_type_counts": dict(sorted(aggregate_minimum_order_policy_order_type_counts.items())),
            "share_minimum_applies_count": aggregate_minimum_order_policy_share_minimum_applies_count,
            "notional_minimum_applies_count": aggregate_minimum_order_policy_notional_minimum_applies_count,
            "execution_authority": False,
        },
        "aggregate_budget_state": {
            "schema_version": "postgame_canonical_budget_state_summary_v1",
            "source_schema": "janus_live_event_state_budget_state_v1",
            "status": "recorded" if items else "missing",
            "budget_status_counts": dict(sorted(aggregate_budget_status_counts.items())),
            "event_cap_usd": round(aggregate_event_cap_usd, 6),
            "remaining_notional_usd": round(aggregate_remaining_notional_usd, 6),
            "profit_ratcheted_requested_addon_usd": round(aggregate_profit_ratcheted_requested_addon_usd, 6),
            "profit_ratcheted_addon_usd": round(aggregate_profit_ratcheted_addon_usd, 6),
            "profit_ratcheted_blocked_addon_usd": round(aggregate_profit_ratcheted_blocked_addon_usd, 6),
            "risk_promotion_allowed_count": aggregate_risk_promotion_allowed_count,
            "risk_promotion_blocked_count": aggregate_risk_promotion_blocked_count,
            "risk_promotion_source_confidence_counts": dict(
                sorted(aggregate_risk_promotion_source_confidence_counts.items())
            ),
            "execution_authority": False,
        },
        "aggregate_db_context_inclusion_status_counts": dict(
            sorted(aggregate_db_context_inclusion_status_counts.items())
        ),
        "aggregate_db_context_stale_blocker_counts": dict(sorted(aggregate_db_context_stale_blocker_counts.items())),
        "aggregate_quarter_revision_review_count": aggregate_quarter_revision_review_count,
        "aggregate_quarter_revision_review_status_counts": dict(
            sorted(aggregate_quarter_revision_review_status_counts.items())
        ),
        "items": items,
        "execution_authority": False,
    }


def _postgame_canonical_live_event_state_item(*, day: str | None, event_id: str) -> dict[str, Any]:
    latest_path = _janus_live_event_state_latest_path(event_id, day=day)
    history_path = latest_path.parent / "history.jsonl"
    readback_endpoints = _janus_live_event_state_readback_endpoints(event_id, day=day)
    latest_state = read_json(latest_path)
    history_count = _jsonl_line_count(history_path)
    if not isinstance(latest_state, dict):
        tick_state = _latest_live_worker_tick_state(day=day, event_id=event_id)
        if tick_state.get("status") == "recorded":
            signal_state = (
                tick_state.get("signal_aggregation_state")
                if isinstance(tick_state.get("signal_aggregation_state"), dict)
                else {}
            )
            budget_state = tick_state.get("budget_state") if isinstance(tick_state.get("budget_state"), dict) else {}
            scoreboard = (
                tick_state.get("scoreboard_resolution")
                if isinstance(tick_state.get("scoreboard_resolution"), dict)
                else {}
            )
            game_state = tick_state.get("game_state") if isinstance(tick_state.get("game_state"), dict) else {}
            db_context_state = _postgame_db_stat_context_state(
                day=day,
                event_id=event_id,
                existing_state=None,
            )
            quarter_revision_state = _postgame_quarter_revision_state(
                day=day,
                event_id=event_id,
                existing_state=None,
            )
            return {
                "schema_version": "postgame_canonical_live_event_state_item_v1",
                "status": "recorded",
                "source": "live_strategy_worker_tick_history_fallback",
                "source_schema": tick_state.get("schema_version"),
                "event_id": event_id,
                "latest_path": str(latest_path),
                "history_path": str(history_path),
                "readback_endpoints": readback_endpoints,
                "history_count": history_count,
                "reason": "janus_live_event_state_latest_missing_tick_history_used",
                "latest_generated_at_utc": None,
                "latest_tick_status": tick_state.get("status"),
                "latest_tick_finished_at_utc": tick_state.get("tick_finished_at_utc"),
                "scoreboard_resolution_status": scoreboard.get("status"),
                "selected_game_id": scoreboard.get("selected_game_id"),
                "scoreboard_resolution_state": _postgame_scoreboard_resolution_state(scoreboard),
                "game_state_status": game_state.get("status"),
                "game_status": game_state.get("game_status"),
                "period": game_state.get("period"),
                "clock": game_state.get("clock"),
                "db_stat_context_state": db_context_state,
                "quarter_revision_state": quarter_revision_state,
                "signal_aggregation_state": {
                    "schema_version": signal_state.get("schema_version"),
                    "status": signal_state.get("status"),
                    "decision_type": signal_state.get("decision_type"),
                    "signal_count": signal_state.get("signal_count"),
                    "order_intent_candidate_count": signal_state.get("order_intent_candidate_count"),
                    "blocker_count": signal_state.get("blocker_count"),
                    "candidate_blocking_blocker_count": signal_state.get("candidate_blocking_blocker_count"),
                    "nonblocking_local_blocker_count": signal_state.get("nonblocking_local_blocker_count"),
                    "blocker_reason_counts": signal_state.get("blocker_reason_counts") or {},
                    "blocker_scope_counts": signal_state.get("blocker_scope_counts") or {},
                    "expected_next_action_counts": signal_state.get("expected_next_action_counts") or {},
                    "minimum_order_policy_summary": signal_state.get("minimum_order_policy_summary") or {},
                },
                "budget_state": {
                    "schema_version": budget_state.get("schema_version"),
                    "status": budget_state.get("status"),
                    "budget_status": budget_state.get("budget_status"),
                    "event_cap_usd": budget_state.get("event_cap_usd"),
                    "remaining_notional_usd": budget_state.get("remaining_notional_usd"),
                    "profit_ratcheted_requested_addon_usd": budget_state.get("profit_ratcheted_requested_addon_usd"),
                    "profit_ratcheted_addon_usd": budget_state.get("profit_ratcheted_addon_usd"),
                    "profit_ratcheted_blocked_addon_usd": budget_state.get("profit_ratcheted_blocked_addon_usd"),
                    "risk_promotion_evidence": budget_state.get("risk_promotion_evidence"),
                },
                "execution_authority": False,
            }
        return {
            "schema_version": "postgame_canonical_live_event_state_item_v1",
            "status": "missing",
            "source": "missing",
            "event_id": event_id,
            "latest_path": str(latest_path),
            "history_path": str(history_path),
            "readback_endpoints": readback_endpoints,
            "history_count": history_count,
            "reason": "janus_live_event_state_latest_missing",
            "execution_authority": False,
        }

    signal_state = latest_state.get("signal_aggregation_state") if isinstance(latest_state.get("signal_aggregation_state"), dict) else {}
    budget_state = latest_state.get("budget_state") if isinstance(latest_state.get("budget_state"), dict) else {}
    scoreboard = latest_state.get("scoreboard_resolution") if isinstance(latest_state.get("scoreboard_resolution"), dict) else {}
    game_state = latest_state.get("game_state") if isinstance(latest_state.get("game_state"), dict) else {}
    db_context_state = (
        latest_state.get("db_stat_context_state") if isinstance(latest_state.get("db_stat_context_state"), dict) else {}
    )
    db_context_state = _postgame_db_stat_context_state(
        day=day,
        event_id=event_id,
        existing_state=db_context_state,
    )
    quarter_revision_state = (
        latest_state.get("quarter_revision_state") if isinstance(latest_state.get("quarter_revision_state"), dict) else {}
    )
    quarter_revision_state = _postgame_quarter_revision_state(
        day=day,
        event_id=event_id,
        existing_state=quarter_revision_state,
    )
    tick_state = (
        latest_state.get("latest_live_worker_tick_state")
        if isinstance(latest_state.get("latest_live_worker_tick_state"), dict)
        else {}
    )
    return {
        "schema_version": "postgame_canonical_live_event_state_item_v1",
        "status": "recorded",
        "source": "janus_live_event_state_latest_json",
        "event_id": event_id,
        "latest_path": str(latest_path),
        "history_path": str(history_path),
        "readback_endpoints": readback_endpoints,
        "history_count": history_count,
        "latest_generated_at_utc": latest_state.get("generated_at_utc"),
        "latest_tick_status": tick_state.get("status"),
        "latest_tick_finished_at_utc": tick_state.get("tick_finished_at_utc"),
        "scoreboard_resolution_status": scoreboard.get("status"),
        "selected_game_id": scoreboard.get("selected_game_id"),
        "scoreboard_resolution_state": _postgame_scoreboard_resolution_state(scoreboard),
        "game_state_status": game_state.get("status"),
        "game_status": game_state.get("game_status"),
        "period": game_state.get("period"),
        "clock": game_state.get("clock"),
        "db_stat_context_state": db_context_state,
        "quarter_revision_state": quarter_revision_state,
        "signal_aggregation_state": {
            "schema_version": signal_state.get("schema_version"),
            "status": signal_state.get("status"),
            "decision_type": signal_state.get("decision_type"),
            "signal_count": signal_state.get("signal_count"),
            "order_intent_candidate_count": signal_state.get("order_intent_candidate_count"),
            "blocker_count": signal_state.get("blocker_count"),
            "candidate_blocking_blocker_count": signal_state.get("candidate_blocking_blocker_count"),
            "nonblocking_local_blocker_count": signal_state.get("nonblocking_local_blocker_count"),
            "blocker_reason_counts": signal_state.get("blocker_reason_counts") or {},
            "blocker_scope_counts": signal_state.get("blocker_scope_counts") or {},
            "expected_next_action_counts": signal_state.get("expected_next_action_counts") or {},
            "minimum_order_policy_summary": signal_state.get("minimum_order_policy_summary") or {},
        },
        "budget_state": {
            "schema_version": budget_state.get("schema_version"),
            "status": budget_state.get("status"),
            "budget_status": budget_state.get("budget_status"),
            "event_cap_usd": budget_state.get("event_cap_usd"),
            "remaining_notional_usd": budget_state.get("remaining_notional_usd"),
            "profit_ratcheted_requested_addon_usd": budget_state.get("profit_ratcheted_requested_addon_usd"),
            "profit_ratcheted_addon_usd": budget_state.get("profit_ratcheted_addon_usd"),
            "profit_ratcheted_blocked_addon_usd": budget_state.get("profit_ratcheted_blocked_addon_usd"),
            "risk_promotion_evidence": budget_state.get("risk_promotion_evidence"),
        },
        "execution_authority": False,
}


def _postgame_db_stat_context_state(
    *,
    day: str | None,
    event_id: str,
    existing_state: dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(existing_state, dict) and existing_state.get("status") == "recorded":
        return existing_state
    state = _build_db_stat_context_state(event_id, day=day)
    if state.get("status") == "recorded":
        return {
            **state,
            "source": "agent_context_fallback",
            "reason": "canonical_live_event_state_db_stat_context_missing",
        }
    if isinstance(existing_state, dict) and existing_state:
        return existing_state
    return state


def _postgame_quarter_revision_state(
    *,
    day: str | None,
    event_id: str,
    existing_state: dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(existing_state, dict) and existing_state.get("status") == "recorded":
        return _with_quarter_revision_schema_contract(existing_state)
    llm_runtime_status = load_latest_llm_runtime_status(session_date=day, event_ids=[event_id])
    state = _build_quarter_revision_state(llm_runtime_status, event_id=event_id)
    if state.get("status") == "recorded":
        return {
            **state,
            "source": "llm_runtime_status_fallback",
            "reason": "canonical_live_event_state_quarter_revision_missing",
        }
    if isinstance(existing_state, dict) and existing_state:
        return _with_quarter_revision_schema_contract(existing_state)
    return _with_quarter_revision_schema_contract(state)


def _with_quarter_revision_schema_contract(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("schema_contract"):
        return state
    return {
        **state,
        "schema_contract": _janus_live_event_state_quarter_revision_schema_contract(),
    }


def _postgame_scoreboard_resolution_state(scoreboard: dict[str, Any]) -> dict[str, Any]:
    candidates = scoreboard.get("candidate_games")
    if not isinstance(candidates, list):
        candidates = scoreboard.get("candidates") if isinstance(scoreboard.get("candidates"), list) else []
    compact_candidates: list[dict[str, Any]] = []
    for candidate in candidates[:8]:
        if not isinstance(candidate, dict):
            continue
        compact_candidates.append(
            {
                "game_id": candidate.get("game_id") or candidate.get("id"),
                "game_date": candidate.get("game_date") or candidate.get("date"),
                "home_team": candidate.get("home_team") or candidate.get("home"),
                "away_team": candidate.get("away_team") or candidate.get("away"),
                "match_status": candidate.get("match_status") or candidate.get("status"),
            }
        )
    searched_date_windows = scoreboard.get("searched_date_windows")
    if not isinstance(searched_date_windows, list):
        searched_date_windows = scoreboard.get("date_windows") if isinstance(scoreboard.get("date_windows"), list) else []

    return {
        "schema_version": "postgame_scoreboard_resolution_readback_v1",
        "status": scoreboard.get("status") or "not_recorded",
        "source_schema": scoreboard.get("schema_version"),
        "selected_game_id": scoreboard.get("selected_game_id"),
        "safe_blocker_reason": scoreboard.get("safe_blocker_reason")
        or scoreboard.get("blocker_reason")
        or scoreboard.get("reason"),
        "execution_blocking_if_required": bool(scoreboard.get("execution_blocking_if_required")),
        "parsed_event_date": scoreboard.get("parsed_event_date") or scoreboard.get("event_date"),
        "parsed_teams": scoreboard.get("parsed_teams") or scoreboard.get("parsed_team_aliases") or {},
        "searched_date_windows": searched_date_windows,
        "candidate_count": len(candidates),
        "candidate_game_ids": [item.get("game_id") for item in compact_candidates if item.get("game_id")],
        "candidate_games": compact_candidates,
    }


def _jsonl_line_count(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    count += 1
    except OSError:
        return 0
    return count


def _read_janus_live_event_state_history(path: Path, *, limit: int) -> dict[str, Any]:
    effective_limit = min(max(_safe_int(limit), 1), 500)
    records: list[dict[str, Any]] = []
    total_record_count = 0
    invalid_line_count = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    invalid_line_count += 1
                    continue
                if not isinstance(record, dict):
                    invalid_line_count += 1
                    continue
                total_record_count += 1
                records.append(
                    {
                        "line_number": line_number,
                        "schema_version": record.get("schema_version"),
                        "generated_at_utc": record.get("generated_at_utc"),
                        "event_identity": record.get("event_identity") or {},
                        "latest_live_worker_tick_state": record.get("latest_live_worker_tick_state") or {},
                        "scoreboard_resolution": record.get("scoreboard_resolution") or {},
                        "game_state": record.get("game_state") or {},
                        "signal_aggregation_state": record.get("signal_aggregation_state") or {},
                        "budget_state": record.get("budget_state") or {},
                        "blockers": record.get("blockers") or [],
                        "execution_authority": False,
                    }
                )
                if len(records) > effective_limit:
                    records = records[-effective_limit:]
    except OSError as exc:
        return {
            "status": "error",
            "reason": "janus_live_event_state_history_read_error",
            "error": str(exc),
            "requested_limit": limit,
            "effective_limit": effective_limit,
            "total_record_count": 0,
            "returned_record_count": 0,
            "invalid_line_count": invalid_line_count,
            "records": [],
        }

    return {
        "status": "recorded",
        "order": "oldest_to_newest_tail",
        "requested_limit": limit,
        "effective_limit": effective_limit,
        "total_record_count": total_record_count,
        "returned_record_count": len(records),
        "invalid_line_count": invalid_line_count,
        "records": records,
    }


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
    postgame_canonical_live_event_state = _build_postgame_canonical_live_event_state_summary(
        day=payload.session_date,
        event_ids=reviewed_event_ids,
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
            "postgame_canonical_live_event_state": postgame_canonical_live_event_state,
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
        "postgame_canonical_live_event_state": postgame_canonical_live_event_state,
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
    account_id, account_resolution = _resolve_postgame_account_id(connection, payload)
    if not account_id:
        return {
            "status": "skipped",
            "reason": account_resolution.get("reason") or "account_id_required",
            "event_count": len(event_ids),
            "account_resolution": account_resolution,
            "unresolved_evidence": [
                {
                    "reason": account_resolution.get("reason") or "account_id_required",
                    "source_confidence": "inferred",
                }
            ],
            "items": [],
        }

    direct_unresolved: list[dict[str, Any]] = []
    try:
        direct_context = _resolve_order_lifecycle_direct_context(
            connection,
            account_id=account_id,
            direct_open_order_external_id=None,
            direct_open_order_count=None,
            direct_open_position_count=None,
            include_direct_clob_evidence=payload.include_direct_clob_evidence,
        )
    except Exception as exc:  # noqa: BLE001
        direct_unresolved = [
            {
                "reason": "direct_account_context_unavailable",
                "error": _exception_detail(exc),
                "source_confidence": "inferred",
            }
        ]
        direct_context = {
            "direct_open_order_external_ids": [],
            "direct_open_order_count": None,
            "direct_open_position_count": None,
            "direct_trade_rows": [],
            "direct_evidence": {
                "enabled": True,
                "ok": False,
                "error": _exception_detail(exc),
                "unresolved_evidence": direct_unresolved,
            },
        }

    direct_evidence = direct_context.get("direct_evidence") or {}
    direct_evidence_summary = {
        key: value for key, value in direct_evidence.items() if key not in {"trades", "open_orders", "open_positions"}
    }
    if direct_unresolved:
        direct_evidence_summary["unresolved_evidence"] = direct_unresolved
    account_return_report = _build_postgame_account_activity_return_report(
        connection,
        account_id=account_id,
        event_ids=event_ids,
    )
    account_return_by_event = {
        str(item.get("event_id") or item.get("event_slug")): item
        for item in account_return_report.get("items", [])
        if isinstance(item, dict)
    }
    start_time, end_time = _postgame_account_window(day)
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
                account_id=account_id,
                event_slug=event_id,
                start_time=start_time,
                end_time=end_time,
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
                    "unresolved_evidence": direct_unresolved,
                    "reconciliation": lifecycle_report,
                    "pnl_attribution": pnl_attribution,
                    "account_return": account_return_by_event.get(event_id),
                }
            )
        except Exception as exc:  # noqa: BLE001
            items.append(
                {
                    "ok": False,
                    "event_id": event_id,
                    "event_slug": event_id,
                    "error": _exception_detail(exc),
                    "account_return": account_return_by_event.get(event_id),
                    "unresolved_evidence": [
                        {
                            "reason": "event_pnl_attribution_failed",
                            "error": _exception_detail(exc),
                            "source_confidence": "inferred",
                        }
                    ],
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
            "account_id": account_id,
            "account_resolution": account_resolution,
            "unresolved_evidence": direct_unresolved,
            "direct_evidence": direct_evidence_summary,
            "direct_trade_trust_summary": _build_postgame_direct_trade_trust_summary(items),
            "account_return_summary": account_return_report,
            "items": items,
        }
    )


def _build_postgame_direct_trade_trust_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    reason_counts: dict[str, int] = {}
    event_rows: list[dict[str, Any]] = []
    scoped_event_count = 0
    trusted_trade_count = 0
    untrusted_trade_count = 0
    all_observed_trade_count = 0
    unresolved_event_count = 0
    policy = "requires_valid_trade_timestamp_for_current_account_evidence"

    for item in items:
        direct_scope = item.get("direct_event_scope") if isinstance(item.get("direct_event_scope"), dict) else {}
        event_id = str(item.get("event_id") or item.get("event_slug") or "")
        if not direct_scope:
            unresolved_event_count += 1
            event_rows.append(
                {
                    "event_id": event_id,
                    "status": "missing_direct_event_scope",
                    "trusted_trade_count": 0,
                    "untrusted_trade_count": 0,
                    "all_observed_trade_count": 0,
                    "account_pnl_input_policy": "trusted_trades_only",
                }
            )
            continue

        if direct_scope.get("status") == "scoped" or direct_scope.get("scoped"):
            scoped_event_count += 1
        else:
            unresolved_event_count += 1
        event_trusted = _safe_int(direct_scope.get("trusted_trade_count"))
        event_untrusted = _safe_int(direct_scope.get("untrusted_trade_count"))
        event_all_observed = _safe_int(direct_scope.get("all_observed_trade_count"))
        trusted_trade_count += event_trusted
        untrusted_trade_count += event_untrusted
        all_observed_trade_count += event_all_observed
        if direct_scope.get("trade_trust_policy"):
            policy = str(direct_scope.get("trade_trust_policy"))
        for reason, count in (direct_scope.get("untrusted_trade_reasons") or {}).items():
            reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + _safe_int(count)
        event_rows.append(
            {
                "event_id": event_id,
                "status": direct_scope.get("status") or "unknown",
                "trusted_trade_count": event_trusted,
                "untrusted_trade_count": event_untrusted,
                "all_observed_trade_count": event_all_observed,
                "untrusted_trade_reasons": direct_scope.get("untrusted_trade_reasons") or {},
                "account_pnl_input_policy": "trusted_trades_only",
            }
        )

    return {
        "schema_version": "postgame_direct_trade_trust_summary_v1",
        "schema_contract": _postgame_direct_trade_trust_summary_schema_contract(),
        "status": "trusted_only" if untrusted_trade_count == 0 else "untrusted_rows_quarantined",
        "trade_trust_policy": policy,
        "account_pnl_input_policy": "trusted_trades_only",
        "event_count": len(items),
        "scoped_event_count": scoped_event_count,
        "unresolved_event_count": unresolved_event_count,
        "trusted_trade_count": trusted_trade_count,
        "untrusted_trade_count": untrusted_trade_count,
        "all_observed_trade_count": all_observed_trade_count,
        "untrusted_trade_reason_counts": dict(sorted(reason_counts.items())),
        "items": event_rows,
        "execution_authority": False,
    }


def _postgame_direct_trade_trust_summary_schema_contract() -> dict[str, Any]:
    return {
        "schema_version": "postgame_direct_trade_trust_summary_contract_v1",
        "primary_schema": "postgame_direct_trade_trust_summary_v1",
        "source_schema": "postgame_direct_event_scope_v1",
        "execution_authority": False,
        "required_sections": [
            "status",
            "trade_trust_policy",
            "account_pnl_input_policy",
            "event_count",
            "scoped_event_count",
            "unresolved_event_count",
            "trusted_trade_count",
            "untrusted_trade_count",
            "all_observed_trade_count",
            "untrusted_trade_reason_counts",
            "items",
            "execution_authority",
        ],
        "item_required_sections": [
            "event_id",
            "status",
            "trusted_trade_count",
            "untrusted_trade_count",
            "all_observed_trade_count",
            "untrusted_trade_reasons",
            "account_pnl_input_policy",
        ],
        "consumer_notes": [
            "Trusted trades are the only rows eligible for account-PnL input.",
            "Untrusted timestamp-zero or invalid-time rows remain market-tape evidence only.",
            "This summary is postgame evidence only and never authorizes order action.",
        ],
    }


def _resolve_postgame_account_id(
    connection: PsycopgConnection,
    payload: OpsCycleRequest,
) -> tuple[str | None, dict[str, Any]]:
    explicit = str(payload.account_id or "").strip()
    if explicit:
        return explicit, {
            "status": "explicit",
            "account_id": explicit,
            "source_confidence": "account_confirmed",
        }
    try:
        account = resolve_trading_account(connection, account_id=None)
    except Exception as exc:  # noqa: BLE001
        return None, {
            "status": "unavailable",
            "reason": "default_account_unavailable",
            "error": _exception_detail(exc),
            "source_confidence": "inferred",
        }
    account_id = str(account.get("account_id") or "").strip()
    if not account_id:
        return None, {
            "status": "unavailable",
            "reason": "default_account_missing_account_id",
            "source_confidence": "inferred",
        }
    return account_id, {
        "status": "default_loaded",
        "account_id": account_id,
        "source_confidence": "account_confirmed",
    }


def _postgame_account_window(day: str | None) -> tuple[datetime | None, datetime | None]:
    if not day:
        return None, None
    try:
        start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    except ValueError:
        return None, None
    return start, start + timedelta(days=2)


def _decimal_from_any(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _rounded_float(value: Decimal | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _account_activity_dedupe_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("transactionHash"),
        row.get("timestamp"),
        row.get("type"),
        row.get("side"),
        row.get("eventSlug") or row.get("slug"),
        row.get("outcome"),
        row.get("size"),
        row.get("price"),
        row.get("usdcSize"),
    )


def _closed_position_dedupe_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("asset"),
        row.get("conditionId"),
        row.get("eventSlug") or row.get("slug"),
        row.get("outcome"),
        row.get("avgPrice"),
        row.get("totalBought"),
        row.get("realizedPnl"),
        row.get("timestamp"),
    )


def _build_account_activity_return_report_from_rows(
    *,
    event_ids: list[str],
    activity_rows: list[dict[str, Any]],
    closed_position_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    event_set = set(event_ids)
    items: list[dict[str, Any]] = []

    closed_by_event: dict[str, list[dict[str, Any]]] = {event_id: [] for event_id in event_ids}
    closed_seen: set[tuple[Any, ...]] = set()
    for raw_row in closed_position_rows:
        if not isinstance(raw_row, dict):
            continue
        event_slug = str(raw_row.get("eventSlug") or raw_row.get("slug") or "").strip()
        if event_slug not in event_set:
            continue
        key = _closed_position_dedupe_key(raw_row)
        if key in closed_seen:
            continue
        closed_seen.add(key)
        closed_by_event.setdefault(event_slug, []).append(raw_row)

    activity_by_event: dict[str, list[dict[str, Any]]] = {event_id: [] for event_id in event_ids}
    activity_seen: set[tuple[Any, ...]] = set()
    for raw_row in activity_rows:
        if not isinstance(raw_row, dict):
            continue
        event_slug = str(raw_row.get("eventSlug") or raw_row.get("slug") or "").strip()
        if event_slug not in event_set:
            continue
        key = _account_activity_dedupe_key(raw_row)
        if key in activity_seen:
            continue
        activity_seen.add(key)
        activity_by_event.setdefault(event_slug, []).append(raw_row)

    for event_id in event_ids:
        event_activity = sorted(
            activity_by_event.get(event_id, []),
            key=lambda row: (row.get("timestamp") or 0, str(row.get("transactionHash") or "")),
        )
        gross_buy_turnover = Decimal("0")
        sell_proceeds = Decimal("0")
        redeem_proceeds = Decimal("0")
        activity_net_cashflow = Decimal("0")
        cumulative_cashflow = Decimal("0")
        peak_cash_at_risk = Decimal("0")
        buy_count = 0
        sell_count = 0
        redeem_count = 0

        for row in event_activity:
            row_type = str(row.get("type") or "").strip().upper()
            side = str(row.get("side") or "").strip().upper()
            size = _decimal_from_any(row.get("size"))
            price = _decimal_from_any(row.get("price"))
            usdc = _decimal_from_any(row.get("usdcSize"))
            if not usdc and size and price:
                usdc = size * price

            cashflow = Decimal("0")
            if row_type == "TRADE" and side == "BUY":
                buy_count += 1
                gross_buy_turnover += usdc
                cashflow = -usdc
            elif row_type == "TRADE" and side == "SELL":
                sell_count += 1
                sell_proceeds += usdc
                cashflow = usdc
            elif row_type == "REDEEM":
                redeem_count += 1
                if not usdc:
                    usdc = size
                redeem_proceeds += usdc
                cashflow = usdc
            else:
                continue

            activity_net_cashflow += cashflow
            cumulative_cashflow += cashflow
            if cumulative_cashflow < 0:
                peak_cash_at_risk = max(peak_cash_at_risk, -cumulative_cashflow)

        closed_rows = closed_by_event.get(event_id, [])
        closed_position_realized_pnl = sum(
            (_decimal_from_any(row.get("realizedPnl")) for row in closed_rows),
            Decimal("0"),
        )
        if closed_rows:
            actual_pnl = closed_position_realized_pnl
            actual_source = "polymarket_closed_positions"
            source_confidence = "account_confirmed"
            status_text = "ready"
        elif event_activity:
            actual_pnl = activity_net_cashflow
            actual_source = "polymarket_account_activity_cashflow"
            source_confidence = "account_confirmed"
            status_text = "ready"
        else:
            actual_pnl = Decimal("0")
            actual_source = "flat_no_account_activity"
            source_confidence = "account_confirmed"
            status_text = "flat_no_trades"

        accounting_notes: list[dict[str, Any]] = []
        if closed_rows and abs(closed_position_realized_pnl - activity_net_cashflow) > Decimal("0.01"):
            accounting_notes.append(
                {
                    "reason": "closed_position_pnl_overrides_activity_cashflow",
                    "activity_net_cashflow_usd": _rounded_float(activity_net_cashflow),
                    "closed_position_realized_pnl_usd": _rounded_float(closed_position_realized_pnl),
                    "source_confidence": "account_confirmed",
                }
            )

        return_on_peak = None
        if peak_cash_at_risk > 0:
            return_on_peak = actual_pnl / peak_cash_at_risk * Decimal("100")

        items.append(
            {
                "event_id": event_id,
                "event_slug": event_id,
                "status": status_text,
                "source": "polymarket_account_activity_return_v1",
                "source_confidence": source_confidence,
                "actual_pnl_usd": _rounded_float(actual_pnl),
                "actual_pnl_source": actual_source,
                "return_on_peak_cash_at_risk_pct": _rounded_float(return_on_peak),
                "peak_cash_at_risk_usd": _rounded_float(peak_cash_at_risk),
                "gross_buy_turnover_usd": _rounded_float(gross_buy_turnover),
                "gross_buy_turnover_is_invested_capital": False,
                "sell_proceeds_usd": _rounded_float(sell_proceeds),
                "redeem_proceeds_usd": _rounded_float(redeem_proceeds),
                "account_activity_net_cashflow_usd": _rounded_float(activity_net_cashflow),
                "closed_position_realized_pnl_usd": _rounded_float(closed_position_realized_pnl)
                if closed_rows
                else None,
                "activity_trade_count": buy_count + sell_count,
                "activity_buy_count": buy_count,
                "activity_sell_count": sell_count,
                "activity_redeem_count": redeem_count,
                "closed_position_count": len(closed_rows),
                "accounting_notes": accounting_notes,
                "unresolved_evidence": [],
            }
        )

    total_actual_pnl = sum(
        (_decimal_from_any(item.get("actual_pnl_usd")) for item in items if item.get("actual_pnl_usd") is not None),
        Decimal("0"),
    )
    total_peak_cash = sum(
        (_decimal_from_any(item.get("peak_cash_at_risk_usd")) for item in items if item.get("peak_cash_at_risk_usd") is not None),
        Decimal("0"),
    )
    return_on_total_peak = None
    if total_peak_cash > 0:
        return_on_total_peak = total_actual_pnl / total_peak_cash * Decimal("100")
    return {
        "schema_version": "polymarket_account_activity_return_report_v1",
        "status": "ready",
        "source": "polymarket_account_activity_and_closed_positions",
        "source_confidence": "account_confirmed",
        "event_count": len(items),
        "ready_event_count": sum(1 for item in items if item.get("status") in {"ready", "flat_no_trades"}),
        "total_actual_pnl_usd": _rounded_float(total_actual_pnl),
        "sum_event_peak_cash_at_risk_usd": _rounded_float(total_peak_cash),
        "return_on_sum_event_peak_cash_at_risk_pct": _rounded_float(return_on_total_peak),
        "total_gross_buy_turnover_usd": _rounded_float(
            sum((_decimal_from_any(item.get("gross_buy_turnover_usd")) for item in items), Decimal("0"))
        ),
        "total_sell_proceeds_usd": _rounded_float(
            sum((_decimal_from_any(item.get("sell_proceeds_usd")) for item in items), Decimal("0"))
        ),
        "total_redeem_proceeds_usd": _rounded_float(
            sum((_decimal_from_any(item.get("redeem_proceeds_usd")) for item in items), Decimal("0"))
        ),
        "items": items,
        "accounting_policy": {
            "gross_buy_turnover_is_invested_capital": False,
            "invested_capital_metric": "peak_cash_at_risk_usd",
            "actual_return_metric": "actual_pnl_usd",
            "closed_position_pnl_precedence": True,
            "market_tape_account_pnl_eligible": False,
        },
    }


def _base_wallet_address(value: Any) -> str | None:
    text = str(value or "").strip().strip('"').strip("'")
    if text.startswith("0x") and len(text) >= 42:
        return text[:42]
    return None


def _fetch_data_api_rows_for_events(
    client: PolymarketDataClient,
    *,
    path: str,
    user: str,
    event_ids: list[str],
    page_limit: int = 100,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    event_set = set(event_ids)
    rows: list[dict[str, Any]] = []
    for page in range(max_pages):
        params = {
            "user": user,
            "limit": page_limit,
            "offset": page * page_limit,
        }
        if path == "/activity":
            params.update({"sortBy": "TIMESTAMP", "sortDirection": "DESC"})
        batch = client._request("GET", path, params=params)
        if not isinstance(batch, list) or not batch:
            break
        for row in batch:
            if not isinstance(row, dict):
                continue
            event_slug = str(row.get("eventSlug") or row.get("slug") or "").strip()
            if event_slug in event_set:
                rows.append(row)
        if len(batch) < page_limit:
            break
    return rows


def _build_postgame_account_activity_return_report(
    connection: PsycopgConnection,
    *,
    account_id: str,
    event_ids: list[str],
) -> dict[str, Any]:
    try:
        account = resolve_trading_account(connection, account_id=account_id)
        wallet = _base_wallet_address(account.get("proxy_wallet_address")) or _base_wallet_address(
            account.get("wallet_address")
        )
        if not wallet:
            return {
                "status": "review_required",
                "reason": "account_wallet_missing",
                "source_confidence": "inferred",
                "items": [],
            }
        client = PolymarketDataClient(timeout=12.0)
        activity_rows = _fetch_data_api_rows_for_events(
            client,
            path="/activity",
            user=wallet,
            event_ids=event_ids,
        )
        closed_position_rows = _fetch_data_api_rows_for_events(
            client,
            path="/closed-positions",
            user=wallet,
            event_ids=event_ids,
        )
        report = _build_account_activity_return_report_from_rows(
            event_ids=event_ids,
            activity_rows=activity_rows,
            closed_position_rows=closed_position_rows,
        )
        return {
            **report,
            "account_id": account_id,
            "wallet_address": wallet,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "schema_version": "polymarket_account_activity_return_report_v1",
            "status": "review_required",
            "reason": "account_activity_return_unavailable",
            "error": _exception_detail(exc),
            "source_confidence": "inferred",
            "unresolved_evidence": [
                {
                    "reason": "account_activity_return_unavailable",
                    "error": _exception_detail(exc),
                    "source_confidence": "inferred",
                }
            ],
            "items": [],
        }


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
    replay_inputs = _read_postgame_replay_tick_stream_summaries(day=day, event_ids=reviewed_event_ids)
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
    llm_usage_analysis = _build_postgame_llm_usage_analysis(day=day, reviewed_event_ids=reviewed_event_ids)
    blocker_efficacy_review = _build_postgame_blocker_efficacy_review(
        replay_inputs=replay_inputs,
        why_no_trade=why_no_trade,
    )
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
                "runtime_artifact": "Metric is backed by local runtime artifacts such as live-worker ticks or LLM traces.",
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
            "llm_usage_analysis": llm_usage_analysis,
            "blocker_efficacy_review": blocker_efficacy_review,
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
        cashflow = _safe_float(account_pnl.get("actual_pnl_usd"))
        if cashflow is None:
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
            "actual_pnl_usd": round(known_cashflow, 6) if known_cashflow_available else None,
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


def _build_postgame_llm_usage_analysis(*, day: str | None, reviewed_event_ids: list[str]) -> dict[str, Any]:
    if not day:
        return {
            "schema_version": "postgame_llm_usage_analysis_v1",
            "status": "not_checked",
            "reason": "session_date_missing",
            "source_confidence": "runtime_artifact",
            "events": [],
        }
    artifacts_root = ops_artifact_root(day).parent.parent
    day_root = artifacts_root / "llm-runtime" / day
    event_set = {str(event_id) for event_id in reviewed_event_ids}
    rows: list[dict[str, Any]] = []
    model_counts: dict[str, int] = {}
    tier_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    trigger_counts: dict[str, int] = {}
    total_estimated_cost = 0.0
    total_input_tokens = 0
    total_output_tokens = 0
    total_reasoning_tokens = 0
    if day_root.exists():
        for path in sorted(day_root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            event_id = str(payload.get("event_id") or "")
            if event_set and event_id not in event_set:
                continue
            response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
            routing = payload.get("model_routing_decision") if isinstance(payload.get("model_routing_decision"), dict) else {}
            telemetry = payload.get("llm_runtime_telemetry") if isinstance(payload.get("llm_runtime_telemetry"), dict) else {}
            usage = telemetry.get("usage") if isinstance(telemetry.get("usage"), dict) else {}
            selected_model = str(payload.get("selected_model") or response.get("selected_model") or routing.get("selected_model") or "")
            selected_tier = str(routing.get("selected_tier") or "")
            response_status = str(response.get("response_status") or payload.get("response_status") or "unknown")
            model_counts[selected_model or "unknown"] = model_counts.get(selected_model or "unknown", 0) + 1
            tier_counts[selected_tier or "unknown"] = tier_counts.get(selected_tier or "unknown", 0) + 1
            status_counts[response_status] = status_counts.get(response_status, 0) + 1
            for trigger in payload.get("trigger_list") or []:
                if not isinstance(trigger, dict):
                    continue
                trigger_type = str(trigger.get("trigger_type") or "unknown")
                trigger_counts[trigger_type] = trigger_counts.get(trigger_type, 0) + 1
            estimated_cost = _safe_float(telemetry.get("estimated_cost_usd"))
            total_estimated_cost += estimated_cost or 0.0
            total_input_tokens += _safe_int(usage.get("input_tokens"))
            total_output_tokens += _safe_int(usage.get("output_tokens"))
            total_reasoning_tokens += _safe_int(usage.get("reasoning_tokens"))
            rows.append(
                {
                    "event_id": event_id,
                    "path": str(path),
                    "selected_model": selected_model or None,
                    "selected_tier": selected_tier or None,
                    "response_status": response_status,
                    "trigger_types": [
                        str(trigger.get("trigger_type") or "unknown")
                        for trigger in payload.get("trigger_list") or []
                        if isinstance(trigger, dict)
                    ],
                    "estimated_cost_usd": round(estimated_cost, 6) if estimated_cost is not None else None,
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "reasoning_tokens": usage.get("reasoning_tokens"),
                }
            )
    model_role_gaps: list[dict[str, Any]] = []
    if tier_counts.get("nano", 0) <= 0:
        model_role_gaps.append(
            {
                "role": "play_by_play_annotation",
                "expected_model": "gpt-5.4-nano",
                "status": "not_observed",
                "issue": "#81",
                "recommended_action": "wire optional real nano dispatcher while preserving deterministic fallback",
            }
        )
    if tier_counts.get("frontier", 0) <= 0 and any(
        trigger in trigger_counts for trigger in ("position_adverse_move", "garbage_time", "target_placement_failed")
    ):
        model_role_gaps.append(
            {
                "role": "deep_loss_or_critical_revision",
                "expected_model": "gpt-5.5",
                "status": "not_observed",
                "issue": "#83",
                "recommended_action": "escalate only deep loss exposure or critical operator-reviewed windows",
            }
        )
    return {
        "schema_version": "postgame_llm_usage_analysis_v1",
        "status": "recorded" if rows else "missing",
        "source_confidence": "runtime_artifact",
        "artifact_root": str(day_root),
        "event_count": len(reviewed_event_ids),
        "trace_count": len(rows),
        "model_counts": dict(sorted(model_counts.items())),
        "tier_counts": dict(sorted(tier_counts.items())),
        "response_status_counts": dict(sorted(status_counts.items())),
        "trigger_type_counts": dict(sorted(trigger_counts.items())),
        "estimated_cost_usd": round(total_estimated_cost, 6),
        "usage": {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "reasoning_tokens": total_reasoning_tokens,
        },
        "model_role_policy": [
            {
                "role": "pregame_kickoff_or_deep_system_review",
                "model": "gpt-5.5",
                "expected_use": "Codex/frontier kickoff or deep loss exposure review only; not every tick.",
            },
            {
                "role": "main_live_revision_engine",
                "model": "gpt-5.4-mini",
                "expected_use": "Live StrategyPlan revision and critical StrategyPlan review under budget gates.",
            },
            {
                "role": "play_by_play_annotation",
                "model": "gpt-5.4-nano",
                "expected_use": "Cheap per-play tagging feeding evidence and escalating only meaningful windows.",
            },
        ],
        "model_role_gaps": model_role_gaps,
        "rows": rows[:100],
    }


def _build_postgame_blocker_efficacy_review(
    *,
    replay_inputs: dict[str, dict[str, Any]],
    why_no_trade: dict[str, Any],
) -> dict[str, Any]:
    counts = why_no_trade.get("aggregate_blocker_reason_counts")
    aggregate_counts = counts if isinstance(counts, dict) else {}
    rows: list[dict[str, Any]] = []
    blocker_policies = {
        "position_limit_reached": {
            "classification": "protective_if_sleeve_scoped",
            "risk": "harmful_when_token_global_and_suppresses_explicit_parallel_sleeves",
            "recommended_action": "keep as local exposure guard; require explicit sleeve/cycle opt-in for add-down lanes",
        },
        "price_band_not_met": {
            "classification": "protective_local_threshold",
            "risk": "harmful_when_used_as_global event blocker or stale strategy band",
            "recommended_action": "keep local; postgame must compare against missed-window extrema before retuning bands",
        },
        "controlled_entry_requires_grid_spread_blocker": {
            "classification": "protective_development_guard",
            "risk": "can undertrade if grid blocker is too strict during good WNBA liquidity",
            "recommended_action": "keep for first controlled entry; tune max controlled entries by risk mode and realized profit",
        },
        "controlled_entry_event_limit_reached": {
            "classification": "protective_duplicate_guard",
            "risk": "can cap useful two-sided participation if event risk remains available",
            "recommended_action": "make cap dynamic by risk profile; never let it suppress reduce/exit signals",
        },
        "reduce_stop_triggered": {
            "classification": "missing_or_new_protective_exit",
            "risk": "harmful only if fired from stale score/CLOB evidence",
            "recommended_action": "enforce with direct inventory and fresh CLOB; audit after every slate",
        },
        "q4_endgame_loss_mode": {
            "classification": "required_protective_exit",
            "risk": "too weak caused May 27 residual losses; too strong can cut live comeback scalps",
            "recommended_action": "allow scalp-only when immediately targetable; block core/rebuy on failed thesis",
        },
        "rebuy_blocked_adverse_thesis_failed": {
            "classification": "required_protective_rebuy_block",
            "risk": "prevents duplicate same-price loss loops after thesis failure",
            "recommended_action": "clear only after StrategyPlan/LLM reviewed reset or new game phase evidence",
        },
    }
    for reason, policy in blocker_policies.items():
        count = _safe_int(aggregate_counts.get(reason))
        rows.append(
            {
                "reason": reason,
                "observed_count": count,
                "classification": policy["classification"],
                "risk": policy["risk"],
                "recommended_action": policy["recommended_action"],
                "scope": _postgame_blocker_scope(reason),
            }
        )
    missed_value = 0.0
    missed_rows = 0
    for summary in replay_inputs.values():
        if not isinstance(summary, dict):
            continue
        missed = summary.get("missed_window_analysis") if isinstance(summary.get("missed_window_analysis"), dict) else {}
        missed_value += _safe_float(missed.get("estimated_missed_value_usd")) or 0.0
        missed_rows += len(missed.get("rows") or [])
    return {
        "schema_version": "postgame_blocker_efficacy_review_v1",
        "status": "recorded",
        "source_confidence": "runtime_artifact",
        "aggregate_observed_blocker_count": sum(_safe_int(value) for value in aggregate_counts.values()),
        "missed_window_count": missed_rows,
        "missed_window_estimated_value_usd": round(missed_value, 6),
        "rows": rows,
        "policy": {
            "good_blocker": "protects live safety, direct truth, event budget, or local sleeve lifecycle without suppressing unrelated sleeves",
            "bad_blocker": "suppresses unrelated sleeves, blocks exits, or hides strategy-quality drift as runtime safety",
            "exit_priority": "reduce/stop/final cleanup signals outrank new buy/rebuy candidates when evidence is fresh",
        },
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
    account_return = item.get("account_return") if isinstance(item.get("account_return"), dict) else {}
    reconciliation = item.get("reconciliation") if isinstance(item.get("reconciliation"), dict) else {}
    direct_scope = item.get("direct_event_scope") if isinstance(item.get("direct_event_scope"), dict) else {}
    buckets = pnl.get("buckets") if isinstance(pnl.get("buckets"), list) else []
    unresolved: list[dict[str, Any]] = []
    if item.get("ok") is False:
        unresolved.append({"reason": "pnl_attribution_error", "error": item.get("error")})
    if pnl.get("pnl_attribution_ready") is not True:
        reason_counts = reconciliation.get("unresolved_lifecycle_reason_counts")
        if not isinstance(reason_counts, dict):
            reason_counts = {}
        unresolved.append(
            {
                "reason": "pnl_attribution_not_ready",
                "unknown_lifecycle_count": pnl.get("unknown_lifecycle_count"),
                "unresolved_lifecycle_reason_counts": reason_counts,
                "unresolved_lifecycle_reasons": sorted(str(reason) for reason in reason_counts),
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
    for gap in account_return.get("unresolved_evidence") or []:
        if isinstance(gap, dict):
            unresolved.append(gap)

    actual_pnl_usd = account_return.get("actual_pnl_usd")
    actual_pnl_ready = actual_pnl_usd is not None and account_return.get("source_confidence") == "account_confirmed"
    lifecycle_ready = pnl.get("pnl_attribution_ready") is True

    return {
        "event_id": item.get("event_id") or item.get("event_slug"),
        "event_slug": item.get("event_slug") or item.get("event_id"),
        "status": "ready" if actual_pnl_ready and not unresolved else "review_required",
        "source_confidence": "account_confirmed" if actual_pnl_ready else "inferred",
        "account_pnl": {
            "source": account_return.get("source") or "portfolio_order_lifecycle_pnl_attribution_v1",
            "source_confidence": account_return.get("source_confidence") or ("account_confirmed" if pnl else "inferred"),
            "actual_pnl_usd": actual_pnl_usd,
            "actual_pnl_source": account_return.get("actual_pnl_source"),
            "return_on_peak_cash_at_risk_pct": account_return.get("return_on_peak_cash_at_risk_pct"),
            "peak_cash_at_risk_usd": account_return.get("peak_cash_at_risk_usd"),
            "gross_buy_turnover_usd": account_return.get("gross_buy_turnover_usd"),
            "gross_buy_turnover_is_invested_capital": account_return.get("gross_buy_turnover_is_invested_capital"),
            "sell_proceeds_usd": account_return.get("sell_proceeds_usd"),
            "redeem_proceeds_usd": account_return.get("redeem_proceeds_usd"),
            "account_activity_net_cashflow_usd": account_return.get("account_activity_net_cashflow_usd"),
            "closed_position_realized_pnl_usd": account_return.get("closed_position_realized_pnl_usd"),
            "accounting_notes": account_return.get("accounting_notes") or [],
            "known_cashflow_usd": pnl.get("known_cashflow_usd"),
            "known_fee_usd": pnl.get("known_fee_usd"),
            "direct_collateral_delta_usd": pnl.get("direct_collateral_delta_usd"),
            "residual_cashflow_usd": pnl.get("residual_cashflow_usd"),
            "residual_status": pnl.get("residual_status"),
            "final_winning_outcome_id": pnl.get("final_winning_outcome_id"),
            "pnl_attribution_ready": lifecycle_ready,
            "lifecycle_cashflow_is_actual_return": False,
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
            "unresolved_lifecycle_reason_counts": reconciliation.get("unresolved_lifecycle_reason_counts"),
            "lifecycle_status_counts": reconciliation.get("lifecycle_status_counts"),
        },
        "direct_event_scope": direct_scope,
        "market_tape": {
            "source_confidence": "clob_market_tape",
            "account_pnl_eligible": False,
            "event_scoped_trade_count": direct_scope.get("trade_count"),
            "trusted_trade_count": direct_scope.get("trusted_trade_count"),
            "untrusted_trade_count": direct_scope.get("untrusted_trade_count"),
            "all_observed_trade_count": direct_scope.get("all_observed_trade_count"),
            "trade_trust_policy": direct_scope.get("trade_trust_policy"),
            "account_pnl_input_policy": "trusted_trades_only",
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
        "direct_trade_trust_state": _build_postgame_direct_trade_trust_state(direct_scope),
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
            "trusted_trade_count": direct_scope.get("trusted_trade_count"),
            "untrusted_trade_count": direct_scope.get("untrusted_trade_count"),
            "all_observed_trade_count": direct_scope.get("all_observed_trade_count"),
            "trade_trust_policy": direct_scope.get("trade_trust_policy"),
        },
        "ui_displayed_price_comparison": _build_ui_displayed_price_comparison(fill_rows),
    }


def _build_postgame_direct_trade_trust_state(direct_scope: dict[str, Any]) -> dict[str, Any]:
    policy = str(direct_scope.get("trade_trust_policy") or "requires_valid_trade_timestamp_for_current_account_evidence")
    trusted_trade_count = _safe_int(direct_scope.get("trusted_trade_count"))
    untrusted_trade_count = _safe_int(direct_scope.get("untrusted_trade_count"))
    all_observed_trade_count = _safe_int(direct_scope.get("all_observed_trade_count"))
    if not direct_scope:
        status_value = "not_recorded"
    elif untrusted_trade_count:
        status_value = "untrusted_rows_quarantined"
    elif all_observed_trade_count or trusted_trade_count:
        status_value = "trusted_only"
    else:
        status_value = "no_current_event_trades_observed"

    untrusted_trade_ids = []
    for trade in direct_scope.get("untrusted_trades") or []:
        external_id = _direct_item_external_id(trade)
        if external_id is not None:
            untrusted_trade_ids.append(external_id)

    return {
        "schema_version": "postgame_direct_trade_trust_state_v1",
        "schema_contract": _postgame_direct_trade_trust_state_schema_contract(),
        "status": status_value,
        "trade_trust_policy": policy,
        "account_pnl_input_policy": "trusted_trades_only",
        "account_pnl_eligible_trade_count": trusted_trade_count,
        "market_tape_only_trade_count": untrusted_trade_count,
        "trusted_trade_count": trusted_trade_count,
        "untrusted_trade_count": untrusted_trade_count,
        "all_observed_trade_count": all_observed_trade_count,
        "untrusted_trade_reasons": direct_scope.get("untrusted_trade_reasons") or {},
        "untrusted_trade_ids": sorted(set(untrusted_trade_ids)),
        "execution_authority": False,
    }


def _postgame_direct_trade_trust_state_schema_contract() -> dict[str, Any]:
    return {
        "schema_version": "postgame_direct_trade_trust_state_contract_v1",
        "primary_schema": "postgame_direct_trade_trust_state_v1",
        "source_schema": "postgame_direct_event_scope_v1",
        "execution_authority": False,
        "required_sections": [
            "status",
            "trade_trust_policy",
            "account_pnl_input_policy",
            "account_pnl_eligible_trade_count",
            "market_tape_only_trade_count",
            "trusted_trade_count",
            "untrusted_trade_count",
            "all_observed_trade_count",
            "untrusted_trade_reasons",
            "untrusted_trade_ids",
            "execution_authority",
        ],
        "consumer_notes": [
            "Account-PnL consumers must use account_pnl_eligible_trade_count/trusted_trade_count.",
            "Market-tape-only rows are retained for audit but excluded from account-PnL.",
            "This state is postgame evidence only and never authorizes order action.",
        ],
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
    return _read_postgame_replay_tick_stream_summaries(day=day, event_ids=[event_id]).get(
        event_id,
        {
            "schema_version": "postgame_replay_tick_stream_summary_v1",
            "status": "missing",
            "reason": "event_not_found_in_batch_summary",
            "event_id": event_id,
            "tick_count": 0,
            "source_confidence": "inferred",
        },
    )


def _read_postgame_replay_tick_stream_summaries(*, day: str | None, event_ids: list[str]) -> dict[str, dict[str, Any]]:
    requested_event_ids = _normalized_unique_values(event_ids)
    if not day:
        return {
            event_id: _postgame_replay_tick_stream_status(
                event_id=event_id,
                status="not_checked",
                reason="session_date_missing",
            )
            for event_id in requested_event_ids
        }
    root = ops_artifact_root(day).parent.parent / "live-strategy-worker" / day
    ticks_path = root / "ticks.jsonl"
    if not ticks_path.exists():
        return {
            event_id: _postgame_replay_tick_stream_status(
                event_id=event_id,
                status="missing",
                reason="ticks_jsonl_missing",
                path=ticks_path,
            )
            for event_id in requested_event_ids
        }

    summaries = {
        event_id: _new_postgame_replay_tick_stream_summary(event_id=event_id, ticks_path=ticks_path)
        for event_id in requested_event_ids
    }
    if not summaries:
        return {}

    try:
        with ticks_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    tick = json.loads(line)
                except json.JSONDecodeError:
                    continue
                stdout = tick.get("stdout") if isinstance(tick.get("stdout"), dict) else {}
                events = stdout.get("events") if isinstance(stdout.get("events"), list) else []
                for event_payload in events:
                    if not isinstance(event_payload, dict):
                        continue
                    event_id = str(event_payload.get("event_id") or "").strip()
                    summary = summaries.get(event_id)
                    if summary is None:
                        continue
                    _accumulate_postgame_replay_tick_summary(summary, tick=tick, event_payload=event_payload)
    except OSError as exc:
        return {
            event_id: {
                **_finalized_postgame_replay_tick_stream_summary(summary),
                "status": "error",
                "error": str(exc),
            }
            for event_id, summary in summaries.items()
        }
    return {
        event_id: _finalized_postgame_replay_tick_stream_summary(summary)
        for event_id, summary in summaries.items()
    }


def _postgame_replay_tick_stream_status(
    *,
    event_id: str,
    status: str,
    reason: str,
    path: Path | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": "postgame_replay_tick_stream_summary_v1",
        "status": status,
        "reason": reason,
        "event_id": event_id,
        "tick_count": 0,
        "source_confidence": "inferred",
    }
    if path is not None:
        payload["path"] = str(path)
    return payload


def _new_postgame_replay_tick_stream_summary(*, event_id: str, ticks_path: Path) -> dict[str, Any]:
    return {
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


def _finalized_postgame_replay_tick_stream_summary(summary: dict[str, Any]) -> dict[str, Any]:
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
    event_start = _strategy_plan_event_start_utc(plan)
    observed_at = datetime.now(timezone.utc)
    trusted_trades: list[Any] = []
    untrusted_trades: list[dict[str, Any]] = []
    for trade in trades:
        trade_payload = to_jsonable(trade)
        trusted, reason = _direct_trade_current_evidence_status(
            trade_payload if isinstance(trade_payload, dict) else {},
            event_start=event_start,
            now=observed_at,
        )
        if trusted:
            trusted_trades.append(trade)
            continue
        annotated = dict(trade_payload) if isinstance(trade_payload, dict) else {"raw": trade_payload}
        annotated["current_account_evidence_status"] = "untrusted"
        annotated["current_account_evidence_reason"] = reason
        untrusted_trades.append(annotated)
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
        "trade_count": len(trusted_trades),
        "trusted_trade_count": len(trusted_trades),
        "untrusted_trade_count": len(untrusted_trades),
        "all_observed_trade_count": len(trades),
        "trade_trust_policy": "requires_valid_trade_timestamp_for_current_account_evidence",
        "open_orders": open_orders,
        "open_positions": open_positions,
        "trades": trusted_trades,
        "untrusted_trades": untrusted_trades,
    }
    return {
        **direct_context,
        "direct_open_order_external_ids": open_order_ids,
        "direct_open_order_count": len(open_orders),
        "direct_open_position_count": len(open_positions),
        "direct_trade_rows": trusted_trades,
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
            "trade_count": len(trusted_trades),
            "trusted_trade_count": len(trusted_trades),
            "untrusted_trade_count": len(untrusted_trades),
            "all_observed_trade_count": len(trades),
            "trade_trust_policy": "requires_valid_trade_timestamp_for_current_account_evidence",
            "untrusted_trade_reasons": dict(
                sorted(
                    {
                        str(item.get("current_account_evidence_reason") or "unknown"): sum(
                            1
                            for candidate in untrusted_trades
                            if candidate.get("current_account_evidence_reason") == item.get("current_account_evidence_reason")
                        )
                        for item in untrusted_trades
                    }.items()
                )
            ),
            "untrusted_trades": untrusted_trades,
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
    worker_summaries = _read_live_worker_tick_summaries(day=day, event_ids=event_ids)
    for event_id in event_ids:
        try:
            counts = _fetch_postgame_live_evidence_counts(connection, event_id=event_id)
            worker_summary = worker_summaries.get(event_id) or _read_live_worker_tick_summary(day=day, event_id=event_id)
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
    return _read_live_worker_tick_summaries(day=day, event_ids=[event_id]).get(
        event_id,
        {"status": "missing", "reason": "event_not_found_in_batch_summary", "tick_count": 0},
    )


def _read_live_worker_tick_summaries(*, day: str | None, event_ids: list[str]) -> dict[str, dict[str, Any]]:
    requested_event_ids = _normalized_unique_values(event_ids)
    if not day:
        return {
            event_id: {"status": "not_checked", "reason": "session_date_missing", "tick_count": 0}
            for event_id in requested_event_ids
        }
    root = ops_artifact_root(day).parent.parent / "live-strategy-worker" / day
    ticks_path = root / "ticks.jsonl"
    summaries = {
        event_id: {
            "status": "missing",
            "tick_count": 0,
            "latest_tick_at_utc": None,
            "heartbeat_present": False,
            "heartbeat_event_match": False,
            "heartbeat_event_ids": [],
        }
        for event_id in requested_event_ids
    }
    if ticks_path.exists():
        try:
            with ticks_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        tick = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    tick_event_ids = set(_normalized_unique_values(tick.get("event_ids") or []))
                    timestamp = tick.get("finished_at_utc") or tick.get("started_at_utc")
                    for event_id in tick_event_ids.intersection(summaries):
                        summaries[event_id]["tick_count"] += 1
                        if timestamp:
                            summaries[event_id]["latest_tick_at_utc"] = timestamp
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
    for event_id, summary in summaries.items():
        summary["heartbeat_present"] = heartbeat is not None
        summary["heartbeat_event_match"] = event_id in heartbeat_event_ids
        summary["heartbeat_event_ids"] = heartbeat_event_ids
        summary["status"] = "recorded" if summary["tick_count"] or heartbeat_event_ids else "missing"
    return summaries


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
        observed_trades = [
            to_jsonable(trade)
            for trade in raw_trades
            if _direct_item_matches_current_event(trade, token_ids=token_ids, condition_ids=condition_ids, event_slugs=event_slugs)
        ]
        event_start = _strategy_plan_event_start_utc(plan)
        trades: list[dict[str, Any]] = []
        untrusted_trades: list[dict[str, Any]] = []
        observed_at = datetime.now(timezone.utc)
        for trade in observed_trades:
            trusted, reason = _direct_trade_current_evidence_status(
                trade,
                event_start=event_start,
                now=observed_at,
            )
            if trusted:
                trades.append(trade)
                continue
            annotated = dict(trade)
            annotated["current_account_evidence_status"] = "untrusted"
            annotated["current_account_evidence_reason"] = reason
            untrusted_trades.append(annotated)
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
                "trusted_trade_count": trade_count,
                "untrusted_trade_count": len(untrusted_trades),
                "all_observed_trade_count": len(observed_trades),
                "trade_trust_policy": "requires_valid_trade_timestamp_for_current_account_evidence",
                "unresolved_inventory_present": bool(open_order_count or active_open_position_count),
                "open_orders": open_orders,
                "open_positions": open_positions,
                "active_open_positions": active_open_positions,
                "documented_residual_positions": documented_residual_positions,
                "blocked_residual_classifications": blocked_residual_classifications,
                "trades": trades,
                "untrusted_trades": untrusted_trades,
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


def _strategy_plan_event_start_utc(plan: dict[str, Any]) -> datetime | None:
    context = plan.get("context_summary") if isinstance(plan.get("context_summary"), dict) else {}
    for value in (
        context.get("game_start_utc"),
        context.get("event_start_utc"),
        context.get("game_start_time"),
        plan.get("game_start_utc"),
        plan.get("event_start_utc"),
        plan.get("game_start_time"),
    ):
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed
    return None


def _direct_trade_current_evidence_status(
    trade: dict[str, Any],
    *,
    event_start: datetime | None = None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    trade_time = _direct_trade_time_utc_for_monitor(trade)
    if trade_time is None:
        return False, "direct_trade_timestamp_missing_or_invalid"
    observed_at = now or datetime.now(timezone.utc)
    lower_bound = event_start - timedelta(hours=12) if event_start is not None else datetime(2020, 1, 1, tzinfo=timezone.utc)
    if trade_time < lower_bound:
        return False, "direct_trade_timestamp_before_current_event_window"
    if trade_time > observed_at + timedelta(minutes=10):
        return False, "direct_trade_timestamp_future"
    return True, "valid_current_trade_timestamp"


def _direct_trade_time_utc_for_monitor(trade: dict[str, Any]) -> datetime | None:
    for field in ("trade_time_utc", "trade_time", "timestamp", "created_at", "createdAt"):
        value = trade.get(field)
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            timestamp = float(value)
            if timestamp <= 0:
                continue
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            try:
                return datetime.fromtimestamp(timestamp, timezone.utc)
            except (OSError, OverflowError, ValueError):
                continue
    return None


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
