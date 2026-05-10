from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PlanOwner = Literal["janus_internal_llm", "codex_agent", "operator", "system"]


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


class OpsCycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_date: str | None = None
    event_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None
    source: str = "codex"
    notes: str | None = None
    execute: bool = False


class PregamePlanRequest(OpsCycleRequest):
    research_markdown: str | None = None
    research_path: str | None = None


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
    action: Literal["scan", "adopt", "protect", "target", "hedge", "cancel", "pause", "ignore"] = "scan"
    external_order_ids: list[str] = Field(default_factory=list)
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
