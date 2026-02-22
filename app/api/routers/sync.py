from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg2.extras import Json
from psycopg2.extensions import connection as PsycopgConnection

from app.api.db import cursor_dict, fetchall_dicts, to_jsonable
from app.api.dependencies import get_db_connection
from app.api.jobs import ensure_api_sync_job_definition, insert_job_run
from app.api.models import (
    MappingSyncRequest,
    NbaScheduleSyncRequest,
    NbaSeasonSyncRequest,
    PolymarketSyncRequest,
    SyncTriggerResponse,
)
from app.data.databases.repositories import JanusUpsertRepository
from app.data.databases.seed_packs.polymarket_event_seed_pack import (
    DEFAULT_EXTRA_EVENT_PROBES,
    EventProbeConfig,
    build_today_nba_event_probes_from_scoreboard,
    run_polymarket_event_seed_pack,
)
from app.data.nodes.nba.players.leaguedash_player_base_season import fetch_player_base_df
from app.data.nodes.nba.teams.leaguedash_team_advanced_season import fetch_team_advanced_df
from app.data.nodes.nba.teams.leaguedash_team_base_season import fetch_team_base_df
from app.data.nodes.nba.teams.team_recent_form_last5 import compute_last5_metrics_df
from app.data.pipelines.daily.cross_domain.sync_mappings import run_cross_domain_mapping_sync
from app.data.pipelines.daily.nba.sync_postgres import run_nba_metadata_sync


router = APIRouter(prefix="/v1/sync", tags=["sync"])


def _slug_from_probe(probe: EventProbeConfig) -> str:
    return probe.url.rstrip("/").split("/")[-1]


def _select_polymarket_probes(
    request: PolymarketSyncRequest,
    connection: PsycopgConnection,
) -> list[EventProbeConfig]:
    selected: list[EventProbeConfig] = []
    if request.probe_set in {"extras", "combined"}:
        selected.extend(DEFAULT_EXTRA_EVENT_PROBES)
    if request.probe_set in {"today_nba", "combined"}:
        today = build_today_nba_event_probes_from_scoreboard(
            max_finished=request.max_finished,
            max_live=request.max_live,
            max_upcoming=request.max_upcoming,
            include_upcoming=request.include_upcoming,
            stream_sample_count=request.stream_sample_count,
            stream_sample_interval_sec=request.stream_sample_interval_sec,
            stream_max_outcomes=request.stream_max_outcomes,
        )
        selected.extend(today.all)

    if request.steps:
        wanted = set(request.steps)
        selected = [item for item in selected if item.step_code in wanted]

    if request.missing_only and selected:
        slugs = [_slug_from_probe(item) for item in selected]
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT canonical_slug
                FROM catalog.events
                WHERE canonical_slug = ANY(%s);
                """,
                (slugs,),
            )
            existing = {str(row[0]) for row in cursor.fetchall()}
        selected = [item for item in selected if _slug_from_probe(item) not in existing]

    return selected


def _create_manual_sync_run(
    connection: PsycopgConnection,
    *,
    provider_code: str,
    provider_name: str,
    module_code: str,
    module_name: str,
    pipeline_name: str,
    meta_json: dict[str, Any],
) -> str:
    repo = JanusUpsertRepository(connection)
    provider_id = repo.upsert_provider(
        provider_id=str(uuid4()),
        code=provider_code,
        name=provider_name,
        category="sports_data",
        base_url=None,
        auth_type="none",
    )
    module_id = repo.upsert_module(
        module_id=str(uuid4()),
        code=module_code,
        name=module_name,
        description=pipeline_name,
        owner="janus",
        is_active=True,
    )
    sync_run_id = str(uuid4())
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO core.sync_runs (
                sync_run_id, provider_id, module_id, pipeline_name, run_type, status, started_at, meta_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                sync_run_id,
                provider_id,
                module_id,
                pipeline_name,
                "manual_api_trigger",
                "running",
                datetime.now(timezone.utc),
                Json(meta_json),
            ),
        )
    return sync_run_id


def _finalize_manual_sync_run(
    connection: PsycopgConnection,
    *,
    sync_run_id: str,
    status: str,
    rows_read: int,
    rows_written: int,
    error_text: str | None,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE core.sync_runs
            SET status = %s,
                ended_at = %s,
                rows_read = %s,
                rows_written = %s,
                error_text = %s
            WHERE sync_run_id = %s;
            """,
            (status, datetime.now(timezone.utc), rows_read, rows_written, error_text, sync_run_id),
        )


def _run_nba_team_stats_sync(
    connection: PsycopgConnection,
    *,
    season: str,
) -> dict[str, Any]:
    sync_run_id = _create_manual_sync_run(
        connection,
        provider_code="nba_stats_api",
        provider_name="NBA Stats API",
        module_code="nba_team_stats_sync",
        module_name="NBA Team Stats Sync",
        pipeline_name="api.sync.nba.teams",
        meta_json={"season": season},
    )
    now = datetime.now(timezone.utc)
    rows_read = 0
    rows_written = 0
    error_text: str | None = None
    run_status = "success"
    try:
        base_df = fetch_team_base_df(season=season)
        advanced_df = fetch_team_advanced_df(season=season)
        rows_read += int(len(base_df) + len(advanced_df))

        repo = JanusUpsertRepository(connection)
        for metric_set, frame in (("base", base_df), ("advanced", advanced_df)):
            for row in frame.to_dict(orient="records"):
                team_id_raw = row.get("team_id")
                if team_id_raw is None:
                    continue
                team_id = int(team_id_raw)
                team_slug = str(row.get("team_slug") or f"T{team_id}").upper()
                team_name = str(row.get("team_name") or team_slug)
                repo.upsert_nba_team(
                    team_id=team_id,
                    team_slug=team_slug,
                    team_name=team_name,
                )
                inserted = repo.insert_nba_team_stats_snapshot(
                    team_id=team_id,
                    season=season,
                    captured_at=now,
                    metric_set=metric_set,
                    stats_json=row,
                    source=f"nba_stats_{metric_set}",
                    ignore_duplicates=True,
                )
                if inserted:
                    rows_written += 1
    except Exception as exc:  # noqa: BLE001
        run_status = "error"
        error_text = repr(exc)

    _finalize_manual_sync_run(
        connection,
        sync_run_id=sync_run_id,
        status=run_status,
        rows_read=rows_read,
        rows_written=rows_written,
        error_text=error_text,
    )
    return {
        "sync_run_id": sync_run_id,
        "status": run_status,
        "rows_read": rows_read,
        "rows_written": rows_written,
        "error_text": error_text,
    }


def _run_nba_player_stats_sync(
    connection: PsycopgConnection,
    *,
    season: str,
) -> dict[str, Any]:
    sync_run_id = _create_manual_sync_run(
        connection,
        provider_code="nba_stats_api",
        provider_name="NBA Stats API",
        module_code="nba_player_stats_sync",
        module_name="NBA Player Stats Sync",
        pipeline_name="api.sync.nba.players",
        meta_json={"season": season},
    )
    now = datetime.now(timezone.utc)
    rows_read = 0
    rows_written = 0
    error_text: str | None = None
    run_status = "success"
    try:
        base_df = fetch_player_base_df(season=season)
        rows_read += int(len(base_df))

        repo = JanusUpsertRepository(connection)
        for row in base_df.to_dict(orient="records"):
            team_id_raw = row.get("team_id")
            team_id = int(team_id_raw) if team_id_raw is not None else None
            team_slug = str(row.get("team_slug") or "").upper().strip()
            if team_id is not None and team_slug:
                repo.upsert_nba_team(
                    team_id=team_id,
                    team_slug=team_slug,
                    team_name=team_slug,
                )
            player_id_raw = row.get("player_nba_id")
            if player_id_raw is None:
                continue
            inserted = repo.insert_nba_player_stats_snapshot(
                player_id=int(player_id_raw),
                player_name=str(row.get("player_name") or "") or None,
                team_id=team_id,
                season=season,
                captured_at=now,
                metric_set="base",
                stats_json=row,
                source="nba_players_base",
                ignore_duplicates=True,
            )
            if inserted:
                rows_written += 1
    except Exception as exc:  # noqa: BLE001
        run_status = "error"
        error_text = repr(exc)

    _finalize_manual_sync_run(
        connection,
        sync_run_id=sync_run_id,
        status=run_status,
        rows_read=rows_read,
        rows_written=rows_written,
        error_text=error_text,
    )
    return {
        "sync_run_id": sync_run_id,
        "status": run_status,
        "rows_read": rows_read,
        "rows_written": rows_written,
        "error_text": error_text,
    }


def _run_nba_team_insights_sync(
    connection: PsycopgConnection,
    *,
    season: str,
) -> dict[str, Any]:
    sync_run_id = _create_manual_sync_run(
        connection,
        provider_code="nba_stats_api",
        provider_name="NBA Stats API",
        module_code="nba_team_insights_sync",
        module_name="NBA Team Insights Sync",
        pipeline_name="api.sync.nba.insights",
        meta_json={"season": season},
    )
    now = datetime.now(timezone.utc)
    rows_read = 0
    rows_written = 0
    error_text: str | None = None
    run_status = "success"
    try:
        insights_df = compute_last5_metrics_df(season=season)
        rows_read += int(len(insights_df))
        repo = JanusUpsertRepository(connection)
        for row in insights_df.to_dict(orient="records"):
            team_id_raw = row.get("team_id")
            if team_id_raw is None:
                continue
            team_id = int(team_id_raw)
            team_slug = str(row.get("team_slug") or f"T{team_id}").upper()
            team_name = str(row.get("team_name") or team_slug)
            repo.upsert_nba_team(team_id=team_id, team_slug=team_slug, team_name=team_name)

            insight_id = str(uuid4())
            inserted = repo.insert_nba_team_insight(
                insight_id=insight_id,
                team_id=team_id,
                captured_at=now,
                insight_type="recent_form_last5",
                category="performance",
                text=(
                    f"Last 5 win rate {row.get('last_5_games_win_rate')} "
                    f"with avg points {row.get('last_5_avg_points')}"
                ),
                condition="last_5_win_rate",
                value=str(row.get("last_5_games_win_rate")),
                source="nba_team_recent_form_last5",
                ignore_duplicates=True,
            )
            if inserted:
                rows_written += 1
    except Exception as exc:  # noqa: BLE001
        run_status = "error"
        error_text = repr(exc)

    _finalize_manual_sync_run(
        connection,
        sync_run_id=sync_run_id,
        status=run_status,
        rows_read=rows_read,
        rows_written=rows_written,
        error_text=error_text,
    )
    return {
        "sync_run_id": sync_run_id,
        "status": run_status,
        "rows_read": rows_read,
        "rows_written": rows_written,
        "error_text": error_text,
    }


def _with_job_run(
    connection: PsycopgConnection,
    *,
    job_code: str,
    description: str,
    runner: Callable[[], dict[str, Any]],
) -> SyncTriggerResponse:
    started_at = datetime.now(timezone.utc)
    summary = runner()
    ended_at = datetime.now(timezone.utc)

    sync_run_id_value = summary.get("sync_run_id")
    if sync_run_id_value:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM core.sync_runs WHERE sync_run_id = %s;",
                (str(sync_run_id_value),),
            )
            if cursor.fetchone() is None:
                sync_run_id_value = None

    job_id = ensure_api_sync_job_definition(connection, job_code=job_code, description=description)
    job_run_id = insert_job_run(
        connection,
        job_id=job_id,
        sync_run_id=sync_run_id_value,
        status=str(summary.get("status") or "unknown"),
        started_at=started_at,
        ended_at=ended_at,
        error_text=summary.get("error_text"),
        metrics={
            "rows_read": summary.get("rows_read"),
            "rows_written": summary.get("rows_written"),
        },
    )
    return SyncTriggerResponse(
        job_run_id=UUID(job_run_id),
        sync_run_id=UUID(str(sync_run_id_value)) if sync_run_id_value else None,
        status=str(summary.get("status") or "unknown"),
        rows_read=summary.get("rows_read"),
        rows_written=summary.get("rows_written"),
        summary=to_jsonable(summary),
    )


@router.post(
    "/polymarket/events",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_polymarket_events(
    payload: PolymarketSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    probes = _select_polymarket_probes(payload, connection)
    if not probes:
        raise HTTPException(status_code=400, detail="No probes selected.")

    def _runner() -> dict[str, Any]:
        summary = run_polymarket_event_seed_pack(probes, persist=True)
        return {
            "sync_run_id": summary.sync_run_id,
            "status": summary.status,
            "rows_read": summary.rows_read,
            "rows_written": summary.rows_written,
            "results": [result.__dict__ for result in summary.results],
        }

    return _with_job_run(
        connection,
        job_code="sync_polymarket_events",
        description="Sync polymarket events payload into catalog graph",
        runner=_runner,
    )


@router.post(
    "/polymarket/markets",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_polymarket_markets(
    payload: PolymarketSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    probes = _select_polymarket_probes(payload, connection)
    if not probes:
        raise HTTPException(status_code=400, detail="No probes selected.")

    def _runner() -> dict[str, Any]:
        summary = run_polymarket_event_seed_pack(probes, persist=True)
        return {
            "sync_run_id": summary.sync_run_id,
            "status": summary.status,
            "rows_read": summary.rows_read,
            "rows_written": summary.rows_written,
            "results": [result.__dict__ for result in summary.results],
        }

    return _with_job_run(
        connection,
        job_code="sync_polymarket_markets",
        description="Sync polymarket markets/outcomes/state snapshots",
        runner=_runner,
    )


@router.post(
    "/nba/schedule",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_nba_schedule(
    payload: NbaScheduleSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    def _runner() -> dict[str, Any]:
        summary = run_nba_metadata_sync(
            season=payload.season,
            schedule_window_days=payload.schedule_window_days,
            include_live_snapshots=payload.include_live_snapshots,
            include_play_by_play=payload.include_play_by_play,
        )
        return to_jsonable(summary.__dict__)

    return _with_job_run(
        connection,
        job_code="sync_nba_schedule",
        description="Sync nba schedule + scoreboard metadata",
        runner=_runner,
    )


@router.post(
    "/nba/teams",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_nba_teams(
    payload: NbaSeasonSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    def _runner() -> dict[str, Any]:
        return _run_nba_team_stats_sync(connection, season=payload.season)

    return _with_job_run(
        connection,
        job_code="sync_nba_teams",
        description="Sync nba team stats snapshots",
        runner=_runner,
    )


@router.post(
    "/nba/players",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_nba_players(
    payload: NbaSeasonSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    def _runner() -> dict[str, Any]:
        return _run_nba_player_stats_sync(connection, season=payload.season)

    return _with_job_run(
        connection,
        job_code="sync_nba_players",
        description="Sync nba player stats snapshots",
        runner=_runner,
    )


@router.post(
    "/nba/insights",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_nba_insights(
    payload: NbaSeasonSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    def _runner() -> dict[str, Any]:
        return _run_nba_team_insights_sync(connection, season=payload.season)

    return _with_job_run(
        connection,
        job_code="sync_nba_insights",
        description="Sync nba insight rows",
        runner=_runner,
    )


@router.post(
    "/nba/mappings",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
def sync_nba_mappings(
    payload: MappingSyncRequest,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> SyncTriggerResponse:
    def _runner() -> dict[str, Any]:
        summary = run_cross_domain_mapping_sync(
            lookback_days=payload.lookback_days,
            lookahead_days=payload.lookahead_days,
        )
        return to_jsonable(summary.__dict__)

    return _with_job_run(
        connection,
        job_code="sync_nba_mappings",
        description="Sync nba<->catalog mappings and information scores",
        runner=_runner,
    )


@router.get("/jobs/runs")
def list_job_runs(
    limit: int = 200,
    connection: PsycopgConnection = Depends(get_db_connection),
) -> dict[str, Any]:
    with cursor_dict(connection) as cursor:
        cursor.execute(
            """
            SELECT
                jr.job_run_id,
                jd.job_code,
                jr.sync_run_id,
                jr.started_at,
                jr.ended_at,
                jr.status,
                jr.error_text,
                jr.metrics_json
            FROM ops.job_runs jr
            JOIN ops.job_definitions jd ON jd.job_id = jr.job_id
            ORDER BY jr.started_at DESC
            LIMIT %s;
            """,
            (limit,),
        )
        rows = fetchall_dicts(cursor)
    return {"items": to_jsonable(rows), "count": len(rows)}
