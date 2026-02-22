from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from psycopg2.extras import Json
from psycopg2.extensions import connection as PsycopgConnection

from app.data.databases.repositories import JanusUpsertRepository


_NAMESPACE = uuid.UUID("249f04e2-f3e2-478b-a574-579f84514224")


def _uuid_for(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, "|".join(parts)))


def ensure_api_sync_job_definition(
    connection: PsycopgConnection,
    *,
    job_code: str,
    description: str,
) -> str:
    repo = JanusUpsertRepository(connection)
    module_id = repo.upsert_module(
        module_id=_uuid_for("module", "api_sync_triggers"),
        code="api_sync_triggers",
        name="API Sync Triggers",
        description="FastAPI trigger routes for sync pipelines",
        owner="janus",
        is_active=True,
    )

    job_id = _uuid_for("job", job_code)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ops.job_definitions (
                job_id, module_id, job_code, description, schedule_cron, is_enabled
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (job_code)
            DO UPDATE SET
                module_id = EXCLUDED.module_id,
                description = EXCLUDED.description,
                is_enabled = EXCLUDED.is_enabled
            RETURNING job_id;
            """,
            (job_id, module_id, job_code, description, None, True),
        )
        row = cursor.fetchone()
    return str(row[0])


def insert_job_run(
    connection: PsycopgConnection,
    *,
    job_id: str,
    sync_run_id: str | None,
    status: str,
    started_at: datetime,
    ended_at: datetime,
    error_text: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> str:
    job_run_id = str(uuid.uuid4())
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ops.job_runs (
                job_run_id, job_id, sync_run_id, started_at, ended_at, status, error_text, metrics_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING job_run_id;
            """,
            (
                job_run_id,
                job_id,
                sync_run_id,
                started_at.astimezone(timezone.utc),
                ended_at.astimezone(timezone.utc),
                status,
                error_text,
                Json(metrics) if metrics is not None else None,
            ),
        )
        row = cursor.fetchone()
    return str(row[0])
