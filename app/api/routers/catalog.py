from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg2.extensions import connection as PsycopgConnection

from app.api.db import cursor_dict, fetchall_dicts, fetchone_dict, to_jsonable
from app.api.dependencies import get_db_connection
from app.api.models import (
    EventCreateRequest,
    EventImportUrlRequest,
    EventPatchRequest,
    EventTypeCreateRequest,
    InformationProfileCreateRequest,
)
from app.data.databases.repositories import JanusUpsertRepository
from app.data.databases.seed_packs.polymarket_event_seed_pack import (
    EventProbeConfig,
    run_polymarket_event_seed_pack,
)


router = APIRouter(prefix="/v1", tags=["catalog"])


def _resolve_event_type_id(
    connection: PsycopgConnection,
    *,
    event_type_id: UUID | None,
    event_type_code: str | None,
) -> str:
    if event_type_id is not None:
        return str(event_type_id)
    if not event_type_code:
        raise HTTPException(status_code=422, detail="event_type_id or event_type_code is required")
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT event_type_id
            FROM catalog.event_types
            WHERE code = %s;
            """,
            (event_type_code,),
        )
        row = fetchone_dict(cursor)
    if row is None:
        raise HTTPException(status_code=404, detail=f"event_type_code not found: {event_type_code}")
    return str(row["event_type_id"])


def _resolve_information_profile_id(
    connection: PsycopgConnection,
    *,
    information_profile_id: UUID | None,
    information_profile_code: str | None,
) -> str | None:
    if information_profile_id is not None:
        return str(information_profile_id)
    if not information_profile_code:
        return None
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT information_profile_id
            FROM catalog.information_profiles
            WHERE code = %s;
            """,
            (information_profile_code,),
        )
        row = fetchone_dict(cursor)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"information_profile_code not found: {information_profile_code}",
        )
    return str(row["information_profile_id"])


def _infer_event_probe_from_url(payload: EventImportUrlRequest) -> EventProbeConfig:
    parsed = urlparse(payload.url)
    pieces = [piece for piece in parsed.path.split("/") if piece]
    if not pieces:
        raise HTTPException(status_code=422, detail="Could not parse event slug from URL.")
    slug = pieces[-1]

    if "/sports/nba/" in payload.url:
        event_type_code = "sports_nba_game"
        history_mode = payload.history_mode or "rolling_recent"
        history_market_selector = payload.history_market_selector or "moneyline"
    else:
        event_type_code = "general_event"
        history_mode = payload.history_mode or "interval_only"
        history_market_selector = payload.history_market_selector or "primary"

    return EventProbeConfig(
        step_code=f"api_import_{slug}",
        url=payload.url,
        event_type_code=event_type_code,
        history_mode=history_mode,
        history_market_selector=history_market_selector,
        history_interval=payload.history_interval,
        history_fidelity=payload.history_fidelity,
        recent_lookback_days=payload.recent_lookback_days,
        allow_snapshot_fallback=payload.allow_snapshot_fallback,
        stream_enabled=payload.stream_enabled,
        stream_sample_count=payload.stream_sample_count,
        stream_sample_interval_sec=payload.stream_sample_interval_sec,
        stream_max_outcomes=payload.stream_max_outcomes,
    )


@router.get("/event-types")
def list_event_types(
    domain: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    params: list[Any] = []
    where_sql = ""
    if domain:
        where_sql = "WHERE domain = %s"
        params.append(domain)
    params.extend([limit, offset])
    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT event_type_id, code, name, domain, description, default_horizon, resolution_policy, created_at
            FROM catalog.event_types
            {where_sql}
            ORDER BY code
            LIMIT %s OFFSET %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.post("/event-types", status_code=status.HTTP_201_CREATED)
def create_event_type(
    payload: EventTypeCreateRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    repo = JanusUpsertRepository(connection)
    event_type_id = repo.upsert_event_type(
        event_type_id=str(payload.event_type_id or uuid4()),
        code=payload.code.strip(),
        name=payload.name.strip(),
        domain=payload.domain.strip(),
        description=payload.description,
        default_horizon=payload.default_horizon,
        resolution_policy=payload.resolution_policy,
    )
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT event_type_id, code, name, domain, description, default_horizon, resolution_policy, created_at
            FROM catalog.event_types
            WHERE event_type_id = %s;
            """,
            (event_type_id,),
        )
        row = fetchone_dict(cursor)
    if row is None:
        raise HTTPException(status_code=500, detail="Event type was not persisted.")
    return to_jsonable(row)


@router.get("/information-profiles")
def list_information_profiles(
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                information_profile_id,
                code,
                name,
                description,
                min_sources,
                required_fields_json,
                refresh_interval_sec,
                created_at
            FROM catalog.information_profiles
            ORDER BY code
            LIMIT %s OFFSET %s;
            """,
            (limit, offset),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.post("/information-profiles", status_code=status.HTTP_201_CREATED)
def create_information_profile(
    payload: InformationProfileCreateRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    repo = JanusUpsertRepository(connection)
    information_profile_id = repo.upsert_information_profile(
        information_profile_id=str(payload.information_profile_id or uuid4()),
        code=payload.code.strip(),
        name=payload.name.strip(),
        description=payload.description,
        min_sources=payload.min_sources,
        required_fields_json=payload.required_fields_json,
        refresh_interval_sec=payload.refresh_interval_sec,
    )
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                information_profile_id,
                code,
                name,
                description,
                min_sources,
                required_fields_json,
                refresh_interval_sec,
                created_at
            FROM catalog.information_profiles
            WHERE information_profile_id = %s;
            """,
            (information_profile_id,),
        )
        row = fetchone_dict(cursor)
    if row is None:
        raise HTTPException(status_code=500, detail="Information profile was not persisted.")
    return to_jsonable(row)


@router.post("/events", status_code=status.HTTP_201_CREATED)
def create_event(
    payload: EventCreateRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    repo = JanusUpsertRepository(connection)
    resolved_event_type_id = _resolve_event_type_id(
        connection,
        event_type_id=payload.event_type_id,
        event_type_code=payload.event_type_code,
    )
    resolved_information_profile_id = _resolve_information_profile_id(
        connection,
        information_profile_id=payload.information_profile_id,
        information_profile_code=payload.information_profile_code,
    )
    event_id = repo.upsert_event(
        event_id=str(payload.event_id or uuid4()),
        event_type_id=resolved_event_type_id,
        information_profile_id=resolved_information_profile_id,
        title=payload.title.strip(),
        status=payload.status.strip(),
        canonical_slug=payload.canonical_slug,
        start_time=payload.start_time,
        end_time=payload.end_time,
        resolution_time=payload.resolution_time,
        metadata_json=payload.metadata_json,
    )
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                e.event_id,
                e.event_type_id,
                et.code AS event_type_code,
                e.information_profile_id,
                ip.code AS information_profile_code,
                e.title,
                e.canonical_slug,
                e.status,
                e.start_time,
                e.end_time,
                e.resolution_time,
                e.metadata_json,
                e.created_at,
                e.updated_at
            FROM catalog.events e
            LEFT JOIN catalog.event_types et ON et.event_type_id = e.event_type_id
            LEFT JOIN catalog.information_profiles ip ON ip.information_profile_id = e.information_profile_id
            WHERE e.event_id = %s;
            """,
            (event_id,),
        )
        row = fetchone_dict(cursor)
    if row is None:
        raise HTTPException(status_code=500, detail="Event was not persisted.")
    return to_jsonable(row)


@router.get("/events")
def list_events(
    status_filter: str | None = Query(default=None, alias="status"),
    event_type_code: str | None = Query(default=None),
    canonical_slug: str | None = Query(default=None),
    canonical_slug_prefix: str | None = Query(default=None),
    start_time_from: datetime | None = Query(default=None),
    start_time_to: datetime | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    conditions: list[str] = []
    params: list[Any] = []
    if status_filter:
        conditions.append("e.status = %s")
        params.append(status_filter)
    if event_type_code:
        conditions.append("et.code = %s")
        params.append(event_type_code)
    if canonical_slug:
        conditions.append("e.canonical_slug = %s")
        params.append(canonical_slug)
    if canonical_slug_prefix:
        conditions.append("e.canonical_slug LIKE %s")
        params.append(f"{canonical_slug_prefix}%")
    if start_time_from:
        conditions.append("e.start_time >= %s")
        params.append(start_time_from)
    if start_time_to:
        conditions.append("e.start_time <= %s")
        params.append(start_time_to)

    where_sql = ""
    if conditions:
        where_sql = f"WHERE {' AND '.join(conditions)}"
    params.extend([limit, offset])

    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT
                e.event_id,
                e.event_type_id,
                et.code AS event_type_code,
                e.information_profile_id,
                ip.code AS information_profile_code,
                e.title,
                e.canonical_slug,
                e.status,
                e.start_time,
                e.end_time,
                e.resolution_time,
                e.metadata_json,
                e.created_at,
                e.updated_at
            FROM catalog.events e
            LEFT JOIN catalog.event_types et ON et.event_type_id = e.event_type_id
            LEFT JOIN catalog.information_profiles ip ON ip.information_profile_id = e.information_profile_id
            {where_sql}
            ORDER BY e.start_time DESC NULLS LAST, e.created_at DESC
            LIMIT %s OFFSET %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.get("/events/{event_id}")
def get_event(
    event_id: UUID,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                e.event_id,
                e.event_type_id,
                et.code AS event_type_code,
                e.information_profile_id,
                ip.code AS information_profile_code,
                e.title,
                e.canonical_slug,
                e.status,
                e.start_time,
                e.end_time,
                e.resolution_time,
                e.metadata_json,
                e.created_at,
                e.updated_at
            FROM catalog.events e
            LEFT JOIN catalog.event_types et ON et.event_type_id = e.event_type_id
            LEFT JOIN catalog.information_profiles ip ON ip.information_profile_id = e.information_profile_id
            WHERE e.event_id = %s;
            """,
            (str(event_id),),
        )
        row = fetchone_dict(cursor)
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return to_jsonable(row)


@router.patch("/events/{event_id}")
def patch_event(
    event_id: UUID,
    payload: EventPatchRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    updates: list[str] = []
    params: list[Any] = []

    if payload.event_type_id is not None or payload.event_type_code:
        resolved_event_type_id = _resolve_event_type_id(
            connection,
            event_type_id=payload.event_type_id,
            event_type_code=payload.event_type_code,
        )
        updates.append("event_type_id = %s")
        params.append(resolved_event_type_id)

    if payload.information_profile_id is not None or payload.information_profile_code:
        resolved_profile_id = _resolve_information_profile_id(
            connection,
            information_profile_id=payload.information_profile_id,
            information_profile_code=payload.information_profile_code,
        )
        updates.append("information_profile_id = %s")
        params.append(resolved_profile_id)

    scalar_updates = {
        "title": payload.title,
        "status": payload.status,
        "canonical_slug": payload.canonical_slug,
        "start_time": payload.start_time,
        "end_time": payload.end_time,
        "resolution_time": payload.resolution_time,
        "metadata_json": payload.metadata_json,
    }
    for field_name, field_value in scalar_updates.items():
        if field_value is not None:
            updates.append(f"{field_name} = %s")
            params.append(field_value)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided for update.")

    updates.append("updated_at = %s")
    params.append(datetime.now(timezone.utc))
    params.append(str(event_id))

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE catalog.events
            SET {", ".join(updates)}
            WHERE event_id = %s;
            """,
            tuple(params),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Event not found")

    return get_event(event_id=event_id, connection=connection)


@router.post("/events/import-url", status_code=status.HTTP_202_ACCEPTED)
def import_event_url(
    payload: EventImportUrlRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    probe = _infer_event_probe_from_url(payload)
    summary = run_polymarket_event_seed_pack([probe], persist=True)
    slug = probe.url.rstrip("/").split("/")[-1]

    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT event_id, title, canonical_slug, status, start_time, end_time, resolution_time, metadata_json
            FROM catalog.events
            WHERE canonical_slug = %s;
            """,
            (slug,),
        )
        event_row = fetchone_dict(cursor)

    return {
        "sync_run_id": summary.sync_run_id,
        "status": summary.status,
        "rows_read": summary.rows_read,
        "rows_written": summary.rows_written,
        "event": to_jsonable(event_row),
        "results": to_jsonable([result.__dict__ for result in summary.results]),
    }


@router.get("/events/{event_id}/markets")
def get_event_markets(
    event_id: UUID,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                market_id,
                event_id,
                question,
                market_type,
                condition_id,
                market_slug,
                open_time,
                close_time,
                settled_time,
                settlement_status,
                metadata_json,
                created_at,
                updated_at
            FROM catalog.markets
            WHERE event_id = %s
            ORDER BY created_at ASC;
            """,
            (str(event_id),),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.get("/markets/{market_id}")
def get_market(
    market_id: UUID,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                market_id,
                event_id,
                question,
                market_type,
                condition_id,
                market_slug,
                open_time,
                close_time,
                settled_time,
                settlement_status,
                metadata_json,
                created_at,
                updated_at
            FROM catalog.markets
            WHERE market_id = %s;
            """,
            (str(market_id),),
        )
        row = fetchone_dict(cursor)
    if row is None:
        raise HTTPException(status_code=404, detail="Market not found")
    return to_jsonable(row)


@router.get("/markets/{market_id}/outcomes")
def get_market_outcomes(
    market_id: UUID,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                outcome_id,
                market_id,
                outcome_index,
                outcome_label,
                token_id,
                is_winner,
                metadata_json,
                created_at,
                updated_at
            FROM catalog.outcomes
            WHERE market_id = %s
            ORDER BY outcome_index ASC;
            """,
            (str(market_id),),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.get("/outcomes/{outcome_id}")
def get_outcome(
    outcome_id: UUID,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                outcome_id,
                market_id,
                outcome_index,
                outcome_label,
                token_id,
                is_winner,
                metadata_json,
                created_at,
                updated_at
            FROM catalog.outcomes
            WHERE outcome_id = %s;
            """,
            (str(outcome_id),),
        )
        row = fetchone_dict(cursor)
    if row is None:
        raise HTTPException(status_code=404, detail="Outcome not found")
    return to_jsonable(row)


@router.get("/outcomes/by-token/{token_id}")
def get_outcome_by_token(
    token_id: str,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                outcome_id,
                market_id,
                outcome_index,
                outcome_label,
                token_id,
                is_winner,
                metadata_json,
                created_at,
                updated_at
            FROM catalog.outcomes
            WHERE token_id = %s
            LIMIT 1;
            """,
            (token_id,),
        )
        row = fetchone_dict(cursor)
    if row is None:
        raise HTTPException(status_code=404, detail="Outcome not found for token_id")
    return to_jsonable(row)
