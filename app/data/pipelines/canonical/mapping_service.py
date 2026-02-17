from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from app.data.pipelines.canonical.adapters.gamma_nba import adapt_gamma_nba_to_canonical
from app.data.pipelines.canonical.adapters.nba_schedule import attach_nba_schedule_context
from app.data.pipelines.canonical.id_rules import dedupe_events
from app.data.pipelines.canonical.information_profiles import (
    EventInformationScore,
    score_events_information,
    select_profile_for_event,
)
from app.data.pipelines.canonical.models import CanonicalBundle
from app.data.pipelines.canonical.quality_gates import QualityGateReport, run_quality_gates


class CanonicalMappingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    bundle: CanonicalBundle
    information_scores: list[EventInformationScore] = Field(default_factory=list)
    quality_report: QualityGateReport
    stats_json: dict[str, int | float | str] = Field(default_factory=dict)


def _to_frame(records: Optional[list[dict[str, Any]]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)


def _with_profile_codes(
    bundle: CanonicalBundle,
    scores: list[EventInformationScore],
) -> CanonicalBundle:
    by_event_id = {s.canonical_event_id: s.information_profile_code for s in scores}
    enriched = []
    for event in bundle.events:
        code = by_event_id.get(event.canonical_event_id)
        if code is None:
            code = select_profile_for_event(event).code
        enriched.append(event.model_copy(update={"information_profile_code": code}))
    return CanonicalBundle(events=dedupe_events(enriched))


def build_canonical_mapping_result(
    events_df: Optional[pd.DataFrame] = None,
    moneyline_df: Optional[pd.DataFrame] = None,
    schedule_df: Optional[pd.DataFrame] = None,
    now: Optional[datetime] = None,
) -> CanonicalMappingResult:
    """
    Build canonical mapping bundle from provider node payload DataFrames.
    """
    now = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    mapped_events = adapt_gamma_nba_to_canonical(
        events_df=events_df,
        moneyline_df=moneyline_df,
        fetched_at=now,
    )
    mapped_events = attach_nba_schedule_context(
        events=mapped_events,
        schedule_df=schedule_df,
        fetched_at=now,
    )

    bundle = CanonicalBundle(events=dedupe_events(mapped_events))
    scores = score_events_information(events=bundle.events, now=now)
    bundle = _with_profile_codes(bundle=bundle, scores=scores)
    quality = run_quality_gates(bundle=bundle, now=now)

    return CanonicalMappingResult(
        bundle=bundle,
        information_scores=scores,
        quality_report=quality,
        stats_json={
            "event_count": len(bundle.events),
            "market_count": sum(len(e.markets) for e in bundle.events),
            "outcome_count": sum(len(m.outcomes) for e in bundle.events for m in e.markets),
            "quality_error_count": quality.error_count,
            "quality_warning_count": quality.warning_count,
        },
    )


def build_canonical_mapping_result_from_payloads(
    gamma_events_payload: Optional[list[dict[str, Any]]] = None,
    gamma_moneyline_payload: Optional[list[dict[str, Any]]] = None,
    nba_schedule_payload: Optional[list[dict[str, Any]]] = None,
    now: Optional[datetime] = None,
) -> CanonicalMappingResult:
    return build_canonical_mapping_result(
        events_df=_to_frame(gamma_events_payload),
        moneyline_df=_to_frame(gamma_moneyline_payload),
        schedule_df=_to_frame(nba_schedule_payload),
        now=now,
    )
