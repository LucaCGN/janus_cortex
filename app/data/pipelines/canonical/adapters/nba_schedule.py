from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from app.data.nodes.nba.schedule.season_schedule import parse_polymarket_slug
from app.data.pipelines.canonical.id_rules import dedupe_events
from app.data.pipelines.canonical.models import CanonicalEvent, CanonicalProviderRef


def _as_utc_datetime(value: object) -> Optional[datetime]:
    if value is None:
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
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _text(value: object) -> Optional[str]:
    if value is None:
        return None
    out = str(value).strip()
    return out or None


def _build_schedule_index(schedule_df: pd.DataFrame) -> dict[tuple[str, str, str], dict[str, object]]:
    if schedule_df.empty:
        return {}
    required = ("game_date", "home_team_slug", "away_team_slug")
    for key in required:
        if key not in schedule_df.columns:
            return {}

    index: dict[tuple[str, str, str], dict[str, object]] = {}
    for _, row in schedule_df.iterrows():
        game_date = _text(row.get("game_date"))
        home = _text(row.get("home_team_slug"))
        away = _text(row.get("away_team_slug"))
        if not game_date or not home or not away:
            continue
        primary = (game_date, away.upper(), home.upper())
        reverse = (game_date, home.upper(), away.upper())
        row_data = row.to_dict()
        index[primary] = row_data
        index[reverse] = row_data
    return index


def attach_nba_schedule_context(
    events: list[CanonicalEvent],
    schedule_df: Optional[pd.DataFrame],
    provider_code: str = "nba_cdn",
    fetched_at: Optional[datetime] = None,
) -> list[CanonicalEvent]:
    """
    Enrich canonical NBA game events with NBA schedule linkage metadata.
    """
    if schedule_df is None or schedule_df.empty or not events:
        return events

    fetched_at = fetched_at.astimezone(timezone.utc) if fetched_at else datetime.now(timezone.utc)
    index = _build_schedule_index(schedule_df)
    if not index:
        return events

    enriched: list[CanonicalEvent] = []
    for event in events:
        away, home, game_date = parse_polymarket_slug(event.canonical_slug)
        if not away or not home or not game_date:
            enriched.append(event)
            continue

        match = index.get((game_date, away.upper(), home.upper()))
        if not match:
            enriched.append(event)
            continue

        metadata = dict(event.metadata_json)
        metadata.update(
            {
                "nba_game_id": _text(match.get("game_id")),
                "nba_game_status": match.get("game_status"),
                "nba_game_status_text": _text(match.get("game_status_text")),
                "nba_game_date": _text(match.get("game_date")),
            }
        )

        start_time = event.start_time or _as_utc_datetime(match.get("game_start_time"))
        source_refs = [
            *event.source_refs,
            CanonicalProviderRef(
                provider_code=provider_code,
                external_id=_text(match.get("game_id")),
                external_slug=f"{away.lower()}-{home.lower()}-{game_date}",
                external_url=None,
                fetched_at=fetched_at,
                raw_summary_json={
                    "game_date": _text(match.get("game_date")),
                    "game_status": match.get("game_status"),
                },
            ),
        ]
        tags = sorted(set([*event.tags, "nba:scheduled"]))
        home_entity = event.home_entity or _text(match.get("home_team_name"))
        away_entity = event.away_entity or _text(match.get("away_team_name"))

        enriched.append(
            event.model_copy(
                update={
                    "metadata_json": metadata,
                    "start_time": start_time,
                    "source_refs": source_refs,
                    "tags": tags,
                    "home_entity": home_entity,
                    "away_entity": away_entity,
                }
            )
        )

    return dedupe_events(enriched)
