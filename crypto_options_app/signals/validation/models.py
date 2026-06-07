from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


SignalPurpose = Literal[
    "strategy_parameter",
    "logic_gate_trigger",
    "refreshing_stat",
    "price_reference",
]

ValidationPhase = Literal[
    "last_week_backtest",
    "last_month_backtest",
    "random_sampling_backtest",
    "live_shadow_test",
]

ValidationStatus = Literal["not_started", "running", "passed", "failed", "blocked"]

QueueState = Literal["QUEUED", "OWNED", "RUNNING", "PASSED", "FAILED", "BLOCKED", "RETIRED", "PROMOTED"]


def build_signal_id(*, family: str, signal_type: str, sources: tuple[str, ...], variant: str, version: str) -> str:
    source_slug = "_".join(_normalize_part(source) for source in sources)
    return "_".join(
        part
        for part in (
            _normalize_part(family),
            _normalize_part(signal_type),
            source_slug,
            _normalize_part(variant),
            _normalize_part(version),
        )
        if part
    )


class SignalCandidateSpec(BaseModel):
    """Read-only specification for one independently testable signal hypothesis."""

    signal_id: str
    family: str = "master_hedge_grid_scalping"
    signal_type: str
    sources: tuple[str, ...]
    variant: str
    version: str = "v1"
    filename: str
    purpose: SignalPurpose
    event_phase_relevance: Literal["none", "pre", "live", "both"]
    refresh_rate_seconds: int | None = Field(default=None, ge=1)
    time_frames_relevant: tuple[str, ...] = ()
    required_data_blocks: tuple[Literal["A", "B", "C"], ...] = ()
    validation_target: str
    win_criteria: str
    sample_unit: str
    impact_if_degraded: Literal["low", "medium", "high", "critical"]
    description: str
    signal_payload_example: dict[str, Any] = Field(default_factory=dict)
    parent_signal_id: str | None = None
    supersedes_signal_id: str | None = None
    blockers: tuple[str, ...] = ()

    @property
    def expected_signal_id(self) -> str:
        return build_signal_id(
            family=self.family,
            signal_type=self.signal_type,
            sources=self.sources,
            variant=self.variant,
            version=self.version,
        )

    @property
    def expected_filename(self) -> str:
        return f"{self.expected_signal_id}.py"

    def structural_blockers(self) -> tuple[str, ...]:
        blockers = list(self.blockers)
        if self.signal_id != self.expected_signal_id:
            blockers.append("signal_id_does_not_match_family_type_sources_variant_version")
        if self.filename != self.expected_filename:
            blockers.append("filename_does_not_match_signal_id")
        if not self.required_data_blocks:
            blockers.append("missing_required_data_blocks")
        return tuple(dict.fromkeys(blockers))


class SignalValidationPhaseResult(BaseModel):
    signal_id: str
    phase: ValidationPhase
    status: ValidationStatus
    evaluated_at_utc: str | None = None
    sample_count: int = 0
    hit_rate: float | None = None
    average_forward_return: float | None = None
    blockers: tuple[str, ...] = ()
    metrics: dict[str, Any] = Field(default_factory=dict)


class SignalValidationFrame(BaseModel):
    """Replay-safe frame consumed by read-only signal validators."""

    frame_key: str
    event_key: str | None = None
    event_token_key: str | None = None
    decision_at_utc: str
    source_observed_at_utc: str | None = None
    data_blocks: dict[str, Any] = Field(default_factory=dict)
    frame_json: dict[str, Any] = Field(default_factory=dict)


class SignalObservation(BaseModel):
    signal_id: str
    version: str
    phase: ValidationPhase
    event_key: str | None = None
    event_token_key: str | None = None
    decision_at_utc: str
    emitted_signal: bool = False
    observed_value: float | None = None
    expected_direction: str | None = None
    outcome_direction: str | None = None
    hit: bool | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    blockers: tuple[str, ...] = ()


class SignalQueueItem(BaseModel):
    queue_item_key: str
    signal_id: str
    version: str
    phase: ValidationPhase
    status: QueueState
    priority: int = 100
    owner_id: str | None = None
    owned_at_utc: str | None = None
    owner_expires_at_utc: str | None = None
    attempt_count: int = 0
    parent_signal_id: str | None = None
    supersedes_signal_id: str | None = None
    last_error: str | None = None


class SignalValidationRunResult(BaseModel):
    validation_run_key: str | None = None
    queue_item: SignalQueueItem | None = None
    phase_result: SignalValidationPhaseResult | None = None
    observations: tuple[SignalObservation, ...] = ()
    status: ValidationStatus = "not_started"
    blockers: tuple[str, ...] = ()
    orders_allowed: bool = False
    live_trading_authorized: bool = False


class SignalValidationSummary(BaseModel):
    signal_id: str
    current_status: ValidationStatus
    phase_results: tuple[SignalValidationPhaseResult, ...] = ()
    promoted_for_strategy_design: bool = False
    blockers: tuple[str, ...] = ()


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _normalize_part(value: str) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")
