from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PortfolioGroup = Literal[
    "janus-controlled",
    "codex-assisted",
    "operator-manual",
    "watch-only",
    "future-domain-candidate",
    "unknown",
]
SourceActor = Literal["janus", "codex", "operator", "external", "unknown"]
TargetState = Literal["target_present", "target_stale", "target_missing", "target_unknown"]
RiskBucket = Literal["janus-sports", "global-portfolio", "future-domain", "operator-manual", "unknown"]
TimeHorizon = Literal["intraday", "short", "medium", "long", "unknown"]
ResolutionRisk = Literal["low", "medium", "high", "unknown"]
PortfolioSide = Literal["yes", "no", "long", "short", "unknown"]
PortfolioManagerAction = Literal[
    "existing_position_target",
    "existing_position_cancel",
    "existing_position_replace",
    "existing_position_close",
    "trend_entry",
    "trend_exit",
    "unknown",
]
ExecutionGateName = Literal[
    "direct_clob_truth_fresh",
    "market_token_order_state_resolved",
    "approved_order_management_path",
    "portfolio_ledger_path",
    "separate_risk_budget",
    "minimum_order_compliance",
    "target_stop_rebuy_policy",
    "kill_switch_clear",
    "non_runtime_truth_rejected",
]
ExecutionGateResult = Literal["execution_gates_satisfied", "management_plan_only_execution_gate_missing"]
PortfolioManagerActionPlanStatus = Literal[
    "management_plan_only_execution_gate_missing",
    "ready_for_approved_order_management_call",
]
ManagedSlotKind = Literal["filled_position", "approved_resting_entry"]
ManagedSlotStatus = Literal["active", "pending_entry", "ignored_covered_market"]
CandidateStatus = Literal["ready_for_order_proof", "rejected", "watch_only"]
GridEligibilityStatus = Literal["eligible_for_service_spawn_proof", "blocked_missing_grid_gates"]
StrategyStyle = Literal[
    "quick_trade",
    "grid_candidate",
    "trend_follow",
    "catalyst_option",
    "long_thesis",
    "target_maintenance",
    "unknown",
]
SizingTier = Literal["validation", "micro_only", "scalable_candidate", "scale_limited", "unknown"]


NO_EXECUTION_STATEMENT = "No execution is authorized by this artifact."
TWENTY_SLOT_SCHEMA_VERSION = "global_portfolio_20_slot_board_v1"
DEEP_PASS_SCHEMA_VERSION = "global_portfolio_deep_pass_plan_v1"
TOP_HOLDER_SCAN_SCHEMA_VERSION = "global_portfolio_top_holder_scan_v1"
GRID_ELIGIBILITY_SCHEMA_VERSION = "global_portfolio_grid_eligibility_review_v1"
DEFAULT_TARGET_MANAGED_SLOTS = 20
DEFAULT_CODEX_SLEEVE_CAP_USD = Decimal("50")
DEFAULT_MAX_EQUITY_FRACTION = Decimal("0.5")
DEFAULT_PER_POSITION_CAP_USD = Decimal("5")
DEFAULT_GRID_30D_RANGE_THRESHOLD_PERCENT = Decimal("10")
DEFAULT_GRID_MIN_DAYS_TO_RESOLUTION = 30
DEFAULT_GRID_MAX_SPREAD_CENTS = Decimal("2")
DEFAULT_GRID_MIN_DEPTH_USD = Decimal("10")
DEFAULT_MAX_SLIPPAGE_TO_EDGE_RATIO = Decimal("0.35")
DEFAULT_MIN_QUICK_TRADE_EDGE_CENTS = Decimal("2")
DEFAULT_SCALE_NOTIONAL_PROBE_USD = Decimal("1000")
DEFAULT_SCALABLE_DEPTH_USD = Decimal("250")
EXECUTION_GATE_ORDER: tuple[ExecutionGateName, ...] = (
    "direct_clob_truth_fresh",
    "market_token_order_state_resolved",
    "approved_order_management_path",
    "portfolio_ledger_path",
    "separate_risk_budget",
    "minimum_order_compliance",
    "target_stop_rebuy_policy",
    "kill_switch_clear",
    "non_runtime_truth_rejected",
)
EXECUTION_GATE_LABELS: dict[ExecutionGateName, str] = {
    "direct_clob_truth_fresh": "fresh direct CLOB/account truth",
    "market_token_order_state_resolved": "resolved market/token/order state",
    "approved_order_management_path": "approved Janus portfolio order-management path",
    "portfolio_ledger_path": "portfolio ledger evidence path",
    "separate_risk_budget": "separate global-portfolio risk budget",
    "minimum_order_compliance": "minimum-order/minimum-notional compliance",
    "target_stop_rebuy_policy": "target/stop/rebuy policy",
    "kill_switch_clear": "kill switch clear",
    "non_runtime_truth_rejected": "screenshots/chat/stale mirrors rejected as execution truth",
}
_NON_AUTHORITATIVE_TRUTH_SOURCES = {
    "chat",
    "chat_memory",
    "github",
    "github_issue",
    "obsidian",
    "portfolio_mirror",
    "screenshot",
    "screenshots",
    "stale_mirror",
    "ui",
    "web_ui",
}


class GlobalPortfolioExecutionGateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: PortfolioManagerAction = "unknown"
    market_title: str | None = None
    market_slug: str | None = None
    token_id: str | None = None
    direct_clob_truth_fresh: bool = False
    market_token_order_state_resolved: bool = False
    approved_order_management_path: bool = False
    portfolio_ledger_path: bool = False
    separate_risk_budget: bool = False
    minimum_order_compliance: bool = False
    target_stop_rebuy_policy: bool = False
    kill_switch_clear: bool = False
    non_runtime_truth_rejected: bool = False
    approved_execution_path: Literal["janus_portfolio_order_management", "independent_polymarket_fallback"] | None = None
    adapter_name: str | None = None
    adapter_version: str | None = None
    risk_budget_name: str | None = None
    risk_budget: dict[str, Any] = Field(default_factory=dict)
    minimum_order_proof: dict[str, Any] = Field(default_factory=dict)
    target_stop_rebuy_policy_detail: dict[str, Any] = Field(default_factory=dict)
    kill_switch_clearance: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    reconciliation_plan: dict[str, Any] | str | None = None
    truth_sources: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    missing_gates: list[ExecutionGateName] = Field(default_factory=list)
    rejected_truth_sources: list[str] = Field(default_factory=list)
    result: ExecutionGateResult = "management_plan_only_execution_gate_missing"
    execution_authorized: bool = False
    order_preparation_authorized: bool = False
    live_order_impact: Literal["none", "read-only", "order-path"] = "read-only"
    proof_diagnostics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _summarize_execution_gates(self) -> "GlobalPortfolioExecutionGateSnapshot":
        rejected_sources = _rejected_truth_sources(self.truth_sources)
        missing: list[ExecutionGateName] = []
        for gate in EXECUTION_GATE_ORDER:
            gate_value = bool(getattr(self, gate))
            if gate == "market_token_order_state_resolved":
                gate_value = gate_value and _has_market_token_order_state_proof(self)
            elif gate == "approved_order_management_path":
                gate_value = gate_value and _has_adapter_proof(self)
            elif gate == "portfolio_ledger_path":
                gate_value = gate_value and _has_portfolio_ledger_proof(self)
            elif gate == "separate_risk_budget":
                gate_value = gate_value and _has_named_global_portfolio_risk_budget(self)
            elif gate == "minimum_order_compliance":
                gate_value = gate_value and _has_minimum_order_proof(self)
            elif gate == "target_stop_rebuy_policy":
                gate_value = gate_value and _has_target_stop_rebuy_policy(self)
            elif gate == "kill_switch_clear":
                gate_value = gate_value and _has_kill_switch_clearance(self)
            if gate == "non_runtime_truth_rejected":
                gate_value = gate_value and not rejected_sources
            if not gate_value:
                missing.append(gate)

        self.rejected_truth_sources = rejected_sources
        self.missing_gates = missing
        self.execution_authorized = not missing
        self.order_preparation_authorized = self.execution_authorized
        self.result = "execution_gates_satisfied" if self.execution_authorized else "management_plan_only_execution_gate_missing"
        self.live_order_impact = "order-path" if self.execution_authorized else "read-only"
        self.proof_diagnostics = _build_execution_gate_diagnostics(
            self,
            missing_gates=missing,
            rejected_truth_sources=rejected_sources,
        )
        return self


def build_execution_gate_snapshot(**kwargs: Any) -> GlobalPortfolioExecutionGateSnapshot:
    return GlobalPortfolioExecutionGateSnapshot.model_validate(kwargs)


def build_execution_gate_diagnostics(snapshot: GlobalPortfolioExecutionGateSnapshot) -> dict[str, Any]:
    return _build_execution_gate_diagnostics(snapshot)


class GlobalPortfolioManagerActionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "global_portfolio_manager_action_plan_v1"
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    issue: str = "#52"
    action: PortfolioManagerAction = "unknown"
    gate_snapshot: GlobalPortfolioExecutionGateSnapshot
    proposed_action: dict[str, Any] = Field(default_factory=dict)
    management_plan: list[str] = Field(default_factory=list)
    operator_review_questions: list[str] = Field(default_factory=list)
    no_execution_statement: str = NO_EXECUTION_STATEMENT
    status: PortfolioManagerActionPlanStatus = "management_plan_only_execution_gate_missing"
    execution_authorized: bool = False
    order_preparation_authorized: bool = False
    live_order_impact: Literal["read-only", "order-path"] = "read-only"
    ledger_record: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _summarize_action_plan(self) -> "GlobalPortfolioManagerActionPlan":
        if self.no_execution_statement != NO_EXECUTION_STATEMENT:
            raise ValueError("no_execution_statement must preserve the standard non-action wording")
        if self.action == "unknown":
            self.action = self.gate_snapshot.action
        elif self.gate_snapshot.action != "unknown" and self.action != self.gate_snapshot.action:
            raise ValueError("action must match gate_snapshot.action")

        self.execution_authorized = self.gate_snapshot.execution_authorized
        self.order_preparation_authorized = self.gate_snapshot.order_preparation_authorized
        self.live_order_impact = self.gate_snapshot.live_order_impact
        self.status = (
            "ready_for_approved_order_management_call"
            if self.gate_snapshot.execution_authorized
            else "management_plan_only_execution_gate_missing"
        )

        if not self.management_plan:
            if self.gate_snapshot.missing_gates:
                self.management_plan.append("Resolve missing execution gates before preparing or submitting any order.")
            else:
                self.management_plan.append(
                    "Use a separate approved Janus portfolio order-management call for any concrete action."
                )
        if self.gate_snapshot.missing_gates and not self.operator_review_questions:
            self.operator_review_questions.append("Which missing gate should be implemented or validated next?")

        self.ledger_record = _build_manager_action_ledger_record(self)
        return self


def build_manager_action_plan(
    *,
    gate_snapshot: GlobalPortfolioExecutionGateSnapshot | dict[str, Any],
    action: PortfolioManagerAction = "unknown",
    proposed_action: dict[str, Any] | None = None,
    management_plan: list[str] | None = None,
    operator_review_questions: list[str] | None = None,
    generated_at_utc: str | datetime | None = None,
    issue: str = "#52",
) -> GlobalPortfolioManagerActionPlan:
    snapshot = (
        gate_snapshot
        if isinstance(gate_snapshot, GlobalPortfolioExecutionGateSnapshot)
        else GlobalPortfolioExecutionGateSnapshot.model_validate(gate_snapshot)
    )
    return GlobalPortfolioManagerActionPlan(
        generated_at_utc=_parse_datetime(generated_at_utc),
        issue=issue,
        action=action,
        gate_snapshot=snapshot,
        proposed_action=dict(proposed_action or {}),
        management_plan=list(management_plan or []),
        operator_review_questions=list(operator_review_questions or []),
    )


class GlobalPortfolioWatchlistEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    watch_id: str = Field(min_length=1)
    market_title: str = Field(min_length=1)
    market_slug: str | None = None
    outcome: str | None = None
    side: PortfolioSide = "unknown"
    group: PortfolioGroup = "unknown"
    source_actor: SourceActor = "unknown"
    thesis: str | None = None
    entry_basis: str | None = None
    current_target: dict[str, Any] | None = None
    target_state: TargetState = "target_unknown"
    rebuy_ladder: list[dict[str, Any]] = Field(default_factory=list)
    risk_bucket: RiskBucket = "global-portfolio"
    time_horizon: TimeHorizon = "unknown"
    event_resolution_risk: ResolutionRisk = "unknown"
    source_evidence: list[str] = Field(default_factory=list)
    source_caveats: list[str] = Field(default_factory=list)
    concentration_tags: list[str] = Field(default_factory=list)
    policy_flags: list[str] = Field(default_factory=list)
    operator_review_questions: list[str] = Field(default_factory=list)
    recommended_followups: list[str] = Field(default_factory=list)
    execution_authorized: bool = False
    order_preparation_authorized: bool = False
    live_order_impact: Literal["none", "read-only"] = "none"

    @model_validator(mode="after")
    def _enforce_read_only_watchlist(self) -> "GlobalPortfolioWatchlistEntry":
        if self.execution_authorized:
            raise ValueError("global portfolio watchlist entries cannot authorize execution")
        if self.order_preparation_authorized:
            raise ValueError("global portfolio watchlist entries cannot authorize order preparation")
        if not self.source_evidence:
            self.source_caveats.append("source_evidence_missing")
        if self.target_state in {"target_missing", "target_stale"} and not self.operator_review_questions:
            self.operator_review_questions.append("target requires operator review before any action")
        if self.target_state == "target_present":
            _append_unique(self.policy_flags, "target_present")
            if self.current_target is None:
                _append_unique(self.policy_flags, "target_present_metadata_missing")
                _append_unique(
                    self.operator_review_questions,
                    "Target is marked present but current_target metadata is missing.",
                )
        if self.target_state == "target_missing":
            _append_unique(self.policy_flags, "target_missing")
        if self.target_state == "target_stale":
            _append_unique(self.policy_flags, "target_stale")
        if self.rebuy_ladder:
            _append_unique(self.policy_flags, "rebuy_ladder_present")
            _append_unique(
                self.operator_review_questions,
                "Rebuy ladder requires operator review before any action.",
            )
        if self.group == "future-domain-candidate" and self.risk_bucket == "global-portfolio":
            self.risk_bucket = "future-domain"
        if self.group == "operator-manual" and self.source_actor == "unknown":
            self.source_actor = "operator"
        if self.group == "future-domain-candidate":
            _append_unique(self.policy_flags, "future_domain_watch_only")
        if self.group == "operator-manual":
            _append_unique(self.policy_flags, "operator_manual_review")
        if self.event_resolution_risk == "high":
            _append_unique(self.policy_flags, "high_resolution_risk")
        return self


class GlobalPortfolioWatchlistArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "global_portfolio_watchlist_v1"
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    issue: str = "#45"
    entries: list[GlobalPortfolioWatchlistEntry] = Field(default_factory=list)
    source_caveats: list[str] = Field(default_factory=list)
    no_execution_statement: str = NO_EXECUTION_STATEMENT
    execution_authorized: bool = False
    order_preparation_authorized: bool = False
    live_order_impact: Literal["none"] = "none"
    summary: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _summarize_and_enforce_read_only(self) -> "GlobalPortfolioWatchlistArtifact":
        if self.execution_authorized:
            raise ValueError("global portfolio artifacts cannot authorize execution")
        if self.order_preparation_authorized:
            raise ValueError("global portfolio artifacts cannot authorize order preparation")
        if self.no_execution_statement != NO_EXECUTION_STATEMENT:
            raise ValueError("no_execution_statement must preserve the standard non-action wording")
        apply_watchlist_policy_flags(self.entries)
        self.summary = build_watchlist_summary(self.entries, source_caveats=self.source_caveats)
        return self


class GlobalPortfolioManagedSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_id: str = Field(min_length=1)
    slot_kind: ManagedSlotKind
    slot_status: ManagedSlotStatus
    account_id: str | None = None
    market_title: str = Field(min_length=1)
    market_slug: str | None = None
    outcome: str | None = None
    side: PortfolioSide = "unknown"
    token_id: str | None = None
    source_actor: SourceActor = "unknown"
    source_evidence: list[str] = Field(default_factory=list)
    size: float | None = None
    average_price: float | None = None
    current_price: float | None = None
    current_value_usd: float | None = None
    risk_cap_usd: float = Field(default=5.0, ge=0.0)
    horizon: TimeHorizon = "unknown"
    confidence: Literal["low", "medium", "high", "unknown"] = "unknown"
    thesis: str | None = None
    premises: list[str] = Field(default_factory=list)
    invalidation_signals: list[str] = Field(default_factory=list)
    watch_points: list[str] = Field(default_factory=list)
    target_stop_rebuy: dict[str, Any] = Field(default_factory=dict)
    latest_action_state: str = "needs_review"
    obsidian_note_path: str | None = None
    direct_truth: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _enforce_slot_defaults(self) -> "GlobalPortfolioManagedSlot":
        if not self.source_evidence:
            self.source_evidence.append("direct_truth_slot_reconciliation")
        if self.slot_kind == "approved_resting_entry":
            self.slot_status = "pending_entry"
        if self.risk_cap_usd > float(DEFAULT_PER_POSITION_CAP_USD):
            self.risk_cap_usd = float(DEFAULT_PER_POSITION_CAP_USD)
        return self


class GlobalPortfolioBudgetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "global_portfolio_budget_snapshot_v1"
    account_id: str | None = None
    target_slot_count: int = Field(default=DEFAULT_TARGET_MANAGED_SLOTS, ge=1)
    managed_slot_count: int = Field(default=0, ge=0)
    empty_slot_count: int = Field(default=DEFAULT_TARGET_MANAGED_SLOTS, ge=0)
    equity_usd: float | None = None
    cash_usd: float | None = None
    codex_sleeve_cap_usd: float = 50.0
    codex_sleeve_max_equity_fraction: float = 0.5
    effective_sleeve_cap_usd: float = 50.0
    codex_sleeve_usage_usd: float = 0.0
    codex_sleeve_remaining_usd: float = 50.0
    per_position_cap_usd: float = 5.0
    target_average_slot_notional_usd: float = 2.5
    budget_status: Literal["within_budget", "over_budget"] = "within_budget"


class GlobalPortfolio20SlotBoard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = TWENTY_SLOT_SCHEMA_VERSION
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    account_id: str | None = None
    target_slot_count: int = Field(default=DEFAULT_TARGET_MANAGED_SLOTS, ge=1)
    managed_slot_count: int = Field(default=0, ge=0)
    empty_slot_count: int = Field(default=DEFAULT_TARGET_MANAGED_SLOTS, ge=0)
    filled_position_slot_count: int = Field(default=0, ge=0)
    approved_resting_entry_slot_count: int = Field(default=0, ge=0)
    covered_market_ignored_count: int = Field(default=0, ge=0)
    budget: GlobalPortfolioBudgetSnapshot
    slots: list[GlobalPortfolioManagedSlot] = Field(default_factory=list)
    ignored_covered_market_rows: list[dict[str, Any]] = Field(default_factory=list)
    source_caveats: list[str] = Field(default_factory=list)
    side_effects: dict[str, bool] = Field(default_factory=lambda: _no_order_side_effects())
    no_execution_statement: str = NO_EXECUTION_STATEMENT

    @model_validator(mode="after")
    def _summarize_slots(self) -> "GlobalPortfolio20SlotBoard":
        self.filled_position_slot_count = sum(1 for slot in self.slots if slot.slot_kind == "filled_position")
        self.approved_resting_entry_slot_count = sum(
            1 for slot in self.slots if slot.slot_kind == "approved_resting_entry"
        )
        self.managed_slot_count = min(len(self.slots), self.target_slot_count)
        self.empty_slot_count = max(0, self.target_slot_count - self.managed_slot_count)
        self.covered_market_ignored_count = len(self.ignored_covered_market_rows)
        if self.no_execution_statement != NO_EXECUTION_STATEMENT:
            raise ValueError("no_execution_statement must preserve the standard non-action wording")
        return self


class GlobalPortfolioCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    market_title: str = Field(min_length=1)
    market_slug: str | None = None
    outcome: str | None = None
    category: str | None = None
    token_id: str | None = None
    proposed_side: Literal["buy", "sell", "unknown"] = "buy"
    proposed_price: float | None = None
    proposed_size: float | None = None
    proposed_notional_usd: float | None = None
    horizon: TimeHorizon = "unknown"
    confidence: Literal["low", "medium", "high", "unknown"] = "unknown"
    strategy_style: StrategyStyle = "unknown"
    expected_hold_days: int | None = None
    expected_return_cents: float | None = None
    expected_return_on_notional_percent: float | None = None
    estimated_entry_slippage_cents: float | None = None
    slippage_to_edge_ratio: float | None = None
    liquidity_capacity_usd: float | None = None
    payoff_velocity_score: int = 0
    sizing_tier: SizingTier = "unknown"
    sizing_guidance: dict[str, Any] = Field(default_factory=dict)
    risk_return_flags: list[str] = Field(default_factory=list)
    score: int = 0
    status: CandidateStatus = "watch_only"
    rejection_reasons: list[str] = Field(default_factory=list)
    edge_summary: str | None = None
    source_url: str | None = None
    direct_orderbook: dict[str, Any] = Field(default_factory=dict)
    profile_signal: dict[str, Any] = Field(default_factory=dict)
    top_holder_signal: dict[str, Any] = Field(default_factory=dict)
    candidate_json: dict[str, Any] = Field(default_factory=dict)


class GlobalPortfolioCandidateQueue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "global_portfolio_candidate_queue_v1"
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    account_id: str | None = None
    candidates: list[GlobalPortfolioCandidate] = Field(default_factory=list)
    ready_count: int = 0
    rejected_count: int = 0
    watch_only_count: int = 0
    side_effects: dict[str, bool] = Field(default_factory=lambda: _no_order_side_effects())

    @model_validator(mode="after")
    def _summarize_candidates(self) -> "GlobalPortfolioCandidateQueue":
        self.ready_count = sum(1 for candidate in self.candidates if candidate.status == "ready_for_order_proof")
        self.rejected_count = sum(1 for candidate in self.candidates if candidate.status == "rejected")
        self.watch_only_count = sum(1 for candidate in self.candidates if candidate.status == "watch_only")
        self.candidates = sorted(self.candidates, key=lambda item: item.score, reverse=True)
        return self


class GlobalPortfolioTopHolderScan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = TOP_HOLDER_SCAN_SCHEMA_VERSION
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    market_title: str = Field(min_length=1)
    market_slug: str | None = None
    source_url: str | None = None
    yes_holders_seen: int = 0
    no_holders_seen: int = 0
    high_profit_profile_count: int = 0
    high_profit_profiles: list[dict[str, Any]] = Field(default_factory=list)
    source_caveats: list[str] = Field(default_factory=list)
    side_effects: dict[str, bool] = Field(default_factory=lambda: _no_order_side_effects())

    @model_validator(mode="after")
    def _summarize_holder_scan(self) -> "GlobalPortfolioTopHolderScan":
        self.high_profit_profile_count = len(self.high_profit_profiles)
        return self


class GlobalPortfolioGridEligibilityReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = GRID_ELIGIBILITY_SCHEMA_VERSION
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    market_title: str = Field(min_length=1)
    market_slug: str | None = None
    token_id: str | None = None
    thirty_day_range_percent: float | None = None
    days_to_resolution: int | None = None
    stable_thesis: bool = False
    spread_cents: float | None = None
    depth_usd: float | None = None
    near_binary_catalyst: bool = False
    explicit_service_spawn_approval: bool = False
    eligible: bool = False
    status: GridEligibilityStatus = "blocked_missing_grid_gates"
    blockers: list[str] = Field(default_factory=list)
    review_json: dict[str, Any] = Field(default_factory=dict)
    side_effects: dict[str, bool] = Field(default_factory=lambda: _no_order_side_effects())

    @model_validator(mode="after")
    def _summarize_grid_review(self) -> "GlobalPortfolioGridEligibilityReview":
        blockers: list[str] = []
        if _decimal_value(self.thirty_day_range_percent) is None or _decimal_value(
            self.thirty_day_range_percent
        ) < DEFAULT_GRID_30D_RANGE_THRESHOLD_PERCENT:
            blockers.append("thirty_day_range_below_10_percent")
        if self.days_to_resolution is None or self.days_to_resolution < DEFAULT_GRID_MIN_DAYS_TO_RESOLUTION:
            blockers.append("resolution_window_below_30_days")
        if not self.stable_thesis:
            blockers.append("thesis_context_not_stable")
        if _decimal_value(self.spread_cents) is None or _decimal_value(self.spread_cents) > DEFAULT_GRID_MAX_SPREAD_CENTS:
            blockers.append("spread_too_wide_or_missing")
        if _decimal_value(self.depth_usd) is None or _decimal_value(self.depth_usd) < DEFAULT_GRID_MIN_DEPTH_USD:
            blockers.append("depth_too_thin_or_missing")
        if self.near_binary_catalyst:
            blockers.append("near_binary_catalyst")
        if not self.explicit_service_spawn_approval:
            blockers.append("explicit_service_spawn_approval_missing")
        self.blockers = blockers
        self.eligible = not blockers
        self.status = "eligible_for_service_spawn_proof" if self.eligible else "blocked_missing_grid_gates"
        return self


class GlobalPortfolioDeepPassPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = DEEP_PASS_SCHEMA_VERSION
    generated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: Literal["slot_deficit_candidate_ready", "slot_deficit_blocked", "20_slots_target_met"]
    board: GlobalPortfolio20SlotBoard
    candidate_queue: GlobalPortfolioCandidateQueue
    selected_candidate: GlobalPortfolioCandidate | None = None
    required_order_path: str = "portfolio-manager-order"
    blockers: list[str] = Field(default_factory=list)
    next_action: str
    side_effects: dict[str, bool] = Field(default_factory=lambda: _no_order_side_effects())
    no_execution_statement: str = NO_EXECUTION_STATEMENT


def build_watchlist_artifact(
    entries: list[dict[str, Any] | GlobalPortfolioWatchlistEntry],
    *,
    source_caveats: list[str] | None = None,
    generated_at_utc: str | datetime | None = None,
) -> GlobalPortfolioWatchlistArtifact:
    generated_at = _parse_datetime(generated_at_utc)
    normalized_entries = [_entry_from_raw(entry, index) for index, entry in enumerate(entries, start=1)]
    return GlobalPortfolioWatchlistArtifact(
        generated_at_utc=generated_at,
        entries=normalized_entries,
        source_caveats=list(source_caveats or []),
    )


def build_watchlist_summary(
    entries: list[GlobalPortfolioWatchlistEntry],
    *,
    source_caveats: list[str] | None = None,
) -> dict[str, Any]:
    groups = Counter(entry.group for entry in entries)
    target_states = Counter(entry.target_state for entry in entries)
    risk_buckets = Counter(entry.risk_bucket for entry in entries)
    policy_flags = Counter(flag for entry in entries for flag in entry.policy_flags)
    needs_operator_review = sum(
        1 for entry in entries if entry.target_state in {"target_missing", "target_stale"} or entry.operator_review_questions
    )
    target_uncovered_or_stale = sum(1 for entry in entries if entry.target_state in {"target_missing", "target_stale"})
    rebuy_ladder_rows = sum(1 for entry in entries if entry.rebuy_ladder)
    paired_exposure_rows = sum(1 for entry in entries if "paired_yes_no_exposure" in entry.policy_flags)
    return {
        "entry_count": len(entries),
        "groups": dict(sorted(groups.items())),
        "target_states": dict(sorted(target_states.items())),
        "risk_buckets": dict(sorted(risk_buckets.items())),
        "policy_flags": dict(sorted(policy_flags.items())),
        "target_policy": {
            "target_present_rows": target_states.get("target_present", 0),
            "target_uncovered_or_stale_rows": target_uncovered_or_stale,
            "rebuy_ladder_rows": rebuy_ladder_rows,
            "paired_exposure_rows": paired_exposure_rows,
            "policy_authority": "review_only_no_execution",
        },
        "needs_operator_review_count": needs_operator_review,
        "source_caveat_count": len(source_caveats or []),
        "execution_authorized": False,
        "order_preparation_authorized": False,
    }


def apply_watchlist_policy_flags(entries: list[GlobalPortfolioWatchlistEntry]) -> None:
    side_by_market: dict[str, set[str]] = {}
    for entry in entries:
        if entry.side not in {"yes", "no"}:
            continue
        market_key = entry.market_slug or entry.market_title
        side_by_market.setdefault(market_key, set()).add(entry.side)

    paired_markets = {market_key for market_key, sides in side_by_market.items() if {"yes", "no"}.issubset(sides)}
    if not paired_markets:
        return

    for entry in entries:
        market_key = entry.market_slug or entry.market_title
        if market_key not in paired_markets:
            continue
        _append_unique(entry.policy_flags, "paired_yes_no_exposure")
        _append_unique(
            entry.operator_review_questions,
            "Resolve paired Yes/No exposure before interpreting directional thesis.",
        )


def load_watchlist_source(payload: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload], []
    if isinstance(payload, dict):
        entries = payload.get("entries", [])
        if not isinstance(entries, list):
            raise ValueError("watchlist source 'entries' must be a list")
        caveats = payload.get("source_caveats", [])
        if not isinstance(caveats, list):
            raise ValueError("watchlist source 'source_caveats' must be a list")
        return [dict(item) for item in entries], [str(item) for item in caveats]
    raise ValueError("watchlist source must be a JSON object or list")


def build_20_slot_board(
    direct_truth_snapshot: dict[str, Any],
    *,
    account_id: str | None = None,
    target_slot_count: int = DEFAULT_TARGET_MANAGED_SLOTS,
    codex_sleeve_cap_usd: Decimal | float | str = DEFAULT_CODEX_SLEEVE_CAP_USD,
    max_equity_fraction: Decimal | float | str = DEFAULT_MAX_EQUITY_FRACTION,
    per_position_cap_usd: Decimal | float | str = DEFAULT_PER_POSITION_CAP_USD,
    generated_at_utc: str | datetime | None = None,
) -> GlobalPortfolio20SlotBoard:
    """Build the durable 20-slot board from direct account/order truth.

    This is read-only reconciliation. It counts filled open positions plus
    explicitly approved resting entry orders, excluding Janus covered NBA/WNBA
    inventory from the Codex global slot target.
    """

    generated_at = _parse_datetime(generated_at_utc)
    resolved_account_id = account_id or _portfolio_first_text(direct_truth_snapshot, ("account_id", "accountId"))
    target_slots = max(1, int(target_slot_count))
    sleeve_cap = _decimal_value(codex_sleeve_cap_usd) or DEFAULT_CODEX_SLEEVE_CAP_USD
    max_equity_fraction_value = _decimal_value(max_equity_fraction) or DEFAULT_MAX_EQUITY_FRACTION
    per_position_cap = _decimal_value(per_position_cap_usd) or DEFAULT_PER_POSITION_CAP_USD
    equity = _portfolio_equity_usd(direct_truth_snapshot)
    cash = _portfolio_cash_usd(direct_truth_snapshot)
    effective_cap = sleeve_cap
    if equity is not None:
        effective_cap = min(sleeve_cap, equity * max_equity_fraction_value)

    slots: list[GlobalPortfolioManagedSlot] = []
    ignored_rows: list[dict[str, Any]] = []
    caveats: list[str] = []
    used_notional = Decimal("0")

    for index, position in enumerate(_portfolio_list(direct_truth_snapshot, "open_positions"), start=1):
        if not isinstance(position, dict):
            caveats.append(f"open_position_row_{index}_not_object")
            continue
        if _is_covered_basketball_row(position):
            ignored_rows.append({"source": "open_positions", "row": dict(position)})
            continue
        slot = _managed_slot_from_position(
            position,
            account_id=resolved_account_id,
            index=index,
            per_position_cap_usd=per_position_cap,
        )
        slots.append(slot)
        used_notional += min(_item_notional_usd(position) or Decimal("0"), per_position_cap)

    order_rows = _portfolio_manager_order_rows(direct_truth_snapshot)
    for index, order in enumerate(order_rows, start=1):
        if not isinstance(order, dict):
            caveats.append(f"open_order_row_{index}_not_object")
            continue
        if _is_covered_basketball_row(order):
            ignored_rows.append({"source": "open_orders", "row": dict(order)})
            continue
        if not _is_approved_resting_entry_order(order):
            continue
        token_id = _portfolio_token_id(order)
        market_slug = _portfolio_market_slug(order)
        if any(_slot_identity_matches(slot, token_id=token_id, market_slug=market_slug) for slot in slots):
            caveats.append(f"approved_resting_entry_duplicates_existing_slot:{token_id or market_slug or index}")
            continue
        slot = _managed_slot_from_order(
            order,
            account_id=resolved_account_id,
            index=index,
            per_position_cap_usd=per_position_cap,
        )
        slots.append(slot)
        used_notional += min(_item_notional_usd(order) or Decimal("0"), per_position_cap)

    managed_count = min(len(slots), target_slots)
    empty_count = max(0, target_slots - managed_count)
    remaining = max(Decimal("0"), effective_cap - used_notional)
    target_average = Decimal("0")
    if empty_count:
        target_average = min(per_position_cap, remaining / Decimal(empty_count))
    elif target_slots:
        target_average = effective_cap / Decimal(target_slots)
    budget = GlobalPortfolioBudgetSnapshot(
        account_id=resolved_account_id,
        target_slot_count=target_slots,
        managed_slot_count=managed_count,
        empty_slot_count=empty_count,
        equity_usd=_decimal_to_float(equity),
        cash_usd=_decimal_to_float(cash),
        codex_sleeve_cap_usd=_decimal_to_float(sleeve_cap) or 0.0,
        codex_sleeve_max_equity_fraction=_decimal_to_float(max_equity_fraction_value) or 0.0,
        effective_sleeve_cap_usd=_decimal_to_float(effective_cap) or 0.0,
        codex_sleeve_usage_usd=_decimal_to_float(used_notional) or 0.0,
        codex_sleeve_remaining_usd=_decimal_to_float(remaining) or 0.0,
        per_position_cap_usd=_decimal_to_float(per_position_cap) or 0.0,
        target_average_slot_notional_usd=_decimal_to_float(target_average) or 0.0,
        budget_status="over_budget" if used_notional > effective_cap else "within_budget",
    )
    return GlobalPortfolio20SlotBoard(
        generated_at_utc=generated_at,
        account_id=resolved_account_id,
        target_slot_count=target_slots,
        budget=budget,
        slots=slots[:target_slots],
        ignored_covered_market_rows=ignored_rows,
        source_caveats=caveats,
    )


def score_portfolio_candidates(
    candidate_rows: list[dict[str, Any]],
    board: GlobalPortfolio20SlotBoard,
    *,
    concentration_tags: list[str] | None = None,
    generated_at_utc: str | datetime | None = None,
) -> GlobalPortfolioCandidateQueue:
    """Score candidate rows for 20-slot filling without preparing orders."""

    generated_at = _parse_datetime(generated_at_utc)
    existing_tags = set(concentration_tags or [])
    for slot in board.slots:
        existing_tags.update(_concentration_tags(slot.direct_truth))
        if slot.market_slug:
            existing_tags.add(slot.market_slug)
    candidates: list[GlobalPortfolioCandidate] = []
    for index, row in enumerate(candidate_rows, start=1):
        if not isinstance(row, dict):
            continue
        candidate = _score_candidate_row(row, board=board, existing_tags=existing_tags, index=index)
        candidates.append(candidate)
    return GlobalPortfolioCandidateQueue(
        generated_at_utc=generated_at,
        account_id=board.account_id,
        candidates=candidates,
    )


def build_top_holder_scan(
    *,
    market_title: str,
    market_slug: str | None = None,
    yes_holders: list[dict[str, Any]] | None = None,
    no_holders: list[dict[str, Any]] | None = None,
    source_url: str | None = None,
    min_profit_usd: Decimal | float | str = Decimal("10000"),
    generated_at_utc: str | datetime | None = None,
) -> GlobalPortfolioTopHolderScan:
    """Summarize Yes/No top-holder rows and promote high-profit profiles."""

    generated_at = _parse_datetime(generated_at_utc)
    threshold = _decimal_value(min_profit_usd) or Decimal("10000")
    promoted: list[dict[str, Any]] = []
    caveats: list[str] = []
    for side, holders in (("yes", yes_holders or []), ("no", no_holders or [])):
        for index, holder in enumerate(holders, start=1):
            if not isinstance(holder, dict):
                caveats.append(f"{side}_holder_row_{index}_not_object")
                continue
            profit = _profile_profit_usd(holder)
            if profit is None or profit < threshold:
                continue
            profile_name = _portfolio_first_text(
                holder,
                ("profile", "username", "name", "display_name", "user", "handle"),
            ) or f"{side}-holder-{index}"
            promoted.append(
                {
                    "profile": profile_name,
                    "side": side,
                    "profit_usd": _decimal_to_float(profit),
                    "position_value_usd": _decimal_to_float(
                        _decimal_value(
                            holder.get("position_value_usd")
                            or holder.get("positions_value_usd")
                            or holder.get("positions")
                        )
                    ),
                    "volume_usd": _decimal_to_float(_decimal_value(holder.get("volume_usd") or holder.get("volume"))),
                    "shares": _decimal_to_float(_decimal_value(holder.get("shares") or holder.get("size"))),
                    "source_row": dict(holder),
                    "promotion_reason": "high_profit_top_holder_on_yes_or_no_side",
                }
            )
    return GlobalPortfolioTopHolderScan(
        generated_at_utc=generated_at,
        market_title=market_title,
        market_slug=market_slug,
        source_url=source_url,
        yes_holders_seen=len(yes_holders or []),
        no_holders_seen=len(no_holders or []),
        high_profit_profiles=promoted,
        source_caveats=caveats,
    )


def build_grid_eligibility_review(
    *,
    market_title: str,
    market_slug: str | None = None,
    token_id: str | None = None,
    thirty_day_range_percent: Decimal | float | str | None = None,
    days_to_resolution: int | None = None,
    stable_thesis: bool = False,
    spread_cents: Decimal | float | str | None = None,
    depth_usd: Decimal | float | str | None = None,
    near_binary_catalyst: bool = False,
    explicit_service_spawn_approval: bool = False,
    review_json: dict[str, Any] | None = None,
    generated_at_utc: str | datetime | None = None,
) -> GlobalPortfolioGridEligibilityReview:
    return GlobalPortfolioGridEligibilityReview(
        generated_at_utc=_parse_datetime(generated_at_utc),
        market_title=market_title,
        market_slug=market_slug,
        token_id=token_id,
        thirty_day_range_percent=_decimal_to_float(_decimal_value(thirty_day_range_percent)),
        days_to_resolution=days_to_resolution,
        stable_thesis=stable_thesis,
        spread_cents=_decimal_to_float(_decimal_value(spread_cents)),
        depth_usd=_decimal_to_float(_decimal_value(depth_usd)),
        near_binary_catalyst=near_binary_catalyst,
        explicit_service_spawn_approval=explicit_service_spawn_approval,
        review_json=dict(review_json or {}),
    )


def build_deep_pass_plan(
    direct_truth_snapshot: dict[str, Any],
    *,
    candidate_rows: list[dict[str, Any]] | None = None,
    account_id: str | None = None,
    target_slot_count: int = DEFAULT_TARGET_MANAGED_SLOTS,
    generated_at_utc: str | datetime | None = None,
) -> GlobalPortfolioDeepPassPlan:
    """Build the 20-slot deep-pass plan. This does not call order tools."""

    generated_at = _parse_datetime(generated_at_utc)
    board = build_20_slot_board(
        direct_truth_snapshot,
        account_id=account_id,
        target_slot_count=target_slot_count,
        generated_at_utc=generated_at,
    )
    queue = score_portfolio_candidates(candidate_rows or [], board, generated_at_utc=generated_at)
    selected = queue.candidates[0] if queue.candidates and queue.candidates[0].status == "ready_for_order_proof" else None
    blockers: list[str] = []
    if board.empty_slot_count > 0 and selected is not None:
        status: Literal["slot_deficit_candidate_ready", "slot_deficit_blocked", "20_slots_target_met"] = (
            "slot_deficit_candidate_ready"
        )
        next_action = (
            "Run portfolio-manager-order dry-run for the selected candidate, then non-dry-run only if all Janus "
            "execution gates, budget gates, kill switch, ledger/idempotency, and reconciliation gates pass."
        )
    elif board.empty_slot_count > 0:
        status = "slot_deficit_blocked"
        if not candidate_rows:
            blockers.append("candidate_queue_empty")
        if board.budget.budget_status != "within_budget":
            blockers.append("codex_sleeve_budget_over_limit")
        if queue.rejected_count:
            blockers.append("all_supplied_candidates_rejected")
        next_action = "Build or refresh the candidate queue from frontend, profile, top-holder, orderbook, and web research."
    else:
        status = "20_slots_target_met"
        next_action = "Review all slots for target maintenance, replacement candidates, closes/reductions, and grid conversion."

    return GlobalPortfolioDeepPassPlan(
        generated_at_utc=generated_at,
        status=status,
        board=board,
        candidate_queue=queue,
        selected_candidate=selected,
        blockers=blockers,
        next_action=next_action,
    )


def render_watchlist_report(artifact: GlobalPortfolioWatchlistArtifact, *, artifact_path: str | None = None) -> str:
    generated_at = artifact.generated_at_utc.isoformat().replace("+00:00", "Z")
    lines = [
        "# Global Portfolio Watchlist Schema - 2026-05-18",
        "",
        f"- timestamp_utc: `{generated_at}`",
        "- automation: `janus-master-controller`",
        "- GitHub issue: `#45`",
        "- persona: `development-agent`",
        "- live-order impact: none. No orders were placed, cancelled, replaced, submitted, prepared, or authorized.",
        f"- non-action statement: `{artifact.no_execution_statement}`",
    ]
    if artifact_path is not None:
        lines.append(f"- artifact: `{artifact_path}`")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- entries: `{artifact.summary['entry_count']}`",
            f"- groups: `{artifact.summary['groups']}`",
            f"- target states: `{artifact.summary['target_states']}`",
            f"- operator-review rows: `{artifact.summary['needs_operator_review_count']}`",
            f"- target policy: `{artifact.summary['target_policy']}`",
            f"- policy flags: `{artifact.summary['policy_flags']}`",
            "",
            "## Watchlist Rows",
            "",
            "| Watch id | Group | Market | Outcome | Target state | Risk bucket |",
            "|---|---|---|---|---|---|",
        ]
    )
    if artifact.entries:
        for entry in artifact.entries:
            lines.append(
                f"| {entry.watch_id} | {entry.group} | {entry.market_title} | {entry.outcome or ''} | "
                f"{entry.target_state} | {entry.risk_bucket} |"
            )
    else:
        lines.append("| none | watch-only | No source rows supplied |  | target_unknown | global-portfolio |")
    lines.extend(
        [
            "",
            "## Schema Decision",
            "",
            "- This is a read-only artifact format for the global portfolio explorer and future target/rebuy ledger.",
            "- It separates source actor, position group, target status, rebuy ladder, risk bucket, horizon, source evidence, caveats, and operator-review questions.",
            "- It rejects execution and order-preparation authority in both row and artifact validation.",
            "- Direct CLOB/account truth remains required before any portfolio-state claim.",
            f"- This report was rendered from `{artifact.summary['entry_count']}` supplied watchlist source rows; source evidence and caveats remain row-local.",
            "",
            "## Target Policy Review",
            "",
            "- Policy flags are review-only signals for stale targets, uncovered targets, rebuy ladders, paired exposure, and future-domain/watch-only routing.",
            f"- Target policy summary: `{artifact.summary['target_policy']}`",
            "- No policy flag authorizes execution, order preparation, risk-budget promotion, or market-order use.",
            "",
            "## Next Safe Action",
            "",
            "Keep `#45` open for repeated read-only explorer runs, stale-target/rebuy policy hardening, and durable tooling gaps. No execution, order preparation, or risk-budget promotion is authorized by this report.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_execution_gate_report(snapshot: GlobalPortfolioExecutionGateSnapshot, *, issue: str = "#52") -> str:
    missing_labels = [EXECUTION_GATE_LABELS[gate] for gate in snapshot.missing_gates]
    lines = [
        "# Global Portfolio Manager Execution Gate - 2026-05-18",
        "",
        f"- GitHub issue: `{issue}`",
        "- automation: `janus-master-controller`",
        "- persona: `development-agent`",
        f"- action: `{snapshot.action}`",
        f"- result: `{snapshot.result}`",
        f"- execution_authorized: `{snapshot.execution_authorized}`",
        f"- order_preparation_authorized: `{snapshot.order_preparation_authorized}`",
        f"- live_order_impact: `{snapshot.live_order_impact}`",
        "",
        "## Gate State",
        "",
    ]
    for gate in EXECUTION_GATE_ORDER:
        value = getattr(snapshot, gate)
        if gate == "non_runtime_truth_rejected" and snapshot.rejected_truth_sources:
            value = False
        lines.append(f"- {gate}: `{bool(value)}` - {EXECUTION_GATE_LABELS[gate]}")

    lines.extend(
        [
            "",
            "## Missing Gates",
            "",
        ]
    )
    if missing_labels:
        lines.extend(f"- {label}" for label in missing_labels)
    else:
        lines.append("- none")

    if snapshot.rejected_truth_sources:
        lines.extend(
            [
                "",
                "## Rejected Truth Sources",
                "",
            ]
        )
        lines.extend(f"- {source}" for source in snapshot.rejected_truth_sources)

    lines.extend(
        [
            "",
            "## Decision",
            "",
            "- This artifact only evaluates whether the portfolio-manager execution gates are satisfied.",
            "- It does not place, cancel, replace, submit, prepare, or authorize any specific order.",
        ]
    )
    if snapshot.result == "management_plan_only_execution_gate_missing":
        lines.append("- The only valid current output is management planning until the missing gates are implemented or validated.")
    else:
        lines.append("- A separate approved order-management call would still be required for any concrete action.")
    return "\n".join(lines) + "\n"


def render_manager_action_plan(plan: GlobalPortfolioManagerActionPlan) -> str:
    generated_at = plan.generated_at_utc.isoformat().replace("+00:00", "Z")
    missing_labels = [EXECUTION_GATE_LABELS[gate] for gate in plan.gate_snapshot.missing_gates]
    lines = [
        "# Global Portfolio Manager Action Plan - 2026-05-18",
        "",
        f"- timestamp_utc: `{generated_at}`",
        f"- GitHub issue: `{plan.issue}`",
        "- automation: `janus-master-controller`",
        "- persona: `development-agent`",
        f"- action: `{plan.action}`",
        f"- status: `{plan.status}`",
        f"- execution_authorized: `{plan.execution_authorized}`",
        f"- order_preparation_authorized: `{plan.order_preparation_authorized}`",
        f"- live_order_impact: `{plan.live_order_impact}`",
        f"- non-action statement: `{plan.no_execution_statement}`",
        "",
        "## Gate Summary",
        "",
    ]
    if missing_labels:
        lines.extend(f"- missing: {label}" for label in missing_labels)
    else:
        lines.append("- missing: none")
    if plan.gate_snapshot.rejected_truth_sources:
        lines.append(f"- rejected truth sources: `{plan.gate_snapshot.rejected_truth_sources}`")

    lines.extend(
        [
            "",
            "## Management Plan",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in plan.management_plan)
    if plan.operator_review_questions:
        lines.extend(
            [
                "",
                "## Operator Review Questions",
                "",
            ]
        )
        lines.extend(f"- {item}" for item in plan.operator_review_questions)
    lines.extend(
        [
            "",
            "## Ledger Record",
            "",
            f"- schema_version: `{plan.ledger_record['schema_version']}`",
            f"- result: `{plan.ledger_record['status']}`",
            f"- missing_gates: `{plan.ledger_record['missing_gates']}`",
            "",
            "## Live-Order Impact",
            "",
            "- No orders were placed, cancelled, replaced, submitted, prepared, authorized, or executed by this plan.",
            "- A separate approved order-management call is still required for any concrete action.",
        ]
    )
    return "\n".join(lines) + "\n"


def _build_manager_action_ledger_record(plan: GlobalPortfolioManagerActionPlan) -> dict[str, Any]:
    snapshot = plan.gate_snapshot
    return {
        "schema_version": "global_portfolio_manager_action_ledger_v1",
        "issue": plan.issue,
        "generated_at_utc": plan.generated_at_utc.isoformat().replace("+00:00", "Z"),
        "action": plan.action,
        "status": plan.status,
        "result": snapshot.result,
        "execution_authorized": plan.execution_authorized,
        "order_preparation_authorized": plan.order_preparation_authorized,
        "live_order_impact": plan.live_order_impact,
        "market_title": snapshot.market_title,
        "market_slug": snapshot.market_slug,
        "token_id": snapshot.token_id,
        "approved_execution_path": snapshot.approved_execution_path,
        "adapter_name": snapshot.adapter_name,
        "adapter_version": snapshot.adapter_version,
        "risk_budget_name": snapshot.risk_budget_name,
        "risk_budget": dict(snapshot.risk_budget),
        "minimum_order_proof": dict(snapshot.minimum_order_proof),
        "target_stop_rebuy_policy_detail": dict(snapshot.target_stop_rebuy_policy_detail),
        "kill_switch_clearance": dict(snapshot.kill_switch_clearance),
        "idempotency_key": snapshot.idempotency_key,
        "reconciliation_plan": snapshot.reconciliation_plan,
        "truth_sources": list(snapshot.truth_sources),
        "rejected_truth_sources": list(snapshot.rejected_truth_sources),
        "missing_gates": list(snapshot.missing_gates),
        "proof_diagnostics": dict(snapshot.proof_diagnostics),
        "evidence": dict(snapshot.evidence),
        "proposed_action": dict(plan.proposed_action),
        "management_plan": list(plan.management_plan),
        "operator_review_questions": list(plan.operator_review_questions),
        "no_execution_statement": plan.no_execution_statement,
    }


def _entry_from_raw(entry: dict[str, Any] | GlobalPortfolioWatchlistEntry, index: int) -> GlobalPortfolioWatchlistEntry:
    if isinstance(entry, GlobalPortfolioWatchlistEntry):
        return entry
    raw = dict(entry)
    raw.setdefault("watch_id", _stable_watch_id(raw, index))
    return GlobalPortfolioWatchlistEntry.model_validate(raw)


def _stable_watch_id(raw: dict[str, Any], index: int) -> str:
    parts = [
        str(raw.get("group") or "watch"),
        str(raw.get("market_slug") or raw.get("market_title") or f"row-{index}"),
        str(raw.get("outcome") or raw.get("side") or "unknown"),
    ]
    slug = "-".join(_slugify(part) for part in parts if part)
    return slug or f"watch-row-{index}"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:120] or "unknown"


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _rejected_truth_sources(sources: list[str]) -> list[str]:
    rejected: list[str] = []
    for source in sources:
        normalized = re.sub(r"[^a-z0-9]+", "_", source.lower()).strip("_")
        if normalized in _NON_AUTHORITATIVE_TRUTH_SOURCES:
            rejected.append(normalized)
    return sorted(set(rejected))


def _has_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _numeric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _no_order_side_effects() -> dict[str, bool]:
    return {
        "orders_placed": False,
        "orders_cancelled": False,
        "orders_replaced": False,
        "orders_submitted": False,
        "orders_prepared": False,
        "live_worker_started": False,
    }


def _decimal_value(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).replace("$", "").replace(",", "").strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _portfolio_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        nested = value.get("positions") if key == "open_positions" else value.get("orders")
        if isinstance(nested, list):
            return nested
        items = value.get("items")
        if isinstance(items, list):
            return items
    aliases = {
        "open_positions": ("positions", "direct_open_positions"),
        "open_orders": ("orders", "direct_open_orders"),
    }.get(key, ())
    for alias in aliases:
        alias_value = payload.get(alias)
        if isinstance(alias_value, list):
            return alias_value
    return []


def _portfolio_first_text(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _portfolio_title(row: dict[str, Any]) -> str:
    return (
        _portfolio_first_text(
            row,
            (
                "market_title",
                "title",
                "question",
                "condition_title",
                "event_title",
                "market",
                "name",
            ),
        )
        or "Unknown global portfolio market"
    )


def _portfolio_market_slug(row: dict[str, Any]) -> str | None:
    return _portfolio_first_text(row, ("market_slug", "slug", "event_slug", "condition_slug"))


def _portfolio_token_id(row: dict[str, Any]) -> str | None:
    return _portfolio_first_text(row, ("token_id", "asset_id", "asset", "clob_token_id", "outcome_token_id"))


def _portfolio_outcome(row: dict[str, Any]) -> str | None:
    return _portfolio_first_text(row, ("outcome", "outcome_name", "side", "name"))


def _portfolio_side(row: dict[str, Any]) -> PortfolioSide:
    raw = (_portfolio_outcome(row) or _portfolio_first_text(row, ("direction", "position_side")) or "").lower()
    if raw in {"yes", "y"}:
        return "yes"
    if raw in {"no", "n"}:
        return "no"
    if raw in {"short", "sell"}:
        return "short"
    if raw in {"long", "buy"}:
        return "long"
    return "unknown"


def _portfolio_equity_usd(snapshot: dict[str, Any]) -> Decimal | None:
    for key in ("equity_usd", "portfolio_value_usd", "portfolio_value", "total_value_usd", "total_value"):
        value = _decimal_value(snapshot.get(key))
        if value is not None:
            return value
    portfolio = snapshot.get("portfolio")
    if isinstance(portfolio, dict):
        for key in ("equity_usd", "value_usd", "value"):
            value = _decimal_value(portfolio.get(key))
            if value is not None:
                return value
    collateral = snapshot.get("collateral")
    if isinstance(collateral, dict):
        cash = _decimal_value(collateral.get("balance_usd") or collateral.get("cash_usd"))
        positions = _decimal_value(collateral.get("positions_value_usd") or collateral.get("positions_value"))
        if cash is not None and positions is not None:
            return cash + positions
        return cash
    cash = _portfolio_cash_usd(snapshot)
    if cash is not None:
        positions_value = sum((_item_notional_usd(item) or Decimal("0")) for item in _portfolio_list(snapshot, "open_positions"))
        return cash + positions_value
    return None


def _portfolio_cash_usd(snapshot: dict[str, Any]) -> Decimal | None:
    for key in ("cash_usd", "available_cash_usd", "available_to_trade_usd", "available_to_trade", "collateral_usd"):
        value = _decimal_value(snapshot.get(key))
        if value is not None:
            return value
    portfolio = snapshot.get("portfolio")
    if isinstance(portfolio, dict):
        for key in ("cash_usd", "available_to_trade_usd", "available_to_trade"):
            value = _decimal_value(portfolio.get(key))
            if value is not None:
                return value
    collateral = snapshot.get("collateral")
    if isinstance(collateral, dict):
        return _decimal_value(collateral.get("balance_usd") or collateral.get("cash_usd") or collateral.get("available"))
    return None


def _item_notional_usd(row: dict[str, Any]) -> Decimal | None:
    for key in (
        "current_value_usd",
        "value_usd",
        "notional_usd",
        "position_value_usd",
        "order_notional_usd",
        "cost_basis_usd",
        "cost_basis",
        "value",
    ):
        value = _decimal_value(row.get(key))
        if value is not None:
            return abs(value)
    size = _decimal_value(row.get("size") or row.get("shares") or row.get("quantity") or row.get("balance"))
    price = _decimal_value(
        row.get("current_price")
        or row.get("price")
        or row.get("limit_price")
        or row.get("average_price")
        or row.get("avg_price")
    )
    if size is not None and price is not None:
        return abs(size * price)
    return None


def _managed_slot_from_position(
    position: dict[str, Any],
    *,
    account_id: str | None,
    index: int,
    per_position_cap_usd: Decimal,
) -> GlobalPortfolioManagedSlot:
    token_id = _portfolio_token_id(position)
    market_slug = _portfolio_market_slug(position)
    title = _portfolio_title(position)
    notional = _item_notional_usd(position)
    return GlobalPortfolioManagedSlot(
        slot_id=_stable_slot_id("position", token_id=token_id, market_slug=market_slug, title=title, index=index),
        slot_kind="filled_position",
        slot_status="active",
        account_id=account_id,
        market_title=title,
        market_slug=market_slug,
        outcome=_portfolio_outcome(position),
        side=_portfolio_side(position),
        token_id=token_id,
        source_actor=_source_actor(position),
        source_evidence=["direct_clob_open_position"],
        size=_decimal_to_float(_decimal_value(position.get("size") or position.get("shares") or position.get("quantity"))),
        average_price=_decimal_to_float(_decimal_value(position.get("average_price") or position.get("avg_price"))),
        current_price=_decimal_to_float(_decimal_value(position.get("current_price") or position.get("price"))),
        current_value_usd=_decimal_to_float(notional),
        risk_cap_usd=_decimal_to_float(per_position_cap_usd) or 5.0,
        horizon=_time_horizon(position),
        confidence=_confidence(position),
        thesis=_portfolio_first_text(position, ("thesis", "rationale", "reason")),
        premises=_string_list(position.get("premises") or position.get("key_premises")),
        invalidation_signals=_string_list(position.get("invalidation_signals") or position.get("invalidating_signals")),
        watch_points=_string_list(position.get("watch_points") or position.get("points_to_watch")),
        target_stop_rebuy=_dict_value(position.get("target_stop_rebuy") or position.get("target_policy")),
        latest_action_state=_portfolio_first_text(position, ("latest_action_state", "action_state", "status")) or "needs_review",
        obsidian_note_path=_portfolio_first_text(position, ("obsidian_note_path", "trade_rationale_note")),
        direct_truth=dict(position),
    )


def _managed_slot_from_order(
    order: dict[str, Any],
    *,
    account_id: str | None,
    index: int,
    per_position_cap_usd: Decimal,
) -> GlobalPortfolioManagedSlot:
    token_id = _portfolio_token_id(order)
    market_slug = _portfolio_market_slug(order)
    title = _portfolio_title(order)
    notional = _item_notional_usd(order)
    return GlobalPortfolioManagedSlot(
        slot_id=_stable_slot_id("entry-order", token_id=token_id, market_slug=market_slug, title=title, index=index),
        slot_kind="approved_resting_entry",
        slot_status="pending_entry",
        account_id=account_id,
        market_title=title,
        market_slug=market_slug,
        outcome=_portfolio_outcome(order),
        side=_portfolio_side(order),
        token_id=token_id,
        source_actor=_source_actor(order),
        source_evidence=["direct_clob_approved_resting_entry_order"],
        size=_decimal_to_float(_decimal_value(order.get("size") or order.get("shares") or order.get("quantity"))),
        average_price=_decimal_to_float(_decimal_value(order.get("price") or order.get("limit_price"))),
        current_price=_decimal_to_float(_decimal_value(order.get("price") or order.get("limit_price"))),
        current_value_usd=_decimal_to_float(notional),
        risk_cap_usd=_decimal_to_float(per_position_cap_usd) or 5.0,
        horizon=_time_horizon(order),
        confidence=_confidence(order),
        thesis=_portfolio_first_text(order, ("thesis", "rationale", "reason")),
        premises=_string_list(order.get("premises") or order.get("key_premises")),
        invalidation_signals=_string_list(order.get("invalidation_signals") or order.get("invalidating_signals")),
        watch_points=_string_list(order.get("watch_points") or order.get("points_to_watch")),
        target_stop_rebuy=_dict_value(order.get("target_stop_rebuy") or order.get("target_policy")),
        latest_action_state="approved_resting_entry_pending_fill",
        obsidian_note_path=_portfolio_first_text(order, ("obsidian_note_path", "trade_rationale_note")),
        direct_truth=dict(order),
    )


def _portfolio_manager_order_rows(direct_truth_snapshot: dict[str, Any]) -> list[Any]:
    rows: list[Any] = []
    seen: set[str] = set()
    for key in (
        "open_orders",
        "local_open_orders",
        "portfolio_open_orders",
        "approved_resting_entry_orders",
        "janus_open_orders",
    ):
        for item in _portfolio_list(direct_truth_snapshot, key):
            if not isinstance(item, dict):
                rows.append(item)
                continue
            identity = "|".join(
                str(item.get(field) or "").strip().lower()
                for field in ("id", "order_id", "external_order_id", "token_id", "asset_id", "market_slug")
            )
            if identity and identity in seen:
                continue
            if identity:
                seen.add(identity)
            rows.append(item)
    return rows


def _stable_slot_id(prefix: str, *, token_id: str | None, market_slug: str | None, title: str, index: int) -> str:
    key = token_id or market_slug or title or str(index)
    return f"{prefix}-{_slugify(str(key))}"


def _slot_identity_matches(slot: GlobalPortfolioManagedSlot, *, token_id: str | None, market_slug: str | None) -> bool:
    if token_id and slot.token_id == token_id:
        return True
    return bool(market_slug and slot.market_slug == market_slug)


def _is_approved_resting_entry_order(order: dict[str, Any]) -> bool:
    status = str(order.get("status") or order.get("order_status") or "").strip().lower()
    if status and status not in {"open", "live", "submitted", "pending", "pending_entry", "resting"}:
        return False
    side = str(order.get("side") or order.get("order_side") or "").strip().lower()
    if side and side not in {"buy", "bid"}:
        return False
    metadata = _dict_value(order.get("metadata_json") or order.get("metadata"))
    if bool(order.get("approved_resting_entry") or order.get("counts_as_managed_slot")):
        return True
    if bool(metadata.get("approved_resting_entry") or metadata.get("counts_as_managed_slot")):
        return True
    intent = " ".join(
        str(item or "").lower()
        for item in (
            order.get("intent"),
            order.get("order_purpose"),
            order.get("purpose"),
            order.get("action"),
            metadata.get("intent"),
            metadata.get("order_purpose"),
            metadata.get("slot_role"),
        )
    )
    return "entry" in intent and ("approved" in intent or "portfolio_manager" in intent or "slot" in intent)


def _source_actor(row: dict[str, Any]) -> SourceActor:
    raw = str(row.get("source_actor") or row.get("actor") or row.get("owner") or "").strip().lower()
    if raw in {"janus", "codex", "operator", "external"}:
        return raw  # type: ignore[return-value]
    metadata = _dict_value(row.get("metadata_json") or row.get("metadata"))
    raw = str(metadata.get("source_actor") or metadata.get("actor") or "").strip().lower()
    if raw in {"janus", "codex", "operator", "external"}:
        return raw  # type: ignore[return-value]
    return "unknown"


def _time_horizon(row: dict[str, Any]) -> TimeHorizon:
    raw = str(row.get("horizon") or row.get("time_horizon") or "").strip().lower()
    if raw in {"intraday", "short", "medium", "long"}:
        return raw  # type: ignore[return-value]
    days = _decimal_value(row.get("days_to_resolution") or row.get("days_to_expiry"))
    if days is None:
        return "unknown"
    if days <= 1:
        return "intraday"
    if days <= 30:
        return "short"
    if days <= 180:
        return "medium"
    return "long"


def _confidence(row: dict[str, Any]) -> Literal["low", "medium", "high", "unknown"]:
    raw = str(row.get("confidence") or "").strip().lower()
    if raw in {"low", "medium", "high"}:
        return raw  # type: ignore[return-value]
    return "unknown"


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _first_decimal(row: dict[str, Any], keys: tuple[str, ...], nested: dict[str, Any] | None = None) -> Decimal | None:
    for key in keys:
        if key in row:
            value = _decimal_value(row.get(key))
            if value is not None:
                return value
    if nested:
        for key in keys:
            if key in nested:
                value = _decimal_value(nested.get(key))
                if value is not None:
                    return value
    return None


def _int_value(value: Any) -> int | None:
    decimal = _decimal_value(value)
    if decimal is None:
        return None
    try:
        return max(0, int(decimal))
    except (ValueError, TypeError):
        return None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _strategy_style(row: dict[str, Any]) -> StrategyStyle:
    raw = str(row.get("strategy_style") or row.get("strategy") or row.get("trade_style") or "").strip().lower()
    normalized = raw.replace("-", "_").replace(" ", "_")
    if normalized in {
        "quick_trade",
        "grid_candidate",
        "trend_follow",
        "catalyst_option",
        "long_thesis",
        "target_maintenance",
        "unknown",
    }:
        return normalized  # type: ignore[return-value]
    text = " ".join(
        str(row.get(key) or "").lower()
        for key in ("source", "edge_summary", "thesis", "reason", "category", "market_title", "title")
    )
    if any(token in text for token in ("grid", "scalp", "oscillat", "band trade")):
        return "grid_candidate"
    if any(token in text for token in ("quick", "intraday", "short swing", "mean reversion")):
        return "quick_trade"
    if any(token in text for token in ("catalyst", "ipo", "launch", "court ruling", "earnings", "deadline")):
        return "catalyst_option"
    horizon = _time_horizon(row)
    if horizon == "long":
        return "long_thesis"
    if horizon in {"short", "medium"}:
        return "trend_follow"
    return "unknown"


def _expected_hold_days(row: dict[str, Any], strategy_style: StrategyStyle) -> int | None:
    for key in ("expected_hold_days", "hold_days", "target_hold_days", "expected_days_to_exit"):
        if key in row:
            value = _int_value(row.get(key))
            if value is not None:
                return value
    days_to_resolution = _int_value(row.get("days_to_resolution"))
    if strategy_style in {"quick_trade", "grid_candidate"}:
        return min(days_to_resolution, 14) if days_to_resolution is not None else 7
    if strategy_style == "catalyst_option":
        return min(days_to_resolution, 45) if days_to_resolution is not None else 30
    horizon_defaults = {"intraday": 1, "short": 14, "medium": 90, "long": 180}
    horizon = _time_horizon(row)
    if horizon in horizon_defaults:
        return min(days_to_resolution, horizon_defaults[horizon]) if days_to_resolution is not None else horizon_defaults[horizon]
    return days_to_resolution


def _expected_return_cents(row: dict[str, Any], proposed_price: Decimal | None = None) -> Decimal | None:
    explicit = _first_decimal(
        row,
        (
            "expected_return_cents",
            "expected_edge_cents",
            "target_edge_cents",
            "price_target_delta_cents",
            "expected_move_cents",
        ),
    )
    if explicit is not None:
        return explicit
    target_price = _decimal_value(row.get("target_price") or row.get("exit_price") or row.get("sell_target_price"))
    entry_price = proposed_price or _decimal_value(row.get("proposed_price") or row.get("price") or row.get("limit_price"))
    if target_price is not None and entry_price is not None:
        return abs(target_price - entry_price) * Decimal("100")
    return None


def _estimated_entry_slippage_cents(row: dict[str, Any], orderbook: dict[str, Any]) -> Decimal | None:
    explicit = _first_decimal(
        row,
        (
            "estimated_entry_slippage_cents",
            "entry_slippage_cents",
            "slippage_cents",
            "price_impact_cents",
            "estimated_price_impact_cents",
        ),
        nested=orderbook,
    )
    if explicit is not None:
        return explicit
    spread = _first_decimal(row, ("spread_cents", "spread"), nested=orderbook)
    if spread is not None:
        return spread / Decimal("2")
    return None


def _liquidity_capacity_usd(row: dict[str, Any], orderbook: dict[str, Any]) -> Decimal | None:
    return _first_decimal(
        row,
        (
            "liquidity_capacity_usd",
            "scalable_depth_usd",
            "depth_to_two_cents_usd",
            "depth_usd",
            "top_depth_usd",
        ),
        nested=orderbook,
    )


def _scale_price_impact_cents(row: dict[str, Any], orderbook: dict[str, Any]) -> Decimal | None:
    return _first_decimal(
        row,
        (
            "scale_price_impact_cents",
            "price_impact_1000_usd_cents",
            "estimated_scale_slippage_cents",
            "impact_at_1000_usd_cents",
        ),
        nested=orderbook,
    )


def _risk_return_analysis(row: dict[str, Any], proposed_notional: Decimal) -> dict[str, Any]:
    orderbook = _dict_value(row.get("direct_orderbook") or row.get("orderbook"))
    proposed_price = _decimal_value(row.get("proposed_price") or row.get("price") or row.get("limit_price"))
    strategy_style = _strategy_style(row)
    expected_hold_days = _expected_hold_days(row, strategy_style)
    expected_return_cents = _expected_return_cents(row, proposed_price=proposed_price)
    slippage_cents = _estimated_entry_slippage_cents(row, orderbook)
    liquidity_capacity = _liquidity_capacity_usd(row, orderbook)
    scale_impact_cents = _scale_price_impact_cents(row, orderbook)

    flags: list[str] = []
    if expected_return_cents is None:
        flags.append("expected_return_unmodeled")
    if slippage_cents is None:
        flags.append("slippage_unmodeled")
    if scale_impact_cents is None:
        flags.append("scale_price_impact_unmodeled")

    slippage_to_edge_ratio: Decimal | None = None
    if expected_return_cents is not None and expected_return_cents > 0 and slippage_cents is not None:
        slippage_to_edge_ratio = slippage_cents / expected_return_cents

    expected_return_on_notional_percent: Decimal | None = None
    if expected_return_cents is not None and proposed_price is not None and proposed_price > 0:
        expected_return_on_notional_percent = (expected_return_cents / Decimal("100")) / proposed_price * Decimal("100")

    payoff_velocity_score = 0
    if expected_return_on_notional_percent is not None and expected_hold_days:
        payoff_velocity_score = int(min(30, max(0, expected_return_on_notional_percent / Decimal(expected_hold_days) * 30)))

    scale_limited = False
    if liquidity_capacity is not None and liquidity_capacity < DEFAULT_SCALABLE_DEPTH_USD:
        scale_limited = True
    if scale_impact_cents is not None and expected_return_cents is not None and expected_return_cents > 0:
        if scale_impact_cents / expected_return_cents > DEFAULT_MAX_SLIPPAGE_TO_EDGE_RATIO:
            scale_limited = True
    elif scale_impact_cents is not None and scale_impact_cents >= Decimal("5"):
        scale_limited = True
    if proposed_notional > DEFAULT_PER_POSITION_CAP_USD:
        scale_limited = True
    if scale_limited:
        flags.append("scale_limited_quick_trade" if strategy_style in {"quick_trade", "grid_candidate"} else "scale_limited")

    quick_edge_too_small = False
    if strategy_style in {"quick_trade", "grid_candidate"} and expected_return_cents is not None:
        required_edge = DEFAULT_MIN_QUICK_TRADE_EDGE_CENTS
        if slippage_cents is not None:
            required_edge = max(required_edge, slippage_cents * Decimal("2"))
        quick_edge_too_small = expected_return_cents < required_edge

    if scale_limited and proposed_notional <= DEFAULT_PER_POSITION_CAP_USD:
        sizing_tier: SizingTier = "micro_only"
    elif (
        liquidity_capacity is not None
        and liquidity_capacity >= DEFAULT_SCALABLE_DEPTH_USD
        and (slippage_to_edge_ratio is None or slippage_to_edge_ratio <= DEFAULT_MAX_SLIPPAGE_TO_EDGE_RATIO)
    ):
        sizing_tier = "scalable_candidate"
    elif proposed_notional <= DEFAULT_PER_POSITION_CAP_USD:
        sizing_tier = "validation"
    else:
        sizing_tier = "scale_limited"

    sizing_guidance = {
        "max_initial_notional_usd": float(min(proposed_notional, DEFAULT_PER_POSITION_CAP_USD)),
        "max_slot_cap_usd": float(DEFAULT_PER_POSITION_CAP_USD),
        "target_average_slot_notional_usd": 2.5,
        "scale_probe_notional_usd": float(DEFAULT_SCALE_NOTIONAL_PROBE_USD),
        "current_notional_is_scale_proof": False,
        "next_scale_condition": (
            "require repeated fills plus direct depth/impact proof at the planned larger notional before increasing size"
        ),
    }

    return {
        "strategy_style": strategy_style,
        "expected_hold_days": expected_hold_days,
        "expected_return_cents": expected_return_cents,
        "expected_return_on_notional_percent": expected_return_on_notional_percent,
        "estimated_entry_slippage_cents": slippage_cents,
        "slippage_to_edge_ratio": slippage_to_edge_ratio,
        "liquidity_capacity_usd": liquidity_capacity,
        "scale_price_impact_cents": scale_impact_cents,
        "payoff_velocity_score": payoff_velocity_score,
        "sizing_tier": sizing_tier,
        "sizing_guidance": sizing_guidance,
        "risk_return_flags": sorted(set(flags)),
        "quick_edge_too_small": quick_edge_too_small,
    }


def _is_covered_basketball_row(row: dict[str, Any]) -> bool:
    raw = " ".join(
        str(value or "").lower()
        for value in (
            row.get("market_title"),
            row.get("title"),
            row.get("question"),
            row.get("market_slug"),
            row.get("slug"),
            row.get("event_slug"),
            row.get("category"),
            row.get("league"),
        )
    )
    covered_tokens = {
        " nba ",
        " wnba ",
        "national basketball association",
        "women's national basketball association",
        "oklahoma city thunder",
        "indiana fever",
        "new york liberty",
        "minnesota lynx",
        "nba finals",
        "wnba finals",
    }
    padded = f" {raw.replace('-', ' ')} "
    return any(token in padded for token in covered_tokens)


def _score_candidate_row(
    row: dict[str, Any],
    *,
    board: GlobalPortfolio20SlotBoard,
    existing_tags: set[str],
    index: int,
) -> GlobalPortfolioCandidate:
    title = _portfolio_title(row)
    market_slug = _portfolio_market_slug(row)
    token_id = _portfolio_token_id(row)
    proposed_notional = _decimal_value(
        row.get("proposed_notional_usd") or row.get("notional_usd") or row.get("target_notional_usd")
    )
    if proposed_notional is None:
        price = _decimal_value(row.get("proposed_price") or row.get("price") or row.get("limit_price"))
        size = _decimal_value(row.get("proposed_size") or row.get("size") or row.get("shares"))
        proposed_notional = price * size if price is not None and size is not None else Decimal("2.5")
    risk_return = _risk_return_analysis(row, proposed_notional)
    rejection_reasons: list[str] = []
    if _is_covered_basketball_row(row):
        rejection_reasons.append("covered_market_excluded")
    if not token_id and not bool(row.get("janus_catalog_mapped")):
        rejection_reasons.append("janus_catalog_token_mapping_missing")
    if _is_profile_only_candidate(row):
        rejection_reasons.append("profile_only_without_direct_edge")
    candidate_tags = set(_concentration_tags(row))
    if bool(row.get("concentration_conflict")) or bool(candidate_tags & existing_tags):
        rejection_reasons.append("concentration_conflict")
    if _is_stale_candidate(row):
        rejection_reasons.append("stale_signal")
    if _is_illiquid_or_wide(row):
        rejection_reasons.append("illiquid_or_wide_spread")
    slippage_to_edge = risk_return["slippage_to_edge_ratio"]
    if slippage_to_edge is not None and slippage_to_edge > DEFAULT_MAX_SLIPPAGE_TO_EDGE_RATIO:
        rejection_reasons.append("slippage_consumes_expected_edge")
    if risk_return["quick_edge_too_small"]:
        rejection_reasons.append("quick_trade_edge_too_small_after_slippage")
    if proposed_notional > DEFAULT_PER_POSITION_CAP_USD:
        rejection_reasons.append("position_cap_exceeded")
    if proposed_notional > _decimal_value(board.budget.codex_sleeve_remaining_usd):
        rejection_reasons.append("sleeve_budget_exceeded")

    score = 0
    if token_id:
        score += 20
    if bool(row.get("janus_catalog_mapped")):
        score += 15
    if _dict_value(row.get("direct_orderbook")) or _dict_value(row.get("orderbook")):
        score += 20
    if _dict_value(row.get("profile_signal")):
        score += 10
    if _dict_value(row.get("top_holder_signal")):
        score += 15
    if row.get("horizon") not in {None, "", "unknown"}:
        score += 5
    if not _is_illiquid_or_wide(row):
        score += 10
    if proposed_notional <= DEFAULT_PER_POSITION_CAP_USD and board.empty_slot_count > 0:
        score += 10
    if risk_return["expected_return_cents"] is not None:
        score += int(min(20, max(0, risk_return["expected_return_cents"] * 2)))
    score += int(risk_return["payoff_velocity_score"])
    if slippage_to_edge is not None:
        if slippage_to_edge <= Decimal("0.20"):
            score += 10
        elif slippage_to_edge <= DEFAULT_MAX_SLIPPAGE_TO_EDGE_RATIO:
            score += 5
    if risk_return["sizing_tier"] == "scalable_candidate":
        score += 15
    elif risk_return["sizing_tier"] == "micro_only":
        score -= 5
    if "expected_return_unmodeled" in risk_return["risk_return_flags"]:
        score -= 8
    if "slippage_unmodeled" in risk_return["risk_return_flags"]:
        score -= 8
    if "scale_price_impact_unmodeled" in risk_return["risk_return_flags"]:
        score -= 3
    if risk_return["strategy_style"] == "long_thesis" and risk_return["payoff_velocity_score"] == 0:
        score -= 5
    if rejection_reasons:
        score = max(0, score - 40 - (5 * len(rejection_reasons)))
    status: CandidateStatus
    if rejection_reasons:
        status = "rejected"
    elif board.empty_slot_count > 0:
        status = "ready_for_order_proof"
    else:
        status = "watch_only"

    return GlobalPortfolioCandidate(
        candidate_id=str(row.get("candidate_id") or _stable_candidate_id(row, index)),
        source=str(row.get("source") or "candidate_queue"),
        market_title=title,
        market_slug=market_slug,
        outcome=_portfolio_outcome(row),
        category=_portfolio_first_text(row, ("category", "domain")),
        token_id=token_id,
        proposed_side=str(row.get("proposed_side") or row.get("side") or "buy").lower()
        if str(row.get("proposed_side") or row.get("side") or "buy").lower() in {"buy", "sell", "unknown"}
        else "buy",
        proposed_price=_decimal_to_float(_decimal_value(row.get("proposed_price") or row.get("price") or row.get("limit_price"))),
        proposed_size=_decimal_to_float(_decimal_value(row.get("proposed_size") or row.get("size") or row.get("shares"))),
        proposed_notional_usd=_decimal_to_float(proposed_notional),
        horizon=_time_horizon(row),
        confidence=_confidence(row),
        strategy_style=risk_return["strategy_style"],
        expected_hold_days=risk_return["expected_hold_days"],
        expected_return_cents=_decimal_to_float(risk_return["expected_return_cents"]),
        expected_return_on_notional_percent=_decimal_to_float(risk_return["expected_return_on_notional_percent"]),
        estimated_entry_slippage_cents=_decimal_to_float(risk_return["estimated_entry_slippage_cents"]),
        slippage_to_edge_ratio=_decimal_to_float(risk_return["slippage_to_edge_ratio"]),
        liquidity_capacity_usd=_decimal_to_float(risk_return["liquidity_capacity_usd"]),
        payoff_velocity_score=risk_return["payoff_velocity_score"],
        sizing_tier=risk_return["sizing_tier"],
        sizing_guidance=risk_return["sizing_guidance"],
        risk_return_flags=risk_return["risk_return_flags"],
        score=score,
        status=status,
        rejection_reasons=rejection_reasons,
        edge_summary=_portfolio_first_text(row, ("edge_summary", "thesis", "reason", "rationale")),
        source_url=_portfolio_first_text(row, ("source_url", "url")),
        direct_orderbook=_dict_value(row.get("direct_orderbook") or row.get("orderbook")),
        profile_signal=_dict_value(row.get("profile_signal")),
        top_holder_signal=_dict_value(row.get("top_holder_signal")),
        candidate_json=dict(row),
    )


def _stable_candidate_id(row: dict[str, Any], index: int) -> str:
    return f"candidate-{_slugify(_portfolio_market_slug(row) or _portfolio_title(row))}-{index}"


def _concentration_tags(row: dict[str, Any]) -> list[str]:
    tags = row.get("concentration_tags")
    if isinstance(tags, list):
        return [str(item).strip().lower() for item in tags if str(item).strip()]
    category = _portfolio_first_text(row, ("category", "domain"))
    return [category.lower()] if category else []


def _is_profile_only_candidate(row: dict[str, Any]) -> bool:
    source = str(row.get("source") or "").lower()
    profile_signal = _dict_value(row.get("profile_signal"))
    has_direct_orderbook = bool(_dict_value(row.get("direct_orderbook") or row.get("orderbook")))
    has_recent_profile_trade = bool(profile_signal.get("recent_trade") or profile_signal.get("recent_trade_count"))
    return ("profile" in source or bool(profile_signal)) and not has_direct_orderbook and not has_recent_profile_trade


def _is_stale_candidate(row: dict[str, Any]) -> bool:
    if bool(row.get("stale_signal") or row.get("stale")):
        return True
    age = _decimal_value(row.get("signal_age_hours") or row.get("age_hours"))
    return age is not None and age > Decimal("48")


def _is_illiquid_or_wide(row: dict[str, Any]) -> bool:
    orderbook = _dict_value(row.get("direct_orderbook") or row.get("orderbook"))
    if orderbook.get("liquidity_ok") is False:
        return True
    spread = _decimal_value(
        row.get("spread_cents")
        or row.get("spread")
        or orderbook.get("spread_cents")
        or orderbook.get("spread")
    )
    depth = _decimal_value(row.get("depth_usd") or orderbook.get("depth_usd") or orderbook.get("depth"))
    if spread is not None and spread > DEFAULT_GRID_MAX_SPREAD_CENTS:
        return True
    return depth is not None and depth < DEFAULT_GRID_MIN_DEPTH_USD


def _profile_profit_usd(holder: dict[str, Any]) -> Decimal | None:
    for key in ("profit_usd", "profit_loss_usd", "profitLoss", "profit_loss", "pnl_usd", "pnl"):
        value = _decimal_value(holder.get(key))
        if value is not None:
            return value
    profile = holder.get("profile")
    if isinstance(profile, dict):
        return _profile_profit_usd(profile)
    return None


def _has_adapter_proof(snapshot: GlobalPortfolioExecutionGateSnapshot) -> bool:
    if snapshot.approved_execution_path not in {"janus_portfolio_order_management", "independent_polymarket_fallback"}:
        return False
    if not _has_text(snapshot.adapter_name):
        return False
    return True


def _has_market_token_order_state_proof(snapshot: GlobalPortfolioExecutionGateSnapshot) -> bool:
    return bool(
        _has_text(snapshot.market_title)
        and _has_text(snapshot.market_slug)
        and _has_text(snapshot.token_id)
    )


def _has_portfolio_ledger_proof(snapshot: GlobalPortfolioExecutionGateSnapshot) -> bool:
    if not _has_text(snapshot.idempotency_key):
        return False
    plan = snapshot.reconciliation_plan
    if isinstance(plan, dict):
        return any(_has_text(value) for value in plan.values())
    return _has_text(plan)


def _has_named_global_portfolio_risk_budget(snapshot: GlobalPortfolioExecutionGateSnapshot) -> bool:
    budget = dict(snapshot.risk_budget or {})
    budget_name = str(snapshot.risk_budget_name or budget.get("name") or "").strip()
    if not budget_name:
        return False
    scope = str(budget.get("scope") or budget.get("risk_bucket") or "").strip().lower()
    if scope and scope != "global-portfolio":
        return False
    limit = _numeric(budget.get("max_notional_usd") or budget.get("limit_notional_usd"))
    used = _numeric(budget.get("used_notional_usd") or 0.0)
    action_notional = _numeric(
        budget.get("action_notional_usd")
        or budget.get("proposed_notional_usd")
        or snapshot.minimum_order_proof.get("notional_usd")
    )
    if limit is None or limit <= 0.0:
        return False
    if used is None or used < 0.0:
        return False
    if action_notional is None or action_notional < 0.0:
        return False
    return action_notional <= max(0.0, limit - used) + 1e-9


def _has_minimum_order_proof(snapshot: GlobalPortfolioExecutionGateSnapshot) -> bool:
    proof = dict(snapshot.minimum_order_proof or {})
    side = str(proof.get("side") or "").strip().lower()
    order_type = str(proof.get("order_type") or "limit").strip().lower()
    price = _numeric(proof.get("price") or proof.get("limit_price"))
    size = _numeric(proof.get("size"))
    min_size = _numeric(proof.get("min_size") or 5.0)
    min_buy_notional = _numeric(proof.get("min_buy_notional_usd") or 1.0)
    notional = _numeric(proof.get("notional_usd"))
    if notional is None and price is not None and size is not None:
        notional = price * size
    if side not in {"buy", "sell"}:
        return False
    if order_type != "limit":
        return False
    if price is None or not 0.0 < price <= 1.0:
        return False
    if size is None or min_size is None or size < min_size:
        return False
    if side == "buy" and (notional is None or min_buy_notional is None or notional < min_buy_notional):
        return False
    return True


def _has_target_stop_rebuy_policy(snapshot: GlobalPortfolioExecutionGateSnapshot) -> bool:
    policy = dict(snapshot.target_stop_rebuy_policy_detail or {})
    required = ("policy_name", "target_policy", "stop_policy", "rebuy_policy", "reason")
    if not all(_has_text(policy.get(key)) for key in required):
        return False
    target_price = _numeric(policy.get("target_price") or policy.get("limit_price"))
    if snapshot.action in {"existing_position_target", "existing_position_replace"}:
        return target_price is not None and 0.0 < target_price <= 1.0
    return True


def _has_kill_switch_clearance(snapshot: GlobalPortfolioExecutionGateSnapshot) -> bool:
    clearance = dict(snapshot.kill_switch_clearance or {})
    blockers = [item for item in clearance.get("blocked_reasons", []) if str(item).strip()]
    return bool(clearance.get("clear") is True and _has_text(clearance.get("source")) and not blockers)


def _diagnostic_entry(
    *,
    passed: bool,
    required_fields: tuple[str, ...],
    missing_fields: list[str] | None = None,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "passed": passed,
        "required_fields": list(required_fields),
        "missing_fields": list(missing_fields or []),
        "blockers": list(blockers or []),
    }


def _build_execution_gate_diagnostics(
    snapshot: GlobalPortfolioExecutionGateSnapshot,
    *,
    missing_gates: list[ExecutionGateName] | None = None,
    rejected_truth_sources: list[str] | None = None,
) -> dict[str, Any]:
    missing = list(missing_gates if missing_gates is not None else snapshot.missing_gates)
    rejected = list(rejected_truth_sources if rejected_truth_sources is not None else snapshot.rejected_truth_sources)
    gates: dict[str, dict[str, Any]] = {}

    gates["direct_clob_truth_fresh"] = _diagnostic_entry(
        passed=bool(snapshot.direct_clob_truth_fresh),
        required_fields=("direct_clob_truth_fresh", "truth_sources"),
        missing_fields=[] if snapshot.direct_clob_truth_fresh else ["direct_clob_truth_fresh"],
    )

    market_missing: list[str] = []
    if not snapshot.market_token_order_state_resolved:
        market_missing.append("market_token_order_state_resolved")
    if not _has_text(snapshot.market_title):
        market_missing.append("market_title")
    if not _has_text(snapshot.market_slug):
        market_missing.append("market_slug")
    if not _has_text(snapshot.token_id):
        market_missing.append("token_id")
    gates["market_token_order_state_resolved"] = _diagnostic_entry(
        passed=bool(snapshot.market_token_order_state_resolved and _has_market_token_order_state_proof(snapshot)),
        required_fields=("market_title", "market_slug", "token_id", "market_token_order_state_resolved"),
        missing_fields=market_missing,
    )

    adapter_missing: list[str] = []
    if not snapshot.approved_order_management_path:
        adapter_missing.append("approved_order_management_path")
    if snapshot.approved_execution_path not in {"janus_portfolio_order_management", "independent_polymarket_fallback"}:
        adapter_missing.append("approved_execution_path")
    if not _has_text(snapshot.adapter_name):
        adapter_missing.append("adapter_name")
    gates["approved_order_management_path"] = _diagnostic_entry(
        passed=bool(snapshot.approved_order_management_path and _has_adapter_proof(snapshot)),
        required_fields=("approved_order_management_path", "approved_execution_path", "adapter_name"),
        missing_fields=adapter_missing,
    )

    ledger_missing: list[str] = []
    if not snapshot.portfolio_ledger_path:
        ledger_missing.append("portfolio_ledger_path")
    if not _has_text(snapshot.idempotency_key):
        ledger_missing.append("idempotency_key")
    plan = snapshot.reconciliation_plan
    plan_present = any(_has_text(value) for value in plan.values()) if isinstance(plan, dict) else _has_text(plan)
    if not plan_present:
        ledger_missing.append("reconciliation_plan")
    gates["portfolio_ledger_path"] = _diagnostic_entry(
        passed=bool(snapshot.portfolio_ledger_path and _has_portfolio_ledger_proof(snapshot)),
        required_fields=("portfolio_ledger_path", "idempotency_key", "reconciliation_plan"),
        missing_fields=ledger_missing,
    )

    budget = dict(snapshot.risk_budget or {})
    budget_missing: list[str] = []
    budget_blockers: list[str] = []
    budget_name = str(snapshot.risk_budget_name or budget.get("name") or "").strip()
    if not snapshot.separate_risk_budget:
        budget_missing.append("separate_risk_budget")
    if not budget_name:
        budget_missing.append("risk_budget_name")
    scope = str(budget.get("scope") or budget.get("risk_bucket") or "").strip().lower()
    if not scope:
        budget_missing.append("risk_budget.scope")
    elif scope != "global-portfolio":
        budget_blockers.append("risk_budget_scope_not_global_portfolio")
    limit = _numeric(budget.get("max_notional_usd") or budget.get("limit_notional_usd"))
    used = _numeric(budget.get("used_notional_usd") or 0.0)
    action_notional = _numeric(
        budget.get("action_notional_usd")
        or budget.get("proposed_notional_usd")
        or snapshot.minimum_order_proof.get("notional_usd")
    )
    if limit is None or limit <= 0.0:
        budget_missing.append("risk_budget.max_notional_usd")
    if used is None or used < 0.0:
        budget_blockers.append("risk_budget_used_notional_invalid")
    if action_notional is None or action_notional < 0.0:
        budget_missing.append("risk_budget.action_notional_usd")
    elif limit is not None and used is not None and action_notional > max(0.0, limit - used) + 1e-9:
        budget_blockers.append("risk_budget_action_exceeds_remaining_budget")
    gates["separate_risk_budget"] = _diagnostic_entry(
        passed=bool(snapshot.separate_risk_budget and _has_named_global_portfolio_risk_budget(snapshot)),
        required_fields=(
            "separate_risk_budget",
            "risk_budget_name",
            "risk_budget.scope",
            "risk_budget.max_notional_usd",
            "risk_budget.used_notional_usd",
            "risk_budget.action_notional_usd",
        ),
        missing_fields=budget_missing,
        blockers=budget_blockers,
    )

    proof = dict(snapshot.minimum_order_proof or {})
    minimum_missing: list[str] = []
    minimum_blockers: list[str] = []
    side = str(proof.get("side") or "").strip().lower()
    order_type = str(proof.get("order_type") or "limit").strip().lower()
    price = _numeric(proof.get("price") or proof.get("limit_price"))
    size = _numeric(proof.get("size"))
    min_size = _numeric(proof.get("min_size") or proof.get("exchange_min_size") or 5.0)
    min_buy_notional = _numeric(proof.get("min_buy_notional_usd") or 1.0)
    notional = _numeric(proof.get("notional_usd") or proof.get("notional"))
    if not snapshot.minimum_order_compliance:
        minimum_missing.append("minimum_order_compliance")
    if side not in {"buy", "sell"}:
        minimum_missing.append("minimum_order_proof.side")
    if order_type != "limit":
        minimum_blockers.append("minimum_order_proof_order_type_not_limit")
    if price is None:
        minimum_missing.append("minimum_order_proof.price")
    elif not 0.0 < price <= 1.0:
        minimum_blockers.append("minimum_order_proof_price_out_of_bounds")
    if size is None:
        minimum_missing.append("minimum_order_proof.size")
    elif min_size is not None and size < min_size:
        minimum_blockers.append("minimum_order_proof_size_below_exchange_minimum")
    if min_size is None:
        minimum_missing.append("minimum_order_proof.min_size")
    if side == "buy" and min_buy_notional is None:
        minimum_missing.append("minimum_order_proof.min_buy_notional_usd")
    if notional is None and price is not None and size is not None:
        notional = price * size
    if notional is None:
        minimum_missing.append("minimum_order_proof.notional_usd")
    elif side == "buy" and min_buy_notional is not None and notional < min_buy_notional:
        minimum_blockers.append("minimum_order_proof_buy_notional_below_minimum")
    gates["minimum_order_compliance"] = _diagnostic_entry(
        passed=bool(snapshot.minimum_order_compliance and _has_minimum_order_proof(snapshot)),
        required_fields=(
            "minimum_order_compliance",
            "minimum_order_proof.side",
            "minimum_order_proof.order_type",
            "minimum_order_proof.price",
            "minimum_order_proof.size",
            "minimum_order_proof.notional_usd",
            "minimum_order_proof.min_size",
            "minimum_order_proof.min_buy_notional_usd",
        ),
        missing_fields=minimum_missing,
        blockers=minimum_blockers,
    )

    policy = dict(snapshot.target_stop_rebuy_policy_detail or {})
    policy_missing: list[str] = []
    policy_blockers: list[str] = []
    if not snapshot.target_stop_rebuy_policy:
        policy_missing.append("target_stop_rebuy_policy")
    for key in ("policy_name", "target_policy", "stop_policy", "rebuy_policy", "reason"):
        if not _has_text(policy.get(key)):
            policy_missing.append(f"target_stop_rebuy_policy_detail.{key}")
    target_price = _numeric(policy.get("target_price") or policy.get("limit_price"))
    if snapshot.action in {"existing_position_target", "existing_position_replace"}:
        if target_price is None:
            policy_missing.append("target_stop_rebuy_policy_detail.target_price")
        elif not 0.0 < target_price <= 1.0:
            policy_blockers.append("target_stop_rebuy_policy_target_price_out_of_bounds")
    gates["target_stop_rebuy_policy"] = _diagnostic_entry(
        passed=bool(snapshot.target_stop_rebuy_policy and _has_target_stop_rebuy_policy(snapshot)),
        required_fields=(
            "target_stop_rebuy_policy",
            "target_stop_rebuy_policy_detail.policy_name",
            "target_stop_rebuy_policy_detail.target_policy",
            "target_stop_rebuy_policy_detail.target_price",
            "target_stop_rebuy_policy_detail.stop_policy",
            "target_stop_rebuy_policy_detail.rebuy_policy",
            "target_stop_rebuy_policy_detail.reason",
        ),
        missing_fields=policy_missing,
        blockers=policy_blockers,
    )

    clearance = dict(snapshot.kill_switch_clearance or {})
    kill_missing: list[str] = []
    kill_blockers = [str(item) for item in clearance.get("blocked_reasons", []) if str(item).strip()]
    if not snapshot.kill_switch_clear:
        kill_missing.append("kill_switch_clear")
    if clearance.get("clear") is not True:
        kill_missing.append("kill_switch_clearance.clear")
    if not _has_text(clearance.get("source")):
        kill_missing.append("kill_switch_clearance.source")
    gates["kill_switch_clear"] = _diagnostic_entry(
        passed=bool(snapshot.kill_switch_clear and _has_kill_switch_clearance(snapshot)),
        required_fields=("kill_switch_clear", "kill_switch_clearance.clear", "kill_switch_clearance.source"),
        missing_fields=kill_missing,
        blockers=kill_blockers,
    )

    truth_missing = [] if snapshot.non_runtime_truth_rejected else ["non_runtime_truth_rejected"]
    gates["non_runtime_truth_rejected"] = _diagnostic_entry(
        passed=bool(snapshot.non_runtime_truth_rejected and not rejected),
        required_fields=("non_runtime_truth_rejected", "truth_sources"),
        missing_fields=truth_missing,
        blockers=[f"rejected_truth_source:{source}" for source in rejected],
    )

    return {
        "schema_version": "global_portfolio_execution_gate_diagnostics_v1",
        "proof_bundle_complete": not missing,
        "missing_gates": missing,
        "next_missing_gate": missing[0] if missing else None,
        "gates": gates,
    }


def _parse_datetime(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


__all__ = [
    "EXECUTION_GATE_LABELS",
    "EXECUTION_GATE_ORDER",
    "GlobalPortfolio20SlotBoard",
    "GlobalPortfolioBudgetSnapshot",
    "GlobalPortfolioCandidate",
    "GlobalPortfolioCandidateQueue",
    "GlobalPortfolioDeepPassPlan",
    "GlobalPortfolioManagerActionPlan",
    "GlobalPortfolioGridEligibilityReview",
    "GlobalPortfolioManagedSlot",
    "GlobalPortfolioTopHolderScan",
    "GlobalPortfolioWatchlistArtifact",
    "GlobalPortfolioWatchlistEntry",
    "GlobalPortfolioExecutionGateSnapshot",
    "NO_EXECUTION_STATEMENT",
    "apply_watchlist_policy_flags",
    "build_20_slot_board",
    "build_deep_pass_plan",
    "build_execution_gate_diagnostics",
    "build_grid_eligibility_review",
    "build_manager_action_plan",
    "build_top_holder_scan",
    "build_watchlist_artifact",
    "build_execution_gate_snapshot",
    "build_watchlist_summary",
    "load_watchlist_source",
    "render_manager_action_plan",
    "render_execution_gate_report",
    "render_watchlist_report",
]
