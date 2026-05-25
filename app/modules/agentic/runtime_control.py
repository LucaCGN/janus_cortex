from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agentic.signal_aggregation import LiveSignalAggregationControl
from app.modules.agentic.store import append_jsonl, artifacts_root, read_json, session_date, write_json


EventControlActor = Literal["janus", "llm", "codex", "operator", "system"]


class EventControlParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_cap_usd: float | None = Field(default=None, ge=0.0)
    max_signal_age_seconds: float | None = Field(default=None, ge=0.0)
    cooldown_seconds: float | None = Field(default=None, ge=0.0)
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    max_grid_leg_shares: float | None = Field(default=None, ge=0.0)
    core_hold_shares: float | None = Field(default=None, ge=0.0)
    buy_drop_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    sell_profit_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    support_band_count: int | None = Field(default=None, ge=0)
    rebuy_review_required: bool | None = None
    allow_inventory_adding: bool | None = None


class EventControlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "event_control_config_v1"
    event_id: str = Field(min_length=1)
    session_date: str = Field(min_length=1)
    enabled: bool = True
    signal_source_toggles: dict[str, bool] = Field(default_factory=dict)
    parameters: EventControlParameters = Field(default_factory=EventControlParameters)
    updated_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_by: EventControlActor = "system"
    source: str = "system_default"
    reason: str = "default_event_control_config"
    evidence_paths: list[str] = Field(default_factory=list)


class EventControlUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: EventControlActor
    reason: str = Field(min_length=1)
    source: str = Field(default="runtime_control_api", min_length=1)
    evidence_paths: list[str] = Field(default_factory=list)
    enabled: bool | None = None
    signal_source_toggles: dict[str, bool] | None = None
    parameters: EventControlParameters | None = None


class EventControlValidationError(ValueError):
    def __init__(self, reason_code: str, detail: dict[str, object] | None = None) -> None:
        self.reason_code = reason_code
        self.detail = detail or {}
        super().__init__(reason_code)


def event_control_root(day: str | None = None, *, root: Path | None = None) -> Path:
    base_root = root if root is not None else artifacts_root()
    return base_root / "event-controls" / session_date(day)


def default_event_control_config(event_id: str, *, day: str | None = None) -> EventControlConfig:
    return EventControlConfig(event_id=event_id, session_date=session_date(day))


def load_event_control_config(
    event_id: str,
    *,
    day: str | None = None,
    root: Path | None = None,
) -> EventControlConfig:
    resolved_day = session_date(day)
    path = event_control_root(resolved_day, root=root) / _safe_name(event_id) / "current.json"
    payload = read_json(path)
    if payload is None:
        return default_event_control_config(event_id, day=resolved_day)
    return EventControlConfig.model_validate(payload)


def update_event_control_config(
    event_id: str,
    update: EventControlUpdateRequest,
    *,
    day: str | None = None,
    root: Path | None = None,
) -> dict[str, object]:
    existing = load_event_control_config(event_id, day=day, root=root)
    merged_parameters = existing.parameters.model_copy(
        update=(update.parameters.model_dump(exclude_none=True) if update.parameters is not None else {})
    )
    merged_toggles = dict(existing.signal_source_toggles)
    if update.signal_source_toggles is not None:
        merged_toggles.update(_validated_signal_toggles(update.signal_source_toggles))
    _validate_parameters(merged_parameters)

    config = EventControlConfig(
        event_id=event_id,
        session_date=existing.session_date,
        enabled=existing.enabled if update.enabled is None else update.enabled,
        signal_source_toggles=merged_toggles,
        parameters=merged_parameters,
        updated_at_utc=datetime.now(timezone.utc),
        updated_by=update.actor,
        source=update.source,
        reason=update.reason,
        evidence_paths=_unique_strings(update.evidence_paths),
    )

    root_path = event_control_root(config.session_date, root=root) / _safe_name(event_id)
    current_path = root_path / "current.json"
    write_json(current_path, config.model_dump(mode="json"))
    append_jsonl(
        event_control_root(config.session_date, root=root) / "event_control_updates.jsonl",
        {
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "event_id": event_id,
            "session_date": config.session_date,
            "actor": update.actor,
            "source": update.source,
            "reason": update.reason,
            "enabled": config.enabled,
            "signal_source_toggles": config.signal_source_toggles,
            "parameters": config.parameters.model_dump(mode="json", exclude_none=True),
            "path": str(current_path),
        },
    )
    return {
        "status": "stored",
        "schema_version": "event_control_update_result_v1",
        "event_id": event_id,
        "session_date": config.session_date,
        "path": str(current_path),
        "config": config.model_dump(mode="json"),
        "aggregation_control": event_control_to_aggregation_control(config).model_dump(mode="json"),
    }


def event_control_to_aggregation_control(config: EventControlConfig) -> LiveSignalAggregationControl:
    enabled_sources = [
        source for source, enabled in sorted(config.signal_source_toggles.items()) if bool(enabled)
    ]
    params = config.parameters
    if not config.enabled:
        enabled_sources = ["__event_control_disabled__"]
    return LiveSignalAggregationControl(
        enabled_signal_sources=enabled_sources,
        cooldown_seconds=params.cooldown_seconds if params.cooldown_seconds is not None else 90.0,
        max_signal_age_seconds=params.max_signal_age_seconds if params.max_signal_age_seconds is not None else 300.0,
        min_confidence=params.min_confidence,
        event_cap_usd=params.event_cap_usd,
        allow_inventory_adding=bool(params.allow_inventory_adding),
    )


def _validate_parameters(parameters: EventControlParameters) -> None:
    if parameters.event_cap_usd is not None and parameters.event_cap_usd > 10.0:
        raise EventControlValidationError(
            "event_cap_above_current_live_learning_limit",
            {"event_cap_usd": parameters.event_cap_usd, "maximum_allowed_usd": 10.0},
        )
    if parameters.max_grid_leg_shares is not None and parameters.max_grid_leg_shares > 5.0:
        raise EventControlValidationError(
            "grid_leg_above_current_min_size_learning_limit",
            {"max_grid_leg_shares": parameters.max_grid_leg_shares, "maximum_allowed_shares": 5.0},
        )
    if parameters.core_hold_shares is not None and parameters.core_hold_shares > 5.0:
        raise EventControlValidationError(
            "core_hold_above_current_min_size_learning_limit",
            {"core_hold_shares": parameters.core_hold_shares, "maximum_allowed_shares": 5.0},
        )


def _validated_signal_toggles(values: dict[str, bool]) -> dict[str, bool]:
    normalized: dict[str, bool] = {}
    for key, value in values.items():
        source = str(key or "").strip()
        if not source:
            raise EventControlValidationError("signal_source_required")
        normalized[source] = bool(value)
    return normalized


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value))[:160] or "unknown"


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


__all__ = [
    "EventControlActor",
    "EventControlConfig",
    "EventControlParameters",
    "EventControlUpdateRequest",
    "EventControlValidationError",
    "default_event_control_config",
    "event_control_root",
    "event_control_to_aggregation_control",
    "load_event_control_config",
    "update_event_control_config",
]
