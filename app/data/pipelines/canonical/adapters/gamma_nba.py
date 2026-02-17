from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

from app.data.pipelines.canonical.id_rules import (
    build_event_id,
    build_market_id,
    build_outcome_id,
    dedupe_events,
    dedupe_markets,
    dedupe_outcomes,
    normalize_slug,
    normalize_team_code,
)
from app.data.pipelines.canonical.models import (
    CanonicalEvent,
    CanonicalMarket,
    CanonicalOutcome,
    CanonicalProviderRef,
)


logger = logging.getLogger(__name__)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _as_utc_datetime(value: Any) -> Optional[datetime]:
    if _is_missing(value):
        return None

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, pd.Timestamp):
        dt = value.to_pydatetime()
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            try:
                dt = datetime.strptime(raw, "%Y-%m-%d")
            except ValueError:
                return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _as_float(value: Any) -> Optional[float]:
    if _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_text(value: Any) -> Optional[str]:
    if _is_missing(value):
        return None
    return str(value).strip()


def _parse_entities_from_slug(slug: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not slug:
        return None, None
    parts = slug.lower().split("-")
    if len(parts) < 6 or parts[0] != "nba":
        return None, None
    away = normalize_team_code(parts[1])
    home = normalize_team_code(parts[2])
    return home, away


def _provider_ref(
    provider_code: str,
    external_id: Optional[str],
    external_slug: Optional[str],
    external_url: Optional[str],
    fetched_at: Optional[datetime],
    raw_summary_json: Optional[dict[str, Any]] = None,
) -> CanonicalProviderRef:
    return CanonicalProviderRef(
        provider_code=provider_code,
        external_id=external_id,
        external_slug=external_slug,
        external_url=external_url,
        fetched_at=fetched_at,
        raw_summary_json=raw_summary_json or {},
    )


def _event_kind_from_gamma(event_type: Optional[str]) -> str:
    token = str(event_type or "").strip().upper()
    if token == "GAME":
        return "sports_game"
    if token == "AWARD":
        return "award"
    return "prediction_event"


def _event_status_from_closed(closed: Any) -> str:
    return "closed" if bool(closed) else "open"


def _market_status_from_closed(closed: Any) -> str:
    return "closed" if bool(closed) else "open"


def _build_outcome_from_moneyline_row(
    row: pd.Series,
    canonical_market_id: str,
    provider_code: str,
    fetched_at: Optional[datetime],
) -> CanonicalOutcome:
    label = _safe_text(row.get("outcome")) or "unknown"
    token_id = _safe_text(row.get("token_id"))
    implied_prob = _as_float(row.get("implied_prob"))
    last_price = _as_float(row.get("last_price"))
    canonical_outcome_id = build_outcome_id(
        canonical_market_id=canonical_market_id,
        label=label,
        token_id=token_id,
    )
    return CanonicalOutcome(
        canonical_outcome_id=canonical_outcome_id,
        label=label,
        token_id=token_id,
        implied_prob=implied_prob,
        last_price=last_price,
        metadata_json={
            "team_id": _safe_text(row.get("team_id")),
            "team_abbr": normalize_team_code(_safe_text(row.get("team_abbr"))),
            "team_name": _safe_text(row.get("team_name")),
        },
        source_refs=[
            _provider_ref(
                provider_code=provider_code,
                external_id=token_id,
                external_slug=normalize_slug(label),
                external_url=None,
                fetched_at=fetched_at,
            )
        ],
    )


def _build_markets_for_event(
    event_id: Optional[str],
    event_slug: Optional[str],
    event_title: Optional[str],
    market_rows: pd.DataFrame,
    canonical_event_id: str,
    provider_code: str,
    fetched_at: Optional[datetime],
) -> list[CanonicalMarket]:
    if market_rows.empty:
        return []

    out: list[CanonicalMarket] = []
    grouped = market_rows.groupby("market_id", dropna=False)
    for _, frame in grouped:
        market_id_raw = _safe_text(frame.iloc[0].get("market_id")) or "unknown-market"
        market_slug = _safe_text(frame.iloc[0].get("market_slug"))
        market_type = _safe_text(frame.iloc[0].get("market_type")) or "moneyline"
        question = market_slug or f"moneyline-{event_slug or event_id or event_title or market_id_raw}"
        canonical_market_id = build_market_id(
            canonical_event_id=canonical_event_id,
            question=question,
            market_kind=market_type,
            provider_code=provider_code,
            external_market_id=market_id_raw,
        )
        close_time = _as_utc_datetime(frame.iloc[0].get("game_start_time"))
        outcomes = [
            _build_outcome_from_moneyline_row(
                row=r,
                canonical_market_id=canonical_market_id,
                provider_code=provider_code,
                fetched_at=fetched_at,
            )
            for _, r in frame.iterrows()
        ]
        outcomes = dedupe_outcomes(outcomes)
        if len(outcomes) < 2:
            logger.warning(
                "adapt_gamma_nba_to_canonical: market=%s produced <2 outcomes after dedupe; skipped",
                market_id_raw,
            )
            continue

        out.append(
            CanonicalMarket(
                canonical_market_id=canonical_market_id,
                canonical_event_id=canonical_event_id,
                question=question,
                market_kind=market_type,
                status=_market_status_from_closed(frame.iloc[0].get("closed")),
                open_time=None,
                close_time=close_time,
                outcomes=outcomes,
                metadata_json={
                    "provider_event_id": event_id,
                    "provider_event_slug": event_slug,
                    "ingestion_source": _safe_text(frame.iloc[0].get("ingestion_source")),
                    "volume": _as_float(frame.iloc[0].get("volume")),
                    "liquidity": _as_float(frame.iloc[0].get("liquidity")),
                },
                source_refs=[
                    _provider_ref(
                        provider_code=provider_code,
                        external_id=market_id_raw,
                        external_slug=market_slug,
                        external_url=None,
                        fetched_at=fetched_at,
                    )
                ],
            )
        )
    return dedupe_markets(out)


def _build_event_from_row(
    row: pd.Series,
    moneyline_df: pd.DataFrame,
    provider_code: str,
    fetched_at: Optional[datetime],
) -> CanonicalEvent:
    event_id = _safe_text(row.get("event_id"))
    slug = _safe_text(row.get("slug"))
    title = _safe_text(row.get("title")) or slug or event_id or "unknown-event"
    start_time = _as_utc_datetime(row.get("start_time"))
    end_time = _as_utc_datetime(row.get("end_time"))
    canonical_slug = normalize_slug(slug or title or event_id or "event")
    canonical_event_id = build_event_id(
        canonical_slug=canonical_slug,
        start_time=start_time,
        provider_code=provider_code,
        external_id=event_id,
    )
    home_entity, away_entity = _parse_entities_from_slug(slug)

    event_market_rows = moneyline_df.copy()
    if "event_id" in event_market_rows.columns and event_id is not None:
        event_market_rows = event_market_rows[event_market_rows["event_id"].astype(str) == event_id]
    elif "event_slug" in event_market_rows.columns and slug:
        event_market_rows = event_market_rows[event_market_rows["event_slug"].astype(str) == slug]
    else:
        event_market_rows = pd.DataFrame()

    markets = _build_markets_for_event(
        event_id=event_id,
        event_slug=slug,
        event_title=title,
        market_rows=event_market_rows,
        canonical_event_id=canonical_event_id,
        provider_code=provider_code,
        fetched_at=fetched_at,
    )

    tags_value = row.get("tags")
    tag_list: list[str] = []
    if isinstance(tags_value, list):
        for item in tags_value:
            tag_token = _safe_text(item)
            if tag_token:
                tag_list.append(tag_token)

    return CanonicalEvent(
        canonical_event_id=canonical_event_id,
        canonical_slug=canonical_slug,
        title=title,
        domain="sports",
        event_kind=_event_kind_from_gamma(_safe_text(row.get("event_type"))),
        status=_event_status_from_closed(row.get("closed")),
        start_time=start_time,
        end_time=end_time,
        home_entity=home_entity,
        away_entity=away_entity,
        tags=tag_list + ["nba", "polymarket"],
        metadata_json={
            "provider_event_id": event_id,
            "provider_slug": slug,
            "category": _safe_text(row.get("category")),
            "subcategory": _safe_text(row.get("subcategory")),
            "volume": _as_float(row.get("volume")),
            "liquidity": _as_float(row.get("liquidity")),
        },
        source_refs=[
            _provider_ref(
                provider_code=provider_code,
                external_id=event_id,
                external_slug=slug,
                external_url=f"https://polymarket.com/event/{slug}" if slug else None,
                fetched_at=fetched_at,
            )
        ],
        markets=markets,
    )


def _build_synthetic_events_from_markets(
    moneyline_df: pd.DataFrame,
    existing_provider_event_ids: set[str],
    provider_code: str,
    fetched_at: Optional[datetime],
) -> list[CanonicalEvent]:
    if moneyline_df.empty:
        return []

    synthetic: list[CanonicalEvent] = []
    grouped = moneyline_df.groupby("event_id", dropna=False)
    for _, frame in grouped:
        provider_event_id = _safe_text(frame.iloc[0].get("event_id"))
        if provider_event_id and provider_event_id in existing_provider_event_ids:
            continue

        slug = _safe_text(frame.iloc[0].get("event_slug"))
        title = _safe_text(frame.iloc[0].get("event_title")) or slug or provider_event_id or "unknown-event"
        start_time = _as_utc_datetime(frame.iloc[0].get("game_start_time"))
        canonical_slug = normalize_slug(slug or title)
        canonical_event_id = build_event_id(
            canonical_slug=canonical_slug,
            start_time=start_time,
            provider_code=provider_code,
            external_id=provider_event_id,
        )
        home_entity, away_entity = _parse_entities_from_slug(slug)
        markets = _build_markets_for_event(
            event_id=provider_event_id,
            event_slug=slug,
            event_title=title,
            market_rows=frame,
            canonical_event_id=canonical_event_id,
            provider_code=provider_code,
            fetched_at=fetched_at,
        )
        synthetic.append(
            CanonicalEvent(
                canonical_event_id=canonical_event_id,
                canonical_slug=canonical_slug,
                title=title,
                domain="sports",
                event_kind="sports_game",
                status="open",
                start_time=start_time,
                end_time=None,
                home_entity=home_entity,
                away_entity=away_entity,
                tags=["nba", "polymarket", "synthetic"],
                metadata_json={
                    "synthetic_from_moneyline": True,
                    "provider_event_id": provider_event_id,
                    "provider_slug": slug,
                },
                source_refs=[
                    _provider_ref(
                        provider_code=provider_code,
                        external_id=provider_event_id,
                        external_slug=slug,
                        external_url=f"https://polymarket.com/event/{slug}" if slug else None,
                        fetched_at=fetched_at,
                    )
                ],
                markets=markets,
            )
        )

    return synthetic


def adapt_gamma_nba_to_canonical(
    events_df: Optional[pd.DataFrame],
    moneyline_df: Optional[pd.DataFrame],
    provider_code: str = "gamma",
    fetched_at: Optional[datetime] = None,
) -> list[CanonicalEvent]:
    """
    Map Gamma NBA events + moneyline rows into canonical events graph.

    This adapter is deterministic and idempotent:
    - IDs are generated by canonical id rules.
    - Outputs are sorted and deduplicated before return.
    """
    event_frame = events_df.copy() if events_df is not None else pd.DataFrame()
    market_frame = moneyline_df.copy() if moneyline_df is not None else pd.DataFrame()
    fetched_at = fetched_at.astimezone(timezone.utc) if fetched_at else datetime.now(timezone.utc)

    for required in ("event_id", "slug", "title"):
        if required not in event_frame.columns:
            event_frame[required] = None

    if "event_id" not in market_frame.columns:
        market_frame["event_id"] = None

    events: list[CanonicalEvent] = []
    for _, row in event_frame.iterrows():
        events.append(
            _build_event_from_row(
                row=row,
                moneyline_df=market_frame,
                provider_code=provider_code,
                fetched_at=fetched_at,
            )
        )

    existing_event_ids = {
        str(x.metadata_json.get("provider_event_id"))
        for x in events
        if x.metadata_json.get("provider_event_id") is not None
    }
    events.extend(
        _build_synthetic_events_from_markets(
            moneyline_df=market_frame,
            existing_provider_event_ids=existing_event_ids,
            provider_code=provider_code,
            fetched_at=fetched_at,
        )
    )

    out = dedupe_events(events)
    logger.info(
        "adapt_gamma_nba_to_canonical: events=%d markets=%d outcomes=%d",
        len(out),
        sum(len(ev.markets) for ev in out),
        sum(len(mk.outcomes) for ev in out for mk in ev.markets),
    )
    return out
