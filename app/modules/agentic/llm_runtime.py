from __future__ import annotations

import copy
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.modules.agentic.contracts import (
    LLMModelRoutingDecision,
    LLMRevisionRequest,
    LLMRevisionResponse,
    LLMRuntimeTrace,
    LLMRuntimeTrigger,
)
from app.runtime.local_paths import repo_root, resolve_shared_root


NANO_MODEL = "gpt-5.4-nano"
MINI_MODEL = "gpt-5.4-mini"
FRONTIER_MODEL = "gpt-5.5"
ROUTING_RULES_VERSION = "llm_model_routing_2026-05-11"
ARTIFACT_SCHEMA_VERSION = "llm_runtime_trace_artifact_v1"
DEFAULT_OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
SAFETY_CONTROLS_VERSION = "llm_runtime_safety_controls_2026-05-17"
DEFAULT_EVENT_TOKEN_BUDGET = 20_000
DEFAULT_EVENT_COST_BUDGET_USD = 0.50
DEFAULT_EVENT_WARNING_FRACTION = 0.80

_TRIGGER_NAMESPACE = uuid.UUID("4f61cce7-2098-44d7-a59e-2b3c92a48f0f")
_CRITICAL_TRIGGER_TYPES = {
    "manual_operator_order",
    "manual_operator_trade",
    "manual_operator_position",
    "position_adverse_move",
    "player_status_shock",
    "stale_feed_recovery",
    "unexplained_clob_move",
    "price_flip",
    "leadership_switch",
    "garbage_time",
    "target_placement_failed",
}
_ORDER_LIFECYCLE_TRIGGER_TYPES = {
    "janus_order_submitted",
    "order_fill",
    "order_cancel",
    "order_stale",
    "target_fill",
    "target_cancel",
    "target_placement_failed",
}
_NANO_TRIGGER_TYPES = {"compression_or_tagging"}
_DEFAULT_TRIGGER_CALL_CAPS = {
    "quarter_end": 4,
    "routine_live_review": 1,
    "manual_operator_position": 3,
    "position_adverse_move": 3,
    "player_status_shock": 5,
    "stale_feed_recovery": 2,
    "unexplained_clob_move": 3,
    "price_flip": 3,
    "leadership_switch": 3,
    "garbage_time": 1,
}
_ESTIMATED_MODEL_COST_PER_MILLION_TOKENS = {
    NANO_MODEL: {"input": 0.05, "output": 0.40},
    MINI_MODEL: {"input": 0.25, "output": 2.00},
    FRONTIER_MODEL: {"input": 2.00, "output": 16.00},
}


class _OpenAIRevisionResponse(BaseModel):
    """Strict OpenAI structured-output envelope.

    The public LLMRevisionResponse contract intentionally carries flexible JSON
    objects. OpenAI strict structured outputs reject arbitrary dict fields, so
    the live call asks for JSON strings and Janus validates them locally before
    creating the canonical response.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "llm_revision_response_v1"
    request_id: str = Field(min_length=1)
    status: str = "response_recorded"
    selected_model: str = Field(min_length=1)
    revised_strategy_plan_json: str | None = None
    reconciliation_actions_json: str = "[]"
    blocked_actions_json: str = "[]"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    skipped_reason: str | None = None
    trace_metadata_json: str = "{}"


JANUS_LIVE_LLM_PROMPT_CONTRACT: dict[str, Any] = {
    "schema_version": "janus_live_llm_prompt_contract_v1",
    "system_persona": (
        "You are the internal Janus live-trading revision engine. You reason about NBA prediction-market "
        "opportunity using only supplied Janus runtime evidence, compare testable return/consistency approaches "
        "under operator-defined minimum-size live experiments, and output structured revision and reconciliation JSON. "
        "Do not reject a strategy because of subjective risk tolerance; mechanical exchange/account safety gates still apply."
    ),
    "authority_boundaries": [
        "The LLM never calls order endpoints.",
        "The LLM never places, cancels, or replaces orders directly.",
        "The LLM outputs structured StrategyPlanJSON revisions and reconciliation actions only.",
        "Janus validates schema, direct CLOB truth, portfolio state, operator sizing policy, and mechanical safety before execution.",
        "Operator sizing policy owns live size and exposure; StrategyPlanJSON sizing is advisory when policy is present.",
    ],
    "required_input_sections": [
        "general_janus_live_trading_persona_and_risk_profile",
        "event_specific_context",
        "deterministic_strategy_candidates",
        "ml_pbp_trigger_evidence",
        "direct_clob_truth",
        "portfolio_orders_positions_trades",
        "current_scoreboard_and_play_by_play_summary",
        "current_strategy_plan_and_stale_reason",
        "operator_sizing_policy",
    ],
    "required_json_output_schema": {
        "schema_version": "llm_revision_response_v1",
        "revised_strategy_plan": "StrategyPlanJSON or null",
        "openai_transport_note": (
            "For strict OpenAI structured outputs, StrategyPlanJSON and flexible arrays/maps are returned "
            "as JSON strings and validated by Janus before becoming canonical LLMRevisionResponse fields."
        ),
        "reconciliation_actions": [
            "adopt",
            "pause",
            "target",
            "stop",
            "hedge",
            "cancel",
            "replace",
            "no_new_entry",
        ],
        "blocked_actions": "array of actions rejected with reasons",
        "confidence": "number from 0.0 to 1.0 or null",
        "blockers": "array of safety/data blockers",
        "trace_metadata": "model, prompt version, evidence ids, and decision rationale",
    },
    "experiment_policy": (
        "When a live position moves adversely in a still-competitive game, do not default to realizing the loss. "
        "Compare holding the target, lowering/replacing the target, hedging the opposite side, and adding down on the "
        "same side as testable approaches before any loss exit. Marketable loss exits are reserved for virtual-dead "
        "states: garbage time, bench-emptying, a severe late score gap, a decisive player-status shock, or stale/unsafe "
        "portfolio truth. Score every option by expected return, consistency, fillability, and evidence quality rather "
        "than by subjective risk tolerance."
    ),
    "safety_rule": "No order endpoint calls are allowed from LLM output or prompt tools.",
}


def build_llm_runtime_safety_controls_status() -> dict[str, Any]:
    policy = _llm_budget_policy()
    return {
        "schema_version": "llm_runtime_safety_controls_status_v1",
        "controls_version": SAFETY_CONTROLS_VERSION,
        "status": "ready",
        "ready_for_controlled_dispatch": True,
        "live_money_dispatch_authorized_by_default": False,
        "dispatch_default": "disabled_until_explicitly_enabled",
        "implemented_controls": [
            "trigger_hash_dedup",
            "per_event_token_budget",
            "per_event_cost_budget",
            "budget_warning_model_downgrade",
            "trigger_type_call_caps",
            "final_flat_shutdown",
            "cost_telemetry",
        ],
        "budget_policy": policy,
    }


def detect_llm_runtime_triggers(
    *,
    event_id: str,
    current_plan: dict[str, Any] | Any | None = None,
    event_context: dict[str, Any] | None = None,
    live_state: dict[str, Any] | None = None,
    direct_clob_truth: dict[str, Any] | None = None,
    orderbook_state: dict[str, Any] | None = None,
    portfolio_state: dict[str, Any] | None = None,
    operator_interventions: list[dict[str, Any]] | None = None,
    strategy_decisions: list[dict[str, Any]] | None = None,
    pbp_shocks: list[dict[str, Any]] | None = None,
    ml_pbp_evidence: dict[str, Any] | list[dict[str, Any]] | None = None,
    source: str = "janus_live_runtime",
    routine_live_review: bool = False,
) -> list[LLMRuntimeTrigger]:
    """Detect application-owned live LLM revision triggers without making model calls."""

    plan = _plain_mapping(current_plan)
    context = dict(event_context or {})
    live = dict(live_state or {})
    direct = dict(direct_clob_truth or {})
    orderbook = dict(orderbook_state or {})
    portfolio = dict(portfolio_state or {})
    interventions = [dict(item) for item in operator_interventions or [] if isinstance(item, dict)]
    decisions = [dict(item) for item in strategy_decisions or [] if isinstance(item, dict)]
    triggers: list[LLMRuntimeTrigger] = []

    quarter_evidence = _quarter_end_evidence(live)
    if quarter_evidence:
        triggers.append(
            _make_trigger(
                event_id=event_id,
                trigger_type="quarter_end",
                source=source,
                reason="Quarter boundary detected by application runtime.",
                severity="routine",
                evidence=quarter_evidence,
            )
        )

    for event in _live_state_revision_events(live=live, orderbook=orderbook):
        triggers.append(
            _make_trigger(
                event_id=event_id,
                trigger_type=event["trigger_type"],
                source=source,
                reason=event["reason"],
                severity=event["severity"],
                evidence=event["evidence"],
            )
        )

    score_gap_break = _score_gap_break_evidence(plan, live)
    if score_gap_break is not None and not any(trigger.trigger_type == "score_gap_break" for trigger in triggers):
        triggers.append(
            _make_trigger(
                event_id=event_id,
                trigger_type="score_gap_break",
                source=source,
                reason="Application runtime detected a score-gap break against active StrategyPlanJSON rules.",
                severity="routine",
                evidence=score_gap_break,
            )
        )

    for item in interventions:
        trigger_type = _operator_trigger_type(item)
        if trigger_type is None:
            continue
        triggers.append(
            _make_trigger(
                event_id=event_id,
                trigger_type=trigger_type,
                source=source,
                reason="Manual/operator exposure detected from direct CLOB or operator intervention state.",
                severity="critical",
                evidence=_compact_evidence(item),
            )
        )

    for event in _order_lifecycle_events(portfolio, decisions):
        trigger_type = _order_lifecycle_trigger_type(event)
        if trigger_type is None:
            continue
        severity = (
            "critical"
            if trigger_type in {"order_fill", "order_cancel", "order_stale", "target_cancel", "target_placement_failed"}
            else "routine"
        )
        triggers.append(
            _make_trigger(
                event_id=event_id,
                trigger_type=trigger_type,
                source=source,
                reason=f"Order lifecycle event requires live LLM revision review: {trigger_type}.",
                severity=severity,
                evidence=_compact_evidence(event),
            )
        )

    for shock in _shock_rows(pbp_shocks, live):
        if shock.get("requires_strategy_plan_revision") is False:
            continue
        triggers.append(
            _make_trigger(
                event_id=event_id,
                trigger_type="player_status_shock",
                source=source,
                reason="Player-status shock detected from play-by-play.",
                severity="critical",
                evidence=_compact_evidence(shock),
            )
        )

    if _truthy(live.get("stale_feed_recovery") or live.get("feed_recovered_after_stale") or direct.get("stale_feed_recovery")):
        triggers.append(
            _make_trigger(
                event_id=event_id,
                trigger_type="stale_feed_recovery",
                source=source,
                reason="Stale feed recovered; current plan may have evaluated stale state.",
                severity="critical",
                evidence={
                    "live_state": _compact_evidence(live),
                    "direct_clob": _compact_evidence(direct),
                },
            )
        )

    clob_move = orderbook.get("unexplained_clob_move") or orderbook.get("clob_move_without_scoreboard_driver")
    if isinstance(clob_move, dict) or _truthy(clob_move):
        triggers.append(
            _make_trigger(
                event_id=event_id,
                trigger_type="unexplained_clob_move",
                source=source,
                reason="CLOB moved without matching scoreboard or play-by-play driver.",
                severity="critical",
                evidence=_compact_evidence(clob_move if isinstance(clob_move, dict) else orderbook),
            )
        )

    for evidence in _ml_pbp_evidence_rows(ml_pbp_evidence):
        trigger_type = _ml_pbp_trigger_type(evidence)
        if trigger_type is None:
            continue
        triggers.append(
            _make_trigger(
                event_id=event_id,
                trigger_type=trigger_type,
                source=source,
                reason="ML/PBP layer emitted a valuation trigger.",
                severity="routine",
                evidence=_compact_evidence(evidence),
            )
        )

    for evidence in _passive_plan_trigger_rows(plan, live=live, orderbook=orderbook, event_context=context):
        triggers.append(
            _make_trigger(
                event_id=event_id,
                trigger_type="strategy_plan_revision_trigger",
                source=source,
                reason="Current StrategyPlanJSON carried a triggered revision watchpoint.",
                severity="routine",
                evidence=_compact_evidence(evidence),
            )
        )

    if routine_live_review:
        triggers.append(
            _make_trigger(
                event_id=event_id,
                trigger_type="routine_live_review",
                source=source,
                reason="Routine live monitor review requested by application runtime.",
                severity="routine",
                evidence={"requested": True},
                requires_revision=False,
                current_plan_stale_reason=None,
            )
        )

    return _mark_triggers_reviewed_by_current_plan(_dedupe_triggers(triggers), plan)


def route_llm_model(
    triggers: list[LLMRuntimeTrigger],
    *,
    live_state: dict[str, Any] | None = None,
    portfolio_state: dict[str, Any] | None = None,
    high_uncertainty: bool = False,
) -> LLMModelRoutingDecision:
    trigger_ids = [trigger.trigger_id for trigger in triggers]
    trigger_types = {trigger.trigger_type for trigger in triggers}
    critical_reasons: list[str] = []

    if not triggers:
        return LLMModelRoutingDecision(
            selected_model=MINI_MODEL,
            selected_tier="mini",
            reason="No runtime LLM trigger was emitted; routine monitor state would use mini if explicitly requested.",
            trigger_ids=[],
            critical_reasons=[],
            routing_rules_version=ROUTING_RULES_VERSION,
        )

    if trigger_types and trigger_types.issubset(_NANO_TRIGGER_TYPES):
        return LLMModelRoutingDecision(
            selected_model=NANO_MODEL,
            selected_tier="nano",
            reason="All triggers are compression, tagging, or summarization work.",
            trigger_ids=trigger_ids,
            critical_reasons=[],
            routing_rules_version=ROUTING_RULES_VERSION,
        )

    critical_trigger_types = sorted(trigger_types.intersection(_CRITICAL_TRIGGER_TYPES))
    if critical_trigger_types:
        critical_reasons.extend(critical_trigger_types)
    if _has_open_exposure(portfolio_state):
        critical_reasons.append("open_exposure")
    if high_uncertainty or _late_game_high_uncertainty(live_state):
        critical_reasons.append("high_uncertainty_late_game")
    if trigger_types.intersection(_ORDER_LIFECYCLE_TRIGGER_TYPES) and _has_missing_protection(portfolio_state):
        critical_reasons.append("missing_protection_or_stop_hedge")

    if critical_reasons:
        return LLMModelRoutingDecision(
            selected_model=FRONTIER_MODEL,
            selected_tier="frontier",
            reason="Critical live-money revision path requires frontier reasoning per Janus model routing.",
            trigger_ids=trigger_ids,
            critical_reasons=sorted(set(critical_reasons)),
            routing_rules_version=ROUTING_RULES_VERSION,
        )

    return LLMModelRoutingDecision(
        selected_model=MINI_MODEL,
        selected_tier="mini",
        reason="Routine live StrategyPlanJSON revision trigger with no open exposure, shock, stale recovery, or manual intervention.",
        trigger_ids=trigger_ids,
        critical_reasons=[],
        routing_rules_version=ROUTING_RULES_VERSION,
    )


def build_llm_prompt_contract() -> dict[str, Any]:
    return copy.deepcopy(JANUS_LIVE_LLM_PROMPT_CONTRACT)


def build_llm_revision_request(
    *,
    event_id: str,
    market_id: str | None = None,
    session_date: str | None = None,
    triggers: list[LLMRuntimeTrigger],
    model_routing: LLMModelRoutingDecision,
    current_plan: dict[str, Any] | Any | None = None,
    event_context: dict[str, Any] | None = None,
    live_state: dict[str, Any] | None = None,
    direct_clob_truth: dict[str, Any] | None = None,
    orderbook_state: dict[str, Any] | None = None,
    portfolio_state: dict[str, Any] | None = None,
    operator_interventions: list[dict[str, Any]] | None = None,
    strategy_decisions: list[dict[str, Any]] | None = None,
    ml_pbp_evidence: dict[str, Any] | list[dict[str, Any]] | None = None,
    current_plan_stale_reason: str | None = None,
) -> LLMRevisionRequest:
    plan = _plain_mapping(current_plan)
    prompt_contract = build_llm_prompt_contract()
    return LLMRevisionRequest(
        request_id=_stable_id("llm-revision-request", event_id, [trigger.trigger_id for trigger in triggers]),
        event_id=event_id,
        market_id=market_id or str(plan.get("market_id") or "") or None,
        session_date=session_date,
        triggers=triggers,
        model_routing=model_routing,
        prompt_contract=prompt_contract,
        current_plan=plan,
        event_context=dict(event_context or {}),
        deterministic_strategy_candidates=_strategy_candidates_from_plan(plan),
        ml_pbp_trigger_evidence=_ml_pbp_evidence_payload(ml_pbp_evidence),
        direct_clob_truth=dict(direct_clob_truth or {}),
        orderbook_state=dict(orderbook_state or {}),
        portfolio_state=dict(portfolio_state or {}),
        operator_interventions=[dict(item) for item in operator_interventions or [] if isinstance(item, dict)],
        strategy_decisions=[dict(item) for item in strategy_decisions or [] if isinstance(item, dict)],
        scoreboard_pbp_summary=_scoreboard_pbp_summary(dict(live_state or {})),
        current_plan_stale_reason=current_plan_stale_reason or _first_stale_reason(triggers),
        operator_sizing_policy=_operator_sizing_policy(dict(portfolio_state or {})),
    )


def build_llm_runtime_trace(
    *,
    event_id: str,
    market_id: str | None = None,
    session_date: str | None = None,
    current_plan: dict[str, Any] | Any | None = None,
    event_context: dict[str, Any] | None = None,
    live_state: dict[str, Any] | None = None,
    direct_clob_truth: dict[str, Any] | None = None,
    orderbook_state: dict[str, Any] | None = None,
    portfolio_state: dict[str, Any] | None = None,
    operator_interventions: list[dict[str, Any]] | None = None,
    strategy_decisions: list[dict[str, Any]] | None = None,
    pbp_shocks: list[dict[str, Any]] | None = None,
    ml_pbp_evidence: dict[str, Any] | list[dict[str, Any]] | None = None,
    source: str = "janus_live_runtime",
    routine_live_review: bool = False,
) -> LLMRuntimeTrace:
    triggers = detect_llm_runtime_triggers(
        event_id=event_id,
        current_plan=current_plan,
        event_context=event_context,
        live_state=live_state,
        direct_clob_truth=direct_clob_truth,
        orderbook_state=orderbook_state,
        portfolio_state=portfolio_state,
        operator_interventions=operator_interventions,
        strategy_decisions=strategy_decisions,
        pbp_shocks=pbp_shocks,
        ml_pbp_evidence=ml_pbp_evidence,
        source=source,
        routine_live_review=routine_live_review,
    )
    routing = route_llm_model(triggers, live_state=live_state, portfolio_state=portfolio_state)
    revision_request: LLMRevisionRequest | None = None
    revision_response: LLMRevisionResponse | None = None
    if triggers:
        revision_request = build_llm_revision_request(
            event_id=event_id,
            market_id=market_id,
            session_date=session_date,
            triggers=triggers,
            model_routing=routing,
            current_plan=current_plan,
            event_context=event_context,
            live_state=live_state,
            direct_clob_truth=direct_clob_truth,
            orderbook_state=orderbook_state,
            portfolio_state=portfolio_state,
            operator_interventions=operator_interventions,
            strategy_decisions=strategy_decisions,
            ml_pbp_evidence=ml_pbp_evidence,
        )
        revision_response = LLMRevisionResponse(
            request_id=revision_request.request_id,
            selected_model=routing.selected_model,
            status="detected_only",
            skipped_reason="audit_only_model_call_not_enabled",
            trace_metadata={
                "audit_only": True,
                "openai_call_attempted": False,
                "strategy_plan_auto_replace_attempted": False,
                "order_endpoint_call_allowed": False,
            },
        )

    return LLMRuntimeTrace(
        trace_id=_stable_id("llm-runtime-trace", event_id, [trigger.trigger_id for trigger in triggers]),
        event_id=event_id,
        trigger_count=len(triggers),
        triggers=triggers,
        model_routing=routing,
        revision_request=revision_request,
        revision_response=revision_response,
        status="detected_only",
        audit_only=True,
        notes="Detection/audit only; no OpenAI call or StrategyPlanJSON replacement is performed in this slice.",
    )


def process_llm_runtime_trace(
    trace: LLMRuntimeTrace,
    *,
    dispatch_enabled: bool = False,
    artifact_root: str | Path | None = None,
    session_date: str | None = None,
    client: Any | None = None,
    client_factory: Any | None = None,
    api_key_env: str = DEFAULT_OPENAI_API_KEY_ENV,
) -> tuple[LLMRuntimeTrace, dict[str, Any]]:
    """Dispatch fail-closed when enabled, then persist the complete runtime trace artifact."""

    dispatched = dispatch_llm_revision(
        trace,
        dispatch_enabled=dispatch_enabled,
        artifact_root=artifact_root,
        session_date=session_date,
        client=client,
        client_factory=client_factory,
        api_key_env=api_key_env,
    )
    persistence = persist_llm_runtime_trace(
        dispatched,
        artifact_root=artifact_root,
        session_date=session_date,
    )
    return dispatched, persistence


def dispatch_llm_revision(
    trace: LLMRuntimeTrace,
    *,
    dispatch_enabled: bool = False,
    artifact_root: str | Path | None = None,
    session_date: str | None = None,
    client: Any | None = None,
    client_factory: Any | None = None,
    api_key_env: str = DEFAULT_OPENAI_API_KEY_ENV,
) -> LLMRuntimeTrace:
    """Call the configured LLM only when explicitly enabled; every failure returns a skipped response."""

    request = trace.revision_request
    if request is None or not trace.triggers:
        return trace.model_copy(
            update={
                "notes": "No LLM runtime trigger requires dispatch for this tick.",
                "audit_only": not dispatch_enabled,
            },
            deep=True,
        )

    if not any(trigger.requires_revision for trigger in trace.triggers):
        response = LLMRevisionResponse(
            request_id=request.request_id,
            selected_model=request.model_routing.selected_model,
            status="detected_only",
            skipped_reason="no_revision_required",
            trace_metadata={
                "dispatch_enabled": dispatch_enabled,
                "openai_call_attempted": False,
                "order_endpoint_call_allowed": False,
                "strategy_plan_auto_replace_attempted": False,
                "llm_never_calls_order_endpoints": True,
            },
        )
        return trace.model_copy(
            update={
                "revision_response": response,
                "status": "detected_only",
                "audit_only": True,
                "notes": "LLM runtime triggers were already reviewed by the current StrategyPlanJSON; no OpenAI call was made.",
            },
            deep=True,
        )

    if not dispatch_enabled:
        return _trace_with_skipped_response(
            trace,
            request=request,
            reason="dispatch_disabled",
            metadata={
                "dispatch_enabled": False,
                "openai_call_attempted": False,
                "order_endpoint_call_allowed": False,
                "llm_runtime_safety_controls": build_llm_runtime_safety_controls_status(),
            },
        )

    guard = _build_llm_pre_dispatch_guard(
        trace,
        artifact_root=artifact_root,
        session_date=session_date,
    )
    if not guard["allowed"]:
        return _trace_with_skipped_response(
            trace,
            request=request,
            reason=str(guard["reason"]),
            metadata={
                "dispatch_enabled": True,
                "openai_call_attempted": False,
                "openai_client_available": None,
                "order_endpoint_call_allowed": False,
                **guard["metadata"],
            },
        )
    trace = _maybe_downgrade_trace_for_budget(trace, guard=guard)
    request = trace.revision_request or request

    resolved_client = client
    if resolved_client is None:
        _load_dotenv_if_available()
        if not str(os.getenv(api_key_env) or "").strip():
            return _trace_with_skipped_response(
                trace,
                request=request,
                reason=f"{api_key_env.lower()}_missing",
                metadata={
                    "dispatch_enabled": True,
                    "openai_call_attempted": False,
                    "openai_client_available": False,
                    "order_endpoint_call_allowed": False,
                    **guard["metadata"],
                },
            )
        try:
            resolved_client = client_factory() if client_factory is not None else _resolve_openai_client()
        except Exception as exc:  # noqa: BLE001
            return _trace_with_skipped_response(
                trace,
                request=request,
                reason=f"openai_client_unavailable:{_exception_detail(exc)}",
                metadata={
                    "dispatch_enabled": True,
                    "openai_call_attempted": False,
                    "openai_client_available": False,
                    "order_endpoint_call_allowed": False,
                    **guard["metadata"],
                },
            )

    if resolved_client is None:
        return _trace_with_skipped_response(
            trace,
            request=request,
            reason="openai_client_unavailable",
            metadata={
                "dispatch_enabled": True,
                "openai_call_attempted": False,
                "openai_client_available": False,
                "order_endpoint_call_allowed": False,
                **guard["metadata"],
            },
        )

    usage: dict[str, int] = {}
    try:
        parsed, usage = _call_openai_revision(client=resolved_client, request=request)
        response = _coerce_llm_revision_response(parsed)
    except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return _trace_with_skipped_response(
            trace,
            request=request,
            reason=f"response_schema_validation_failed:{_exception_detail(exc)}",
            metadata={
                "dispatch_enabled": True,
                "openai_call_attempted": True,
                "openai_client_available": True,
                "order_endpoint_call_allowed": False,
                "usage": usage,
                **_response_telemetry_metadata(request=request, usage=usage, guard=guard),
            },
        )
    except Exception as exc:  # noqa: BLE001
        return _trace_with_skipped_response(
            trace,
            request=request,
            reason=f"openai_call_error:{_exception_detail(exc)}",
            metadata={
                "dispatch_enabled": True,
                "openai_call_attempted": True,
                "openai_client_available": True,
                "order_endpoint_call_allowed": False,
                **_response_telemetry_metadata(request=request, usage=usage, guard=guard),
            },
        )

    response = response.model_copy(
        update={
            "request_id": request.request_id,
            "selected_model": request.model_routing.selected_model,
            "status": "response_recorded",
            "skipped_reason": None,
            "trace_metadata": {
                **response.trace_metadata,
                "dispatch_enabled": True,
                "openai_call_attempted": True,
                "openai_client_available": True,
                "order_endpoint_call_allowed": False,
                "strategy_plan_auto_replace_attempted": False,
                "usage": usage,
                **_response_telemetry_metadata(request=request, usage=usage, guard=guard),
            },
        },
        deep=True,
    )
    return trace.model_copy(
        update={
            "revision_response": response,
            "status": "response_recorded",
            "audit_only": False,
            "notes": "LLM revision response recorded; order authority remains with Janus validators and reviewed StrategyPlanJSON flow.",
        },
        deep=True,
    )


def persist_llm_runtime_trace(
    trace: LLMRuntimeTrace,
    *,
    artifact_root: str | Path | None = None,
    session_date: str | None = None,
) -> dict[str, Any]:
    resolved_day = _resolve_session_date(trace, session_date=session_date)
    root = Path(artifact_root) if artifact_root is not None else default_llm_runtime_artifact_root()
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    day_root = root / resolved_day
    day_root.mkdir(parents=True, exist_ok=True)
    timestamp = trace.created_at_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    filename = f"{timestamp}_{_safe_filename(trace.event_id)}_{trace.trace_id[:8]}.json"
    path = day_root / filename
    now = datetime.now(timezone.utc)
    response = trace.revision_response.model_dump(mode="json") if trace.revision_response else None
    request = trace.revision_request.model_dump(mode="json") if trace.revision_request else None
    response_metadata = response.get("trace_metadata") if isinstance(response, dict) else {}
    telemetry = response_metadata.get("llm_runtime_telemetry") if isinstance(response_metadata, dict) else None
    payload = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_kind": "llm_runtime_trace",
        "persisted_at_utc": now.isoformat(),
        "session_date": resolved_day,
        "event_id": trace.event_id,
        "event_ids": [trace.event_id],
        "trace_id": trace.trace_id,
        "status": trace.status,
        "response_status": response.get("status") if isinstance(response, dict) else None,
        "trigger_count": trace.trigger_count,
        "trigger_types": [trigger.trigger_type for trigger in trace.triggers],
        "selected_model": trace.model_routing.selected_model,
        "model_routing_decision": trace.model_routing.model_dump(mode="json"),
        "trigger_list": [trigger.model_dump(mode="json") for trigger in trace.triggers],
        "prompt_payload": request,
        "response": response,
        "llm_runtime_telemetry": telemetry,
        "trace": trace.model_dump(mode="json"),
    }
    _write_json(path, payload)
    _append_jsonl(day_root / "llm_runtime_events.jsonl", {**_status_from_artifact_payload(payload), "path": str(path)})
    return {
        "enabled": True,
        "ok": True,
        "status": "persisted",
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "path": str(path),
        "event_id": trace.event_id,
        "event_ids": [trace.event_id],
        "trace_id": trace.trace_id,
        "trigger_count": trace.trigger_count,
        "response_status": payload["response_status"],
        "selected_model": trace.model_routing.selected_model,
        "persisted_at_utc": now.isoformat(),
    }


def default_llm_runtime_artifact_root() -> Path:
    return resolve_shared_root() / "artifacts" / "llm-runtime"


def load_latest_llm_runtime_status(
    *,
    session_date: str | None,
    event_ids: list[str],
    artifact_root: str | Path | None = None,
) -> dict[str, Any]:
    normalized_event_ids = [event_id for event_id in dict.fromkeys(str(item).strip() for item in event_ids) if event_id]
    resolved_day = session_date or datetime.now(timezone.utc).date().isoformat()
    if not normalized_event_ids:
        return {
            "status": "not_requested",
            "session_date": resolved_day,
            "event_count": 0,
            "items": [],
            "safety_controls": build_llm_runtime_safety_controls_status(),
            "artifact_root": str(Path(artifact_root).expanduser().resolve() if artifact_root else default_llm_runtime_artifact_root()),
        }

    root = Path(artifact_root) if artifact_root is not None else default_llm_runtime_artifact_root()
    root = root.expanduser().resolve()
    day_root = root / resolved_day
    latest_by_event: dict[str, dict[str, Any]] = {}
    if day_root.exists():
        for path in sorted(day_root.glob("*.json")):
            payload = _read_json(path)
            if not isinstance(payload, dict):
                continue
            event_id = str(payload.get("event_id") or ((payload.get("trace") or {}).get("event_id") if isinstance(payload.get("trace"), dict) else "")).strip()
            if not event_id:
                continue
            status_payload = _status_from_artifact_payload(payload)
            status_payload["path"] = str(path)
            adoption = status_payload.get("llm_revision_adoption")
            if isinstance(adoption, dict):
                adoption["trace_artifact_path"] = str(path)
            latest_by_event[event_id] = status_payload

    items: list[dict[str, Any]] = []
    for event_id in normalized_event_ids:
        item = latest_by_event.get(event_id)
        if item is None:
            items.append(
                {
                    "event_id": event_id,
                    "status": "not_recorded",
                    "path": None,
                    "trace_id": None,
                    "trigger_count": 0,
                    "response_status": None,
                    "selected_model": None,
                    "adoption_status": "not_available",
                    "llm_revision_adoption": {
                        "status": "not_available",
                        "review_required": False,
                        "blocker": "trace_not_recorded",
                        "adoption_endpoint": f"/v1/events/{event_id}/llm-revision/adopt",
                        "trace_artifact_path": None,
                        "order_endpoint_call_allowed": False,
                    },
                }
            )
        else:
            items.append(item)

    recorded_count = sum(1 for item in items if item.get("status") != "not_recorded")
    return {
        "status": "recorded" if recorded_count == len(items) else ("partial" if recorded_count else "not_recorded"),
        "session_date": resolved_day,
        "event_count": len(items),
        "recorded_event_count": recorded_count,
        "items": items,
        "safety_controls": build_llm_runtime_safety_controls_status(),
        "artifact_root": str(root),
    }


def _llm_budget_policy() -> dict[str, Any]:
    token_budget = _env_int("JANUS_LLM_EVENT_TOKEN_BUDGET", DEFAULT_EVENT_TOKEN_BUDGET)
    cost_budget = _env_float("JANUS_LLM_EVENT_COST_BUDGET_USD", DEFAULT_EVENT_COST_BUDGET_USD)
    warning_fraction = _env_float("JANUS_LLM_EVENT_WARNING_FRACTION", DEFAULT_EVENT_WARNING_FRACTION)
    warning_fraction = min(max(warning_fraction, 0.0), 1.0)
    return {
        "schema_version": "llm_runtime_budget_policy_v1",
        "event_token_budget": max(token_budget, 0),
        "event_cost_budget_usd": max(cost_budget, 0.0),
        "warning_fraction": warning_fraction,
        "trigger_call_caps": _trigger_call_caps_from_env(),
        "pricing_source": "janus_configured_estimates",
        "estimated_cost_per_million_tokens": _ESTIMATED_MODEL_COST_PER_MILLION_TOKENS,
    }


def _trigger_call_caps_from_env() -> dict[str, int]:
    caps = dict(_DEFAULT_TRIGGER_CALL_CAPS)
    default_cap = _env_int("JANUS_LLM_TRIGGER_CALL_CAP_DEFAULT", 0)
    if default_cap > 0:
        for trigger_type in caps:
            caps[trigger_type] = default_cap
    for trigger_type in list(caps):
        env_key = f"JANUS_LLM_TRIGGER_CAP_{trigger_type.upper()}"
        configured = _env_int(env_key, caps[trigger_type])
        caps[trigger_type] = max(configured, 0)
    return caps


def _build_llm_pre_dispatch_guard(
    trace: LLMRuntimeTrace,
    *,
    artifact_root: str | Path | None,
    session_date: str | None,
) -> dict[str, Any]:
    request = trace.revision_request
    policy = _llm_budget_policy()
    history = _load_event_llm_runtime_history(
        trace.event_id,
        artifact_root=artifact_root,
        session_date=session_date or (request.session_date if request else None),
    )
    usage_summary = _llm_history_usage_summary(history)
    trigger_summary = _llm_history_trigger_summary(history)
    final_shutdown = _final_flat_shutdown_status(request)
    repeated = _repeated_trigger_hashes(trace.triggers, history)
    capped = _capped_trigger_types(trace.triggers, trigger_summary, policy)
    budget = _budget_status(usage_summary, policy)
    metadata = {
        "llm_runtime_safety_controls": build_llm_runtime_safety_controls_status(),
        "llm_runtime_budget_policy": policy,
        "llm_runtime_usage_before": usage_summary,
        "llm_runtime_trigger_history": trigger_summary,
        "llm_trigger_dedup": {
            "schema_version": "llm_trigger_dedup_v1",
            "repeated_trigger_hashes": repeated,
            "all_revision_triggers_repeated": bool(repeated)
            and len(repeated) == len([item for item in trace.triggers if item.requires_revision]),
        },
        "llm_trigger_call_caps": capped,
        "llm_budget_status": budget,
        "llm_final_flat_shutdown": final_shutdown,
    }
    if final_shutdown["active"]:
        return {"allowed": False, "reason": "event_final_flat_shutdown", "metadata": metadata}
    if budget["hard_block"]:
        return {"allowed": False, "reason": "llm_event_budget_exceeded", "metadata": metadata}
    if capped["hard_block"]:
        return {"allowed": False, "reason": "llm_trigger_call_cap_exceeded", "metadata": metadata}
    if metadata["llm_trigger_dedup"]["all_revision_triggers_repeated"]:
        return {"allowed": False, "reason": "repeated_trigger_hash_dedup", "metadata": metadata}
    return {"allowed": True, "reason": None, "metadata": metadata}


def _load_event_llm_runtime_history(
    event_id: str,
    *,
    artifact_root: str | Path | None,
    session_date: str | None,
) -> list[dict[str, Any]]:
    resolved_day = session_date or datetime.now(timezone.utc).date().isoformat()
    root = Path(artifact_root) if artifact_root is not None else default_llm_runtime_artifact_root()
    day_root = root.expanduser().resolve() / resolved_day
    if not day_root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(day_root.glob("*.json")):
        payload = _read_json(path)
        if not isinstance(payload, dict):
            continue
        payload_event_id = str(
            payload.get("event_id")
            or ((payload.get("trace") or {}).get("event_id") if isinstance(payload.get("trace"), dict) else "")
        ).strip()
        if payload_event_id != event_id:
            continue
        rows.append({**payload, "path": str(path)})
    return rows


def _llm_history_usage_summary(history: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "schema_version": "llm_runtime_usage_summary_v1",
        "call_count": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
    }
    for payload in history:
        response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
        trace_metadata = response.get("trace_metadata") if isinstance(response.get("trace_metadata"), dict) else {}
        if trace_metadata.get("openai_call_attempted") is not True:
            continue
        summary["call_count"] += 1
        usage = trace_metadata.get("usage") if isinstance(trace_metadata.get("usage"), dict) else {}
        selected_model = str(response.get("selected_model") or payload.get("selected_model") or "")
        usage_total = _usage_total_tokens(usage)
        summary["input_tokens"] += int(usage.get("input_tokens") or 0)
        summary["cached_input_tokens"] += int(usage.get("cached_input_tokens") or 0)
        summary["output_tokens"] += int(usage.get("output_tokens") or 0)
        summary["reasoning_tokens"] += int(usage.get("reasoning_tokens") or 0)
        summary["total_tokens"] += usage_total
        telemetry = payload.get("llm_runtime_telemetry")
        if not isinstance(telemetry, dict):
            telemetry = trace_metadata.get("llm_runtime_telemetry")
        estimated = _safe_float((telemetry or {}).get("last_call_estimated_cost_usd") if isinstance(telemetry, dict) else None)
        summary["estimated_cost_usd"] += (
            estimated if estimated is not None else _estimate_openai_cost_usd(selected_model, usage)
        )
    summary["estimated_cost_usd"] = round(float(summary["estimated_cost_usd"]), 6)
    return summary


def _llm_history_trigger_summary(history: list[dict[str, Any]]) -> dict[str, Any]:
    trigger_type_call_counts: dict[str, int] = {}
    called_trigger_hashes: set[str] = set()
    for payload in history:
        response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
        trace_metadata = response.get("trace_metadata") if isinstance(response.get("trace_metadata"), dict) else {}
        if trace_metadata.get("openai_call_attempted") is not True:
            continue
        for trigger_type in payload.get("trigger_types") or []:
            trigger_type_text = str(trigger_type or "").strip()
            if trigger_type_text:
                trigger_type_call_counts[trigger_type_text] = trigger_type_call_counts.get(trigger_type_text, 0) + 1
        for item in payload.get("trigger_list") or []:
            if not isinstance(item, dict):
                continue
            trigger_id = str(item.get("trigger_id") or "").strip()
            if trigger_id:
                called_trigger_hashes.add(trigger_id)
    return {
        "schema_version": "llm_runtime_trigger_history_v1",
        "trigger_type_call_counts": trigger_type_call_counts,
        "called_trigger_hashes": sorted(called_trigger_hashes),
    }


def _repeated_trigger_hashes(triggers: list[LLMRuntimeTrigger], history: list[dict[str, Any]]) -> list[str]:
    called = set(_llm_history_trigger_summary(history)["called_trigger_hashes"])
    repeated = [
        trigger.trigger_id
        for trigger in triggers
        if trigger.requires_revision and trigger.trigger_id in called
    ]
    return sorted(dict.fromkeys(repeated))


def _capped_trigger_types(
    triggers: list[LLMRuntimeTrigger],
    trigger_summary: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    caps = policy.get("trigger_call_caps") if isinstance(policy.get("trigger_call_caps"), dict) else {}
    counts = (
        trigger_summary.get("trigger_type_call_counts")
        if isinstance(trigger_summary.get("trigger_type_call_counts"), dict)
        else {}
    )
    capped: list[dict[str, Any]] = []
    for trigger in triggers:
        if not trigger.requires_revision:
            continue
        trigger_type = str(trigger.trigger_type)
        cap = _safe_int(caps.get(trigger_type))
        if cap is None or cap <= 0:
            continue
        count = _safe_int(counts.get(trigger_type)) or 0
        if count >= cap:
            capped.append({"trigger_type": trigger_type, "call_count": count, "cap": cap})
    return {
        "schema_version": "llm_trigger_call_caps_v1",
        "hard_block": bool(capped),
        "capped_trigger_types": capped,
    }


def _budget_status(usage_summary: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    token_budget = _safe_int(policy.get("event_token_budget")) or 0
    cost_budget = _safe_float(policy.get("event_cost_budget_usd")) or 0.0
    warning_fraction = _safe_float(policy.get("warning_fraction")) or DEFAULT_EVENT_WARNING_FRACTION
    tokens = _safe_int(usage_summary.get("total_tokens")) or 0
    cost = _safe_float(usage_summary.get("estimated_cost_usd")) or 0.0
    token_ratio = (tokens / token_budget) if token_budget else 0.0
    cost_ratio = (cost / cost_budget) if cost_budget else 0.0
    return {
        "schema_version": "llm_event_budget_status_v1",
        "hard_block": (token_budget > 0 and tokens >= token_budget) or (cost_budget > 0 and cost >= cost_budget),
        "warning": token_ratio >= warning_fraction or cost_ratio >= warning_fraction,
        "token_ratio": round(token_ratio, 6),
        "cost_ratio": round(cost_ratio, 6),
        "event_token_budget": token_budget,
        "event_cost_budget_usd": cost_budget,
        "usage_before": usage_summary,
    }


def _maybe_downgrade_trace_for_budget(trace: LLMRuntimeTrace, *, guard: dict[str, Any]) -> LLMRuntimeTrace:
    metadata = guard.get("metadata") if isinstance(guard.get("metadata"), dict) else {}
    budget = metadata.get("llm_budget_status") if isinstance(metadata.get("llm_budget_status"), dict) else {}
    if not budget.get("warning") or trace.model_routing.selected_tier != "frontier":
        return trace
    routing = trace.model_routing.model_copy(
        update={
            "selected_model": MINI_MODEL,
            "selected_tier": "mini",
            "reason": f"{trace.model_routing.reason} Downgraded from frontier because event LLM budget is at warning threshold.",
            "fallback_alias": trace.model_routing.selected_model,
        },
        deep=True,
    )
    request = trace.revision_request.model_copy(update={"model_routing": routing}, deep=True) if trace.revision_request else None
    metadata["llm_budget_model_downgrade"] = {
        "schema_version": "llm_budget_model_downgrade_v1",
        "applied": True,
        "from_model": trace.model_routing.selected_model,
        "to_model": MINI_MODEL,
        "reason": "event_budget_warning",
    }
    return trace.model_copy(update={"model_routing": routing, "revision_request": request}, deep=True)


def _response_telemetry_metadata(
    *,
    request: LLMRevisionRequest,
    usage: dict[str, int],
    guard: dict[str, Any],
) -> dict[str, Any]:
    metadata = guard.get("metadata") if isinstance(guard.get("metadata"), dict) else {}
    policy = metadata.get("llm_runtime_budget_policy") if isinstance(metadata.get("llm_runtime_budget_policy"), dict) else _llm_budget_policy()
    before = metadata.get("llm_runtime_usage_before") if isinstance(metadata.get("llm_runtime_usage_before"), dict) else {}
    last_call_cost = _estimate_openai_cost_usd(request.model_routing.selected_model, usage)
    before_cost = _safe_float(before.get("estimated_cost_usd")) or 0.0
    before_tokens = _safe_int(before.get("total_tokens")) or 0
    after_tokens = before_tokens + _usage_total_tokens(usage)
    after_cost = round(before_cost + last_call_cost, 6)
    telemetry = {
        "schema_version": "llm_runtime_telemetry_v1",
        "controls_version": SAFETY_CONTROLS_VERSION,
        "selected_model": request.model_routing.selected_model,
        "selected_tier": request.model_routing.selected_tier,
        "usage": usage,
        "last_call_estimated_cost_usd": last_call_cost,
        "event_usage_before": before,
        "event_usage_after": {
            "total_tokens": after_tokens,
            "estimated_cost_usd": after_cost,
        },
        "budget_policy": policy,
        "budget_status_before": metadata.get("llm_budget_status"),
        "trigger_dedup": metadata.get("llm_trigger_dedup"),
        "trigger_call_caps": metadata.get("llm_trigger_call_caps"),
        "final_flat_shutdown": metadata.get("llm_final_flat_shutdown"),
        "model_downgrade": metadata.get("llm_budget_model_downgrade", {"applied": False}),
        "behavior_changed": False,
    }
    return {
        **metadata,
        "estimated_cost_usd": last_call_cost,
        "llm_runtime_telemetry": telemetry,
    }


def _final_flat_shutdown_status(request: LLMRevisionRequest | None) -> dict[str, Any]:
    if request is None:
        return {
            "schema_version": "llm_final_flat_shutdown_v1",
            "active": False,
            "event_final": False,
            "event_scope_flat": False,
            "reason": "missing_revision_request",
        }
    event_final = _request_event_is_final(request)
    event_scope_flat = _request_event_scope_is_flat(request)
    return {
        "schema_version": "llm_final_flat_shutdown_v1",
        "active": event_final and event_scope_flat,
        "event_final": event_final,
        "event_scope_flat": event_scope_flat,
        "reason": "final_and_flat" if event_final and event_scope_flat else None,
    }


def _request_event_is_final(request: LLMRevisionRequest) -> bool:
    candidates: list[Any] = []
    for source in (
        request.scoreboard_pbp_summary,
        request.event_context,
        request.orderbook_state,
        request.portfolio_state,
        request.direct_clob_truth,
    ):
        if not isinstance(source, dict):
            continue
        for key in ("status", "status_text", "game_status", "game_status_text", "state", "event_status"):
            candidates.append(source.get(key))
        latest = source.get("latest_snapshot") if isinstance(source.get("latest_snapshot"), dict) else {}
        for key in ("status", "status_text", "game_status", "game_status_text", "state"):
            candidates.append(latest.get(key))
        if _truthy(source.get("final") or source.get("is_final") or source.get("event_final")):
            return True
    final_texts = {"final", "game final", "closed", "settled", "settlement", "complete", "completed", "ended", "postgame"}
    return any(_normalize(candidate) in final_texts for candidate in candidates)


def _request_event_scope_is_flat(request: LLMRevisionRequest) -> bool:
    explicit_flags: list[Any] = []
    for source in (request.direct_clob_truth, request.portfolio_state):
        if not isinstance(source, dict):
            continue
        explicit_flags.extend(
            [
                source.get("event_scope_flat"),
                source.get("current_event_scope_flat"),
                source.get("current_event_flat"),
                source.get("is_flat"),
                source.get("flat"),
            ]
        )
    if any(_truthy(value) for value in explicit_flags):
        return True
    if any(str(value).strip().lower() in {"false", "0", "no"} for value in explicit_flags if value is not None):
        return False
    has_exposure_signal = False
    for source in (request.direct_clob_truth, request.portfolio_state):
        if not isinstance(source, dict):
            continue
        if _structure_has_open_event_exposure(source):
            return False
        if _structure_reports_flat_scope(source):
            has_exposure_signal = True
    return has_exposure_signal


def _structure_reports_flat_scope(value: dict[str, Any]) -> bool:
    for key in (
        "open_orders",
        "orders",
        "open_positions",
        "positions",
        "pending_intents",
        "current_event_open_orders",
        "current_event_open_positions",
        "current_event_positions",
        "current_event_orders",
    ):
        if key not in value:
            continue
        item = value.get(key)
        if isinstance(item, list) and not item:
            return True
        if isinstance(item, dict):
            nested = item.get("orders") or item.get("positions") or item.get("items")
            if isinstance(nested, list) and not nested:
                return True
            count = _safe_int(item.get("count") or item.get("total"))
            if count == 0:
                return True
        count = _safe_int(item)
        if count == 0:
            return True
    return False


def _structure_has_open_event_exposure(value: dict[str, Any]) -> bool:
    for key in (
        "open_orders_count",
        "open_positions_count",
        "pending_intents_count",
        "current_event_open_orders_count",
        "current_event_open_positions_count",
    ):
        count = _safe_int(value.get(key))
        if count is not None and count > 0:
            return True
    for key in (
        "open_orders",
        "orders",
        "open_positions",
        "positions",
        "pending_intents",
        "current_event_open_orders",
        "current_event_open_positions",
        "current_event_positions",
        "current_event_orders",
    ):
        item = value.get(key)
        if isinstance(item, list) and item:
            return True
        if isinstance(item, dict):
            nested = item.get("orders") or item.get("positions") or item.get("items")
            if isinstance(nested, list) and nested:
                return True
            count = _safe_int(item.get("count") or item.get("total"))
            if count is not None and count > 0:
                return True
    return False


def _estimate_openai_cost_usd(model: str, usage: dict[str, Any]) -> float:
    pricing = _ESTIMATED_MODEL_COST_PER_MILLION_TOKENS.get(model) or _ESTIMATED_MODEL_COST_PER_MILLION_TOKENS[MINI_MODEL]
    input_tokens = _safe_int(usage.get("input_tokens")) or 0
    output_tokens = _safe_int(usage.get("output_tokens")) or 0
    input_cost = (input_tokens / 1_000_000.0) * float(pricing["input"])
    output_cost = (output_tokens / 1_000_000.0) * float(pricing["output"])
    return round(input_cost + output_cost, 6)


def _usage_total_tokens(usage: dict[str, Any]) -> int:
    return int(_safe_int(usage.get("input_tokens")) or 0) + int(_safe_int(usage.get("output_tokens")) or 0)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not str(value).strip():
        return default
    parsed = _safe_int(value)
    return default if parsed is None else parsed


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not str(value).strip():
        return default
    parsed = _safe_float(value)
    return default if parsed is None else parsed


def _make_trigger(
    *,
    event_id: str,
    trigger_type: str,
    source: str,
    reason: str,
    severity: str,
    evidence: dict[str, Any],
    requires_revision: bool = True,
    current_plan_stale_reason: str | None = "app_owned_live_revision_trigger_detected",
) -> LLMRuntimeTrigger:
    trigger_id = _stable_id("llm-runtime-trigger", event_id, trigger_type, evidence)
    return LLMRuntimeTrigger(
        trigger_id=trigger_id,
        event_id=event_id,
        trigger_type=trigger_type,  # type: ignore[arg-type]
        source=source,
        reason=reason,
        severity=severity,  # type: ignore[arg-type]
        requires_revision=requires_revision,
        current_plan_stale_reason=current_plan_stale_reason,
        evidence=evidence,
    )


def _trace_with_skipped_response(
    trace: LLMRuntimeTrace,
    *,
    request: LLMRevisionRequest,
    reason: str,
    metadata: dict[str, Any],
) -> LLMRuntimeTrace:
    response = LLMRevisionResponse(
        request_id=request.request_id,
        selected_model=request.model_routing.selected_model,
        status="skipped_unavailable",
        skipped_reason=reason,
        trace_metadata={
            **metadata,
            "strategy_plan_auto_replace_attempted": False,
            "llm_never_calls_order_endpoints": True,
        },
    )
    return trace.model_copy(
        update={
            "revision_response": response,
            "status": "skipped_unavailable",
            "audit_only": not bool(metadata.get("dispatch_enabled")),
            "notes": f"LLM revision failed closed: {reason}.",
        },
        deep=True,
    )


def _resolve_openai_client() -> Any | None:
    try:
        from openai import OpenAI
    except Exception:
        return None

    _load_dotenv_if_available()
    try:
        return OpenAI()
    except Exception:
        return None


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    load_dotenv(repo_root() / ".env", override=False)


def _call_openai_revision(*, client: Any, request: LLMRevisionRequest) -> tuple[Any, dict[str, int]]:
    prompt_payload = request.model_dump(mode="json")
    system_prompt = _build_live_revision_system_prompt(request)
    responses = getattr(client, "responses", None)
    parser = getattr(responses, "parse", None)
    if not callable(parser):
        raise TypeError("OpenAI client does not expose responses.parse")
    response = parser(
        model=request.model_routing.selected_model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(prompt_payload, separators=(",", ":"), sort_keys=True, ensure_ascii=True)},
        ],
        text_format=_OpenAIRevisionResponse,
        reasoning={"effort": "medium"},
        # Live revisions may include compact StrategyPlanJSON diffs and can exceed
        # 4k output tokens when the model compares stop/hold/hedge alternatives.
        max_output_tokens=8192,
        store=False,
    )
    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        parsed = response
    return parsed, _usage_from_response(response)


def _build_live_revision_system_prompt(request: LLMRevisionRequest) -> str:
    return "\n".join(
        [
            str(request.prompt_contract.get("system_persona") or JANUS_LIVE_LLM_PROMPT_CONTRACT["system_persona"]),
            "Use only the supplied JSON payload.",
            "Return strict JSON matching LLMRevisionResponse.",
            "If you return revised_strategy_plan_json, plan_owner must be one of: janus_internal_llm, codex_agent, operator, system. Use janus_internal_llm for internal live revisions.",
            "For transport, put any revised StrategyPlanJSON in revised_strategy_plan_json as a compact JSON string, or null if unchanged.",
            "For any executable live buy/sell strategy, include compiler-ready fields: entry_rules.price_policy, price_band or max_price/min_price, max_orderbook_age_seconds, max_scoreboard_age_seconds, max_spread_cents, max_abs_score_gap when score-dependent, and exit_rules.min_target_cents or target_return_fraction. For adverse moves, use stop_rules.review_after_adverse_cents plus explicit hold/hedge/add-down comparison; only include loss-exit instructions when stop_rules.loss_exit_requires_virtual_dead=true and virtual-dead evidence is present. Do not rely only on prose keys such as target_delta_cents.",
            "Put reconciliation_actions_json and blocked_actions_json into their *_json string fields as JSON arrays, even when there is only one action; use [] when empty.",
            "Put trace metadata into trace_metadata_json as a JSON object.",
            "Default to revised_strategy_plan_json=null for routine quarter reviews unless supplied evidence clearly invalidates the current plan.",
            "Do not copy the current plan into revised_strategy_plan_json just to restate it.",
            "Keep the full response compact enough for live use: concise strings, short arrays, and no repeated source payloads.",
            "Keep explanations compact; prefer trace_metadata_json with concise rationale and evidence ids.",
            "Do not call tools, order endpoints, exchange endpoints, or external APIs.",
            "Output StrategyPlanJSON/reconciliation actions only; Janus validators own all live-order authority.",
        ]
    )


def _coerce_llm_revision_response(value: Any) -> LLMRevisionResponse:
    if isinstance(value, LLMRevisionResponse):
        return value
    if isinstance(value, _OpenAIRevisionResponse):
        return _coerce_openai_revision_response(value)
    if hasattr(value, "model_dump") and callable(value.model_dump):
        value = value.model_dump(mode="json")
    if isinstance(value, dict) and (
        "revised_strategy_plan_json" in value
        or "reconciliation_actions_json" in value
        or "blocked_actions_json" in value
    ):
        return _coerce_openai_revision_response(_OpenAIRevisionResponse.model_validate(value))
    if isinstance(value, str):
        return LLMRevisionResponse.model_validate_json(value)
    if isinstance(value, dict):
        return LLMRevisionResponse.model_validate(value)
    raise TypeError(f"unsupported LLM response type: {type(value).__name__}")


def _coerce_openai_revision_response(value: _OpenAIRevisionResponse) -> LLMRevisionResponse:
    revised_plan = _loads_optional_object(value.revised_strategy_plan_json, field_name="revised_strategy_plan_json")
    reconciliation_actions = _loads_list(value.reconciliation_actions_json, field_name="reconciliation_actions_json")
    blocked_actions = _loads_list(value.blocked_actions_json, field_name="blocked_actions_json")
    trace_metadata = _loads_object(value.trace_metadata_json, field_name="trace_metadata_json")
    status = _normalize_llm_response_status(value.status)
    if status != value.status:
        trace_metadata = {**trace_metadata, "raw_llm_status": value.status}
    return LLMRevisionResponse(
        schema_version=value.schema_version,
        request_id=value.request_id,
        status=status,  # type: ignore[arg-type]
        selected_model=value.selected_model,
        revised_strategy_plan=revised_plan,
        reconciliation_actions=reconciliation_actions,
        blocked_actions=blocked_actions,
        confidence=value.confidence,
        skipped_reason=value.skipped_reason,
        trace_metadata=trace_metadata,
    )


def _loads_optional_object(value: str | None, *, field_name: str) -> dict[str, Any] | None:
    if value is None or not str(value).strip():
        return None
    parsed = json.loads(value)
    if parsed is None:
        return None
    if not isinstance(parsed, dict):
        raise ValueError(f"{field_name} must decode to an object or null")
    return parsed


def _loads_object(value: str | None, *, field_name: str) -> dict[str, Any]:
    if value is None or not str(value).strip():
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"{field_name} must decode to an object")
    return parsed


def _loads_list(value: str | None, *, field_name: str) -> list[dict[str, Any]]:
    if value is None or not str(value).strip():
        return []
    parsed = json.loads(value)
    if isinstance(parsed, dict):
        for key in ("items", "actions", "blocked_actions", "reconciliation_actions"):
            nested = parsed.get(key)
            if isinstance(nested, list):
                parsed = nested
                break
        else:
            parsed = [parsed]
    if not isinstance(parsed, list):
        raise ValueError(f"{field_name} must decode to a list")
    values: list[dict[str, Any]] = []
    for item in parsed:
        if isinstance(item, dict):
            values.append(item)
        elif isinstance(item, str):
            key = "action" if field_name == "reconciliation_actions_json" else "reason"
            values.append({key: item})
        else:
            values.append({"value": item})
    return values


def _normalize_llm_response_status(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    allowed = {"detected_only", "skipped_unavailable", "called", "response_recorded"}
    if normalized in allowed:
        return normalized
    if normalized in {"ok", "valid", "complete", "completed", "reconciled", "no_change", "no_changes", "unchanged"}:
        return "response_recorded"
    return "response_recorded"


def _usage_from_response(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    cached_input_tokens = int(getattr(input_details, "cached_tokens", 0) or 0)
    reasoning_tokens = int(getattr(output_details, "reasoning_tokens", 0) or 0)
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


def _resolve_session_date(trace: LLMRuntimeTrace, *, session_date: str | None) -> str:
    request_day = trace.revision_request.session_date if trace.revision_request else None
    return str(session_date or request_day or trace.created_at_utc.astimezone(timezone.utc).date().isoformat())


def _status_from_artifact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    trace = payload.get("trace") if isinstance(payload.get("trace"), dict) else {}
    response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
    model_routing = payload.get("model_routing_decision") if isinstance(payload.get("model_routing_decision"), dict) else {}
    trigger_list = payload.get("trigger_list") if isinstance(payload.get("trigger_list"), list) else []
    event_id = str(payload.get("event_id") or trace.get("event_id") or "").strip()
    adoption = _llm_revision_adoption_status(event_id=event_id, response=response)
    trace_metadata = response.get("trace_metadata") if isinstance(response.get("trace_metadata"), dict) else {}
    telemetry = payload.get("llm_runtime_telemetry") if isinstance(payload.get("llm_runtime_telemetry"), dict) else None
    if telemetry is None and isinstance(trace_metadata.get("llm_runtime_telemetry"), dict):
        telemetry = trace_metadata["llm_runtime_telemetry"]
    return {
        "event_id": event_id,
        "trace_id": payload.get("trace_id") or trace.get("trace_id"),
        "status": payload.get("status") or trace.get("status"),
        "response_status": payload.get("response_status") or response.get("status"),
        "skipped_reason": response.get("skipped_reason"),
        "trigger_count": int(payload.get("trigger_count") or trace.get("trigger_count") or 0),
        "trigger_types": payload.get("trigger_types") or [item.get("trigger_type") for item in trigger_list if isinstance(item, dict)],
        "selected_model": payload.get("selected_model") or model_routing.get("selected_model"),
        "selected_tier": model_routing.get("selected_tier"),
        "llm_runtime_telemetry": telemetry,
        "persisted_at_utc": payload.get("persisted_at_utc"),
        "artifact_schema_version": payload.get("schema_version"),
        "adoption_status": adoption["status"],
        "llm_revision_adoption": adoption,
    }


def _llm_revision_adoption_status(*, event_id: str, response: dict[str, Any]) -> dict[str, Any]:
    response_status = response.get("status")
    skipped_reason = response.get("skipped_reason")
    revised_plan = response.get("revised_strategy_plan")
    request_id = response.get("request_id")
    selected_model = response.get("selected_model")
    confidence = response.get("confidence")

    status_value = "not_adoptable"
    blocker = "response_missing"
    review_required = False
    if response:
        if response_status in {"detected_only", "skipped_unavailable"} or skipped_reason:
            blocker = "response_skipped_or_unavailable"
        elif not isinstance(revised_plan, dict):
            blocker = "revised_strategy_plan_missing"
        else:
            status_value = "adoptable_review_required"
            blocker = None
            review_required = True

    return {
        "status": status_value,
        "review_required": review_required,
        "blocker": blocker,
        "adoption_endpoint": f"/v1/events/{event_id}/llm-revision/adopt" if event_id else None,
        "trace_artifact_path": None,
        "request_id": request_id,
        "response_status": response_status,
        "skipped_reason": skipped_reason,
        "selected_model": selected_model,
        "confidence": confidence,
        "order_endpoint_call_allowed": False,
        "required_review_fields": ["reviewed_by", "review_reason"] if review_required else [],
        "apply_current_requires_explicit_request": True,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=_json_default))
        handle.write("\n")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return value.model_dump(mode="json")
    return str(value)


def _exception_detail(exc: Exception) -> str:
    return " ".join(str(exc).split())[:500]


def _safe_filename(value: str) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "event"))
    text = "-".join(part for part in text.split("-") if part)
    return text[:96] or "event"


def _quarter_end_evidence(live_state: dict[str, Any]) -> dict[str, Any] | None:
    if _truthy(live_state.get("quarter_end") or live_state.get("period_end")):
        return _compact_evidence(live_state)
    period_status = str(live_state.get("period_status") or live_state.get("status") or "").strip().lower()
    if period_status in {"quarter_end", "end_of_quarter", "period_end", "end_period"}:
        return _compact_evidence(live_state)
    latest = live_state.get("latest_snapshot") if isinstance(live_state.get("latest_snapshot"), dict) else live_state
    period = _safe_int((latest or {}).get("period") or live_state.get("period"))
    clock = str((latest or {}).get("game_clock") or (latest or {}).get("clock") or live_state.get("game_clock") or live_state.get("clock") or "")
    if period is not None and 1 <= period <= 4 and _clock_is_zero(clock):
        return _compact_evidence({"period": period, "clock": clock, "latest_snapshot": latest})
    for row in _pbp_rows(live_state):
        text = _normalize(" ".join(str(row.get(key) or "") for key in ("description", "actionType", "subType")))
        if "end" in text and ("quarter" in text or "period" in text):
            return _compact_evidence(row)
    return None


def _operator_trigger_type(item: dict[str, Any]) -> str | None:
    action = str(item.get("action") or item.get("reaction_type") or item.get("reason") or "").strip().lower()
    if action in {"position_adverse_move", "position_stop_review", "position_management_review"}:
        return "position_adverse_move"
    if action in {"adopt_operator_open_order", "unknown_direct_clob_order_detected", "manual_order", "operator_order"}:
        return "manual_operator_order"
    if action in {"adopt_operator_trade", "unknown_direct_clob_trade_detected", "manual_trade", "operator_trade"}:
        return "manual_operator_trade"
    if action in {"adopt_operator_position", "operator_intervention_detected", "manual_position", "operator_position"}:
        return "manual_operator_position"
    if item.get("external_trade_ids"):
        return "manual_operator_trade"
    if item.get("external_order_ids"):
        return "manual_operator_order"
    if item.get("position_size") or item.get("current_position"):
        return "manual_operator_position"
    return None


def _order_lifecycle_events(portfolio_state: dict[str, Any], strategy_decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("submitted_orders", "orders", "pending_intent_orders", "recent_order_events", "order_events"):
        value = portfolio_state.get(key)
        if isinstance(value, list):
            rows.extend(dict(item) for item in value if isinstance(item, dict))
    for decision in strategy_decisions:
        payload = decision.get("order_intent_json") or decision.get("fill_json") or decision.get("raw_json")
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _order_lifecycle_trigger_type(event: dict[str, Any]) -> str | None:
    status = str(event.get("status") or event.get("order_status") or event.get("decision_type") or "").strip().lower()
    event_type = str(event.get("event_type") or event.get("type") or event.get("action") or "").strip().lower()
    text = f"{status} {event_type}"
    target_trigger = _target_lifecycle_trigger_type(event, text=text)
    if target_trigger is not None:
        return target_trigger
    if any(value in text for value in ("pending_submit", "submitted", "open", "working", "order_intent")):
        return "janus_order_submitted"
    if any(value in text for value in ("filled", "fill", "partially_filled", "trade")):
        return "order_fill"
    if any(value in text for value in ("cancel", "canceled", "cancelled", "expired", "reject")):
        return "order_cancel"
    if any(value in text for value in ("stale", "stale_order", "stale_status")):
        return "order_stale"
    return None


def _target_lifecycle_trigger_type(event: dict[str, Any], *, text: str) -> str | None:
    if not _is_target_order_event(event):
        return None
    if any(
        value in text
        for value in (
            "submit_error",
            "placement_failed",
            "target_placement_failed",
            "target_submit_error",
            "protection_failed",
            "failed_target_placement",
            "balance",
            "allowance",
            "error",
        )
    ):
        return "target_placement_failed"
    if any(value in text for value in ("filled", "fill", "partially_filled", "trade")):
        return "target_fill"
    if any(value in text for value in ("cancel", "canceled", "cancelled", "expired", "reject")):
        return "target_cancel"
    return None


def _is_target_order_event(event: dict[str, Any]) -> bool:
    fields = [
        event.get("role"),
        event.get("order_role"),
        event.get("intent_role"),
        event.get("purpose"),
        event.get("reason"),
        event.get("action"),
        event.get("event_type"),
        event.get("type"),
    ]
    metadata = event.get("metadata") or event.get("raw_json") or event.get("order_metadata")
    if isinstance(metadata, dict):
        fields.extend(
            [
                metadata.get("role"),
                metadata.get("order_role"),
                metadata.get("intent_role"),
                metadata.get("purpose"),
                metadata.get("reason"),
                metadata.get("action"),
                metadata.get("target_order"),
                metadata.get("protective_target"),
            ]
        )
    haystack = " ".join(str(item or "").strip().lower() for item in fields)
    return "target" in haystack or "protective" in haystack


def _shock_rows(pbp_shocks: list[dict[str, Any]] | None, live_state: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(pbp_shocks, list):
        rows.extend(dict(item) for item in pbp_shocks if isinstance(item, dict))
    for key in ("player_status_shocks", "player_status_shock_events"):
        value = live_state.get(key)
        if isinstance(value, list):
            rows.extend(dict(item) for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            rows.append(dict(value))
    return rows


def _ml_pbp_evidence_rows(value: dict[str, Any] | list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        nested = value.get("signals") or value.get("triggers")
        if isinstance(nested, list):
            return [dict(item) for item in nested if isinstance(item, dict)]
        return [dict(value)]
    return []


def _ml_pbp_trigger_type(evidence: dict[str, Any]) -> str | None:
    if evidence.get("emit_trigger") is False:
        return None
    signal = str(
        evidence.get("valuation")
        or evidence.get("valuation_signal")
        or evidence.get("trigger_type")
        or evidence.get("signal")
        or ""
    ).strip().lower()
    if "undervalu" in signal or signal in {"buy_edge", "underpriced", "long_edge"}:
        return "ml_pbp_undervaluation"
    if "overvalu" in signal or signal in {"sell_edge", "overpriced", "short_edge"}:
        return "ml_pbp_overvaluation"
    if _truthy(evidence.get("undervaluation")):
        return "ml_pbp_undervaluation"
    if _truthy(evidence.get("overvaluation")):
        return "ml_pbp_overvaluation"
    return None


def _live_state_revision_events(*, live: dict[str, Any], orderbook: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    price_flip = _first_runtime_signal(
        (orderbook, ("price_flip", "price_flip_detected", "favorite_flip", "favorite_side_changed", "price_crossed_midpoint")),
        (live, ("price_flip", "price_flip_detected", "favorite_flip", "favorite_side_changed", "price_crossed_midpoint")),
    )
    if price_flip is not None:
        rows.append(
            {
                "trigger_type": "price_flip",
                "reason": "Application runtime detected a material price/favorite flip.",
                "severity": "critical",
                "evidence": price_flip,
            }
        )

    leadership_switch = _first_runtime_signal(
        (live, ("leadership_switch", "lead_change", "lead_changed", "leader_changed", "score_leader_changed")),
    )
    if leadership_switch is not None:
        rows.append(
            {
                "trigger_type": "leadership_switch",
                "reason": "Application runtime detected a scoreboard leadership switch.",
                "severity": "critical",
                "evidence": leadership_switch,
            }
        )

    score_gap_break = _first_runtime_signal(
        (live, ("score_gap_break", "score_gap_band_break", "score_gap_outside_rule")),
    )
    if score_gap_break is not None:
        rows.append(
            {
                "trigger_type": "score_gap_break",
                "reason": "Application runtime detected an explicit score-gap break signal.",
                "severity": "routine",
                "evidence": score_gap_break,
            }
        )

    recent_run = _first_runtime_signal(
        (live, ("recent_run", "scoring_run", "momentum_run", "current_run")),
    )
    if recent_run is None:
        recent_run = _computed_recent_run_evidence(live)
    if recent_run is not None:
        rows.append(
            {
                "trigger_type": "recent_run",
                "reason": "Application runtime detected a recent scoring run that may stale the live plan.",
                "severity": "routine",
                "evidence": recent_run,
            }
        )

    garbage_time = _first_runtime_signal(
        (live, ("garbage_time", "garbage_time_state", "is_garbage_time")),
    )
    if garbage_time is None:
        garbage_time = _computed_garbage_time_evidence(live)
    if garbage_time is not None:
        rows.append(
            {
                "trigger_type": "garbage_time",
                "reason": "Application runtime detected garbage-time state; no-new-entry/exit posture needs review.",
                "severity": "critical",
                "evidence": garbage_time,
            }
        )

    return rows


def _score_gap_break_evidence(plan: dict[str, Any], live: dict[str, Any]) -> dict[str, Any] | None:
    latest = live.get("latest_snapshot") if isinstance(live.get("latest_snapshot"), dict) else {}
    score_gap = _score_gap(live, latest if isinstance(latest, dict) else {})
    if score_gap is None:
        return None

    broken: list[dict[str, Any]] = []
    for strategy in plan.get("active_strategies") or []:
        if not isinstance(strategy, dict):
            continue
        entry_rules = strategy.get("entry_rules") if isinstance(strategy.get("entry_rules"), dict) else {}
        max_gap = _first_score_gap_limit(entry_rules)
        if max_gap is None or abs(score_gap) <= max_gap:
            continue
        broken.append(
            {
                "strategy_id": strategy.get("strategy_id"),
                "family": strategy.get("family"),
                "side": strategy.get("side"),
                "sleeve_id": strategy.get("sleeve_id"),
                "max_abs_score_gap": max_gap,
            }
        )

    if not broken:
        return None
    return _compact_evidence(
        {
            "computed": True,
            "score_gap": score_gap,
            "period": (latest or {}).get("period") or live.get("period"),
            "clock": (latest or {}).get("game_clock") or (latest or {}).get("clock") or live.get("game_clock") or live.get("clock"),
            "broken_strategy_count": len(broken),
            "broken_strategies": broken,
        }
    )


def _first_score_gap_limit(entry_rules: dict[str, Any]) -> float | None:
    for key in ("max_abs_score_gap", "max_close_score_gap", "max_trailing_score_gap"):
        value = _safe_float(entry_rules.get(key))
        if value is not None:
            return abs(value)
    return None


def _first_runtime_signal(*groups: tuple[dict[str, Any], tuple[str, ...]]) -> dict[str, Any] | None:
    for source, keys in groups:
        for key in keys:
            evidence = _runtime_signal_evidence(source.get(key), key=key)
            if evidence is not None:
                return evidence
    return None


def _runtime_signal_evidence(value: Any, *, key: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if value.get("emit_trigger") is False or value.get("triggered") is False:
            return None
        return _compact_evidence({"signal_key": key, **value})
    if isinstance(value, list):
        rows = [dict(item) for item in value if isinstance(item, dict)]
        if not rows:
            return None
        return _compact_evidence({"signal_key": key, "signals": rows})
    if _truthy(value):
        return {"signal_key": key, "value": value}
    return None


def _computed_recent_run_evidence(live: dict[str, Any]) -> dict[str, Any] | None:
    points_for = _safe_float(live.get("run_points_for") or live.get("recent_run_points_for") or live.get("scoring_run_for"))
    points_against = _safe_float(
        live.get("run_points_against") or live.get("recent_run_points_against") or live.get("scoring_run_against")
    )
    run_margin = _safe_float(live.get("run_margin") or live.get("recent_run_margin"))
    if run_margin is None and points_for is not None and points_against is not None:
        run_margin = points_for - points_against
    if run_margin is None or abs(run_margin) < 6:
        return None
    return _compact_evidence(
        {
            "computed": True,
            "run_margin": run_margin,
            "run_points_for": points_for,
            "run_points_against": points_against,
            "team": live.get("run_team") or live.get("recent_run_team"),
            "window_seconds": live.get("run_window_seconds") or live.get("recent_run_window_seconds"),
        }
    )


def _computed_garbage_time_evidence(live: dict[str, Any]) -> dict[str, Any] | None:
    latest = live.get("latest_snapshot") if isinstance(live.get("latest_snapshot"), dict) else live
    period = _safe_int((latest or {}).get("period") or live.get("period"))
    if period is None or period < 4:
        return None
    clock = (latest or {}).get("game_clock") or (latest or {}).get("clock") or live.get("game_clock") or live.get("clock")
    seconds_remaining = _clock_seconds_remaining(clock)
    if seconds_remaining is None or seconds_remaining > 180:
        return None
    score_gap = _score_gap(live, latest if isinstance(latest, dict) else {})
    if score_gap is None or abs(score_gap) < 15:
        return None
    return _compact_evidence(
        {
            "computed": True,
            "period": period,
            "clock": clock,
            "seconds_remaining": seconds_remaining,
            "score_gap": score_gap,
            "threshold": "period>=4, seconds_remaining<=180, abs(score_gap)>=15",
        }
    )


def _passive_plan_trigger_rows(
    plan: dict[str, Any],
    *,
    live: dict[str, Any],
    orderbook: dict[str, Any],
    event_context: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for trigger in plan.get("trigger_conditions") or []:
        if isinstance(trigger, dict):
            candidates.append(trigger)
    for strategy in plan.get("active_strategies") or []:
        if not isinstance(strategy, dict):
            continue
        for trigger in strategy.get("revision_triggers") or []:
            if isinstance(trigger, dict):
                candidates.append({**trigger, "strategy_id": strategy.get("strategy_id")})
    runtime_flags = {
        **{f"live_{key}": value for key, value in live.items() if isinstance(value, (str, int, float, bool))},
        **{f"orderbook_{key}": value for key, value in orderbook.items() if isinstance(value, (str, int, float, bool))},
        **{f"context_{key}": value for key, value in event_context.items() if isinstance(value, (str, int, float, bool))},
    }
    for trigger in candidates:
        if _truthy(trigger.get("triggered") or trigger.get("runtime_triggered") or trigger.get("active")):
            rows.append({"trigger": trigger, "runtime_flags": runtime_flags})
            continue
        semantic_evidence = _semantic_passive_plan_trigger_evidence(trigger, plan=plan, live=live)
        if semantic_evidence is not None:
            rows.append({"trigger": trigger, "runtime_flags": runtime_flags, **semantic_evidence})
    return rows


def _semantic_passive_plan_trigger_evidence(
    trigger: dict[str, Any],
    *,
    plan: dict[str, Any],
    live: dict[str, Any],
) -> dict[str, Any] | None:
    trigger_type = str(trigger.get("type") or trigger.get("trigger_type") or "").strip().lower()
    if trigger_type != "fresh_q3_state_after_halftime":
        return None
    if _plan_has_review_marker(plan, "fresh_q3_state_after_halftime_reviewed_utc", "q3_fresh_state_reviewed_utc"):
        return None

    latest = live.get("latest_snapshot") if isinstance(live.get("latest_snapshot"), dict) else live
    period = _safe_int((latest or {}).get("period") or live.get("period"))
    clock = (
        (latest or {}).get("game_clock")
        or (latest or {}).get("clock")
        or live.get("game_clock")
        or live.get("clock")
    )
    seconds_remaining = _clock_seconds_remaining(clock)
    min_seconds = _safe_float(trigger.get("min_clock_remaining_seconds"))
    max_seconds = _safe_float(trigger.get("max_clock_remaining_seconds"))
    min_seconds = 360.0 if min_seconds is None else min_seconds
    max_seconds = 720.0 if max_seconds is None else max_seconds
    if period != 3 or seconds_remaining is None or seconds_remaining < min_seconds or seconds_remaining > max_seconds:
        return None
    return {
        "semantic_trigger_type": trigger_type,
        "period": period,
        "clock": clock,
        "seconds_remaining": seconds_remaining,
        "condition": "period=3 and early-Q3 fresh state after halftime",
    }


def _plan_has_review_marker(plan: dict[str, Any], *keys: str) -> bool:
    for section_name in ("explainability", "context_summary"):
        section = plan.get(section_name)
        if not isinstance(section, dict):
            continue
        for key in keys:
            if section.get(key):
                return True
    return False


def _dedupe_triggers(triggers: list[LLMRuntimeTrigger]) -> list[LLMRuntimeTrigger]:
    seen: set[str] = set()
    result: list[LLMRuntimeTrigger] = []
    for trigger in triggers:
        if trigger.trigger_id in seen:
            continue
        seen.add(trigger.trigger_id)
        result.append(trigger)
    return result


def _has_open_exposure(portfolio_state: dict[str, Any] | None) -> bool:
    portfolio = dict(portfolio_state or {})
    for key in ("open_positions", "open_orders", "pending_intents", "pending_buy_intents", "uncovered_positions"):
        value = _safe_float(portfolio.get(key))
        if value is not None and value > 0:
            return True
    orders = portfolio.get("pending_intent_orders")
    return isinstance(orders, list) and bool(orders)


def _has_missing_protection(portfolio_state: dict[str, Any] | None) -> bool:
    portfolio = dict(portfolio_state or {})
    return _truthy(
        portfolio.get("missing_protection")
        or portfolio.get("missing_target")
        or portfolio.get("stop_hedge_review_required")
    )


def _late_game_high_uncertainty(live_state: dict[str, Any] | None) -> bool:
    live = dict(live_state or {})
    latest = live.get("latest_snapshot") if isinstance(live.get("latest_snapshot"), dict) else live
    period = _safe_int((latest or {}).get("period") or live.get("period"))
    score_gap = _safe_float((latest or {}).get("score_gap") or live.get("score_gap"))
    if period is None or period < 4:
        return False
    return score_gap is None or abs(score_gap) <= 6


def _plain_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return dict(value.model_dump(mode="json"))
    return {}


def _strategy_candidates_from_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for strategy in plan.get("active_strategies") or []:
        if not isinstance(strategy, dict):
            continue
        rows.append(
            {
                "strategy_id": strategy.get("strategy_id"),
                "family": strategy.get("family"),
                "side": strategy.get("side"),
                "entry_rules": strategy.get("entry_rules") or {},
                "exit_rules": strategy.get("exit_rules") or {},
                "stop_rules": strategy.get("stop_rules") or {},
                "hedge_rules": strategy.get("hedge_rules") or {},
                "shadow_flags": strategy.get("shadow_flags") or {},
            }
        )
    return rows


def _ml_pbp_evidence_payload(value: dict[str, Any] | list[dict[str, Any]] | None) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return {"signals": [dict(item) for item in value if isinstance(item, dict)]}
    return {"status": "placeholder_schema_ready", "signals": []}


def _scoreboard_pbp_summary(live_state: dict[str, Any]) -> dict[str, Any]:
    latest = live_state.get("latest_snapshot") if isinstance(live_state.get("latest_snapshot"), dict) else {}
    return {
        "game_id": live_state.get("game_id") or latest.get("game_id"),
        "period": latest.get("period") or live_state.get("period"),
        "clock": latest.get("game_clock") or latest.get("clock") or live_state.get("game_clock") or live_state.get("clock"),
        "home_score": latest.get("home_score") or live_state.get("home_score"),
        "away_score": latest.get("away_score") or live_state.get("away_score"),
        "recent_play_by_play_count": len(_pbp_rows(live_state)),
        "player_status_shock_count": len(_shock_rows(None, live_state)),
    }


def _operator_sizing_policy(portfolio_state: dict[str, Any]) -> dict[str, Any]:
    policy = portfolio_state.get("operator_sizing_policy") or portfolio_state.get("sizing_policy")
    return dict(policy) if isinstance(policy, dict) else {}


def _first_stale_reason(triggers: list[LLMRuntimeTrigger]) -> str | None:
    for trigger in triggers:
        if trigger.current_plan_stale_reason:
            return trigger.current_plan_stale_reason
    return None


def _mark_triggers_reviewed_by_current_plan(
    triggers: list[LLMRuntimeTrigger],
    plan: dict[str, Any],
) -> list[LLMRuntimeTrigger]:
    plan_generated_at = _parse_utc_datetime(plan.get("generated_at_utc"))
    quarter_end_reviews = _quarter_end_review_markers(plan)
    if plan_generated_at is None and not quarter_end_reviews:
        return triggers
    reviewed: list[LLMRuntimeTrigger] = []
    for trigger in triggers:
        evidence_time = _evidence_time_utc(trigger.evidence)
        quarter_end_reviewed_at = _reviewed_quarter_end_at(trigger, quarter_end_reviews)
        if trigger.requires_revision and quarter_end_reviewed_at is not None:
            reviewed.append(
                trigger.model_copy(
                    update={
                        "requires_revision": False,
                        "current_plan_stale_reason": None,
                        "reason": "Quarter boundary reviewed by current StrategyPlanJSON.",
                        "evidence": {
                            **trigger.evidence,
                            "reviewed_by_current_plan": True,
                            "quarter_end_reviewed_by_plan": True,
                            "plan_quarter_end_reviewed_at_utc": quarter_end_reviewed_at.isoformat().replace("+00:00", "Z"),
                            "trigger_evidence_time_utc": (
                                evidence_time.isoformat().replace("+00:00", "Z") if evidence_time is not None else None
                            ),
                        },
                    }
                )
            )
            continue
        if (
            trigger.requires_revision
            and evidence_time is not None
            and plan_generated_at is not None
            and plan_generated_at >= evidence_time
        ):
            reviewed.append(
                trigger.model_copy(
                    update={
                        "requires_revision": False,
                        "current_plan_stale_reason": None,
                        "reason": f"{trigger.reason} Current StrategyPlanJSON was generated after this trigger evidence.",
                        "evidence": {
                            **trigger.evidence,
                            "reviewed_by_current_plan": True,
                            "plan_generated_at_utc": plan_generated_at.isoformat().replace("+00:00", "Z"),
                            "trigger_evidence_time_utc": evidence_time.isoformat().replace("+00:00", "Z"),
                        },
                    }
                )
            )
            continue
        reviewed.append(trigger)
    return reviewed


def _quarter_end_review_markers(plan: dict[str, Any]) -> dict[int | None, datetime]:
    markers: dict[int | None, datetime] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key).strip().lower()
                parsed = _parse_utc_datetime(item)
                if parsed is not None and (
                    key_text == "quarter_end_reviewed_utc" or key_text.endswith("_quarter_end_reviewed_utc")
                ):
                    markers[_period_from_quarter_end_review_key(key_text)] = parsed
                if isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, (dict, list)):
                    visit(item)

    visit(plan)
    return markers


def _reviewed_quarter_end_at(
    trigger: LLMRuntimeTrigger,
    quarter_end_reviews: dict[int | None, datetime],
) -> datetime | None:
    if trigger.trigger_type != "quarter_end" or not quarter_end_reviews:
        return None
    period = _trigger_period(trigger.evidence)
    if period is not None and period in quarter_end_reviews:
        return quarter_end_reviews[period]
    return quarter_end_reviews.get(None) if period is None else None


def _trigger_period(evidence: dict[str, Any]) -> int | None:
    candidates = [
        evidence.get("period"),
        evidence.get("game_period"),
        evidence.get("quarter"),
    ]
    payload = evidence.get("payload_json") if isinstance(evidence.get("payload_json"), dict) else {}
    candidates.extend([payload.get("period"), payload.get("game_period"), payload.get("quarter")])
    latest = evidence.get("latest_snapshot") if isinstance(evidence.get("latest_snapshot"), dict) else {}
    candidates.extend([latest.get("period"), latest.get("game_period"), latest.get("quarter")])
    for candidate in candidates:
        parsed = _safe_int(candidate)
        if parsed is not None:
            return parsed
    return None


def _period_from_quarter_end_review_key(key: str) -> int | None:
    prefix = key.removesuffix("_quarter_end_reviewed_utc")
    if prefix in {"", "quarter_end_reviewed_utc"}:
        return None
    normalized = prefix.replace("-", "_").replace(" ", "_")
    pieces = [piece for piece in normalized.split("_") if piece]
    for piece in pieces:
        if piece.startswith("q") and piece[1:].isdigit():
            return _safe_int(piece[1:])
        if piece.isdigit():
            return _safe_int(piece)
    return None


def _evidence_time_utc(evidence: dict[str, Any]) -> datetime | None:
    candidates: list[Any] = []
    for key in (
        "captured_at",
        "captured_at_utc",
        "created_at",
        "created_at_utc",
        "detected_at",
        "detected_at_utc",
        "edited",
        "timestamp",
        "timestamp_utc",
        "updated_at",
        "updated_at_utc",
    ):
        candidates.append(evidence.get(key))
    payload = evidence.get("payload_json") if isinstance(evidence.get("payload_json"), dict) else {}
    for key in ("edited", "captured_at", "created_at", "timestamp", "updated_at"):
        candidates.append(payload.get(key))
    latest = evidence.get("latest_snapshot") if isinstance(evidence.get("latest_snapshot"), dict) else {}
    for key in ("captured_at", "captured_at_utc", "created_at", "timestamp", "updated_at"):
        candidates.append(latest.get(key))
    parsed = [_parse_utc_datetime(value) for value in candidates]
    present = [value for value in parsed if value is not None]
    return max(present) if present else None


def _parse_utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _compact_evidence(value: Any, *, limit: int = 40) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"value": value}
    result: dict[str, Any] = {}
    for index, (key, item) in enumerate(value.items()):
        if index >= limit:
            result["truncated"] = True
            break
        if isinstance(item, (str, int, float, bool)) or item is None:
            result[key] = item
        elif isinstance(item, list):
            result[key] = item[:5]
        elif isinstance(item, dict):
            result[key] = {k: v for k, v in list(item.items())[:10] if isinstance(v, (str, int, float, bool)) or v is None}
        else:
            result[key] = str(item)
    return result


def _pbp_rows(live_state: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("recent_play_by_play", "play_by_play", "pbp"):
        value = live_state.get(key)
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _clock_is_zero(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized in {"0:00", "00:00", "pt00m00.00s", "pt0m0.00s", "end"}


def _clock_seconds_remaining(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return max(int(value), 0)
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"end", "final"}:
        return 0
    if text.startswith("pt") and "m" in text and "s" in text:
        try:
            minutes_text = text.split("pt", 1)[1].split("m", 1)[0]
            seconds_text = text.split("m", 1)[1].split("s", 1)[0]
            return max(int(float(minutes_text) * 60 + float(seconds_text)), 0)
        except (IndexError, ValueError):
            return None
    if ":" in text:
        pieces = text.split(":")
        try:
            minutes = int(float(pieces[-2]))
            seconds = int(float(pieces[-1]))
            return max(minutes * 60 + seconds, 0)
        except ValueError:
            return None
    parsed = _safe_int(text)
    return parsed if parsed is not None and parsed >= 0 else None


def _score_gap(live: dict[str, Any], latest: dict[str, Any]) -> float | None:
    score_gap = _safe_float(latest.get("score_gap") or live.get("score_gap"))
    if score_gap is not None:
        return score_gap
    home_score = _safe_float(latest.get("home_score") or live.get("home_score"))
    away_score = _safe_float(latest.get("away_score") or live.get("away_score"))
    if home_score is None or away_score is None:
        return None
    return home_score - away_score


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    parsed = _safe_float(value)
    return int(parsed) if parsed is not None else None


def _normalize(value: Any) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in str(value or "")).split())


def _stable_id(*parts: Any) -> str:
    text = "|".join(_stable_part(part) for part in parts)
    return str(uuid.uuid5(_TRIGGER_NAMESPACE, text))


def _stable_part(value: Any) -> str:
    if isinstance(value, dict):
        return "{" + ",".join(f"{key}:{_stable_part(value[key])}" for key in sorted(value)) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_stable_part(item) for item in value) + "]"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


__all__ = [
    "FRONTIER_MODEL",
    "JANUS_LIVE_LLM_PROMPT_CONTRACT",
    "MINI_MODEL",
    "NANO_MODEL",
    "build_llm_prompt_contract",
    "build_llm_revision_request",
    "build_llm_runtime_trace",
    "default_llm_runtime_artifact_root",
    "detect_llm_runtime_triggers",
    "dispatch_llm_revision",
    "load_latest_llm_runtime_status",
    "persist_llm_runtime_trace",
    "process_llm_runtime_trace",
    "route_llm_model",
]
