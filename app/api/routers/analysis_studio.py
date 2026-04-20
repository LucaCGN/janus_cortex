from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from app.data.pipelines.daily.nba.analysis.consumer_adapters import (
    list_available_analysis_versions,
    load_analysis_consumer_snapshot,
)
from app.data.pipelines.daily.nba.analysis.contracts import (
    ANALYSIS_VERSION,
    DEFAULT_LOCAL_ROOT_ENV_VAR,
    DEFAULT_SEASON,
    DEFAULT_SEASON_PHASE,
    WINDOWS_LOCAL_ROOT,
    AnalysisConsumerRequest,
    resolve_default_output_root,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_STUDIO_ROOT = REPO_ROOT / "frontend" / "analysis_studio"
ANALYSIS_STUDIO_STATIC_ROOT = ANALYSIS_STUDIO_ROOT / "static"
ANALYSIS_STUDIO_INDEX_PATH = ANALYSIS_STUDIO_ROOT / "index.html"
VALIDATION_ROOT_SUFFIX = Path("archives") / "output" / "nba_analysis_validation"
STUDIO_RUN_ROOT_SUFFIX = Path("archives") / "output" / "nba_analysis_studio_runs"

_RUN_LOCK = threading.Lock()
_RUN_REGISTRY: dict[str, dict[str, Any]] = {}

router = APIRouter(tags=["analysis-studio"])


class AnalysisStudioRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "run_analysis_validation",
        "build_analysis_mart",
        "build_analysis_report",
        "run_analysis_backtests",
        "train_analysis_baselines",
    ]
    season: str = DEFAULT_SEASON
    season_phase: str = DEFAULT_SEASON_PHASE
    analysis_version: str = ANALYSIS_VERSION
    validation_target: Literal["disposable", "dev_clone"] = "disposable"
    rebuild: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_local_root() -> Path:
    env_value = os.getenv(DEFAULT_LOCAL_ROOT_ENV_VAR)
    if env_value:
        return Path(env_value)
    if WINDOWS_LOCAL_ROOT.exists():
        return WINDOWS_LOCAL_ROOT
    return REPO_ROOT / "janus_local"


def _ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_validation_root() -> Path:
    return _ensure_directory(_resolve_local_root() / VALIDATION_ROOT_SUFFIX)


def _resolve_studio_run_root() -> Path:
    return _ensure_directory(_resolve_local_root() / STUDIO_RUN_ROOT_SUFFIX)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def _sorted_summary_dirs(root: Path) -> list[Path]:
    candidates = [path for path in root.iterdir() if path.is_dir() and (path / "validation_summary.json").exists()]
    return sorted(candidates, key=lambda path: path.name, reverse=True)


def _build_validation_summary_card(summary_dir: Path) -> dict[str, Any]:
    payload = _read_json(summary_dir / "validation_summary.json") or {}
    parsed_outputs = payload.get("parsed_outputs") or {}
    snapshot = parsed_outputs.get("collect_validation_snapshot") or {}
    consumer_snapshot = snapshot.get("consumer_snapshot") or {}
    universe = snapshot.get("universe") or {}
    commands = payload.get("commands") or []
    return {
        "run_label": summary_dir.name,
        "target": payload.get("target"),
        "season": payload.get("season"),
        "season_phase": payload.get("season_phase"),
        "analysis_version": payload.get("analysis_version"),
        "all_commands_ok": bool(payload.get("all_commands_ok")),
        "output_root": payload.get("output_root"),
        "summary_json": str(summary_dir / "validation_summary.json"),
        "summary_markdown": str(summary_dir / "validation_summary.md"),
        "command_count": len(commands),
        "commands": [
            {
                "name": row.get("name"),
                "ok": row.get("ok"),
                "exit_code": row.get("exit_code"),
                "duration_seconds": row.get("duration_seconds"),
            }
            for row in commands
        ],
        "database_target": snapshot.get("database_target") or {},
        "consumer_snapshot": consumer_snapshot,
        "universe": {
            "games_total": universe.get("games_total"),
            "research_ready_games": universe.get("research_ready_games"),
            "descriptive_only_games": universe.get("descriptive_only_games"),
            "excluded_games": universe.get("excluded_games"),
        },
    }


def _list_recent_validation_summaries(limit: int = 5) -> list[dict[str, Any]]:
    root = _resolve_validation_root()
    if not root.exists():
        return []
    return [_build_validation_summary_card(path) for path in _sorted_summary_dirs(root)[:limit]]


def _list_run_records(limit: int = 20) -> list[dict[str, Any]]:
    with _RUN_LOCK:
        records = list(_RUN_REGISTRY.values())
    return sorted(records, key=lambda row: row["created_at"], reverse=True)[:limit]


def _store_run_record(record: dict[str, Any]) -> dict[str, Any]:
    with _RUN_LOCK:
        _RUN_REGISTRY[record["run_id"]] = record
    return record


def _update_run_record(run_id: str, **updates: Any) -> None:
    with _RUN_LOCK:
        record = _RUN_REGISTRY.get(run_id)
        if record is None:
            return
        record.update(updates)


def _resolve_run_command(request: AnalysisStudioRunRequest, run_root: Path) -> tuple[str, list[str], Path]:
    if request.action == "run_analysis_validation":
        output_root = run_root / "validation_output"
        args = [
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO_ROOT / "tools" / "run_analysis_validation.ps1"),
            "-Target",
            request.validation_target,
            "-Season",
            request.season,
            "-SeasonPhase",
            request.season_phase,
            "-AnalysisVersion",
            request.analysis_version,
            "-OutputRoot",
            str(output_root),
        ]
        if request.rebuild:
            args.append("-RebuildMart")
        return "powershell", args, output_root

    output_root = run_root / "analysis_output"
    args = [
        "-m",
        "app.data.pipelines.daily.nba.analysis_module",
        request.action,
        "--season",
        request.season,
        "--season-phase",
        request.season_phase,
        "--analysis-version",
        request.analysis_version,
        "--output-root",
        str(output_root),
    ]
    if request.action == "build_analysis_mart" and request.rebuild:
        args.append("--rebuild")
    return "python", args, output_root


def _finalize_run_record(run_id: str, return_code: int, output_root: Path) -> None:
    record: dict[str, Any] | None = None
    with _RUN_LOCK:
        record = _RUN_REGISTRY.get(run_id)
    if record is None:
        return
    action = str(record.get("action") or "")
    result_paths: dict[str, str] = {"output_root": str(output_root)}
    if action == "run_analysis_validation":
        result_paths["summary_json"] = str(output_root / "validation_summary.json")
        result_paths["summary_markdown"] = str(output_root / "validation_summary.md")
        result_paths["analysis_output_dir"] = str(
            output_root / record["season"] / record["season_phase"] / record["analysis_version"]
        )
    else:
        result_paths["analysis_output_dir"] = str(
            output_root / record["season"] / record["season_phase"] / record["analysis_version"]
        )

    _update_run_record(
        run_id,
        status="succeeded" if return_code == 0 else "failed",
        ended_at=_now_iso(),
        return_code=return_code,
        result_paths=result_paths,
    )


def _launch_analysis_studio_run(record: dict[str, Any], request: AnalysisStudioRunRequest) -> None:
    run_root = Path(record["run_root"])
    stdout_path = Path(record["stdout_path"])
    stderr_path = Path(record["stderr_path"])
    executable, args, output_root = _resolve_run_command(request, run_root)
    _ensure_directory(output_root)

    stdout_handle = stdout_path.open("w", encoding="utf-8")
    stderr_handle = stderr_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [executable, *args],
        cwd=str(REPO_ROOT),
        stdout=stdout_handle,
        stderr=stderr_handle,
    )
    _update_run_record(
        record["run_id"],
        status="running",
        started_at=_now_iso(),
        pid=process.pid,
        command=[executable, *args],
        output_root=str(output_root),
    )

    def _monitor() -> None:
        try:
            return_code = process.wait()
        finally:
            stdout_handle.close()
            stderr_handle.close()
        _finalize_run_record(record["run_id"], return_code, output_root)

    threading.Thread(target=_monitor, daemon=True).start()


def _create_run_record(request: AnalysisStudioRunRequest) -> dict[str, Any]:
    run_id = uuid.uuid4().hex
    label = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_root = _ensure_directory(_resolve_studio_run_root() / f"{label}_{request.action}_{run_id[:8]}")
    logs_root = _ensure_directory(run_root / "logs")
    return _store_run_record(
        {
            "run_id": run_id,
            "action": request.action,
            "status": "queued",
            "season": request.season,
            "season_phase": request.season_phase,
            "analysis_version": request.analysis_version,
            "validation_target": request.validation_target if request.action == "run_analysis_validation" else None,
            "rebuild": request.rebuild,
            "created_at": _now_iso(),
            "started_at": None,
            "ended_at": None,
            "return_code": None,
            "pid": None,
            "command": [],
            "run_root": str(run_root),
            "stdout_path": str(logs_root / "stdout.log"),
            "stderr_path": str(logs_root / "stderr.log"),
            "output_root": None,
            "result_paths": {},
        }
    )


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


@router.get("/v1/analysis/studio/control")
def get_analysis_studio_control(
    season: str = Query(default=DEFAULT_SEASON),
    season_phase: str = Query(default=DEFAULT_SEASON_PHASE),
) -> dict[str, Any]:
    output_root = resolve_default_output_root()
    validations = _list_recent_validation_summaries()
    versions = list_available_analysis_versions(
        season=season,
        season_phase=season_phase,
        output_root=str(output_root),
    )
    latest_version = versions[-1] if versions else None
    latest_output_dir = str(output_root / season / season_phase / latest_version) if latest_version else None
    return {
        "repo_root": str(REPO_ROOT),
        "local_root": str(_resolve_local_root()),
        "default_output_root": str(output_root),
        "season": season,
        "season_phase": season_phase,
        "available_analysis_versions": versions,
        "latest_analysis_output_dir": latest_output_dir,
        "latest_validation": validations[0] if validations else None,
        "recent_validations": validations,
        "recent_runs": _list_run_records(),
    }


@router.get("/v1/analysis/studio/runs")
def list_analysis_studio_runs() -> dict[str, Any]:
    items = _list_run_records()
    return {"items": items, "count": len(items)}


@router.get("/v1/analysis/studio/runs/{run_id}")
def get_analysis_studio_run(run_id: str) -> dict[str, Any]:
    with _RUN_LOCK:
        record = _RUN_REGISTRY.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="analysis studio run not found")
    return record


@router.post("/v1/analysis/studio/runs")
def create_analysis_studio_run(request: AnalysisStudioRunRequest) -> dict[str, Any]:
    record = _create_run_record(request)
    _launch_analysis_studio_run(record, request)
    with _RUN_LOCK:
        created = dict(_RUN_REGISTRY[record["run_id"]])
    return created


__all__ = [
    "ANALYSIS_STUDIO_INDEX_PATH",
    "ANALYSIS_STUDIO_ROOT",
    "ANALYSIS_STUDIO_STATIC_ROOT",
    "AnalysisStudioRunRequest",
    "router",
]
