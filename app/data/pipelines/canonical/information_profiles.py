from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.data.pipelines.canonical.models import CanonicalEvent


class InformationProfileDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    name: str
    min_sources: int = 1
    min_markets: int = 1
    min_outcomes_per_market: int = 2
    required_event_fields: list[str] = Field(default_factory=list)
    required_market_fields: list[str] = Field(default_factory=list)
    required_outcome_fields: list[str] = Field(default_factory=list)
    max_source_age_minutes: Optional[int] = 180


class EventInformationScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_event_id: str
    information_profile_code: str
    coverage_score: float
    quality_score: float
    latency_score: float
    total_score: float
    is_trade_eligible: bool
    missing_fields: list[str] = Field(default_factory=list)
    metrics_json: dict[str, float | int | str] = Field(default_factory=dict)


def get_default_information_profiles() -> dict[str, InformationProfileDefinition]:
    return {
        "nba_game_core": InformationProfileDefinition(
            code="nba_game_core",
            name="NBA Game Core",
            min_sources=1,
            min_markets=1,
            min_outcomes_per_market=2,
            required_event_fields=["title", "canonical_slug", "start_time", "home_entity", "away_entity"],
            required_market_fields=["question", "market_kind", "status"],
            required_outcome_fields=["label", "last_price"],
            max_source_age_minutes=180,
        ),
        "nba_award_core": InformationProfileDefinition(
            code="nba_award_core",
            name="NBA Award Core",
            min_sources=1,
            min_markets=0,
            min_outcomes_per_market=2,
            required_event_fields=["title", "canonical_slug"],
            required_market_fields=["question", "market_kind"],
            required_outcome_fields=["label"],
            max_source_age_minutes=720,
        ),
        "generic_event_core": InformationProfileDefinition(
            code="generic_event_core",
            name="Generic Event Core",
            min_sources=1,
            min_markets=0,
            min_outcomes_per_market=2,
            required_event_fields=["title", "canonical_slug"],
            required_market_fields=[],
            required_outcome_fields=["label"],
            max_source_age_minutes=None,
        ),
    }


def select_profile_for_event(
    event: CanonicalEvent,
    profiles: Optional[dict[str, InformationProfileDefinition]] = None,
) -> InformationProfileDefinition:
    profiles = profiles or get_default_information_profiles()
    if event.information_profile_code and event.information_profile_code in profiles:
        return profiles[event.information_profile_code]

    kind = event.event_kind.lower()
    slug = event.canonical_slug.lower()
    tags = {x.lower() for x in event.tags}
    if kind == "sports_game" and ("nba" in tags or slug.startswith("nba-")):
        return profiles["nba_game_core"]
    if kind == "award":
        return profiles["nba_award_core"]
    return profiles["generic_event_core"]


def _field_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, list) and not value:
        return True
    return False


def _missing_required_fields(event: CanonicalEvent, profile: InformationProfileDefinition) -> list[str]:
    missing: list[str] = []
    for field_name in profile.required_event_fields:
        if _field_missing(getattr(event, field_name, None)):
            missing.append(f"event.{field_name}")

    for i, market in enumerate(event.markets):
        for field_name in profile.required_market_fields:
            if _field_missing(getattr(market, field_name, None)):
                missing.append(f"market[{i}].{field_name}")
        for j, outcome in enumerate(market.outcomes):
            for field_name in profile.required_outcome_fields:
                if _field_missing(getattr(outcome, field_name, None)):
                    missing.append(f"market[{i}].outcome[{j}].{field_name}")
    return sorted(set(missing))


def _coverage_score(event: CanonicalEvent, profile: InformationProfileDefinition) -> tuple[float, list[str]]:
    missing = _missing_required_fields(event=event, profile=profile)
    total_required = (
        len(profile.required_event_fields)
        + len(profile.required_market_fields) * max(len(event.markets), 1)
        + len(profile.required_outcome_fields) * max(sum(len(m.outcomes) for m in event.markets), 1)
    )
    if total_required <= 0:
        return 100.0, []
    satisfied = max(0, total_required - len(missing))
    return (satisfied / total_required) * 100.0, missing


def _quality_score(event: CanonicalEvent, profile: InformationProfileDefinition) -> tuple[float, dict[str, float | int | str]]:
    source_count = len(event.source_refs)
    unique_sources = len({s.provider_code for s in event.source_refs})
    market_count = len(event.markets)
    outcomes_per_market = [len(m.outcomes) for m in event.markets]

    source_score = min(100.0, (source_count / max(profile.min_sources, 1)) * 100.0)
    if profile.min_markets <= 0:
        market_score = 100.0
    else:
        market_score = min(100.0, (market_count / profile.min_markets) * 100.0)

    if not outcomes_per_market:
        outcome_score = 100.0 if profile.min_markets <= 0 else 0.0
    else:
        markets_with_min = [x for x in outcomes_per_market if x >= profile.min_outcomes_per_market]
        outcome_score = (len(markets_with_min) / len(outcomes_per_market)) * 100.0

    value_fields = 0
    valued_outcomes = 0
    for market in event.markets:
        for outcome in market.outcomes:
            valued_outcomes += 1
            if outcome.last_price is not None or outcome.implied_prob is not None:
                value_fields += 1
    price_score = 100.0 if valued_outcomes == 0 else (value_fields / valued_outcomes) * 100.0

    quality = (source_score + market_score + outcome_score + price_score) / 4.0
    metrics = {
        "source_count": source_count,
        "unique_sources": unique_sources,
        "market_count": market_count,
        "outcome_value_coverage": round(price_score, 4),
    }
    return quality, metrics


def _latency_score(
    event: CanonicalEvent,
    profile: InformationProfileDefinition,
    now: datetime,
) -> tuple[float, dict[str, float | int | str]]:
    fetched_points: list[datetime] = []
    for source in event.source_refs:
        if source.fetched_at:
            fetched_points.append(source.fetched_at)
    for market in event.markets:
        for source in market.source_refs:
            if source.fetched_at:
                fetched_points.append(source.fetched_at)
        for outcome in market.outcomes:
            for source in outcome.source_refs:
                if source.fetched_at:
                    fetched_points.append(source.fetched_at)

    if not fetched_points:
        return 50.0, {"freshness_minutes": -1}

    freshest = max(fetched_points)
    age_minutes = max(0.0, (now - freshest).total_seconds() / 60.0)
    if profile.max_source_age_minutes is None or profile.max_source_age_minutes <= 0:
        return 100.0, {"freshness_minutes": round(age_minutes, 4)}

    if age_minutes <= 0:
        return 100.0, {"freshness_minutes": 0.0}
    score = max(0.0, 100.0 * (1.0 - (age_minutes / profile.max_source_age_minutes)))
    return score, {"freshness_minutes": round(age_minutes, 4)}


def score_event_information(
    event: CanonicalEvent,
    profile: Optional[InformationProfileDefinition] = None,
    now: Optional[datetime] = None,
) -> EventInformationScore:
    now = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    profile = profile or select_profile_for_event(event)

    coverage_score, missing_fields = _coverage_score(event=event, profile=profile)
    quality_score, quality_metrics = _quality_score(event=event, profile=profile)
    latency_score, latency_metrics = _latency_score(event=event, profile=profile, now=now)
    total_score = (coverage_score * 0.45) + (quality_score * 0.35) + (latency_score * 0.20)

    has_min_sources = len(event.source_refs) >= profile.min_sources
    has_min_markets = len(event.markets) >= profile.min_markets
    has_required_outcomes = all(
        len(m.outcomes) >= profile.min_outcomes_per_market
        for m in event.markets
    ) if event.markets else profile.min_markets == 0
    is_trade_eligible = (
        has_min_sources
        and has_min_markets
        and has_required_outcomes
        and not missing_fields
        and coverage_score >= 80.0
        and quality_score >= 70.0
        and latency_score >= 40.0
    )

    return EventInformationScore(
        canonical_event_id=event.canonical_event_id,
        information_profile_code=profile.code,
        coverage_score=round(coverage_score, 4),
        quality_score=round(quality_score, 4),
        latency_score=round(latency_score, 4),
        total_score=round(total_score, 4),
        is_trade_eligible=is_trade_eligible,
        missing_fields=missing_fields,
        metrics_json={**quality_metrics, **latency_metrics},
    )


def score_events_information(
    events: Iterable[CanonicalEvent],
    now: Optional[datetime] = None,
    profiles: Optional[dict[str, InformationProfileDefinition]] = None,
) -> list[EventInformationScore]:
    profiles = profiles or get_default_information_profiles()
    out: list[EventInformationScore] = []
    for event in sorted(events, key=lambda x: x.canonical_event_id):
        profile = select_profile_for_event(event=event, profiles=profiles)
        out.append(score_event_information(event=event, profile=profile, now=now))
    return out
