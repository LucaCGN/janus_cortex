from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Iterable, Optional

from app.data.pipelines.canonical.models import (
    CanonicalEvent,
    CanonicalMarket,
    CanonicalOutcome,
    CanonicalProviderRef,
)


def normalize_token(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_slug(value: str) -> str:
    token = normalize_token(value)
    token = re.sub(r"[^a-z0-9\s-]", "", token)
    token = token.replace(" ", "-")
    token = re.sub(r"-+", "-", token).strip("-")
    return token


def normalize_team_code(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    token = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    return token or None


def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _event_time_key(start_time: Optional[datetime]) -> str:
    dt = _to_utc(start_time)
    if dt is None:
        return "na"
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_deterministic_id(namespace: str, *parts: str) -> str:
    payload = "|".join([normalize_token(namespace), *[normalize_token(x) for x in parts]])
    return str(uuid.uuid5(uuid.NAMESPACE_URL, payload))


def build_event_id(
    canonical_slug: Optional[str],
    start_time: Optional[datetime],
    provider_code: str,
    external_id: Optional[str] = None,
) -> str:
    slug_key = normalize_slug(canonical_slug or "")
    time_key = _event_time_key(start_time)

    if slug_key:
        # Cross-provider stable key for same event timeline.
        return make_deterministic_id("event", slug_key, time_key)

    return make_deterministic_id("event", provider_code, external_id or "na", time_key)


def build_market_id(
    canonical_event_id: str,
    question: str,
    market_kind: str,
    provider_code: str,
    external_market_id: Optional[str] = None,
) -> str:
    if external_market_id:
        return make_deterministic_id(
            "market",
            canonical_event_id,
            provider_code,
            str(external_market_id),
        )

    return make_deterministic_id(
        "market",
        canonical_event_id,
        normalize_slug(question),
        normalize_slug(market_kind),
    )


def build_outcome_id(
    canonical_market_id: str,
    label: str,
    token_id: Optional[str] = None,
) -> str:
    return make_deterministic_id(
        "outcome",
        canonical_market_id,
        normalize_slug(label),
        str(token_id or "na"),
    )


def dedupe_provider_refs(refs: Iterable[CanonicalProviderRef]) -> list[CanonicalProviderRef]:
    seen: dict[tuple[str, str, str], CanonicalProviderRef] = {}
    for ref in refs:
        key = (
            normalize_token(ref.provider_code),
            str(ref.external_id or ""),
            str(ref.external_slug or ""),
        )
        seen[key] = ref
    return sorted(seen.values(), key=lambda x: (x.provider_code, str(x.external_id or ""), str(x.external_slug or "")))


def dedupe_outcomes(outcomes: Iterable[CanonicalOutcome]) -> list[CanonicalOutcome]:
    by_id: dict[str, CanonicalOutcome] = {}
    for out in outcomes:
        key = out.canonical_outcome_id
        if key not in by_id:
            by_id[key] = out
            continue

        prev = by_id[key]
        merged = CanonicalOutcome(
            canonical_outcome_id=key,
            label=out.label if out.label else prev.label,
            token_id=out.token_id or prev.token_id,
            implied_prob=out.implied_prob if out.implied_prob is not None else prev.implied_prob,
            last_price=out.last_price if out.last_price is not None else prev.last_price,
            metadata_json={**prev.metadata_json, **out.metadata_json},
            source_refs=dedupe_provider_refs([*prev.source_refs, *out.source_refs]),
        )
        by_id[key] = merged

    return sorted(by_id.values(), key=lambda x: (x.label, x.canonical_outcome_id))


def dedupe_markets(markets: Iterable[CanonicalMarket]) -> list[CanonicalMarket]:
    by_id: dict[str, CanonicalMarket] = {}

    for market in markets:
        key = market.canonical_market_id
        if key not in by_id:
            by_id[key] = market
            continue

        prev = by_id[key]
        merged = CanonicalMarket(
            canonical_market_id=key,
            canonical_event_id=market.canonical_event_id,
            question=market.question if market.question else prev.question,
            market_kind=market.market_kind if market.market_kind else prev.market_kind,
            status=market.status if market.status else prev.status,
            open_time=market.open_time or prev.open_time,
            close_time=market.close_time or prev.close_time,
            outcomes=dedupe_outcomes([*prev.outcomes, *market.outcomes]),
            metadata_json={**prev.metadata_json, **market.metadata_json},
            source_refs=dedupe_provider_refs([*prev.source_refs, *market.source_refs]),
        )
        by_id[key] = merged

    return sorted(by_id.values(), key=lambda x: x.canonical_market_id)


def _event_score(event: CanonicalEvent) -> tuple[int, int, int, int]:
    # Deterministic preference: richer object wins on merge collisions.
    fields_score = 0
    if event.start_time is not None:
        fields_score += 1
    if event.end_time is not None:
        fields_score += 1
    if event.home_entity and event.away_entity:
        fields_score += 1

    return (
        len(event.markets),
        len(event.source_refs),
        fields_score,
        len(event.metadata_json),
    )


def _merge_events(primary: CanonicalEvent, secondary: CanonicalEvent) -> CanonicalEvent:
    winner, loser = (primary, secondary)
    if _event_score(secondary) > _event_score(primary):
        winner, loser = secondary, primary

    merged = CanonicalEvent(
        canonical_event_id=winner.canonical_event_id,
        canonical_slug=winner.canonical_slug or loser.canonical_slug,
        title=winner.title or loser.title,
        domain=winner.domain or loser.domain,
        event_kind=winner.event_kind or loser.event_kind,
        status=winner.status or loser.status,
        start_time=winner.start_time or loser.start_time,
        end_time=winner.end_time or loser.end_time,
        resolution_time=winner.resolution_time or loser.resolution_time,
        home_entity=winner.home_entity or loser.home_entity,
        away_entity=winner.away_entity or loser.away_entity,
        tags=sorted(set([*winner.tags, *loser.tags])),
        information_profile_code=winner.information_profile_code or loser.information_profile_code,
        metadata_json={**loser.metadata_json, **winner.metadata_json},
        source_refs=dedupe_provider_refs([*winner.source_refs, *loser.source_refs]),
        markets=dedupe_markets([*winner.markets, *loser.markets]),
    )
    return merged


def dedupe_events(events: Iterable[CanonicalEvent]) -> list[CanonicalEvent]:
    # First pass by canonical_event_id.
    by_event_id: dict[str, CanonicalEvent] = {}
    for event in events:
        key = event.canonical_event_id
        if key in by_event_id:
            by_event_id[key] = _merge_events(by_event_id[key], event)
        else:
            by_event_id[key] = event

    # Second pass by (canonical_slug, start_time) to catch cross-source collisions
    # where providers produced different IDs before normalization rollouts.
    by_slug_time: dict[tuple[str, str], CanonicalEvent] = {}
    for event in by_event_id.values():
        key = (event.canonical_slug, _event_time_key(event.start_time))
        if key in by_slug_time:
            by_slug_time[key] = _merge_events(by_slug_time[key], event)
        else:
            by_slug_time[key] = event

    return sorted(by_slug_time.values(), key=lambda x: (x.start_time or datetime(1970, 1, 1, tzinfo=timezone.utc), x.canonical_slug))
