from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from psycopg2.extras import Json
from psycopg2.extensions import connection as PsycopgConnection

from app.api.db import cursor_dict, fetchall_dicts, fetchone_dict, to_jsonable
from app.api.dependencies import get_db_connection
from app.api.models import HealthResponse, ModuleCreateRequest, ProviderCreateRequest
from app.data.databases.repositories import JanusUpsertRepository


router = APIRouter(prefix="/v1", tags=["system"])


@router.get("/health", response_model=HealthResponse)
def get_health(
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ops.system_heartbeats (service_name, status, last_heartbeat, message)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (service_name)
            DO UPDATE SET
                status = EXCLUDED.status,
                last_heartbeat = EXCLUDED.last_heartbeat,
                message = EXCLUDED.message;
            """,
            ("fastapi", "ok", now, "FastAPI process healthy"),
        )

    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT service_name, status, last_heartbeat, message
            FROM ops.system_heartbeats
            ORDER BY service_name;
            """
        )
        services = fetchall_dicts(cursor)

    overall_status = "ok"
    if any(str(item.get("status", "")).lower() != "ok" for item in services):
        overall_status = "degraded"

    return {
        "status": overall_status,
        "database": "ok",
        "timestamp": now,
        "services": to_jsonable(services),
    }


@router.get("/providers")
def list_providers(
    is_active: bool | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    conditions: list[str] = []
    params: list[Any] = []
    if is_active is not None:
        conditions.append("is_active = %s")
        params.append(is_active)

    where_sql = ""
    if conditions:
        where_sql = f"WHERE {' AND '.join(conditions)}"
    params.extend([limit, offset])

    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT provider_id, code, name, category, base_url, auth_type, is_active, created_at, updated_at
            FROM core.providers
            {where_sql}
            ORDER BY code
            LIMIT %s OFFSET %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.post("/providers", status_code=status.HTTP_201_CREATED)
def create_provider(
    payload: ProviderCreateRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    repo = JanusUpsertRepository(connection)
    provider_id = repo.upsert_provider(
        provider_id=str(payload.provider_id or uuid4()),
        code=payload.code.strip(),
        name=payload.name.strip(),
        category=payload.category.strip(),
        base_url=payload.base_url,
        auth_type=payload.auth_type,
        is_active=payload.is_active,
    )
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT provider_id, code, name, category, base_url, auth_type, is_active, created_at, updated_at
            FROM core.providers
            WHERE provider_id = %s;
            """,
            (provider_id,),
        )
        row = fetchone_dict(cursor)
    if row is None:
        raise HTTPException(status_code=500, detail="Provider was not persisted.")
    return to_jsonable(row)


@router.get("/modules")
def list_modules(
    is_active: bool | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    conditions: list[str] = []
    params: list[Any] = []
    if is_active is not None:
        conditions.append("is_active = %s")
        params.append(is_active)

    where_sql = ""
    if conditions:
        where_sql = f"WHERE {' AND '.join(conditions)}"
    params.extend([limit, offset])

    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT module_id, code, name, description, owner, is_active, created_at, updated_at
            FROM core.modules
            {where_sql}
            ORDER BY code
            LIMIT %s OFFSET %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.post("/modules", status_code=status.HTTP_201_CREATED)
def create_module(
    payload: ModuleCreateRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    repo = JanusUpsertRepository(connection)
    module_id = repo.upsert_module(
        module_id=str(payload.module_id or uuid4()),
        code=payload.code.strip(),
        name=payload.name.strip(),
        description=payload.description,
        owner=payload.owner,
        is_active=payload.is_active,
    )
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT module_id, code, name, description, owner, is_active, created_at, updated_at
            FROM core.modules
            WHERE module_id = %s;
            """,
            (module_id,),
        )
        row = fetchone_dict(cursor)
    if row is None:
        raise HTTPException(status_code=500, detail="Module was not persisted.")
    return to_jsonable(row)


@router.get("/sync-runs")
def list_sync_runs(
    status_filter: str | None = Query(default=None, alias="status"),
    pipeline_name: str | None = Query(default=None),
    provider_code: str | None = Query(default=None),
    module_code: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    conditions: list[str] = []
    params: list[Any] = []

    if status_filter:
        conditions.append("sr.status = %s")
        params.append(status_filter)
    if pipeline_name:
        conditions.append("sr.pipeline_name = %s")
        params.append(pipeline_name)
    if provider_code:
        conditions.append("p.code = %s")
        params.append(provider_code)
    if module_code:
        conditions.append("m.code = %s")
        params.append(module_code)

    where_sql = ""
    if conditions:
        where_sql = f"WHERE {' AND '.join(conditions)}"
    params.extend([limit, offset])

    with cursor_dict(connection) as cursor:
        cursor.execute(
            f"""
            SELECT
                sr.sync_run_id,
                sr.pipeline_name,
                sr.run_type,
                sr.status,
                sr.started_at,
                sr.ended_at,
                sr.rows_read,
                sr.rows_written,
                sr.error_text,
                sr.meta_json,
                p.code AS provider_code,
                m.code AS module_code
            FROM core.sync_runs sr
            LEFT JOIN core.providers p ON p.provider_id = sr.provider_id
            LEFT JOIN core.modules m ON m.module_id = sr.module_id
            {where_sql}
            ORDER BY sr.started_at DESC
            LIMIT %s OFFSET %s;
            """,
            tuple(params),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}


@router.get("/sync-runs/{sync_run_id}")
def get_sync_run(
    sync_run_id: UUID,
    include_payloads: bool = Query(default=False),
    payload_limit: int = Query(default=25, ge=1, le=200),
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                sr.sync_run_id,
                sr.pipeline_name,
                sr.run_type,
                sr.status,
                sr.started_at,
                sr.ended_at,
                sr.rows_read,
                sr.rows_written,
                sr.error_text,
                sr.meta_json,
                p.code AS provider_code,
                m.code AS module_code
            FROM core.sync_runs sr
            LEFT JOIN core.providers p ON p.provider_id = sr.provider_id
            LEFT JOIN core.modules m ON m.module_id = sr.module_id
            WHERE sr.sync_run_id = %s;
            """,
            (str(sync_run_id),),
        )
        run_row = fetchone_dict(cursor)
        if run_row is None:
            raise HTTPException(status_code=404, detail="sync_run_id not found")

        cursor.execute(
            """
            SELECT count(*)::int AS payload_count
            FROM core.raw_payloads
            WHERE sync_run_id = %s;
            """,
            (str(sync_run_id),),
        )
        payload_count_row = fetchone_dict(cursor)
        payload_count = int(payload_count_row["payload_count"]) if payload_count_row else 0

        payloads: list[dict[str, Any]] = []
        if include_payloads:
            cursor.execute(
                """
                SELECT raw_payload_id, endpoint, external_id, fetched_at, payload_json
                FROM core.raw_payloads
                WHERE sync_run_id = %s
                ORDER BY fetched_at DESC
                LIMIT %s;
                """,
                (str(sync_run_id), payload_limit),
            )
            payloads = fetchall_dicts(cursor)

    return {
        "sync_run": to_jsonable(run_row),
        "raw_payload_count": payload_count,
        "raw_payloads": to_jsonable(payloads),
    }
