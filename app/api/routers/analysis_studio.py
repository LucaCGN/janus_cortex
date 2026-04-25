from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from app.data.pipelines.daily.nba.analysis.consumer_adapters import (
    list_available_analysis_versions,
    load_analysis_backtest_family_detail,
    load_analysis_backtest_index,
    load_analysis_consumer_snapshot,
)
from app.data.pipelines.daily.nba.analysis.benchmark_integration import (
    UnifiedBenchmarkRequest,
    build_unified_benchmark_dashboard,
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

GAME_EXPLORER_PROFILE_COLUMNS = [
    "game_id",
    "team_side",
    "team_slug",
    "opponent_team_slug",
    "season",
    "season_phase",
    "analysis_version",
    "game_date",
    "game_start_time",
    "coverage_status",
    "research_ready_flag",
    "price_path_reconciled_flag",
    "final_winner_flag",
    "opening_price",
    "closing_price",
    "opening_band",
    "total_swing",
    "inversion_count",
    "max_favorable_excursion",
    "max_adverse_excursion",
    "winner_stable_80_clock_elapsed_seconds",
]
GAME_EXPLORER_STATE_COLUMNS = [
    "game_id",
    "team_side",
    "team_slug",
    "opponent_team_slug",
    "state_index",
    "event_at",
    "period",
    "clock",
    "score_for",
    "score_against",
    "score_diff",
    "context_bucket",
    "team_price",
    "price_delta_from_open",
    "mfe_from_state",
    "mae_from_state",
    "large_swing_next_12_states_flag",
    "crossed_50c_next_12_states_flag",
    "winner_stable_80_after_state_flag",
]

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


def _resolve_analysis_output_base(output_root: str | None) -> Path:
    return Path(output_root) if output_root else resolve_default_output_root()


def _resolve_analysis_output_dir(
    *,
    season: str,
    season_phase: str,
    analysis_version: str | None,
    output_root: str | None,
) -> tuple[Path, str]:
    base = _resolve_analysis_output_base(output_root)
    if analysis_version:
        resolved_version = analysis_version
    else:
        versions = list_available_analysis_versions(
            season=season,
            season_phase=season_phase,
            output_root=str(base),
        )
        if not versions:
            raise FileNotFoundError(f"No analysis output versions found for {season} {season_phase}")
        resolved_version = versions[-1]
    return base / season / season_phase / resolved_version, resolved_version


def _build_consumer_request(
    *,
    season: str,
    season_phase: str,
    analysis_version: str | None,
    backtest_experiment_id: str | None,
    output_root: str | None,
) -> AnalysisConsumerRequest:
    return AnalysisConsumerRequest(
        season=season,
        season_phase=season_phase,
        analysis_version=analysis_version,
        backtest_experiment_id=backtest_experiment_id,
        output_root=output_root,
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None
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


def _read_analysis_frame(
    output_dir: Path,
    stem: str,
    *,
    columns: list[str] | None = None,
    filters: list[tuple[str, str, Any]] | None = None,
) -> pd.DataFrame:
    parquet_path = output_dir / f"{stem}.parquet"
    if parquet_path.exists():
        try:
            return pd.read_parquet(parquet_path, columns=columns, filters=filters)
        except Exception:
            pass

    csv_path = output_dir / f"{stem}.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Required analysis artifact not found: {parquet_path} or {csv_path}")
    frame = pd.read_csv(csv_path, usecols=columns)
    if filters:
        for column, operator, value in filters:
            if operator != "==":
                continue
            frame = frame[frame[column].astype(str) == str(value)]
    return frame


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {key: _clean_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    if isinstance(value, tuple):
        return [_clean_value(item) for item in value]
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    except ValueError:
        pass
    if hasattr(value, "item") and not isinstance(value, (str, bytes, dict, list, tuple, set)):
        try:
            value = value.item()
        except Exception:
            pass
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    return value


def _clean_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: _clean_value(value) for key, value in record.items()}


def _boolish(value: Any) -> bool:
    cleaned = _clean_value(value)
    if isinstance(cleaned, bool):
        return cleaned
    if isinstance(cleaned, str):
        return cleaned.strip().lower() in {"1", "true", "t", "yes", "y"}
    if cleaned is None:
        return False
    return bool(cleaned)


def _profile_summary(profile: dict[str, Any] | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    return _clean_record(
        {
        "team_side": profile.get("team_side"),
        "team_slug": profile.get("team_slug"),
        "opponent_team_slug": profile.get("opponent_team_slug"),
        "coverage_status": profile.get("coverage_status"),
        "research_ready_flag": _boolish(profile.get("research_ready_flag")),
        "price_path_reconciled_flag": _boolish(profile.get("price_path_reconciled_flag")),
        "final_winner_flag": _boolish(profile.get("final_winner_flag")),
        "opening_price": profile.get("opening_price"),
        "closing_price": profile.get("closing_price"),
        "opening_band": profile.get("opening_band"),
        "total_swing": profile.get("total_swing"),
        "inversion_count": profile.get("inversion_count"),
        "max_favorable_excursion": profile.get("max_favorable_excursion"),
        "max_adverse_excursion": profile.get("max_adverse_excursion"),
        "winner_stable_80_clock_elapsed_seconds": profile.get("winner_stable_80_clock_elapsed_seconds"),
        }
    )


def _game_explorer_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    work = frame.copy()
    for column in (
        "game_date",
        "game_start_time",
        "opening_price",
        "closing_price",
        "total_swing",
        "inversion_count",
    ):
        if column in work.columns:
            work[column] = work[column]
    items: list[dict[str, Any]] = []
    for game_id, group in work.groupby("game_id", sort=False):
        side_lookup = {
            str(row.get("team_side")): row.to_dict()
            for _, row in group.iterrows()
        }
        home = side_lookup.get("home")
        away = side_lookup.get("away")
        anchor = home or away or {}
        coverage_values = sorted({str(value) for value in group["coverage_status"].dropna().tolist()})
        winner_team_slug = None
        for candidate in (home, away):
            if candidate and _boolish(candidate.get("final_winner_flag")):
                winner_team_slug = candidate.get("team_slug")
                break
        items.append(
            _clean_record(
                {
                "game_id": str(game_id),
                "season": anchor.get("season"),
                "season_phase": anchor.get("season_phase"),
                "analysis_version": anchor.get("analysis_version"),
                "game_date": anchor.get("game_date"),
                "game_start_time": anchor.get("game_start_time"),
                "matchup": f"{away.get('team_slug') if away else 'UNK'} @ {home.get('team_slug') if home else 'UNK'}",
                "research_ready_game_flag": bool(group["research_ready_flag"].map(_boolish).all()),
                "coverage_statuses": coverage_values,
                "winner_team_slug": winner_team_slug,
                "home": _profile_summary(home),
                "away": _profile_summary(away),
                }
            )
        )
    return items


def _state_summary(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "state_count": 0,
            "latest_state_index": None,
            "price_min": None,
            "price_max": None,
            "top_context_buckets": [],
        }
    price_series = pd.to_numeric(frame["team_price"], errors="coerce")
    context_counts = [
        {"context_bucket": key, "state_count": int(value)}
        for key, value in frame["context_bucket"].fillna("unknown").astype(str).value_counts().head(8).items()
    ]
    latest_state_index = pd.to_numeric(frame["state_index"], errors="coerce").max()
    return _clean_record(
        {
        "state_count": int(len(frame)),
        "latest_state_index": int(latest_state_index) if pd.notna(latest_state_index) else None,
        "price_min": float(price_series.min()) if price_series.notna().any() else None,
        "price_max": float(price_series.max()) if price_series.notna().any() else None,
        "top_context_buckets": context_counts,
        }
    )


def _select_state_rows(frame: pd.DataFrame, *, limit: int) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    ordered = frame.sort_values("state_index", ascending=True)
    if len(ordered) > limit:
        head_count = min(8, max(4, limit // 3))
        tail_count = max(limit - head_count, 1)
        ordered = (
            pd.concat([ordered.head(head_count), ordered.tail(tail_count)], axis=0)
            .drop_duplicates(subset=["state_index"], keep="first")
            .sort_values("state_index", ascending=True)
        )
    rows: list[dict[str, Any]] = []
    for _, row in ordered.iterrows():
        rows.append(
            _clean_record(
                {
                    "state_index": row.get("state_index"),
                    "event_at": row.get("event_at"),
                    "period": row.get("period"),
                    "clock": row.get("clock"),
                    "score_for": row.get("score_for"),
                    "score_against": row.get("score_against"),
                    "score_diff": row.get("score_diff"),
                    "context_bucket": row.get("context_bucket"),
                    "team_price": row.get("team_price"),
                    "price_delta_from_open": row.get("price_delta_from_open"),
                    "mfe_from_state": row.get("mfe_from_state"),
                    "mae_from_state": row.get("mae_from_state"),
                    "large_swing_next_12_states_flag": _boolish(row.get("large_swing_next_12_states_flag")),
                    "crossed_50c_next_12_states_flag": _boolish(row.get("crossed_50c_next_12_states_flag")),
                    "winner_stable_80_after_state_flag": _boolish(row.get("winner_stable_80_after_state_flag")),
                }
            )
        )
    return rows


def _build_state_panel_payload(frame: pd.DataFrame, *, side: str, limit: int) -> dict[str, Any]:
    if frame.empty:
        side_frame = frame
    else:
        side_frame = frame[frame["team_side"].astype(str) == side].copy()
    return {
        "summary": _state_summary(side_frame),
        "rows": _select_state_rows(side_frame, limit=limit),
    }


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
    request = _build_consumer_request(
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


@router.get("/v1/analysis/studio/backtests")
def get_analysis_studio_backtests(
    season: str = Query(...),
    season_phase: str = Query(...),
    analysis_version: str | None = Query(default=None),
    backtest_experiment_id: str | None = Query(default=None),
    output_root: str | None = Query(default=None),
) -> dict[str, Any]:
    request = _build_consumer_request(
        season=season,
        season_phase=season_phase,
        analysis_version=analysis_version,
        backtest_experiment_id=backtest_experiment_id,
        output_root=output_root,
    )
    try:
        return load_analysis_backtest_index(request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/v1/analysis/studio/backtests/{strategy_family}")
def get_analysis_studio_backtest_family(
    strategy_family: str,
    season: str = Query(...),
    season_phase: str = Query(...),
    analysis_version: str | None = Query(default=None),
    backtest_experiment_id: str | None = Query(default=None),
    output_root: str | None = Query(default=None),
    trade_limit: int = Query(default=5, ge=1, le=20),
    context_limit: int = Query(default=10, ge=1, le=40),
    trace_limit: int = Query(default=3, ge=1, le=12),
) -> dict[str, Any]:
    request = _build_consumer_request(
        season=season,
        season_phase=season_phase,
        analysis_version=analysis_version,
        backtest_experiment_id=backtest_experiment_id,
        output_root=output_root,
    )
    try:
        return load_analysis_backtest_family_detail(
            request,
            strategy_family=strategy_family,
            trade_limit=trade_limit,
            context_limit=context_limit,
            trace_limit=trace_limit,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        status_code = 404 if "Unknown strategy_family" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


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


@router.get("/v1/analysis/studio/benchmark-dashboard")
def get_analysis_studio_benchmark_dashboard(
    season: str = Query(default=DEFAULT_SEASON),
    replay_artifact_name: str = Query(default="postseason_execution_replay"),
    shared_root: str | None = Query(default=None),
    finalist_limit: int = Query(default=6, ge=2, le=12),
) -> dict[str, Any]:
    request = UnifiedBenchmarkRequest(
        season=season,
        replay_artifact_name=replay_artifact_name,
        shared_root=shared_root,
        finalist_limit=finalist_limit,
    )
    return build_unified_benchmark_dashboard(request)


@router.get("/v1/analysis/studio/games")
def list_analysis_studio_games(
    season: str = Query(default=DEFAULT_SEASON),
    season_phase: str = Query(default=DEFAULT_SEASON_PHASE),
    analysis_version: str | None = Query(default=None),
    output_root: str | None = Query(default=None),
    team_slug: str | None = Query(default=None),
    coverage_status: str | None = Query(default=None),
    game_date: str | None = Query(default=None),
    limit: int = Query(default=24, ge=1, le=200),
) -> dict[str, Any]:
    try:
        output_dir, resolved_version = _resolve_analysis_output_dir(
            season=season,
            season_phase=season_phase,
            analysis_version=analysis_version,
            output_root=output_root,
        )
        frame = _read_analysis_frame(
            output_dir,
            "nba_analysis_game_team_profiles",
            columns=GAME_EXPLORER_PROFILE_COLUMNS,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    work = frame.copy()
    if game_date:
        work = work[work["game_date"].astype(str) == game_date]
    if team_slug:
        team_token = team_slug.strip().upper()
        matching_game_ids = set(
            work.loc[
                work["team_slug"].astype(str).str.upper().eq(team_token)
                | work["opponent_team_slug"].astype(str).str.upper().eq(team_token),
                "game_id",
            ].astype(str)
        )
        work = work[work["game_id"].astype(str).isin(matching_game_ids)]
    if coverage_status:
        matching_game_ids = set(
            work.loc[work["coverage_status"].astype(str) == coverage_status, "game_id"].astype(str)
        )
        work = work[work["game_id"].astype(str).isin(matching_game_ids)]

    if not work.empty:
        work = work.sort_values(
            by=["game_date", "game_start_time", "game_id", "team_side"],
            ascending=[False, False, False, True],
        )
    items = _game_explorer_rows(work)
    limited_items = items[:limit]
    return {
        "season": season,
        "season_phase": season_phase,
        "analysis_version": resolved_version,
        "output_dir": str(output_dir),
        "total_games": len(items),
        "returned_games": len(limited_items),
        "filters": {
            "team_slug": team_slug,
            "coverage_status": coverage_status,
            "game_date": game_date,
            "limit": limit,
        },
        "items": limited_items,
    }


@router.get("/v1/analysis/studio/games/{game_id}")
def get_analysis_studio_game(
    game_id: str,
    season: str = Query(default=DEFAULT_SEASON),
    season_phase: str = Query(default=DEFAULT_SEASON_PHASE),
    analysis_version: str | None = Query(default=None),
    output_root: str | None = Query(default=None),
    state_limit: int = Query(default=24, ge=6, le=160),
) -> dict[str, Any]:
    try:
        output_dir, resolved_version = _resolve_analysis_output_dir(
            season=season,
            season_phase=season_phase,
            analysis_version=analysis_version,
            output_root=output_root,
        )
        profiles = _read_analysis_frame(
            output_dir,
            "nba_analysis_game_team_profiles",
            columns=GAME_EXPLORER_PROFILE_COLUMNS,
            filters=[("game_id", "==", game_id)],
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if profiles.empty:
        raise HTTPException(status_code=404, detail=f"analysis game not found for game_id={game_id}")

    try:
        state_frame = _read_analysis_frame(
            output_dir,
            "nba_analysis_state_panel",
            columns=GAME_EXPLORER_STATE_COLUMNS,
            filters=[("game_id", "==", game_id)],
        )
    except FileNotFoundError:
        state_frame = pd.DataFrame(columns=GAME_EXPLORER_STATE_COLUMNS)

    profiles = profiles.sort_values(by=["team_side"], ascending=True)
    game_payload = _game_explorer_rows(profiles)[0]
    profile_lookup = {
        str(row.get("team_side")): row.to_dict()
        for _, row in profiles.iterrows()
    }
    return {
        "season": season,
        "season_phase": season_phase,
        "analysis_version": resolved_version,
        "output_dir": str(output_dir),
        "game": game_payload,
        "profiles": {
            "home": _profile_summary(profile_lookup.get("home")),
            "away": _profile_summary(profile_lookup.get("away")),
        },
        "state_panel": {
            "home": _build_state_panel_payload(state_frame, side="home", limit=state_limit),
            "away": _build_state_panel_payload(state_frame, side="away", limit=state_limit),
        },
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
