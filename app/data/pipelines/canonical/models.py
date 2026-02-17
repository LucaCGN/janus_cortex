from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return dt.astimezone(timezone.utc)


def _normalize_token(value: str) -> str:
    return " ".join(value.strip().split()).lower()


class CanonicalProviderRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_code: str
    external_id: Optional[str] = None
    external_slug: Optional[str] = None
    external_url: Optional[str] = None
    fetched_at: Optional[datetime] = None
    raw_summary_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider_code")
    @classmethod
    def _provider_code_not_empty(cls, value: str) -> str:
        out = _normalize_token(value)
        if not out:
            raise ValueError("provider_code cannot be empty")
        return out

    @field_validator("fetched_at")
    @classmethod
    def _validate_fetched_at(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        return _ensure_aware(value)


class CanonicalOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_outcome_id: str
    label: str
    token_id: Optional[str] = None
    implied_prob: Optional[float] = None
    last_price: Optional[float] = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[CanonicalProviderRef] = Field(default_factory=list)

    @field_validator("canonical_outcome_id", "label")
    @classmethod
    def _required_text(cls, value: str) -> str:
        out = value.strip()
        if not out:
            raise ValueError("required text field cannot be empty")
        return out

    @field_validator("implied_prob", "last_price")
    @classmethod
    def _validate_price_bounds(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        if value < 0.0 or value > 1.0:
            raise ValueError("probability/price values must be between 0 and 1")
        return float(value)


class CanonicalMarket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_market_id: str
    canonical_event_id: str
    question: str
    market_kind: str = "binary"
    status: str = "open"
    open_time: Optional[datetime] = None
    close_time: Optional[datetime] = None
    outcomes: list[CanonicalOutcome] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[CanonicalProviderRef] = Field(default_factory=list)

    @field_validator("canonical_market_id", "canonical_event_id", "question")
    @classmethod
    def _required_text(cls, value: str) -> str:
        out = value.strip()
        if not out:
            raise ValueError("required text field cannot be empty")
        return out

    @field_validator("market_kind", "status")
    @classmethod
    def _normalize_enum_text(cls, value: str) -> str:
        out = _normalize_token(value)
        if not out:
            raise ValueError("field cannot be empty")
        return out

    @field_validator("open_time", "close_time")
    @classmethod
    def _validate_datetimes(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        return _ensure_aware(value)

    @model_validator(mode="after")
    def _validate_market_integrity(self) -> "CanonicalMarket":
        if len(self.outcomes) < 2:
            raise ValueError("canonical market must contain at least two outcomes")

        outcome_ids = [x.canonical_outcome_id for x in self.outcomes]
        if len(outcome_ids) != len(set(outcome_ids)):
            raise ValueError("duplicate canonical_outcome_id detected in market")

        if self.open_time and self.close_time and self.close_time < self.open_time:
            raise ValueError("close_time cannot be earlier than open_time")

        return self


class CanonicalEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_event_id: str
    canonical_slug: str
    title: str
    domain: str = "sports"
    event_kind: str = "sports_game"
    status: str = "open"
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    resolution_time: Optional[datetime] = None
    home_entity: Optional[str] = None
    away_entity: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    information_profile_code: Optional[str] = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[CanonicalProviderRef] = Field(default_factory=list)
    markets: list[CanonicalMarket] = Field(default_factory=list)

    @field_validator("canonical_event_id", "canonical_slug", "title")
    @classmethod
    def _required_text(cls, value: str) -> str:
        out = value.strip()
        if not out:
            raise ValueError("required text field cannot be empty")
        return out

    @field_validator("domain", "event_kind", "status")
    @classmethod
    def _normalize_enum_text(cls, value: str) -> str:
        out = _normalize_token(value)
        if not out:
            raise ValueError("field cannot be empty")
        return out

    @field_validator("canonical_slug")
    @classmethod
    def _normalize_slug(cls, value: str) -> str:
        slug = _normalize_token(value).replace(" ", "-")
        if not slug:
            raise ValueError("canonical_slug cannot be empty")
        return slug

    @field_validator("start_time", "end_time", "resolution_time")
    @classmethod
    def _validate_datetimes(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        return _ensure_aware(value)

    @field_validator("tags")
    @classmethod
    def _normalize_tags(cls, values: list[str]) -> list[str]:
        out = [_normalize_token(x) for x in values if str(x).strip()]
        return sorted(set(out))

    @model_validator(mode="after")
    def _validate_event_integrity(self) -> "CanonicalEvent":
        market_ids = [x.canonical_market_id for x in self.markets]
        if len(market_ids) != len(set(market_ids)):
            raise ValueError("duplicate canonical_market_id detected in event")

        if self.start_time and self.end_time and self.end_time < self.start_time:
            raise ValueError("end_time cannot be earlier than start_time")

        if self.resolution_time and self.start_time and self.resolution_time < self.start_time:
            raise ValueError("resolution_time cannot be earlier than start_time")

        return self


class CanonicalBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    events: list[CanonicalEvent] = Field(default_factory=list)

    @field_validator("generated_at")
    @classmethod
    def _validate_generated_at(cls, value: datetime) -> datetime:
        return _ensure_aware(value)

    @model_validator(mode="after")
    def _validate_bundle(self) -> "CanonicalBundle":
        event_ids = [x.canonical_event_id for x in self.events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("duplicate canonical_event_id detected in bundle")
        return self
