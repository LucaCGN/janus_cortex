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
    direct_trade_token_ids = _direct_trade_token_ids_for_events(payload.event_ids, day=payload.session_date)
    integrity = build_integrity_snapshot(
        connection,
        account_id=payload.account_id,
        direct_trade_token_ids=direct_trade_token_ids,
    )
    recorded = record_ops_stage(
        "integrity-check",
        {**payload.model_dump(mode="json"), "ops_status": ops_status, "integrity": integrity},
        day=payload.session_date,
    )
    return {**recorded, "ops_status": ops_status, "integrity": integrity}


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
    direct_trade_token_ids = _direct_trade_token_ids_for_events(payload.event_ids, day=payload.session_date)
    integrity = build_integrity_snapshot(
        connection,
        account_id=payload.account_id,
        direct_trade_token_ids=direct_trade_token_ids,
    )
    strategy_plan_gate = _build_strategy_plan_gate(payload.event_ids, day=payload.session_date)
    llm_runtime_status = load_latest_llm_runtime_status(
        session_date=payload.session_date,
        event_ids=payload.event_ids,
    )
    recorded = record_ops_stage(
        "live-monitor",
        {
            **payload.model_dump(mode="json"),
            "ops_status": ops_status,
            "integrity": integrity,
            "strategy_plan_gate": strategy_plan_gate,
            "llm_runtime_status": llm_runtime_status,
        },
        day=payload.session_date,
    )
    return {
        **recorded,
        "ops_status": ops_status,
        "integrity": integrity,
        "strategy_plan_gate": strategy_plan_gate,
        "llm_runtime_status": llm_runtime_status,
    }


@router.post("/ops/postgame-review", status_code=status.HTTP_202_ACCEPTED)
def run_ops_postgame_review(
    payload: OpsCycleRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    reviewed_event_ids = _resolve_postgame_review_event_ids(payload.event_ids, day=payload.session_date)
    strategy_plan_gate = _build_strategy_plan_gate(reviewed_event_ids, day=payload.session_date)
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
            "portfolio_pnl_attribution": portfolio_pnl_attribution,
        },
        day=payload.session_date,
    )
    return {
        **recorded,
        "reviewed_event_ids": reviewed_event_ids,
        "strategy_plan_gate": strategy_plan_gate,
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
        plans.append(
            {
                "event_id": event_id,
                "market_id": plan.get("market_id"),
                "schema_version": plan.get("schema_version"),
                "plan_owner": plan.get("plan_owner"),
                "active_strategy_count": len(plan.get("active_strategies") or []),
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


def _normalized_unique_values(values: list[str]) -> list[str]:
    return [value for value in dict.fromkeys(str(item).strip() for item in values) if value]
