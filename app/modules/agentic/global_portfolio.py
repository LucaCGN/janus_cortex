from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
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


NO_EXECUTION_STATEMENT = "No execution is authorized by this artifact."
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

    @model_validator(mode="after")
    def _summarize_execution_gates(self) -> "GlobalPortfolioExecutionGateSnapshot":
        rejected_sources = _rejected_truth_sources(self.truth_sources)
        missing: list[ExecutionGateName] = []
        for gate in EXECUTION_GATE_ORDER:
            gate_value = bool(getattr(self, gate))
            if gate == "approved_order_management_path":
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
        return self


def build_execution_gate_snapshot(**kwargs: Any) -> GlobalPortfolioExecutionGateSnapshot:
    return GlobalPortfolioExecutionGateSnapshot.model_validate(kwargs)


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


def _has_adapter_proof(snapshot: GlobalPortfolioExecutionGateSnapshot) -> bool:
    if snapshot.approved_execution_path not in {"janus_portfolio_order_management", "independent_polymarket_fallback"}:
        return False
    if not _has_text(snapshot.adapter_name):
        return False
    return True


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
    "GlobalPortfolioManagerActionPlan",
    "GlobalPortfolioWatchlistArtifact",
    "GlobalPortfolioWatchlistEntry",
    "GlobalPortfolioExecutionGateSnapshot",
    "NO_EXECUTION_STATEMENT",
    "apply_watchlist_policy_flags",
    "build_manager_action_plan",
    "build_watchlist_artifact",
    "build_execution_gate_snapshot",
    "build_watchlist_summary",
    "load_watchlist_source",
    "render_manager_action_plan",
    "render_execution_gate_report",
    "render_watchlist_report",
]
