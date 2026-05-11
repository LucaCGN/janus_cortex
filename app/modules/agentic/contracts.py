from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PlanOwner = Literal["janus_internal_llm", "codex_agent", "operator", "system"]
LLMRuntimeTriggerType = Literal[
    "quarter_end",
    "janus_order_submitted",
    "order_fill",
    "order_cancel",
    "order_stale",
    "manual_operator_order",
    "manual_operator_trade",
    "manual_operator_position",
    "player_status_shock",
    "stale_feed_recovery",
    "unexplained_clob_move",
    "ml_pbp_undervaluation",
    "ml_pbp_overvaluation",
    "strategy_plan_revision_trigger",
    "routine_live_review",
    "compression_or_tagging",
]
LLMModelTier = Literal["nano", "mini", "frontier"]
LLMRuntimeStatus = Literal["detected_only", "skipped_unavailable", "called", "response_recorded"]


class ActiveStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    side: str = Field(min_length=1)
    budget_usd: float = Field(default=0.0, ge=0.0)
    max_positions: int = Field(default=1, ge=0, le=100)
    entry_rules: dict[str, Any] = Field(default_factory=dict)
    exit_rules: dict[str, Any] = Field(default_factory=dict)
    stop_rules: dict[str, Any] = Field(default_factory=dict)
    hedge_rules: dict[str, Any] = Field(default_factory=dict)
    revision_triggers: list[dict[str, Any]] = Field(default_factory=list)
    shadow_flags: dict[str, Any] = Field(default_factory=dict)


class StrategyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="strategy_plan_v1", min_length=1)
    event_id: str = Field(min_length=1)
    market_id: str = Field(min_length=1)
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    valid_until_utc: datetime | None = None
    plan_owner: PlanOwner = "janus_internal_llm"
    context_summary: dict[str, Any] = Field(default_factory=dict)
    active_strategies: list[ActiveStrategy] = Field(default_factory=list)
    trigger_conditions: list[dict[str, Any]] = Field(default_factory=list)
    portfolio_reconciliation: list[dict[str, Any]] = Field(default_factory=list)
    explainability: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_plan(self) -> "StrategyPlan":
        if self.valid_until_utc is not None and self.valid_until_utc <= self.generated_at_utc:
            raise ValueError("valid_until_utc must be after generated_at_utc")
        strategy_ids = [item.strategy_id for item in self.active_strategies]
        if len(strategy_ids) != len(set(strategy_ids)):
            raise ValueError("active_strategies strategy_id values must be unique")
        return self


class LLMRuntimeTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    trigger_type: LLMRuntimeTriggerType
    source: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    detected_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity: Literal["info", "routine", "critical"] = "routine"
    requires_revision: bool = True
    current_plan_stale_reason: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class LLMModelRoutingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_model: str = Field(min_length=1)
    selected_tier: LLMModelTier
    reason: str = Field(min_length=1)
    trigger_ids: list[str] = Field(default_factory=list)
    critical_reasons: list[str] = Field(default_factory=list)
    fallback_alias: str | None = None
    routing_rules_version: str = "llm_model_routing_2026-05-11"


class LLMRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "llm_revision_request_v1"
    request_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    market_id: str | None = None
    session_date: str | None = None
    created_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    triggers: list[LLMRuntimeTrigger] = Field(default_factory=list)
    model_routing: LLMModelRoutingDecision
    prompt_contract: dict[str, Any] = Field(default_factory=dict)
    current_plan: dict[str, Any] = Field(default_factory=dict)
    event_context: dict[str, Any] = Field(default_factory=dict)
    deterministic_strategy_candidates: list[dict[str, Any]] = Field(default_factory=list)
    ml_pbp_trigger_evidence: dict[str, Any] = Field(default_factory=dict)
    direct_clob_truth: dict[str, Any] = Field(default_factory=dict)
    orderbook_state: dict[str, Any] = Field(default_factory=dict)
    portfolio_state: dict[str, Any] = Field(default_factory=dict)
    operator_interventions: list[dict[str, Any]] = Field(default_factory=list)
    strategy_decisions: list[dict[str, Any]] = Field(default_factory=list)
    scoreboard_pbp_summary: dict[str, Any] = Field(default_factory=dict)
    current_plan_stale_reason: str | None = None
    operator_sizing_policy: dict[str, Any] = Field(default_factory=dict)


class LLMRevisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "llm_revision_response_v1"
    request_id: str = Field(min_length=1)
    status: LLMRuntimeStatus = "detected_only"
    selected_model: str = Field(min_length=1)
    revised_strategy_plan: dict[str, Any] | None = None
    reconciliation_actions: list[dict[str, Any]] = Field(default_factory=list)
    blocked_actions: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    skipped_reason: str | None = "detection_only_no_openai_call"
    trace_metadata: dict[str, Any] = Field(default_factory=dict)


class LLMRuntimeTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    created_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trigger_count: int = Field(default=0, ge=0)
    triggers: list[LLMRuntimeTrigger] = Field(default_factory=list)
    model_routing: LLMModelRoutingDecision
    revision_request: LLMRevisionRequest | None = None
    revision_response: LLMRevisionResponse | None = None
    status: LLMRuntimeStatus = "detected_only"
    audit_only: bool = True
    notes: str | None = None


class OpsCycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_date: str | None = None
    event_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None
    account_id: str | None = None
    source: str = "codex"
    notes: str | None = None
    execute: bool = False


class PregamePlanRequest(OpsCycleRequest):
    research_markdown: str | None = None
    research_path: str | None = None
    strategy_plans: list[StrategyPlan] = Field(default_factory=list)


class WatchlistEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_key: str = Field(min_length=1)
    category: Literal["nba", "crypto_options", "geopolitics", "other"] = "other"
    title: str = Field(min_length=1)
    source_urls: list[str] = Field(default_factory=list)
    market_id: str | None = None
    notes: str | None = None
    passive_only: bool = True


class WatchlistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[WatchlistEvent] = Field(default_factory=list)
    source: str = "codex"


class MarketWatchSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    watch_session_id: str | None = None
    event_key: str = Field(min_length=1)
    category: Literal["nba", "crypto_options", "geopolitics", "other"] = "other"
    passive_only: bool = True
    cadence_ms: int | None = Field(default=None, ge=0)
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MarketOrderbookTick(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_key: str = Field(min_length=1)
    market_id: str | None = None
    outcome_id: str | None = None
    token_id: str | None = None
    captured_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_timestamp_utc: datetime | None = None
    best_bid: float | None = Field(default=None, ge=0.0, le=1.0)
    best_ask: float | None = Field(default=None, ge=0.0, le=1.0)
    spread: float | None = Field(default=None, ge=0.0)
    mid_price: float | None = Field(default=None, ge=0.0, le=1.0)
    bid_depth: float | None = Field(default=None, ge=0.0)
    ask_depth: float | None = Field(default=None, ge=0.0)
    source_latency_ms: float | None = Field(default=None, ge=0.0)
    ingest_latency_ms: float | None = Field(default=None, ge=0.0)
    levels: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class MarketOrderbookTickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticks: list[MarketOrderbookTick] = Field(default_factory=list)
    source: str = "codex"


class MarketTradeObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_key: str = Field(min_length=1)
    market_id: str | None = None
    outcome_id: str | None = None
    token_id: str | None = None
    external_trade_id: str | None = None
    trade_time_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    observed_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    side: str | None = None
    price: float | None = Field(default=None, ge=0.0, le=1.0)
    size: float | None = Field(default=None, ge=0.0)
    source_latency_ms: float | None = Field(default=None, ge=0.0)
    raw: dict[str, Any] = Field(default_factory=dict)


class MarketTradeObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trades: list[MarketTradeObservation] = Field(default_factory=list)
    source: str = "codex"


class ReplayFromWatchSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    watch_session_id: str = Field(min_length=1)
    event_key: str | None = None
    output_name: str | None = None
    notes: str | None = None


class OperatorInterventionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str | None = None
    event_id: str | None = None
    market_id: str | None = None
    action: Literal["scan", "adopt", "reject", "protect", "target", "hedge", "cancel", "pause", "ignore"] = "scan"
    external_order_ids: list[str] = Field(default_factory=list)
    external_trade_ids: list[str] = Field(default_factory=list)
    strategy_family: str | None = None
    manual_reason: str | None = None
    target_status: str | None = None
    stop_status: str | None = None
    hedge_status: str | None = None
    protective_order_status: str | None = None
    expected_close_path: str | None = None
    final_pnl_usd: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None


class OrderIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    market_id: str = Field(min_length=1)
    outcome_id: str = Field(min_length=1)
    token_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    strategy_family: str = Field(min_length=1)
    side: Literal["buy", "sell"]
    order_type: Literal["limit", "market"] = "limit"
    price: float = Field(ge=0.0, le=1.0)
    size: float = Field(gt=0.0)
    time_in_force: str = "gtc"
    dry_run: bool = True
    reason: str = "strategy_plan"
    metadata: dict[str, Any] = Field(default_factory=dict)


class StrategyPlanEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: StrategyPlan | None = None
    session_date: str | None = None
    account_id: str | None = None
    dry_run: bool = True
    execute: bool = False
    market_state: dict[str, Any] = Field(default_factory=dict)
    portfolio_state: dict[str, Any] = Field(default_factory=dict)
    source: str = "codex"
    max_intents: int = Field(default=10, ge=0, le=100)


class StrategyPlanEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    market_id: str
    evaluated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    intent_count: int = 0
    blocked_count: int = 0
    intents: list[OrderIntent] = Field(default_factory=list)
    blockers: list[dict[str, Any]] = Field(default_factory=list)
    executed_orders: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "ActiveStrategy",
    "LLMModelRoutingDecision",
    "LLMModelTier",
    "LLMRevisionRequest",
    "LLMRevisionResponse",
    "LLMRuntimeStatus",
    "LLMRuntimeTrace",
    "LLMRuntimeTrigger",
    "LLMRuntimeTriggerType",
    "MarketOrderbookTick",
    "MarketOrderbookTickRequest",
    "MarketTradeObservation",
    "MarketTradeObservationRequest",
    "MarketWatchSessionRequest",
    "OperatorInterventionRequest",
    "OrderIntent",
    "OpsCycleRequest",
    "PregamePlanRequest",
    "ReplayFromWatchSessionRequest",
    "StrategyPlan",
    "StrategyPlanEvaluationRequest",
    "StrategyPlanEvaluationResult",
    "WatchlistEvent",
    "WatchlistRequest",
]
