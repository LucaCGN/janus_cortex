from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProviderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: UUID | None = None
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    base_url: str | None = None
    auth_type: str | None = None
    is_active: bool = True


class ModuleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module_id: UUID | None = None
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    owner: str | None = None
    is_active: bool = True


class EventTypeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type_id: UUID | None = None
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    description: str | None = None
    default_horizon: str | None = None
    resolution_policy: str | None = None


class InformationProfileCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    information_profile_id: UUID | None = None
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    min_sources: int = Field(default=1, ge=1)
    required_fields_json: list[str] | dict[str, Any] | None = None
    refresh_interval_sec: int | None = Field(default=None, ge=1)


class EventCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID | None = None
    event_type_id: UUID | None = None
    event_type_code: str | None = None
    information_profile_id: UUID | None = None
    information_profile_code: str | None = None
    title: str = Field(min_length=1)
    status: str = Field(min_length=1)
    canonical_slug: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    resolution_time: datetime | None = None
    metadata_json: dict[str, Any] | list[Any] | None = None

    @model_validator(mode="after")
    def _validate_ids(self) -> "EventCreateRequest":
        if self.event_type_id is None and not self.event_type_code:
            raise ValueError("event_type_id or event_type_code is required")
        return self


class EventPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type_id: UUID | None = None
    event_type_code: str | None = None
    information_profile_id: UUID | None = None
    information_profile_code: str | None = None
    title: str | None = None
    status: str | None = None
    canonical_slug: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    resolution_time: datetime | None = None
    metadata_json: dict[str, Any] | list[Any] | None = None


class EventImportUrlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1)
    history_mode: Literal["game_period", "rolling_recent", "interval_only"] | None = None
    history_market_selector: Literal["moneyline", "primary", "all"] | None = None
    history_interval: str = "1m"
    history_fidelity: int = Field(default=10, ge=1)
    recent_lookback_days: int = Field(default=7, ge=1)
    allow_snapshot_fallback: bool = True
    stream_enabled: bool = False
    stream_sample_count: int = Field(default=3, ge=0)
    stream_sample_interval_sec: float = Field(default=1.0, ge=0.0)
    stream_max_outcomes: int = Field(default=30, ge=1)


class PolymarketSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    probe_set: Literal["today_nba", "extras", "combined"] = "today_nba"
    max_finished: int = Field(default=2, ge=0)
    max_live: int = Field(default=2, ge=0)
    max_upcoming: int = Field(default=2, ge=0)
    include_upcoming: bool = False
    stream_sample_count: int = Field(default=3, ge=0)
    stream_sample_interval_sec: float = Field(default=1.0, ge=0.0)
    stream_max_outcomes: int = Field(default=30, ge=1)
    missing_only: bool = False
    steps: list[str] = Field(default_factory=list)


class NbaScheduleSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season: str = "2025-26"
    schedule_window_days: int = Field(default=2, ge=0)
    include_live_snapshots: bool = True
    include_play_by_play: bool = True


class NbaSeasonSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season: str = "2025-26"


class MappingSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lookback_days: int = Field(default=3, ge=0)
    lookahead_days: int = Field(default=2, ge=0)


class SyncTriggerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_run_id: UUID | None = None
    sync_run_id: UUID | None = None
    status: str
    rows_read: int | None = None
    rows_written: int | None = None
    summary: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    database: str
    timestamp: datetime
    services: list[dict[str, Any]]
