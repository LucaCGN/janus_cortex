"""Canonical event-domain contracts and mapping outputs."""

from app.data.pipelines.canonical import (
    CanonicalMappingResult,
    build_canonical_mapping_result,
    build_canonical_mapping_result_from_payloads,
)
from app.data.pipelines.canonical.id_rules import (
    build_event_id,
    build_market_id,
    build_outcome_id,
    dedupe_events,
    dedupe_markets,
    dedupe_outcomes,
)
from app.data.pipelines.canonical.information_profiles import (
    EventInformationScore,
    InformationProfileDefinition,
    score_event_information,
    score_events_information,
)
from app.data.pipelines.canonical.models import (
    CanonicalBundle,
    CanonicalEvent,
    CanonicalMarket,
    CanonicalOutcome,
    CanonicalProviderRef,
)
from app.data.pipelines.canonical.quality_gates import QualityGateReport, run_quality_gates

__all__ = [
    "CanonicalBundle",
    "CanonicalEvent",
    "CanonicalMappingResult",
    "CanonicalMarket",
    "CanonicalOutcome",
    "CanonicalProviderRef",
    "EventInformationScore",
    "InformationProfileDefinition",
    "QualityGateReport",
    "build_canonical_mapping_result",
    "build_canonical_mapping_result_from_payloads",
    "build_event_id",
    "build_market_id",
    "build_outcome_id",
    "dedupe_events",
    "dedupe_markets",
    "dedupe_outcomes",
    "run_quality_gates",
    "score_event_information",
    "score_events_information",
]

