from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any

from app.modules.agentic.contracts import (
    LLMModelRoutingDecision,
    LLMRevisionRequest,
    LLMRevisionResponse,
    LLMRuntimeTrace,
    LLMRuntimeTrigger,
)


NANO_MODEL = "gpt-5.4-nano"
MINI_MODEL = "gpt-5.4-mini"
FRONTIER_MODEL = "gpt-5.5"
ROUTING_RULES_VERSION = "llm_model_routing_2026-05-11"

_TRIGGER_NAMESPACE = uuid.UUID("4f61cce7-2098-44d7-a59e-2b3c92a48f0f")
_CRITICAL_TRIGGER_TYPES = {
    "manual_operator_order",
    "manual_operator_trade",
    "manual_operator_position",
    "player_status_shock",
    "stale_feed_recovery",
    "unexplained_clob_move",
}
_ORDER_LIFECYCLE_TRIGGER_TYPES = {
    "janus_order_submitted",
    "order_fill",
    "order_cancel",
    "order_stale",
}
_NANO_TRIGGER_TYPES = {"compression_or_tagging"}


JANUS_LIVE_LLM_PROMPT_CONTRACT: dict[str, Any] = {
    "schema_version": "janus_live_llm_prompt_contract_v1",
    "system_persona": (
        "You are the internal Janus live-trading revision engine. You reason about NBA prediction-market "
        "risk using only supplied Janus runtime evidence, prioritize capital preservation, and output "
        "structured revision and reconciliation JSON."
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
    "safety_rule": "No order endpoint calls are allowed from LLM output or prompt tools.",
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
        severity = "critical" if trigger_type in {"order_fill", "order_cancel", "order_stale"} else "routine"
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

    return _dedupe_triggers(triggers)


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
    if any(value in text for value in ("pending_submit", "submitted", "open", "working", "order_intent")):
        return "janus_order_submitted"
    if any(value in text for value in ("filled", "fill", "partially_filled", "trade")):
        return "order_fill"
    if any(value in text for value in ("cancel", "canceled", "cancelled", "expired", "reject")):
        return "order_cancel"
    if any(value in text for value in ("stale", "stale_order", "stale_status")):
        return "order_stale"
    return None


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
    return rows


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
    "detect_llm_runtime_triggers",
    "route_llm_model",
]
