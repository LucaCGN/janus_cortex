from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.data.pipelines.daily.nba.analysis.consumer_adapters import load_analysis_consumer_snapshot
from app.data.pipelines.daily.nba.analysis.contracts import AnalysisConsumerRequest


REPO_ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_STUDIO_ROOT = REPO_ROOT / "frontend" / "analysis_studio"
ANALYSIS_STUDIO_STATIC_ROOT = ANALYSIS_STUDIO_ROOT / "static"
ANALYSIS_STUDIO_INDEX_PATH = ANALYSIS_STUDIO_ROOT / "index.html"

router = APIRouter(tags=["analysis-studio"])


@router.get("/analysis-studio", include_in_schema=False)
def get_analysis_studio_index() -> FileResponse:
    if not ANALYSIS_STUDIO_INDEX_PATH.exists():
        raise HTTPException(status_code=404, detail="analysis studio frontend is not available")
    return FileResponse(ANALYSIS_STUDIO_INDEX_PATH)


@router.get("/v1/analysis/studio/snapshot")
def get_analysis_studio_snapshot(
    season: str = Query(...),
    season_phase: str = Query(...),
    analysis_version: str | None = Query(default=None),
    backtest_experiment_id: str | None = Query(default=None),
    output_root: str | None = Query(default=None),
) -> dict[str, Any]:
    request = AnalysisConsumerRequest(
        season=season,
        season_phase=season_phase,
        analysis_version=analysis_version,
        backtest_experiment_id=backtest_experiment_id,
        output_root=output_root,
    )
    try:
        return load_analysis_consumer_snapshot(request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


__all__ = [
    "ANALYSIS_STUDIO_INDEX_PATH",
    "ANALYSIS_STUDIO_ROOT",
    "ANALYSIS_STUDIO_STATIC_ROOT",
    "router",
]
