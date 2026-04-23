from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.modules.nba.execution import LiveRunCreateRequest, get_live_run_service


REPO_ROOT = Path(__file__).resolve().parents[3]
LIVE_CONTROL_ROOT = REPO_ROOT / "frontend" / "live_control"
LIVE_CONTROL_STATIC_ROOT = LIVE_CONTROL_ROOT / "static"
LIVE_CONTROL_INDEX_PATH = LIVE_CONTROL_ROOT / "index.html"

router = APIRouter(tags=["nba-live"])


@router.get("/live-control", include_in_schema=False)
def get_live_control_page() -> FileResponse:
    if not LIVE_CONTROL_INDEX_PATH.exists():
        raise HTTPException(status_code=404, detail="live-control UI not found")
    return FileResponse(LIVE_CONTROL_INDEX_PATH)


@router.post("/v1/nba/live/runs")
def create_live_run(payload: LiveRunCreateRequest) -> dict[str, Any]:
    return get_live_run_service().start_or_resume_run(payload)


@router.get("/v1/nba/live/runs/{run_id}")
def get_live_run(run_id: str) -> dict[str, Any]:
    try:
        return get_live_run_service().get_run_summary(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/v1/nba/live/runs/{run_id}/games")
def get_live_run_games(run_id: str) -> dict[str, Any]:
    try:
        return get_live_run_service().get_run_games(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/v1/nba/live/runs/{run_id}/orders")
def get_live_run_orders(run_id: str) -> dict[str, Any]:
    try:
        return get_live_run_service().get_run_orders(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/v1/nba/live/runs/{run_id}/events")
def get_live_run_events(run_id: str) -> dict[str, Any]:
    try:
        return get_live_run_service().get_run_events(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/v1/nba/live/runs/{run_id}/summary")
def get_live_run_summary_cards(run_id: str) -> dict[str, Any]:
    try:
        return get_live_run_service().get_run_summary_cards(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/v1/nba/live/runs/{run_id}/pause-entries")
def pause_live_run_entries(run_id: str) -> dict[str, Any]:
    try:
        return get_live_run_service().pause_entries(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/v1/nba/live/runs/{run_id}/resume-entries")
def resume_live_run_entries(run_id: str) -> dict[str, Any]:
    try:
        return get_live_run_service().resume_entries(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/v1/nba/live/runs/{run_id}/stop")
def stop_live_run(run_id: str) -> dict[str, Any]:
    try:
        return get_live_run_service().stop_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


__all__ = [
    "LIVE_CONTROL_INDEX_PATH",
    "LIVE_CONTROL_STATIC_ROOT",
    "router",
]
