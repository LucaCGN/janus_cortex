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


class NbaGameLiveSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_live_snapshots: bool = True
    include_play_by_play: bool = True


class NbaSeasonSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season: str = "2025-26"


class NbaSeasonRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season: str = "2025-26"
    refresh_metadata: bool = True
    game_ids: list[str] = Field(default_factory=list)
    max_games: int | None = Field(default=None, ge=1, le=2000)
    only_finished: bool = False
    include_odds_fetch: bool = True
    build_rollups: bool = True
    moneyline_window_days: int = Field(default=14, ge=1, le=30)
    moneyline_page_size: int = Field(default=100, ge=1, le=500)
    moneyline_max_pages: int = Field(default=30, ge=1, le=200)


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


class OutcomeTicksQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_time: datetime | None = None
    end_time: datetime | None = None
    source: str | None = None
    limit: int = Field(default=500, ge=1, le=5000)


class OutcomeCandlesQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeframe: str = Field(default="1m", min_length=1)
    start_time: datetime | None = None
    end_time: datetime | None = None
    source: str | None = None
    limit: int = Field(default=500, ge=1, le=5000)


class OrderbookHistoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_time: datetime | None = None
    end_time: datetime | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    include_levels: bool = True
    levels_per_side: int = Field(default=10, ge=1, le=100)


class TradingAccountCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID | None = None
    provider_id: UUID | None = None
    provider_code: str = Field(default="polymarket_data_api", min_length=1)
    account_label: str = Field(min_length=1)
    wallet_address: str | None = None
    proxy_wallet_address: str | None = None
    chain_id: int | None = Field(default=None, ge=1)
    is_active: bool = True


class PortfolioSummaryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID | None = None
    limit: int = Field(default=200, ge=1, le=1000)


class PortfolioPositionsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID | None = None
    outcome_id: UUID | None = None
    latest_only: bool = True
    source: str | None = None
    limit: int = Field(default=500, ge=1, le=5000)
    offset: int = Field(default=0, ge=0)


class PortfolioPositionHistoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID | None = None
    outcome_id: UUID | None = None
    source: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    limit: int = Field(default=1000, ge=1, le=10000)
    offset: int = Field(default=0, ge=0)


class PortfolioOrdersQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID | None = None
    market_id: UUID | None = None
    outcome_id: UUID | None = None
    status: str | None = None
    side: str | None = None
    limit: int = Field(default=500, ge=1, le=5000)
    offset: int = Field(default=0, ge=0)


class PortfolioTradesQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID | None = None
    market_id: UUID | None = None
    outcome_id: UUID | None = None
    order_id: UUID | None = None
    side: str | None = None
    limit: int = Field(default=500, ge=1, le=5000)
    offset: int = Field(default=0, ge=0)


class ManualOrderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID
    market_id: UUID
    outcome_id: UUID | None = None
    side: Literal["buy", "sell"]
    order_type: Literal["limit", "market"] = "limit"
    time_in_force: str | None = None
    limit_price: float | None = Field(default=None, ge=0.0, le=1.0)
    size: float | None = Field(default=None, gt=0.0)
    metadata_json: dict[str, Any] | list[Any] | None = None
    dry_run: bool = True


class ManualOrderCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID | None = None
    reason: str | None = None
    dry_run: bool = True


class ManualOrderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: UUID
    status: str
    event_type: str
    external_order_id: str | None = None
    dry_run: bool
    summary: dict[str, Any] = Field(default_factory=dict)


class PolymarketPortfolioSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wallet_address: str | None = None
    limit: int = Field(default=250, ge=1, le=2000)
    payload_override: dict[str, list[dict[str, Any]]] | None = None


class PolymarketClosedPositionConsolidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wallet_address: str | None = None
    limit: int = Field(default=250, ge=1, le=2000)
    run_portfolio_sync: bool = True
    stale_sample_limit: int = Field(default=20, ge=0, le=200)


class PolymarketOrderbookSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome_id: UUID | None = None
    token_id: str | None = None
    sample_count: int = Field(default=2, ge=1, le=100)
    sample_interval_sec: float = Field(default=0.5, ge=0.0, le=30.0)
    max_levels_per_side: int = Field(default=10, ge=1, le=100)
    dry_run: bool = False


class PolymarketPricesSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome_id: UUID
    lookback_hours: int = Field(default=24, ge=1, le=24 * 30)
    interval: str = Field(default="1m", min_length=1)
    fidelity: int = Field(default=10, ge=1, le=60)
    allow_snapshot_fallback: bool = True
    dry_run: bool = False


class NbaGameEventLinkCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    linked_by: str | None = None
